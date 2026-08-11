from __future__ import annotations

import re
from typing import Any

from .catalog import get_family
from .utils import wrap_mplus_names


_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,7}$")
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,7}$")
PRELIMINARY_FAMILIES = {"descriptive", "reliability", "correlation", "difference"}
COMPILED_FAMILIES = {
    "efa", "cfa", "sem", "path", "regression", "logistic", "mediation",
    "serial-mediation", "moderation", "moderated-mediation", "growth", "clpm", "lca",
}
COMPILED_GUIDED_FAMILIES = {"multilevel"}


def _names(items: list[str], mapping: dict[str, str]) -> list[str]:
    missing = [x for x in items if x not in mapping]
    if missing:
        raise ValueError(f"模型中使用了未登记变量：{missing}")
    return [mapping[x] for x in items]


def _latent_map(factors: dict[str, list[str]]) -> dict[str, str]:
    return {name: f"F{i:06d}" for i, name in enumerate(factors, 1)}


def _entity(name: str, observed: dict[str, str], latent: dict[str, str], derived: dict[str, str] | None = None) -> str:
    if name in observed:
        return observed[name]
    if name in latent:
        return latent[name]
    if derived and name in derived:
        return derived[name]
    raise ValueError(f"结构模型引用了未登记的变量或因子：{name}")


