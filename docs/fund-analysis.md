# 场外基金净值与AI解读日报（CLI / Actions）

场外基金使用独立身份 `fund:六位代码` 和净值数据契约，不进入股票/场内ETF的 OHLCV、技术择时、LLM评级、止损点或回测链路。裸六位股票代码原有含义保持不变；不要把场外基金代码放进 `STOCK_LIST`。

## 使用

安装项目现有依赖后运行：

```bash
# 可选：在 .env 或进程环境配置 FUND_LIST；配置本身不启用分析/定时
# FUND_LIST=021190,018123,020356,001672,025500,024480
python main.py --funds --no-notify --fund-no-news --fund-no-ai

# 显式代码覆盖 FUND_LIST；首次数据验收不发通知，不检索新闻，不调用模型
python main.py --funds 021190,018123,020356,001672,025500,024480 --no-notify --fund-no-news --fund-no-ai

# 显式基金身份等价写法；复用已配置云端模型与新闻检索，但不发送
python main.py --funds fund:001672,fund:020356 --no-notify

# 复用现有通知配置，包括邮件；运行前确认收件人和所有启用渠道
python main.py --funds 021190,018123,020356,001672,025500,024480
```

- `--funds` 是独立的一次性模式，不能同时指定股票、券商持仓、定时、大盘复盘、ETF轮动、回测或 Web 服务模式。
- `--fund-no-news` 和 `--fund-no-ai` 只在基金模式有效，分别跳过新闻和AI，互不影响。`--dry-run` 在基金模式同时禁止新闻、AI和通知，仍读取正式净值并生成数据报告。
- 不带代码的 `--funds` 读取 `FUND_LIST`；显式代码覆盖配置。进程环境优先于 `.env`，空或非法清单报错，不回退到股票。
- `FUND_LIST` 不修改 `STOCK_LIST`，普通股票启动不消费它，错误的未使用基金清单也不会阻断股票。
- CLI基金模式默认尝试云端AI解读，复用现有模型/渠道配置；没有可用密钥或模型失败时明确降级为程序数据报告。`--fund-no-ai` 的纯数据模式不需要LLM密钥。基金模式拒绝本地Codex、Claude Code、OpenCode生成及本地回退；模型地址要求HTTPS和公开地址语法，拒绝私网、回环和已知本地别名。不解析DNS，也不证明公开域名背后的实际部署位置。
- 报告保存为 `reports/fund_report_<UTC时间戳>.md`，同名 JSON 保存独立基金身份、资料、净值历史、指标、来源、抓取时间、新闻和失败项。JSON `schema_version=2` 增加 `news_evidence`（来源/关系/时效/覆盖）及 `interpretation`（状态、实际模型、事实引用、单基金解读和横向观察），保留原 `news` 文本。不会写入股票 `StockDaily` 或分析历史表。
- 一只基金失败仍保存其他基金及失败原因；有基金失败时 CLI 返回 1。通知全部/部分失败、无可用通知渠道或结果不确定也返回 1；主动禁用通知和配置中的降噪抑制不视为失败。
- 通知结果记录在日志和JSON的 `notification`（状态、渠道成功标记、错误代码），不保存邮件账号凭证。失败/结果不确定均不由本模块重发；SMTP接收成功仍不等于收件箱已送达。
- 通知沿用 `report` 路由与默认收件人，**不使用股票代码分组**、不向所有邮件分组广播。基金邮件使用基金主题。已有股票/ETF的默认主题和路由不变。

## GitHub Actions 手动运行

沿用 `.github/workflows/00-daily-analysis.yml`（界面名称仍为“每日股票分析”），不建立第二条并行日报工作流：

1. 可在 Settings → Secrets and variables → Actions → Repository variables 配置 `FUND_LIST`；同名Secret作为后备。不要改动原 `STOCK_LIST`。
2. Actions → 每日股票分析 → Run workflow，选择目标分支和 `mode=funds-only`。
3. `fund_codes` 可填本次六位基金代码以覆盖 `FUND_LIST`；留空使用配置。该输入只在基金模式有效。
4. 首次纯数据验收勾选 `no_notify`、`fund_no_news`、`fund_no_ai`。完整AI报告仅预览时只勾选 `no_notify`，其余两项false；发送前核对原有收件人与 `NOTIFICATION_REPORT_CHANNELS`。
5. `funds-only` 在股票交易日检查前进入独立路径，非交易日也可读取最新已披露净值，`force_run` 可保持false，不会补造当天净值。
6. 功能未合入main前，Use workflow from 选择 `feature/off-exchange-fund-mvp`，否则运行的是所选分支自己的代码。
7. 从本次Run的 `analysis-reports-<运行编号>` artifact读取Markdown、JSON及日志。任务只调用一次基金入口，不再运行股票或大盘、不逐基金重复推送。

