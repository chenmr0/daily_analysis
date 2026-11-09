# 场外基金净值与AI解读日报（CLI / Actions）

场外基金使用独立身份 `fund:六位代码` 和净值数据契约，不进入股票/场内ETF的 OHLCV、技术择时、LLM评级、止损点或回测链路。裸六位股票代码原有含义保持不变；不要把场外基金代码放进 `STOCK_LIST`。

## 首屏操作参考

报告开头先集中展示全部基金状态速览，再逐只展示由纯程序生成的操作参考、未持有/已持有两种适用情形、证据日期和改变判断前需要核验的条件，再展示横向数据、AI观点与详细资料。读取失败的基金同样保留“无法判断”卡片。

- `conditional / observe`：必要的近1月、近3月净值证据可用，但未取得个人背景和申赎规则，结论为**观望**。若未持有，暂不依据本报告新增买入；若已持有，是否卖出尚不能判断，须核对原定退出条件、资金期限和可承受风险范围。观望不表示继续持有安全，也不声称这些个人条件已触发。
- `unavailable / undetermined`：必需净值/区间证据不可用、数值或日期不合格、或本基金操作参考组件失败。不能把无法判断伪装成中性趋势。净值、AI状态和其他基金参考不受单只参考组件失败影响；参考失败不另改原有CLI退出码与通知语义。
- 正、负、零只描述各自实际区间的历史方向，使用未舍入值；极小非零值不显示为“持平”。同涨不等于买入候选，同跌不自动触发卖出评估。没有交易评分、价格、仓位、收益目标、回撤买卖阈值或已验证择时策略。
- 现有 `analyze_fund` 是指标与事件门控的真源。操作参考消费其 `Metric`，不重算收益或补齐 N/A；同时拒绝非有限值、布尔值、越界值、错误结束日期与不符合原有日历月锚点7自然日容差的起点。近12个月最大回撤历史不足时可以 N/A，不把短期历史冒充年指标。它是历史最差峰谷跌幅，非当前回撤、个人亏损或止损条件；本次没有新增波动率等指标。
- 币种未知在每卡明确显示“未核验”，不猜人民币、不作金额换算或跨基金优劣比较；同一基金已有的无量纲历史净值变化仍可保留。报告截止日不等于净值所属日，抓取日不等于真实披露日；周末、假期、QDII或自然日间隔都不机械产生买卖或失效判断。参考仅说明所示历史证据，不能视为实时行情。
- 改变判断前须核验个人条件、当前申赎状态/限额/费用/确认与到账规则，并在新有效净值或证据补齐后重新核对。新闻与历史持仓不是交易触发器，未建立基金穿透或收益归因。

操作参考在AI调用前冻结，独立保存在JSON的 `action_references`，`method=evidence_boundary_v1`。该方法是证据边界提示，不是买卖策略。模型不接收或改写这些卡片，不接收任何新增个人背景；关闭AI、单只/横向拒绝、恶意模型买卖文本或模型故障都不改变程序参考。现有严格交易词与其他AI校验保持不变。纯函数只接收公开基金分析与截止日期，本版本不收集或保存个人持仓、成本、期限、风险偏好。

快照新增 `action_references` 并升至 **schema 4**，原有 `analyses`、`failed_codes`、新闻、AI逐基金/横向状态及 `notification` 保留。按基金代码索引的参考包含状态、操作标签、适用情形、日期、指标证据、固定原因、后续核验条件和边界。只接受schema 3的外部读取脚本须显式接受4，并区分上述两种状态；没有新增Web/API消费端、配置项、股票行为、定时或通知路由。

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
- 报告保存为 `reports/fund_report_<UTC时间戳>.md`，同名 JSON 保存独立基金身份、资料、净值历史、指标、来源、抓取时间、新闻和失败项。JSON `schema_version=4` 保留 `news_evidence`（来源/关系/时效/覆盖）及 `interpretation`（状态、实际模型、事实引用、单基金解读和横向观察），保留原 `news` 文本；增加 `interpretation.fund_statuses`、`comparison_status` 及事实的 `allowed_limitations`，状态枚举增加 `partial`。读取快照的外部脚本需接受新版本及状态；没有 Web/API 消费端变更。不会写入股票 `StockDaily` 或分析历史表。
- 一只基金数据获取或校验失败仍保存其他基金及失败原因；存在此类数据失败时 CLI 返回 1（AI单基金拒绝或 `partial` 不属于此类数据失败）。通知全部/部分失败、无可用通知渠道或结果不确定也返回 1；主动禁用通知和配置中的降噪抑制不视为失败。
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

