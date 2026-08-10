from __future__ import annotations

from typing import Any


SOURCES = {
    "factor": "MacCallum et al. (1999), https://doi.org/10.1037/1082-989X.4.1.84",
    "sem": "Wolf et al. (2013), https://doi.org/10.1177/0013164413495237",
    "mixture": "Tein et al. (2013), https://doi.org/10.1080/10705511.2013.824781",
    "multilevel": "Maas & Hox (2005), https://doi.org/10.1027/1614-2241.1.3.86",
}


def sample_size_advisory(analysis: str, sample_size: int, design: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a non-blocking planning warning, never a model-validity verdict."""
    family = analysis.lower()
    design = design or {}
    if family in {"efa", "esem"}:
        reference, complex_reference, source = 200, 300, SOURCES["factor"]
        context = "共同度、载荷强度、因子数和每个因子的指标数都会改变所需样本量。"
    elif family in {"lpa", "lca", "gmm", "lta"}:
        reference, complex_reference, source = 300, 500, SOURCES["mixture"]
        context = "类别分离度、最小类别占比、指标质量和类别数通常比单一总样本量更重要。"
    elif family in {"multilevel", "complex-survey"}:
        clusters = design.get("cluster_count")
        warning = clusters is None or int(clusters) < 50
        return {
            "是否提示风险": warning,
            "当前样本量": int(sample_size),
            "规划参考": "优先检查高层单位数；可先以至少 50 个高层单位作为规划参考，再按 ICC、效应量和模型复杂度做模拟功效分析。",
            "说明": "总样本量不能替代高层单位数。该数字是规划提醒，不是拒绝运行的硬门槛。",
            "依据": SOURCES["multilevel"],
        }
    else:
        reference, complex_reference, source = 200, 300, SOURCES["sem"]
        context = "自由参数、效应大小、指标可靠性、分布、缺失和估计量都会改变所需样本量。"

    warning = int(sample_size) < reference
    return {
        "是否提示风险": warning,
        "当前样本量": int(sample_size),
        "规划参考": f"可先以 {reference} 作为基础模型的初步规划参考；复杂、分类、纵向或小类别情形可从 {complex_reference} 或更高样本量开始规划。",
        "说明": f"{context} 最可靠的做法是依据预期效应和完整模型进行 Monte Carlo/模拟功效分析；这里的数字不是硬门槛。",
        "依据": source,
    }


def advisory_markdown(advisory: dict[str, Any]) -> list[str]:
    level = "样本量风险提醒" if advisory["是否提示风险"] else "样本量规划说明"
    return [
        f"### {level}",
        "",
        f"- 当前样本量：{advisory['当前样本量']}",
        f"- {advisory['规划参考']}",
        f"- {advisory['说明']}",
        f"- 参考依据：{advisory['依据']}",
    ]