def _factor_lines(factors: dict[str, list[str]], observed: dict[str, str], latent: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for label, indicators in factors.items():
        if len(indicators) < 2:
            raise ValueError(f"因子“{label}”少于 2 个指标，标准模式停止。")
        lines.append(f"  {latent[label]} BY {wrap_mplus_names(_names(indicators, observed), indent='    ', width=66)};")
    return lines


def _regression_lines(
    regressions: list[dict[str, Any]],
    observed: dict[str, str],
    latent: dict[str, str],
    derived: dict[str, str] | None = None,
) -> list[str]:
    lines: list[str] = []
    for relation in regressions:
        outcome = _entity(str(relation["outcome"]), observed, latent, derived)
        raw_predictors = [str(x) for x in relation.get("predictors", [])]
        predictors = [_entity(x, observed, latent, derived) for x in raw_predictors]
        if not predictors:
            raise ValueError(f"回归结构中 {relation['outcome']} 没有预测变量。")
        labels = relation.get("labels") or {}
        if not isinstance(labels, dict):
            raise ValueError("回归关系的 labels 必须是预测变量到参数标签的对象。")
        if labels:
            unknown = sorted(set(labels) - set(raw_predictors))
            if unknown:
                raise ValueError(f"回归 labels 引用了不在 predictors 中的变量：{unknown}")
            for raw, predictor in zip(raw_predictors, predictors):
                label = labels.get(raw)
                if label is not None and not _LABEL.fullmatch(str(label)):
                    raise ValueError(f"Mplus 参数标签不合法：{label}")
                suffix = f" ({label})" if label is not None else ""
                lines.append(f"  {outcome} ON {predictor}{suffix};")
        else:
            lines.append(f"  {outcome} ON {wrap_mplus_names(predictors, indent='    ', width=66)};")
    return lines


def _indirect_lines(
    indirect: list[dict[str, str]],
    observed: dict[str, str],
    latent: dict[str, str],
    derived: dict[str, str] | None = None,
) -> list[str]:
    lines: list[str] = []
    for relation in indirect:
        y = _entity(relation["outcome"], observed, latent, derived)
        x = _entity(relation["predictor"], observed, latent, derived)
        mediators = relation.get("mediators") or [relation.get("mediator")]
        if not mediators or any(x is None for x in mediators):
            raise ValueError("间接效应必须声明 mediator 或 mediators。")
        mids = [_entity(str(m), observed, latent, derived) for m in reversed(mediators)]
        # Mplus lists serial mediators from the outcome side back to the predictor.
        lines.append(f"  {y} IND {' '.join(mids)} {x};")
    return lines


def _covariance_lines(
    covariances: list[dict[str, str]],
    observed: dict[str, str],
    latent: dict[str, str],
    derived: dict[str, str] | None = None,
) -> list[str]:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for relation in covariances:
        left = _entity(str(relation.get("left", "")), observed, latent, derived)
        right = _entity(str(relation.get("right", "")), observed, latent, derived)
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
    if family.id in PRELIMINARY_FAMILIES:
        raise ValueError(f"“{family.name_zh}”应使用 preliminary 入口，不调用 Mplus 结构化编译器。")
    if family.id not in COMPILED_FAMILIES and family.id not in COMPILED_GUIDED_FAMILIES:
        if family.mode == "guided":
            raise ValueError(f"“{family.name_zh}”当前是引导模块，尚未接入结构化编译器；不会创建半成品项目。")
        raise ValueError(f"“{family.name_zh}”尚不允许通过结构化标准模式自动编译。")
    if family.id in COMPILED_GUIDED_FAMILIES and spec.get("confirmed_guided") is not True:
        raise ValueError(f"“{family.name_zh}”需要先确认层级、变量角色和中心化方案，并设置 confirmed_guided=true。")
    variables = spec.get("variables")
    if not isinstance(variables, list) or not variables or any(not isinstance(x, str) for x in variables):
        raise ValueError("分析设计必须提供非空 variables 字符串列表。")
    if len(set(variables)) != len(variables):
        raise ValueError("variables 中存在重复变量。")
    factors = spec.get("factors") or {}
    if family.id == "cfa" and len(factors) == 1:
        factor_name, indicators = next(iter(factors.items()))
        if len(indicators) == 2:
            raise ValueError(
                f"单因子 CFA“{factor_name}”仅有 2 个指标，普通模板无法提供可检验的"
                "测量模型。请先核对量表设计；若有明确的额外识别约束，转入专家模式。"
            )
    categorical = spec.get("categorical", [])
    if not isinstance(categorical, list) or any(x not in variables for x in categorical):
        raise ValueError("categorical 必须是 variables 的子集。")
    if family.id == "logistic":
        outcome = str(spec.get("outcome", ""))
        if not outcome or outcome not in variables:
            raise ValueError("Logistic 回归必须声明 variables 中的 outcome。")
        if outcome not in categorical:
            raise ValueError("Logistic 回归的 outcome 必须同时声明在 categorical 中。")
        if any(name != outcome for name in categorical):
            raise ValueError("Logistic 标准入口只把结果变量声明为 categorical；分类预测变量应先按研究设计编码。")
    if family.id in {"moderation", "moderated-mediation"} and not spec.get("interactions"):
        raise ValueError(f"{family.name_zh}必须声明 interactions。")
    if family.id in {"mediation", "serial-mediation"}:
        regressions = spec.get("regressions") or []
        edges = {
            (str(predictor), str(relation.get("outcome", "")))
            for relation in regressions
            for predictor in relation.get("predictors", [])
        }
        allowed = set(variables) | set((spec.get("factors") or {}).keys())
        for effect in spec.get("indirect") or []:
            predictor = str(effect.get("predictor", ""))
            outcome = str(effect.get("outcome", ""))
            mediators = effect.get("mediators") or [effect.get("mediator")]
            if not predictor or not outcome or not mediators or any(item is None for item in mediators):
                raise ValueError("中介效应必须明确 predictor、outcome 和 mediator/mediators。")
            route = [predictor, *map(str, mediators), outcome]
            if len(route) != len(set(route)):
                raise ValueError("中介路径中的自变量、中介变量和因变量不能重复。")
            unknown = [name for name in route if name not in allowed]
            if unknown:
                raise ValueError(f"中介路径引用了未登记的变量或因子：{unknown}")
            missing_edges = [(left, right) for left, right in zip(route, route[1:]) if (left, right) not in edges]
            if missing_edges:
                readable = "、".join(f"{right} ON {left}" for left, right in missing_edges)
                raise ValueError(
                    f"间接效应声明的路径与回归结构不一致，缺少：{readable}。"
                    "链式中介的 mediators 应按 X→M1→M2→Y 的研究顺序填写。"
                )
    if family.id == "clpm":
        panels = spec.get("panels")
        if not isinstance(panels, dict) or len(panels) != 2:
            raise ValueError("基础 CLPM 必须提供两个变量的 panels。")
        waves = [list(values) for values in panels.values()]
        if any(len(values) < 3 for values in waves) or len(waves[0]) != len(waves[1]):
            raise ValueError("基础 CLPM 需要两个变量各至少 3 个相同波次的测量。")
        if any(item not in variables for values in waves for item in values):
            raise ValueError("panels 中的变量必须全部登记在 variables。")
    if family.id == "multilevel":
        cluster = str(spec.get("cluster", ""))
        within = [str(x) for x in spec.get("within_variables", [])]
        between = [str(x) for x in spec.get("between_variables", [])]
        if not cluster or cluster not in variables:
            raise ValueError("多层分析必须声明 variables 中的 cluster。")
        if not within and not between:
            raise ValueError("多层分析必须声明 within_variables 或 between_variables。")
        if cluster in within or cluster in between:
            raise ValueError("cluster 不能同时作为层级内或层级间分析变量。")
        if set(within) & set(between):
            raise ValueError("同一观测变量不能同时声明为纯个体层和纯群体层变量。")
        if any(name not in variables for name in [*within, *between]):
            raise ValueError("within_variables 和 between_variables 必须是 variables 的子集。")
        if spec.get("random_slopes") and categorical:
            raise ValueError("当前跨层随机斜率入口只支持连续结果；分类结果进入专家路径。")
        within_factors = set((spec.get("within_factors") or {}).keys())
        between_factors = set((spec.get("between_factors") or {}).keys())
        if within_factors & between_factors:
            raise ValueError("个体层和群体层潜变量名称不能重复。")
        observed_predictors: set[str] = set()
        for relation in spec.get("within_regressions", []):
            names = {str(relation.get("outcome", "")), *map(str, relation.get("predictors", []))}
            if names & set(between):
                raise ValueError("纯群体层变量不能出现在 within_regressions。")
            if names & between_factors:
                raise ValueError("群体层潜变量不能出现在 within_regressions。")
            for predictor in map(str, relation.get("predictors", [])):
                if predictor in variables and predictor not in within:
                    raise ValueError(f"组内回归预测变量“{predictor}”必须声明在 within_variables。")
                if predictor in variables:
                    observed_predictors.add(predictor)
        slope_names = {str(item.get("name", "")) for item in spec.get("random_slopes", [])}
        for item in spec.get("random_slopes", []):
            predictor = str(item.get("predictor", ""))
            outcome = str(item.get("outcome", ""))
            if predictor not in within:
                raise ValueError(f"随机斜率预测变量“{predictor}”必须声明在 within_variables。")
            if outcome in between:
                raise ValueError(f"随机斜率结果变量“{outcome}”不能是纯 BETWEEN 变量。")
            observed_predictors.add(predictor)
        for relation in spec.get("between_regressions", []):
            names = {str(relation.get("outcome", "")), *map(str, relation.get("predictors", []))} - slope_names
            if names & set(within):
                raise ValueError("纯个体层变量不能出现在 between_regressions。")
            if names & within_factors:
                raise ValueError("个体层潜变量不能出现在 between_regressions。")
            for predictor in map(str, relation.get("predictors", [])):
                if predictor in slope_names or predictor in between_factors:
                    continue
                if predictor in variables and predictor not in between:
                    raise ValueError(f"组间回归预测变量“{predictor}”必须声明在 between_variables。")
                if predictor in variables:
                    observed_predictors.add(predictor)
        centering = spec.get("centering")
        if not isinstance(centering, dict):
            raise ValueError(
                "多层分析必须为每个观测预测变量提供 centering，例如 "
                '{"x":"groupmean","w":"grandmean"}；如不中心化请明确写 none。'
            )
        unknown_centering = sorted(set(map(str, centering)) - set(variables))
        if unknown_centering:
            raise ValueError(f"centering 引用了未登记变量：{unknown_centering}")
        missing_centering = sorted(observed_predictors - set(map(str, centering)))
        if missing_centering:
            raise ValueError(f"以下多层预测变量尚未明确中心化方案：{missing_centering}")
        for variable, mode in centering.items():
            normalized = str(mode).lower()
            if normalized not in {"groupmean", "grandmean", "none"}:
                raise ValueError(f"变量“{variable}”的中心化只能是 groupmean、grandmean 或 none。")
            if str(variable) in between and normalized == "groupmean":
                raise ValueError(f"纯 BETWEEN 变量“{variable}”不能做组均值中心化。")


def _interaction_setup(
    interactions: list[dict[str, Any]], observed: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    derived: dict[str, str] = {}
    define_lines: list[str] = []
    centered: set[str] = set()
    for index, relation in enumerate(interactions, 1):
        name = str(relation.get("name", ""))
        left = str(relation.get("left", ""))
        right = str(relation.get("right", ""))
        if not name or name in observed or name in derived:
            raise ValueError("interaction name 必须唯一，且不能与原变量重名。")
        if left not in observed or right not in observed or left == right:
            raise ValueError(f"交互项“{name}”的 left/right 必须是两个不同的原始变量。")
        center = str(relation.get("center", "grandmean")).lower()
        if center not in {"grandmean", "none"}:
            raise ValueError("观测变量交互项的 center 只能是 grandmean 或 none。")
        if center == "grandmean":
            for original in [left, right]:
                internal = observed[original]
                if internal not in centered:
                    define_lines.append(f"  CENTER {internal} (GRANDMEAN);")
                    centered.add(internal)
        internal_name = f"D{index:06d}"
        derived[name] = internal_name
        define_lines.append(f"  {internal_name} = {observed[left]}*{observed[right]};")
    return derived, define_lines


def _conditional_indirect_lines(spec: dict[str, Any]) -> list[str]:
    condition = spec.get("conditional_indirect")
    if not condition:
        return []
    required = ["a_label", "interaction_label", "b_label", "moderator_values"]
    if any(key not in condition for key in required):
        raise ValueError(f"conditional_indirect 必须提供 {required}。")
    a_label = str(condition["a_label"])
    interaction_label = str(condition["interaction_label"])
    b_label = str(condition["b_label"])
    if any(not _LABEL.fullmatch(label) for label in [a_label, interaction_label, b_label]):
        raise ValueError("conditional_indirect 的参数标签必须是 1-8 位 Mplus 名称。")
    values = condition["moderator_values"]
    if not isinstance(values, dict) or not values:
        raise ValueError("moderator_values 必须是名称到数值的对象。")
    names: list[str] = []
    equations: list[str] = []
    for raw_name, raw_value in values.items():
        name = str(raw_name).upper()
        if not _LABEL.fullmatch(name):
            raise ValueError(f"条件间接效应名称不合法：{raw_name}")
        value = float(raw_value)
        names.append(name)
        equations.append(f"  {name} = ({a_label} + {interaction_label}*({value:g}))*{b_label};")
    return ["MODEL CONSTRAINT:", f"  NEW({' '.join(names)});", *equations]


def _clpm_lines(spec: dict[str, Any], observed: dict[str, str]) -> list[str]:
    panels = {str(name): [str(x) for x in values] for name, values in spec["panels"].items()}
    left_name, right_name = list(panels)
    left, right = panels[left_name], panels[right_name]
    equal = bool(spec.get("equal_lagged", False))
    lines = [f"  {observed[left[0]]} WITH {observed[right[0]]};"]
    for wave in range(1, len(left)):
        labels_left = {left[wave - 1]: "ARX", right[wave - 1]: "CLYX"} if equal else {}
        labels_right = {right[wave - 1]: "ARY", left[wave - 1]: "CLXY"} if equal else {}
        lines.extend(_regression_lines([
            {"outcome": left[wave], "predictors": [left[wave - 1], right[wave - 1]], "labels": labels_left},
            {"outcome": right[wave], "predictors": [right[wave - 1], left[wave - 1]], "labels": labels_right},
        ], observed, {}))
        lines.append(f"  {observed[left[wave]]} WITH {observed[right[wave]]};")
    return lines


def _multilevel_lines(spec: dict[str, Any], observed: dict[str, str]) -> tuple[list[str], bool]:
    within_factors = spec.get("within_factors") or {}
    between_factors = spec.get("between_factors") or {}
    if not isinstance(within_factors, dict) or not isinstance(between_factors, dict):
        raise ValueError("within_factors 和 between_factors 必须是因子名到指标列表的对象。")
    overlap = set(within_factors) & set(between_factors)
    if overlap:
        raise ValueError(f"个体层与群体层因子名称不能重复：{sorted(overlap)}")
    latent = _latent_map({**within_factors, **between_factors})
    within_lines = _factor_lines(within_factors, observed, latent)
    between_lines = _factor_lines(between_factors, observed, latent)
    random_slopes = spec.get("random_slopes") or []
    if not isinstance(random_slopes, list):
        raise ValueError("random_slopes 必须是列表。")
    slopes: dict[str, str] = {}
    for index, relation in enumerate(random_slopes, 1):
        name = str(relation.get("name", ""))
        outcome = str(relation.get("outcome", ""))
        predictor = str(relation.get("predictor", ""))
        if not name or name in slopes or outcome not in observed or predictor not in observed:
            raise ValueError("每个随机斜率都必须有唯一 name，并引用已登记的 outcome 和 predictor。")
        internal = f"S{index:06d}"
        slopes[name] = internal
        within_lines.append(f"  {internal} | {observed[outcome]} ON {observed[predictor]};")
    within_lines.extend(_regression_lines(spec.get("within_regressions", []), observed, latent))
    within_lines.extend(_covariance_lines(spec.get("within_covariances", []), observed, latent))
    between_entities = {**latent, **slopes}
    between_lines.extend(_regression_lines(spec.get("between_regressions", []), observed, {}, between_entities))
    between_lines.extend(_covariance_lines(spec.get("between_covariances", []), observed, {}, between_entities))
    return ["  %WITHIN%", *within_lines, "  %BETWEEN%", *between_lines], bool(slopes)


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
    interactions = spec.get("interactions") or []
    if not isinstance(interactions, list):
        raise ValueError("interactions 必须是列表。")
    derived, define_lines = _interaction_setup(interactions, observed)
    if analysis == "multilevel":
        for variable, mode in (spec.get("centering") or {}).items():
            normalized = str(mode).lower()
            if normalized != "none":
                define_lines.append(f"  CENTER {observed[str(variable)]} ({normalized.upper()});")

    model_internal = list(internal)
    if analysis == "multilevel":
        model_internal = [name for original, name in zip(variables, internal) if original != str(spec.get("cluster"))]
    variable_lines = [
        "  NAMES ARE " + wrap_mplus_names(["ROWID", *internal], indent="    ", width=66) + ";",
        "  USEVARIABLES ARE " + wrap_mplus_names([*model_internal, *derived.values()], indent="    ", width=59) + ";",
        "  IDVARIABLE = ROWID;",
    ]
    if spec.get("has_missing"):
        variable_lines.append(f"  MISSING = ALL({int(missing_code)});")
    if categorical:
        variable_lines.append("  CATEGORICAL ARE " + wrap_mplus_names(categorical, indent="    ", width=59) + ";")
    if analysis == "multilevel":
        variable_lines.append(f"  CLUSTER = {observed[str(spec['cluster'])]};")
        within_names = _names([str(x) for x in spec.get("within_variables", [])], observed)
        between_names = _names([str(x) for x in spec.get("between_variables", [])], observed)
        if within_names:
            variable_lines.append("  WITHIN = " + wrap_mplus_names(within_names, indent="    ", width=70) + ";")
        if between_names:
            variable_lines.append("  BETWEEN = " + wrap_mplus_names(between_names, indent="    ", width=69) + ";")

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
    elif analysis in {"cfa", "sem"} or (analysis in {"mediation", "serial-mediation"} and factors):
        if analysis in {"cfa", "sem"} and not factors:
            raise ValueError(f"{analysis.upper()} 必须声明 factors。")
        model_lines.extend(_factor_lines(factors, observed, latent))
        model_lines.extend(_regression_lines(spec.get("regressions", []), observed, latent, derived))
        model_lines.extend(_covariance_lines(spec.get("covariances", []), observed, latent, derived))
        if spec.get("indirect"):
            extra_lines.extend(["MODEL INDIRECT:", *_indirect_lines(spec["indirect"], observed, latent, derived)])
        if categorical:
            analysis_lines.append("  ESTIMATOR = WLSMV;")
    elif analysis in {"path", "regression", "logistic", "mediation", "serial-mediation", "moderation", "moderated-mediation"}:
        if analysis in {"moderation", "moderated-mediation"} and factors:
            raise ValueError("当前标准调节入口只支持观测变量；潜变量交互应进入专家路径。")
        model_lines.extend(_regression_lines(spec.get("regressions", []), observed, latent, derived))
        model_lines.extend(_covariance_lines(spec.get("covariances", []), observed, latent, derived))
        if spec.get("indirect"):
            extra_lines.extend(["MODEL INDIRECT:", *_indirect_lines(spec["indirect"], observed, latent, derived)])
        extra_lines.extend(_conditional_indirect_lines(spec))
        if analysis == "logistic":
            analysis_lines.append("  ESTIMATOR = MLR;")
    elif analysis == "clpm":
        model_lines.extend(_clpm_lines(spec, observed))
    elif analysis == "multilevel":
        model_lines, has_random = _multilevel_lines(spec, observed)
        analysis_lines.append("  TYPE = TWOLEVEL RANDOM;" if has_random else "  TYPE = TWOLEVEL;")
        if categorical:
            analysis_lines.append("  ESTIMATOR = WLSMV;")
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
        if analysis not in {"mediation", "serial-mediation", "moderated-mediation"} or categorical:
            raise ValueError("Bootstrap 标准模式目前仅用于连续变量中介、链式中介或有调节的中介模型。")
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
    if define_lines:
        blocks.extend(["", "DEFINE:", *define_lines])
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
    elif analysis == "multilevel" and spec.get("random_slopes"):
        output = "  TECH1 TECH8 CINTERVAL;"
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