一批基金一次请求，生成每只核心结论、风险、证据、后续观察及横向观察。程序先计算事实，模型以事实ID占位引用数字、日期、代码，另行撰写中文定性解释。引用、结构、基金归属、数量词、方向和当前持仓等经过保守检查。响应解析、全局结构与基金身份不完整或不一致时仍整批拒绝；通过全局检查后，每只基金独立校验，单只失败只弃用该基金全部AI章节，其余有效观点保留。横向观察独立全有或全无，任何横向语句失败会弃用全部横向观察，不丢弃已通过的单基金观点；引用已拒绝基金的事实（包括包含该基金的比较事实）也会拒绝。确定性数据始终保留。自然语言检查不是事实证明，仍可能遗漏量化/时态错误，也可能拒绝合理文字；报告明确标记AI观点，关键判断应回查原始证据。

### 缺失证据与限制措辞契约

程序根据实际输入生成 `missing_holdings`（`F<code>:holdings`）及 `missing_allocation`（`F<code>:allocation`）事实。缺少可用股票明细不代表空仓；没有可用、带报告期的行业配置资料不代表没有行业投资或比例为零。本次检索缺失不能证明管理人尚未披露，因此“持仓数据暂未披露”“当前持仓数据未披露”不获许可，可改用有据的“本次未取得可用股票持仓数据”。缺行业配置与缺股票持仓分别判断，基金类型、净值、新闻和笼统质量警告不能替代这些证据。

事实的 `allowed_limitations` 只由程序设置，不从来源文字、新闻标题或模型返回中推导授权。涉及持仓/敞口限制的自由文字必须是所引用事实明确允许的完整短句；同一语句涉及的所有基金都需有相应事实支持。例如缺持仓事实允许“未取得可用股票持仓数据”“持仓缺失不代表空仓”，缺行业配置事实允许“行业配置数据不可用”。目标ETF身份关系没有可用于自由文字配置断言的带日期快照，不能借股票或行业资料日期背书目标ETF配置/权重/敞口；仅允许完整事实占位符呈现已知资料。目标ETF身份已核验且比例确实缺失时才允许“目标ETF配置比例未提供”，不能据此声称缺少股票持仓或当前配置已核验。

不在允许短句清单内的措辞仍可能被保守拒绝，建议直接引用程序事实占位符（例如 `{{F001672:holdings}}`），由程序展开原始边界说明。占位符同时按去除后的连续文字与分句边界两种视图检查，既不能拆开禁词/当前性，也不能拼出许可缺失短句。允许的缺失短句不是通用豁免：附加当前配置、暴露、空仓、排名、收益归因或外部数量/日期断言仍受原有检查。历史持仓断言只接受带日期的 `dated_holdings`，行业配置断言只接受带日期的 `dated_allocation`；同分句兼含两种主题则两类证据都必须支持每只引用基金，主题不明的敞口/权重断言不猜测。比较自由文字点名的已知基金必须通过本批单基金校验且被该条证据实际引用，不能仅用成功基金引用掩盖失败基金名称。当前性、跨基金归属、可比性和数量词护栏没有放宽。

### 风险免责声明与禁止表达诊断

禁止词仍按保守词法匹配，不因“不、不能、并非”等否定前缀豁免。仅五个程序定义的完整 `text` 模板例外，须引用对应类型的已提供事实；引用涉及多只基金时每只基金都需支持。模板通过只表示边界说明符合有限契约，不是程序认证投资结论。

