from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

from . import __version__
from .mplus_detect import MplusEnvironment
from .utils import sha256_file


def runtime_fingerprint() -> str:
    if getattr(sys, "frozen", False):
        return sha256_file(Path(sys.executable).resolve())
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent
    files = sorted(package.rglob("*.py"))
    template_root = package.parents[1] / "assets" / "templates"
    if template_root.exists():
        files.extend(sorted(template_root.rglob("*.tmpl")))
    for path in files:
        digest.update(str(path.relative_to(package.parents[1])).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_self_test_receipt(receipt_path: str | Path | None, env: MplusEnvironment) -> dict[str, str]:
    if not receipt_path:
        raise RuntimeError("标准模式需要本机自检凭证。请先运行 self-test，再通过 --self-test-receipt 指定生成的凭证。")
    path = Path(receipt_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到本机自检凭证：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "凭证类型": "mplusflow-self-test",
        "自检状态": "通过",
        "Runtime版本": __version__,
        "操作系统": env.os_name,
        "架构": env.arch,
        "Mplus版本": env.version,
        "Mplus程序架构": env.executable_arch,
        "Runtime指纹SHA256": runtime_fingerprint(),
    }
    mismatches = [f"{key}: 凭证={data.get(key)!r}, 当前={value!r}" for key, value in expected.items() if data.get(key) != value]
    manifest = path.parent / ".mplus_runtime" / "manifest.json"
    if not manifest.is_file():
        mismatches.append("凭证旁缺少 .mplus_runtime/manifest.json")
    elif data.get("manifest_sha256") != sha256_file(manifest):
        mismatches.append("自检 manifest 的 SHA256 与凭证不一致")
    if mismatches:
        raise RuntimeError("本机自检凭证与当前环境不匹配：" + "；".join(mismatches))
    return {str(k): str(v) for k, v in data.items()}


def resolve_environment_validation(
    receipt_path: str | Path | None,
    env: MplusEnvironment,
    allow_provisional: bool = False,
) -> dict[str, str]:
    """Prefer a verified local environment, with an explicit pilot-run escape hatch."""
    try:
        receipt = validate_self_test_receipt(receipt_path, env)
        return {**receipt, "验证状态": "已通过本机自检"}
    except (FileNotFoundError, RuntimeError, json.JSONDecodeError) as exc:
        if not allow_provisional:
            raise
        return {
            "验证状态": "试运行（未完成本机自检）",
            "自检问题": str(exc),
            "说明": "Mplus 已被调用，但未通过本 Skill 的端到端自检；本次结果可用于检查代码和数据，正式研究结论前应完成自检并复跑。",
        }
