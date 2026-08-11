from __future__ import annotations

from typing import Any


_RULES: list[tuple[tuple[str, ...], str, str, str]] = [
    (
        ("VARIANCE", "MAXIMUM ALLOWED"),
        "方差异常大",
        "常见原因是特殊缺失码被当成真实数值、小数点或单位错误，或数据列与变量名错位。",
        "先看变量对应表和转换前后描述统计；回查极端值、缺失码和原始单位，不要仅靠标准化掩盖问题。",
    ),
    (
        ("NON-NUMERIC DATA",),
        "数据中出现非数值内容",
        "Mplus 的无表头数据区只能读取数值；学校名、班级名、中文标签或编码异常都可能触发此错误。",
        "保留原始文件不变，让 Skill 只导出模型需要的数值列；文本聚类/分组变量应由 Skill 自动编码。",
    ),
    (
        ("UNEXPECTED END OF FILE",),
        "数据字段数与变量声明不一致",
        "实际数据字段少于 NAMES 声明，Mplus 会跨行拼接，样本数和变量值随之错位。",
        "核对转换报告中的行数、列数和逐值回读结果；不要手工修改 DAT 或 NAMES 顺序。",
    ),
    (
        ("NO VARIATION WITHIN A CLUSTER",),
        "个体层变量在部分聚类内没有变异",
        "这可能是真实的小组特征，也可能是层级声明、聚类编号或变量顺序有误。",
        "查看报告列出的聚类数量；若所有聚类都无组内变异，应把变量改为群体层或回查数据错位。",
    ),
    (
        ("NO WITHIN-CLUSTER VARIATION",),
        "个体层变量在部分聚类内没有变异",
        "这可能是真实的小组特征，也可能是层级声明、聚类编号或变量顺序有误。",
        "查看报告列出的聚类数量；若所有聚类都无组内变异，应把变量改为群体层或回查数据错位。",
    ),
    (
        ("BETWEEN-LEVEL", "VARIATION WITHIN"),
        "群体层变量在同一聚类内出现多个值",
        "纯 BETWEEN 变量按定义应在每个学校、班级等聚类内恒定。",
        "核对聚类编号和变量顺序；若变量本来随个体变化，应重新声明为 WITHIN 或未分解变量。",
    ),
    (
        ("MODEL MAY NOT BE IDENTIFIED",),
        "模型可能不可识别",
        "参数过多、路径重复、变量共线、样本信息不足或模型约束不完整都可能造成这一问题。",
        "先从更简单的测量或结构模型开始，核对自由参数、方差和相关，不要只增加迭代次数。",
    ),
    (
        ("NO CONVERGENCE",),
        "模型未收敛",
        "未收敛可能来自数据问题、模型识别、极端参数或混合模型局部最优。",
        "先处理输出中的首个错误和数据检查项，再考虑合理增加随机起始值或迭代次数。",
    ),
]


def diagnose_mplus_messages(
    messages: list[str],
    expected_rows: int | None,
    actual_rows: int | None,
) -> list[dict[str, Any]]:
    """Translate common Mplus failures without turning every warning into a hard stop."""
    combined = "\n".join(str(item) for item in messages).upper()
    findings: list[dict[str, Any]] = []
    seen_issues: set[str] = set()
    for fragments, issue, explanation, advice in _RULES:
        if issue not in seen_issues and all(fragment in combined for fragment in fragments):
            findings.append({"问题": issue, "解释": explanation, "建议": advice})
            seen_issues.add(issue)
    if expected_rows and actual_rows is not None and actual_rows != expected_rows:
        findings.append({
            "问题": "Mplus 实际样本数与转换数据行数不同",
            "解释": (
                f"转换文件已经逐行逐列回读为 {expected_rows} 行，但 Mplus 实际使用 {actual_rows} 行。"
                "因此应优先检查预测变量缺失、分组/筛选条件和模型变量的有效样本，而不是重新修改文件编码。"
            ),
            "建议": "比较原始样本、各模型变量非缺失样本和 Mplus 输出中的删除说明；报告必须说明样本为何减少。",
        })
    return findings
