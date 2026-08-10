from __future__ import annotations

import importlib.util
import sys


REQUIRED_PACKAGES = {
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "xlrd": "xlrd",
    "pyreadstat": "pyreadstat",
    "PyYAML": "yaml",
}


def runtime_environment() -> dict[str, object]:
    missing = [name for name, module in REQUIRED_PACKAGES.items() if importlib.util.find_spec(module) is None]
    python_ready = sys.version_info >= (3, 10)
    return {
        "Python命令": sys.executable,
        "Python版本": ".".join(map(str, sys.version_info[:3])),
        "Python版本符合要求": python_ready,
        "缺少的Python依赖": missing,
        "Python环境状态": "就绪" if python_ready and not missing else "待配置",
        "配置说明": (
            "Python 3.10+ 与数据读取依赖均已就绪。"
            if python_ready and not missing
            else "请在用户明确同意后运行 bootstrap --yes；它只配置本 Skill 的独立 Python 环境。"
        ),
    }
