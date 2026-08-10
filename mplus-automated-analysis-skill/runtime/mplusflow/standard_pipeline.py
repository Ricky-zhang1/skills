from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .catalog import get_family
from .compiler import render_standard_input, validate_spec
from .data_io import INTERNAL_MISSING, SENTINEL_CANDIDATES, load_dataframe
from .mplus_detect import detect_mplus
from .parser import parse_mplus_output, read_mplus_text, read_savedata
from .review import CRITICAL_WARNING_FRAGMENTS
from .utils import ensure_dir, safe_copy, sha256_file, write_json
from .validation import resolve_environment_validation
from .sample_size import advisory_markdown, sample_size_advisory


@dataclass
class PreparedStandardData:
    project_dir: Path
    runtime_dir: Path
    spec: dict[str, Any]
    internal_map: dict[str, str]


def _new_runtime_dir() -> Path:
    parent = os.getenv("MPLUSFLOW_TEMP")
    runtime = Path(tempfile.mkdtemp(prefix="mplusflow-standard-", dir=parent or None))
    if not str(runtime).isascii():
        shutil.rmtree(runtime, ignore_errors=True)
        raise RuntimeError("Mplus 临时执行路径含非 ASCII 字符；请设置 MPLUSFLOW_TEMP 为纯英文路径。")
    return runtime


def prepare_standard_project(
    design: dict[str, Any],
    input_path: str | Path,
    output_dir: str | Path,
    user_id: str | None = None,
    missing_codes: list[float] | None = None,
    text_columns: list[str] | None = None,
    copy_original: bool = True,
) -> PreparedStandardData:
    validate_spec(design)
    src = Path(input_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"找不到数据文件：{src}")
    project = Path(output_dir).expanduser().resolve()
    if project.exists() and any(project.iterdir()):
        raise FileExistsError(f"输出目录已存在且非空：{project}。请使用新目录。")
    df, source_meta = load_dataframe(
        src,
        text_columns=text_columns,
        preserve_text_columns=[user_id] if user_id else None,
    )
    variables = list(design["variables"])
    absent = [v for v in variables if v not in df.columns]
    if absent:
        raise ValueError(f"分析变量不存在：{absent}")
    if user_id and user_id not in df.columns:
        raise ValueError(f"ID 变量不存在：{user_id}")

    missing_codes = missing_codes or []
    work = df[variables].copy()
    for code in missing_codes:
        work = work.replace(code, np.nan)

    undeclared: dict[str, list[float]] = {}
    categorical = set(design.get("categorical", []))
    audit_rows: list[dict[str, Any]] = []
    for var in variables:
        original = df[var]
        numeric = pd.to_numeric(work[var], errors="coerce")
        newly_missing = int(numeric.isna().sum() - work[var].isna().sum())
        if newly_missing:
            raise ValueError(f"分析变量“{var}”含 {newly_missing} 个无法转为数值的内容。")
        work[var] = numeric.astype(float)
        observed_values = set(pd.to_numeric(original, errors="coerce").dropna().tolist())
        suspicious = sorted(float(x) for x in observed_values.intersection(SENTINEL_CANDIDATES) if x not in missing_codes)
        if suspicious:
            undeclared[var] = suspicious
        unique = int(work[var].nunique(dropna=True))
        if unique <= 1:
            raise ValueError(f"分析变量“{var}”没有有效变异。")
        if var in categorical:
            vals = work[var].dropna()
            if not np.allclose(vals, np.round(vals)):
                raise ValueError(f"分类变量“{var}”含非整数值，需先确认编码。")
        audit_rows.append({
            "原变量名": var,
            "角色": "分类变量" if var in categorical else "连续变量",
            "非缺失数": int(work[var].notna().sum()),
            "缺失数": int(work[var].isna().sum()),
            "唯一值数": unique,
        })
    if undeclared:
        raise ValueError(f"发现未声明的疑似缺失码：{undeclared}。请确认后通过 missing_codes 声明。")

    runtime = _new_runtime_dir()
    dirs = {
        "original": ensure_dir(project / "00_原始数据"),
        "audit": ensure_dir(project / "01_数据检查"),
        "data": ensure_dir(project / "02_分析数据"),
        "code": ensure_dir(project / "03_Mplus代码"),
        "raw": ensure_dir(project / "04_Mplus原始结果"),
        "result": ensure_dir(project / "05_分析结果"),
        "report": ensure_dir(project / "06_分析报告"),
        "reference": ensure_dir(project / "07_参考依据"),
        "internal": ensure_dir(project / ".mplus_runtime"),
    }
    if copy_original:
        safe_copy(src, dirs["original"])
    mapping = {name: f"V{i:06d}" for i, name in enumerate(variables, 1)}
    mapped_rows = []
    for row in audit_rows:
        row = dict(row)
        row["Mplus内部变量名"] = mapping[str(row["原变量名"])]
        mapped_rows.append(row)
    map_df = pd.DataFrame(mapped_rows)
    map_df.to_excel(dirs["audit"] / "变量对应表.xlsx", index=False)
    map_df.to_csv(dirs["audit"] / "变量对应表.csv", index=False, encoding="utf-8-sig")

    analysis = pd.DataFrame({"ROWID": np.arange(1, len(df) + 1, dtype=int)})
    for original, internal in mapping.items():
        analysis[internal] = work[original]
    for destination in [dirs["data"] / "Mplus分析数据.dat", runtime / "data.dat"]:
        analysis.to_csv(destination, sep=" ", header=False, index=False, na_rep=str(int(INTERNAL_MISSING)), float_format="%.10g")

    id_map = pd.DataFrame({"ROWID": analysis["ROWID"]})
    if user_id:
        id_map[user_id] = df[user_id].map(lambda x: None if pd.isna(x) else str(x))
    id_map.to_excel(dirs["audit"] / "个案ID对应表.xlsx", index=False)
    id_map.to_csv(dirs["audit"] / "个案ID对应表.csv", index=False, encoding="utf-8-sig")

    size_advisory = sample_size_advisory(str(design["analysis"]), len(df), design)
    final_design = dict(design)
    final_design.update({
        "模板ID": f"STRUCTURED-{str(design['analysis']).upper()}",
        "数据文件": str(src),
        "数据文件SHA256": sha256_file(src),
        "用户ID变量": user_id,
        "用户声明缺失码": missing_codes,
        "内部缺失码": INTERNAL_MISSING,
        "原始样本数": int(len(df)),
        "has_missing": bool(work.isna().any().any()),
        "源数据元信息": source_meta,
        "内部变量对应": mapping,
        "样本量提示": size_advisory,
    })
    write_json(dirs["data"] / "分析设计清单.json", final_design)
    audit = [
        "# 数据质量检查报告", "",
        f"- 分析类型：{get_family(str(design['analysis'])).name_zh}",
        f"- 原始样本数：{len(df)}", f"- 分析变量数：{len(variables)}", "",
        "## 变量检查", "",
    ]
    audit.extend(
        f"- {r['原变量名']} -> {r['Mplus内部变量名']}；{r['角色']}；缺失 {r['缺失数']}；唯一值 {r['唯一值数']}"
        for r in mapped_rows
    )
    audit.extend(["", *advisory_markdown(size_advisory)])
    (dirs["audit"] / "数据质量检查报告.md").write_text("\n".join(audit), encoding="utf-8")
    return PreparedStandardData(project, runtime, final_design, mapping)


