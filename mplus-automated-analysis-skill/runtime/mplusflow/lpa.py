from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import ensure_dir, wrap_mplus_names


START_TIERS = {
    "screening": (200, 40),
    "verification": (1000, 200),
    "difficult": (5000, 1000),
}

TEMPLATE_RELATIVE_PATH = Path("assets/templates/LPA_基础模型.inp.tmpl")


def template_path() -> Path:
    candidates = [Path(__file__).resolve().parents[2] / TEMPLATE_RELATIVE_PATH]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(0, Path(bundle_root) / TEMPLATE_RELATIVE_PATH)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("找不到认证模板 assets/templates/LPA_基础模型.inp.tmpl，拒绝生成未登记代码。")


@dataclass
class GeneratedModel:
    k: int
    stage: str
    runtime_dir: Path
    input_path: Path
    visible_input_path: Path
    output_path: Path
    savedata_path: Path | None


def render_lpa_input(
    k: int,
    indicator_names: list[str],
    missing_code: float,
    stage: str = "screening",
    optseed: int | None = None,
    include_lrt: bool = False,
    starts: tuple[int, int] | None = None,
    stiterations: int = 20,
) -> str:
    if k < 1:
        raise ValueError("类别数必须 >= 1")
    all_names = ["ROWID"] + indicator_names
    all_vars = wrap_mplus_names(all_names, indent="    ")
    indicators = wrap_mplus_names(indicator_names, indent="    ")

    if k == 1:
        starts_block = "  STARTS = 0;\n"
        optseed_block = ""
        lrtstarts_block = ""
        lrt_output = ""
        savedata_block = ""
    else:
        if optseed is not None and include_lrt:
            # Web Note 14 style: use the seed of an already stable solution.
            starts_block = "  STARTS = 0;\n"
            optseed_block = f"  OPTSEED = {optseed};\n"
        else:
            s = starts or START_TIERS.get(stage, START_TIERS["screening"])
            starts_block = f"  STARTS = {s[0]} {s[1]};\n  STITERATIONS = {stiterations};\n"
            optseed_block = ""
        lrtstarts_block = "  LRTSTARTS = 0 0 100 20;\n" if include_lrt else ""
        lrt_output = " TECH11 TECH14" if include_lrt else ""
        savedata_block = (
            "SAVEDATA:\n"
            "  FILE = class_probs.dat;\n"
            "  SAVE = CPROBABILITIES;\n"
            "  FORMAT = FREE;\n"
        )

    replacements = {
        "{{CLASS_COUNT}}": str(k),
        "{{ALL_VARIABLES}}": all_vars,
        "{{INDICATORS}}": indicators,
        "{{MISSING_CODE}}": str(int(missing_code) if float(missing_code).is_integer() else missing_code),
        "{{STARTS_BLOCK}}": starts_block,
        "{{OPTSEED_BLOCK}}": optseed_block,
        "{{LRTSTARTS_BLOCK}}": lrtstarts_block,
        "{{LRT_OUTPUT}}": lrt_output,
        "{{SAVEDATA_BLOCK}}": savedata_block,
    }
    code = template_path().read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        code = code.replace(placeholder, value)
    unresolved = re.findall(r"\{\{[A-Z0-9_]+\}\}", code)
    if unresolved:
        raise RuntimeError(f"认证模板仍有未填充字段：{unresolved}")
    return code


def write_model(
    project_dir: Path,
    runtime_root: Path,
    k: int,
    indicator_names: list[str],
    missing_code: float,
    stage: str,
    optseed: int | None = None,
    include_lrt: bool = False,
    starts: tuple[int, int] | None = None,
) -> GeneratedModel:
    model_dir = ensure_dir(runtime_root / f"k{k}_{stage}")
    shutil.copy2(runtime_root / "data.dat", model_dir / "data.dat")
    code = render_lpa_input(k, indicator_names, missing_code, stage, optseed, include_lrt, starts)
    inp = model_dir / "model.inp"
    inp.write_text(code, encoding="ascii")
    visible = ensure_dir(project_dir / "03_Mplus代码") / f"{k}类别_分析代码.inp"
    visible.write_text(code, encoding="ascii")
    return GeneratedModel(
        k=k,
        stage=stage,
        runtime_dir=model_dir,
        input_path=inp,
        visible_input_path=visible,
        output_path=model_dir / "model.out",
        savedata_path=(model_dir / "class_probs.dat") if k >= 2 else None,
    )


def run_model(model: GeneratedModel, mplus_command: str, timeout_seconds: int = 7200) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [mplus_command, model.input_path.name],
        cwd=model.runtime_dir,
        capture_output=True,
        text=True,
        errors="ignore",
        timeout=timeout_seconds,
    )
    # 始终保存进程层日志，便于区分“没有生成 .out”与“Mplus模型本身报错”。
    (model.runtime_dir / "process.log").write_text(
        f"returncode={proc.returncode}\n\n[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}\n",
        encoding="utf-8", errors="ignore"
    )
    return proc