- `metric`（存在有限数值，不能是 `unavailable_metric`）：`历史净值表现不保证收益，仍需审慎评估风险。`、`历史净值表现不保证回报，仍需审慎评估风险。`、`历史净值表现不构成收益预测，仍需审慎评估风险。`
- `dated_holdings`、`dated_allocation` 或 `target_relationship`：`来源关联不构成收益归因，仍需独立核验。`
- `news_lead`：`关联线索不构成收益归因，仍需独立核验。`，保留“线索”的新闻不确定性要求。

模板通过事实的 `allowed_limitations` 提供给模型，但与持仓缺失短句不同，风险模板必须独占整条 `text`，不允许任何其他分句、前后缀或占位符。仅沿用NFKC及可见空白归一化，不忽略标点。自由文字在原始与NFKC规范化两个视图中均拒绝Unicode类别C、M（组合标记/变体选择符）及Zl/Zp（行/段分隔符），以 `control_character` 报告；不能用不可见格式拆开禁词或把多行文字压成许可。比如 `不保证{{证据ID}}收益` 不能经删除占位符拼成许可。模板不能用于要求事实占位符的 `evidence` 节；横向模板仍须通过跨基金归属、被拒基金隔离及指标可比性校验。所有其他数字、日期、身份、新闻、方向和敞口检查不跳过。

合法同义句如“历史业绩不保证收益，应继续核验资料”仍可能被拒；本次不尝试通用中文否定理解。“风险较高/较低”等比较性用语也仍拒绝，不能仅凭风险免责声明就认定其有比较证据。需要这类边界说明时使用上述完整模板；其他内容改用不含禁止词的审慎表达，并引用正确事实。

`narrative/unsafe_claim` 保持主错误码兼容，新增可选 `claim_category`：`relative_time`（相对日期）、`rating`（评分）、`trading_or_forecast`（交易或收益预测）、`guarantee`（保证性表达）、`causal_attribution`（因果/归因）、`ranking_or_comparison`（排名/比较）。它表示首个被命中的词法类别，不说明原句必然为肯定断言，也不列出全部风险。更早的数字、控制字符或引用校验可以先阻断而没有该类别。只保存这些固定枚举，不保存命中文本、原句或未知引用ID；旧诊断没有类别时不补猜。

Run8 的018123与025500只留存了 `conclusion[0]` 的 `unsafe_claim`、字符数与引用数量，失败原句和具体引用ID不可用，不能确定是误拒，也不能推断命中类别。本次使用这两只基金的实际公开来源输入投影及明确标记的合成文本进行离线回归，非真实模型输出重放；未调用模型、搜索或SMTP，未触发Actions。

### 行业数词与历史分句诊断

`批发和零售业` 是本次唯一新增的完整非数量行业术语，不泛化豁免“零”或任意来源名称。程序仅在已校验行业资料的原始结构化 `asset.name` 精确等于该术语时建立内部许可；必须实际引用该条 `dated_allocation`，日期合法且覆盖该句每只引用基金。其他行业、股票持仓、ETF关系、净值资料、来源备注或伪造展示格式都不能提供该许可。内部许可不写入模型事实、提示词载荷或JSON；仅从JSON重新加载的普通事实映射无此许可，须由原始结构化输入重建，缺失时保守拒绝。

术语只在数词检查视图中按原位置掩码，先于其他非数量词处理；`批发和一并零售业`、用事实占位符拆开术语、术语旁附加数字/比例都不能借此通过。实际文字仍经过全部引用、数量词、Unicode双视图、禁止表达、历史/当前性、主题、方向、新闻及比较门控。来源里出现其他含数词的公司或行业名称不会生成新的许可。检查不是行业事实语义认证，不能保证其他自由定性判断与原资料一致。

历史限定仍不得跨分句或占位符借用。ASCII点号也作为分句边界，因此全角点号和NFKC后成为点号的省略号同样分隔；原有中文标点规则保留。该规则只检查占位符之外的自由文字，不拆开事实ID，也不改变程序展开的日期、小数或报告内容。泛化语句如“投资者应关注持仓风险，并结合资料评估不确定性”仍可能保守拒绝；本次未增加通用风险语义解析或新的风险整句模板。可独立使用被引事实已许可的“应核验持仓资料的适用时点”，或用“可核对程序提供的历史资料：{{证据ID}}”呈现原始依据。其他普通含数词词语，如“一方面”“一贯”，仍可能被拒。

