from datetime import date, timedelta
from pathlib import Path

from market_momentum.database import calculate_indicators, connect, latest_snapshot, load_prices
from market_momentum.demo import PriceRow
from market_momentum.report import _trend_matrix
from market_momentum.validation import validate_prices


def make_series(code: str, closes, amount: float):
    start = date(2026, 1, 1)
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            PriceRow(
                thscode=code,
                name=code,
                trade_date=start + timedelta(days=index),
                open=float(close),
                high=float(close) + 1.0,
                low=max(0.1, float(close) - 1.0),
                close=float(close),
                amount_billion=amount,
            )
        )
    return rows


def test_trend_and_liquidity_classification(tmp_path: Path):
    rows = []
    rows += make_series("STRONG.SH", range(1, 62), 25.0)
    rows += make_series("WEAK.SH", range(61, 0, -1), 10.0)
    rows += make_series("REPAIR.SH", list(range(100, 40, -1)) + [60], 2.0)
    rows += make_series("PULLBACK.SH", list(range(40, 100)) + [80], 0.5)
    expected_date = max(row.trade_date for row in rows)

    connection = connect(tmp_path / "test.duckdb")
    try:
        load_prices(connection, rows)
        validate_prices(connection, expected_date)
        calculate_indicators(connection)
        snapshot = {row["thscode"]: row for row in latest_snapshot(connection)}
    finally:
        connection.close()

    assert snapshot["STRONG.SH"]["trend"] == "强趋势"
    assert snapshot["WEAK.SH"]["trend"] == "弱趋势"
    assert snapshot["REPAIR.SH"]["trend"] == "修复"
    assert snapshot["PULLBACK.SH"]["trend"] == "回调"
    assert snapshot["STRONG.SH"]["liquidity"] == "T1"
    assert snapshot["WEAK.SH"]["liquidity"] == "T2"
    assert snapshot["REPAIR.SH"]["liquidity"] == "T3"
    assert snapshot["PULLBACK.SH"]["liquidity"] == "T4"

    expected_return = 61.0 / 41.0 - 1.0
    assert abs(snapshot["STRONG.SH"]["return_20d"] - expected_return) < 1e-12


def test_trend_matrix_keeps_new_stocks_without_return_history():
    matrix = _trend_matrix(
        [
            {"liquidity": "na", "trend": "na", "return_20d": None},
            {"liquidity": "na", "trend": "na", "return_20d": 0.1},
        ]
    )
    cell = next(
        item for item in matrix if item["liquidity"] == "na" and item["trend"] == "na"
    )
    assert cell["count"] == 2
    assert cell["median_return"] == 0.1