def _efa_solutions(text: str) -> list[dict[str, float | int | None]]:
    starts = list(re.finditer(r"EXPLORATORY FACTOR ANALYSIS WITH\s+(\d+)\s+FACTOR\(S\):", text, re.I))
    rows: list[dict[str, float | int | None]] = []
    for index, match in enumerate(starts):
        section = text[match.end(): starts[index + 1].start() if index + 1 < len(starts) else len(text)]

        def value(pattern: str) -> float | None:
            found = re.search(pattern, section, re.I | re.S)
            return float(found.group(1)) if found else None

        rows.append({
            "因子数": int(match.group(1)),
            "卡方": value(r"Chi-Square Test of Model Fit\s+Value\s+([0-9.]+)"),
            "自由度": value(r"Chi-Square Test of Model Fit\s+.*?Degrees of Freedom\s+([0-9.]+)"),
            "CFI": value(r"CFI\s+([0-9.]+)"),
            "TLI": value(r"TLI\s+([0-9.]+)"),
            "RMSEA": value(r"RMSEA \(Root Mean Square Error Of Approximation\)\s+Estimate\s+([0-9.]+)"),
            "SRMR": value(r"SRMR \(Standardized Root Mean Square Residual\)\s+Value\s+([0-9.]+)"),
        })
    return rows


def _efa_loadings(text: str) -> list[dict[str, Any]]:
    starts = list(re.finditer(r"EXPLORATORY FACTOR ANALYSIS WITH\s+(\d+)\s+FACTOR\(S\):", text, re.I))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(starts):
        factors = int(match.group(1))
        section = text[match.end(): starts[index + 1].start() if index + 1 < len(starts) else len(text)]
        loadings = re.search(r"GEOMIN ROTATED LOADINGS.*?\n(.*?)(?:\n\s*(?:GEOMIN FACTOR CORRELATIONS|FACTOR STRUCTURE|ESTIMATED RESIDUAL VARIANCES))", section, re.I | re.S)
        if not loadings:
            continue
        for line in loadings.group(1).splitlines():
            item = re.match(r"\s*(V\d{6})\s+(.+)$", line, re.I)
            if not item:
                continue
            values = re.findall(r"[-+]?\d+\.\d+", item.group(2))[:factors]
            for factor, value in enumerate(values, 1):
                rows.append({"因子解": factors, "Mplus变量": item.group(1).upper(), "因子": factor, "旋转载荷": float(value)})
    return rows


