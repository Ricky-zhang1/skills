from __future__ import annotations

from typing import Any


SOURCES = {
    "factor": "MacCallum et al. (1999), https://doi.org/10.1037/1082-989X.4.1.84",
    "sem": "Wolf et al. (2013), https://doi.org/10.1177/0013164413495237",
    "mixture": "Tein et al. (2013), https://doi.org/10.1080/10705511.2013.824781",
    "multilevel": "Maas & Hox (2005), https://doi.org/10.1027/1614-2241.1.3.86",
    "monte_carlo": (
        "Mplus User's Guide Chapter 12 examples, https://www.statmodel.com/usersguide/chapter12.shtml; "
        "MONTECARLO command, https://www.statmodel.com/HTML_UG/chapter19V8.htm"
    ),
}


def sample_size_advisory(analysis: str, sample_size: int, design: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a non-blocking planning warning, never a model-validity verdict."""
    family = analysis.lower()
    design = design or {}
    if family in {"efa", "esem"}:
        screening, source = 200, SOURCES["factor"]
        context = "共同度、载荷强度、因子数和每个因子的指标数都会改变所需样本量。"
        evidence_limit = "MacCallum 等不支持一个适用所有 EFA 的固定最小 N。"
    elif family in {"lpa", "lca", "gmm", "lta"}:
        screening, source = 300, SOURCES["mixture"]
        context = "类别分离度、最小类别占比、指标质量和类别数通常比单一总样本量更重要。"
        evidence_limit = "Tein 等的模拟不支持通用的 N=300 或 N=500 最小值。"
    elif family in {"multilevel", "complex-survey"}:
        clusters = design.get("cluster_count")
        warning = clusters is None or int(clusters) < 50
        return {
            "是否提示风险": warning,
            "当前样本量": int(sample_size),
            "内部筛查线": "高层单位数 < 50 时触发提醒。",
            "规划参考": (
                "优先报告高层单位数，再按 ICC、效应量、随机效应和模型复杂度做模拟功效分析。"
                "Maas & Hox (2005) 在其两层线性模型模拟条件下发现，50 或更少高层单位会使二层标准误出现偏差。"
            ),
            "说明": "总样本量不能替代高层单位数；50 是特定模拟结果和内部筛查线，不是所有多层模型的通用最小值，也不阻止用户继续运行。",
            "依据": f"{SOURCES['multilevel']}; {SOURCES['monte_carlo']}",
        }
    else:
        screening, source = 200, SOURCES["sem"]
        context = "自由参数、效应大小、指标可靠性、分布、缺失和估计量都会改变所需样本量。"
        evidence_limit = "Wolf 等展示了 SEM 所需样本对模型与数据条件的强依赖，不支持通用 N=200/300 规则。"

    warning = int(sample_size) < screening
    return {
        "是否提示风险": warning,
        "当前样本量": int(sample_size),
        "内部筛查线": f"N < {screening} 时触发样本量风险提醒。",
        "规划参考": (
            f"当前文献没有支持适用该分析家族所有模型的固定最小 N。"
            "正式建议值应来自预实验/可靠文献中的预期参数，并在完整目标模型上做 Monte Carlo 或其他模拟功效分析。"
        ),
        "说明": f"{context} {evidence_limit} N={screening} 只是本 Skill 用来防止遗漏提醒的内部筛查线，不是硬门槛，不阻止运行。",
        "依据": f"{source}; {SOURCES['monte_carlo']}",
    }


def advisory_markdown(advisory: dict[str, Any]) -> list[str]:
    level = "样本量风险提醒" if advisory["是否提示风险"] else "样本量规划说明"
    return [
        f"### {level}",
        "",
        f"- 当前样本量：{advisory['当前样本量']}",
        f"- 内部筛查线：{advisory['内部筛查线']}",
        f"- {advisory['规划参考']}",
        f"- {advisory['说明']}",
        f"- 参考依据：{advisory['依据']}",
    ]
