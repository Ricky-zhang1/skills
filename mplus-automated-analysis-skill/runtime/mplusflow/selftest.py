from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .pipeline import run_lpa_pipeline
from . import __version__
from .utils import sha256_file, write_json
from .validation import runtime_fingerprint


def run_self_test(
    output_dir: str | Path,
    mplus_command: str | None = None,
    allow_untested_version: bool = False,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run a small deterministic end-to-end test on the user's own installation."""
    output = Path(output_dir).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="mplusflow-selftest-") as td:
        rng = np.random.default_rng(20260810)
        latent_class = np.repeat([0, 1], 45)
        rng.shuffle(latent_class)
        data = pd.DataFrame(
            {
                "自检指标1": rng.normal(latent_class * 1.8, 0.55),
                "自检指标2": rng.normal(latent_class * 1.3, 0.60),
                "自检指标3": rng.normal((1 - latent_class) * 1.5, 0.50),
            }
        )
        data_path = Path(td) / "selftest.csv"
        data.to_csv(data_path, index=False, encoding="utf-8-sig")
        result = run_lpa_pipeline(
            input_path=data_path,
            indicators=["自检指标1", "自检指标2", "自检指标3"],
            output_dir=output,
            classes=[1, 2],
            mplus_command=mplus_command,
            allow_untested_version=allow_untested_version,
            self_test_mode=True,
            timeout_seconds=timeout_seconds,
        )

    manifest_path = output / ".mplus_runtime" / "manifest.json"
    required = [
        output / "05_分析结果" / "类别模型比较表.xlsx",
        output / "05_分析结果" / "个体类别归属.xlsx",
        output / "05_分析结果" / "个体类别归属.csv",
        output / "06_分析报告" / "LPA分析报告.md",
        manifest_path,
    ]
    missing = [str(path) for path in required if not path.exists()]
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    two_class = next((item for item in manifest_data.get("模型结果", []) if item.get("class_count") == 2), None)
    selftest_errors: list[str] = []
    if not two_class:
        selftest_errors.append("未找到2类模型结果")
    else:
        if two_class.get("best_ll_replicated") is not True:
            selftest_errors.append("2类模型最佳loglikelihood未确认重复")
        if two_class.get("tech11_p") is None:
            selftest_errors.append("未解析到TECH11")
        if two_class.get("tech14_p") is None or two_class.get("tech14_trustworthy") is not True:
            selftest_errors.append("未获得可信的TECH14")
        if not two_class.get("savedata_variables"):
            selftest_errors.append("未解析到SAVEDATA变量顺序")
    if missing or result.get("质量问题报告") or selftest_errors:
        raise RuntimeError(
            f"本机端到端自检未通过。缺失文件：{missing}；质量报告：{result.get('质量问题报告')}；"
            f"关键解析检查：{selftest_errors}"
        )
    result["自检状态"] = "通过"
    result["状态"] = "安装自检通过"
    result["说明"] = "该结果验证本机 Mplus 可调用、数据转换、模型执行和关键输出解析链路；自检使用小型 LPA 样例，不代表真实数据适合任何特定模型。"
    environment = json.loads((output / ".mplus_runtime" / "运行环境.json").read_text(encoding="utf-8"))
    receipt = {
        "凭证类型": "mplusflow-self-test",
        "自检状态": "通过",
        "Runtime版本": __version__,
        "操作系统": environment.get("操作系统"),
        "架构": environment.get("架构"),
        "Mplus版本": environment.get("Mplus版本"),
        "Mplus程序架构": environment.get("Mplus程序架构"),
        "Runtime指纹SHA256": runtime_fingerprint(),
        "生成时间UTC": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": sha256_file(manifest_path),
    }
    receipt_path = output / "Mplus自检凭证.json"
    write_json(receipt_path, receipt)
    result["自检凭证"] = str(receipt_path)
    return result