def _standardized_parameters(text: str) -> list[dict[str, Any]]:
    start = re.search(r"STANDARDIZED MODEL RESULTS.*?STDYX Standardization", text, re.I | re.S)
    if not start:
        return []
    tail = text[start.end():]
    end = re.search(r"(?:\n\s*STDY Standardization|\n\s*R-SQUARE)", tail, re.I)
    section = tail[:end.start()] if end else tail
    rows: list[dict[str, Any]] = []
    outcome = operator = None
    for line in section.splitlines():
        if re.match(r"\s*(?:Intercepts|Thresholds|Means|Variances|Residual Variances|R-SQUARE)\s*$", line, re.I):
            outcome = operator = None
            continue
        heading = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+(BY|ON|WITH)\s*$", line, re.I)
        if heading:
            outcome, operator = heading.group(1).upper(), heading.group(2).upper()
            continue
        value = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+(-?\d+\.\d+)\s+(\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+\.\d+)", line, re.I)
        if value and outcome and operator:
            rows.append({
                "结果变量/因子": outcome, "关系": operator, "预测变量/指标": value.group(1).upper(),
                "STDYX估计": float(value.group(2)), "标准误": float(value.group(3)),
                "z": float(value.group(4)), "p": float(value.group(5)),
            })
    return rows


def _standardized_indirect(text: str) -> list[dict[str, Any]]:
    start = re.search(r"STANDARDIZED TOTAL, TOTAL INDIRECT, SPECIFIC INDIRECT, AND DIRECT EFFECTS\s+STDYX Standardization", text, re.I | re.S)
    if not start:
        return []
    tail = text[start.end():]
    end = re.search(r"\n\s*STDY Standardization", tail, re.I)
    section = tail[:end.start()] if end else tail
    effect_from = effect_to = None
    specific_label: str | None = None
    specific_path: list[str] = []
    rows: list[dict[str, Any]] = []
    for line in section.splitlines():
        heading = re.search(r"Effects from\s+([A-Z][A-Z0-9_]{0,7})\s+to\s+([A-Z][A-Z0-9_]{0,7})", line, re.I)
        if heading:
            effect_from, effect_to = heading.group(1).upper(), heading.group(2).upper()
            specific_label = None
            specific_path = []
            continue
        specific = re.match(r"\s*(Specific indirect\s+\d+)\s*$", line, re.I)
        if specific:
            specific_label = specific.group(1)
            specific_path = []
            continue
        if specific_label:
            path_value = re.match(
                r"\s*([A-Z][A-Z0-9_]{0,7})(?:\s+(-?\d+\.\d+)\s+(\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+\.\d+))?",
                line, re.I,
            )
            if path_value:
                specific_path.append(path_value.group(1).upper())
                if path_value.group(2) and effect_from and effect_to:
                    via = specific_path[1:-1]
                    rows.append({
                        "起点": effect_from, "终点": effect_to, "效应": specific_label,
                        "路径": " -> ".join([effect_from, *via, effect_to]),
                        "STDYX估计": float(path_value.group(2)), "标准误": float(path_value.group(3)),
                        "z": float(path_value.group(4)), "p": float(path_value.group(5)),
                    })
                    specific_label = None
                    specific_path = []
                continue
        value = re.match(r"\s*(Indirect|Sum of indirect|Total indirect|Direct|Total)\s+(-?\d+\.\d+)\s+(\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+\.\d+)", line, re.I)
        if value and effect_from and effect_to:
            rows.append({"起点": effect_from, "终点": effect_to, "效应": value.group(1), "STDYX估计": float(value.group(2)), "标准误": float(value.group(3)), "z": float(value.group(4)), "p": float(value.group(5))})
    return rows


def _lca_probability_scale(text: str) -> list[dict[str, Any]]:
    start = re.search(r"RESULTS IN PROBABILITY SCALE", text, re.I)
    if not start:
        return []
    tail = text[start.end():]
    end = re.search(r"LATENT CLASS ODDS RATIO RESULTS|QUALITY OF NUMERICAL RESULTS|TECHNICAL 1 OUTPUT", tail, re.I)
    section = tail[:end.start()] if end else tail
    rows: list[dict[str, Any]] = []
    class_no: int | None = None
    variable: str | None = None
    for line in section.splitlines():
        class_match = re.match(r"\s*Latent Class\s+(\d+)\s*$", line, re.I)
        if class_match:
            class_no = int(class_match.group(1))
            variable = None
            continue
        variable_match = re.match(r"\s*(V\d{6}|[A-Z][A-Z0-9_]{0,7})\s*$", line, re.I)
        if variable_match:
            variable = variable_match.group(1).upper()
            continue
        category = re.match(r"\s*Category\s+(\d+)\s+([01]?\.\d+)\s+([0-9.]+)\s+(-?[0-9.]+)\s+([0-9.]+)", line, re.I)
        if category and class_no and variable:
            rows.append({
                "类别": class_no, "Mplus变量": variable, "Mplus类别序号": int(category.group(1)),
                "反应概率": float(category.group(2)), "标准误": float(category.group(3)),
                "z": float(category.group(4)), "p": float(category.group(5)),
            })
    return rows


