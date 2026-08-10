from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

from .data_io import load_dataframe
from .utils import ensure_dir, write_json


def _role(series: pd.Series) -> str:
    unique = int(series.nunique(dropna=True))
    if pd.api.types.is_numeric_dtype(series):
        if unique == 2:
            return "二分类/多选指示候选"
        if 3 <= unique <= 10:
            return "有序分类或低基数数值"
        return "连续数值候选"
    if unique <= 20:
        return "文本分类候选"
    return "文本/ID候选"


def _question_groups(columns: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for column in columns:
        match = re.match(r"^(.+?)_(?:Row|Choice)\d+$", column, re.I)
        if match:
            groups.setdefault(match.group(1), []).append(column)
    return {key: value for key, value in groups.items() if len(value) >= 3}


def inspect_dataset(input_path: str | Path, output_dir: str | Path, text_columns: list[str] | None = None) -> dict[str, Any]:
    src = Path(input_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"输出目录已存在且非空：{output}。")
    ensure_dir(output)
    df, meta = load_dataframe(src, text_columns=text_columns)
    labels = meta.get("统计软件元数据", {}).get("变量标签", {})
    rows: list[dict[str, Any]] = []
    for column in df.columns:
        series = df[column]
        numeric = pd.to_numeric(series, errors="coerce") if not pd.api.types.is_numeric_dtype(series) else series
        rows.append({
            "变量名": column,
            "变量标签": labels.get(column, ""),
            "数据类型": str(series.dtype),
            "推测角色": _role(series),
            "非缺失数": int(series.notna().sum()),
            "缺失数": int(series.isna().sum()),
            "缺失比例": float(series.isna().mean()),
            "唯一值数": int(series.nunique(dropna=True)),
            "最小值": float(numeric.min()) if pd.api.types.is_numeric_dtype(numeric) and numeric.notna().any() else None,
            "最大值": float(numeric.max()) if pd.api.types.is_numeric_dtype(numeric) and numeric.notna().any() else None,
        })
    profile = pd.DataFrame(rows)
    profile.to_excel(output / "变量画像.xlsx", index=False)
    profile.to_csv(output / "变量画像.csv", index=False, encoding="utf-8-sig")
    groups = _question_groups(list(df.columns))
    binary_groups = [
        key for key, columns in groups.items()
        if all(int(df[column].nunique(dropna=True)) == 2 for column in columns)
    ]
    scale_groups = [key for key, columns in groups.items() if key not in binary_groups and len(columns) >= 3]
    recommendations: list[str] = []
    duplicate_rows = int(df.duplicated().sum())
    complete_rows = int(df.notna().all(axis=1).sum())
    high_missing = [str(row["变量名"]) for row in rows if float(row["缺失比例"]) >= 0.20]
    outlier_flags: dict[str, int] = {}
    for column in df.select_dtypes(include=[np.number]).columns:
        values = df[column].dropna()
        if values.nunique() < 10:
            continue
        q1, q3 = values.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr > 0:
            count = int(((values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr)).sum())
            if count:
                outlier_flags[str(column)] = count
    if scale_groups:
        recommendations.append(f"发现 {len(scale_groups)} 个至少 3 题的 Row 题组，可在确认量表含义后考虑 EFA/CFA。")
    if binary_groups:
        recommendations.append(f"发现 {len(binary_groups)} 个二元 Choice 题组，可在符合研究问题时考虑 LCA。")
    recommendations.extend([
        "未看到明确时间点和研究设计时，不自动建议增长模型、LTA 或 RI-CLPM。",
        "数据画像只用于缩小分析选择范围，不代替研究问题、量表计分和抽样设计确认。",
    ])
    if duplicate_rows:
        recommendations.append(f"发现 {duplicate_rows} 行完全重复记录；先核对样本编号和收集日志，不自动删除。")
    if high_missing:
        recommendations.append(f"有 {len(high_missing)} 个变量缺失比例达到 20%；建模前需说明缺失如何产生，并考虑敏感性分析。")
    if outlier_flags:
        recommendations.append(f"IQR 规则标记了 {len(outlier_flags)} 个数值变量中的可疑记录；标记不等于错误，不自动删除。")
    result = {
        "状态": "数据画像完成_未运行模型",
        "数据文件": str(src),
        "样本数": int(len(df)),
        "变量数": int(df.shape[1]),
        "题组": groups,
        "完全重复行数": duplicate_rows,
        "完整记录数": complete_rows,
        "高缺失变量": high_missing,
        "IQR异常标记数": outlier_flags,
        "可考虑的分析": recommendations,
    }
    write_json(output / "数据画像.json", result)
    lines = [
        "# 数据画像", "", f"- 样本数：{len(df)}", f"- 变量数：{df.shape[1]}",
        f"- 完全重复行：{duplicate_rows}", f"- 所有变量均完整的记录：{complete_rows}",
        f"- 缺失比例达到 20% 的变量数：{len(high_missing)}",
        f"- 被 IQR 规则标记的数值变量数：{len(outlier_flags)}", "",
        "异常标记只用于回查原始记录，不自动删除；缺失比例只描述现象，不能据此判断 MCAR、MAR 或 MNAR。", "",
        "## 可考虑的分析", "",
    ]
    lines.extend(f"- {item}" for item in recommendations)
    lines.extend(["", "## 隐私", "", "画像不输出文本变量的具体取值，不复制原始数据。"])
    (output / "数据画像.md").write_text("\n".join(lines), encoding="utf-8")
    return {
        "状态": result["状态"],
        "样本数": result["样本数"],
        "变量数": result["变量数"],
        "识别题组数": len(groups),
        "可考虑的分析": recommendations,
        "详细数据画像": str(output / "数据画像.md"),
        "变量画像表": str(output / "变量画像.xlsx"),
    }
