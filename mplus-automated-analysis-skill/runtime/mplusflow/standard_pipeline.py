from __future__ import annotations

import json
import math
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
from .data_io import INTERNAL_MISSING, SENTINEL_CANDIDATES, load_dataframe, write_verified_mplus_data
from .diagnostics import diagnose_mplus_messages
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


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip().strip(".")
    return cleaned or "分析结果"


def _new_runtime_dir() -> Path:
    parent = os.getenv("MPLUSFLOW_TEMP")
    runtime = Path(tempfile.mkdtemp(prefix="mplusflow-standard-", dir=parent or None))
    if not str(runtime).isascii():
        shutil.rmtree(runtime, ignore_errors=True)
        raise RuntimeError("Mplus 临时执行路径含非 ASCII 字符；请设置 MPLUSFLOW_TEMP 为纯英文路径。")
    return runtime


def _validate_multilevel_structure(
    design: dict[str, Any], work: pd.DataFrame
) -> tuple[dict[str, Any], list[str]]:
    final_design = dict(design)
    warnings: list[str] = []
    cluster_name = str(design.get("cluster", ""))
    if not cluster_name:
        return final_design, warnings
    if work[cluster_name].isna().any():
        raise ValueError("聚类变量存在缺失值；请先核对缺失产生原因和分析样本。")
    cluster_sizes = work.groupby(cluster_name).size()
    final_design["cluster_count"] = int(cluster_sizes.size)
    final_design["cluster_size_min"] = int(cluster_sizes.min())
    final_design["cluster_size_max"] = int(cluster_sizes.max())
    for variable in [str(x) for x in design.get("between_variables", [])]:
        varying = work.groupby(cluster_name)[variable].nunique(dropna=True)
        offenders = varying[varying > 1]
        if not offenders.empty:
            examples = ", ".join(map(str, offenders.index[:5].tolist()))
            raise ValueError(
                f"群体层变量“{variable}”在 {len(offenders)} 个聚类内出现多个取值（例如 {examples}）。"
                "它不能声明为纯 BETWEEN 变量；请核对变量顺序、聚类编号或层级归属。"
            )
    for variable in [str(x) for x in design.get("within_variables", [])]:
        variation = work.groupby(cluster_name)[variable].nunique(dropna=True)
        varying_clusters = int((variation > 1).sum())
        if varying_clusters == 0:
            raise ValueError(
                f"个体层变量“{variable}”在所有聚类内都没有变异。它更像群体层变量，"
                "或数据列已经错位；请核对后再运行。"
            )
        no_variation = int((variation <= 1).sum())
        if no_variation:
            warnings.append(
                f"个体层变量“{variable}”在 {no_variation}/{len(variation)} 个聚类内没有变异；"
                "分析仍可继续，但这些聚类不能为该变量的组内效应提供信息。"
            )
    return final_design, warnings


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

    missing_codes = [float(value) for value in (missing_codes or [])]
    missing_by_variable = design.get("missing_by_variable") or {}
    confirmed_valid_values = design.get("confirmed_valid_values") or {}
    if not isinstance(missing_by_variable, dict) or not isinstance(confirmed_valid_values, dict):
        raise ValueError("missing_by_variable 和 confirmed_valid_values 必须是变量到数值列表的对象。")
    for field_name, values_by_variable in [
        ("missing_by_variable", missing_by_variable),
        ("confirmed_valid_values", confirmed_valid_values),
    ]:
        unknown = sorted(set(map(str, values_by_variable)) - set(variables))
        if unknown:
            raise ValueError(f"{field_name} 引用了未登记变量：{unknown}")
        if any(not isinstance(values, list) for values in values_by_variable.values()):
            raise ValueError(f"{field_name} 的每个变量都必须对应数值列表。")
    variable_missing = {
        str(variable): [float(value) for value in values]
        for variable, values in missing_by_variable.items()
    }
    variable_valid = {
        str(variable): [float(value) for value in values]
        for variable, values in confirmed_valid_values.items()
    }
    for variable in variables:
        conflict = set(missing_codes + variable_missing.get(variable, [])) & set(variable_valid.get(variable, []))
        if conflict:
            raise ValueError(f"变量“{variable}”的值 {sorted(conflict)} 同时被声明为缺失和有效。")
    work = df[variables].copy()
    for variable in variables:
        codes = [*missing_codes, *variable_missing.get(variable, [])]
        if codes:
            work[variable] = work[variable].replace(codes, np.nan)

    undeclared: dict[str, list[float]] = {}
    role_code_maps: dict[str, dict[str, int]] = {}
    auto_code_roles = {str(design.get("cluster", "")), str(design.get("group", ""))} - {""}
    categorical = set(design.get("categorical", []))
    audit_rows: list[dict[str, Any]] = []
    scale_warnings: list[str] = []
    for var in variables:
        original = df[var]
        numeric = pd.to_numeric(work[var], errors="coerce")
        newly_missing = int(numeric.isna().sum() - work[var].isna().sum())
        if newly_missing and var in auto_code_roles:
            observed = [str(value) for value in original.dropna().unique()]
            code_map = {value: index for index, value in enumerate(observed, 1)}
            numeric = original.map(lambda value: np.nan if pd.isna(value) else code_map[str(value)]).astype(float)
            role_code_maps[var] = code_map
            newly_missing = 0
        if newly_missing:
            raise ValueError(
                f"分析变量“{var}”含 {newly_missing} 个无法转为数值的内容。"
                "如果它是学校名、班级名或姓名等文本标识且不参与模型，请不要把它放入 variables；"
                "若它是聚类或分组变量，请明确声明 cluster/group，由 Skill 自动编码并保留对应表。"
            )
        work[var] = numeric.astype(float)
        nonfinite = work[var].notna() & ~np.isfinite(work[var])
        if nonfinite.any():
            raise ValueError(f"分析变量“{var}”含 {int(nonfinite.sum())} 个无穷值，Mplus 无法可靠读取。")
        observed_values = set(pd.to_numeric(original, errors="coerce").dropna().tolist())
        declared_missing = set(missing_codes + variable_missing.get(var, []))
        declared_valid = set(variable_valid.get(var, []))
        suspicious = sorted(
            float(x) for x in observed_values.intersection(SENTINEL_CANDIDATES)
            if x not in declared_missing and x not in declared_valid
        )
        if suspicious:
            undeclared[var] = suspicious
        unique = int(work[var].nunique(dropna=True))
        if unique <= 1:
            raise ValueError(f"分析变量“{var}”没有有效变异。")
        if var in categorical:
            vals = work[var].dropna()
            if not np.allclose(vals, np.round(vals)):
                raise ValueError(f"分类变量“{var}”含非整数值，需先确认编码。")
        finite = work[var].dropna()
        mean = float(finite.mean())
        sd = float(finite.std(ddof=1)) if len(finite) > 1 else 0.0
        minimum = float(finite.min())
        maximum = float(finite.max())
        # Use a robust typical scale so one bad value cannot hide itself by inflating the mean.
        typical = max(float(finite.abs().median()), 1.0)
        if max(abs(minimum), abs(maximum)) > max(1e9, typical * 100000):
            scale_warnings.append(
                f"变量“{var}”存在相对其典型取值极大的观测（范围 {minimum:g} 至 {maximum:g}）。"
                "这不会自动阻止分析，但请回查单位、小数点和特殊缺失码。"
            )
        audit_rows.append({
            "原变量名": var,
            "角色": "分类变量" if var in categorical else "连续变量",
            "非缺失数": int(work[var].notna().sum()),
            "缺失数": int(work[var].isna().sum()),
            "唯一值数": unique,
            "均值": mean,
            "标准差": sd,
            "最小值": minimum,
            "最大值": maximum,
        })
    if undeclared:
        raise ValueError(f"发现未声明的疑似缺失码：{undeclared}。请确认后通过 missing_codes 声明。")
    # Finish model-specific data validation before creating any output directory.
    final_design, level_warnings = _validate_multilevel_structure(design, work)

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
    if role_code_maps:
        role_rows = [
            {"变量": variable, "原值": original, "Mplus编码": code}
            for variable, code_map in role_code_maps.items()
            for original, code in code_map.items()
        ]
        role_table = pd.DataFrame(role_rows)
        role_table.to_excel(dirs["audit"] / "角色变量编码表.xlsx", index=False)
        role_table.to_csv(dirs["audit"] / "角色变量编码表.csv", index=False, encoding="utf-8-sig")

    analysis = pd.DataFrame({"ROWID": np.arange(1, len(df) + 1, dtype=int)})
    for original, internal in mapping.items():
        analysis[internal] = work[original]
    export_check = write_verified_mplus_data(
        analysis,
        [dirs["data"] / "Mplus分析数据.dat", runtime / "data.dat"],
    )

    id_map = pd.DataFrame({"ROWID": analysis["ROWID"]})
    if user_id:
        id_map[user_id] = df[user_id].map(lambda x: None if pd.isna(x) else str(x))
    id_map.to_excel(dirs["audit"] / "个案ID对应表.xlsx", index=False)
    id_map.to_csv(dirs["audit"] / "个案ID对应表.csv", index=False, encoding="utf-8-sig")

    size_advisory = sample_size_advisory(str(design["analysis"]), len(df), final_design)
    final_design.update({
        "模板ID": f"STRUCTURED-{str(design['analysis']).upper()}",
        "数据文件": str(src),
        "数据文件SHA256": sha256_file(src),
        "用户ID变量": user_id,
        "用户声明缺失码": missing_codes,
        "逐变量缺失码": variable_missing,
        "已确认有效的疑似缺失值": variable_valid,
        "内部缺失码": INTERNAL_MISSING,
        "原始样本数": int(len(df)),
        "has_missing": bool(work.isna().any().any()),
        "源数据元信息": source_meta,
        "Mplus数据转换核验": export_check,
        "内部变量对应": mapping,
        "自动编码对应": role_code_maps,
        "数据警告": [*scale_warnings, *level_warnings],
        "样本量提示": size_advisory,
    })
    write_json(dirs["data"] / "分析设计清单.json", final_design)
    audit = [
        "# 数据质量检查报告", "",
        f"- 分析类型：{get_family(str(design['analysis'])).name_zh}",
        f"- 原始样本数：{len(df)}", f"- 分析变量数：{len(variables)}",
        f"- 转换核验：{export_check['状态']}；{export_check['行数']} 行 × {export_check['列数']} 列；{export_check['编码']}", "",
        "## 变量检查", "",
    ]
    if source_meta.get("文本编码"):
        audit.insert(5, f"- 识别到的源文本编码：{source_meta['文本编码']}")
    audit.extend(
        f"- {r['原变量名']} -> {r['Mplus内部变量名']}；{r['角色']}；缺失 {r['缺失数']}；"
        f"唯一值 {r['唯一值数']}；均值 {r['均值']:.6g}；标准差 {r['标准差']:.6g}；"
        f"范围 [{r['最小值']:.6g}, {r['最大值']:.6g}]"
        for r in mapped_rows
    )
    if scale_warnings or level_warnings:
        audit.extend(["", "## 需要核对但不自动中止的问题", ""])
        audit.extend(f"- {item}" for item in [*scale_warnings, *level_warnings])
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
                    # Mplus prints the path from outcome back to predictor.
                    via = list(reversed(specific_path[1:-1]))
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


