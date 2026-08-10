from __future__ import annotations

from dataclasses import asdict, dataclass

from .mplus_detect import _version_tuple


@dataclass(frozen=True)
class AnalysisFamily:
    id: str
    name_zh: str
    mode: str
    source: str
    description: str
    product_scope: str


# mode values are deliberately user-facing. "standard" means the Runtime can
# compile the model from a structured specification; it does not certify that
# every possible model in the family is substantively appropriate.
FAMILIES = (
    AnalysisFamily("lpa", "潜在剖面分析", "standard", "Mplus UG 7.9", "连续指标的 1-K 类 LPA", "core"),
    AnalysisFamily("lca", "潜在类别分析", "standard", "Mplus UG 7.3", "二分类或有序分类指标 LCA", "core"),
    AnalysisFamily("efa", "探索性因子分析", "standard", "Mplus UG 4.1", "连续或分类指标 EFA", "core"),
    AnalysisFamily("esem", "探索性结构方程模型", "guided", "Mplus UG 5.24-5.30", "ESEM 与 bifactor ESEM", "core"),
    AnalysisFamily("cfa", "验证性因子分析", "standard", "Mplus UG 5.1-5.4", "连续、分类或混合指标 CFA", "core"),
    AnalysisFamily("multigroup-cfa", "测量不变性", "guided", "Mplus UG 5.14-5.17", "多组或纵向的配置、载荷、截距/阈值约束比较", "core"),
    AnalysisFamily("sem", "结构方程模型", "standard", "Mplus UG 5.11-5.13", "测量模型与结构路径", "core"),
    AnalysisFamily("mediation", "中介效应", "standard", "Mplus UG 3.16 / 5.12", "观测或潜变量间接效应", "core"),
    AnalysisFamily("growth", "潜在增长模型", "standard", "Mplus UG Chapter 6", "当前标准模式为基础线性增长；扩展形态需引导", "core"),
    AnalysisFamily("gmm", "增长混合模型", "guided", "Mplus UG Chapter 8", "GMM、LCGA 及纵向混合模型", "core"),
    AnalysisFamily("lta", "潜在转变分析", "guided", "Mplus UG 8.12-8.15", "LTA、隐马尔可夫和 mover-stayer", "core"),
    AnalysisFamily("ri-clpm", "随机截距交叉滞后模型", "guided", "Mplus RI-CLPM topic", "CLPM、RI-CLPM 与纵向 SEM", "core"),
    AnalysisFamily("multilevel", "多层 CFA/SEM 与中介", "guided", "Mplus UG Chapter 9", "两层或三层潜变量模型", "core"),
    AnalysisFamily("complex-survey", "复杂抽样分析", "guided", "Mplus UG Chapter 9", "权重、分层、聚类和重复权重", "core"),
    AnalysisFamily("bayes", "贝叶斯潜变量模型", "expert", "Mplus UG Chapters 5/11", "BSEM、先验、后验诊断与可信区间", "expert-extension"),
    AnalysisFamily("dsem", "动态结构方程模型", "expert", "Mplus DSEM topic", "密集纵向、时间序列与多层 DSEM", "expert-extension"),
    AnalysisFamily("path", "路径分析与回归", "standard", "Mplus UG Chapter 3", "作为 SEM 与中介的基础能力保留", "supporting"),
    AnalysisFamily("irt", "项目反应理论", "guided", "Mplus UG 5.5", "可用 Mplus，但不是本产品首要普通用户入口", "optional-extension"),
    AnalysisFamily("survival", "生存分析", "guided", "Mplus UG Chapter 6", "仅在潜变量或离散时间设计确有需要时使用", "optional-extension"),
    AnalysisFamily("missing-data", "缺失数据与多重插补", "expert", "Mplus UG Chapter 11", "作为建模辅助或专家模型，不作为默认分析家族", "optional-extension"),
    AnalysisFamily("monte-carlo", "Monte Carlo 模拟", "expert", "Mplus UG Chapter 12", "用于功效和方法模拟，不作为普通数据分析入口", "optional-extension"),
)


def _version_adaptation(family_id: str, version: str | None) -> str:
    parsed = _version_tuple(version)
    if parsed is None:
        return "未检测 Mplus 版本；核心标准模板可先生成，自检后再运行。"
    major, minor = parsed
    if major <= 6:
        return "Mplus 6.x 及更早版本：不进入标准自动运行。"
    if family_id in {"lpa", "lca", "efa", "cfa", "sem", "mediation", "growth", "path"}:
        return "使用 7+ 通用语法与兼容输出解析。"
    if family_id == "esem" and (major, minor) >= (9, 1):
        return "9.1 可使用新版并列 ESEM 输出；当前仍为引导模块。"
    if family_id == "multigroup-cfa" and (major, minor) >= (8, 9):
        return "8.9+ 可使用自动纵向不变性测试；当前仍为引导模块。"
    if family_id == "ri-clpm" and (major, minor) >= (8, 7):
        return "8.7+ 支持残差回归式 RI-CLPM；当前仍为引导模块。"
    if family_id == "dsem" and (major, minor) >= (9, 0):
        return "9.0+ 有三层 DSEM 扩展；当前为专家模块。"
    if family_id in {"gmm", "lta", "multilevel", "complex-survey"}:
        return "可生成版本匹配的引导方案，但不自动补全高级版本专属语法。"
    return "按版本与官方专题资料进入引导或专家模式。"


def catalog(include_extensions: bool = False, mplus_version: str | None = None) -> list[dict[str, str]]:
    items = FAMILIES if include_extensions else tuple(item for item in FAMILIES if item.product_scope == "core")
    result: list[dict[str, str]] = []
    for item in items:
        row = asdict(item)
        row["版本适配"] = _version_adaptation(item.id, mplus_version)
        result.append(row)
    return result


def get_family(family_id: str) -> AnalysisFamily:
    for item in FAMILIES:
        if item.id == family_id:
            return item
    raise ValueError(f"未知分析类型：{family_id}")
