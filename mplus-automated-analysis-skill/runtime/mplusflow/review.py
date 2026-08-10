from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .parser import MplusResult


CRITICAL_WARNING_FRAGMENTS = {
    "STANDARD ERRORS OF THE MODEL PARAMETER ESTIMATES COULD NOT BE COMPUTED": "参数标准误无法计算",
    "STANDARD ERRORS OF THE MODEL PARAMETER ESTIMATES MAY NOT BE TRUSTWORTHY": "参数标准误可能不可信",
    "NON-POSITIVE DEFINITE FIRST-ORDER DERIVATIVE PRODUCT MATRIX": "一阶导数乘积矩阵非正定",
    "FISHER INFORMATION MATRIX COULD NOT BE INVERTED": "Fisher 信息矩阵不可逆",
    "THE MODEL ESTIMATION DID NOT TERMINATE NORMALLY": "模型未正常结束",
    "MODEL MAY NOT BE IDENTIFIED": "模型可能不可识别",
    "MODEL NONIDENTIFICATION": "模型可能不可识别",
    "LATENT VARIABLE COVARIANCE MATRIX (PSI) IS NOT POSITIVE DEFINITE": "潜变量协方差矩阵非正定",
    "RESIDUAL COVARIANCE MATRIX (THETA) IS NOT POSITIVE DEFINITE": "残差协方差矩阵非正定",
    "THE MLE MAY NOT BE TRUSTWORTHY": "最大似然估计可能不可信",
    "LOGLIKELIHOOD DECREASED IN THE LAST EM ITERATION": "最后一次 EM 迭代的 loglikelihood 下降",
    "COMPUTATIONAL PROBLEMS ESTIMATING THE CORRELATION": "分类变量相关矩阵估计失败",
    "NO CONVERGENCE.  NUMBER OF ITERATIONS EXCEEDED": "迭代次数耗尽仍未收敛",
}


def critical_warning_labels(result: MplusResult) -> list[str]:
    warning_text = " ".join(result.warnings).upper()
    return sorted({label for fragment, label in CRITICAL_WARNING_FRAGMENTS.items() if fragment in warning_text})


