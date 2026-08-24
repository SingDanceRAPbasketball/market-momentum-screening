"""Build a self-contained interactive HTML report."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List

from jinja2 import Environment, PackageLoader, select_autoescape

from .validation import CheckResult


def _json_ready(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _trend_matrix(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple, List[Any]] = {}
    for row in rows:
        key = (row["liquidity"], row["trend"])
        groups.setdefault(key, []).append(row["return_20d"])

    order_liquidity = ["T1", "T2", "T3", "T4", "na"]
    order_trend = ["强趋势", "修复", "回调", "弱趋势", "na"]
    matrix: List[Dict[str, Any]] = []
    for liquidity in order_liquidity:
        for trend in order_trend:
            values = groups.get((liquidity, trend), [])
            valid_values = [value for value in values if value is not None]
            matrix.append(
                {
                    "liquidity": liquidity,
                    "trend": trend,
                    "count": len(values),
                    "median_return": median(valid_values) if valid_values else None,
                }
            )
    return matrix


def build_payload(
    snapshot: List[Dict[str, Any]],
    width: List[Dict[str, Any]],
    checks: List[CheckResult],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    as_of = max(row["trade_date"] for row in snapshot)
    active = [row for row in snapshot if row["trade_date"] == as_of]
    valid_returns = [row["return_20d"] for row in active if row["return_20d"] is not None]
    valid_ma20 = [row for row in active if row["ma20"] is not None]
    valid_ma60 = [row for row in active if row["ma60"] is not None]
    day_returns = [row["return_1d"] for row in active if row["return_1d"] is not None]
    strong = [row for row in active if row["trend"] == "强趋势"]

    def percentile(values: List[float], fraction: float) -> Any:
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    payload = {
        "as_of": as_of,
        "generated_at": datetime.now().astimezone(),
        "metadata": metadata,
        "summary": {
            "symbols": len(active),
            "database_symbols": len(snapshot),
            "valid_return_20d": len(valid_returns),
            "new_lt20": len(active) - len(valid_returns),
            "up_today": sum(value > 1e-12 for value in day_returns),
            "down_today": sum(value < -1e-12 for value in day_returns),
            "flat_today": sum(abs(value) <= 1e-12 for value in day_returns),
            "pct_up_today": (
                sum(value > 1e-12 for value in day_returns) / len(day_returns)
                if day_returns
                else None
            ),
            "amount_today_billion": sum(row["amount_billion"] for row in active),
            "median_return_20d": median(valid_returns) if valid_returns else None,
            "mean_return_20d": mean(valid_returns) if valid_returns else None,
            "p10_return_20d": percentile(valid_returns, 0.10),
            "p90_return_20d": percentile(valid_returns, 0.90),
            "pct_positive_20d": (
                sum(value >= 0 for value in valid_returns) / len(valid_returns)
                if valid_returns
                else None
            ),
            "pct_above_ma20": (
                sum(row["close"] > row["ma20"] for row in valid_ma20) / len(valid_ma20)
                if valid_ma20
                else None
            ),
            "pct_above_ma60": (
                sum(row["close"] > row["ma60"] for row in valid_ma60) / len(valid_ma60)
                if valid_ma60
                else None
            ),
            "pct_strong_trend": len(strong) / len(active) if active else None,
            "strong_trend": len(strong),
        },
        "stocks": active,
        "market_width": width,
        "trend_matrix": _trend_matrix(active),
        "checks": [
            {"name": check.name, "passed": check.passed, "detail": check.detail}
            for check in checks
        ],
    }
    return _json_ready(payload)


def render_report(payload: Dict[str, Any]) -> str:
    environment = Environment(
        loader=PackageLoader("market_momentum", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template("report.html.j2")
    report_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    report_json = report_json.replace("</", "<\\/")
    return template.render(report_json=report_json, as_of=payload["as_of"])


def write_report_atomic(html: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(html, encoding="utf-8")
    temporary_path.replace(output_path)
