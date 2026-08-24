"""Build the offline industry-strength dashboard from official CLI snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

import duckdb
from jinja2 import Environment, PackageLoader, select_autoescape


@dataclass(frozen=True)
class IndustryBuildResult:
    report_path: Path
    manifest_path: Path
    as_of: date
    industries: int


def _read_items(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not envelope.get("ok"):
        raise ValueError(f"unsuccessful response envelope: {path}")
    data = envelope.get("data")
    items = data.get("item") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"response does not contain data.item: {path}")
    return items


def _history_path(history_dir: Path, thscode: str) -> Path:
    return history_dir / f"{thscode.replace('.', '_')}.json"


def _return(values: Sequence[float], sessions: int, end: Optional[int] = None) -> Optional[float]:
    end_index = len(values) - 1 if end is None else end
    start_index = end_index - sessions
    if start_index < 0 or end_index >= len(values):
        return None
    start = values[start_index]
    return values[end_index] / start - 1.0 if start else None


def _rs(
    industry: Sequence[float],
    benchmark: Sequence[float],
    sessions: int,
    end: Optional[int] = None,
) -> Optional[float]:
    industry_return = _return(industry, sessions, end)
    benchmark_return = _return(benchmark, sessions, end)
    if industry_return is None or benchmark_return is None:
        return None
    return (industry_return - benchmark_return) * 100.0


def _stock_snapshot(database_path: Path) -> Tuple[date, Dict[str, Dict[str, Any]]]:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        latest_date = connection.execute("SELECT MAX(trade_date) FROM raw_prices").fetchone()[0]
        rows = connection.execute(
            """
            WITH lagged AS (
                SELECT
                    thscode,
                    name,
                    trade_date,
                    close,
                    amount_billion,
                    LAG(close) OVER (
                        PARTITION BY thscode ORDER BY trade_date
                    ) AS previous_close
                FROM raw_prices
            )
            SELECT thscode, name, close, previous_close, amount_billion
            FROM lagged
            WHERE trade_date = ?
            """,
            [latest_date],
        ).fetchall()
    finally:
        connection.close()

    snapshot: Dict[str, Dict[str, Any]] = {}
    for thscode, name, close, previous_close, amount_billion in rows:
        change = (
            (float(close) / float(previous_close) - 1.0) * 100.0
            if previous_close not in (None, 0)
            else None
        )
        snapshot[thscode] = {
            "code": thscode,
            "name": name,
            "chg": change,
            "turnover": float(amount_billion) * 100_000_000.0,
        }
    return latest_date, snapshot


def _market_width(snapshot: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    changes = [row["chg"] for row in snapshot.values() if row["chg"] is not None]
    up = sum(value > 1e-12 for value in changes)
    down = sum(value < -1e-12 for value in changes)
    flat = len(changes) - up - down
    return {
        "up": up,
        "down": down,
        "flat": flat,
        "total": len(changes),
        "up_ratio": up / len(changes) if changes else None,
        "turnover": sum(row["turnover"] for row in snapshot.values()),
    }


def build_industry_payload(
    catalog_path: Path,
    history_dir: Path,
    constituents_dir: Path,
    database_path: Path,
    benchmark_code: str = "000300.SH",
) -> Dict[str, Any]:
    catalog = [
        item
        for item in _read_items(catalog_path)
        if str(item.get("thscode", "")).startswith("881")
    ]
    if not catalog:
        raise ValueError("industry catalog has no 881xxx.TI entries")

    latest_date, stock_snapshot = _stock_snapshot(database_path)
    benchmark_items = _read_items(_history_path(history_dir, benchmark_code))
    benchmark_by_date = {
        int(item["date_ms"]): float(item["close_price"])
        for item in benchmark_items
        if item.get("date_ms") is not None and item.get("close_price") is not None
    }
    latest_ms = int(
        datetime.combine(latest_date, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1000
    )
    benchmark_dates = sorted(date_ms for date_ms in benchmark_by_date if date_ms <= latest_ms)
    if len(benchmark_dates) < 81:
        raise ValueError("benchmark history must contain at least 81 aligned sessions")

    rows: List[Dict[str, Any]] = []
    common_heat_dates = benchmark_dates[-20:]
    for industry in catalog:
        code = industry["thscode"]
        history_items = _read_items(_history_path(history_dir, code))
        history_by_date = {
            int(item["date_ms"]): item
            for item in history_items
            if item.get("date_ms") is not None and int(item["date_ms"]) <= latest_ms
        }
        aligned_dates = [date_ms for date_ms in benchmark_dates if date_ms in history_by_date]
        industry_close = [float(history_by_date[date_ms]["close_price"]) for date_ms in aligned_dates]
        benchmark_close = [benchmark_by_date[date_ms] for date_ms in aligned_dates]
        turnovers = [float(history_by_date[date_ms].get("turnover") or 0.0) for date_ms in aligned_dates]
        if len(aligned_dates) < 81 or aligned_dates[-1] != benchmark_dates[-1]:
            raise ValueError(f"insufficient aligned history for {code}")

        heat: List[Optional[float]] = []
        aligned_position = {date_ms: index for index, date_ms in enumerate(aligned_dates)}
        for date_ms in common_heat_dates:
            index = aligned_position.get(date_ms)
            if index is None or index == 0:
                heat.append(None)
                continue
            industry_daily = industry_close[index] / industry_close[index - 1] - 1.0
            benchmark_daily = benchmark_close[index] / benchmark_close[index - 1] - 1.0
            heat.append((industry_daily - benchmark_daily) * 100.0)

        members = _read_items(_history_path(constituents_dir, code))
        constituent_rows = [
            stock_snapshot[item["thscode"]]
            for item in members
            if item.get("thscode") in stock_snapshot
        ]
        changes = [item["chg"] for item in constituent_rows if item["chg"] is not None]
        up = sum(value > 1e-12 for value in changes)
        down = sum(value < -1e-12 for value in changes)
        flat = len(changes) - up - down
        top_stocks = sorted(
            constituent_rows,
            key=lambda item: item["turnover"],
            reverse=True,
        )[:8]

        previous_turnover = turnovers[-21:-1]
        pulse = turnovers[-1] / mean(previous_turnover) if previous_turnover and mean(previous_turnover) else None
        row = {
            "code": code,
            "name": industry.get("name") or code,
            "rs5": _rs(industry_close, benchmark_close, 5),
            "rs20": _rs(industry_close, benchmark_close, 20),
            "rs60": _rs(industry_close, benchmark_close, 60),
            "rs20_old": _rs(
                industry_close,
                benchmark_close,
                20,
                len(industry_close) - 21,
            ),
            "pulse": pulse,
            "above_ma20": industry_close[-1] > mean(industry_close[-20:]),
            "idx_chg": _return(industry_close, 1) * 100.0,
            "heat": heat,
            "cons_n": len(constituent_rows),
            "catalog_cons_n": len(members),
            "up": up,
            "down": down,
            "flat": flat,
            "ew_proxy": mean(changes) if changes else None,
            "turn_sum": sum(item["turnover"] for item in constituent_rows),
            "top_stocks": top_stocks,
        }
        rows.append(row)

    ranked_now = sorted(rows, key=lambda item: item["rs20"], reverse=True)
    ranked_old = sorted(rows, key=lambda item: item["rs20_old"], reverse=True)
    old_ranks = {item["code"]: index + 1 for index, item in enumerate(ranked_old)}
    for index, row in enumerate(ranked_now):
        row["rank_now"] = index + 1
        row["rank_old"] = old_ranks[row["code"]]
        row["rank_chg"] = row["rank_old"] - row["rank_now"]

    width = _market_width(stock_snapshot)
    strongest = ranked_now[0]
    pulse_high = max(rows, key=lambda item: item["pulse"] or 0.0)
    rank_gainer = max(rows, key=lambda item: item["rank_chg"])
    payload = {
        "as_of": latest_date.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "benchmark": benchmark_code,
        "trade_dates": len(benchmark_dates[-90:]),
        "heat_dates": [
            datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
            for value in common_heat_dates
        ],
        "width": width,
        "summary": {
            "industry_count": len(rows),
            "rs20_positive": sum(item["rs20"] > 0 for item in rows),
            "above_ma20": sum(item["above_ma20"] for item in rows),
            "strongest": {"name": strongest["name"], "value": strongest["rs20"]},
            "pulse_high": {"name": pulse_high["name"], "value": pulse_high["pulse"]},
            "rank_gainer": {"name": rank_gainer["name"], "value": rank_gainer["rank_chg"]},
        },
        "industries": ranked_now,
    }
    return payload


def build_industry_report(
    output_path: Path,
    catalog_path: Path,
    history_dir: Path,
    constituents_dir: Path,
    database_path: Path,
    benchmark_code: str = "000300.SH",
) -> IndustryBuildResult:
    payload = build_industry_payload(
        catalog_path=catalog_path,
        history_dir=history_dir,
        constituents_dir=constituents_dir,
        database_path=database_path,
        benchmark_code=benchmark_code,
    )
    environment = Environment(
        loader=PackageLoader("market_momentum", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    report_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    html = environment.get_template("industry.html.j2").render(
        report_json=report_json,
        as_of=payload["as_of"],
    )
    if "NaN" in html or "undefined" in html:
        raise RuntimeError("industry report contains a non-serializable numeric marker")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(html, encoding="utf-8")
    temporary_path.replace(output_path)

    manifest_path = output_path.parent / "industry_manifest.json"
    manifest = {
        "status": "success",
        "source": "hithink-finance-index-and-marketdb",
        "as_of": payload["as_of"],
        "generated_at": payload["generated_at"],
        "industries": len(payload["industries"]),
        "benchmark": benchmark_code,
        "report": str(output_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return IndustryBuildResult(
        report_path=output_path,
        manifest_path=manifest_path,
        as_of=date.fromisoformat(payload["as_of"]),
        industries=len(payload["industries"]),
    )
