#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
project_dir=${script_dir:h}
cd "$project_dir"

require_ok() {
  jq -e '.ok == true' >/dev/null <<< "$1"
}

auth_result=$(hithink-finance auth status --format json)
require_ok "$auth_result"
if [[ $(jq -r '.data.configured' <<< "$auth_result") != true ]]; then
  print -u2 '同花顺金融 API 尚未认证，请先运行: hithink-finance auth login'
  exit 1
fi

sync_result=$(hithink-finance data sync --format json)
require_ok "$sync_result"
validate_result=$(hithink-finance data validate --format json)
require_ok "$validate_result"
status_result=$(hithink-finance data status --format json)
require_ok "$status_result"
source_database=$(jq -r '.data.path' <<< "$status_result")

mkdir -p runtime/industry-history runtime/industry-constituents output
staging_dir=$(mktemp -d "${project_dir}/runtime/report-build.XXXXXX")
cleanup_staging() {
  rm -rf "$staging_dir"
}
trap cleanup_staging EXIT

hithink-finance symbol list \
  --asset-type a-share \
  --exchange SH,SZ \
  --limit 10000 \
  --offset 0 \
  --output runtime/hithink-symbols.json \
  --format json >/dev/null
jq -e '.ok == true' runtime/hithink-symbols.json >/dev/null

hithink-finance index catalog \
  --tag industry \
  --output runtime/hithink-industries.json \
  --format json >/dev/null
jq -e '.ok == true' runtime/hithink-industries.json >/dev/null

latest_date=$(.venv/bin/python - "$source_database" <<'PY'
import sys
import duckdb

connection = duckdb.connect(sys.argv[1], read_only=True)
try:
    print(connection.execute("SELECT MAX(date) FROM v_daily_qfq").fetchone()[0].isoformat())
finally:
    connection.close()
PY
)
start_date=$(date -j -v-180d -f '%Y-%m-%d' "$latest_date" '+%Y-%m-%d')
start_ms=$(date -j -f '%Y-%m-%d %H:%M:%S' "$start_date 00:00:00" '+%s000')
today=$(date '+%Y-%m-%d')
end_ms=$(date -j -f '%Y-%m-%d %H:%M:%S' "$today 23:59:59" '+%s000')

fetch_history() {
  local code=$1
  local target="runtime/industry-history/${code//./_}.json"
  hithink-finance index history \
    --thscode "$code" \
    --start-ms "$start_ms" \
    --end-ms "$end_ms" \
    --output "$target" \
    --format json >/dev/null
  jq -e '.ok == true' "$target" >/dev/null
}

fetch_history '000300.SH'
benchmark_date=$(.venv/bin/python - runtime/industry-history/000300_SH.json <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["data"]["item"]
print(max(datetime.fromtimestamp(item["date_ms"] / 1000, ZoneInfo("Asia/Shanghai")).date() for item in items))
PY
)

market_snapshot_args=()
if [[ "$benchmark_date" > "$latest_date" ]]; then
  current_hhmm=$(date '+%H%M')
  if [[ "$benchmark_date" != "$today" || "$current_hhmm" -lt 1505 ]]; then
    print -u2 "远端指数已到 ${benchmark_date}，但仅支持在当日 15:05 后用收盘快照补齐"
    exit 1
  fi
  hithink-finance market snapshot \
    --limit 10000 \
    --offset 0 \
    --output runtime/hithink-market-snapshot.json \
    --format json >/dev/null
  jq -e '.ok == true and (.data.item | length) >= 5000' runtime/hithink-market-snapshot.json >/dev/null
  market_snapshot_args=(
    --market-snapshot runtime/hithink-market-snapshot.json
    --snapshot-date "$benchmark_date"
  )
  print "marketdb 截止 ${latest_date}，使用 ${benchmark_date} 全市场收盘快照补齐"
fi

count=0
while IFS= read -r code; do
  fetch_history "$code"
  target="runtime/industry-constituents/${code//./_}.json"
  hithink-finance index constituents \
    --thscode "$code" \
    --output "$target" \
    --format json >/dev/null
  jq -e '.ok == true' "$target" >/dev/null
  count=$((count + 1))
  if (( count % 15 == 0 )); then
    print "行业数据 ${count}/90"
  fi
done < <(jq -r '.data.item[] | select(.thscode | startswith("881")) | .thscode' runtime/hithink-industries.json)

.venv/bin/market-momentum build-marketdb \
  --source-database "$source_database" \
  --symbol-catalog runtime/hithink-symbols.json \
  "${market_snapshot_args[@]}" \
  --sessions 120 \
  --output "$staging_dir/latest.html" \
  --database runtime/market.duckdb

.venv/bin/market-momentum build-industry \
  --catalog runtime/hithink-industries.json \
  --history-dir runtime/industry-history \
  --constituents-dir runtime/industry-constituents \
  --database runtime/market.duckdb \
  --benchmark 000300.SH \
  --output "$staging_dir/industry.html"

.venv/bin/pytest -q
.venv/bin/market-momentum publish \
  --staging-dir "$staging_dir" \
  --output-dir output
print '真实数据报告刷新完成：output/latest.html 与 output/industry.html'
