from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .data_io import load_dataframe
from .utils import ensure_dir, write_json


PRELIMINARY_FAMILIES = {"descriptive", "reliability", "correlation", "difference"}


def _variables_exist(df: pd.DataFrame, variables: list[str]) -> None:
    missing = [name for name in variables if name not in df.columns]
    if missing:
        raise ValueError(f"数据中找不到这些变量：{missing}")


def _numeric(df: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    _variables_exist(df, variables)
    result = pd.DataFrame(index=df.index)
    for name in variables:
        original = df[name]
        converted = pd.to_numeric(original, errors="coerce")
        bad = int(converted.isna().sum() - original.isna().sum())
        if bad:
            raise ValueError(f"变量“{name}”有 {bad} 个值无法转为数值。")
        result[name] = converted.astype(float)
    return result


def _holm(pvalues: list[float]) -> list[float]:
    count = len(pvalues)
    order = sorted(range(count), key=lambda i: pvalues[i])
    adjusted = [math.nan] * count
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (count - rank) * pvalues[index]))
        adjusted[index] = running
    return adjusted


def _descriptives(data: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name in data.columns:
        values = data[name].dropna()
        rows.append({
            "变量": name,
            "样本量": int(values.size),
            "缺失数": int(data[name].isna().sum()),
            "缺失比例": float(data[name].isna().mean()),
            "均值": float(values.mean()) if not values.empty else None,
            "标准差": float(values.std(ddof=1)) if values.size >= 2 else None,
            "中位数": float(values.median()) if not values.empty else None,
            "最小值": float(values.min()) if not values.empty else None,
            "最大值": float(values.max()) if not values.empty else None,
            "偏度": float(values.skew()) if values.size >= 3 else None,
            "峰度": float(values.kurt()) if values.size >= 4 else None,
            "唯一值数": int(values.nunique()),
        })
    return pd.DataFrame(rows)


def _alpha(data: pd.DataFrame) -> float | None:
    if data.shape[1] < 2 or len(data) < 2:
        return None
    item_variances = data.var(axis=0, ddof=1).sum()
    total_variance = data.sum(axis=1).var(ddof=1)
    if not np.isfinite(total_variance) or total_variance <= 0:
        return None
    k = data.shape[1]
    return float(k / (k - 1) * (1 - item_variances / total_variance))


def _reliability_tables(df: pd.DataFrame, design: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    scales = design.get("scales")
    if not isinstance(scales, dict) or not scales:
        raise ValueError("信度分析必须提供 scales，例如 {\"投入\": [\"q1\", \"q2\", \"q3\"]}。")
    if design.get("items_aligned") is not True:
        raise ValueError("计算信度前必须确认反向题已经正确计分，并设置 items_aligned=true。")
    summary_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for scale, raw_items in scales.items():
        items = [str(x) for x in raw_items]
        if len(items) < 2:
            raise ValueError(f"量表“{scale}”少于 2 个题项。")
        numeric = _numeric(df, items)
        complete = numeric.dropna()
        alpha = _alpha(complete)
        summary_rows.append({
            "量表": scale,
            "题项数": len(items),
            "完整样本量": len(complete),
            "Cronbach_alpha": alpha,
            "缺失处理": "量表内完整案例",
        })
        for item in items:
            rest = complete.drop(columns=[item]).sum(axis=1)
            correlation = complete[item].corr(rest) if len(complete) >= 3 and rest.nunique() > 1 else None
            item_rows.append({
                "量表": scale,
                "题项": item,
                "校正题总相关": float(correlation) if correlation is not None and np.isfinite(correlation) else None,
                "删题后_alpha": _alpha(complete.drop(columns=[item])),
            })
    return pd.DataFrame(summary_rows), pd.DataFrame(item_rows)


def _correlations(data: pd.DataFrame, method: str) -> pd.DataFrame:
    if method not in {"pearson", "spearman"}:
        raise ValueError("相关方法必须是 pearson 或 spearman。")
    rows: list[dict[str, Any]] = []
    columns = list(data.columns)
    for left_index, left in enumerate(columns):
        for right in columns[left_index + 1:]:
            pair = data[[left, right]].dropna()
            if len(pair) < 3 or pair[left].nunique() < 2 or pair[right].nunique() < 2:
                coefficient = pvalue = math.nan
            elif method == "pearson":
                coefficient, pvalue = stats.pearsonr(pair[left], pair[right])
            else:
                coefficient, pvalue = stats.spearmanr(pair[left], pair[right])
            rows.append({"变量1": left, "变量2": right, "方法": method, "成对样本量": len(pair), "相关系数": coefficient, "p": pvalue})
    valid = [float(row["p"]) for row in rows if np.isfinite(row["p"])]
    adjusted = iter(_holm(valid))
    for row in rows:
        row["Holm校正p"] = next(adjusted) if np.isfinite(row["p"]) else math.nan
    return pd.DataFrame(rows)


def _welch_anova(groups: list[np.ndarray]) -> tuple[float, float, float, float]:
    k = len(groups)
    sizes = np.array([len(group) for group in groups], dtype=float)
    means = np.array([np.mean(group) for group in groups], dtype=float)
    variances = np.array([np.var(group, ddof=1) for group in groups], dtype=float)
    if np.any(sizes < 2) or np.any(variances <= 0):
        return math.nan, float(k - 1), math.nan, math.nan
    weights = sizes / variances
    weighted_mean = np.sum(weights * means) / np.sum(weights)
    term = np.sum(((1 - weights / np.sum(weights)) ** 2) / (sizes - 1))
    df1 = float(k - 1)
    df2 = float((k**2 - 1) / (3 * term)) if term > 0 else math.inf
    numerator = np.sum(weights * (means - weighted_mean) ** 2) / df1
    denominator = 1 + (2 * (k - 2) / (k**2 - 1)) * term
    statistic = float(numerator / denominator)
    return statistic, df1, df2, float(stats.f.sf(statistic, df1, df2))


def _difference_tables(df: pd.DataFrame, design: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    pairs = design.get("pairs") or []
    if pairs:
        rows: list[dict[str, Any]] = []
        for raw_pair in pairs:
            if not isinstance(raw_pair, list) or len(raw_pair) != 2:
                raise ValueError("pairs 中每一项都必须是两个变量名。")
            left, right = map(str, raw_pair)
            pair = _numeric(df, [left, right]).dropna()
            result = stats.ttest_rel(pair[left], pair[right]) if len(pair) >= 2 else None
            differences = pair[left] - pair[right]
            dz = differences.mean() / differences.std(ddof=1) if len(pair) >= 2 and differences.std(ddof=1) > 0 else math.nan
            rows.append({
                "结果变量": f"{left} - {right}", "检验": "配对样本 t", "样本量": len(pair),
                "统计量": float(result.statistic) if result else math.nan,
                "自由度1": float(result.df) if result else math.nan, "自由度2": math.nan,
                "p": float(result.pvalue) if result else math.nan, "效应量": dz, "效应量名称": "Cohen dz",
            })
        return pd.DataFrame(rows), pd.DataFrame()

    outcomes = [str(x) for x in design.get("outcomes", [])]
    group_name = str(design.get("group", ""))
    if not outcomes or not group_name:
        raise ValueError("独立组差异分析必须提供 outcomes 和 group。")
    _variables_exist(df, [group_name, *outcomes])
    numeric = _numeric(df, outcomes)
    group = df[group_name]
    levels = [level for level in pd.unique(group.dropna())]
    if len(levels) < 2:
        raise ValueError("分组变量至少需要两个有观测的组。")
    rows: list[dict[str, Any]] = []
    posthoc_rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        arrays = [numeric.loc[group.eq(level), outcome].dropna().to_numpy() for level in levels]
        if any(len(values) < 2 for values in arrays):
            raise ValueError(f"结果变量“{outcome}”至少有一个组少于 2 个有效观测。")
        if len(levels) == 2:
            test = stats.ttest_ind(arrays[0], arrays[1], equal_var=False)
            n1, n2 = len(arrays[0]), len(arrays[1])
            s1, s2 = np.var(arrays[0], ddof=1), np.var(arrays[1], ddof=1)
            pooled = math.sqrt(((n1 - 1) * s1 + (n2 - 1) * s2) / (n1 + n2 - 2))
            d = (np.mean(arrays[0]) - np.mean(arrays[1])) / pooled if pooled > 0 else math.nan
            correction = 1 - 3 / (4 * (n1 + n2) - 9) if n1 + n2 > 2 else 1
            rows.append({
                "结果变量": outcome, "检验": "Welch 独立样本 t", "样本量": n1 + n2,
                "统计量": float(test.statistic), "自由度1": float(test.df), "自由度2": math.nan,
                "p": float(test.pvalue), "效应量": float(d * correction), "效应量名称": "Hedges g",
            })
        else:
            statistic, df1, df2, pvalue = _welch_anova(arrays)
            grand = numeric.loc[group.isin(levels), outcome].dropna()
            total_ss = float(((grand - grand.mean()) ** 2).sum())
            between_ss = sum(len(values) * (np.mean(values) - grand.mean()) ** 2 for values in arrays)
            rows.append({
                "结果变量": outcome, "检验": "Welch 单因素方差分析", "样本量": sum(map(len, arrays)),
                "统计量": statistic, "自由度1": df1, "自由度2": df2,
                "p": pvalue, "效应量": float(between_ss / total_ss) if total_ss > 0 else math.nan,
                "效应量名称": "eta squared（描述性）",
            })
            if design.get("posthoc"):
                pair_ps: list[float] = []
                pair_rows: list[dict[str, Any]] = []
                for i, left in enumerate(levels):
                    for j in range(i + 1, len(levels)):
                        right = levels[j]
                        test = stats.ttest_ind(arrays[i], arrays[j], equal_var=False)
                        pair_ps.append(float(test.pvalue))
                        pair_rows.append({"结果变量": outcome, "组1": str(left), "组2": str(right), "均值差": float(np.mean(arrays[i]) - np.mean(arrays[j])), "Welch_t": float(test.statistic), "p": float(test.pvalue)})
                for row, adjusted in zip(pair_rows, _holm(pair_ps)):
                    row["Holm校正p"] = adjusted
                posthoc_rows.extend(pair_rows)
    return pd.DataFrame(rows), pd.DataFrame(posthoc_rows)


def run_preliminary(
    design: dict[str, Any],
    input_path: str | Path,
    output_dir: str | Path,
    missing_codes: list[float] | None = None,
    text_columns: list[str] | None = None,
) -> dict[str, Any]:
    analysis = str(design.get("analysis", ""))
    if analysis not in PRELIMINARY_FAMILIES:
        raise ValueError(f"初步统计入口不支持分析类型“{analysis}”。")
    output = Path(output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"输出目录已存在且非空：{output}。")
    ensure_dir(output)
    df, source_meta = load_dataframe(input_path, text_columns=text_columns)
    for code in missing_codes or []:
        df = df.replace(code, np.nan)

    tables: dict[str, pd.DataFrame] = {}
    notes: list[str] = []
    if analysis == "descriptive":
        variables = [str(x) for x in design.get("variables", [])]
        if not variables:
            raise ValueError("描述统计必须提供 variables。")
        tables["描述统计"] = _descriptives(_numeric(df, variables))
    elif analysis == "reliability":
        summary, items = _reliability_tables(df, design)
        tables["量表信度"] = summary
        tables["题项诊断"] = items
        notes.append("Cronbach alpha 只描述内部一致性。量表结构仍需结合 EFA/CFA，不能把 alpha 当作单维性证据。")
    elif analysis == "correlation":
        variables = [str(x) for x in design.get("variables", [])]
        if len(variables) < 2:
            raise ValueError("相关分析至少需要两个 variables。")
        method = str(design.get("method", "pearson")).lower()
        tables["相关分析"] = _correlations(_numeric(df, variables), method)
        notes.append("相关不表示因果。Pearson 与 Spearman 的选择应依据变量尺度、关系形态和异常值情况。")
    else:
        main, posthoc = _difference_tables(df, design)
        tables["差异检验"] = main
        if not posthoc.empty:
            tables["事后比较"] = posthoc
        notes.append("差异检验同时报告效应量。检验方法不根据显著性结果事后更换。")

    workbook = output / "初步统计结果.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        for sheet, table in tables.items():
            table.to_excel(writer, sheet_name=sheet[:31], index=False)
    for name, table in tables.items():
        table.to_csv(output / f"{name}.csv", index=False, encoding="utf-8-sig")
    manifest = {
        "状态": "完成",
        "分析类型": analysis,
        "样本量": len(df),
        "源数据元信息": source_meta,
        "分析设计": design,
        "说明": notes,
    }
    write_json(output / "分析记录.json", manifest)
    report = [f"# {analysis} 分析报告", "", f"- 样本量：{len(df)}", f"- 输出工作簿：{workbook.name}", ""]
    report.extend(f"- {note}" for note in notes)
    report.extend(["", "原始数据没有被改写。缺失值只按本次设计中声明的编码处理。"])
    (output / "初步统计报告.md").write_text("\n".join(report), encoding="utf-8")
    return {"状态": "完成", "分析类型": analysis, "结果工作簿": str(workbook), "分析报告": str(output / "初步统计报告.md")}
