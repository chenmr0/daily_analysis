# 场外基金已披露净值日报（CLI / Actions MVP）

场外基金使用独立身份 `fund:六位代码` 和净值数据契约，不进入股票/场内ETF的 OHLCV、技术择时、LLM评级、止损点或回测链路。裸六位股票代码原有含义保持不变；不要把场外基金代码放进 `STOCK_LIST`。

## 使用

安装项目现有依赖后运行：

```bash
# 可选：在 .env 或进程环境配置 FUND_LIST；配置本身不启用分析/定时
# FUND_LIST=021190,018123,020356,001672,025500,024480
python main.py --funds --no-notify --fund-no-news

# 显式代码覆盖 FUND_LIST；首次验收不发通知，不检索新闻
python main.py --funds 021190,018123,020356,001672,025500,024480 --no-notify --fund-no-news

# 显式基金身份等价写法；复用已配置新闻检索，但不发送
python main.py --funds fund:001672,fund:020356 --no-notify

# 复用现有通知配置，包括邮件；运行前确认收件人和所有启用渠道
python main.py --funds 021190,018123,020356,001672,025500,024480
```

- `--funds` 是独立的一次性模式，不能同时指定股票、券商持仓、定时、大盘复盘、ETF轮动、回测或 Web 服务模式。
- `--fund-no-news` 只在基金模式有效。`--dry-run` 在基金模式同时禁止新闻和通知，仍读取正式净值并生成确定性报告。
- 不带代码的 `--funds` 读取 `FUND_LIST`；显式代码覆盖配置。进程环境优先于 `.env`，空或非法清单报错，不回退到股票。
- `FUND_LIST` 不修改 `STOCK_LIST`，普通股票启动不消费它，错误的未使用基金清单也不会阻断股票。
- 无需 LLM key，不调用本地 Codex 或 LLM。
- 报告保存为 `reports/fund_report_<UTC时间戳>.md`，同名 JSON 保存独立基金身份、资料、净值历史、指标、来源、抓取时间、新闻和失败项。不会写入股票 `StockDaily` 或分析历史表。
- 一只基金失败仍保存其他基金及失败原因；有基金失败时 CLI 返回 1。通知全部/部分失败、无可用通知渠道或结果不确定也返回 1；主动禁用通知和配置中的降噪抑制不视为失败。
- 通知结果记录在日志和JSON的 `notification`（状态、渠道成功标记、错误代码），不保存邮件账号凭证。失败/结果不确定均不由本模块重发；SMTP接收成功仍不等于收件箱已送达。
- 通知沿用 `report` 路由与默认收件人，**不使用股票代码分组**、不向所有邮件分组广播。基金邮件使用基金主题。已有股票/ETF的默认主题和路由不变。

## GitHub Actions 手动运行

沿用 `.github/workflows/00-daily-analysis.yml`（界面名称仍为“每日股票分析”），不建立第二条并行日报工作流：

1. 可在 Settings → Secrets and variables → Actions → Repository variables 配置 `FUND_LIST`；同名Secret作为后备。不要改动原 `STOCK_LIST`。
2. Actions → 每日股票分析 → Run workflow，选择目标分支和 `mode=funds-only`。
3. `fund_codes` 可填本次六位基金代码以覆盖 `FUND_LIST`；留空使用配置。该输入只在基金模式有效。
4. 首次只验收时勾选 `no_notify`、`fund_no_news`。要使用现有新闻与通知，保持两者false，先确认原有邮件收件人与 `NOTIFICATION_REPORT_CHANNELS` 配置。
5. 从本次Run的 `analysis-reports-<运行编号>` artifact读取Markdown、JSON及日志。任务只调用一次基金入口，不再运行股票或大盘、不逐基金重复推送。

既有新闻key、邮件Secrets和通知路由原样复用，不新增密钥、不改收件人。未配置新闻只提示不可用，不影响确定性净值报告；未配置通知且请求发送则任务返回失败。

**防重复边界：** 同一GitHub Run的 Re-run（`github.run_attempt>1`）在基金模式强制 `--no-notify`，无论首次是部分成功还是发送结果未知，都不会自动再发。需要再次发送应检查前次状态后新建一次 Run workflow。新Run仍可能发送相同净值，这是明确的新请求；本版不承诺所有跨Run情况严格只投递一次。

**没有启用基金定时。** 原股票cron `0 10 * * 1-5`（北京时间工作日18:00）完全保留，定时触发仍只执行原full股票流程；填写FUND_LIST或手动选择funds-only都不改变它。基金自动运行时间须另外选择并明确启用，此补丁未添加基金cron。

## 当前数据来源与可信边界

均为东方财富/天天基金公开接口转述的已披露数据，未独立核对管理人原始公告：

1. `fund.eastmoney.com/pingzhongdata/{code}.js`：基金代码/名称、单位净值、累计净值、日增长率、分红/拆分标记、经理。
2. `api.fund.eastmoney.com/f10/lsjz`：核对最新净值日期及数值。与第一项同源，不是独立双源容灾。不一致阻止该基金分析；该接口不可用时明确提示仅有单端点数据。
3. `fundf10.eastmoney.com/jbgk_{code}.html`：类型、成立日期等当前资料。币种未核验时不猜测。
4. `FundArchivesDatas.aspx?type=jjcc`：最近披露股票明细及报告期末。缺数据不等于零持仓；报告期末不等于公告日，真实公告日期缺失时保留未知。