主错误码保持兼容，仅增加可选安全诊断：

- `detection_view`：`joined`（移除占位符的连续文字）或 `boundary`（占位符作为分句边界）；仅在这两次自由文字检查中产生。
- `chinese_numeral` 的 `guard_reason=unapproved_numeral`，只表示仍有未获许可的中文数词字符，不区分真实数量或未支持的普通词语。
- `historical_evidence_missing` 的 `guard_reason`：`target_not_snapshot`、`topic_unspecified`、`dated_fact_missing`、`historical_marker_missing`，依次对应目标关系不是配置快照、主题不明确、缺少同范围同主题有效日期事实、该分句没有历史限定。
- 同错误可附 `exposure_topic=target/unspecified/holdings/allocation/mixed`、从零开始且不超过600的 `clause_index`；仅 `dated_fact_missing` 可附 `missing_fact_kind=dated_holdings/dated_allocation/both`。分句索引包含连续分隔符形成的空段，不是原文字符偏移。

这些字段穿过错误包装、单基金/横向范围、最终JSON、Markdown及日志的白名单清洗；不保存原句、命中词、未知引用ID或异常内容。仍只报告各范围首个阻断，不说明后续检查已通过，旧记录不补猜新字段。schema 4、操作参考卡片、五个既有完整风险模板、缺失短句、逐基金隔离与所有事实门控保持不变。

Run9 的001672与025500只保留 `evidence[0]` 的 `historical_evidence_missing` / `chinese_numeral` 以及安全计数；失败原句及实际引用ID不可用，无法确定真实原因或证明这两条失败已经解决。本次回归使用真实公开输入最小投影、真实留存的018123已接受渲染结果及明确标记的合成案例。025500的实际资料包含该完整行业名称，不能据此推断它的失败原句使用了此词。没有重放真实模型输出，也未调用模型、新闻、SMTP或Actions。

横向表只并列净值日期与真实指标区间，不评分或排名。只有全体币种已核验且相同、净值日期相同、指标有效、实际起止区间相同时，才允许对应指标的跨基金数值观察。QDII时差、未知币种、分红或短历史不得靠模型补齐。

状态 `ok/partial/disabled/unavailable/model_failed` 均有原因：`ok` 表示全部单基金及必需横向观察通过；`partial` 表示至少一只基金通过，但其他基金或横向观察被拒绝；没有可采用的单基金观点时为 `model_failed`。`fund_statuses` 为每只进入模型输入的基金保存 `status/reason/diagnostics`，数据抓取失败仍单列于 `failed_codes`；`comparison_status` 同样记录横向状态，单基金正常响应为 `not_applicable`。关闭、不可用或整批失败时各范围也显式记录原因。报告显示整体、各基金及横向状态，部分成功时仅显示通过的模型结论，不再声称全部结论均不可用。

`interpretation.diagnostics` 及各范围的 `diagnostics` 在JSON、报告和标准日志中保留白名单诊断：固定阶段/错误码、已确认基金代码、章节及从零开始的位置索引，另有字符数、字节数、引用数或JSON行列等计数。诊断不保存模型原文、证据文本、未知字段、未知引用ID、密钥或供应商异常字符串；未知诊断字段丢弃。每个失败范围仅报告首个阻断校验，不表示其他校验已经通过；全局失败不尝试局部恢复。报告边界再次清洗每个范围的诊断。单次调用、通知路由、发送次数和CLI退出语义不变，不追加模型调用或自动发送。

仅凭HTTP成功不代表解读通过。此前只保留统一校验错误的报告不能回溯具体子校验原因；新诊断只适用于应用补丁后的运行。无配置、关闭、本地后端不允许、模型报错/超时或输出无效不冒充GPT。AI降级本身不使CLI退出失败，仍可按既有通知配置发送明确降级的数据报告，运营检查JSON `interpretation.status`。基金程序API `run_fund_analysis` 默认保持解读关闭以兼容调用，CLI显式开启；历史截止日不混入当前新闻/AI判断。