def _odds_ratios(text: str) -> list[dict[str, Any]]:
    start = re.search(r"LOGISTIC REGRESSION ODDS RATIO RESULTS", text, re.I)
    if not start:
        return []
    tail = text[start.end():]
    end = re.search(r"STANDARDIZED MODEL RESULTS", tail, re.I)
    section = tail[:end.start()] if end else tail
    rows: list[dict[str, Any]] = []
    outcome: str | None = None
    for line in section.splitlines():
        heading = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+ON\s*$", line, re.I)
        if heading:
            outcome = heading.group(1).upper()
            continue
        value = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+([0-9.]+)\s+([0-9.]+)\s+(-?[0-9.]+)\s+([0-9.]+)", line, re.I)
        if value and outcome:
            rows.append({
                "结果变量": outcome, "预测变量": value.group(1).upper(),
                "优势比": float(value.group(2)), "标准误": float(value.group(3)),
                "检验统计量": float(value.group(4)), "p": float(value.group(5)),
            })
    ci_start = re.search(r"CONFIDENCE INTERVALS FOR THE LOGISTIC REGRESSION ODDS RATIO RESULTS", text, re.I)
    intervals: dict[tuple[str, str], tuple[float, float]] = {}
    if ci_start:
        ci_tail = text[ci_start.end():]
        ci_end = re.search(r"CONFIDENCE INTERVALS OF STANDARDIZED MODEL RESULTS", ci_tail, re.I)
        ci_section = ci_tail[:ci_end.start()] if ci_end else ci_tail
        outcome = None
        for line in ci_section.splitlines():
            heading = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+ON\s*$", line, re.I)
            if heading:
                outcome = heading.group(1).upper()
                continue
            value = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+" + r"\s+".join([r"([0-9.]+)"] * 7), line, re.I)
            if value and outcome:
                intervals[(outcome, value.group(1).upper())] = (float(value.group(3)), float(value.group(7)))
    for row in rows:
        interval = intervals.get((str(row["结果变量"]), str(row["预测变量"])))
        row["95%CI下限"] = interval[0] if interval else None
        row["95%CI上限"] = interval[1] if interval else None
    return rows