def mirror_output(model: GeneratedModel, project_dir: Path) -> Path | None:
    if not model.output_path.exists():
        return None
    dst = ensure_dir(project_dir / "04_Mplus原始结果") / f"{model.k}类别_Mplus输出.out"
    shutil.copy2(model.output_path, dst)
    return dst


def assert_template_integrity(code: str, k: int, indicators: list[str]) -> list[str]:
    """
    静态检查基础 LPA 模板有没有被改义。

    这里采用“允许列表”思路：标准 LPA 的模型含义来自官方 Example 7.9 的
    默认参数化，因此任何 MODEL/DEFINE/变量类型/抽样结构语句的出现都应先
    切换到另一个已登记模板，而不是在基础模板上临时修改。
    """
    issues: list[str] = []
    required = [
        "TYPE = MIXTURE;",
        "ESTIMATOR = MLR;",
        f"CLASSES = C({k});",
        "IDVARIABLE = ROWID;",
    ]
    for item in required:
        if item not in code:
            issues.append(f"缺少模板必需语句：{item}")

    # 基础模板依赖 Mplus 的默认 mixture 参数化，不允许 Agent 临时增加会改变模型含义的命令。
    forbidden_patterns = {
        r"(?im)^\s*MODEL\s*:": "基础LPA模板不允许出现 MODEL:；需要释放方差/协方差或加入协变量时必须切换模板。",
        r"(?im)^\s*DEFINE\s*:": "标准模式不允许在 Mplus 内部临时 DEFINE/转换变量。",
        r"\bWITH\b": "基础LPA模板出现 WITH 语句，可能未经授权释放类内协方差。",
        r"\bCATEGORICAL\b": "基础LPA模板出现 CATEGORICAL，指标类型已偏离连续指标LPA。",
        r"\bNOMINAL\b": "基础LPA模板出现 NOMINAL，指标类型已偏离连续指标LPA。",
        r"\bCOUNT\b": "基础LPA模板出现 COUNT，指标类型已偏离连续指标LPA。",
        r"\bCENSORED\b": "基础LPA模板出现 CENSORED，指标类型已偏离连续指标LPA。",
        r"\bCLUSTER\s*=|\bCLUSTER\s+IS": "基础LPA模板出现聚类结构，必须切换多层/复杂抽样模板。",
        r"\bWEIGHT\s*=|\bWEIGHT\s+IS": "基础LPA模板出现权重，必须切换带权重的已登记模板。",
        r"\bGROUPING\s*=|\bGROUPING\s+IS": "基础LPA模板出现分组分析，必须切换多组模板。",
        r"\bUSEOBSERVATIONS\b|\bSUBPOPULATION\b": "基础LPA模板出现样本筛选语句，可能改变分析样本。",
        r"\bAUXILIARY\b": "基础LPA模板出现 AUXILIARY；协变量/三步法需使用专门模板。",
    }
    for pattern, msg in forbidden_patterns.items():
        if re.search(pattern, code, re.I | re.M):
            issues.append(msg)

    # NAMES 与 USEVARIABLES 必须与运行时建立的变量映射完全一致。
    names_match = re.search(r"NAMES\s+ARE\s+(.+?);", code, re.I | re.S)
    if not names_match:
        issues.append("无法找到 NAMES ARE。")
    else:
        names = re.findall(r"\b(?:ROWID|V\d{6})\b", names_match.group(1), re.I)
        expected_names = ["ROWID", *indicators]
        if [x.upper() for x in names] != [x.upper() for x in expected_names]:
            issues.append(f"NAMES ARE 与分析数据列顺序不一致：代码={names}，设计={expected_names}")

    use_match = re.search(r"USEVARIABLES\s+ARE\s+(.+?);", code, re.I | re.S)
    if not use_match:
        issues.append("无法找到 USEVARIABLES。")
    else:
        used = re.findall(r"\bV\d{6}\b", use_match.group(1), re.I)
        if [u.upper() for u in used] != [x.upper() for x in indicators]:
            issues.append(f"USEVARIABLES 与分析设计不一致：代码={used}，设计={indicators}")

    if len(re.findall(r"\bCLASSES\s*=", code, re.I)) != 1:
        issues.append("CLASSES 声明数量异常，标准模板应且只能出现一次。")
    if len(re.findall(r"\bESTIMATOR\s*=", code, re.I)) != 1:
        issues.append("ESTIMATOR 声明数量异常，标准模板应且只能出现一次。")
    if not re.search(r"OUTPUT\s*:\s*[^;]*TECH1\s+TECH8", code, re.I | re.S):
        issues.append("OUTPUT 缺少 TECH1/TECH8，无法完成参数化与优化过程核验。")
    return issues