请求有超时、重试次数和响应大小上限，校验 HTTP 正文契约、代码一致性、非有限值、净值日期、重复日期及两端点冲突。只用 JSON 字面量解析，不执行外部 JavaScript。不使用盘中估值或估值回填正式净值。

2026-11-08在线验证的六代码均成功取得正式净值并通过同源最新值核对。五只返回2026-09-30股票持仓，021190持仓端点为空；六个 `fundgz` 估值端点都返回HTTP 200的HTML错误页，因此完全未接入。上游接口没有可用性保证，生产部署仍需自行检查其服务及数据使用条款。

## 指标口径

- 单位净值保留至少四位精度，时间戳按中国时区转换净值所属日期。
- 每个区间展示实际起止日期：1日为相邻可用净值日；1/3/6/12月按最新净值日向前移日历月，选不晚于锚点的最近净值；今年以来以前一年末为锚点。
- 月度锚点最多允许7自然日差距，年初锚点最多14日；区间内大于14自然日缺口触发 N/A，不假装已经核对各市场交易日历。
- 只有所有区间点的累计净值可用、累计减单位净值差额在0.000001容差内稳定、没有分红/拆分标记，才计算单位净值比值收益和最大回撤。它是保守的净值收益口径，**不是已独立核验的红利再投资总回报**。
- 分红/拆分区间或缺资料时 N/A，不将累计净值相除、不链乘已舍入日增长率、不补缺值或捏造历史。首版不输出波动率、Sharpe、评级。
- 12个月最大回撤要求完整12个月历史，定义为区间中相对此前最高净值的最大跌幅，输出正百分比。新基金不足整年不会用短窗口冒充年指标。
- 资料来源的净值通常已扣持续计提费用，本模块不重复扣减，也不估算个人申赎费用或实际收益。

## QDII、持仓与新闻

QDII显示实际净值日期、数据年龄和延迟提示，不硬编码“T+2”，不使用A股交易日判断应有当日净值。超过7自然日是一般时效提醒，不代表已违反该产品披露规则。

持仓只代表所标报告期。联接基金股票明细并不覆盖目标ETF及其底层持仓，未穿透或做涨跌归因；没有公布日期的资料不能用于历史时点回测。当前快照的经理等资料也不能作为历史资料。

复用现有有时限的题材新闻搜索，查询基金全名与代码；仅展示标题/摘要包含基金全名的结果（六位代码可能与股票重合，不能单独作为匹配依据）、发布日期（缺失则明确）和来源链接。新闻仅提供线索，不自动认定事件真实或解释净值涨跌。检索失败不影响净值报告。标题等外部文本进行HTML/Markdown转义，不允许脚本链接。

## 已知范围限制

- 支持CLI与GitHub Actions手动模式、本地JSON快照；未启用基金定时，也未接入 Web/API、股票导入、机器人、Agent工具或原股票回测。
- 未实现ETF穿透、独立管理人公告核对、真实披露时间、合同基准/未来变更、申赎限制、费率表、交易执行或个性化组合分析。
- 货币基金不是普通单位净值模型，明确拒绝。
- 没有基金专用持久数据库、跨运行增量缓存、通知跨运行去重保证。每次运行是独立报告；不要把本地存在报告等同于通知送达。

## 验证与部署注意

```bash
python -m pytest tests/test_fund_analysis.py tests/test_fund_fetcher.py tests/test_fund_analysis_service.py tests/test_main_funds.py tests/test_fund_config.py tests/test_daily_fund_workflow.py
./scripts/ci_gate.sh
```

首次数据接口验收使用 `--no-notify --fund-no-news`，只有确认通知配置后再开启推送。离线测试使用合成数据，不用某天的真实行情当永久预期值。

现有项目配置在检测到 `HTTP_PROXY` 时会把 `eastmoney.com` 加进 `NO_PROXY`。如果部署环境只能通过HTTPS代理出站，可能出现“单独接口成功、main入口失败”；应按环境需求配置代理（例如仅配置所需 `HTTPS_PROXY`，避免触发该既有直连规则）。本次改造未改动全项目代理策略，也不会关闭TLS校验。

## English summary

This is an opt-in CLI and manual GitHub Actions MVP for published off-exchange fund NAV reports. Use `--funds` (optionally reading `FUND_LIST`), never `STOCK_LIST`. Manual `funds-only` dispatch accepts a one-time `fund_codes` override; fund reruns suppress notifications. No fund cron is added or enabled, and the original stock schedule is unchanged. Existing stock/ETF routes remain unchanged. Run with `--no-notify --fund-no-news` for safe live validation. It saves separate Markdown/JSON artifacts, validates identity and dates, and reuses existing news and notification services. Return metrics are conservative event-free unit-NAV changes, not a claimed reinvested total-return series. Distributions, missing data and insufficient history produce N/A. QDII publication lags and dated holdings are explicit. Web/API/scheduling, look-through, subscriptions/redemptions, trading and backtesting are out of scope. All data currently comes from the same Eastmoney source family.