def _fit_summary(output_path: Path) -> dict[str, Any]:
    text = read_mplus_text(output_path)

    def number(pattern: str) -> float | None:
        match = re.search(pattern, text, re.I | re.M | re.S)
        return float(match.group(1)) if match else None

    result = parse_mplus_output(output_path)
    warning_text = " ".join(result.warnings).upper()
    critical = sorted({label for fragment, label in CRITICAL_WARNING_FRAGMENTS.items() if fragment in warning_text})
    efa_completed = "RESULTS FOR EXPLORATORY FACTOR ANALYSIS" in text.upper() and not result.errors
    summary = {
        "Mplus输出版本": result.mplus_version,
        "输出解析配置": result.output_profile,
        "正常结束": result.normal_termination or efa_completed,
        "Mplus错误": result.errors,
        "Mplus警告": result.warnings,
        "重大警告": critical,
        "实际样本数": result.sample_size,
        "估计量": result.estimator,
        "自由参数数": result.free_parameters,
        "卡方": number(r"Chi-Square Test of Model Fit\s+Value\s+([0-9.]+)"),
        "自由度": number(r"Chi-Square Test of Model Fit\s+.*?Degrees of Freedom\s+([0-9.]+)"),
        "CFI": number(r"CFI\s+([0-9.]+)"),
        "TLI": number(r"TLI\s+([0-9.]+)"),
        "RMSEA": number(r"RMSEA \(Root Mean Square Error Of Approximation\)\s+Estimate\s+([0-9.]+)"),
        "SRMR": number(r"SRMR \(Standardized Root Mean Square Residual\)\s+Value\s+([0-9.]+)"),
        "AIC": result.aic,
        "BIC": result.bic,
        "SABIC": result.sabic,
        "Entropy": result.entropy,
        "最佳LL重复": result.best_ll_replicated,
        "TECH11_p": result.tech11_p,
        "TECH14_p": result.tech14_p,
        "TECH14可用": result.tech14_trustworthy,
        "类别样本数": result.class_counts,
        "类别比例": result.class_proportions,
        "后验概率对角线": result.posterior_diag,
        "SAVEDATA变量": result.savedata_variables,
        "SAVEDATA文件": result.savedata_filename,
    }
    efa = _efa_solutions(text)
    if efa:
        summary["EFA因子解"] = efa
        summary["EFA旋转载荷"] = _efa_loadings(text)
        for key in ["卡方", "自由度", "CFI", "TLI", "RMSEA", "SRMR"]:
            summary[key] = None
    parameters = _standardized_parameters(text)
    if parameters:
        summary["标准化参数"] = parameters
    indirect = _standardized_indirect(text)
    if indirect:
        summary["标准化间接效应"] = indirect
    return summary


