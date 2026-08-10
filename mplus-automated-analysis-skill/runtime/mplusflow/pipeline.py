from __future__ import annotations

from pathlib import Path
import shutil
import sys
from typing import Any

import pandas as pd

from .data_io import PreparedLPAData, prepare_lpa_project
from .lpa import START_TIERS, assert_template_integrity, mirror_output, run_model, write_model
from .mplus_detect import detect_mplus
from .parser import MplusResult, parse_mplus_output, read_savedata
from .report import (
    choose_statistical_candidate,
    comparison_dataframe,
    write_analysis_report,
    write_code_explanation,
    write_manifest,
    write_quality_report,
    write_reference_files,
)
from .review import ReviewIssue, programmatic_review
from .utils import ensure_dir, write_json
from .validation import resolve_environment_validation


def _ll_close(a: float | None, b: float | None, tol: float = 1e-3) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _stable_model_for_k(
    prep: PreparedLPAData,
    k: int,
    mplus_command: str,
    timeout_seconds: int,
) -> tuple[MplusResult, Any]:
    indicators = prep.spec["内部剖面指标"]
    missing = prep.spec["内部缺失码"]

    if k == 1:
        model = write_model(prep.project_dir, prep.runtime_dir, k, indicators, missing, "oneclass")
        issues = assert_template_integrity(model.input_path.read_text(encoding="ascii"), k, indicators)
        if issues:
            raise RuntimeError("模板静态检查失败：" + "；".join(issues))
        proc = run_model(model, mplus_command, timeout_seconds)
        if not model.output_path.exists():
            raise RuntimeError(
                f"1 类模型调用 Mplus 后没有生成 model.out（进程返回码 {proc.returncode}）。"
                f"请查看内部运行日志：{model.runtime_dir / 'process.log'}"
            )
        result = parse_mplus_output(model.output_path)
        mirrored = mirror_output(model, prep.project_dir)
        if mirrored:
            result.file = str(mirrored)
        return result, model

    previous: MplusResult | None = None
    previous_model: Any | None = None
    accepted: tuple[MplusResult, Any] | None = None
    for tier in ["screening", "verification", "difficult"]:
        model = write_model(prep.project_dir, prep.runtime_dir, k, indicators, missing, tier, starts=START_TIERS[tier])
        issues = assert_template_integrity(model.input_path.read_text(encoding="ascii"), k, indicators)
        if issues:
            raise RuntimeError("模板静态检查失败：" + "；".join(issues))
        run_model(model, mplus_command, timeout_seconds)
        if not model.output_path.exists():
            continue
        result = parse_mplus_output(model.output_path)
        if not result.normal_termination or result.errors or result.best_ll_replicated is not True:
            previous = result
            previous_model = model
            continue
        if tier == "screening":
            # 即使初筛已重复，也继续verification确认最优解。
            previous = result
            previous_model = model
            continue
        if previous and previous.best_ll_replicated is True and _ll_close(previous.loglikelihood, result.loglikelihood):
            accepted = (result, model)
            break
        if tier == "difficult":
            accepted = (result, model)
            break
        previous = result
        previous_model = model

    if accepted is None:
        # 返回最后一次结果，交由质量审查判为不稳定。
        if previous is None:
            raise RuntimeError(f"{k} 类模型没有产生可解析结果。")
        if previous_model is None:
            raise RuntimeError(f"{k} 类模型结果与运行目录无法对应。")
        mirrored = mirror_output(previous_model, prep.project_dir)
        if mirrored:
            previous.file = str(mirrored)
        return previous, previous_model

    stable_result, stable_model = accepted
    if stable_result.best_seed is None:
        # 如果无法提取seed，保守地保留稳定主模型，不伪造TECH11/14比较。
        mirrored = mirror_output(stable_model, prep.project_dir)
        if mirrored:
            stable_result.file = str(mirrored)
        return stable_result, stable_model

    # 使用稳定解的seed运行TECH11/TECH14，避免重新自由搜索主解。
    comp = write_model(
        prep.project_dir,
        prep.runtime_dir,
        k,
        indicators,
        missing,
        "comparison",
        optseed=stable_result.best_seed,
        include_lrt=True,
    )
    issues = assert_template_integrity(comp.input_path.read_text(encoding="ascii"), k, indicators)
    if issues:
        raise RuntimeError("比较模型模板静态检查失败：" + "；".join(issues))
    run_model(comp, mplus_command, timeout_seconds)
    if not comp.output_path.exists():
        mirrored = mirror_output(stable_model, prep.project_dir)
        if mirrored:
            stable_result.file = str(mirrored)
        return stable_result, stable_model
    final = parse_mplus_output(comp.output_path)
    # STARTS=0 + OPTSEED 的比较阶段不会再打印“最佳LL已重复”，继承已经验证过的稳定性证据。
    final.best_ll_replicated = stable_result.best_ll_replicated
    final.best_seed = stable_result.best_seed
    if final.loglikelihood is not None and stable_result.loglikelihood is not None and not _ll_close(final.loglikelihood, stable_result.loglikelihood):
        final.warnings.append(
            f"比较阶段使用 OPTSEED 后的 loglikelihood ({final.loglikelihood}) 与稳定阶段 ({stable_result.loglikelihood}) 不一致。"
        )
    mirrored = mirror_output(comp, prep.project_dir)
    if mirrored:
        final.file = str(mirrored)
    return final, comp


