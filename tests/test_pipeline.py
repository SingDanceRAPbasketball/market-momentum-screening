import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from market_momentum.demo import latest_weekday, trading_days
from market_momentum.industry import build_industry_report
from market_momentum.pipeline import build_local_report, build_marketdb_report
from market_momentum.publishing import publish_report_bundle
from market_momentum.validation import validate_prices


def test_weekend_rolls_back_to_friday():
    assert latest_weekday(date(2026, 8, 23)) == date(2026, 8, 21)
    days = trading_days(date(2026, 8, 23), 5)
    assert days[-1] == date(2026, 8, 21)
    assert all(day.weekday() < 5 for day in days)


def test_end_to_end_report_is_self_contained(tmp_path: Path):
    report = tmp_path / "output" / "latest.html"
    database = tmp_path / "runtime" / "market.duckdb"
    result = build_local_report(
        output_path=report,
        database_path=database,
        as_of=date(2026, 8, 21),
        symbols=24,
        sessions=80,
        seed=42,
    )

    html = report.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.symbols == 24
    assert database.exists()
    assert "全市场趋势与动量筛选" in html
    assert "一键刷新" in html
    assert "重启服务" in html
    assert "/api/restart" in html
    assert "/api/health" in html
    assert "/api/auth/login" in html
    assert 'const REPORT_KEY = "latest"' in html
    assert "loadLatestReport(result.status)" in html
    assert "设置 API Key" in html
    assert 'class="market-table market-table-all"' in html
    assert '<th class="number">20日涨幅</th>' in html
    assert "prefers-color-scheme: light" in html
    assert 'class="head-line"' in html
    assert 'preserveAspectRatio: "xMidYMid meet"' in html
    assert "const robustExtent = values" in html
    assert "极端值贴边显示" in html
    assert "application/json" in html
    assert "NaN" not in html
    assert "undefined" not in html
    assert manifest["status"] == "success"
    assert all(check["passed"] for check in manifest["checks"])