def _additional_parameters(text: str) -> list[dict[str, Any]]:
    model_start = re.search(r"MODEL RESULTS", text, re.I)
    if not model_start:
        return []
    tail = text[model_start.end():]
    standardized = re.search(r"STANDARDIZED MODEL RESULTS", tail, re.I)
    section = tail[:standardized.start()] if standardized else tail
    start = re.search(r"New/Additional Parameters", section, re.I)
    if not start:
        return []
    rows: list[dict[str, Any]] = []
    for line in section[start.end():].splitlines():
        value = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+(-?[0-9.]+)\s+([0-9.]+)\s+(-?[0-9.]+)\s+([0-9.]+)", line, re.I)
        if value:
            rows.append({
                "参数": value.group(1).upper(), "估计": float(value.group(2)),
                "标准误": float(value.group(3)), "z": float(value.group(4)), "p": float(value.group(5)),
            })
        elif rows and not line.strip():
            break
    ci_start = re.search(r"CONFIDENCE INTERVALS OF MODEL RESULTS", text, re.I)
    intervals: dict[str, tuple[float, float]] = {}
    if ci_start:
        ci_tail = text[ci_start.end():]
        ci_end = re.search(r"CONFIDENCE INTERVALS OF STANDARDIZED MODEL RESULTS", ci_tail, re.I)
        ci_section = ci_tail[:ci_end.start()] if ci_end else ci_tail
        additional = re.search(r"New/Additional Parameters", ci_section, re.I)
        if additional:
            for line in ci_section[additional.end():].splitlines():
                value = re.match(r"\s*([A-Z][A-Z0-9_]{0,7})\s+" + r"\s+".join([r"(-?[0-9.]+)"] * 7), line, re.I)
                if value:
                    intervals[value.group(1).upper()] = (float(value.group(3)), float(value.group(7)))
                elif intervals and not line.strip():
                    break
    for row in rows:
        interval = intervals.get(str(row["参数"]))
        row["95%CI下限"] = interval[0] if interval else None
        row["95%CI上限"] = interval[1] if interval else None
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
    odds = _odds_ratios(text)
    if odds:
        summary["Logistic优势比"] = odds
    additional = _additional_parameters(text)
    if additional:
        summary["条件间接效应"] = additional
    return summary


