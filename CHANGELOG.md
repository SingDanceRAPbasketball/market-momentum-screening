# 更新记录

本文件记录对使用者可见的功能、修复和运行方式变化。以后每次功能迭代或重要修复都应同步更新本文件；纯数据刷新和内部重构可以不单独记录。

## 未发布

### 文档

- 新增本更新记录，并在 Pull Request 模板中加入更新记录检查项。

## 2026-08-31 · 本地服务托管与页面重启

对应提交：[`13c9ccd`](https://github.com/SingDanceRAPbasketball/market-momentum-screening/commit/13c9ccd3da28ea1c1653d2456de5c5f1e8cdbf95)

### 新增

- 全市场趋势页和行业强度页增加“重启服务”按钮，服务恢复后页面自动重载。
- 新增仅限本机、临时令牌保护的 `/api/restart` 接口，并在数据刷新期间阻止重启。
- 新增 macOS LaunchAgent 托管，可登录自动启动、异常退出自动恢复，无需保持终端窗口运行。
- 新增 `market-momentum service install/status/uninstall` 服务管理命令。

### 验证

- 增加服务重启、LaunchAgent 配置和页面按钮的自动化测试。

## 2026-08-25 · 真实数据刷新与报告自动发布

对应提交：[`7ee05cc`](https://github.com/SingDanceRAPbasketball/market-momentum-screening/commit/7ee05cc694b320d94f9725dd65db85c0576a3a0b)

### 新增

- 收盘数据包滞后时，可使用最新全市场快照衔接前复权历史。
- 两张报告采用临时目录构建和原子发布，全部生成并校验通过后才替换线上页面。
- 刷新成功后自动展示最新报告，并清理 `output/` 中旧的 HTML 文件。
- 页面持续检测报告版本，不同标签页触发刷新后也能自动切换到最新版本。

### 修复

- 修复最新交易日已经收盘但页面仍展示前一交易日数据的问题。
- 修复行业热力带日期不同步、图表异常拉伸和刷新后页面未自动更新的问题。

## 2026-08-24 · 首个公开版本

发布版本：[`v0.1.0`](https://github.com/SingDanceRAPbasketball/market-momentum-screening/releases/tag/v0.1.0)

相关提交：[`0373cf3`](https://github.com/SingDanceRAPbasketball/market-momentum-screening/commit/0373cf3)、[`e48326b`](https://github.com/SingDanceRAPbasketball/market-momentum-screening/commit/e48326b)

### 新增

- 建立本地全市场趋势、动量和流动性筛选看板。
- 增加 20 日涨幅、MA20、MA60、流动性分层和四种趋势状态。
- 增加市场宽度、动量散点、涨幅分布、趋势矩阵、联动筛选和 CSV 导出。
- 建立 90 个一级行业的 RS5/RS20/RS60、近20日热力带、成交额脉冲、成分股宽度和行业详情。
- 接入同花顺金融 API、Market Dump、本地 DuckDB 和页面内 API Key 认证入口。
- 发布 2026-08-21 参考报告，作为本地页面效果基线。

### 修复与治理

- 修复浏览器页面放大后表头与数字列错位的问题。
- 增加 CODEOWNERS，仓库改动通过 Pull Request 接受所有者审核。