最多二十只基金、提示词一百五十KB、输出四十八KB、输出token上限六千。默认报告等待四十五秒，单进程只有一个未结束请求。等待期限不是网络硬取消：已有云端请求或配置回退可能继续至底层超时并产生费用；迟到结果不采用，也不补发报告或邮件。

## 已知范围限制

- 支持CLI与GitHub Actions手动模式、本地JSON快照；未启用基金定时，也未接入 Web/API、股票导入、机器人、Agent工具或原股票回测。
- 未实现ETF穿透、独立管理人公告核对、真实披露时间、合同基准/未来变更、申赎限制、费率表、交易执行或个性化组合分析。
- 货币基金不是普通单位净值模型，明确拒绝。
- 没有基金专用持久数据库、跨运行增量缓存、通知跨运行去重保证。每次运行是独立报告；不要把本地存在报告等同于通知送达。

## 验证与部署注意

```bash
python -m pytest tests/test_fund_action_reference.py tests/test_fund_analysis.py tests/test_fund_fetcher.py tests/test_fund_analysis_service.py tests/test_fund_news_service.py tests/test_fund_interpretation.py tests/test_fund_interpretation_isolation.py tests/test_fund_claim_guard.py tests/test_main_funds.py tests/test_fund_config.py tests/test_daily_fund_workflow.py tests/test_daily_dependencies.py tests/test_fund_dependency_isolation.py
./scripts/ci_gate.sh
```

首次数据接口验收使用 `--no-notify --fund-no-news --fund-no-ai`，只有确认通知配置后再开启推送。指标测试使用合成数据，不用某天行情代替算法预期。缺失证据与部分成功回归另外保留 Run7 的021190去敏输入投影（仅最近两个净值点、原已计算指标及必要持仓关系），用确定性合成叙述和模拟模型边界，未重新请求历史 Run7 的真实模型输出，也不声称复现其原始文本。

现有项目配置在检测到 `HTTP_PROXY` 时会把 `eastmoney.com` 加进 `NO_PROXY`。如果部署环境只能通过HTTPS代理出站，可能出现“单独接口成功、main入口失败”；应按环境需求配置代理（例如仅配置所需 `HTTPS_PROXY`，避免触发该既有直连规则）。本次改造未改动全项目代理策略，也不会关闭TLS校验。

## English summary

Program-generated action references now precede data and AI sections. They are non-personal evidence-boundary guidance, not a backtested timing strategy. `conditional / observe` means available one-/three-month historical NAV evidence still cannot determine a personal trade: someone not holding should not initiate a purchase based on this report, and whether a holder should sell remains undetermined. Observation does not imply that continuing to hold is safe. `unavailable / undetermined` distinguishes missing or invalid required evidence and isolated reference-component failures. Return signs, drawdown and calendar-day age never map to buy/sell decisions. Full-year drawdown can remain N/A without inventing an annual statistic; available maximum drawdown is historical worst peak-to-trough decline, not current decline or personal loss. Each card shows actual evidence dates, source and unknown currency explicitly. Unknown currency alone does not erase valid within-fund percentage history, but no monetary conversion or cross-fund ranking is made. Personal exit conditions, funding deadlines, risk limits and current dealing terms must be checked before a personal decision; the report does not claim those conditions were triggered. This version collects no personal inputs. References are frozen before model work, never sent to or overwritten by the model, and retain the same action across model states. Strict model trading guards, stock behavior, workflow and notification semantics remain unchanged.

