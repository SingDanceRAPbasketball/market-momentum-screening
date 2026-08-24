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
end_ms=$(date -j -f '%Y-%m-%d %H:%M:%S' "$latest_date 23:59:59" '+%s000')

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
  --sessions 120 \
  --output output/latest.html \
  --database runtime/market.duckdb

.venv/bin/market-momentum build-industry \
  --catalog runtime/hithink-industries.json \
  --history-dir runtime/industry-history \
  --constituents-dir runtime/industry-constituents \
  --database runtime/market.duckdb \
  --benchmark 000300.SH \
  --output output/industry.html

.venv/bin/pytest -q
print '真实数据报告刷新完成：output/latest.html 与 output/industry.html'
