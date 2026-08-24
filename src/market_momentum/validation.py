"""Data-quality checks for local and production pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List

import duckdb


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


class DataValidationError(RuntimeError):
    pass


MIN_LATEST_SYMBOL_COVERAGE = 0.98


def validate_prices(
    connection: duckdb.DuckDBPyConnection,
    expected_date: date,
) -> List[CheckResult]:
    checks: List[CheckResult] = []

    latest = connection.execute("SELECT MAX(trade_date) FROM raw_prices").fetchone()[0]
    checks.append(CheckResult("latest_trade_date", latest == expected_date, str(latest)))

    duplicates = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT thscode, trade_date
            FROM raw_prices
            GROUP BY thscode, trade_date
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    checks.append(CheckResult("unique_symbol_date", duplicates == 0, f"duplicates={duplicates}"))

    invalid_ohlc = connection.execute(
        """
        SELECT COUNT(*) FROM raw_prices
        WHERE high < GREATEST(open, close)
           OR low > LEAST(open, close)
           OR low <= 0
           OR open <= 0
           OR high <= 0
           OR close <= 0
        """
    ).fetchone()[0]
    checks.append(CheckResult("valid_ohlc", invalid_ohlc == 0, f"invalid_rows={invalid_ohlc}"))

    negative_amount = connection.execute(
        "SELECT COUNT(*) FROM raw_prices WHERE amount_billion < 0"
    ).fetchone()[0]
    checks.append(
        CheckResult("non_negative_amount", negative_amount == 0, f"invalid_rows={negative_amount}")
    )

    latest_coverage = connection.execute(
        "SELECT COUNT(DISTINCT thscode) FROM raw_prices WHERE trade_date = ?",
        [expected_date],
    ).fetchone()[0]
    total_symbols = connection.execute(
        "SELECT COUNT(DISTINCT thscode) FROM raw_prices"
    ).fetchone()[0]
    coverage_ratio = latest_coverage / total_symbols if total_symbols else 0.0
    checks.append(
        CheckResult(
            "latest_symbol_coverage",
            coverage_ratio >= MIN_LATEST_SYMBOL_COVERAGE,
            (
                f"latest={latest_coverage}, total={total_symbols}, "
                f"coverage={coverage_ratio:.2%}, required={MIN_LATEST_SYMBOL_COVERAGE:.0%}"
            ),
        )
    )

    failures = [check for check in checks if not check.passed]
    if failures:
        detail = "; ".join(f"{check.name}: {check.detail}" for check in failures)
        raise DataValidationError(detail)
    return checks
