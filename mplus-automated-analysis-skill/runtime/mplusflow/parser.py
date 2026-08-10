from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pandas as pd

from .mplus_detect import version_profile


@dataclass
class MplusResult:
    file: str
    normal_termination: bool
    errors: list[str]
    warnings: list[str]
    sample_size: int | None
    estimator: str | None
    class_count: int | None
    continuous_variables: list[str]
    loglikelihood: float | None
    free_parameters: int | None
    aic: float | None
    bic: float | None
    sabic: float | None
    entropy: float | None
    best_ll_replicated: bool | None
    best_seed: int | None
    class_counts: list[int]
    class_proportions: list[float]
    posterior_diag: list[float]
    tech11_p: float | None
    tech11_adjusted_p: float | None
    tech14_p: float | None
    tech14_trustworthy: bool | None
    class_means: dict[str, dict[str, float]]
    savedata_variables: list[str]
    savedata_filename: str | None
    mplus_version: str | None = None
    output_profile: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_mplus_text(path: Path) -> str:
    for enc in ["utf-8", "utf-8-sig", "cp1252", "latin1"]:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin1", errors="ignore")


def _float(pattern: str, text: str, flags: int = re.I | re.M) -> float | None:
    m = re.search(pattern, text, flags)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:  # noqa: BLE001
        return None


def _int(pattern: str, text: str, flags: int = re.I | re.M) -> int | None:
    m = re.search(pattern, text, flags)
    return int(m.group(1)) if m else None