def _post_run_contract(
    design: dict[str, Any], internal_map: dict[str, str], summary: dict[str, Any], output_path: Path
) -> list[str]:
    if summary.get("Mplus错误") or not summary.get("正常结束"):
        return []
    text = read_mplus_text(output_path)
    match = re.search(r"SUMMARY OF ANALYSIS(.*?)(?:\n\s*Estimator\s+)", text, re.I | re.S)
    actual = set(re.findall(r"\bV\d{6}\b", match.group(1), re.I)) if match else set()
    actual = {name.upper() for name in actual}
    expected = {name.upper() for name in internal_map.values()}
    issues: list[str] = []
    if not actual:
        issues.append("无法从 Mplus 的 SUMMARY OF ANALYSIS 核对实际分析变量。")
    elif actual != expected:
        issues.append(f"Mplus 实际识别变量 {sorted(actual)} 与设计变量 {sorted(expected)} 不一致。")
    analysis = str(design["analysis"])
    categorical = bool(design.get("categorical"))
    if analysis in {"logistic", "lca"}:
        expected_estimators = {"MLR"}
    elif analysis == "multilevel":
        expected_estimators = {"WLSMV"} if categorical else {"MLR"}
    elif categorical:
        expected_estimators = {"WLSMV"}
    else:
        expected_estimators = {"ML"}
    if summary.get("估计量") not in expected_estimators:
        issues.append(
            f"Mplus 实际估计量为 {summary.get('估计量') or '未识别'}，"
            f"当前受控模板预期为 {sorted(expected_estimators)}。"
        )
    if summary.get("实际样本数") is None:
        issues.append("无法从 Mplus 输出确认实际样本数。")
    return issues


