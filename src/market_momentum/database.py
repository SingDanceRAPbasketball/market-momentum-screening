"""DuckDB storage and indicator calculations."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Iterable, List

import duckdb

from .demo import PriceRow


def connect(database_path: Path) -> duckdb.DuckDBPyConnection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(database_path))


def load_prices(connection: duckdb.DuckDBPyConnection, rows: Iterable[PriceRow]) -> None:
    connection.execute("DROP TABLE IF EXISTS raw_prices")
    connection.execute(
        """
        CREATE TABLE raw_prices (
            thscode VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            trade_date DATE NOT NULL,
            open DOUBLE NOT NULL,
            high DOUBLE NOT NULL,
            low DOUBLE NOT NULL,
            close DOUBLE NOT NULL,
            amount_billion DOUBLE NOT NULL
        )
        """
    )
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".csv") as handle:
        writer = csv.writer(handle)
        writer.writerows(row.as_tuple() for row in rows)
        handle.flush()
        escaped_path = handle.name.replace("'", "''")
        connection.execute(
            f"COPY raw_prices FROM '{escaped_path}' (FORMAT CSV, HEADER FALSE)"
        )


def load_marketdb_prices(
    connection: duckdb.DuckDBPyConnection,
    source_database: Path,
    sessions: int = 120,
) -> None:
    """Load the latest forward-adjusted panel from the official marketdb schema."""
    if not source_database.exists():
        raise FileNotFoundError(f"marketdb does not exist: {source_database}")
    if sessions < 60:
        raise ValueError("sessions must be at least 60")

    escaped_path = str(source_database.resolve()).replace("'", "''")
    connection.execute(f"ATTACH '{escaped_path}' AS source_marketdb (READ_ONLY)")
    try:
        connection.execute("DROP TABLE IF EXISTS raw_prices")
        connection.execute(
            """
            CREATE TABLE raw_prices AS
            WITH recent_dates AS (
                SELECT DISTINCT date AS trade_date
                FROM source_marketdb.v_daily_qfq
                ORDER BY trade_date DESC
                LIMIT ?
            )
            SELECT
                prices.thscode,
                COALESCE(symbols.name, prices.thscode) AS name,
                prices.date AS trade_date,
                prices.open::DOUBLE AS open,
                prices.high::DOUBLE AS high,
                prices.low::DOUBLE AS low,
                prices.close::DOUBLE AS close,
                prices.amount::DOUBLE / 100000000.0 AS amount_billion
            FROM source_marketdb.v_daily_qfq AS prices
            INNER JOIN recent_dates ON recent_dates.trade_date = prices.date
            LEFT JOIN source_marketdb.v_symbol AS symbols USING (thscode)
            WHERE prices.open IS NOT NULL
              AND prices.high IS NOT NULL
              AND prices.low IS NOT NULL
              AND prices.close IS NOT NULL
              AND prices.amount IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY prices.thscode, prices.date
                ORDER BY prices.thscode
            ) = 1
            """,
            [sessions],
        )
    finally:
        connection.execute("DETACH source_marketdb")


def apply_symbol_catalog(
    connection: duckdb.DuckDBPyConnection,
    catalog_path: Path,
) -> int:
    """Fill stock names from an official ``symbol list`` JSON envelope."""
    if not catalog_path.exists():
        raise FileNotFoundError(f"symbol catalog does not exist: {catalog_path}")

    envelope = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not envelope.get("ok"):
        raise ValueError("symbol catalog response envelope is not successful")
    data = envelope.get("data")
    items = data.get("item") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("symbol catalog must contain data.item array")

    names = [
        (item.get("thscode"), item.get("name"))
        for item in items
        if isinstance(item, dict) and item.get("thscode") and item.get("name")
    ]
    connection.execute("DROP TABLE IF EXISTS symbol_names")
    connection.execute(
        "CREATE TEMP TABLE symbol_names (thscode VARCHAR PRIMARY KEY, name VARCHAR NOT NULL)"
    )
    connection.executemany("INSERT OR REPLACE INTO symbol_names VALUES (?, ?)", names)
    connection.execute(
        """
        UPDATE raw_prices
        SET name = symbol_names.name
        FROM symbol_names
        WHERE raw_prices.thscode = symbol_names.thscode
        """
    )
    return connection.execute(
        "SELECT COUNT(DISTINCT thscode) FROM raw_prices WHERE name <> thscode"
    ).fetchone()[0]


def calculate_indicators(connection: duckdb.DuckDBPyConnection) -> None:
    """Calculate with full precision; rounding is reserved for presentation."""
    connection.execute("DROP TABLE IF EXISTS indicators")
    connection.execute(
        """
        CREATE TABLE indicators AS
        WITH rolling AS (
            SELECT
                *,
                LAG(close, 1) OVER symbol_window AS previous_close,
                LAG(close, 20) OVER symbol_window AS close_20_sessions_ago,
                AVG(close) OVER (
                    PARTITION BY thscode ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS ma20_candidate,
                COUNT(close) OVER (
                    PARTITION BY thscode ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS observations_20,
                AVG(close) OVER (
                    PARTITION BY thscode ORDER BY trade_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) AS ma60_candidate,
                COUNT(close) OVER (
                    PARTITION BY thscode ORDER BY trade_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) AS observations_60,
                AVG(amount_billion) OVER (
                    PARTITION BY thscode ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS amount_20_candidate
            FROM raw_prices
            WINDOW symbol_window AS (PARTITION BY thscode ORDER BY trade_date)
        ), normalized AS (
            SELECT
                *,
                CASE WHEN observations_20 = 20 THEN ma20_candidate END AS ma20,
                CASE WHEN observations_60 = 60 THEN ma60_candidate END AS ma60,
                CASE WHEN observations_20 = 20 THEN amount_20_candidate END AS amount_avg_20,
                CASE
                    WHEN close_20_sessions_ago IS NOT NULL
                    THEN close / close_20_sessions_ago - 1.0
                END AS return_20d
            FROM rolling
        )
        SELECT
            thscode,
            name,
            trade_date,
            open,
            high,
            low,
            close,
            ma20,
            ma60,
            amount_billion,
            amount_avg_20,
            return_20d,
            CASE
                WHEN previous_close IS NOT NULL AND previous_close <> 0
                THEN close / previous_close - 1.0
            END AS return_1d,
            CASE
                WHEN ma20 IS NULL OR ma60 IS NULL THEN 'na'
                WHEN close > ma20 AND ma20 > ma60 THEN '强趋势'
                WHEN close > ma20 AND ma20 <= ma60 THEN '修复'
                WHEN close <= ma20 AND ma20 > ma60 THEN '回调'
                ELSE '弱趋势'
            END AS trend,
            CASE
                WHEN amount_avg_20 IS NULL THEN 'na'
                WHEN amount_avg_20 >= 20.0 THEN 'T1'
                WHEN amount_avg_20 >= 5.0 THEN 'T2'
                WHEN amount_avg_20 >= 1.0 THEN 'T3'
                ELSE 'T4'
            END AS liquidity
        FROM normalized
        """
    )


def rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> List[Dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def latest_snapshot(connection: duckdb.DuckDBPyConnection) -> List[Dict[str, Any]]:
    return rows_as_dicts(
        connection.execute(
            """
            SELECT * EXCLUDE (row_number)
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY thscode ORDER BY trade_date DESC
                ) AS row_number
                FROM indicators
            )
            WHERE row_number = 1
            ORDER BY thscode
            """
        )
    )


def market_width(connection: duckdb.DuckDBPyConnection) -> List[Dict[str, Any]]:
    return rows_as_dicts(
        connection.execute(
            """
            SELECT
                trade_date,
                COUNT(*) FILTER (WHERE ma20 IS NOT NULL) AS eligible_ma20,
                COUNT(*) FILTER (WHERE ma60 IS NOT NULL) AS eligible_ma60,
                COUNT(*) FILTER (WHERE ma20 IS NOT NULL AND close > ma20) AS above_ma20,
                COUNT(*) FILTER (WHERE ma60 IS NOT NULL AND close > ma60) AS above_ma60,
                CASE
                    WHEN COUNT(*) FILTER (WHERE ma20 IS NOT NULL) > 0
                    THEN COUNT(*) FILTER (WHERE ma20 IS NOT NULL AND close > ma20) * 1.0
                         / COUNT(*) FILTER (WHERE ma20 IS NOT NULL)
                END AS pct_above_ma20,
                CASE
                    WHEN COUNT(*) FILTER (WHERE ma60 IS NOT NULL) > 0
                    THEN COUNT(*) FILTER (WHERE ma60 IS NOT NULL AND close > ma60) * 1.0
                         / COUNT(*) FILTER (WHERE ma60 IS NOT NULL)
                END AS pct_above_ma60
            FROM indicators
            GROUP BY trade_date
            HAVING COUNT(*) FILTER (WHERE ma60 IS NOT NULL) > 0
            ORDER BY trade_date
            """
        )
    )


def max_trade_date(connection: duckdb.DuckDBPyConnection) -> date:
    return connection.execute("SELECT MAX(trade_date) FROM raw_prices").fetchone()[0]
