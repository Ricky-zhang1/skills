from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MplusEnvironment:
    os_name: str
    arch: str
    command: str | None
    version: str | None
    source: str | None
    compatibility: str
    compatibility_note: str
    executable_arch: str | None = None
    execution_note: str | None = None
    version_profile: str = "unknown"
    version_profile_note: str = "未能选择版本适配配置。"


def _version_tuple(version: str | None) -> tuple[int, int] | None:
    if not version:
        return None
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?", version.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def version_profile(version: str | None) -> tuple[str, str]:
    """Return the parser and language profile used by this runtime."""
    parsed = _version_tuple(version)
    if parsed is None:
        return "unknown", "未能读取版本；使用宽松输出解析，不启用任何版本专属语法。"
    major, minor = parsed
    if major == 9 and minor >= 1:
        return "v9.1", "核心模板使用 7+ 通用语法；ESEM 新并列输出、SEFA/DSEFA 与新版不变性输出仅在引导模块启用。"
    if major == 9:
        return "v9.0", "核心模板使用 7+ 通用语法；多步 mixture、两层 Bootstrap、PSEM 等 9.x 新功能保持引导或专家模式。"
    if major == 8 and minor >= 9:
        return "v8.9-8.11", "核心模板使用 7+ 通用语法；自动纵向不变性、PSEM、扩展 ALIGNMENT/DSEM 输出不自动写入标准模型。"
    if major == 8 and minor >= 4:
        return "v8.4-8.8", "核心模板使用 7+ 通用语法；多潜类别变量的 mixture 输出与早期版本不同，当前单一 LPA/LCA 解析不依赖该布局。"
    if major == 8:
        return "v8.0-8.3", "核心模板使用 7+ 通用语法；RDSEM、时间序列等新增功能保持专家模式。"
    if major == 7:
        return "v7", "采用旧版兼容输出解析；不依赖部分 7.x 安装中缺失的分类 logits 或后续版本新增输出。"
    if major <= 6:
        return "legacy", "Mplus 6.x 及更早版本不在标准模式支持范围。"
    return "future", "该版本高于当前适配表；保留通用核心语法，不启用版本专属功能。"


def _compatibility(version: str | None, os_name: str, arch: str) -> tuple[str, str]:
    if not version:
        return "unverified", "无法读取 Mplus 版本；标准模式不得据此宣称版本兼容。"
    parsed = _version_tuple(version)
    if parsed is None:
        return "unverified", "Mplus 版本格式无法识别；标准模式不得据此宣称版本兼容。"
    major, minor = parsed
    normalized_arch = arch.lower()
    if (major, minor) == (8, 3) and os_name == "Darwin" and normalized_arch in {"arm64", "aarch64"}:
        return "target", "Mac M 系列 + Mplus 8.3 是当前已完成真实运行回归的目标组合；每台用户电脑仍需通过本机自检。"
    if major in {8, 9} and os_name == "Darwin" and normalized_arch in {"arm64", "aarch64"}:
        return (
            "provisional",
            f"Mac M 系列是主要适配方向，但当前只有 Mplus 8.3 的真实运行记录；"
            f"Mplus {version} 必须通过本机自检，且仍标记为未实机认证。",
        )
    if major in {8, 9} and os_name in {"Darwin", "Windows"}:
        return "provisional", "已保留安装、路径和构建兼容，但当前没有该系统组合的真实 Mplus 验证记录。"
    if major == 7:
        return "best-effort", "Mplus 7.x 仅属兼容模式，输出格式尚未完成实机认证。"
    if major <= 6:
        return "unsupported", "Mplus 6.x 及更早版本不在标准模式支持范围。"
    return "unverified", f"Mplus {version} 高于当前验证矩阵，需先完成兼容性验证。"


def _detect_binary_arch(command: str, os_name: str) -> str | None:
    if os_name != "Darwin":
        return None
    try:
        p = subprocess.run(
            ["/usr/bin/file", "-b", command], capture_output=True, text=True,
            timeout=5, errors="ignore",
        )
        text = (p.stdout or "").lower()
    except Exception:  # noqa: BLE001
        return None
    has_arm = "arm64" in text
    has_x86 = "x86_64" in text
    if has_arm and has_x86:
        return "universal2"
    if has_arm:
        return "arm64"
    if has_x86:
        return "x86_64"
    return None