def test_build_from_official_marketdb_views(tmp_path: Path):
    source = tmp_path / "source.duckdb"
    connection = duckdb.connect(str(source))
    connection.execute(
        """
        CREATE TABLE v_daily_qfq (
            thscode VARCHAR,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            amount DOUBLE
        )
        """
    )
    connection.execute(
        "CREATE TABLE v_symbol (thscode VARCHAR, name VARCHAR)"
    )
    connection.execute("INSERT INTO v_symbol VALUES ('600000.SH', NULL)")
    rows = []
    for index in range(65):
        close = 10.0 + index * 0.1
        rows.append(
            (
                "600000.SH",
                date(2026, 1, 1) + timedelta(days=index),
                close - 0.05,
                close + 0.2,
                close - 0.2,
                close,
                2_500_000_000.0,
            )
        )
    connection.executemany(
        "INSERT INTO v_daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.close()

    symbol_catalog = tmp_path / "symbols.json"
    symbol_catalog.write_text(
        json.dumps(
            {
                "ok": True,
                "data": {
                    "item": [
                        {"thscode": "600000.SH", "name": "测试股票"},
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_marketdb_report(
        output_path=tmp_path / "output" / "marketdb.html",
        database_path=tmp_path / "runtime" / "report.duckdb",
        source_database=source,
        symbol_catalog=symbol_catalog,
        sessions=60,
    )
    html = result.report_path.read_text(encoding="utf-8")
    assert result.symbols == 1
    assert "同花顺金融数据 marketdb" in html
    assert "测试股票" in html
    assert "前复权" in html


def test_marketdb_build_appends_closed_market_snapshot(tmp_path: Path):
    source = tmp_path / "source.duckdb"
    connection = duckdb.connect(str(source))
    connection.execute(
        "CREATE TABLE v_daily_qfq (thscode VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, amount DOUBLE)"
    )
    connection.execute("CREATE TABLE v_symbol (thscode VARCHAR, name VARCHAR)")
    connection.execute("INSERT INTO v_symbol VALUES ('600000.SH', '测试股票')")
    first = date(2026, 6, 1)
    rows = []
    for index in range(60):
        close = 10 + index * .1
        rows.append(("600000.SH", first + timedelta(days=index), close, close, close, close, 100_000_000))
    connection.executemany("INSERT INTO v_daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    connection.close()

    snapshot_date = first + timedelta(days=60)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "ok": True,
                "data": {
                    "timestamp": int(
                        datetime.combine(
                            snapshot_date,
                            datetime.min.time(),
                            tzinfo=ZoneInfo("Asia/Shanghai"),
                        ).timestamp()
                        * 1000
                    ),
                    "item": [
                        {
                            "thscode": "600000.SH",
                            "open_price": 15.9,
                            "high_price": 16.2,
                            "low_price": 15.8,
                            "last_price": 16.0,
                            "prev_price": 15.9,
                            "turnover": 200_000_000,
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    result = build_marketdb_report(
        output_path=tmp_path / "output" / "latest.html",
        database_path=tmp_path / "runtime" / "report.duckdb",
        source_database=source,
        market_snapshot=snapshot,
        snapshot_date=snapshot_date,
        sessions=60,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.as_of == snapshot_date
    assert manifest["snapshot_rows"] == 1
    assert "marketdb + 收盘快照" in result.report_path.read_text(encoding="utf-8")


def test_latest_coverage_allows_a_small_suspended_population():
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE raw_prices (
            thscode VARCHAR,
            trade_date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            amount_billion DOUBLE
        )
        """
    )
    latest = date(2026, 8, 21)
    rows = [
        (f"{index:06d}.SZ", latest, 10.0, 10.2, 9.8, 10.1, 1.0)
        for index in range(99)
    ]
    rows.append(("999999.SZ", latest - timedelta(days=1), 10.0, 10.2, 9.8, 10.1, 1.0))
    connection.executemany("INSERT INTO raw_prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    checks = validate_prices(connection, latest)

    coverage = next(check for check in checks if check.name == "latest_symbol_coverage")
    assert coverage.passed
    assert "99.00%" in coverage.detail


def test_build_industry_report_from_official_cli_envelopes(tmp_path: Path):
    database = tmp_path / "market.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(
        """
        CREATE TABLE raw_prices (
            thscode VARCHAR, name VARCHAR, trade_date DATE, open DOUBLE,
            high DOUBLE, low DOUBLE, close DOUBLE, amount_billion DOUBLE
        )
        """
    )
    latest = date(2026, 8, 21)
    connection.executemany(
        "INSERT INTO raw_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("600001.SH", "行业一股票", latest - timedelta(days=1), 10, 10, 10, 10, 1),
            ("600001.SH", "行业一股票", latest, 11, 11, 11, 11, 2),
            ("600002.SH", "行业二股票", latest - timedelta(days=1), 10, 10, 10, 10, 1),
            ("600002.SH", "行业二股票", latest, 9, 9, 9, 9, 3),
        ],
    )
    connection.close()

    def write_envelope(path: Path, items):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"ok": True, "data": {"item": items}}, ensure_ascii=False),
            encoding="utf-8",
        )

    catalog = tmp_path / "catalog.json"
    write_envelope(
        catalog,
        [
            {"thscode": "881001.TI", "name": "测试行业一"},
            {"thscode": "881002.TI", "name": "测试行业二"},
        ],
    )
    history_dir = tmp_path / "history"
    dates = [latest - timedelta(days=89 - index) for index in range(90)]

    def history_items(multiplier: float):
        return [
            {
                "date_ms": int(
                    datetime.combine(
                        day,
                        datetime.min.time(),
                        tzinfo=ZoneInfo("Asia/Shanghai"),
                    ).timestamp()
                    * 1000
                ),
                "close_price": 100 + index * multiplier,
                "turnover": 1_000_000_000 + index * 1_000_000,
            }
            for index, day in enumerate(dates)
        ]

    write_envelope(history_dir / "000300_SH.json", history_items(1.0))
    write_envelope(history_dir / "881001_TI.json", history_items(1.8))
    write_envelope(history_dir / "881002_TI.json", history_items(0.4))

    constituents_dir = tmp_path / "constituents"
    write_envelope(
        constituents_dir / "881001_TI.json",
        [{"thscode": "600001.SH", "name": "行业一股票"}],
    )
    write_envelope(
        constituents_dir / "881002_TI.json",
        [{"thscode": "600002.SH", "name": "行业二股票"}],
    )

    result = build_industry_report(
        output_path=tmp_path / "output" / "industry.html",
        catalog_path=catalog,
        history_dir=history_dir,
        constituents_dir=constituents_dir,
        database_path=database,
    )

    html = result.report_path.read_text(encoding="utf-8")
    assert result.industries == 2
    assert "测试行业一" in html
    assert "行业一股票" in html
    assert "000300.SH 沪深300" in html
    assert "一键刷新" in html
    assert "重启服务" in html
    assert "/api/restart" in html
    assert "/api/refresh" in html
    assert "/api/auth/login" in html
    assert "设置 API Key" in html
    assert 'const REPORT_KEY="industry"' in html
    assert "loadLatestReport(result.status)" in html
    assert "prefers-color-scheme:light" in html
    assert "updateSelection(previousCode)" in html
    assert '"heat_dates":["2026-08-02"' in html
    assert '"2026-08-21"]' in html


def test_publish_report_bundle_replaces_current_reports_and_removes_old_html(tmp_path: Path):
    staging = tmp_path / "staging"
    output = tmp_path / "output"
    staging.mkdir()
    output.mkdir()
    (output / "latest.html").write_text("old market", encoding="utf-8")
    (output / "industry.html").write_text("old industry", encoding="utf-8")
    (output / "market-2026-08-21.html").write_text("obsolete", encoding="utf-8")
    generated_at = "2026-08-25T14:00:00+08:00"
    (staging / "latest.html").write_text("market 2026-08-24", encoding="utf-8")
    (staging / "industry.html").write_text("industry 2026-08-24", encoding="utf-8")
    for name in ("run_manifest.json", "industry_manifest.json"):
        (staging / name).write_text(
            json.dumps(
                {
                    "status": "success",
                    "as_of": "2026-08-24",
                    "generated_at": generated_at,
                    "report": "staging/report.html",
                }
            ),
            encoding="utf-8",
        )

    result = publish_report_bundle(staging, output)

    assert result.as_of == "2026-08-24"
    assert (output / "latest.html").read_text(encoding="utf-8") == "market 2026-08-24"
    assert (output / "industry.html").read_text(encoding="utf-8") == "industry 2026-08-24"
    assert not (output / "market-2026-08-21.html").exists()
    assert json.loads((output / "run_manifest.json").read_text())["report"] == str(
        output.resolve() / "latest.html"
    )