This is an opt-in CLI and manual GitHub Actions MVP for published off-exchange fund NAV reports. Use `--funds` (optionally reading `FUND_LIST`), never `STOCK_LIST`. Manual `funds-only` dispatch accepts a one-time `fund_codes` override; fund reruns suppress notifications. No fund cron is added or enabled, and the original stock schedule is unchanged. Existing stock/ETF routes remain unchanged. Run with `--no-notify --fund-no-news --fund-no-ai` for safe live validation. It saves separate Markdown/JSON artifacts, validates identity and dates, and reuses existing news and notification services. Direct news requires full fund/share-class headline identity. Related news requires verified ETF identities or dated holdings/industry relationships, issuer/sector context and freshness metadata; strict matching reduces recall. Only fresh news enters model evidence. Return metrics are conservative event-free unit-NAV changes, not a claimed reinvested total-return series. Distributions, missing data and insufficient history produce N/A. QDII publication lags and dated holdings are explicit. Web/API/scheduling, look-through, subscriptions/redemptions, trading and backtesting are out of scope. Fund profile failures use one identity-checked same-provider homepage fallback, preserving diagnostics and missing-field warnings. All data currently comes from the same Eastmoney source family.

The fund CLI now attempts evidence-cited cloud narrative through the existing configured model interface; use `--fund-no-ai` or manual workflow `fund_no_ai` to disable it. Structured numeric/date placeholders are program-owned. Conservative free-prose checks do not guarantee semantic truth or eliminate every quantitative/temporal error. Actual model/provider identity and explicit degradation states are reported. No local CLI backend or private/plain-HTTP model endpoint is allowed. Endpoint validation does not resolve DNS. The report deadline is not hard cancellation of an in-flight cloud request. Model/provider-boundary mocks do not constitute live API validation. Validation diagnostics now use finite stage/reason codes, validated positions and bounded counts without retaining model output or provider exception text. Legacy generic failures cannot be retroactively attributed. Quote/profile pages and mere mentions in another fund’s portfolio are not issuer events; broad-sector background is separately labelled and excluded from model evidence. News freshness alone is not relevance or event verification.

Only an exact manual `workflow_dispatch` / `funds-only` run excludes `fxmacrodata-public-client` from a temporary requirements file. The original requirements, stock/market/scheduled installs, pip upgrade and Futu smoke check are unchanged. All remaining bytes and relative include paths are preserved; other pip failures still fail the job. Missing, duplicate or continued macro entries fail explicitly, and the temporary file is cleaned up. No dependency source, TLS or hash-verification setting is changed. Full stock installs remain exposed to the original macro wheel HTTP 504 failure. Removing the package also removes its transitive dependency constraints, so resolver output is not guaranteed identical. Local Python 3.12 offline checks do not validate a clean Actions Python 3.11 install; that install has not been run.

Snapshot schema 4 preserves all prior fields, including per-fund `fund_statuses`, `comparison_status`, and program-owned fact `allowed_limitations` introduced in schema 3, and adds the independent `action_references` map. Consumers that only accept schema 3 must explicitly support version 4. Consumers must handle the new `partial` aggregate state. Global JSON, shape and identity failures still reject the entire response. After those checks, each fund is accepted or rejected as a whole, and comparison text is independently all-or-nothing. Rejected funds cannot support comparisons, including through shared comparison facts. `ok` requires all requested fund scopes and required comparisons to pass; `partial` requires at least one accepted fund with another failed scope; no accepted fund means `model_failed`. Single-fund comparisons are `not_applicable` on normal responses. Reports retain all deterministic data, show each scope's reason and sanitized diagnostics, and display only accepted insights. Scope diagnostics are sanitized again before rendering, saving and logging. Notification count, report routing and CLI exit semantics remain unchanged; AI degradation alone is not a CLI failure. No Web/API changes are involved.

Missing holdings and missing dated industry allocation are separate program-generated facts. Exposure-limit prose must match a complete allowed clause on cited facts for every referenced fund; arbitrary source text cannot create permissions. Missing target-ETF weight permits only its specific weight limitation, not claims of absent holdings or verified current allocation. Unsupported wording should use the program-owned fact placeholder instead. Missing evidence never means zero exposure, and attaching a permitted clause does not exempt current-exposure, numeric, temporal, attribution, ranking or comparison checks. Offline regression and mocked provider responses are not a replay of the real Run7 model output.

