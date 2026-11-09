"""Published-NAV facts with separately labelled evidence-cited cloud interpretation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import html
import json
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from src.core.fund_analysis import FundAnalysis, analyze_fund, parse_fund_codes
from src.core.fund_action_reference import (
    FundActionReference, REASON_LABELS as ACTION_REASON_LABELS, build_action_reference, unavailable_reference,
)
from src.services.fund_interpretation import (
    FundInterpretation, InterpretationScopeStatus, interpret_funds, safe_interpretation_diagnostics,
)
from src.services.fund_news_service import FundNewsBundle, REASON_LABELS, gather_fund_news

logger = logging.getLogger(__name__)

METRIC_LABELS = {
    "return_1d": "相邻净值日收益",
    "return_1m": "近1个月收益",
    "return_3m": "近3个月收益",
    "return_6m": "近6个月收益",
    "return_12m": "近12个月收益",
    "return_ytd": "今年以来收益",
    "max_drawdown_12m": "近12个月最大回撤",
}


@dataclass
class FundReport:
    markdown: str
    analyses: List[FundAnalysis] = field(default_factory=list)
    failed_codes: Dict[str, str] = field(default_factory=dict)
    report_path: Optional[str] = None
    snapshot_path: Optional[str] = None
    notification_status: str = "disabled"
    interpretation_status: str = "disabled"
    interpretation: Optional[FundInterpretation] = None
    action_references: Dict[str, FundActionReference] = field(default_factory=dict)


def _text(value: object) -> str:
    """Keep external names/news readable without accepting HTML or Markdown."""
    text = html.escape(" ".join(str(value).split()), quote=True)
    return re.sub(r"([\\`*_\[\]#|])", r"\\\1", text)


def _link(label: str, url: str) -> str:
    parts = urlsplit(str(url))
    if parts.scheme not in {"https", "http"} or not parts.netloc or parts.username or parts.password:
        return _text(label)
    return f"[{_text(label)}]({quote(url, safe=':/?=&%+,-._~')})"


NEWS_LABELS = {
    "announcement": "基金公告", "direct_fund": "基金新闻", "target_etf": "目标ETF",
    "holding": "已披露重仓股", "industry": "已披露行业", "theme": "产品名称主题",
}
DATE_LABELS = {"fresh": "日期在本次新闻窗口内", "stale": "已过时效窗口", "unknown": "日期未核验", "future": "未来日期，未作为近期证据"}
INSIGHT_LABELS = {"conclusion": "核心结论", "risks": "风险提示", "evidence": "证据", "observations": "后续观察"}


def _news_reason_counts(coverage) -> dict[str, int]:
    """Project only fixed outcome codes and bounded counts into diagnostics."""
    values = getattr(coverage, "reason_counts", {})
    if not isinstance(values, dict):
        return {}
    return {key: values[key] for key in REASON_LABELS
            if type(values.get(key)) is int and 0 <= values[key] <= 100}


def _news_lines(bundle: FundNewsBundle) -> List[str]:
    lines, backgrounds = [], []
    for item in bundle.items:
        scope = getattr(item, "evidence_scope", "weak_background")
        label = {"fund_event": "基金事件线索", "related_event": "关联对象事件线索",
                 "sector_context": "具体行业/主题背景", "weak_background": "宽泛行业弱背景"}.get(scope, "未分类线索")
        eligibility = ("可作为待核验线索，不代表事件已证实" if item.model_eligible is True
                       else "不作为模型事件证据")
        line = (
            f"【{NEWS_LABELS.get(item.category, '相关线索')} / {label}】{_link(item.title, item.url)}；"
            f"来源：{_text(item.source)}；发布日期：{item.published_date or '未提供'}；"
            f"{DATE_LABELS.get(item.date_status, '日期未核验')}；{eligibility}；关联依据：{_text(item.relation_basis)}"
            + (f"；{_link('关联资料来源', item.source_url)}。" if item.source_url else "。")
        )
        (backgrounds if scope == "weak_background" else lines).append(line)
    if not lines:
        lines.append("未找到通过本基金身份匹配或已核验关联筛选的近期事件新闻；不代表没有相关事件。")
    if backgrounds:
        lines.append("以下仅为宽泛背景，与本基金具体事件关联不足，不纳入模型事件证据：")
        lines.extend(backgrounds)
    for category, coverage in bundle.coverage.items():
        if coverage.queries or coverage.status in {"not_configured", "no_identity", "timeout", "unavailable", "skipped_budget"}:
            lines.append(f"{NEWS_LABELS.get(category, category)}检索覆盖：{_text(coverage.detail or coverage.status)}")
        counts = _news_reason_counts(coverage)
        if counts:
            lines.append(f"{NEWS_LABELS.get(category, category)}筛选统计：" + "；".join(f"{REASON_LABELS[key]} {value}条" for key, value in counts.items()))
    lines.extend(_text(warning) for warning in bundle.warnings)
    return list(dict.fromkeys(lines))


def _fund_news(data, search_service) -> List[str]:
    """Compatibility text projection; structured evidence is kept in snapshots."""
    return _news_lines(gather_fund_news(data, search_service))


def _statement_line(statement, interpretation: FundInterpretation) -> str:
    citations = []
    for evidence_id in statement.evidence_ids:
        fact = interpretation.facts.get(evidence_id)
        if fact is None:
            continue
        label = evidence_id + (f" / {fact.data_date}" if fact.data_date else " / 资料日期未提供")
        citations.append(_link(label, fact.source_url) if fact.source_url else _text(label))
    return f"- {_text(statement.text)}" + ("（证据：" + "；".join(citations) + "）" if citations else "")


def _diagnostic_lines(diagnostics) -> List[str]:
    diagnostic = safe_interpretation_diagnostics(diagnostics)
    if not diagnostic:
        return []
    fields = [f"{key}={value}" for key, value in diagnostic.items() if key != "counts"]
    fields.extend(f"{key}={value}" for key, value in diagnostic.get("counts", {}).items())
    return ["安全诊断（仅固定代码、位置与计数；索引从0起）：" + _text("；".join(fields)) + "。", ""]


def _overview(analyses, interpretation: Optional[FundInterpretation]) -> List[str]:
    lines = ["## 横向数据对照（程序计算，不作排名）", "",
             "各列以各基金已披露日期和实际计算区间为准；日期、币种、区间未对齐时只作并列展示，不能解释为优劣排名。", "",
             "| 基金 | 净值日期 | 近一个月净值变化 | 近十二个月最大回撤 |", "| --- | --- | --- | --- |"]
    for analysis in analyses:
        def display(key):
            metric = analysis.metrics.get(key)
            if metric is None or metric.value is None:
                return "N/A"
            return f"{metric.value * 100:.2f}%（{metric.start_date} 至 {metric.end_date}）"
        profile = analysis.data.profile
        lines.append(f"| {_text(profile.name)}（{profile.code}） | {analysis.data.navs[-1].nav_date} | {display('return_1m')} | {display('max_drawdown_12m')} |")
    lines.append("")
    if interpretation is None:
        return lines
    reasons = list(dict.fromkeys(reason for item in interpretation.comparison_context.get("metrics", {}).values()
                                 for reason in item.get("reasons", [])))
    if reasons:
        lines.extend(["比较限制：" + "；".join(_text(reason) for reason in reasons) + "。", ""])
    lines.extend(["## AI解读状态与横向观察", "",
                  f"状态：{_text(interpretation.status)}；{_text(interpretation.reason)}", ""])
    if interpretation.model or interpretation.provider:
        lines.extend([f"实际模型：{_text(interpretation.model or '未返回')}；供应方：{_text(interpretation.provider or '未返回')}；后端：{_text(interpretation.backend or '未返回')}。", ""])
    lines.extend(_diagnostic_lines(interpretation.diagnostics))
    comparison_status = interpretation.comparison_status
    lines.extend([f"横向观察状态：{_text(comparison_status.status)}；{_text(comparison_status.reason)}", ""])
    lines.extend(_diagnostic_lines(comparison_status.diagnostics))
    if interpretation.status in {"ok", "partial"} and comparison_status.status == "ok":
        lines.extend(_statement_line(statement, interpretation) for statement in interpretation.comparison)
        lines.append("")
    if interpretation.status == "partial":
        lines.extend(["部分AI解读通过校验；仅展示通过的基金观点及横向观察，失败部分保留程序数据与来源。", ""])
    elif interpretation.status != "ok":
        lines.extend(["本报告未生成可采用的模型结论。以下只有程序计算、来源证据与明确的数据边界，不冒充GPT解读。", ""])
    return lines


def _build_action_references(analyses, failed, as_of: date) -> Dict[str, FundActionReference]:
    references = {}
    for analysis in analyses:
        code = analysis.data.profile.code
        try:
            reference = build_action_reference(analysis, as_of=as_of)
            if not isinstance(reference, FundActionReference) or reference.fund_code != code:
                raise ValueError("Invalid action reference contract")
            references[code] = reference
        except Exception:
            # An isolated reference failure must not discard NAVs or AI scopes.
            references[code] = unavailable_reference(code, as_of)
            logger.warning("[基金] %s 操作参考不可用: component_failed", code)
    for code in failed:
        references[code] = unavailable_reference(code, as_of, "nav_unavailable")
    return references


def _reference_percent(value: float) -> str:
    if value != 0 and f"{abs(value) * 100:.2f}" == "0.00":
        return ("正值" if value > 0 else "负值") + "，绝对值不足0.01%"
    return f"{value * 100:.2f}%"


def _action_reference_lines(analyses, references: Dict[str, FundActionReference]) -> List[str]:
    lines = ["## 操作参考（程序生成，非个性化）", "",
             "先看适用情形，再核对证据。历史涨跌只描述方向，不自动映射买入或卖出；这里没有经过回测验证的择时策略。",
             "未收集个人持仓、成本、期限或风险偏好；下列条件尚未核实，不代表已触发。AI关闭、失败或被拒绝不会改变本节。", ""]
    names = {a.data.profile.code: a.data.profile.name for a in analyses}
    sources = {a.data.profile.code: a.data.source_url for a in analyses}
    currencies = {a.data.profile.code: a.data.profile.currency for a in analyses}
    lines.extend(["**全部基金速览**（观望不表示继续持有安全；各自依据与适用情形见下方）", ""])
    for code, reference in references.items():
        label = "观望" if reference.status == "conditional" else "无法判断"
        lines.append(f"- {_text(names.get(code, '数据不可用'))}（fund:{_text(code)}）：**{label}**；"
                     f"净值日期 {reference.nav_date or '不可用'}。")
    lines.append("")
    for code, reference in references.items():
        lines.extend([f"### {_text(names.get(code, '数据不可用'))}（fund:{_text(code)}）", "",
                      f"**{_text(reference.headline)}**", "",
                      f"- {_text(reference.not_held)}", f"- {_text(reference.held)}",
                      f"- 证据日期：净值所属日期 {reference.nav_date or '不可用'}；报告截止日期 {reference.as_of}。"
                      "净值日期不是实际披露时间或成交日期。"])
        lines.append(f"- 币种：{_text(currencies.get(code) or '未核验')}；仅展示本基金历史净值变化，不作金额换算或跨基金优劣比较。")
        for evidence in reference.evidence:
            label = METRIC_LABELS[evidence.metric]
            if evidence.value is None:
                lines.append(f"- {label}：N/A（{_text(evidence.reason or '证据不可用')}）。")
                continue
            direction = {"positive": "区间单位净值上升", "negative": "区间单位净值下降", "flat": "区间单位净值持平"}.get(evidence.direction)
            detail = (f"；{direction}，不直接决定买卖" if direction else
                      "；历史最差峰谷跌幅，非当前回撤或个人亏损，不作为止损阈值")
            lines.append(f"- {label}：{_reference_percent(evidence.value)}"
                         f"（实际区间 {evidence.start_date} 至 {evidence.end_date}）{detail}。")
        if code in sources:
            lines.append(f"- {_link('本基金净值证据来源', sources[code])}；同源资料，未独立复核，不作跨基金优劣排名。")
        for reason in reference.reason_codes:
            lines.append(f"- 判断限制：{_text(ACTION_REASON_LABELS[reason])}")
        lines.extend(["", "改变判断前需要核验："])
        lines.extend(f"- {_text(check)}" for check in reference.next_checks)
        lines.append("")
    lines.extend(["本节不提供实时行情、买卖价格、仓位、目标收益或交易执行。各基金实际币种、费用、净值口径与披露时差以详细资料为准。",
                  "分红/拆分等指标门控继续生效；周末、假期和QDII不按自然日间隔自动判失效或买卖。", ""])
    return lines


def render_fund_report(analyses: List[FundAnalysis], failed: Dict[str, str], news, generated_at: datetime,
                       interpretation: Optional[FundInterpretation] = None, *,
                       action_references: Optional[Dict[str, FundActionReference]] = None,
                       as_of: Optional[date] = None) -> str:
    lines = [
        "# 场外基金已披露净值日报",
        "",
        f"生成时间：{generated_at.isoformat()}；各基金以各自最新已披露净值日期为准。",
        "",
        "仅作数据研究，不是交易指令、评分或收益承诺。未提供个人持有金额或组合权重，不计算个人组合收益。",
        "收益由程序计算，仅在区间未发现分红/拆分且累计净值与单位净值差额稳定时使用净值比值。",
        "遇到分红、拆分、缺数或历史不足显示 N/A，不将累计净值比值冒充总回报。",
        "",
    ]
    references = action_references if action_references is not None else _build_action_references(
        analyses, failed, as_of or generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date(),
    )
    lines.extend(_action_reference_lines(analyses, references))
    lines.extend(_overview(analyses, interpretation))
    for analysis in analyses:
        data = analysis.data
        profile = data.profile
        latest = data.navs[-1]
        lines.extend([
            f"## {_text(profile.name)}（{profile.canonical_id}）",
            "",
            f"- 份额代码：{profile.code}；类型：{_text(profile.fund_type or '未提供')}；"
            f"币种：{_text(profile.currency or '未核验')}。",
            f"- 最新已披露单位净值：{latest.unit_nav:.6f}；净值所属日期：{latest.nav_date.isoformat()}。",
            f"- 净值来源：{_link('东方财富/天天基金', data.source_url)}；抓取时间：{data.fetched_at.isoformat()}；"
            "真实披露时间：未提供（抓取时间不等于披露时间）。",
            f"- 基金资料：{_link('资料来源', profile.source_url)}；基金经理：{_text(profile.manager or '未提供')}；"
            f"成立日期：{profile.inception_date or '未提供'}。",
        ])
        if interpretation is not None:
            scope = interpretation.fund_statuses.get(profile.code)
            if scope is not None:
                lines.extend(["", f"AI基金解读状态：{_text(scope.status)}；{_text(scope.reason)}", ""])
                lines.extend(_diagnostic_lines(scope.diagnostics))
            insight = interpretation.funds.get(profile.code)
            if (interpretation.status in {"ok", "partial"} and insight is not None
                    and scope is not None and scope.status == "ok"):
                lines.extend(["", "### AI基金解读（推理观点，非事实认证）", ""])
                for key, label in INSIGHT_LABELS.items():
                    lines.append(f"**{label}**")
                    lines.extend(_statement_line(statement, interpretation) for statement in getattr(insight, key))
                lines.append("")
        if latest.accumulated_nav is not None:
            lines.append(f"- 累计净值：{latest.accumulated_nav:.6f}（不用于直接计算总回报）。")
        for key, label in METRIC_LABELS.items():
            metric = analysis.metrics[key]
            if metric.value is None:
                lines.append(f"- {label}：N/A（{_text(metric.reason or '数据不足')}）。")
            else:
                lines.append(
                    f"- {label}：{metric.value * 100:.2f}%"
                    f"（实际区间 {metric.start_date} 至 {metric.end_date}）。"
                )
        lines.extend(["", "### 最新披露持仓", ""])
        portfolio = data.holdings
        if portfolio is None or not portfolio.holdings:
            lines.append("持仓数据不可用或该接口未覆盖；不代表空仓。")
        else:
            lines.append(
                f"报告期末：{portfolio.report_period_end}；真实披露日期：{portfolio.published_at or '未提供'}；"
                f"{_link('持仓来源', portfolio.source_url)}。只展示已披露股票，不代表今天持仓。"
            )
            for holding in portfolio.holdings[:10]:
                lines.append(f"- {_text(holding.name)}（{_text(holding.code)}）：占基金净值 {holding.weight_pct:.2f}%。")
        if "联接" in profile.name:
            lines.append("这是ETF联接基金：股票持仓表不等于目标ETF或底层完整持仓；目标ETF身份以另列来源为准，未进行穿透和收益归因。")
        related = [asset for asset in getattr(data, "related_assets", []) if asset.verified]
        if related:
            lines.extend(["", "### 已核验关联资料", ""])
            for asset in related:
                suffix = f"；占基金净值 {asset.weight_pct:.2f}%" if asset.weight_pct is not None else ""
                lines.append(f"- {NEWS_LABELS.get(asset.category, asset.category)}：{_text(asset.name)}"
                             f"（{_text(asset.code or '未提供代码')}）；报告期/关系日期：{asset.source_date or '未知'}"
                             f"；真实披露日期：{asset.published_at or '未知'}{suffix}；{_link('关联来源', asset.source_url)}"
                             + (f"；{_link('关联标的身份', asset.identity_url)}" if asset.identity_url else "") + "。")
        lines.extend(["", "### 数据质量与边界", ""])
        for warning in dict.fromkeys([*data.warnings, *analysis.warnings]):
            lines.append(f"- {_text(warning)}")
        lines.append("- 不提供盘中估值、成交量、盘口、个股买卖点或未经验证的评级。")
        lines.extend(["", "### 基金相关新闻", ""])
        lines.extend(f"- {line}" for line in news.get(profile.code, ["本次未启用新闻检索。 "]))
        lines.append("")
    if failed:
        lines.extend(["## 数据获取或校验失败", ""])
        lines.extend(f"- fund:{code}：{_text(reason)}" for code, reason in failed.items())
        lines.append("")
    lines.append("当前版本不支持交易执行、历史时点回测、合同基准超额收益、持仓穿透或申赎规则判断。")
    return "\n".join(lines) + "\n"


def run_fund_analysis(
    codes,
    *,
    send_notification: bool = False,
    include_news: bool = True,
    include_interpretation: bool = False,
    interpreter=None,
    fetcher=None,
    notifier=None,
    search_service=None,
    as_of: Optional[date] = None,
    output_dir=None,
) -> FundReport:
    """Save an isolated fund snapshot and report; optionally reuse notifications.

    One fund/news failure does not discard other funds. Notifications are
    attempted once, never retried here, and never routed by ambiguous stock codes.
    """
    fund_codes = parse_fund_codes(codes)
    if fetcher is None:
        from data_provider.fund_fetcher import FundFetcher
        fetcher = FundFetcher()
    if include_news and search_service is None:
        try:
            from src.search_service import get_search_service
            search_service = get_search_service()
        except Exception:
            logger.warning("[基金] 新闻服务不可用: news_initialization_failed")
    generated_at = datetime.now(timezone.utc)
    end_day = as_of or generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    analyses, failed, news, news_evidence = [], {}, {}, {}
    historical = as_of is not None and as_of < generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    for code in fund_codes:
        try:
            data = fetcher.fetch(code, as_of=as_of) if as_of is not None else fetcher.fetch(code)
            if data.profile.code != code:
                raise ValueError(f"返回基金代码 {data.profile.code} 与请求 {code} 不一致")
            analysis = analyze_fund(data, as_of=end_day)
            analyses.append(analysis)
            if historical:
                news[code] = ["历史截止日分析不加入当前新闻，避免前视信息。"]
            elif include_news:
                try:
                    bundle = gather_fund_news(analysis.data, search_service, as_of=end_day)
                    for category, coverage in bundle.coverage.items():
                        coverage.reason_counts = _news_reason_counts(coverage)
                        if category in NEWS_LABELS and coverage.reason_counts:
                            logger.info("[基金] %s 新闻筛选安全诊断 %s: %s", code, category,
                                        json.dumps(coverage.reason_counts, sort_keys=True))
                    news_evidence[code] = asdict(bundle)
                    news[code] = _news_lines(bundle)
                except Exception:
                    logger.warning("[基金] 新闻组件异常，保留净值: news_component_failed")
                    news[code] = ["新闻组件不可用；未核验近期事件，不影响净值数据。"]
                    news_evidence[code] = {"items": [], "coverage": {}, "warnings": news[code]}
            else:
                news[code] = ["本次未启用新闻检索。"]
        except Exception as exc:
            failed[code] = str(exc) or type(exc).__name__
            logger.warning("[基金] %s 获取或校验失败: %s", code, failed[code])
    # Freeze references before any model work; model text and statuses are not inputs.
    action_references = _build_action_references(analyses, failed, end_day)
    try:
        interpretation = (interpreter or interpret_funds)(
            analyses, news_evidence, enabled=include_interpretation and not historical,
        )
        if not isinstance(interpretation, FundInterpretation):
            raise TypeError("Invalid interpretation contract")
        if historical and include_interpretation:
            interpretation.reason = "历史截止日不混入当前资料和新闻形成AI判断；仅展示程序数据。"
    except Exception:
        interpretation = FundInterpretation(
            "model_failed", "解读组件不可用，保留程序数据与来源；未采用模型结论。",
            diagnostics={"stage": "component", "code": "component_failed"},
        )
    # Complete scopes for component failures and legacy injected interpreters.
    # Only requested, successfully fetched identities may enter the report contract.
    legacy_scopes = not interpretation.fund_statuses
    interpretation.fund_statuses = {
        analysis.data.profile.code: interpretation.fund_statuses.get(
            analysis.data.profile.code,
            InterpretationScopeStatus(
                "ok" if interpretation.status in {"ok", "partial"}
                and analysis.data.profile.code in interpretation.funds else
                (interpretation.status if interpretation.status in {"disabled", "unavailable"} else "model_failed"),
                interpretation.reason, interpretation.diagnostics,
            ),
        ) for analysis in analyses
    }
    if legacy_scopes:
        interpretation.comparison_status = InterpretationScopeStatus(
            ("not_applicable" if len(analyses) == 1 else "ok") if interpretation.status == "ok" else
            (interpretation.status if interpretation.status in {"disabled", "unavailable"} else "model_failed"),
            interpretation.reason, interpretation.diagnostics,
        )
    for code, scope in [*interpretation.fund_statuses.items(), ("comparison", interpretation.comparison_status)]:
        scope.diagnostics = safe_interpretation_diagnostics(scope.diagnostics)
        if scope.status == "model_failed" and scope.diagnostics:
            logger.info("[基金] 模型解读范围安全诊断 %s: %s", code,
                        json.dumps(scope.diagnostics, ensure_ascii=True, sort_keys=True))
    interpretation.diagnostics = safe_interpretation_diagnostics(interpretation.diagnostics)
    if interpretation.diagnostics:
        logger.info("[基金] 模型解读安全诊断: %s", json.dumps(interpretation.diagnostics, ensure_ascii=True, sort_keys=True))
    report = FundReport(render_fund_report(analyses, failed, news, generated_at, interpretation,
                                         action_references=action_references, as_of=end_day),
                        analyses, failed, interpretation_status=interpretation.status, interpretation=interpretation,
                        action_references=action_references)
    directory = Path(output_dir) if output_dir is not None else Path(__file__).resolve().parents[2] / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    basename = f"fund_report_{generated_at:%Y%m%d_%H%M%S_%f}"
    report_path, snapshot_path = directory / f"{basename}.md", directory / f"{basename}.json"
    snapshot = {
        "schema_version": 4, "asset_type": "mutual_fund", "generated_at": generated_at,
        "as_of": end_day,
        "analyses": [dict(canonical_id=item.data.profile.canonical_id, **asdict(item)) for item in analyses],
        "failed_codes": failed,
        "news": news,
        "news_evidence": news_evidence,
        "interpretation": asdict(interpretation),
        "action_references": {code: asdict(reference) for code, reference in action_references.items()},
    }
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")
    report_path.write_text(report.markdown, encoding="utf-8")
    report.report_path, report.snapshot_path = str(report_path), str(snapshot_path)
    logger.info("[基金] 报告已保存: %s；数据快照: %s", report_path, snapshot_path)
    channel_results = []
    if send_notification:
        try:
            if notifier is None:
                from src.notification import NotificationService
                notifier = NotificationService()
            if not notifier.is_available():
                report.notification_status = "unavailable"
            else:
                result = notifier.send_with_results(
                    report.markdown, route_type="report", email_subject=f"场外基金已披露净值日报 - {end_day}"
                )
                report.notification_status = result.status
                channel_results = [
                    {"channel": item.channel, "success": bool(item.success), "error_code": item.error_code}
                    for item in getattr(result, "channel_results", [])
                ]
        except Exception as exc:
            report.notification_status = "unknown"
            logger.warning("[基金] 通知结果不确定，未重试: %s", exc)
    # Persist transport feedback separately from NAV success. A transport's
    # acceptance does not prove inbox delivery, and is never retried here.
    snapshot["notification"] = {"status": report.notification_status, "channels": channel_results}
    pending_path = snapshot_path.with_suffix(".json.tmp")
    pending_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")
    pending_path.replace(snapshot_path)
    return report
