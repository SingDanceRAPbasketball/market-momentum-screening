# Market Momentum Screening

全市场趋势、动量、流动性与行业强度筛选项目。仓库支持确定性模拟数据和同花顺金融 API 真实数据两条路径；真实路径使用官方 `hithink-finance` CLI、Market Dump、本地 DuckDB、证券目录与 90 个一级行业指数数据，输出可完全离线打开的交互式 HTML。

详细设计和已确定的计算口径见 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)，每次功能迭代与重要修复见 [CHANGELOG.md](CHANGELOG.md)。

## 本地快速开始

要求 Python 3.9 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/market-momentum build --as-of 2026-08-21
```

生成物：

- `output/latest.html`：单文件离线交互报告，内含全部筛选明细；
- `output/industry.html`：90 个一级行业的相对强度、热力带、成分股宽度和详情；
- `output/run_manifest.json`：本次运行日期、数据源和校验结果；
- `output/industry_manifest.json`：行业报告运行清单；
- `runtime/market.duckdb`：本地 DuckDB 数据库。

在 macOS 中以静态方式打开报告：

```bash
open output/latest.html
```

静态方式可查看和筛选，但浏览器不能直接运行本地刷新脚本。要启用页面右上角的“一键刷新”，启动仅绑定本机回环地址的报告服务：

```bash
.venv/bin/market-momentum serve
```

然后打开 [http://127.0.0.1:8765/latest.html](http://127.0.0.1:8765/latest.html)。可以通过“设置 API Key”将凭据安全提交给仅限本机的服务：Key 通过 stdin 传给官方 CLI，并保存到系统凭据库，不写入 HTML、项目文件或日志。随后“一键刷新”会同步数据、重建全市场与行业报告，完成后自动重载页面；“重启服务”会完整重启当前本地服务并在恢复后自动重载页面。运行日志保存在 `runtime/refresh.log`。未安装下述守护服务时，服务完全停止后浏览器无法自行启动本机进程。

如需彻底免除终端常驻，可安装登录自动启动且异常退出自动恢复的 macOS 守护服务：

```bash
.venv/bin/market-momentum service install
```

安装后可用 `market-momentum service status` 检查状态，或用 `market-momentum service uninstall` 停止并移除。守护服务日志保存在 `runtime/server.stdout.log` 与 `runtime/server.stderr.log`。

刷新采用临时目录构建和原子发布：两张报告全部生成且测试通过后才会替换 `output/latest.html` 与 `output/industry.html`，同时删除 `output/` 下其他旧 HTML。通过本地服务打开的页面会持续检测报告版本；即使刷新由另一个标签页触发，也会自动切换到带版本标识的最新 HTML。直接以 `file://` 打开的静态页面无法接收刷新通知。

也可以调整模拟市场规模和日期：

```bash
.venv/bin/market-momentum build \
  --as-of 2026-08-21 \
  --symbols 5000 \
  --sessions 120 \
  --seed 20260821
```

当 `--as-of` 是周末时，本地版本会自动回退到最近一个工作日。模拟日历暂未处理中国法定节假日，接入扶摇交易日历后替换。

## 已实现

- 确定性模拟 OHLC 与成交额数据；
- DuckDB 本地存储及窗口指标计算；
- 20 日涨幅、MA20、MA60、20 日均额；
- 强趋势、修复、回调、弱趋势状态；
- T1—T4 流动性分层；
- 日期、唯一键、OHLC、成交额和最新日覆盖校验；
- 市场 KPI、动量散点、涨幅直方图、市场宽度、趋势矩阵；
- 股票名称、趋势、流动性、涨幅和最低成交额联动筛选；
- 当前筛选下 20 日涨幅前 15 名。
- 全量股票明细分页、排序与当前筛选结果 CSV 导出；
- 流动性分层和趋势状态独立统计；
- 官方 marketdb `v_daily_qfq` 前复权视图适配。
- 官方证券目录名称补全，避免 Market Dump 维表名称为空时退化为代码；
- 90 个 `881xxx.TI` 一级行业的 RS5 / RS20 / RS60、20 日热力带、成交额脉冲和排名变化；
- 行业成分股涨跌家数、等权涨跌代理、成交额与活跃个股联动详情；
- 主页面与行业页面双向导航。
- 仅限 `127.0.0.1` 的一键刷新与手动重启服务、并发保护、临时请求令牌和页面自动重载。
- 页面内 API Key 注入入口，使用 stdin 调用官方 CLI 并保存到系统凭据库。

本地报告图表层使用内嵌原生 SVG，不加载外部 CDN，因此断网也可打开。后续生产版可以替换为内嵌 ECharts，指标数据接口无需改变。

## 使用真实全市场数据

本项目不会用模拟数据冒充真实行情。先安装并认证官方 CLI：

```bash
npm install -g @hithink-tech/hithink-finance-cli
hithink-finance auth login
hithink-finance data init --format json
```

认证信息保存在系统凭据库，不写入仓库。首次初始化后，可使用一键脚本增量同步数据并重建两张报告：

```bash
./scripts/refresh_real_reports.zsh
```

若收盘后官方 marketdb 同步包仍滞后，脚本会先用沪深300日线确认最新交易日，再以全市场收盘快照衔接前复权历史。快照覆盖率或两张报告日期校验不通过时不会覆盖当前页面。

也可以单独构建全市场页面：

```bash
.venv/bin/market-momentum build-marketdb \
  --source-database "$HOME/Library/Application Support/hithink-finance/data/market.duckdb" \
  --symbol-catalog runtime/hithink-symbols.json \
  --sessions 120
```

源数据库必须包含官方 `v_daily_qfq` 和 `v_symbol` 视图。报告会明确显示“同花顺金融数据 marketdb / 前复权”。`--symbol-catalog` 接收官方 `symbol list --output` 的 JSON 信封，用于补齐真实股票名称。

行业页使用官方 `index catalog/history/constituents` 命令落盘的数据，并与全市场运行库联算：

```bash
.venv/bin/market-momentum build-industry \
  --catalog runtime/hithink-industries.json \
  --history-dir runtime/industry-history \
  --constituents-dir runtime/industry-constituents \
  --database runtime/market.duckdb \
  --output output/industry.html
```

## 测试

```bash
.venv/bin/pytest
```

测试覆盖四种趋势状态、四级流动性、20 日涨幅口径、停牌覆盖率、新股空收益、证券名称补全，以及全市场与行业报告端到端生成。

## 下一阶段

1. 将真实数据刷新接入定时任务；
2. 增加行业成分变更历史与成分覆盖率告警；
3. 增加报告版本归档和跨日期对比；
4. 接入对象存储和云端幂等发布。

## 安全约定

- API Key 优先保存在官方 CLI 的系统凭据库，云端通过 Secrets 注入；
- `.env`、DuckDB、Parquet、下载缓存、运行日志和生成报告不得提交到 Git；
- 报告公开发布前需要确认行情数据的再分发授权。
