"""Deterministic demo market data for the local MVP."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List


@dataclass(frozen=True)
class PriceRow:
    thscode: str
    name: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    amount_billion: float

    def as_tuple(self) -> tuple:
        return (
            self.thscode,
            self.name,
            self.trade_date,
            self.open,
            self.high,
            self.low,
            self.close,
            self.amount_billion,
        )


def latest_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def trading_days(end: date, count: int) -> List[date]:
    """Return a weekday-only calendar suitable for deterministic local demos."""
    days: List[date] = []
    cursor = latest_weekday(end)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def generate_demo_prices(
    as_of: date,
    symbols: int = 240,
    sessions: int = 120,
    seed: int = 20260821,
) -> Iterable[PriceRow]:
    """Generate reproducible OHLC and turnover data with varied market regimes."""
    if symbols < 1:
        raise ValueError("symbols must be at least 1")
    if sessions < 60:
        raise ValueError("sessions must be at least 60")

    rng = random.Random(seed)
    days = trading_days(as_of, sessions)
    rows: List[PriceRow] = []

    for index in range(symbols):
        market = "SH" if index % 2 == 0 else "SZ"
        code = (600000 + index) if market == "SH" else (1 + index)
        thscode = f"{code:06d}.{market}"
        name = f"示例股票{index + 1:04d}"
        price = rng.uniform(4.0, 80.0)

        regime = index % 5
        base_drift = {0: 0.0020, 1: 0.0008, 2: 0.0, 3: -0.0008, 4: -0.0018}[regime]
        base_amount = math.exp(rng.uniform(math.log(0.25), math.log(65.0)))

        for position, trade_day in enumerate(days):
            cycle = math.sin((position + index % 17) / 11.0) * 0.0015
            shock = rng.gauss(0.0, 0.018)
            previous_close = price
            close = max(0.8, previous_close * (1.0 + base_drift + cycle + shock))
            open_price = max(0.8, previous_close * (1.0 + rng.gauss(0.0, 0.007)))
            intraday = abs(rng.gauss(0.012, 0.006))
            high = max(open_price, close) * (1.0 + intraday)
            low = min(open_price, close) * max(0.2, 1.0 - intraday)
            amount = max(0.01, base_amount * (1.0 + rng.gauss(0.0, 0.24)))

            rows.append(
                PriceRow(
                    thscode=thscode,
                    name=name,
                    trade_date=trade_day,
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close, 4),
                    amount_billion=round(amount, 4),
                )
            )
            price = close

    return rows