def _execution_note(os_name: str, host_arch: str, executable_arch: str | None, version: str | None) -> str | None:
    if os_name != "Darwin" or not executable_arch:
        return None
    host = host_arch.lower()
    if host in {"arm64", "aarch64"} and executable_arch == "x86_64":
        if version:
            return "Mplus 为 Intel x86_64 程序，已在当前 Mac 上成功执行并读取版本；实际通过 Rosetta 转译运行。"
        return "Mplus 为 Intel x86_64 程序；当前未能确认 Rosetta 调用成功。"
    if host in {"arm64", "aarch64"} and executable_arch in {"arm64", "universal2"}:
        return "Mplus 可在当前 Apple Silicon 环境原生运行。"
    return f"Mplus 程序架构为 {executable_arch}。"


def _candidate_paths() -> list[str]:
    candidates: list[str] = []
    env_cmd = os.getenv("MPLUS_COMMAND")
    if env_cmd:
        candidates.append(env_cmd)

    for name in ["mplus", "Mplus", "mplus.exe", "Mplus.exe", "mpdemo", "mpdemo.exe"]:
        found = shutil.which(name)
        if found:
            candidates.append(found)

    if os.name == "nt":
        pf = [os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)")]
        for base in filter(None, pf):
            candidates += glob.glob(str(Path(base) / "Mplus*" / "Mplus*.exe"))
            candidates += glob.glob(str(Path(base) / "Mplus*" / "mplus*.exe"))
    elif platform.system() == "Darwin":
        candidates += [
            "/Applications/Mplus/Mplus",
            "/Applications/Mplus/mplus",
            "/Applications/mplus/Mplus",
            "/Applications/mplus/mplus",
        ]
        candidates += glob.glob("/Applications/[Mm]plus*/[Mm]plus")
        candidates += glob.glob("/Applications/Mplus*.app/Contents/MacOS/*plus*")
        candidates += glob.glob(str(Path.home() / "Applications/Mplus*.app/Contents/MacOS/*plus*"))

    # 去重并保序
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        c = str(Path(c).expanduser())
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _detect_version(command: str) -> str | None:
    attempts = [[command, "-version"], [command, "--version"]]
    for args in attempts:
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=10, errors="ignore")
            text = (p.stdout or "") + "\n" + (p.stderr or "")
            m = re.search(r"Mplus\s+(?:VERSION|Version)?\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            pass
    m = re.search(r"Mplus\s*([0-9]+(?:\.[0-9]+)?)", Path(command).name, re.I)
    return m.group(1) if m else None


def detect_mplus(explicit: str | None = None) -> MplusEnvironment:
    os_name = platform.system()
    arch = platform.machine()
    if explicit:
        supplied = Path(explicit).expanduser()
        if supplied.is_dir():
            candidates = [str(supplied / name) for name in ["mplus", "Mplus", "mplus.exe", "Mplus.exe"]]
        else:
            candidates = [explicit]
    else:
        candidates = _candidate_paths()
    for c in candidates:
        if not c:
            continue
        p = Path(c).expanduser()
        # which得到的命令可能无需Path.exists；普通绝对路径则检查存在。
        if p.is_absolute() and not p.exists():
            continue
        if p.exists() and (not p.is_file() or (os.name != "nt" and not os.access(p, os.X_OK))):
            continue
        version = _detect_version(str(c))
        if version is None and explicit and Path(explicit).expanduser().is_dir():
            continue
        if version is None and not explicit:
            continue
        status, note = _compatibility(version, os_name, arch)
        executable_arch = _detect_binary_arch(str(c), os_name)
        execution_note = _execution_note(os_name, arch, executable_arch, version)
        source = "explicit-directory" if explicit and Path(explicit).expanduser().is_dir() else ("explicit" if explicit else "auto")
        profile, profile_note = version_profile(version)
        return MplusEnvironment(
            os_name, arch, str(c), version, source, status, note, executable_arch, execution_note,
            profile, profile_note,
        )
    hint = "已检查环境变量、系统 PATH 和常见安装目录。只有自动检测失败时，才需要用户选择 Mplus 安装目录或可执行文件。"
    return MplusEnvironment(os_name, arch, None, None, None, "not-found", hint, None, None)
