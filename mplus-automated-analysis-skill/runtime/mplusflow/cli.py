from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .environment import runtime_environment
from .mplus_detect import detect_mplus
from .utils import parse_csv_list, parse_number_list


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mplusflow", description="Mplus智能分析 Runtime")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="检查 Mplus 与运行环境")
    doctor.add_argument("--mplus", default=None, help="Mplus可执行程序路径")

    selftest = sub.add_parser("self-test", help="在本机运行可复现的端到端安装自检")
    selftest.add_argument("--output", required=True, help="自检输出目录，必须为新目录")
    selftest.add_argument("--mplus", default=None, help="Mplus可执行程序路径")
    selftest.add_argument("--allow-untested-version", action="store_true", help="允许在 7.x 或未知版本进行专家自检")
    selftest.add_argument("--timeout", type=int, default=600, help="单个自检模型最长运行秒数")

    lpa = sub.add_parser("lpa", help="执行 LPA 1—K 类标准流水线")
    lpa.add_argument("--data", required=True, help="数据文件路径")
    lpa.add_argument("--indicators", required=True, help="剖面指标，逗号分隔")
    lpa.add_argument("--output", required=True, help="输出项目目录")
    lpa.add_argument("--id", default=None, help="用户ID变量，可选")
    lpa.add_argument("--missing", default=None, help="特殊缺失码，逗号分隔，例如 -999,-99")
    lpa.add_argument("--classes", default="1,2,3,4,5", help="类别范围，默认1,2,3,4,5")
    lpa.add_argument("--standardize", action="store_true", help="明确授权对指标做Z标准化；默认不标准化")
    lpa.add_argument("--confirm-low-cardinality", action="store_true", help="研究者确认将不超过10个唯一值的指标按连续变量处理")
    lpa.add_argument("--mplus", default=None, help="Mplus可执行程序路径")
    lpa.add_argument("--allow-untested-version", action="store_true", help="专家确认后允许在未认证版本运行；报告会保留版本警告")
    lpa.add_argument("--self-test-receipt", help="当前系统、Runtime和Mplus版本对应的本机自检凭证")
    lpa.add_argument("--provisional-environment", action="store_true", help="仅在用户明确同意时，未完成自检也以试运行方式执行；报告会标记不可直接作为正式结论")
    lpa.add_argument("--dry-run", action="store_true", help="只完成数据转换和代码生成，不调用Mplus")
    lpa.add_argument("--timeout", type=int, default=7200, help="单个模型最长运行秒数")
    lpa.add_argument("--text-columns", default=None, help="无表头TXT/DAT的列名，逗号分隔")

    po = sub.add_parser("parse-output", help="解析单个Mplus .out")
    po.add_argument("path")

    catalog_parser = sub.add_parser("catalog", help="列出面向普通用户的核心分析家族")
    catalog_parser.add_argument("--all", action="store_true", help="同时显示辅助和专家扩展能力")
    catalog_parser.add_argument("--mplus", default=None, help="可选：检测此 Mplus 后按版本显示适配说明")

    inspect = sub.add_parser("inspect-data", help="生成数据画像和分析家族提示，不运行模型")
    inspect.add_argument("--data", required=True, help="数据文件")
    inspect.add_argument("--output", required=True, help="新的数据画像目录")
    inspect.add_argument("--text-columns", default=None, help="无表头 TXT/DAT 的列名")

    run_spec = sub.add_parser("run-spec", help="根据结构化 JSON 设计执行非 LPA 标准分析")
    run_spec.add_argument("--spec", required=True, help="分析设计 JSON")
    run_spec.add_argument("--data", required=True, help="数据文件")
    run_spec.add_argument("--output", required=True, help="新的输出项目目录")
    run_spec.add_argument("--id", default=None, help="用户 ID 变量")
    run_spec.add_argument("--missing", default=None, help="特殊缺失码，逗号分隔")
    run_spec.add_argument("--mplus", default=None, help="Mplus 可执行程序路径")
    run_spec.add_argument("--self-test-receipt", default=None, help="当前环境的 Mplus 自检凭证")
    run_spec.add_argument("--provisional-environment", action="store_true", help="仅在用户明确同意时，未完成自检也以试运行方式执行；报告会标记不可直接作为正式结论")
    run_spec.add_argument("--dry-run", action="store_true", help="只生成数据和代码，不运行 Mplus")
    run_spec.add_argument("--timeout", type=int, default=7200)
    run_spec.add_argument("--text-columns", default=None, help="无表头 TXT/DAT 的列名")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            env = detect_mplus(args.mplus)
            runtime = runtime_environment()
            print(json.dumps({
                "操作系统": env.os_name,
                "架构": env.arch,
                "Mplus命令": env.command,
                "Mplus版本": env.version,
                "Mplus程序架构": env.executable_arch,
                "执行方式说明": env.execution_note,
                "版本兼容状态": env.compatibility,
                "版本说明": env.compatibility_note,
                "版本适配配置": env.version_profile,
                "版本适配说明": env.version_profile_note,
                **runtime,
                "状态": (
                    "环境就绪" if env.command and runtime["Python环境状态"] == "就绪"
                    else "环境待配置"
                ),
            }, ensure_ascii=False, indent=2))
            return 0 if env.command and runtime["Python环境状态"] == "就绪" else 2

        if args.command == "catalog":
            from .catalog import catalog
            env = detect_mplus(args.mplus) if args.mplus else None
            print(json.dumps(catalog(
                include_extensions=args.all,
                mplus_version=env.version if env and env.command else None,
            ), ensure_ascii=False, indent=2))
            return 0

        if args.command == "inspect-data":
            from .inspection import inspect_dataset
            result = inspect_dataset(
                args.data, args.output,
                parse_csv_list(args.text_columns) if args.text_columns else None,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "parse-output":
            from .parser import parse_mplus_output
            r = parse_mplus_output(args.path)
            print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "self-test":
            from .selftest import run_self_test
            result = run_self_test(
                output_dir=args.output,
                mplus_command=args.mplus,
                allow_untested_version=args.allow_untested_version,
                timeout_seconds=args.timeout,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "lpa":
            from .pipeline import run_lpa_pipeline
            classes = [int(x) for x in parse_csv_list(args.classes)]
            result = run_lpa_pipeline(
                input_path=args.data,
                indicators=parse_csv_list(args.indicators),
                output_dir=args.output,
                user_id=args.id,
                missing_codes=parse_number_list(args.missing),
                standardize=args.standardize,
                allow_low_cardinality=args.confirm_low_cardinality,
                classes=classes,
                mplus_command=args.mplus,
                allow_untested_version=args.allow_untested_version,
                self_test_receipt=args.self_test_receipt,
                provisional_environment=args.provisional_environment,
                dry_run=args.dry_run,
                timeout_seconds=args.timeout,
                text_columns=parse_csv_list(args.text_columns) if args.text_columns else None,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 3 if result.get("状态") == "完成_发现重大问题" else 0

        if args.command == "run-spec":
            from .standard_pipeline import load_design, run_standard_pipeline
            result = run_standard_pipeline(
                design=load_design(args.spec),
                input_path=args.data,
                output_dir=args.output,
                user_id=args.id,
                missing_codes=parse_number_list(args.missing),
                mplus_command=args.mplus,
                self_test_receipt=args.self_test_receipt,
                provisional_environment=args.provisional_environment,
                dry_run=args.dry_run,
                timeout_seconds=args.timeout,
                text_columns=parse_csv_list(args.text_columns) if args.text_columns else None,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 3 if result.get("状态") in {"完成_存在重大问题", "完成_无可用候选"} else 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"状态": "失败", "错误": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