def _collect_message_blocks(text: str, label: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    label_upper = label.upper()
    while i < len(lines):
        if label_upper in lines[i].upper():
            block = [lines[i].strip()]
            j = i + 1
            while j < len(lines) and lines[j].strip() and len(block) < 20:
                if re.match(r"^[A-Z][A-Z\s\-]+$", lines[j].strip()) and len(block) > 1:
                    break
                block.append(lines[j].strip())
                j += 1
            blocks.append(" ".join(x for x in block if x))
            i = j
        else:
            i += 1
    return blocks


def _parse_continuous_variables(text: str) -> list[str]:
    m = re.search(
        r"Observed dependent variables\s+Continuous\s+(.*?)(?:\n\s*(?:Categorical latent variables|Observed independent variables|Continuous latent variables|Variables with special functions|Estimator)\b)",
        text,
        re.I | re.S,
    )
    if not m:
        return []
    return re.findall(r"\b(?:V\d{6}|ROWID|[A-Z][A-Z0-9_]{0,7})\b", m.group(1), re.I)


def _parse_class_counts(text: str) -> tuple[list[int], list[float]]:
    start = re.search(
        r"FINAL CLASS COUNTS AND PROPORTIONS FOR THE LATENT CLASSES\s+BASED ON THEIR MOST LIKELY LATENT CLASS MEMBERSHIP",
        text,
        re.I,
    )
    if not start:
        return [], []
    tail = text[start.end():]
    end = re.search(r"CLASSIFICATION QUALITY", tail, re.I)
    section = tail[: end.start()] if end else tail[:1500]
    pairs: list[tuple[int, float]] = []
    for line in section.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+([01]?\.\d+|1\.0+)\s*$", line)
        if m:
            pairs.append((int(m.group(2)), float(m.group(3))))
    # 去重，通常每类仅出现一次。
    if not pairs:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _parse_posterior_diag(text: str, k: int | None) -> list[float]:
    if not k or k < 2:
        return []
    m = re.search(
        r"Average Latent Class Probabilities for Most Likely Latent Class Membership \(Row\)\s+by Latent Class \(Column\)(.*?)(?:Classification Probabilities|Logits for the Classification Probabilities|MODEL RESULTS)",
        text,
        re.I | re.S,
    )
    if not m:
        return []
    diag: list[float] = []
    for line in m.group(1).splitlines():
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", line)
        if len(nums) == k + 1:
            row = int(float(nums[0]))
            vals = [float(x) for x in nums[1:]]
            if 1 <= row <= k:
                diag.append(vals[row - 1])
    return diag[:k]


def _parse_tech11(text: str) -> tuple[float | None, float | None]:
    m = re.search(r"VUONG-LO-MENDELL-RUBIN LIKELIHOOD RATIO TEST(.*?)(?:TECHNICAL 14 OUTPUT|PARAMETRIC BOOTSTRAPPED|QUALITY OF NUMERICAL RESULTS|$)", text, re.I | re.S)
    if not m:
        return None, None
    section = m.group(1)
    pvals = [float(x) for x in re.findall(r"P-Value\s+([0-9]*\.?[0-9]+)", section, re.I)]
    if not pvals:
        return None, None
    return pvals[0], pvals[1] if len(pvals) > 1 else None


def _parse_tech14(text: str) -> tuple[float | None, bool | None]:
    m = re.search(r"PARAMETRIC BOOTSTRAPPED LIKELIHOOD RATIO TEST(.*?)(?:QUALITY OF NUMERICAL RESULTS|TECHNICAL 15 OUTPUT|$)", text, re.I | re.S)
    if not m:
        return None, None
    section = m.group(1)
    p = _float(r"(?:Approximate\s+)?P-Value\s+([0-9]*\.?[0-9]+)", section)
    bad = any(
        phrase in section.upper()
        for phrase in [
            "P-VALUE MAY NOT BE TRUSTWORTHY",
            "DID NOT CONVERGE",
            "BEST LOGLIKELIHOOD VALUE WAS NOT REPLICATED",
            "LIKELIHOOD RATIO TEST COULD NOT BE COMPUTED",
        ]
    )
    return p, (not bad) if p is not None else False


def _parse_best_seed(text: str) -> int | None:
    m = re.search(r"RANDOM STARTS RESULTS RANKED FROM THE BEST TO THE WORST LOGLIKELIHOOD VALUES(.*?)(?:THE BEST LOGLIKELIHOOD VALUE|THE MODEL ESTIMATION)", text, re.I | re.S)
    if not m:
        return None
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*-?\d+\.\d+\s+(\d+)\s+\d+\s*$", line)
        if mm:
            return int(mm.group(1))
    return None


def _parse_class_means(text: str) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    model_pos = re.search(r"\n\s*MODEL RESULTS\s*\n", text, re.I)
    if not model_pos:
        return result
    section = text[model_pos.end():]
    class_matches = list(re.finditer(r"Latent Class\s+(\d+)", section, re.I))
    for i, cm in enumerate(class_matches):
        class_no = cm.group(1)
        end = class_matches[i + 1].start() if i + 1 < len(class_matches) else min(len(section), cm.start() + 6000)
        csec = section[cm.end():end]
        mm = re.search(r"\n\s*Means\s*\n(.*?)(?:\n\s*Variances\s*\n|\n\s*Thresholds\s*\n|\n\s*Residual Variances\s*\n)", csec, re.I | re.S)
        if not mm:
            continue
        vals: dict[str, float] = {}
        for line in mm.group(1).splitlines():
            vm = re.match(r"\s*(V\d{6}|[A-Z][A-Z0-9_]{0,7})\s+(-?\d+\.\d+)", line, re.I)
            if vm:
                vals[vm.group(1).upper()] = float(vm.group(2))
        if vals:
            result[class_no] = vals
    return result


def _parse_savedata_info(text: str) -> tuple[str | None, list[str]]:
    m = re.search(r"SAVEDATA INFORMATION(.*?)(?:PLOT INFORMATION|DIAGRAM INFORMATION|$)", text, re.I | re.S)
    if not m:
        return None, []
    section = m.group(1)
    fn = None
    fm = re.search(r"Save file\s+(?:is\s+)?([^\s]+)", section, re.I)
    if fm:
        fn = fm.group(1).strip()
    # Mplus 8.x prints "Order of variables" with one name per line, while
    # newer outputs may include a storage format after each variable name.
    om = re.search(r"Order(?: and format)? of variables(.*?)(?:Save file format|Save file record length|$)", section, re.I | re.S)
    vars_: list[str] = []
    if om:
        for line in om.group(1).splitlines():
            vm = re.match(r"\s*([A-Za-z][A-Za-z0-9_#]*)\s*(?:[AIF]\d.*)?$", line)
            if vm:
                vars_.append(vm.group(1).upper())
    return fn, vars_


def parse_mplus_output(path: str | Path) -> MplusResult:
    p = Path(path)
    text = read_mplus_text(p)
    version_match = re.search(r"Mplus\s+VERSION\s+([0-9]+(?:\.[0-9]+)?)", text, re.I)
    mplus_version = version_match.group(1) if version_match else None
    output_profile, _ = version_profile(mplus_version)
    errors = _collect_message_blocks(text, "*** ERROR")
    warnings = _collect_message_blocks(text, "*** WARNING")
    warnings.extend(_collect_message_blocks(text, "WARNING:"))
    warnings.extend(_collect_message_blocks(text, "COMPUTATIONAL PROBLEMS"))
    for phrase in [
        "NO CONVERGENCE.  NUMBER OF ITERATIONS EXCEEDED.",
        "THE MODEL ESTIMATION DID NOT TERMINATE NORMALLY.  ESTIMATES CANNOT BE TRUSTED.",
    ]:
        if phrase in text.upper() and phrase not in warnings:
            warnings.append(phrase)
    warnings = list(dict.fromkeys(warnings))

    class_count = _int(r"CLASSES\s*=\s*C\s*\(\s*(\d+)\s*\)", text)
    counts, props = _parse_class_counts(text)
    if class_count is None and counts:
        class_count = len(counts)

    estimator_match = re.search(r"^\s*Estimator\s+([A-Z0-9]+)\s*$", text, re.I | re.M)
    estimator = estimator_match.group(1).upper() if estimator_match else None
    ll_repl: bool | None
    if class_count == 1:
        ll_repl = True
    elif "THE BEST LOGLIKELIHOOD VALUE HAS BEEN REPLICATED" in text.upper():
        ll_repl = True
    elif "THE BEST LOGLIKELIHOOD VALUE WAS NOT REPLICATED" in text.upper() or "BEST LOGLIKELIHOOD VALUE HAS NOT BEEN REPLICATED" in text.upper():
        ll_repl = False
    else:
        ll_repl = None

    tech11, tech11_adj = _parse_tech11(text)
    tech14, tech14_ok = _parse_tech14(text)
    save_fn, save_vars = _parse_savedata_info(text)

    return MplusResult(
        file=str(p),
        normal_termination="THE MODEL ESTIMATION TERMINATED NORMALLY" in text.upper(),
        errors=errors,
        warnings=warnings,
        sample_size=_int(r"Number of observations\s+(\d+)", text),
        estimator=estimator,
        class_count=class_count,
        continuous_variables=_parse_continuous_variables(text),
        loglikelihood=_float(r"H0 Value\s+(-?\d+\.\d+)", text),
        free_parameters=_int(r"Number of Free Parameters\s+(\d+)", text),
        aic=_float(r"Akaike \(AIC\)\s+(-?\d+\.\d+)", text),
        bic=_float(r"Bayesian \(BIC\)\s+(-?\d+\.\d+)", text),
        sabic=_float(r"Sample-Size Adjusted BIC\s+(-?\d+\.\d+)", text),
        entropy=_float(r"Entropy\s+([0-9]*\.?[0-9]+)", text),
        best_ll_replicated=ll_repl,
        best_seed=_parse_best_seed(text),
        class_counts=counts,
        class_proportions=props,
        posterior_diag=_parse_posterior_diag(text, class_count),
        tech11_p=tech11,
        tech11_adjusted_p=tech11_adj,
        tech14_p=tech14,
        tech14_trustworthy=tech14_ok,
        class_means=_parse_class_means(text),
        savedata_variables=save_vars,
        savedata_filename=save_fn,
        mplus_version=mplus_version,
        output_profile=output_profile,
    )


def read_savedata(savedata_path: Path, variable_names: list[str]) -> pd.DataFrame:
    if not savedata_path.exists():
        raise FileNotFoundError(f"找不到 Mplus SAVEDATA 文件：{savedata_path}")
    if not variable_names:
        raise ValueError("无法从 Mplus 输出确定 SAVEDATA 的变量顺序，拒绝猜测列名。")
    df = pd.read_csv(
        savedata_path,
        sep=r"\s+",
        header=None,
        na_values=["*"],
        engine="python",
    )
    if df.shape[1] != len(variable_names):
        raise ValueError(f"SAVEDATA 列数与 Mplus 输出中的变量顺序不一致：实际 {df.shape[1]}，应为 {len(variable_names)}")
    df.columns = variable_names
    return df
