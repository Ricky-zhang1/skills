from __future__ import annotations

import re
from typing import Any

from .catalog import get_family
from .utils import wrap_mplus_names


_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,7}$")
COMPILED_FAMILIES = {"efa", "cfa", "sem", "path", "mediation", "growth", "lca"}


def _names(items: list[str], mapping: dict[str, str]) -> list[str]:
    missing = [x for x in items if x not in mapping]
    if missing:
        raise ValueError(f"模型中使用了未登记变量：{missing}")
    return [mapping[x] for x in items]


def _latent_map(factors: dict[str, list[str]]) -> dict[str, str]:
    return {name: f"F{i:06d}" for i, name in enumerate(factors, 1)}


def _entity(name: str, observed: dict[str, str], latent: dict[str, str]) -> str:
    if name in observed:
        return observed[name]
    if name in latent:
        return latent[name]
    raise ValueError(f"结构模型引用了未登记的变量或因子：{name}")


def _factor_lines(factors: dict[str, list[str]], observed: dict[str, str], latent: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for label, indicators in factors.items():
        if len(indicators) < 2:
            raise ValueError(f"因子“{label}”少于 2 个指标，标准模式停止。")
        lines.append(f"  {latent[label]} BY {wrap_mplus_names(_names(indicators, observed), indent='    ', width=66)};")
    return lines


def _regression_lines(regressions: list[dict[str, Any]], observed: dict[str, str], latent: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for relation in regressions:
        outcome = _entity(str(relation["outcome"]), observed, latent)
        predictors = [_entity(str(x), observed, latent) for x in relation.get("predictors", [])]
        if not predictors:
            raise ValueError(f"回归结构中 {relation['outcome']} 没有预测变量。")
        lines.append(f"  {outcome} ON {wrap_mplus_names(predictors, indent='    ', width=66)};")
    return lines


def _indirect_lines(indirect: list[dict[str, str]], observed: dict[str, str], latent: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for relation in indirect:
        y = _entity(relation["outcome"], observed, latent)
        x = _entity(relation["predictor"], observed, latent)
        mediators = relation.get("mediators") or [relation.get("mediator")]
        if not mediators or any(x is None for x in mediators):
            raise ValueError("间接效应必须声明 mediator 或 mediators。")
        mids = [_entity(str(m), observed, latent) for m in mediators]
        # Mplus specific indirect syntax is outcome IND mediator(s) predictor.
        lines.append(f"  {y} IND {' '.join(mids)} {x};")
    return lines


def _covariance_lines(covariances: list[dict[str, str]], observed: dict[str, str], latent: dict[str, str]) -> list[str]:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for relation in covariances:
        left = _entity(str(relation.get("left", "")), observed, latent)
        right = _entity(str(relation.get("right", "")), observed, latent)
        if left == right:
            raise ValueError("协方差两端不能是同一个变量或因子。")
        pair = tuple(sorted((left, right)))
        if pair in seen:
            raise ValueError("covariances 中存在重复协方差。")
        seen.add(pair)
        lines.append(f"  {left} WITH {right};")
    return lines


def validate_spec(spec: dict[str, Any]) -> None:
    family = get_family(str(spec.get("analysis", "")))
    if family.id not in COMPILED_FAMILIES:
        if family.mode == "guided":
            raise ValueError(f"“{family.name_zh}”当前是引导模块，尚未接入结构化编译器；不会创建半成品项目。")
        raise ValueError(f"“{family.name_zh}”尚不允许通过结构化标准模式自动编译。")
    variables = spec.get("variables")
    if not isinstance(variables, list) or not variables or any(not isinstance(x, str) for x in variables):
        raise ValueError("分析设计必须提供非空 variables 字符串列表。")
    if len(set(variables)) != len(variables):
        raise ValueError("variables 中存在重复变量。")


def render_standard_input(spec: dict[str, Any], observed: dict[str, str], missing_code: float = -999999.0) -> str:
    validate_spec(spec)
    analysis = str(spec["analysis"])
    variables = list(spec["variables"])
    internal = _names(variables, observed)
    categorical = _names(list(spec.get("categorical", [])), observed)
    factors = spec.get("factors") or {}
    if not isinstance(factors, dict):
        raise ValueError("factors 必须是“因子名: [指标]”的对象。")
    latent = _latent_map(factors)

    variable_lines = [
        "  NAMES ARE " + wrap_mplus_names(["ROWID", *internal], indent="    ", width=66) + ";",
        "  USEVARIABLES ARE " + wrap_mplus_names(internal, indent="    ", width=59) + ";",
        "  IDVARIABLE = ROWID;",
    ]
    if spec.get("has_missing"):
        variable_lines.append(f"  MISSING = ALL({int(missing_code)});")
    if categorical:
        variable_lines.append("  CATEGORICAL ARE " + wrap_mplus_names(categorical, indent="    ", width=59) + ";")

    analysis_lines: list[str] = []
    model_lines: list[str] = []
    extra_lines: list[str] = []

    if analysis == "efa":
        lo = int(spec.get("min_factors", 1))
        hi = int(spec.get("max_factors", min(6, len(variables) // 2)))
        if lo < 1 or hi < lo or hi >= len(variables):
            raise ValueError("EFA 因子范围无效；必须满足 1 <= min <= max < 指标数。")
        analysis_lines.append(f"  TYPE = EFA {lo} {hi};")
        if categorical:
            analysis_lines.append("  ESTIMATOR = WLSMV;")
    elif analysis in {"cfa", "sem"} or (analysis == "mediation" and factors):
        if analysis in {"cfa", "sem"} and not factors:
            raise ValueError(f"{analysis.upper()} 必须声明 factors。")
        model_lines.extend(_factor_lines(factors, observed, latent))
        model_lines.extend(_regression_lines(spec.get("regressions", []), observed, latent))
        model_lines.extend(_covariance_lines(spec.get("covariances", []), observed, latent))
        if spec.get("indirect"):
            extra_lines.extend(["MODEL INDIRECT:", *_indirect_lines(spec["indirect"], observed, latent)])
        if categorical:
            analysis_lines.append("  ESTIMATOR = WLSMV;")
    elif analysis in {"path", "mediation"}:
        model_lines.extend(_regression_lines(spec.get("regressions", []), observed, latent))
        model_lines.extend(_covariance_lines(spec.get("covariances", []), observed, latent))
        if spec.get("indirect"):
            extra_lines.extend(["MODEL INDIRECT:", *_indirect_lines(spec["indirect"], observed, latent)])
    elif analysis == "growth":
        repeated = list(spec.get("repeated", []))
        scores = list(spec.get("time_scores", []))
        if len(repeated) < 3 or len(repeated) != len(scores):
            raise ValueError("增长模型需要至少 3 次 repeated 测量，且 time_scores 数量必须一致。")
        terms = " ".join(f"{v}@{float(t):g}" for v, t in zip(_names(repeated, observed), scores))
        model_lines.append(f"  I S | {terms};")
        model_lines.extend(_regression_lines(spec.get("regressions", []), observed, {"intercept": "I", "slope": "S"}))
    elif analysis == "lca":
        if not categorical or set(categorical) != set(internal):
            raise ValueError("LCA 标准模式要求所有分析指标均声明为 categorical。")
        k = int(spec.get("class_count", 0))
        if k < 1 or k > 10:
            raise ValueError("LCA class_count 必须在 1-10 之间。")
        variable_lines.append(f"  CLASSES = C({k});")
        analysis_lines.extend(["  TYPE = MIXTURE;", "  ESTIMATOR = MLR;"])
        if k == 1:
            analysis_lines.append("  STARTS = 0;")
        else:
            analysis_lines.extend(["  STARTS = 1000 200;", "  STITERATIONS = 20;"])
    else:
        raise ValueError(f"分析类型 {analysis} 已登记，但结构化编译器尚未实现。")

    bootstrap = int(spec.get("bootstrap", 0))
    if bootstrap:
        if analysis != "mediation" or categorical:
            raise ValueError("Bootstrap 标准模式目前仅用于连续变量中介模型。")
        if bootstrap < 500 or bootstrap > 100000:
            raise ValueError("bootstrap 抽样次数必须在 500-100000 之间。")
        analysis_lines.append(f"  BOOTSTRAP = {bootstrap};")

    requested_title = str(spec.get("title") or "")
    safe_title = requested_title.replace(";", "").strip()
    if not safe_title.isascii() or not safe_title:
        safe_title = f"mplusflow {analysis} structured model"
    blocks = [
        f"TITLE: {safe_title};",
        "",
        "DATA:",
        "  FILE = data.dat;",
        "",
        "VARIABLE:",
        *variable_lines,
    ]
    if analysis_lines:
        blocks.extend(["", "ANALYSIS:", *analysis_lines])
    if model_lines:
        blocks.extend(["", "MODEL:", *model_lines])
    if extra_lines:
        blocks.extend(["", *extra_lines])
    if analysis == "efa":
        output = "  MODINDICES;"
    elif analysis == "lca":
        output = "  TECH1 TECH8;" if int(spec.get("class_count", 1)) == 1 else "  TECH1 TECH8 TECH11 TECH14;"
    elif bootstrap:
        output = "  STANDARDIZED CINTERVAL(BCBOOTSTRAP) TECH1 TECH4 RESIDUAL;"
    else:
        output = "  STANDARDIZED CINTERVAL TECH1 TECH4 RESIDUAL;"
    blocks.extend(["", "OUTPUT:", output])
    if analysis == "lca":
        blocks.extend(["", "SAVEDATA:", "  FILE = savedata.dat;", "  SAVE = CPROBABILITIES;"])
    code = "\n".join(blocks) + "\n"
    if "{{" in code or "}}" in code:
        raise RuntimeError("编译后的 Mplus 代码仍有未解析字段。")
    for name in re.findall(r"\b(?:V|F)\d{6}\b", code):
        if not _NAME.fullmatch(name):
            raise RuntimeError(f"内部 Mplus 名称不合法：{name}")
    return code