def _write_standard_code_explanation(project: Path, family: Any, code: str) -> Path:
    """Create a short teaching companion without changing executable Mplus code."""
    explanations = {
        "DATA": "指定 Skill 生成的无表头 `.dat` 数据文件；变量列的实际顺序由 VARIABLE 段声明。",
        "VARIABLE": "声明数据列、实际用于本模型的变量、缺失码及分类变量。这里是核对变量是否被正确带入模型的第一处。",
        "ANALYSIS": "指定估计方式和模型类型。它影响标准误、拟合指标和模型估计，不是可随意替换的装饰参数。",
        "MODEL": "写入因子、回归路径或增长轨迹等研究假设。标准模式只把已确认的结构编译为 Mplus 语法。",
        "MODEL INDIRECT": "要求 Mplus 计算预先声明的间接效应；间接效应方向来自分析设计，而不是由 Skill 根据显著性事后挑选。",
        "OUTPUT": "请求标准化参数、置信区间和技术输出，供结果表和质量检查使用。",
        "SAVEDATA": "在适用时保存后验概率或类别归属；Skill 会先核对输出列顺序，再导出 Excel。",
    }
    lines = [
        f"# {family.name_zh}代码逐段说明", "",
        "这份说明解释代码每一段的用途。它不替代研究假设或方法训练，但可帮助你核对 AI 是否按已确认的设计生成代码。", "",
    ]
    for heading, explanation in explanations.items():
        if re.search(rf"(?m)^{re.escape(heading)}:\s*$", code):
            lines.extend([f"## {heading}", "", explanation, ""])
    lines.extend([
        "## 本次代码依据", "",
        f"- 分析家族：{family.name_zh}",
        f"- 登记来源：{family.source}",
        "- Mplus User's Guide: https://www.statmodel.com/html_ug.shtml",
    ])
    path = ensure_dir(project / "03_Mplus代码") / "代码逐段说明.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _run_lca_comparison(
    design: dict[str, Any],
    input_path: str | Path,
    output_dir: str | Path,
    user_id: str | None,
    missing_codes: list[float] | None,
    mplus_command: str | None,
    self_test_receipt: str | Path | None,
    provisional_environment: bool,
    dry_run: bool,
    timeout_seconds: int,
    text_columns: list[str] | None,
) -> dict[str, Any]:
    classes = [int(x) for x in design.get("class_counts", [])]
    if classes != list(range(1, max(classes, default=0) + 1)) or max(classes, default=0) > 10:
        raise ValueError("LCA class_counts 必须从 1 开始连续且不超过 10，例如 [1,2,3,4,5]。")
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"输出目录已存在且非空：{root}。")
    root.mkdir(parents=True, exist_ok=True)
    safe_copy(Path(input_path).expanduser().resolve(), ensure_dir(root / "00_原始数据"))
    rows: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    for k in classes:
        print(f"[mplusflow] 正在处理 {k} 类 LCA 模型（共 {len(classes)} 个）...", file=sys.stderr, flush=True)
        single = dict(design)
        single.pop("class_counts", None)
        single["class_count"] = k
        result = run_standard_pipeline(
            single, input_path, root / "各类别模型" / f"{k}类", user_id,
            missing_codes, mplus_command, self_test_receipt, provisional_environment, dry_run,
            timeout_seconds, text_columns, _copy_original=False,
        )
        projects.append(result)
        if not dry_run:
            summary_path = root / "各类别模型" / f"{k}类" / "05_分析结果" / "模型结果摘要.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            proportions = summary.get("类别比例") or []
            posterior = summary.get("后验概率对角线") or []
            rows.append({"类别数": k, **{key: summary.get(key) for key in [
                "正常结束", "最佳LL重复", "实际样本数", "自由参数数",
                "AIC", "BIC", "SABIC", "Entropy", "TECH11_p", "TECH14_p", "TECH14可用",
            ]},
                "最小类别占比": min(proportions) if proportions else None,
                "最低平均后验概率": min(posterior) if posterior else None,
                "重大问题": bool(summary.get("Mplus错误") or summary.get("重大警告")),
            })
    if dry_run:
        return {"状态": "仅生成代码_未运行Mplus", "项目目录": str(root), "各模型": projects}

    comparison = pd.DataFrame(rows)
    result_dir = ensure_dir(root / "05_分析结果")
    comparison.to_excel(result_dir / "LCA类别模型比较表.xlsx", index=False)
    comparison.to_csv(result_dir / "LCA类别模型比较表.csv", index=False, encoding="utf-8-sig")
    valid = comparison[
        comparison["正常结束"].eq(True)
        & comparison["重大问题"].eq(False)
        & ((comparison["类别数"] == 1) | comparison["最佳LL重复"].eq(True))
        & comparison["BIC"].notna()
    ]
    candidate = int(valid.loc[valid["BIC"].idxmin(), "类别数"]) if not valid.empty else None
    conflict: list[str] = []
    if candidate == max(classes):
        conflict.append("BIC 最低点位于搜索上界，当前类别范围可能尚未覆盖拐点。")
    if len(valid) != len(comparison):
        conflict.append("部分类别模型未通过正常结束、重大警告或最佳 loglikelihood 重复门槛。")
    candidate_row = valid[valid["类别数"] == candidate].iloc[0] if candidate is not None else None
    if candidate_row is not None and candidate >= 2:
        if pd.notna(candidate_row["TECH11_p"]) and float(candidate_row["TECH11_p"]) >= 0.05:
            conflict.append(f"{candidate} 类模型 TECH11 未支持相对 {candidate - 1} 类继续增加类别。")
        if pd.notna(candidate_row["TECH14_p"]) and candidate_row["TECH14可用"] == True and float(candidate_row["TECH14_p"]) >= 0.05:  # noqa: E712
            conflict.append(f"{candidate} 类模型 TECH14 未支持相对 {candidate - 1} 类继续增加类别。")
        if pd.isna(candidate_row["TECH11_p"]) and pd.isna(candidate_row["TECH14_p"]):
            conflict.append(f"{candidate} 类模型没有可用的 TECH11/TECH14，类别数证据不完整。")
    assignment_path: Path | None = None
    probability_path: Path | None = None
    if candidate is not None:
        selected_root = root / "各类别模型" / f"{candidate}类"
        summary = json.loads((selected_root / "05_分析结果" / "模型结果摘要.json").read_text(encoding="utf-8"))
        savedata = selected_root / ".mplus_runtime" / "执行记录" / str(summary.get("SAVEDATA文件") or "savedata.dat")
        assignments = read_savedata(savedata, list(summary.get("SAVEDATA变量") or []))
        if "ROWID" not in assignments.columns or assignments["ROWID"].duplicated().any():
            raise RuntimeError("LCA SAVEDATA 缺少唯一 ROWID，拒绝导出个体类别归属。")
        id_map = pd.read_csv(selected_root / "01_数据检查" / "个案ID对应表.csv", dtype="string")
        id_map["ROWID"] = pd.to_numeric(id_map["ROWID"], errors="raise").astype(int)
        assignments["ROWID"] = pd.to_numeric(assignments["ROWID"], errors="raise").astype(int)
        if len(assignments) != len(id_map):
            raise RuntimeError(f"LCA SAVEDATA 行数 {len(assignments)} 与输入个案数 {len(id_map)} 不一致。")
        assignments = id_map.merge(assignments, on="ROWID", how="left", validate="one_to_one")
        design_data = json.loads((selected_root / "02_分析数据" / "分析设计清单.json").read_text(encoding="utf-8"))
        reverse = {internal: original for original, internal in design_data.get("内部变量对应", {}).items()}
        assignments = assignments.rename(columns={
            **reverse,
            **{f"CPROB{i}": f"类别{i}后验概率" for i in range(1, candidate + 1)},
            "C": "最可能类别",
        })
        assignment_path = result_dir / "个体类别归属.xlsx"
        assignments.to_excel(assignment_path, index=False)
        assignments.to_csv(result_dir / "个体类别归属.csv", index=False, encoding="utf-8-sig")
        output_text = read_mplus_text(selected_root / "04_Mplus原始结果" / "lca_Mplus输出.out")
        probability_rows = _lca_probability_scale(output_text)
        if not probability_rows:
            raise RuntimeError("候选 LCA 输出中未解析到题项反应概率，拒绝生成空的类别解释材料。")
        probability_table = pd.DataFrame(probability_rows)
        probability_table["原变量名"] = probability_table["Mplus变量"].map(reverse)
        probability_path = result_dir / "类别题项反应概率.xlsx"
        probability_table.to_excel(probability_path, index=False)
        probability_table.to_csv(result_dir / "类别题项反应概率.csv", index=False, encoding="utf-8-sig")
    report_dir = ensure_dir(root / "06_分析报告")
    report = [
        "# 潜在类别分析（LCA）模型比较", "",
        f"- 比较范围：1-{max(classes)} 类", f"- 统计候选：{candidate if candidate else '无可用候选'}", "",
        "候选模型先通过正常结束、重大警告和最佳 loglikelihood 重复门槛，再以 BIC 最低模型作为统计候选。最终类别数还必须结合 TECH11/TECH14、类别规模、分类质量与实质可解释性。", "",
        "| 类别数 | 正常结束 | 最佳LL重复 | AIC | BIC | SABIC | Entropy | TECH11 p | TECH14 p | 最小类别占比 | 最低平均后验概率 |",
        "|---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in comparison.iterrows():
        def show(value: Any, digits: int = 3) -> str:
            return "—" if pd.isna(value) else (f"{float(value):.{digits}f}" if isinstance(value, (float, np.floating)) else str(value))
        report.append(
            f"| {int(row['类别数'])} | {'是' if row['正常结束'] else '否'} | "
            f"{'是' if row['最佳LL重复'] else ('否' if row['最佳LL重复'] is False else '未知')} | "
            f"{show(row['AIC'])} | {show(row['BIC'])} | {show(row['SABIC'])} | {show(row['Entropy'])} | "
            f"{show(row['TECH11_p'], 4)} | {show(row['TECH14_p'], 4)} | "
            f"{show(row['最小类别占比'])} | {show(row['最低平均后验概率'])} |"
        )
    report.extend(["", *advisory_markdown(sample_size_advisory("lca", int(comparison["实际样本数"].max())))])
    report.extend(["", "统计候选是证据摘要，不是唯一真值；类别命名还需检查题项反应概率和理论含义。"])
    report.extend(f"- 需要注意：{x}" for x in conflict)
    if assignment_path:
        report.extend(["", "候选模型的后验概率和个体类别归属已导出到《个体类别归属.xlsx》。类别编号只是计算标签。"])
    if probability_path:
        report.extend(["", "候选模型的类别题项反应概率已导出到《类别题项反应概率.xlsx》。Mplus 类别序号按原始有序编码从低到高排列，命名前必须结合变量对应表和问卷编码核对实际含义。"])
    report.extend(["", "官方来源：Mplus User's Guide Chapter 7, Example 7.3。", "https://www.statmodel.com/usersguide/chapter7.shtml"])
    (report_dir / "LCA模型比较报告.md").write_text("\n".join(report), encoding="utf-8")
    return {
        "状态": "完成" if candidate is not None else "完成_无可用候选",
        "项目目录": str(root), "统计候选类别数": candidate,
        "模型比较表": str(result_dir / "LCA类别模型比较表.xlsx"),
        "个体类别归属": str(assignment_path) if assignment_path else None,
        "类别题项反应概率": str(probability_path) if probability_path else None,
        "分析报告": str(report_dir / "LCA模型比较报告.md"), "冲突": conflict,
    }


def run_standard_pipeline(
    design: dict[str, Any],
    input_path: str | Path,
    output_dir: str | Path,
    user_id: str | None = None,
    missing_codes: list[float] | None = None,
    mplus_command: str | None = None,
    self_test_receipt: str | Path | None = None,
    provisional_environment: bool = False,
    dry_run: bool = False,
    timeout_seconds: int = 7200,
    text_columns: list[str] | None = None,
    _copy_original: bool = True,
) -> dict[str, Any]:
    if design.get("analysis") == "lca" and "class_counts" in design:
        return _run_lca_comparison(
            design, input_path, output_dir, user_id, missing_codes, mplus_command,
            self_test_receipt, provisional_environment, dry_run, timeout_seconds, text_columns,
        )
    # 在读取数据和创建项目目录前完成结构预编译，避免无效设计留下半成品。
    variables = list(design.get("variables") or [])
    preflight_mapping = {name: f"V{i:06d}" for i, name in enumerate(variables, 1)}
    render_standard_input(design, preflight_mapping, INTERNAL_MISSING)
    env = None
    receipt = None
    if not dry_run:
        env = detect_mplus(mplus_command)
        if not env.command:
            raise RuntimeError("没有找到可调用的 Mplus。")
        if env.compatibility == "unsupported":
            raise RuntimeError(f"Mplus 版本兼容状态为 {env.compatibility}：{env.compatibility_note}")
        receipt = resolve_environment_validation(
            self_test_receipt, env, allow_provisional=provisional_environment,
        )
    prep = prepare_standard_project(design, input_path, output_dir, user_id, missing_codes, text_columns, _copy_original)
    code = render_standard_input(prep.spec, prep.internal_map, INTERNAL_MISSING)
    visible_inp = prep.project_dir / "03_Mplus代码" / f"{design['analysis']}_分析代码.inp"
    visible_inp.write_text(code, encoding="ascii")
    family = get_family(str(design["analysis"]))
    explanation = _write_standard_code_explanation(prep.project_dir, family, code)
    runtime_inp = prep.runtime_dir / "model.inp"
    runtime_inp.write_text(code, encoding="ascii")
    if dry_run:
        shutil.copytree(prep.runtime_dir, prep.project_dir / ".mplus_runtime" / "生成记录", dirs_exist_ok=True)
        shutil.rmtree(prep.runtime_dir, ignore_errors=True)
        return {"状态": "仅生成代码_未运行Mplus", "项目目录": str(prep.project_dir), "代码文件": str(visible_inp), "代码说明": str(explanation)}

    assert env is not None and env.command is not None
    proc = subprocess.run(
        [env.command, runtime_inp.name], cwd=prep.runtime_dir, capture_output=True,
        text=True, errors="ignore", timeout=timeout_seconds,
    )
    (prep.runtime_dir / "process.log").write_text(
        f"returncode={proc.returncode}\n\n[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}\n",
        encoding="utf-8", errors="ignore",
    )
    runtime_out = prep.runtime_dir / "model.out"
    if not runtime_out.exists():
        raise RuntimeError(f"Mplus 没有生成 model.out；进程返回码 {proc.returncode}。")
    visible_out = prep.project_dir / "04_Mplus原始结果" / f"{design['analysis']}_Mplus输出.out"
    shutil.copy2(runtime_out, visible_out)
    summary = _fit_summary(runtime_out)
    summary.update({
        "Mplus版本": env.version,
        "Mplus版本适配配置": env.version_profile,
        "环境自检凭证": receipt,
    })
    write_json(prep.project_dir / "05_分析结果" / "模型结果摘要.json", summary)
    pd.DataFrame([{k: v for k, v in summary.items() if not isinstance(v, (list, dict))}]).to_excel(
        prep.project_dir / "05_分析结果" / "模型结果摘要.xlsx", index=False
    )
    if summary.get("EFA因子解"):
        efa_table = pd.DataFrame(summary["EFA因子解"])
        efa_table.to_excel(prep.project_dir / "05_分析结果" / "EFA因子解比较表.xlsx", index=False)
        efa_table.to_csv(prep.project_dir / "05_分析结果" / "EFA因子解比较表.csv", index=False, encoding="utf-8-sig")
    reverse_names = {internal: original for original, internal in prep.internal_map.items()}
    reverse_names.update({f"F{i:06d}": name for i, name in enumerate((design.get("factors") or {}).keys(), 1)})

    def mapped_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
        table = pd.DataFrame(rows)
        for column in ["Mplus变量", "结果变量/因子", "预测变量/指标", "起点", "终点"]:
            if column in table.columns:
                table[column] = table[column].map(lambda value: reverse_names.get(str(value), value))
        return table

    exports = [
        ("EFA旋转载荷", "EFA旋转载荷表"),
        ("标准化参数", "标准化载荷与路径表"),
        ("标准化间接效应", "标准化间接效应表"),
    ]
    generated_tables: list[str] = []
    for key, filename in exports:
        if summary.get(key):
            table = mapped_table(summary[key])
            table.to_excel(prep.project_dir / "05_分析结果" / f"{filename}.xlsx", index=False)
            table.to_csv(prep.project_dir / "05_分析结果" / f"{filename}.csv", index=False, encoding="utf-8-sig")
            generated_tables.append(filename)
    mixture_unstable = design["analysis"] == "lca" and int(design.get("class_count", 1)) >= 2 and summary["最佳LL重复"] is not True
    status = "完成" if summary["正常结束"] and not summary["Mplus错误"] and not summary["重大警告"] and not mixture_unstable else "完成_存在重大问题"
    report_lines = [
        f"# {family.name_zh}分析报告", "", "## 分析设计", "",
        f"- 分析类型：{family.name_zh}", f"- 变量数：{len(design['variables'])}",
        f"- Mplus 版本：{env.version}", f"- 版本适配配置：{env.version_profile}",
        f"- 输出解析配置：{summary.get('输出解析配置', '未记录')}",
        f"- 环境验证：{receipt.get('验证状态', '已通过本机自检')}", f"- 运行状态：{status}", "", "## 模型摘要", "",
    ]
    base_keys = ["实际样本数", "估计量"] if summary.get("EFA因子解") else ["实际样本数", "估计量", "卡方", "自由度", "CFI", "TLI", "RMSEA", "SRMR", "AIC", "BIC", "SABIC", "Entropy"]
    for key in base_keys:
        if summary.get(key) is not None:
            report_lines.append(f"- {key}：{summary[key]}")
    if summary.get("EFA因子解"):
        report_lines.extend(["", "| 因子数 | 卡方 | 自由度 | CFI | TLI | RMSEA | SRMR |", "|---:|---:|---:|---:|---:|---:|---:|"])
        for row in summary["EFA因子解"]:
            report_lines.append(f"| {row['因子数']} | {row['卡方']} | {row['自由度']} | {row['CFI']} | {row['TLI']} | {row['RMSEA']} | {row['SRMR']} |")
    if generated_tables:
        report_lines.extend(["", "可编辑的参数结果已另存为：" + "、".join(f"《{name}.xlsx》" for name in generated_tables) + "。"])
    report_lines.extend(["", *advisory_markdown(prep.spec["样本量提示"])])
    report_lines.extend(["", "## 质量检查", ""])
    if not summary["正常结束"]:
        report_lines.append("- 重大：Mplus 没有报告模型正常结束，当前结果不得用于实质结论。")
    if summary["重大警告"]:
        report_lines.extend(f"- 重大：{x}" for x in summary["重大警告"])
    if summary["Mplus错误"]:
        report_lines.extend(f"- Mplus 错误：{x}" for x in summary["Mplus错误"])
    if mixture_unstable:
        report_lines.append("- 重大：最佳 loglikelihood 未稳定重复，当前 LCA 结果不建议用于结论。")
    if summary["Mplus警告"]:
        report_lines.extend(f"- Mplus 警告：{x}" for x in summary["Mplus警告"])
    if summary.get("自由度") == 0:
        report_lines.append("- 提示：该模型恰好识别（df=0），CFI=1、RMSEA=0 等完美拟合值不能用于评价模型优劣。")
    if summary["正常结束"] and not summary["重大警告"] and not summary["Mplus错误"] and not mixture_unstable and not summary["Mplus警告"]:
        report_lines.append("- 程序未识别到会直接使结果失效的错误或重大警告。")
    if receipt.get("验证状态") == "试运行（未完成本机自检）":
        report_lines.append("- 提示：本次为用户明确同意的未自检试运行。可先核对代码与数据；正式研究结论前应完成本机自检并复跑。")
    report_lines.extend([
        "", "## 解读边界", "",
        "拟合指标只是模型证据的一部分，不使用单一阈值自动宣布模型成立。结论还需核对参数估计、识别状态、数据特征与理论设定。",
        "", "## 代码依据", "", f"- {family.source}",
        "- Mplus User's Guide: https://www.statmodel.com/html_ug.shtml",
    ])
    (prep.project_dir / "06_分析报告" / f"{family.name_zh}分析报告.md").write_text("\n".join(report_lines), encoding="utf-8")
    (prep.project_dir / "07_参考依据" / "代码模板来源.md").write_text(
        f"# 代码模板来源\n\n- 分析家族：{family.name_zh}\n- 官方依据：{family.source}\n- https://www.statmodel.com/html_ug.shtml\n",
        encoding="utf-8",
    )
    write_json(prep.project_dir / ".mplus_runtime" / "manifest.json", {
        "状态": status, "分析设计": prep.spec, "结果摘要": summary, "环境验证": receipt,
    })
    shutil.copytree(prep.runtime_dir, prep.project_dir / ".mplus_runtime" / "执行记录", dirs_exist_ok=True)
    shutil.rmtree(prep.runtime_dir, ignore_errors=True)
    return {
        "状态": status, "项目目录": str(prep.project_dir),
        "Mplus输出": str(visible_out),
        "结果摘要": str(prep.project_dir / "05_分析结果" / "模型结果摘要.xlsx"),
        "分析报告": str(prep.project_dir / "06_分析报告" / f"{family.name_zh}分析报告.md"),
        "代码说明": str(explanation),
        "环境验证状态": receipt.get("验证状态", "已通过本机自检"),
    }


def load_design(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("分析设计 JSON 顶层必须是对象。")
    return data
