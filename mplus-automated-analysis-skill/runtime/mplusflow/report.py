from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .parser import MplusResult
from .review import ReviewIssue, critical_warning_labels
from .utils import ensure_dir, write_json
from .sample_size import advisory_markdown


def comparison_dataframe(results: list[MplusResult]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for r in sorted(results, key=lambda x: x.class_count or 999):
        rows.append({
            "类别数": r.class_count,
            "正常结束": "是" if r.normal_termination else "否",
            "最佳LL重复": "是" if r.best_ll_replicated else ("否" if r.best_ll_replicated is False else "未知"),
            "Loglikelihood": r.loglikelihood,
            "AIC": r.aic,
            "BIC": r.bic,
            "样本量调整BIC": r.sabic,
            "Entropy": r.entropy,
            "TECH11_p": r.tech11_p,
            "TECH11调整后_p": r.tech11_adjusted_p,
            "TECH14_p": r.tech14_p,
            "TECH14可用": "是" if r.tech14_trustworthy else ("否" if r.tech14_trustworthy is False else "未运行"),
            "最小类别占比": min(r.class_proportions) if r.class_proportions else None,
            "平均后验概率最小值": min(r.posterior_diag) if r.posterior_diag else None,
        })
    return pd.DataFrame(rows)


def choose_statistical_candidate(results: list[MplusResult], max_requested_k: int, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成统计候选，不把任何单一指标当成最终类别数裁决。"""
    expected_n = int((spec or {}).get("预计Mplus有效样本数", 0) or 0)
    expected_vars = [str(x).upper() for x in (spec or {}).get("内部剖面指标", [])]

    def hard_valid(r: MplusResult) -> bool:
        if not r.normal_termination or r.errors or r.bic is None or r.class_count is None:
            return False
        if r.class_count >= 2 and r.best_ll_replicated is not True:
            return False
        if r.estimator is None or r.estimator.upper() != "MLR":
            return False
        if r.sample_size is None or (expected_n and r.sample_size != expected_n):
            return False
        actual = [v.upper() for v in r.continuous_variables if v.upper().startswith("V")]
        if not actual or (expected_vars and actual != expected_vars):
            return False
        if any("OPTSEED" in w.upper() and "LOGLIKELIHOOD" in w.upper() and "不一致" in w for w in r.warnings):
            return False
        if critical_warning_labels(r):
            return False
        return True

    valid = [r for r in results if hard_valid(r)]
    if not valid:
        return {
            "状态": "无可用模型",
            "候选类别数": None,
            "说明": ["没有候选模型同时通过正常结束、输出反校验和混合模型稳定性门槛。"],
            "冲突": [],
        }

    valid_by_k = {int(r.class_count): r for r in valid if r.class_count is not None}
    candidate = min(valid, key=lambda r: r.bic if r.bic is not None else float("inf"))
    k = int(candidate.class_count or 1)
    reasons = [f"在通过硬门槛的模型中，{k} 类模型 BIC 最低。"]
    conflicts: list[str] = []

    if k == max_requested_k:
        conflicts.append("BIC最低点位于当前搜索范围上界，类别范围可能尚未覆盖信息准则拐点。")

    # 当前 K 的 LRT 检验 K 是否优于 K-1。
    if k >= 2:
        if candidate.tech11_p is None and candidate.tech14_p is None:
            conflicts.append(f"{k} 类模型未获得 TECH11/TECH14，类别数比较证据不完整。")
        if candidate.tech11_p is not None:
            reasons.append(f"{k} 类模型 TECH11 p={candidate.tech11_p:.4g}，用于比较 {k-1} 类与 {k} 类。")
            if candidate.tech11_p >= 0.05:
                conflicts.append(f"TECH11 未支持从 {k-1} 类增加到 {k} 类。")
        if candidate.tech14_p is not None:
            if candidate.tech14_trustworthy:
                reasons.append(f"{k} 类模型 TECH14 p={candidate.tech14_p:.4g}，且未检测到本 Skill 识别的可信度警告。")
                if candidate.tech14_p >= 0.05:
                    conflicts.append(f"TECH14 未支持从 {k-1} 类增加到 {k} 类。")
            else:
                conflicts.append(f"{k} 类模型 TECH14 存在局部最优或 bootstrap 相关警告，本次不作为有效证据。")

    # 邻接的 K+1 模型同样重要：若它的可靠 LRT 明确支持继续增加类别，说明 BIC 候选与 LRT 冲突。
    next_model = valid_by_k.get(k + 1)
    if next_model is not None:
        if next_model.tech11_p is None and next_model.tech14_p is None:
            conflicts.append(f"{k+1} 类模型未获得 TECH11/TECH14，无法充分判断是否应继续增加类别。")
        if next_model.tech14_p is not None and next_model.tech14_trustworthy:
            if next_model.tech14_p < 0.05:
                conflicts.append(f"{k+1} 类模型 TECH14 仍支持相对 {k} 类增加类别。")
            else:
                reasons.append(f"{k+1} 类模型 TECH14 p={next_model.tech14_p:.4g}，未支持继续增加到 {k+1} 类。")
        elif next_model.tech14_p is not None and next_model.tech14_trustworthy is False:
            conflicts.append(f"{k+1} 类模型 TECH14 不可靠，无法用其判断是否应继续增加类别。")
        if next_model.tech11_p is not None:
            if next_model.tech11_p < 0.05:
                conflicts.append(f"{k+1} 类模型 TECH11 仍支持相对 {k} 类增加类别。")
            else:
                reasons.append(f"{k+1} 类模型 TECH11 p={next_model.tech11_p:.4g}，未支持继续增加到 {k+1} 类。")

    if candidate.class_proportions and min(candidate.class_proportions) < 0.01:
        conflicts.append("候选模型存在低于1%的极小类别，需要谨慎判断其稳定性与实质意义。")

    # Entropy只描述分类清晰度；作为描述性信息，不赋予机械阈值。
    if candidate.entropy is not None:
        reasons.append(f"候选模型 Entropy={candidate.entropy:.3f}；该指标仅作为分类清晰度信息，不单独决定类别数。")

    status = "统计证据较集中" if not conflicts else "证据不一致"
    return {"状态": status, "候选类别数": k, "说明": reasons, "冲突": conflicts}


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _comparison_table(results: list[MplusResult]) -> list[str]:
    lines = [
        "| 类别数 | 正常结束 | 最佳LL重复 | AIC | BIC | SABIC | Entropy | TECH11 p | TECH14 p | 最小类别占比 |",
        "|---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(results, key=lambda x: x.class_count or 999):
        smallest = min(r.class_proportions) if r.class_proportions else None
        lines.append(
            f"| {_fmt(r.class_count, 0)} | {'是' if r.normal_termination else '否'} | "
            f"{'是' if r.best_ll_replicated else ('否' if r.best_ll_replicated is False else '未知')} | "
            f"{_fmt(r.aic)} | {_fmt(r.bic)} | {_fmt(r.sabic)} | {_fmt(r.entropy)} | "
            f"{_fmt(r.tech11_p, 4)} | {_fmt(r.tech14_p, 4)} | "
            f"{_fmt(smallest * 100 if smallest is not None else None, 1)}% |"
        )
    return lines


def _profile_section(spec: dict[str, Any], selected: MplusResult | None) -> list[str]:
    if selected is None or not selected.class_means or not selected.class_count:
        return ["当前没有通过质量门槛的统计候选，因此不生成类别剖面解释。"]
    original = list(spec.get("剖面指标", []))
    internal = [str(x).upper() for x in spec.get("内部剖面指标", [])]
    names = dict(zip(internal, original))
    class_ids = sorted(selected.class_means, key=lambda x: int(x))
    indicators = [x for x in internal if all(x in selected.class_means[c] for c in class_ids)]
    lines = [
        "下表为候选模型的类别特异估计均值。类别编号是 Mplus 的计算标签，不代表价值高低；理论命名应结合量表含义完成。",
        "",
        "| 类别 | 样本数 | 占比 | 最低平均后验概率 | " + " | ".join(names.get(v, v) for v in indicators) + " |",
        "|---:|---:|---:|---:|" + "---:|" * len(indicators),
    ]
    for index, class_id in enumerate(class_ids):
        count = selected.class_counts[index] if index < len(selected.class_counts) else None
        prop = selected.class_proportions[index] if index < len(selected.class_proportions) else None
        posterior = selected.posterior_diag[index] if index < len(selected.posterior_diag) else None
        means = " | ".join(_fmt(selected.class_means[class_id].get(v)) for v in indicators)
        lines.append(
            f"| {class_id} | {_fmt(count, 0)} | {_fmt(prop * 100 if prop is not None else None, 1)}% | "
            f"{_fmt(posterior)} | {means} |"
        )

    rank_labels: dict[tuple[str, str], str] = {}
    k = len(class_ids)
    for var in indicators:
        ranked = sorted((selected.class_means[c][var], c) for c in class_ids)
        for rank, (_, class_id) in enumerate(ranked, start=1):
            if k == 2:
                label = "相对较低" if rank == 1 else "相对较高"
            elif k == 3:
                label = ["相对较低", "居中", "相对较高"][rank - 1]
            else:
                label = f"由低到高位列第 {rank}"
            rank_labels[(class_id, var)] = label
    lines += ["", "按每个指标在类别间的相对位置，可作如下描述："]
    for class_id in class_ids:
        descriptions = [f"{names.get(v, v)}{rank_labels[(class_id, v)]}" for v in indicators]
        lines.append(f"- 类别 {class_id}：" + "；".join(descriptions) + "。")
    return lines


def write_code_explanation(project: Path, spec: dict[str, Any], candidate: dict[str, Any]) -> Path:
    p = ensure_dir(project / "03_Mplus代码") / "代码逐段说明.md"
    lines = [
        "# Mplus LPA 代码逐段说明",
        "",
        "本说明用于帮助你看懂代码逻辑。真正提交给 Mplus 的 `.inp` 文件使用英文和 ASCII 路径，以降低不同版本与系统的兼容风险。",
        "",
        "## DATA",
        "",
        "`FILE = data.dat;` 指向 Skill 自动生成的 Mplus 分析数据。该文件无表头，变量顺序由 `NAMES ARE` 决定。",
        "",
        "## VARIABLE",
        "",
        "`NAMES ARE` 声明数据文件每一列的顺序；`USEVARIABLES ARE` 只选择本次 LPA 的剖面指标；`IDVARIABLE = ROWID` 用于把分类结果准确合并回原始个案；`MISSING` 声明内部缺失码；`CLASSES = C(K)` 指定当前候选类别数。",
        "",
        "## ANALYSIS",
        "",
        "`TYPE = MIXTURE` 指定有限混合模型。标准 LPA 模板显式使用 `ESTIMATOR = MLR`。`STARTS` 控制随机起始值搜索，目的是降低停留在局部最优解的风险；本 Skill 不把固定的 STARTS 数字当作通用标准，而会检查最佳 loglikelihood 是否重复，并在必要时增加起始值。",
        "",
        "## MODEL 为什么没有自由添加语句",
        "",
        "基础模板沿用 Mplus 官方 Example 7.9 的默认参数化：类别均值允许不同、各指标方差跨类别相等、类内指标协方差为0。标准模式禁止 Agent 临时增加 `WITH` 或类别特异方差；如果需要这些设定，应切换到另一个经过认证的模板。",
        "",
        "## OUTPUT",
        "",
        "`TECH1` 用于检查实际参数化；`TECH8` 用于查看优化过程与随机起始值；稳定模型进一步使用 `TECH11` / `TECH14` 比较 K 类与 K-1 类。TECH14 若出现局部最优或 bootstrap 不收敛警告，其 p 值不会被当成有效证据。",
        "",
        "## SAVEDATA",
        "",
        "`SAVE = CPROBABILITIES` 保存每个个案的类别后验概率和最可能类别归属。Skill 会根据 `.out` 中的 `SAVEDATA INFORMATION` 读取实际变量顺序，再转换为 Excel，不会自行猜列名。",
        "",
        "## 本次分析设计",
        "",
        f"- 剖面指标：{', '.join(spec.get('剖面指标', []))}",
        f"- 类别范围：{spec.get('类别范围')}",
        f"- 指标Z标准化：{'是' if spec.get('变量标准化') else '否'}",
        f"- 当前统计候选：{candidate.get('候选类别数')} 类（{candidate.get('状态')}）",
        "",
        "## 权威来源",
        "",
        "- [Mplus User's Guide Example 7.9](https://www.statmodel.com/usersguide/chap7/ex7.9.html)（基础连续指标 mixture/LPA 结构）",
        "- [Asparouhov & Muthén (2012), Mplus Web Note 14](https://www.statmodel.com/examples/webnotes/webnote14.pdf)（TECH11/TECH14）",
        "- [Nylund, Asparouhov & Muthén (2007)](https://doi.org/10.1080/10705510701575396)（类别数选择模拟研究）",
        "- [Masyn (2013)](https://doi.org/10.1093/oxfordhb/9780199934898.013.0025)（LCA/LPA应用规范综述）",
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_reference_files(project: Path) -> None:
    ref_dir = ensure_dir(project / "07_参考依据")
    (ref_dir / "代码模板来源.md").write_text(
        "# 代码模板来源\n\n本次标准 LPA 基础模板以 Mplus User's Guide Chapter 7 Example 7.9 为一级来源，并由本 Skill 增加随机起始值稳定性检查、TECH11/TECH14 和 SAVEDATA 工作流。\n\n官方示例：https://www.statmodel.com/usersguide/chap7/ex7.9.html\n",
        encoding="utf-8",
    )
    (ref_dir / "模型判断依据.md").write_text(
        "# 模型判断依据\n\n类别数不由单一指标机械决定。先要求模型正常结束并确认最佳 loglikelihood 稳定，再综合 BIC/SABIC/AIC、TECH11、TECH14、Entropy、后验分类概率、类别规模与实质解释。Entropy 主要说明分类清晰程度，不单独决定类别数。TECH14 若出现局部最优或 bootstrap 不收敛警告，则不作为有效证据。\n",
        encoding="utf-8",
    )
    (ref_dir / "参考文献.md").write_text(
        "# 参考文献\n\n"
        "1. Muthén & Muthén. Mplus User's Guide Examples, Chapter 7, Example 7.9. https://www.statmodel.com/usersguide/chap7/ex7.9.html\n"
        "2. Asparouhov, T., & Muthén, B. (2012). Using Mplus TECH11 and TECH14 to test the number of latent classes. Mplus Web Notes No. 14. https://www.statmodel.com/examples/webnotes/webnote14.pdf\n"
        "3. Nylund, K. L., Asparouhov, T., & Muthén, B. O. (2007). Deciding on the Number of Classes in Latent Class Analysis and Growth Mixture Modeling. Structural Equation Modeling, 14(4), 535–569. https://doi.org/10.1080/10705510701575396\n"
        "4. Masyn, K. E. (2013). Latent Class Analysis and Finite Mixture Modeling. In The Oxford Handbook of Quantitative Methods in Psychology. https://doi.org/10.1093/oxfordhb/9780199934898.013.0025\n",
        encoding="utf-8",
    )


def write_analysis_report(project: Path, spec: dict[str, Any], results: list[MplusResult], candidate: dict[str, Any], issues: list[ReviewIssue]) -> Path:
    p = ensure_dir(project / "06_分析报告") / "LPA分析报告.md"
    valid_issues = [i for i in issues if i.level == "重大"]
    lines = [
        "# 潜在剖面分析（LPA）报告",
        "",
        "## 一、分析设计",
        "",
        f"本次使用 {len(spec.get('剖面指标', []))} 个连续指标进行潜在剖面分析，比较 {min(spec.get('类别范围', [1]))}—{max(spec.get('类别范围', [5]))} 类候选模型。基础参数化采用等方差、类内指标协方差为0的标准 LPA 起点。",
        "",
        f"剖面指标：{ '、'.join(spec.get('剖面指标', [])) }。",
        f"指标是否做 Z 标准化：{'是' if spec.get('变量标准化') else '否'}。",
        f"原始样本数：{spec.get('原始样本数')}；预计 Mplus 有效样本数：{spec.get('预计Mplus有效样本数')}。",
        f"运行环境验证：{spec.get('环境验证状态', '未记录')}。",
        f"Mplus 版本：{spec.get('Mplus版本', '未记录')}；版本适配配置：{spec.get('版本适配配置', '未记录')}。",
        "",
        *advisory_markdown(spec.get("样本量提示", {
            "是否提示风险": False, "当前样本量": spec.get("预计Mplus有效样本数"),
            "规划参考": "未生成规划参考。", "说明": "请结合完整模型做功效分析。", "依据": "未记录",
        })),
        "",
        "## 二、模型稳定性与比较",
        "",
        "模型比较的第一步是排除没有正常收敛或最佳 loglikelihood 未稳定重复的模型，再综合信息准则和类别比较检验。可编辑数值见《类别模型比较表.xlsx》。",
        "",
    ]
    lines.extend(_comparison_table(results))
    selected_k = candidate.get("候选类别数")
    selected = next((r for r in results if r.class_count == selected_k), None)
    candidate_text = f"{selected_k} 类" if selected_k is not None else "未形成可用候选"
    lines += [
        "",
        f"当前统计候选为 **{candidate_text}**，状态：**{candidate.get('状态')}**。",
        "",
    ]
    if spec.get("环境验证状态") == "试运行（未完成本机自检）":
        lines += [
            "",
            "## 试运行说明",
            "",
            "本次是在用户明确同意下完成的未自检试运行。Mplus 原始输出和数据转换结果已保留，可用于核对代码与数据；正式研究结论前请完成本机自检并复跑。",
        ]
    for reason in candidate.get("说明", []):
        lines.append(f"- {reason}")
    for conflict in candidate.get("冲突", []):
        lines.append(f"- 需要注意：{conflict}")
    lines += [
        "",
        "## 三、类别剖面与分类质量",
        "",
    ]
    lines.extend(_profile_section(spec, selected))
    lines += [
        "",
        "## 四、如何理解这个推荐",
        "",
        "这里的“统计候选”不是把某个指标当成唯一标准。最终类别数还要结合各类别的指标均值形态、类别规模和研究问题判断。如果不同证据相互冲突，本报告会明确写出冲突，不强行制造一个确定结论。",
        "",
        "## 五、质量状态与限制",
        "",
    ]
    if valid_issues:
        lines.append("本次程序化审查发现重大问题，当前结果暂不建议直接用于论文。请查看 `08_质量审查/质量审查发现的问题.md`。")
    else:
        lines.append("程序化硬检查未发现会直接使结果失效的重大错误。独立 Agent 审查仍应按 Skill 规范执行。")
    lines += [
        "",
        "",
        f"运行环境：Mplus {spec.get('Mplus版本') or '版本未识别'}；兼容状态：{spec.get('Mplus版本兼容状态') or '未记录'}；输出解析配置：{selected.output_profile if selected else '未记录'}。",
        "本报告只覆盖连续指标、等方差、类内零协方差的基础 LPA。它不替代对量表测量质量、样本代表性、缺失机制和理论可解释性的研究判断。",
        "",
        "## 六、方法依据",
        "",
        "代码模板与模型判断来源见 `07_参考依据`。核心依据为 Mplus 官方 Example 7.9 [1]、Mplus Web Note 14 [2]、Nylund et al. (2007) [3] 与 Masyn (2013) [4]。",
        "",
        "1. Mplus User's Guide Example 7.9. https://www.statmodel.com/usersguide/chap7/ex7.9.html",
        "2. Asparouhov, T., & Muthén, B. (2012). Mplus Web Note 14. https://www.statmodel.com/examples/webnotes/webnote14.pdf",
        "3. Nylund, K. L., Asparouhov, T., & Muthén, B. O. (2007). https://doi.org/10.1080/10705510701575396",
        "4. Masyn, K. E. (2013). https://doi.org/10.1093/oxfordhb/9780199934898.013.0025",
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_quality_report(project: Path, issues: list[ReviewIssue]) -> Path | None:
    major = [i for i in issues if i.level == "重大"]
    if not major:
        return None
    qdir = ensure_dir(project / "08_质量审查")
    p = qdir / "质量审查发现的问题.md"
    lines = ["# 质量审查发现的问题", "", "以下问题可能影响分析结论，当前结果暂不建议直接用于论文：", ""]
    for i in major:
        lines.append(f"- **{i.code}**：{i.message}")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_manifest(project: Path, spec: dict[str, Any], results: list[MplusResult], candidate: dict[str, Any], issues: list[ReviewIssue]) -> Path:
    data = {
        "分析类型": "LPA",
        "模板ID": spec.get("模板ID"),
        "运行类型": spec.get("运行类型"),
        "本机自检凭证": spec.get("本机自检凭证"),
        "模型结果": [r.to_dict() for r in results],
        "统计候选": candidate,
        "程序化审查": [i.to_dict() for i in issues],
        "审查状态": "FAIL" if any(i.level == "重大" for i in issues) else "程序检查通过_待独立Agent审查",
    }
    path = ensure_dir(project / ".mplus_runtime") / "manifest.json"
    write_json(path, data)
    return path