既有云端模型配置、新闻key、邮件Secrets和通知路由原样复用，不新增密钥、不改收件人。未配置新闻只提示不可用，不影响确定性净值报告；未配置通知且请求发送则任务返回失败。

**依赖安装边界：** 仅 `workflow_dispatch` 且 `mode` 精确为 `funds-only` 时，安装脚本从临时依赖清单中排除独立基金路径不使用的 `fxmacrodata-public-client`，避免该 GitHub release wheel 下载故障阻塞基金运行；仓库原 `requirements.txt` 不变。股票、大盘、定时及未知模式仍安装完整依赖。其余依赖内容（含哈希、markers、注释和相对 include 路径）不变，pip 升级及 Futu 导入检查保留；任何其他安装失败照常中止。宏观包条目缺失、重复或使用续行时明确失败，需审查清单后再运行，不静默跳过。临时文件在安装成功或失败后清理。此隔离不适用于股票 Agent 或通用部署，也不更换依赖来源或关闭 TLS/哈希校验。股票等完整安装仍可能受原宏观 wheel 的 HTTP 504 影响。移除该包也会移除它附带的传递依赖约束，不承诺解算结果完全相同；本地 Python 3.12 离线回归不是 Actions Python 3.11 干净 runner 的实际安装验证，后者尚未执行。

**防重复边界：** 同一GitHub Run的 Re-run（`github.run_attempt>1`）在基金模式强制 `--no-notify`，无论首次是部分成功还是发送结果未知，都不会自动再发。需要再次发送应检查前次状态后新建一次 Run workflow。新Run仍可能发送相同净值，这是明确的新请求；本版不承诺所有跨Run情况严格只投递一次。

**没有启用基金定时。** 原股票cron `0 10 * * 1-5`（北京时间工作日18:00）完全保留，定时触发仍只执行原full股票流程；填写FUND_LIST或手动选择funds-only都不改变它。基金自动运行时间须另外选择并明确启用，此补丁未添加基金cron。

## 当前数据来源与可信边界

均为东方财富/天天基金公开接口转述的已披露数据，未独立核对管理人原始公告：

1. `fund.eastmoney.com/pingzhongdata/{code}.js`：基金代码/名称、单位净值、累计净值、日增长率、分红/拆分标记、经理。
2. `api.fund.eastmoney.com/f10/lsjz`：核对最新净值日期及数值。与第一项同源，不是独立双源容灾。不一致阻止该基金分析；该接口不可用时明确提示仅有单端点数据。
3. `fundf10.eastmoney.com/jbgk_{code}.html`：类型、成立日期等当前资料。标题校验基金全名及代码；请求失败、身份校验失败或类型/成立日期/经理缺失时，至多增加一个同源备用页面 `fund.eastmoney.com/{code}.html`，先校验身份，再限定解析基金资料表。保留主来源错误类别/HTTP状态和实际采用的来源链接；备用也失败就保留已有可信字段并标明缺失，不用旧资料补值。币种未核验时不猜测。
4. `FundArchivesDatas.aspx?type=jjcc`：最近披露股票明细及报告期末。缺数据不等于零持仓；报告期末不等于公告日，真实公告日期缺失时保留未知。
5. 基金首页标题区域“查看相关ETF”链接：仅ETF联接产品读取，再访问目标页核验代码/名称/ETF类型。核验的是目标身份，不是配置比例或底层穿透，关系披露时间缺失保持未知。
6. `api.fund.eastmoney.com/f10/HYPZ/?fundCode={code}&year=`：核验基金代码、名称、行业行及报告期，读取最新已披露行业比例。拒绝错基金、未来报告期和无效比例；报告期不等于实际披露日期。

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

新闻使用现有有时限的搜索服务，按证据关系分开检索：

- 基金公告及直接新闻：标题含完整基金名称与份额类别，统一全半角、HTML实体和空白；代码或摘要单独出现均不通过。公告仅是相关检索线索，未声称核对管理人原文。
- 目标ETF：只用基金标题区域链接和目标页双重身份核验的关系，缺关系不猜测。
- 已披露重仓：选明细中权重最大的至多两项；名称和原样代码保留在查询/来源中。短名称或常见单词必须在标题同时出现原代码和金融/公司语境，不猜别名或市场，较长名称也要求公司语境。保留报告期及披露日期未知标记，不证明现阶段仍持有或造成涨跌。
- 行业：优先带报告期的行业配置；缺配置时仅用产品名称中明确主题词，并注明“名称主题，非持仓配置”。行业/主题标题也需行业语境，不据此核验公司身份。