def _archive_runtime(prep: PreparedLPAData) -> None:
    destination = prep.audit_runtime_dir / "执行记录"
    shutil.copytree(prep.runtime_dir, destination, dirs_exist_ok=True)
    shutil.rmtree(prep.runtime_dir, ignore_errors=True)


def _export_selected_savedata(prep: PreparedLPAData, selected_result: MplusResult, selected_model: Any) -> Path | None:
    if selected_model.savedata_path is None or not selected_model.savedata_path.exists():
        return None
    saved = read_savedata(selected_model.savedata_path, selected_result.savedata_variables)
    rename: dict[str, str] = {}
    for _, row in prep.variable_map.iterrows():
        rename[str(row["Mplus内部变量名"]).upper()] = str(row["原变量名"])
    for c in saved.columns:
        cu = str(c).upper()
        if cu == "C":
            rename[c] = "最可能类别"
        elif cu.startswith("CPROB"):
            digits = "".join(x for x in cu if x.isdigit())
            rename[c] = f"类别{digits}后验概率" if digits else "类别后验概率"
        elif cu in rename:
            rename[c] = rename[cu]
    saved = saved.rename(columns=rename)

    id_map = prep.id_map.copy()
    rowid_col = next((c for c in saved.columns if str(c).upper() == "ROWID"), None)
    if rowid_col is None:
        raise ValueError("SAVEDATA 中没有 ROWID，无法安全合并回原始个案。")
    saved[rowid_col] = pd.to_numeric(saved[rowid_col], errors="raise").astype(int)
    if saved[rowid_col].duplicated().any():
        raise ValueError("SAVEDATA 中 ROWID 重复，无法安全合并回原始个案。")
    expected_ids = set(
        prep.analysis_df.loc[
            ~prep.analysis_df.drop(columns=["ROWID"]).isna().all(axis=1), "ROWID"
        ].astype(int)
    )
    saved_ids = set(saved[rowid_col].tolist())
    if saved_ids != expected_ids:
        missing = sorted(expected_ids - saved_ids)[:10]
        extra = sorted(saved_ids - expected_ids)[:10]
        raise ValueError(f"SAVEDATA 的 ROWID 与有效分析个案不一致；缺少 {missing}，多出 {extra}。")
    out = id_map.merge(saved, left_on="ROWID", right_on=rowid_col, how="left", validate="one_to_one")
    if len(out) != len(id_map):
        raise ValueError("类别归属结果与原始个案数不一致。")
    classification_columns = [c for c in saved.columns if str(c).upper() == "C" or str(c).upper().startswith("CPROB")]
    valid_rows = out["ROWID"].isin(expected_ids)
    if classification_columns and out.loc[valid_rows, classification_columns].isna().any().any():
        raise ValueError("有效分析个案的类别归属或后验概率存在空值。")
    path = ensure_dir(prep.project_dir / "05_分析结果") / "个体类别归属.xlsx"
    out.to_excel(path, index=False)
    out.to_csv(path.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    return path


def _export_class_means(prep: PreparedLPAData, selected_result: MplusResult) -> Path | None:
    if not selected_result.class_means:
        return None
    reverse = {
        str(row["Mplus内部变量名"]).upper(): str(row["原变量名"])
        for _, row in prep.variable_map.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for cls, vals in selected_result.class_means.items():
        for var, value in vals.items():
            if var.upper() in reverse:
                rows.append({"类别": int(cls), "指标": reverse[var.upper()], "估计均值": value})
    if not rows:
        return None
    p = ensure_dir(prep.project_dir / "05_分析结果") / "类别剖面均值.xlsx"
    profile = pd.DataFrame(rows)
    profile.to_excel(p, index=False)
    profile.to_csv(p.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    return p


def generate_dry_run(prep: PreparedLPAData) -> list[Path]:
    paths: list[Path] = []
    for k in prep.spec["类别范围"]:
        m = write_model(
            prep.project_dir,
            prep.runtime_dir,
            int(k),
            prep.spec["内部剖面指标"],
            prep.spec["内部缺失码"],
            "dryrun",
            starts=START_TIERS["screening"],
        )
        issues = assert_template_integrity(m.input_path.read_text(encoding="ascii"), int(k), prep.spec["内部剖面指标"])
        if issues:
            raise RuntimeError("模板静态检查失败：" + "；".join(issues))
        paths.append(m.visible_input_path)
    return paths


def run_lpa_pipeline(
    input_path: str | Path,
    indicators: list[str],
    output_dir: str | Path,
    user_id: str | None = None,
    missing_codes: list[float] | None = None,
    standardize: bool = False,
    allow_low_cardinality: bool = False,
    classes: list[int] | None = None,
    mplus_command: str | None = None,
    allow_untested_version: bool = False,
    self_test_receipt: str | Path | None = None,
    provisional_environment: bool = False,
    self_test_mode: bool = False,
    dry_run: bool = False,
    timeout_seconds: int = 7200,
    text_columns: list[str] | None = None,
) -> dict[str, Any]:
    classes = classes or [1, 2, 3, 4, 5]
    env = None
    receipt_data = None
    if not dry_run:
        env = detect_mplus(mplus_command)
        if not env.command:
            raise RuntimeError("没有找到可调用的 Mplus。请确认已安装 Mplus，或通过 --mplus 指定可执行程序路径。")
        if env.compatibility == "unsupported" and not allow_untested_version:
            raise RuntimeError(
                f"Mplus 版本兼容状态为 {env.compatibility}：{env.compatibility_note} "
                "如已由专家确认，可显式使用 --allow-untested-version 运行并保留风险记录。"
            )
        if not self_test_mode:
            receipt_data = resolve_environment_validation(
                self_test_receipt, env, allow_provisional=provisional_environment,
            )
    prep = prepare_lpa_project(
        input_path=input_path,
        indicators=indicators,
        output_dir=output_dir,
        user_id=user_id,
        missing_codes=missing_codes,
        standardize=standardize,
        allow_low_cardinality=allow_low_cardinality,
        classes=classes,
        text_columns=text_columns,
    )
    write_reference_files(prep.project_dir)

    if dry_run:
        codes = generate_dry_run(prep)
        _archive_runtime(prep)
        return {
            "状态": "仅生成代码_未运行Mplus",
            "项目目录": str(prep.project_dir),
            "代码文件": [str(x) for x in codes],
            "分析设计清单": str(prep.spec_file),
        }

    assert env is not None and env.command is not None
    prep.spec["Mplus版本"] = env.version
    prep.spec["Mplus版本兼容状态"] = env.compatibility
    prep.spec["Mplus版本说明"] = env.compatibility_note
    prep.spec["版本适配配置"] = env.version_profile
    prep.spec["版本适配说明"] = env.version_profile_note
    prep.spec["运行类型"] = "安装自检" if self_test_mode else "真实数据分析"
    prep.spec["本机自检凭证"] = receipt_data
    prep.spec["环境验证状态"] = receipt_data.get("验证状态") if receipt_data else "安装自检"
    write_json(prep.spec_file, prep.spec)

    results: list[MplusResult] = []
    models_by_k: dict[int, Any] = {}
    for k in classes:
        print(f"[mplusflow] 正在运行 {k} 类 LPA 模型（共 {len(classes)} 个）...", file=sys.stderr, flush=True)
        result, model = _stable_model_for_k(prep, int(k), env.command, timeout_seconds)
        results.append(result)
        models_by_k[int(k)] = model

    comp = comparison_dataframe(results)
    comp_path = ensure_dir(prep.project_dir / "05_分析结果") / "类别模型比较表.xlsx"
    comp.to_excel(comp_path, index=False)
    comp.to_csv(prep.project_dir / "05_分析结果" / "类别模型比较表.csv", index=False, encoding="utf-8-sig")

    candidate = choose_statistical_candidate(results, max(classes), prep.spec)
    issues = programmatic_review(prep.spec, results)

    selected_k = candidate.get("候选类别数")
    if selected_k in models_by_k:
        selected_result = next(r for r in results if r.class_count == selected_k)
        try:
            _export_selected_savedata(prep, selected_result, models_by_k[selected_k])
        except Exception as exc:  # noqa: BLE001
            issues.append(ReviewIssue("重大", "SAVEDATA-EXPORT", f"SAVEDATA转Excel失败：{exc}"))
        _export_class_means(prep, selected_result)

    write_code_explanation(prep.project_dir, prep.spec, candidate)
    report = write_analysis_report(prep.project_dir, prep.spec, results, candidate, issues)
    quality = write_quality_report(prep.project_dir, issues)
    manifest = write_manifest(prep.project_dir, prep.spec, results, candidate, issues)

    # 环境记录
    write_json(
        prep.audit_runtime_dir / "运行环境.json",
        {
            "操作系统": env.os_name,
            "架构": env.arch,
            "Mplus命令": env.command,
            "Mplus版本": env.version,
            "Mplus程序架构": env.executable_arch,
            "执行方式说明": env.execution_note,
            "版本兼容状态": env.compatibility,
            "版本说明": env.compatibility_note,
            "版本适配配置": env.version_profile,
            "版本适配说明": env.version_profile_note,
        },
    )
    _archive_runtime(prep)

    return {
        "状态": "完成" if not quality else "完成_发现重大问题",
        "项目目录": str(prep.project_dir),
        "Mplus版本": env.version,
        "模型比较表": str(comp_path),
        "分析报告": str(report),
        "质量问题报告": str(quality) if quality else None,
        "内部manifest": str(manifest),
        "统计候选": candidate,
        "环境验证状态": prep.spec["环境验证状态"],
    }