def _measurement_quality(design: dict[str, Any], parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    factors = design.get("factors") or {}
    if not factors:
        return []
    latent_names = {name: f"F{i:06d}" for i, name in enumerate(factors, 1)}
    correlations: dict[tuple[str, str], float] = {}
    for row in parameters:
        if str(row.get("关系")) != "WITH":
            continue
        left = str(row.get("结果变量/因子"))
        right = str(row.get("预测变量/指标"))
        correlations[tuple(sorted((left, right)))] = abs(float(row["STDYX估计"]))
    rows: list[dict[str, Any]] = []
    for factor, indicators in factors.items():
        internal = latent_names[factor]
        loadings = [
            float(row["STDYX估计"])
            for row in parameters
            if row.get("关系") == "BY" and row.get("结果变量/因子") == internal
        ]
        if len(loadings) != len(indicators) or len(loadings) < 2:
            continue
        improper = [loading for loading in loadings if not math.isfinite(loading) or abs(loading) >= 1]
        if improper:
            rows.append({
                "因子": factor,
                "题项数": len(loadings),
                "组合信度_CR": None,
                "模型隐含omega近似": None,
                "AVE": None,
                "sqrt_AVE": None,
                "最大因子相关绝对值": None,
                "Fornell_Larcker描述": None,
                "计算状态": "未计算：标准化载荷存在绝对值大于或等于 1 的不当解风险。",
                "计算说明": "先处理 Heywood/识别问题，不用截断残差的方式强行计算信效度。",
            })
            continue
        error_sum = sum(max(0.0, 1 - loading**2) for loading in loadings)
        loading_sum = sum(loadings)
        denominator = loading_sum**2 + error_sum
        omega = loading_sum**2 / denominator if denominator > 0 else None
        ave = sum(loading**2 for loading in loadings) / len(loadings)
        other_latents = [value for key, value in latent_names.items() if key != factor]
        related = [correlations[pair] for other in other_latents if (pair := tuple(sorted((internal, other)))) in correlations]
        max_correlation = max(related) if related else None
        rows.append({
            "因子": factor,
            "题项数": len(loadings),
            "组合信度_CR": omega,
            "模型隐含omega近似": omega,
            "AVE": ave,
            "sqrt_AVE": math.sqrt(ave),
            "最大因子相关绝对值": max_correlation,
            "Fornell_Larcker描述": (
                math.sqrt(ave) > max_correlation if max_correlation is not None else None
            ),
            "计算状态": "已计算",
            "计算说明": (
                "基于标准化单因子载荷且假定题项残差不相关；"
                "交叉载荷、残差相关或更复杂测量模型需另行计算。"
                + ("分类指标下结果位于潜在反应尺度。" if design.get("categorical") else "")
            ),
        })
    return rows


MEASUREMENT_REFERENCES = [
    "Anderson & Gerbing (1988): https://doi.org/10.1037/0033-2909.103.3.411",
    "Hu & Bentler (1999): https://doi.org/10.1080/10705519909540118",
    "Marsh, Hau, & Wen (2004): https://doi.org/10.1207/S15328007SEM1103_2",
    "Fornell & Larcker (1981): https://doi.org/10.2307/3151312",
    "Rönkkö & Cho (2022): https://doi.org/10.1177/1094428120968614",
    "McNeish (2018): https://doi.org/10.1037/met0000144",
    "Fabrigar et al. (1999): https://doi.org/10.1037/1082-989X.4.3.272",
    "Horn (1965): https://doi.org/10.1007/BF02289447",
    "Xia & Yang (2019): https://doi.org/10.3758/s13428-018-1055-2",
    "Kenny, Kaniskan, & McCoach (2015): https://doi.org/10.1177/0049124114543236",
]


def _measurement_quality_gate(
    design: dict[str, Any], summary: dict[str, Any], reverse_names: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Turn measurement diagnostics into an actionable, non-mechanical gate."""
    analysis = str(design.get("analysis", ""))
    factors = design.get("factors") or {}
    if analysis != "efa" and not factors:
        return None
    reverse_names = reverse_names or {}
    issues: list[dict[str, str]] = []

    def add(level: str, item: str, actual: str, reference: str, explanation: str, source: str) -> None:
        issues.append({
            "级别": level, "项目": item, "实际结果": actual,
            "常用参照": reference, "说明": explanation, "来源": source,
        })

    run_failures = [
        *[str(x) for x in summary.get("Mplus错误", [])],
        *[str(x) for x in summary.get("重大警告", [])],
        *[str(x) for x in summary.get("运行后契约问题", [])],
    ]
    if summary.get("正常结束") is False or run_failures:
        actual = "、".join(run_failures[:3]) or "Mplus 未报告模型正常结束"
        add(
            "先修正再解释", "模型估计与识别", actual,
            "Mplus 正常结束且无重大识别/契约问题",
            "关键参数或拟合结果可能不可用；不应在缺少这些证据时显示“可继续”。",
            "本次 Mplus 输出与运行后契约检查",
        )

    for factor, indicators in factors.items():
        count = len(indicators)
        if count == 1:
            add(
                "先修正再解释", f"因子 {factor} 的指标数", "1 题", "标准潜变量通常至少需要多个指标",
                "单题不能在普通 CFA/SEM 模板中同时识别潜变量与测量误差。",
                "Bollen (1989); Brown (2015)",
            )
        elif count == 2:
            add(
                "需要重点说明", f"因子 {factor} 的指标数", "2 题", "3 题及以上是更稳妥的设计建议",
                "两题因子并非在所有模型中都非法，但识别依赖模型结构或额外约束，且可检验信息较少。",
                "Bollen (1989); Brown (2015)",
            )

    def check_fit(row: dict[str, Any], prefix: str = "整体模型") -> None:
        df = row.get("自由度")
        if df == 0:
            add(
                "需要重点说明", f"{prefix}拟合", "df=0", "恰好识别模型不能用整体拟合指标评价",
                "CFI=1 或 RMSEA=0 在此处不是拟合优秀的证据。", "Brown (2015)",
            )
            return
        if df is not None and 0 < float(df) <= 4:
            add(
                "参考提示", f"{prefix}RMSEA适用边界", f"df={float(df):g}",
                "低自由度时不用 RMSEA 单独判定拟合",
                "低自由度模型的 RMSEA 可对样本波动过度敏感。",
                "Kenny, Kaniskan, & McCoach (2015)",
            )
        categorical_fit = bool(design.get("categorical"))
        if categorical_fit:
            add(
                "参考提示", f"{prefix}分类指标拟合边界",
                f"估计量 {summary.get('估计量') or '未识别'}",
                "不机械照搬连续数据 ML 的 Hu-Bentler 参照",
                "WLSMV/DWLS 在有序分类数据下可产生更小 RMSEA 和更大 CFI/TLI；"
                "应加看阈值、载荷、残差与参数合理性。",
                "Xia & Yang (2019)",
            )
        references = {
            "CFI": (0.95, "higher"), "TLI": (0.95, "higher"),
            "RMSEA": (0.06, "lower"), "SRMR": (0.08, "lower"),
        }
        for key, (target, direction) in references.items():
            value = row.get(key)
            if value is None:
                continue
            missed = value < target if direction == "higher" else value > target
            if not missed:
                continue
            severe = value < 0.90 if direction == "higher" else value > 0.10
            add(
                "需要重点说明" if severe else "参考提示",
                f"{prefix}{key}", f"{float(value):.3f}",
                f"接近 {target:.2f}" + (" 或更高" if direction == "higher" else " 或更低"),
                "这是对连续数据 ML 两指标策略的历史定位参照，不是跨估计量、跨领域的发表硬门槛。",
                "Hu & Bentler (1999); Marsh, Hau, & Wen (2004)"
                + ("; Xia & Yang (2019)" if categorical_fit else ""),
            )

    if analysis == "efa":
        selected = design.get("selected_factors")
        solutions = summary.get("EFA因子解") or []
        if selected is None:
            add(
                "需要重点说明", "EFA 因子数", "尚未确认最终因子解",
                "结合平行分析、碎石图、拟合、载荷与可解释性",
                "在未确认因子解前，不应由程序直接把 EFA 结果当作已验证量表送入 CFA/SEM。",
                "Horn (1965); Fabrigar et al. (1999)",
            )
        else:
            chosen = next((row for row in solutions if int(row.get("因子数", -1)) == int(selected)), None)
            if chosen is None:
                add(
                    "先修正再解释", "EFA 因子数", str(selected), "必须存在于本次估计的因子解范围",
                    "设计中选择的因子数没有对应输出。", "Fabrigar et al. (1999)",
                )
            else:
                check_fit(chosen, f"{selected} 因子解")
                loading_rows = [
                    row for row in summary.get("EFA旋转载荷", [])
                    if int(row.get("因子解", -1)) == int(selected)
                ]
                by_item: dict[str, list[float]] = {}
                for row in loading_rows:
                    item = reverse_names.get(str(row.get("Mplus变量")), str(row.get("Mplus变量")))
                    by_item.setdefault(item, []).append(abs(float(row["旋转载荷"])))
                for item, values in by_item.items():
                    ordered = sorted(values, reverse=True)
                    primary = ordered[0]
                    second = ordered[1] if len(ordered) > 1 else 0.0
                    if primary < 0.40:
                        add(
                            "需要重点说明", f"EFA 指标 {item}", f"最大绝对载荷 {primary:.3f}",
                            "Skill 工作提醒线：.40", "主载荷较弱；不要仅凭这一条自动删题，应回到题意与量表设计。",
                            "Brown (2015); Skill 工作提醒线",
                        )
                    elif second >= 0.30 and primary - second < 0.20:
                        add(
                            "需要重点说明", f"EFA 指标 {item}",
                            f"主载荷 {primary:.3f}，次载荷 {second:.3f}",
                            "Skill 工作提醒线：次载荷 <.30 或主次差 >=.20",
                            "存在较明显交叉载荷，需要结合题意、旋转方法和替代因子解解释。",
                            "Fabrigar et al. (1999); Skill 工作提醒线",
                        )
    else:
        check_fit(summary)
        latent_names = {name: f"F{i:06d}" for i, name in enumerate(factors, 1)}
        factor_by_internal = {internal: original for original, internal in latent_names.items()}
        for row in summary.get("标准化参数", []):
            if row.get("关系") != "BY":
                continue
            loading = abs(float(row["STDYX估计"]))
            if loading >= 0.50:
                continue
            factor = factor_by_internal.get(str(row.get("结果变量/因子")), str(row.get("结果变量/因子")))
            item = reverse_names.get(str(row.get("预测变量/指标")), str(row.get("预测变量/指标")))
            add(
                "需要重点说明" if loading < 0.40 else "参考提示",
                f"{factor} 的指标 {item}", f"标准化载荷 {loading:.3f}；解释方差约 {loading**2:.1%}",
                "Skill 工作提醒线：.40 以下较弱，.40-.49 需说明",
                "载荷提醒用于定位问题，不是自动删题指令；应同时检查内容效度、残差和模型设定。",
                "Brown (2015); Skill 工作提醒线",
            )
        for row in summary.get("信效度指标", []):
            factor = str(row.get("因子"))
            if row.get("计算状态") not in {None, "已计算"}:
                add(
                    "先修正再解释", f"{factor} 的信效度计算",
                    str(row.get("计算状态")), "标准化解无 Heywood/识别问题",
                    "不当解下强行计算 CR/omega/AVE 会掩盖模型问题。",
                    "Raykov (1997); Mplus 输出诊断",
                )
                continue
            reliability = row.get("组合信度_CR")
            ave = row.get("AVE")
            if reliability is not None and float(reliability) < 0.70:
                add(
                    "需要重点说明" if float(reliability) < 0.60 else "参考提示",
                    f"{factor} 的复合信度", f"模型隐含 CR/omega 近似 {float(reliability):.3f}", ".70 是常见历史规划参照",
                    "信度受题项数和模型假设影响，不应为了越过 .70 而机械删题。",
                    "Raykov (1997); McNeish (2018)",
                )
            if ave is not None and float(ave) < 0.50:
                add(
                    "需要重点说明" if reliability is not None and float(reliability) < 0.70 else "参考提示",
                    f"{factor} 的 AVE", f"AVE {float(ave):.3f}", ".50 是常用收敛效度参照",
                    "AVE 是收敛效度证据的一部分，不能脱离载荷与内容效度单独决定题项去留。",
                    "Fornell & Larcker (1981)",
                )
            if row.get("Fornell_Larcker描述") is False:
                add(
                    "需要重点说明", f"{factor} 的区分效度",
                    f"sqrt(AVE)={float(row['sqrt_AVE']):.3f}，最大因子相关={float(row['最大因子相关绝对值']):.3f}",
                    "sqrt(AVE) 高于与其他因子的相关绝对值",
                    "Fornell-Larcker 描述未满足；这是风险信号而非唯一判据。即使满足，也不能单独证明区分效度。",
                    "Fornell & Larcker (1981); Rönkkö & Cho (2022)",
                )

    if any(row["级别"] == "先修正再解释" for row in issues):
        status = "先修正再解释"
        advice = "先解决识别、因子解或估计问题，再进入正式的结构解释。"
    elif any(row["级别"] == "需要重点说明" for row in issues):
        status = "带限制继续"
        advice = (
            "可以继续计算 SEM、中介或 LPA，但后续报告必须保留这些测量限制，"
            "并将结论写成条件性或探索性证据；后续显著结果不能反向证明测量已经合格。"
        )
    else:
        status = "可继续"
        advice = "未触发需要限制后续分析的测量问题；仍需结合理论、内容效度和参数估计解释。"
    return {
        "状态": status,
        "问题数": len(issues),
        "问题": issues,
        "后续分析建议": advice,
        "阈值说明": "常用参考用于定位和说明问题，不是自动发表或毕业判定线。",
        "参考文献": MEASUREMENT_REFERENCES,
    }


def _map_result_table(rows: list[dict[str, Any]], reverse_names: dict[str, str]) -> pd.DataFrame:
    table = pd.DataFrame(rows)
    for column in ["Mplus变量", "结果变量/因子", "预测变量/指标", "起点", "终点"]:
        if column in table.columns:
            table[column] = table[column].map(lambda value: reverse_names.get(str(value), value))
    if "路径" in table.columns:
        table["路径"] = table["路径"].map(
            lambda value: value if pd.isna(value) else " -> ".join(
                reverse_names.get(part.strip(), part.strip()) for part in str(value).split("->")
            )
        )
    return table


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
                "重大问题": bool(
                    summary.get("Mplus错误") or summary.get("重大警告") or summary.get("运行后契约问题")
                ),
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
    excluded_count = 0
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
        design_data = json.loads((selected_root / "02_分析数据" / "分析设计清单.json").read_text(encoding="utf-8"))
        internal_variables = list(design_data.get("内部变量对应", {}).values())
        exported = pd.read_csv(
            selected_root / "02_分析数据" / "Mplus分析数据.dat",
            sep=r"\s+", header=None, engine="python",
        )
        expected_columns = ["ROWID", *internal_variables]
        if exported.shape[1] != len(expected_columns):
            raise RuntimeError(
                f"LCA 分析数据实际 {exported.shape[1]} 列，应为 {len(expected_columns)} 列。"
            )
        exported.columns = expected_columns
        missing_value = float(design_data["内部缺失码"])
        valid_mask = ~exported[internal_variables].eq(missing_value).all(axis=1)
        expected_ids = set(exported.loc[valid_mask, "ROWID"].astype(int))
        saved_ids = set(assignments["ROWID"].tolist())
        if saved_ids != expected_ids:
            missing_ids = sorted(expected_ids - saved_ids)[:10]
            extra_ids = sorted(saved_ids - expected_ids)[:10]
            raise RuntimeError(
                f"LCA SAVEDATA 的有效 ROWID 与分析数据不一致；缺少 {missing_ids}，多出 {extra_ids}。"
            )
        excluded_count = int(len(id_map) - len(expected_ids))
        id_map["纳入LCA估计"] = pd.to_numeric(id_map["ROWID"], errors="raise").astype(int).isin(expected_ids)
        assignments = id_map.merge(assignments, on="ROWID", how="left", validate="one_to_one")
        reverse = {internal: original for original, internal in design_data.get("内部变量对应", {}).items()}
        assignments = assignments.rename(columns={
            **reverse,
            **{f"CPROB{i}": f"类别{i}后验概率" for i in range(1, candidate + 1)},
            "C": "最可能类别",
        })
        classification_columns = [
            column for column in assignments.columns
            if column == "最可能类别" or str(column).startswith("类别") and str(column).endswith("后验概率")
        ]
        if classification_columns and assignments.loc[assignments["纳入LCA估计"], classification_columns].isna().any().any():
            raise RuntimeError("有效分析个案的 LCA 类别归属或后验概率存在空值。")
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
    if excluded_count:
        report.extend([
            "", f"有 {excluded_count} 个个案的全部 LCA 指标均缺失，未进入模型估计。"
            "这些个案仍保留在《个体类别归属.xlsx》中，并标记为未纳入；类别与后验概率留空。"
        ])
    report.extend(["", "官方来源：Mplus User's Guide Chapter 7, Example 7.3。", "https://www.statmodel.com/usersguide/chapter7.shtml"])
    (report_dir / "LCA模型比较报告.md").write_text("\n".join(report), encoding="utf-8")
    return {
        "状态": "完成" if candidate is not None else "完成_无可用候选",
        "项目目录": str(root), "统计候选类别数": candidate,
        "模型比较表": str(result_dir / "LCA类别模型比较表.xlsx"),
        "个体类别归属": str(assignment_path) if assignment_path else None,
        "类别题项反应概率": str(probability_path) if probability_path else None,
        "分析报告": str(report_dir / "LCA模型比较报告.md"), "冲突": conflict,
        "全指标缺失未纳入个案数": excluded_count,
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
        hints = diagnose_mplus_messages(
            [proc.stdout or "", proc.stderr or ""],
            expected_rows=int(prep.spec["Mplus数据转换核验"]["行数"]),
            actual_rows=None,
        )
        detail = "；".join(item["建议"] for item in hints[:2])
        suffix = f" 建议：{detail}" if detail else ""
        raise RuntimeError(f"Mplus 没有生成 model.out；进程返回码 {proc.returncode}。{suffix}")
    visible_out = prep.project_dir / "04_Mplus原始结果" / f"{design['analysis']}_Mplus输出.out"
    shutil.copy2(runtime_out, visible_out)
    summary = _fit_summary(runtime_out)
    summary["运行后契约问题"] = _post_run_contract(prep.spec, prep.internal_map, summary, runtime_out)
    summary["诊断提示"] = diagnose_mplus_messages(
        [*summary.get("Mplus错误", []), *summary.get("Mplus警告", [])],
        expected_rows=int(prep.spec["Mplus数据转换核验"]["行数"]),
        actual_rows=summary.get("实际样本数"),
    )
    measurement_quality = _measurement_quality(design, summary.get("标准化参数", []))
    if measurement_quality:
        summary["信效度指标"] = measurement_quality
    reverse_names = {internal: original for original, internal in prep.internal_map.items()}
    reverse_names.update({f"F{i:06d}": name for i, name in enumerate((design.get("factors") or {}).keys(), 1)})
    measurement_gate = _measurement_quality_gate(design, summary, reverse_names)
    if measurement_gate:
        summary["测量质量提示"] = measurement_gate
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
    exports = [
        ("EFA旋转载荷", "EFA旋转载荷表"),
        ("标准化参数", "标准化载荷与路径表"),
        ("标准化间接效应", "标准化间接效应表"),
        ("Logistic优势比", "Logistic优势比"),
        ("条件间接效应", "条件间接效应表"),
        ("信效度指标", "信效度指标"),
    ]
    generated_tables: list[str] = []
    for key, filename in exports:
        if summary.get(key):
            table = _map_result_table(summary[key], reverse_names)
            table.to_excel(prep.project_dir / "05_分析结果" / f"{filename}.xlsx", index=False)
            table.to_csv(prep.project_dir / "05_分析结果" / f"{filename}.csv", index=False, encoding="utf-8-sig")
            generated_tables.append(filename)
    if measurement_gate:
        gate_table = pd.DataFrame(measurement_gate["问题"])
        if gate_table.empty:
            gate_table = pd.DataFrame([{
                "级别": "无重点问题", "项目": "测量质量关口", "实际结果": measurement_gate["状态"],
                "常用参照": "未触发当前规则", "说明": measurement_gate["后续分析建议"], "来源": "见测量质量判断依据",
            }])
        gate_table.to_excel(prep.project_dir / "05_分析结果" / "测量质量提示.xlsx", index=False)
        gate_table.to_csv(
            prep.project_dir / "05_分析结果" / "测量质量提示.csv", index=False, encoding="utf-8-sig",
        )
        generated_tables.append("测量质量提示")
    mixture_unstable = design["analysis"] == "lca" and int(design.get("class_count", 1)) >= 2 and summary["最佳LL重复"] is not True
    status = "完成" if summary["正常结束"] and not summary["Mplus错误"] and not summary["重大警告"] and not summary["运行后契约问题"] and not mixture_unstable else "完成_存在重大问题"
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
    if summary.get("信效度指标"):
        scale_note = "分类指标的数值位于潜在反应尺度。" if prep.spec.get("categorical") else ""
        report_lines.extend([
            "",
            "在标准化单因子、题项残差不相关且无不当解的条件下，"
            "CFA 载荷用于计算模型隐含的复合信度/omega 近似值与 AVE。"
            f"{scale_note}这些数值是测量证据的一部分，不替代模型设定、内容效度或区分效度判断。",
        ])
    if measurement_gate:
        report_lines.extend([
            "", "## 测量质量与后续分析", "",
            f"- 结论：**{measurement_gate['状态']}**",
            f"- 后续建议：{measurement_gate['后续分析建议']}",
            f"- 判断边界：{measurement_gate['阈值说明']}",
        ])
        for item in measurement_gate["问题"]:
            report_lines.append(
                f"- **{item['级别']} | {item['项目']}**：{item['实际结果']}；"
                f"常用参照：{item['常用参照']}。{item['说明']} "
                f"依据：{item['来源']}。"
            )
    report_lines.extend(["", *advisory_markdown(prep.spec["样本量提示"])])
    report_lines.extend(["", "## 质量检查", ""])
    if not summary["正常结束"]:
        report_lines.append("- 重大：Mplus 没有报告模型正常结束，当前结果不得用于实质结论。")
    if summary["重大警告"]:
        report_lines.extend(f"- 重大：{x}" for x in summary["重大警告"])
    if summary["Mplus错误"]:
        report_lines.extend(f"- Mplus 错误：{x}" for x in summary["Mplus错误"])
    if summary["运行后契约问题"]:
        report_lines.extend(f"- 重大：{x}" for x in summary["运行后契约问题"])
    if mixture_unstable:
        report_lines.append("- 重大：最佳 loglikelihood 未稳定重复，当前 LCA 结果不建议用于结论。")
    if summary["Mplus警告"]:
        report_lines.extend(f"- Mplus 警告：{x}" for x in summary["Mplus警告"])
    if summary.get("诊断提示"):
        report_lines.extend(["", "## 怎样排查", ""])
        for item in summary["诊断提示"]:
            report_lines.append(f"- **{item['问题']}**：{item['解释']} 建议：{item['建议']}")
    if summary.get("自由度") == 0:
        report_lines.append("- 提示：该模型恰好识别（df=0），CFI=1、RMSEA=0 等完美拟合值不能用于评价模型优劣。")
    if prep.spec.get("cluster_count") is not None:
        report_lines.extend([
            f"- 聚类数：{prep.spec['cluster_count']}；组大小范围："
            f"{prep.spec.get('cluster_size_min')}—{prep.spec.get('cluster_size_max')}。"
        ])
    if prep.spec.get("数据警告"):
        report_lines.extend(f"- 数据提示：{item}" for item in prep.spec["数据警告"])
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
    report_path = prep.project_dir / "06_分析报告" / _safe_filename(f"{family.name_zh}分析报告.md")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    (prep.project_dir / "07_参考依据" / "代码模板来源.md").write_text(
        f"# 代码模板来源\n\n- 分析家族：{family.name_zh}\n- 官方依据：{family.source}\n- https://www.statmodel.com/html_ug.shtml\n",
        encoding="utf-8",
    )
    measurement_reference = Path(__file__).resolve().parents[2] / "references" / "测量质量与后续分析.md"
    if measurement_gate and measurement_reference.exists():
        shutil.copy2(measurement_reference, prep.project_dir / "07_参考依据" / "测量质量判断依据.md")
    write_json(prep.project_dir / ".mplus_runtime" / "manifest.json", {
        "状态": status, "分析设计": prep.spec, "结果摘要": summary, "环境验证": receipt,
    })
    shutil.copytree(prep.runtime_dir, prep.project_dir / ".mplus_runtime" / "执行记录", dirs_exist_ok=True)
    shutil.rmtree(prep.runtime_dir, ignore_errors=True)
    return {
        "状态": status, "项目目录": str(prep.project_dir),
        "Mplus输出": str(visible_out),
        "结果摘要": str(prep.project_dir / "05_分析结果" / "模型结果摘要.xlsx"),
        "分析报告": str(report_path),
        "代码说明": str(explanation),
        "环境验证状态": receipt.get("验证状态", "已通过本机自检"),
        "测量质量状态": measurement_gate["状态"] if measurement_gate else None,
    }


def load_design(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("分析设计 JSON 顶层必须是对象。")
    return data