@dataclass
class ReviewIssue:
    level: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def programmatic_review(spec: dict[str, Any], results: list[MplusResult]) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    expected_vars = [x.upper() for x in spec.get("内部剖面指标", [])]
    expected_n = int(spec.get("预计Mplus有效样本数", spec.get("原始样本数", 0)))

    expected_classes = {int(x) for x in spec.get("类别范围", [])}
    seen_k: set[int] = set()
    for r in results:
        k = r.class_count
        if k is None:
            issues.append(ReviewIssue("重大", "CLASSCOUNT-UNVERIFIED", "无法从 Mplus 输出确认实际类别数，禁止把该结果视为已验证模型。"))
        if k is not None:
            if k in seen_k:
                issues.append(ReviewIssue("重大", "MODEL-DUP-K", f"出现重复的 {k} 类最终结果，可能复制了错误输出。"))
            seen_k.add(k)

        if r.errors:
            issues.append(ReviewIssue("重大", "MPLUS-ERROR", f"{k or '?'} 类模型存在 Mplus ERROR：{r.errors[0]}"))
        if not r.normal_termination:
            issues.append(ReviewIssue("重大", "MPLUS-NOT-NORMAL", f"{k or '?'} 类模型未正常结束。"))
        for label in critical_warning_labels(r):
            issues.append(ReviewIssue("重大", "MPLUS-CRITICAL-WARNING", f"{k or '?'} 类模型出现严重警告：{label}。"))
        if k and k >= 2 and r.best_ll_replicated is False:
            issues.append(ReviewIssue("重大", "LPA-LL-NOT-REPLICATED", f"{k} 类模型最佳 loglikelihood 未重复。"))
        if r.estimator is None:
            issues.append(ReviewIssue("重大", "ESTIMATOR-UNVERIFIED", f"{k or '?'} 类模型无法从输出确认估计量。"))
        elif r.estimator.upper() != "MLR":
            issues.append(ReviewIssue("重大", "ESTIMATOR-MISMATCH", f"{k or '?'} 类模型实际估计量为 {r.estimator}，标准模板要求 MLR。"))
        if r.sample_size is None:
            issues.append(ReviewIssue("重大", "N-UNVERIFIED", f"{k or '?'} 类模型无法从输出确认实际样本数。"))
        elif expected_n and r.sample_size != expected_n:
            issues.append(ReviewIssue("重大", "N-MISMATCH", f"{k or '?'} 类模型 Mplus 样本数为 {r.sample_size}，预计为 {expected_n}。"))
        actual_vars = [v.upper() for v in r.continuous_variables if v.upper().startswith("V")]
        if not actual_vars:
            issues.append(ReviewIssue("重大", "VARIABLES-UNVERIFIED", f"{k or '?'} 类模型无法从输出确认实际连续剖面指标。"))
        elif actual_vars != expected_vars:
            issues.append(ReviewIssue("重大", "VARIABLE-MISMATCH", f"{k or '?'} 类模型实际连续指标 {actual_vars} 与设计 {expected_vars} 不一致。"))
        if any("OPTSEED" in w.upper() and "LOGLIKELIHOOD" in w.upper() and "不一致" in w for w in r.warnings):
            issues.append(ReviewIssue("重大", "OPTSEED-LL-MISMATCH", f"{k or '?'} 类模型比较阶段与已验证稳定解的 loglikelihood 不一致。"))
        if r.tech14_p is not None and r.tech14_trustworthy is False:
            issues.append(ReviewIssue("警告", "TECH14-UNTRUSTWORTHY", f"{k or '?'} 类模型 TECH14 存在可信度问题，禁止作为有效类别数证据。"))
        if k and k >= 2:
            if r.tech11_p is None and r.tech14_p is None:
                issues.append(ReviewIssue("警告", "LRT-EVIDENCE-MISSING", f"{k} 类模型未获得 TECH11/TECH14，类别数比较证据不完整。"))
            if len(r.class_proportions) != k or abs(sum(r.class_proportions) - 1.0) > 0.02:
                issues.append(ReviewIssue("重大", "CLASS-PROPORTIONS-UNVERIFIED", f"{k} 类模型无法可靠核对各类别比例。"))
            if len(r.posterior_diag) != k:
                issues.append(ReviewIssue("重大", "POSTERIOR-UNVERIFIED", f"{k} 类模型无法可靠核对平均后验分类概率。"))
            if r.savedata_filename and not r.savedata_variables:
                issues.append(ReviewIssue("重大", "SAVEDATA-ORDER-UNVERIFIED", f"{k} 类模型无法从输出确认 SAVEDATA 变量顺序。"))
        if r.class_proportions:
            smallest = min(r.class_proportions)
            if smallest < 0.01:
                issues.append(ReviewIssue("严重提示", "TINY-CLASS-1", f"{k or '?'} 类模型最小类别占比 {smallest:.2%}，低于1%。"))
            elif smallest < 0.05:
                issues.append(ReviewIssue("提示", "SMALL-CLASS-5", f"{k or '?'} 类模型最小类别占比 {smallest:.2%}，低于5%；这是启发式提示，不自动否决模型。"))
    if expected_classes:
        missing_classes = sorted(expected_classes - seen_k)
        unexpected_classes = sorted(seen_k - expected_classes)
        if missing_classes:
            issues.append(ReviewIssue("重大", "MODEL-MISSING-K", f"缺少用户要求的类别模型：{missing_classes}。"))
        if unexpected_classes:
            issues.append(ReviewIssue("重大", "MODEL-UNEXPECTED-K", f"出现分析设计之外的类别模型：{unexpected_classes}。"))
    return issues