每基金最多六次检索、总等待三十六秒、单次最多十二秒、总计最多十条去重结果。静态行情/资料页不作为事件新闻；其他基金或ETF的持仓名单提及不视为该公司的事件，只有标题另有该公司自身事件时才保留。对已返回的最多五个候选先筛身份/URL，再优先保留有效期内的新闻，其他日期仅作为警示线索；公告/直接新闻各最多一条，避免占满关联新闻配额。每条保存来源、发布日期、抓取时间、关联依据和资料日期。`fresh/stale/unknown/future` 明确区分时效，只有fresh标题进入模型当前事件证据。各类别覆盖状态区分未配置、无关系、无结果、过滤、超时或预算不足，不把“未找到”解释为“没有风险”。拒绝脚本、私网及异常链接；外部文本经HTML/Markdown转义。

JSON新闻追加 `evidence_scope` 和 `model_eligible`：`fund_event` 是本基金事件线索，`related_event` 是关联对象事件线索，`sector_context` 是具体行业/主题背景，`weak_background` 是制造业等宽泛行业背景。它们都不是事实认证。宽泛背景在报告独立列出，明确不作为模型事件证据；`fresh` 仅表示检索日期在窗口内，不等于关联充分或事件已核实。只有日期和内容资格同时满足的结果才进入模型线索，具体行业背景也不能当成基金事件。

每类检索的 `coverage.reason_counts` 保存固定原因与计数，包括静态页、其他产品持仓提及、缺事件内容、身份不符、重复、额度舍弃和最终保留类别。标准日志只记录这些固定代码/计数，不记录诊断对象的标题、URL或供应商异常原文。已有新闻标题及公开来源仍作为报告正常证据保存。

严格匹配可能漏报；标题不等于事实核验。新闻失败不丢弃有效净值。

## 云端AI解读与横向比较

通过现有 `GeminiAnalyzer.generate_text_with_metadata` 公共接口复用云端LiteLLM模型及配置的云端回退，不是给规则模板改名。虽然接口类名含Gemini，实际可配置OpenAI、Gemini、Anthropic等，报告显示实际返回模型/供应方/后端，不冒称GPT。需要GPT时使用项目现有OpenAI云端配置，不增加基金专用新密钥。

一批基金一次请求，生成每只核心结论、风险、证据、后续观察及横向观察。程序先计算事实，模型以事实ID占位引用数字、日期、代码，另行撰写中文定性解释。引用、结构、基金归属、数量词、方向和当前持仓等经过保守检查，失败整批弃用模型结论并保留数据。自然语言检查不是事实证明，仍可能遗漏量化/时态错误，也可能拒绝合理文字；报告明确标记AI观点，关键判断应回查原始证据。

横向表只并列净值日期与真实指标区间，不评分或排名。只有全体币种已核验且相同、净值日期相同、指标有效、实际起止区间相同时，才允许对应指标的跨基金数值观察。QDII时差、未知币种、分红或短历史不得靠模型补齐。

状态 `ok/disabled/unavailable/model_failed` 均有原因。失败的 `interpretation.diagnostics` 在JSON、报告和标准日志中保留白名单诊断：固定阶段/错误码、已确认基金代码、章节及从零开始的位置索引，另有字符数、字节数、引用数或JSON行列等计数。诊断不保存模型原文、证据文本、未知字段、未知引用ID、密钥或供应商异常字符串；未知诊断字段丢弃。仅报告首个阻断校验，不表示其他校验已经通过；增加诊断不会追加模型调用或自动发送。

仅凭HTTP成功不代表解读通过。此前只保留统一校验错误的报告不能回溯具体子校验原因；新诊断只适用于应用补丁后的运行。无配置、关闭、本地后端不允许、模型报错/超时或输出无效不冒充GPT。AI降级本身不使CLI退出失败，仍可按既有通知配置发送明确降级的数据报告，运营检查JSON `interpretation.status`。基金程序API `run_fund_analysis` 默认保持解读关闭以兼容调用，CLI显式开启；历史截止日不混入当前新闻/AI判断。

最多二十只基金、提示词一百五十KB、输出四十八KB、输出token上限六千。默认报告等待四十五秒，单进程只有一个未结束请求。等待期限不是网络硬取消：已有云端请求或配置回退可能继续至底层超时并产生费用；迟到结果不采用，也不补发报告或邮件。