Dated holdings cannot substitute for dated industry allocation, or vice versa. Mixed exposure topics require both evidence kinds for every cited fund, and unspecified exposure topics are rejected. Known fund names in comparison prose must refer to accepted and actually cited funds. Input coverage gaps do not prove that the manager has not disclosed holdings; use the supported input-absence clause rather than claiming non-disclosure.

Target-ETF identity facts do not support free-prose ETF exposure/weight claims and cannot borrow dates from stock holdings or industry allocations. Use full program fact slots for known relationship data. Both joined and boundary views of placeholders are checked: slots cannot split unsafe words/currentness or manufacture permitted absence clauses.

The unsafe-claim guard now permits only five exact, program-owned whole-text risk disclaimers supported by the cited fact kinds for every referenced fund. Available numeric metrics support historical-performance disclaimers; unavailable metrics do not. Dated holdings/allocation or verified target relationships support the source-association disclaimer, while news requires the separate qualified “lead” template. No prefix, suffix, additional clause or placeholder is allowed. NFKC and visible-space normalization are retained. Unicode categories C, M and Zl/Zp are rejected in both the original and NFKC-normalized views as control_character, preventing combining marks/variation selectors from splitting forbidden tokens or line separators from being compacted into a template; all other validation remains checked. Unsupported safe synonyms may still fail closed. `unsafe_claim` adds the optional finite `claim_category` enum within the preserved interpretation contract; it reports a lexical match category, not a semantic conviction. Run8's two rejected texts and citation IDs were not retained, so their exact causes cannot be reconstructed. The new fixture preserves public Run8 inputs and old safe diagnostics; all new model narratives are explicitly synthetic offline examples.

The Run9 guard repair adds exactly one bounded non-quantitative industry term, `批发和零售业` (wholesale and retail). Permission is registered only when the validated structured industry `asset.name` equals that entire fixed term. The statement must cite that actual dated-allocation fact for every referenced fund; another industry, holdings, ETF identity, NAV, source notes, or forged display-format text cannot grant permission. Private permission metadata is not a fact, prompt-payload, or JSON field. Plain deserialized fact mappings fail closed unless rebuilt from the original structured inputs. The term is masked in place before existing lexical stripping, only in the numeral-detection view. Spliced words, placeholders, extra numerals, proportions and Unicode concealment still fail; all downstream fact/citation/currentness/topic/claim/direction/news/comparison gates remain active. This does not certify arbitrary narrative claims against the underlying source.

ASCII periods now end exposure clauses, including fullwidth periods and ellipses after NFKC normalization. Historical qualifiers cannot leak into the next sentence. This operates only on free prose, not program-rendered decimals/dates or placeholder IDs. Generic exposure-risk statements and unsupported ordinary numeral-containing words such as “一方面” and “一贯” remain conservatively rejected. No general negation exception or new risk template is added. The prompt offers existing supported limitation clauses or a neutral introduction followed by a complete fact placeholder instead.

Optional safe diagnostics preserve the original primary codes: `detection_view=joined/boundary`; `guard_reason=unapproved_numeral` for `chinese_numeral`; and `target_not_snapshot/topic_unspecified/dated_fact_missing/historical_marker_missing` for `historical_evidence_missing`. The latter may include `exposure_topic=target/unspecified/holdings/allocation/mixed`, bounded zero-based `clause_index`, and, only for missing dated facts, `missing_fact_kind=dated_holdings/dated_allocation/both`. Clause indices include empty segments from consecutive delimiters. Finite typed allowlists cover rewrapping, per-fund and comparison isolation, Markdown, JSON and logs. No original text, matched word, unknown reference ID or exception is retained; old diagnostics are not enriched by guessing. Schema 4, action references, all five existing complete risk templates, missing-data clauses and first-failure reporting are unchanged.

Run9 did not retain the rejected 001672/025500 text or actual citation IDs. Their real causes cannot be reconstructed or claimed resolved. The fixture projects real public inputs, retains real accepted 018123 rendered statements, and explicitly labels new narratives as synthetic. Inverse fact substitution creates derived validation examples, not recovered model output. The observed retail industry name motivates a reproducible defect case but does not prove that name appeared in the rejected sentence. Validation is offline only, with no model, news, SMTP or Actions calls.
