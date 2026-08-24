"""Local end-to-end report pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .database import (
    apply_symbol_catalog,
    calculate_indicators,
    connect,
    latest_snapshot,
    load_marketdb_prices,
    load_prices,
    market_width,
    max_trade_date,
)
from .demo import generate_demo_prices, latest_weekday
from .report import build_payload, render_report, write_report_atomic
from .validation import validate_prices


@dataclass(frozen=True)
class BuildResult:
    report_path: Path
    database_path: Path
    manifest_path: Path
    as_of: date
    symbols: int


def build_local_report(
    output_path: Path,
    database_path: Path,
    as_of: date,
    symbols: int = 5000,
    sessions: int = 120,
    seed: int = 20260821,
) -> BuildResult:
    target_date = latest_weekday(as_of)
    rows = list(
        generate_demo_prices(
            as_of=target_date,
            symbols=symbols,
            sessions=sessions,
            seed=seed,
        )
    )

    connection = connect(database_path)
    try:
        load_prices(connection, rows)
        checks = validate_prices(connection, target_date)
        calculate_indicators(connection)
        snapshot = latest_snapshot(connection)
        width = market_width(connection)
    finally:
        connection.close()

    payload = build_payload(
        snapshot,
        width,
        checks,
        metadata={
            "source": "确定性模拟行情",
            "price_basis": "模拟价格（无公司行为）",
            "sessions": sessions,
            "is_demo": True,
        },
    )
    html = render_report(payload)
    if "NaN" in html or "undefined" in html:
        raise RuntimeError("report contains a non-serializable numeric marker")
    write_report_atomic(html, output_path)

    manifest: Dict[str, Any] = {
        "status": "success",
        "source": "deterministic-demo",
        "as_of": target_date.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "symbols": len(snapshot),
        "sessions": sessions,
        "report": str(output_path),
        "database": str(database_path),
        "checks": payload["checks"],
    }
    manifest_path = output_path.parent / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return BuildResult(
        report_path=output_path,
        database_path=database_path,
        manifest_path=manifest_path,
        as_of=target_date,
        symbols=len(snapshot),
    )


def build_marketdb_report(
    output_path: Path,
    database_path: Path,
    source_database: Path,
    symbol_catalog: Optional[Path] = None,
    sessions: int = 120,
) -> BuildResult:
    connection = connect(database_path)
    try:
        load_marketdb_prices(connection, source_database=source_database, sessions=sessions)
        named_symbols = (
            apply_symbol_catalog(connection, symbol_catalog) if symbol_catalog is not None else 0
        )
        target_date = max_trade_date(connection)
        checks = validate_prices(connection, target_date)
        calculate_indicators(connection)
        snapshot = latest_snapshot(connection)
        width = market_width(connection)
    finally:
        connection.close()

    payload = build_payload(
        snapshot,
        width,
        checks,
        metadata={
            "source": "同花顺金融数据 marketdb",
            "price_basis": "前复权",
            "sessions": sessions,
            "is_demo": False,
            "named_symbols": named_symbols,
        },
    )
    html = render_report(payload)
    if "NaN" in html or "undefined" in html:
        raise RuntimeError("report contains a non-serializable numeric marker")
    write_report_atomic(html, output_path)

    manifest: Dict[str, Any] = {
        "status": "success",
        "source": "marketdb-v_daily_qfq",
        "source_database": str(source_database),
        "symbol_catalog": str(symbol_catalog) if symbol_catalog is not None else None,
        "named_symbols": named_symbols,
        "as_of": target_date.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "symbols": len(snapshot),
        "sessions": sessions,
        "report": str(output_path),
        "database": str(database_path),
        "checks": payload["checks"],
    }
    manifest_path = output_path.parent / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return BuildResult(
        report_path=output_path,
        database_path=database_path,
        manifest_path=manifest_path,
        as_of=target_date,
        symbols=len(snapshot),
    )