## 已知范围限制

- 支持CLI与GitHub Actions手动模式、本地JSON快照；未启用基金定时，也未接入 Web/API、股票导入、机器人、Agent工具或原股票回测。
- 未实现ETF穿透、独立管理人公告核对、真实披露时间、合同基准/未来变更、申赎限制、费率表、交易执行或个性化组合分析。
- 货币基金不是普通单位净值模型，明确拒绝。
- 没有基金专用持久数据库、跨运行增量缓存、通知跨运行去重保证。每次运行是独立报告；不要把本地存在报告等同于通知送达。

## 验证与部署注意

```bash
python -m pytest tests/test_fund_analysis.py tests/test_fund_fetcher.py tests/test_fund_analysis_service.py tests/test_fund_news_service.py tests/test_fund_interpretation.py tests/test_main_funds.py tests/test_fund_config.py tests/test_daily_fund_workflow.py tests/test_daily_dependencies.py tests/test_fund_dependency_isolation.py
./scripts/ci_gate.sh
```

首次数据接口验收使用 `--no-notify --fund-no-news --fund-no-ai`，只有确认通知配置后再开启推送。离线测试使用合成数据，不用某天的真实行情当永久预期值。

现有项目配置在检测到 `HTTP_PROXY` 时会把 `eastmoney.com` 加进 `NO_PROXY`。如果部署环境只能通过HTTPS代理出站，可能出现“单独接口成功、main入口失败”；应按环境需求配置代理（例如仅配置所需 `HTTPS_PROXY`，避免触发该既有直连规则）。本次改造未改动全项目代理策略，也不会关闭TLS校验。

## English summary

This is an opt-in CLI and manual GitHub Actions MVP for published off-exchange fund NAV reports. Use `--funds` (optionally reading `FUND_LIST`), never `STOCK_LIST`. Manual `funds-only` dispatch accepts a one-time `fund_codes` override; fund reruns suppress notifications. No fund cron is added or enabled, and the original stock schedule is unchanged. Existing stock/ETF routes remain unchanged. Run with `--no-notify --fund-no-news --fund-no-ai` for safe live validation. It saves separate Markdown/JSON artifacts, validates identity and dates, and reuses existing news and notification services. Direct news requires full fund/share-class headline identity. Related news requires verified ETF identities or dated holdings/industry relationships, issuer/sector context and freshness metadata; strict matching reduces recall. Only fresh news enters model evidence. Return metrics are conservative event-free unit-NAV changes, not a claimed reinvested total-return series. Distributions, missing data and insufficient history produce N/A. QDII publication lags and dated holdings are explicit. Web/API/scheduling, look-through, subscriptions/redemptions, trading and backtesting are out of scope. Fund profile failures use one identity-checked same-provider homepage fallback, preserving diagnostics and missing-field warnings. All data currently comes from the same Eastmoney source family.

The fund CLI now attempts evidence-cited cloud narrative through the existing configured model interface; use `--fund-no-ai` or manual workflow `fund_no_ai` to disable it. Structured numeric/date placeholders are program-owned. Conservative free-prose checks do not guarantee semantic truth or eliminate every quantitative/temporal error. Actual model/provider identity and explicit degradation states are reported. No local CLI backend or private/plain-HTTP model endpoint is allowed. Endpoint validation does not resolve DNS. The report deadline is not hard cancellation of an in-flight cloud request. Model/provider-boundary mocks do not constitute live API validation. Validation diagnostics now use finite stage/reason codes, validated positions and bounded counts without retaining model output or provider exception text. Legacy generic failures cannot be retroactively attributed. Quote/profile pages and mere mentions in another fund’s portfolio are not issuer events; broad-sector background is separately labelled and excluded from model evidence. News freshness alone is not relevance or event verification.

Only an exact manual `workflow_dispatch` / `funds-only` run excludes `fxmacrodata-public-client` from a temporary requirements file. The original requirements, stock/market/scheduled installs, pip upgrade and Futu smoke check are unchanged. All remaining bytes and relative include paths are preserved; other pip failures still fail the job. Missing, duplicate or continued macro entries fail explicitly, and the temporary file is cleaned up. No dependency source, TLS or hash-verification setting is changed. Full stock installs remain exposed to the original macro wheel HTTP 504 failure. Removing the package also removes its transitive dependency constraints, so resolver output is not guaranteed identical. Local Python 3.12 offline checks do not validate a clean Actions Python 3.11 install; that install has not been run.
