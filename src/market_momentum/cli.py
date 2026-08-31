"""Command-line interface for the local MVP."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from .industry import build_industry_report
from .pipeline import build_local_report, build_marketdb_report
from .publishing import publish_report_bundle
from .server import serve_reports
from .service_manager import install_service, service_status, uninstall_service


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("日期必须使用 YYYY-MM-DD 格式") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="market-momentum",
        description="生成本地 A 股市场趋势与动量筛选报告",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="使用确定性模拟数据生成离线报告")
    build.add_argument("--as-of", type=parse_date, default=date.today())
    build.add_argument("--symbols", type=int, default=5000)
    build.add_argument("--sessions", type=int, default=120)
    build.add_argument("--seed", type=int, default=20260821)
    build.add_argument("--output", type=Path, default=Path("output/latest.html"))
    build.add_argument("--database", type=Path, default=Path("runtime/market.duckdb"))

    marketdb = subparsers.add_parser(
        "build-marketdb",
        help="从官方 marketdb 的前复权视图生成全市场报告",
    )
    marketdb.add_argument("--source-database", type=Path, required=True)
    marketdb.add_argument(
        "--symbol-catalog",
        type=Path,
        help="官方 symbol list 命令输出的 JSON，用于补齐证券名称",
    )
    marketdb.add_argument(
        "--market-snapshot",
        type=Path,
        help="官方 market snapshot 输出的 JSON，用于在 marketdb 发布滞后时衔接最新收盘日",
    )
    marketdb.add_argument(
        "--snapshot-date",
        type=parse_date,
        help="收盘快照对应交易日（YYYY-MM-DD，需与 --market-snapshot 同时提供）",
    )
    marketdb.add_argument("--sessions", type=int, default=120)
    marketdb.add_argument("--output", type=Path, default=Path("output/latest.html"))
    marketdb.add_argument("--database", type=Path, default=Path("runtime/report.duckdb"))

    industry = subparsers.add_parser(
        "build-industry",
        help="从官方行业指数快照和本地 marketdb 生成行业强度报告",
    )
    industry.add_argument("--catalog", type=Path, required=True)
    industry.add_argument("--history-dir", type=Path, required=True)
    industry.add_argument("--constituents-dir", type=Path, required=True)
    industry.add_argument("--database", type=Path, required=True)
    industry.add_argument("--benchmark", default="000300.SH")
    industry.add_argument("--output", type=Path, default=Path("output/industry.html"))

    publish = subparsers.add_parser(
        "publish",
        help="原子发布一组已生成的报告并清理旧 HTML",
    )
    publish.add_argument("--staging-dir", type=Path, required=True)
    publish.add_argument("--output-dir", type=Path, default=Path("output"))

    serve = subparsers.add_parser(
        "serve",
        help="启动仅限本机的报告服务，并启用页面一键刷新",
    )
    serve.add_argument("--project-dir", type=Path, default=Path.cwd())
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    service = subparsers.add_parser(
        "service",
        help="安装、检查或卸载无需终端常驻的 macOS 本地服务",
    )
    service_commands = service.add_subparsers(dest="service_command", required=True)
    service_install = service_commands.add_parser("install", help="安装并启动 LaunchAgent")
    service_install.add_argument("--project-dir", type=Path, default=Path.cwd())
    service_install.add_argument("--host", default="127.0.0.1")
    service_install.add_argument("--port", type=int, default=8765)
    service_commands.add_parser("status", help="检查 LaunchAgent 状态")
    service_commands.add_parser("uninstall", help="停止并移除 LaunchAgent")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        if args.symbols < 1:
            raise SystemExit("--symbols 必须大于 0")
        if args.sessions < 60:
            raise SystemExit("--sessions 必须至少为 60")
        result = build_local_report(
            output_path=args.output,
            database_path=args.database,
            as_of=args.as_of,
            symbols=args.symbols,
            sessions=args.sessions,
            seed=args.seed,
        )
        print(f"报告已生成: {result.report_path.resolve()}")
        print(f"数据日期: {result.as_of.isoformat()} | 股票数: {result.symbols}")
        print(f"运行清单: {result.manifest_path.resolve()}")
        return 0
    if args.command == "build-marketdb":
        if args.sessions < 60:
            raise SystemExit("--sessions 必须至少为 60")
        result = build_marketdb_report(
            output_path=args.output,
            database_path=args.database,
            source_database=args.source_database,
            symbol_catalog=args.symbol_catalog,
            market_snapshot=args.market_snapshot,
            snapshot_date=args.snapshot_date,
            sessions=args.sessions,
        )
        print(f"报告已生成: {result.report_path.resolve()}")
        print(f"数据日期: {result.as_of.isoformat()} | 股票数: {result.symbols}")
        print(f"运行清单: {result.manifest_path.resolve()}")
        return 0
    if args.command == "build-industry":
        result = build_industry_report(
            output_path=args.output,
            catalog_path=args.catalog,
            history_dir=args.history_dir,
            constituents_dir=args.constituents_dir,
            database_path=args.database,
            benchmark_code=args.benchmark,
        )
        print(f"行业报告已生成: {result.report_path.resolve()}")
        print(f"数据日期: {result.as_of.isoformat()} | 行业数: {result.industries}")
        print(f"运行清单: {result.manifest_path.resolve()}")
        return 0
    if args.command == "publish":
        result = publish_report_bundle(
            staging_dir=args.staging_dir,
            output_dir=args.output_dir,
        )
        print(f"最新报告已发布: {result.output_dir}")
        print(f"数据日期: {result.as_of} | 已清理旧 HTML: {len(result.removed_html)}")
        return 0
    if args.command == "serve":
        serve_reports(
            project_dir=args.project_dir,
            host=args.host,
            port=args.port,
        )
        return 0
    if args.command == "service":
        if args.service_command == "install":
            plist_path = install_service(
                project_dir=args.project_dir,
                host=args.host,
                port=args.port,
            )
            print(f"本地守护服务已安装: {plist_path}")
            print(f"服务地址: http://{args.host}:{args.port}/latest.html")
            return 0
        if args.service_command == "status":
            status = service_status()
            print(f"LaunchAgent: {status['label']}")
            print(f"状态: {'运行中' if status['loaded'] else '未安装或未运行'}")
            print(f"配置: {status['plist']}")
            return 0 if status["loaded"] else 1
        if args.service_command == "uninstall":
            plist_path = uninstall_service()
            print(f"本地守护服务已移除: {plist_path}")
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
