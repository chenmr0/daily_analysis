"""Published-NAV facts with separately labelled evidence-cited cloud interpretation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import html
import json
import logging
import math
from pathlib import Path
import re
from typing import Dict, List, Optional
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from src.core.fund_analysis import FundAnalysis, analyze_fund, parse_fund_codes
from src.core.fund_action_reference import (
    FundActionReference, build_action_reference, unavailable_reference,
)
from src.services.fund_interpretation import (
    FundInterpretation, InterpretationScopeStatus, interpret_funds, safe_interpretation_diagnostics,
)
from src.services.fund_news_service import FundNewsBundle, REASON_LABELS, gather_fund_news, select_news_digest

logger = logging.getLogger(__name__)

METRIC_LABELS = {
    "return_1d": "相邻净值变化",
    "return_1m": "约1个月净值变化",
    "return_3m": "约3个月净值变化",
    "return_6m": "约6个月净值变化",
    "return_12m": "约12个月净值变化",
    "return_ytd": "年初至今净值变化",
    "max_drawdown_12m": "约12个月最大回撤",
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


def _statement_line(statement, interpretation: FundInterpretation, label: str = "", *,
                    compact_facts=None, omitted_citations=()) -> str:
    citations = []
    seen = set()
    statement_text = statement.text
    fact_labels = {"profile": "基金资料", "nav": "净值", "metric": "净值区间",
                   "dated_holdings": "历史持仓", "dated_allocation": "历史行业",
                   "target_relationship": "目标ETF关系", "news_lead": "新闻线索",
                   "news_coverage": "检索范围", "data_quality": "数据质量",
                   "risk_boundary": "资料边界", "missing_holdings": "持仓资料缺口",
                   "missing_allocation": "行业资料缺口"}
    for evidence_id in statement.evidence_ids:
        fact = interpretation.facts.get(evidence_id)
        if fact is None:
            continue
        # Replace only exact program-generated expansions, never rewrite the
        # model's qualitative prose or remove its conditional language.
        if compact_facts and evidence_id in compact_facts:
            statement_text = statement_text.replace(fact.text, compact_facts[evidence_id])
        if evidence_id in omitted_citations:
            continue
        source_label = fact_labels.get(fact.kind, "资料")
        if fact.data_date:
            source_label += f" {fact.data_date}"
        key = (source_label, fact.source_url)
        if key in seen:
            continue
        seen.add(key)
        citations.append(_link(source_label, fact.source_url) if fact.source_url else _text(source_label))
    prefix = f"**{_text(label)}**：" if label else ""
    return f"- {prefix}{_text(statement_text)}" + ("（" + "；".join(citations) + "）" if citations else "")


def _repair_note(scope):
    """Render only program-owned repair states, never provider/model text."""
    labels = {
        "succeeded": "修正一次后通过校验。",
        "failed": "修正仍未获得可用解读；保留程序数据。",
        "skipped_budget": "首次解读未通过，剩余时间不足，未追加修正；保留程序数据。",
        "skipped_busy": "首次解读未通过，已有请求尚未结束，未追加修正；保留程序数据。",
    }
    status = getattr(scope, "repair_status", None)
    label = labels.get(status) if isinstance(status, str) else None
    return f"- AI：{label}" if label else None


# Only these exact, program-owned disclaimers are non-informative by themselves.
# Never classify a risk as boilerplate from shared citations or a word such as
# "历史": e.g. liquidity, suspension and concentration risks must stay visible.
_GENERIC_RISKS = frozenset({
    "历史净值表现不保证收益，仍需审慎评估风险。",
    "历史净值表现不保证回报，仍需审慎评估风险。",
    "历史净值表现不构成收益预测，仍需审慎评估风险。",
})


def _decision_lines(insight, interpretation, *, compact_facts=None):
    """Keep distinct reasoning/conditions; only exact repeated text is omitted.

    Shared event IDs do not prove shared meaning: a rationale may identify a
    valuation gap while an impact describes cash use. Neither may replace the
    other. Compact follow-up layout does not weaken the accepted conditions.
    """
    decision = getattr(insight, "decision", None)
    labels = {"consider_buy": "买入候选（有条件新增）",
              "consider_reduce": "减仓候选（若已持有）",
              "watch": "观望", "undetermined": "无法判断"}
    seen = set()
    if decision is None or decision.direction not in labels:
        lines = ["- **操作判断**：本次模型未提供。"]
        statements = [*insight.conclusion[:1], *insight.risks]
    else:
        rationale = decision.rationale
        lines = [_statement_line(rationale, interpretation, f"操作倾向：{labels[decision.direction]}",
                                 compact_facts=compact_facts)]
        seen.add(rationale.text)
        supplements = {}
        for label, statement in (("成立条件", decision.conditions), ("何时改变判断", decision.invalidation)):
            if statement is None or statement.text in seen:
                continue
            entry = supplements.setdefault(statement.text, {"statement": statement, "labels": []})
            entry["labels"].append(label)
        follow_up = [_statement_line(entry["statement"], interpretation, " / ".join(entry["labels"]),
                                     compact_facts=compact_facts).removeprefix("- ")
                     for entry in supplements.values()]
        if follow_up:
            lines.append("- " + " ".join(follow_up))
            seen.update(supplements)
        statements = list(insight.risks)
    for statement in statements:
        if statement.text in seen or statement.text in _GENERIC_RISKS:
            continue
        seen.add(statement.text)
        lines.append(_statement_line(statement, interpretation, "风险" if statement in insight.risks else "解读",
                                     compact_facts=compact_facts))
    return lines, seen


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


def _percent(value: float) -> str:
    if value != 0 and f"{abs(value) * 100:.2f}" == "0.00":
        return ("正值" if value > 0 else "负值") + "，绝对值不足0.01%"
    return f"{value * 100:+.2f}%" if value else "0.00%"


def _valid_metric(analysis, key):
    """Do not turn absent or malformed program evidence into a report finding."""
    metric = analysis.metrics.get(key)
    if (metric is None or metric.reason is not None or type(metric.value) not in (int, float)
            or not math.isfinite(metric.value) or not math.isfinite(metric.value * 100)
            or type(metric.start_date) is not date or type(metric.end_date) is not date
            or not metric.start_date < metric.end_date == analysis.data.navs[-1].nav_date):
        return None
    if (key.startswith("return_") and metric.value <= -1) or (
            key.startswith("max_drawdown") and not 0 <= metric.value <= 1):
        return None
    return metric


def _period_dates(start, end, as_of):
    if start.year == end.year == as_of.year:
        return f"{start:%m-%d}→{end:%m-%d}"
    return f"{start}→{end}"


def _metric_line(analysis, key, as_of):
    metric = _valid_metric(analysis, key)
    label = METRIC_LABELS[key]
    if key.startswith("max_drawdown"):
        label += "（历史峰谷，非当前回撤）"
    if metric is None:
        original = analysis.metrics.get(key)
        reason = getattr(original, "reason", None) or "指标或日期证据不足"
        return f"- {label}：N/A（{_text(reason)}）。"
    value = f"{metric.value * 100:.2f}%" if key.startswith("max_drawdown") else _percent(metric.value)
    duration = f"，{(metric.end_date - metric.start_date).days}天" if key == "return_1m" else ""
    return f"- {label}：{value}（{_period_dates(metric.start_date, metric.end_date, as_of)}{duration}）。"


def _scope_label(scope) -> str:
    if scope is None:
        return "未提供状态"
    diagnostic = safe_interpretation_diagnostics(scope.diagnostics)
    if diagnostic.get("code") == "request_timeout":
        return "超时，未采用模型结论"
    labels = {"ok": "已通过校验", "disabled": "未启用", "unavailable": "不可用",
              "model_failed": "失败，未采用模型结论", "not_applicable": "不适用"}
    label = labels.get(scope.status, "状态未知")
    if scope.status not in {"ok", "disabled", "not_applicable"} and scope.reason:
        label += "；" + _text(scope.reason).rstrip("。")
    return label


def _ai_summary(analyses, interpretation):
    if interpretation is None or interpretation.status == "disabled":
        return "AI未启用；以下为程序数据。"
    scopes = [interpretation.fund_statuses.get(a.data.profile.code) for a in analyses]
    accepted = sum(scope is not None and scope.status == "ok" and a.data.profile.code in interpretation.funds
                   for a, scope in zip(analyses, scopes)) if interpretation.status in {"ok", "partial"} else 0
    if not accepted:
        timeout = scopes and all(scope is not None and safe_interpretation_diagnostics(scope.diagnostics).get("code")
                                 == "request_timeout" for scope in scopes)
        return "AI：本期请求超时，未取得可用解读。" if timeout else "AI：本期解读不可用或未通过校验，仅展示数据与新闻线索。"
    status = f"AI解读 {accepted}/{len(analyses)}只通过校验（推理观点）"
    if accepted < len(analyses):
        status += "；其余基金未提供操作判断"
    if interpretation.comparison_status.status not in {"ok", "not_applicable", "disabled"}:
        status += "；横向解读未通过校验"
    return status + "。"


def _compact_facts(analyses, interpretation, as_of):
    """Exact replacements keyed to actual program facts; no narrative rewriting."""
    result = {}
    if interpretation is None:
        return result
    for analysis in analyses:
        code = analysis.data.profile.code
        latest = analysis.data.navs[-1]
        result[f"F{code}:nav"] = f"单位净值 {latest.unit_nav:.6f}（{latest.nav_date}）"
        for key in METRIC_LABELS:
            if _valid_metric(analysis, key):
                result[f"F{code}:{key}"] = _metric_line(analysis, key, as_of).removeprefix("- ").rstrip("。")
    return result


def _metric_brief(analysis, as_of, *, shown_metrics=()):
    """The same daily/recent/context budget regardless of AI success.

    Unsafe unselected windows still surface as grouped data errors, rather than
    silently disappearing when the reader sees only the concise projection.
    """
    recent, year = (_valid_metric(analysis, key) for key in ("return_1m", "return_12m"))
    context = "return_12m" if recent and year and recent.value * year.value < 0 else "max_drawdown_12m"
    keys = ("return_1d", "return_1m", context)
    lines = []
    for key in keys:
        if key in shown_metrics or _valid_metric(analysis, key) is None:
            continue
        line = _metric_line(analysis, key, as_of)
        lines.append(line)
    failures = {}
    for key in dict.fromkeys((*keys, *METRIC_LABELS)):
        if (key in keys or key in analysis.metrics) and _valid_metric(analysis, key) is None:
            metric = analysis.metrics.get(key)
            reason = getattr(metric, "reason", None) or "指标或日期证据不足"
            failures.setdefault(reason, []).append(METRIC_LABELS[key])
    for reason, labels in failures.items():
        label = labels[0] + "：" if len(labels) == 1 else "数据缺口：" + "、".join(labels) + " "
        lines.append(f"- {label}N/A（{_text(reason)}）。")
    return lines


def _material_warnings(analysis):
    routine = {
        "口径：仅在有证据支持区间无分红/拆分时计算单位净值收益，非红利再投资总回报。"
        "净值差额容差为0.000001；数据源舍入及事件记录完整性可能影响核实。",
        "QDII净值可能因海外估值及假期延迟披露，不假设固定T+N时效。",
        "持仓为定期报告期末股票明细，非实时仓位；实际公告日期未知，不能用于历史时点回测。",
    }
    for warning in dict.fromkeys([*analysis.data.warnings, *analysis.warnings]):
        if warning in routine or re.fullmatch(
                r"同源净值接口已按相同日期核对，最近共同日期 \d{4}-\d{2}-\d{2}；非独立数据源复核。|"
                r"最新净值日期\d{4}-\d{2}-\d{2}；截至\d{4}-\d{2}-\d{2}已过去\d+个自然日。", warning):
            continue
        yield warning


def _period_pattern(analysis):
    """Describe within-fund windows, never rank products or infer a turning point."""
    recent, year = (_valid_metric(analysis, key) for key in ("return_1m", "return_12m"))
    if recent and year and recent.value < 0 < year.value:
        return "近期区间下跌，但约12个月区间仍上涨，两个区间方向不同。"
    windows = [_valid_metric(analysis, key) for key in ("return_1m", "return_3m", "return_6m", "return_12m")]
    if all(metric and metric.value < 0 for metric in windows):
        return "近期及约3、6、12个月区间均下跌，不只是近期区间为负。"
    if recent and year and recent.value > 0 > year.value:
        return "近期区间上涨，但约12个月区间仍下跌，两个区间方向不同。"
    return None


def _known_period(period, published_at, as_of):
    return (type(period) is date and period <= as_of
            and (published_at is None or type(published_at) is date and published_at <= as_of))


def _holding_brief(data, as_of):
    lines = []
    portfolio = data.holdings
    if (portfolio and portfolio.holdings
            and _known_period(portfolio.report_period_end, portfolio.published_at, as_of)):
        holdings = portfolio.holdings
        total = sum(h.weight_pct for h in holdings)
        detail = "、".join(f"{_text(h.name)} {h.weight_pct:.2f}%"
                          for h in sorted(holdings, key=lambda h: h.weight_pct, reverse=True)[:2])
        lines.append(f"- 历史持仓：{portfolio.report_period_end} 报告期的{len(holdings)}只股票合计 {total:.2f}%；")
        lines.append(f"  其中{detail}。{_link('持仓来源', portfolio.source_url)}（公告日：{portfolio.published_at or '未提供'}）。")
    else:
        lines.append("- 持仓缺口：未取得可用股票明细，不代表空仓。")
    related = [a for a in data.related_assets if a.verified
               and (a.published_at is None or type(a.published_at) is date and a.published_at <= as_of)]
    targets = [a for a in related if a.category == "target_etf"
               and (a.source_date is None or type(a.source_date) is date and a.source_date <= as_of)]
    for target in targets[:1]:
        lines.append(f"- 关联ETF：{_text(target.name)}（{_text(target.code or '代码未知')}）；"
                     f"关系资料日期 {target.source_date or '未知'}。{_link('关联来源', target.source_url)}。")
    industries = [a for a in related if a.category == "industry" and a.weight_pct is not None]
    dated = [a for a in industries if _known_period(a.source_date, a.published_at, as_of)]
    if industries and not dated:
        lines.append("- 行业资料缺口：报告期缺失或晚于截止日，未展示占比。")
    if dated:
        industry = max(dated, key=lambda a: a.weight_pct)
        lines.append(f"- 历史行业：{industry.source_date or '日期未知'} 的{_text(industry.name)}占基金净值 {industry.weight_pct:.2f}%。"
                     f"{_link('配置来源', industry.source_url)}。")
    if "联接" in data.profile.name:
        lines.append("- 联接基金的股票明细不等于目标ETF底层持仓；未做穿透。")
    return lines


def _historical_intersections(analyses, as_of):
    """Same-period, same-code-and-name disclosed lists only; no current overlap claim."""
    groups = {}
    for analysis in analyses:
        portfolio = analysis.data.holdings
        if portfolio is None or not _known_period(portfolio.report_period_end, portfolio.published_at, as_of):
            continue
        for holding in portfolio.holdings:
            key = (portfolio.report_period_end, holding.code, holding.name)
            groups.setdefault(key, {})[analysis.data.profile.code] = holding.weight_pct
    shared = [(key, funds) for key, funds in groups.items() if len(funds) > 1]
    if not shared:
        return []
    lines = ["## 历史名单交集", ""]
    for (period, code, name), funds in shared[:2]:
        values = "、".join(f"{fund}（{weight:.2f}%）" for fund, weight in funds.items())
        lines.append(f"- {period}：{values}均列有{_text(name)}（{_text(code)}）。")
    lines.extend(["仅为所列报告期股票名单的交集，不是当前完整持仓重合度。", ""])
    return lines


def _brief_news(bundle, fallback, nav_date, *, fund_code=None, insight=None, interpretation=None,
                digest=None, rendered_statements=(), compact_facts=None, is_feeder=False):
    if bundle is None:
        return [f"- 新闻：{_text(line)}" for line in (fallback or []) if "本次未启用新闻检索" not in line]
    warnings = bundle.get("warnings", []) if isinstance(bundle, dict) else bundle.warnings
    if any("新闻组件不可用" in warning for warning in warnings):
        return ["- 新闻组件不可用，近期事件未核验。"]
    digest = digest if digest is not None else select_news_digest(bundle, max_items=2)
    lines, relationships = [], set()
    impacts = {item.news_evidence_id: item for item in getattr(insight, "news_impacts", [])}
    for group in digest["items"]:
        item = group["item"]
        get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        context = group["reporting_context"]
        label = {"fund": "基金线索", "direct_target": "目标ETF线索",
                 "historical_holding": "历史关联线索", "sector_background": "行业背景"}.get(context, "待核实线索")
        lines.append(f"- {label}：{_link(get('title', ''), get('url', ''))}")
        sources = group["sources"]
        extra = f"；同主体同日相近线索合并{len(sources)}条来源" if len(sources) > 1 else ""
        published = get("published_date")
        if isinstance(published, str):
            try:
                published = date.fromisoformat(published)
            except ValueError:
                published = None
        timing = "；晚于所列净值日，仅作后续线索" if type(published) is date and published > nav_date else ""
        lines.append(f"  {_text(get('source', '未提供'))}｜{_text(published or '日期未知')}{extra}{timing}；标题未核实。")
        relationship = group.get("relationship")
        relation_key = (context, str(relationship))
        if relation_key not in relationships:
            relationships.add(relation_key)
            if relationship and context == "historical_holding":
                lines.append(f"  关联：{_text(relationship.get('source_date') or '日期未知')} 历史股票名单中的"
                             f"{_text(relationship.get('name', ''))}；是否仍持有未核验。"
                             + (f"{_link('关系来源', relationship['source_url'])}。" if relationship.get("source_url") else "")
                             + ("联接基金股票表未穿透目标ETF。" if is_feeder else ""))
            elif context == "direct_target":
                lines.append("  关联：目标ETF身份已匹配，当前配置比例未核验。")
            elif context == "sector_background":
                lines.append("  关联：行业或名称主题背景，不等于已确认基金敞口。")
        evidence_id = f"F{fund_code}:news:{group['item_index']}"
        impact = impacts.get(evidence_id)
        if impact is not None and interpretation is not None and impact.transmission.text not in rendered_statements:
            lines.append(_statement_line(impact.transmission, interpretation, "可能影响（标题线索未核实）",
                                         compact_facts=compact_facts, omitted_citations={evidence_id}))
    assessment = getattr(insight, "news_assessment", None)
    if (assessment is not None and assessment.status in {"insufficient_evidence", "conditional_impacts"}
            and assessment.explanation is not None and interpretation is not None):
        # A separate assessment can explain an unanalysed event, not merely
        # reprint the same accepted impact in another mandatory section.
        displayed = [item.transmission.text for item in impacts.values()]
        if assessment.explanation.text not in displayed and assessment.explanation.text not in rendered_statements:
            lines.append(_statement_line(assessment.explanation, interpretation, "影响待核实", compact_facts=compact_facts))
    if impacts and any(f"F{fund_code}:news:{group['item_index']}" in impacts for group in digest["items"]):
        lines.append("  对基金影响幅度未知，不能据此解释已发生的净值变化。")
    coverage = bundle.get("coverage", {}) if isinstance(bundle, dict) else bundle.coverage
    status_labels = {"timeout": "新闻检索失败：请求超时", "unavailable": "新闻检索不可用",
                     "skipped_budget": "部分检索未执行：查询、时间或条数预算用尽",
                     "not_configured": "未配置新闻检索", "no_identity": "身份不足，未执行新闻检索"}
    incomplete = {}
    for category, detail in coverage.items():
        status = detail.get("status") if isinstance(detail, dict) else detail.status
        if status in status_labels:
            incomplete.setdefault(status, []).append(NEWS_LABELS.get(category, "相关线索"))
    for status, categories in incomplete.items():
        lines.append(f"- {'、'.join(categories)}：{status_labels[status]}。")
    # Raw candidate/selection counts are diagnostic detail, not another event.
    return lines


def _reference_brief(analysis, insight, interpretation, digest, as_of):
    """Show dated portfolio context only where the accepted view uses it.

    Never print a decorative top-name list. Historical news already supplies its
    exact relationship/date in place, so do not repeat that same context here.
    """
    if insight is None or interpretation is None:
        return []
    statements = [*insight.conclusion, *insight.risks]
    if insight.decision:
        statements += [insight.decision.rationale, insight.decision.conditions, insight.decision.invalidation]
    kinds = {interpretation.facts[key].kind for statement in statements if statement is not None
             for key in statement.evidence_ids if key in interpretation.facts}
    contexts = {group["reporting_context"] for group in digest["items"]}
    data = analysis.data
    lines = []
    portfolio = data.holdings
    if ("dated_holdings" in kinds and "historical_holding" not in contexts and portfolio and portfolio.holdings
            and _known_period(portfolio.report_period_end, portfolio.published_at, as_of)):
        total = sum(h.weight_pct for h in portfolio.holdings)
        lines.append(f"- {_link('历史股票名单', portfolio.source_url)}：{portfolio.report_period_end} 报告期，"
                     f"{len(portfolio.holdings)}只合计 {total:.2f}%；非当前仓位，公告日 {_text(portfolio.published_at or '未提供')}。")
        if "联接" in data.profile.name:
            lines.append("  联接基金股票表不等于目标ETF底层持仓；未做穿透。")
    if "target_relationship" in kinds and "direct_target" not in contexts:
        for asset in data.related_assets:
            if (asset.category == "target_etf" and asset.verified
                    and (asset.source_date is None or _known_period(asset.source_date, asset.published_at, as_of))
                    and (asset.published_at is None or type(asset.published_at) is date and asset.published_at <= as_of)):
                lines.append(f"- 目标ETF：{_link(asset.name, asset.source_url)}（{_text(asset.code or '代码未知')}）；"
                             f"关系资料日期 {_text(asset.source_date or '未知')}，当前配置及底层持仓未核验。")
                break
    return lines


def render_fund_report(analyses: List[FundAnalysis], failed: Dict[str, str], news, generated_at: datetime,
                       interpretation: Optional[FundInterpretation] = None, *,
                       action_references: Optional[Dict[str, FundActionReference]] = None,
                       as_of: Optional[date] = None, news_evidence=None) -> str:
    """Mobile-friendly brief. Full facts, references and diagnostics stay in JSON."""
    end_day = as_of or generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    lines = [f"# 场外基金日报｜{end_day}", "", _ai_summary(analyses, interpretation), ""]
    compact_facts = _compact_facts(analyses, interpretation, end_day)
    no_news = []
    for analysis in analyses:
        data, profile = analysis.data, analysis.data.profile
        latest = data.navs[-1]
        scope = interpretation.fund_statuses.get(profile.code) if interpretation else None
        insight = interpretation.funds.get(profile.code) if interpretation else None
        accepted = (interpretation is not None and interpretation.status in {"ok", "partial"}
                    and insight is not None and scope is not None and scope.status == "ok")
        suffix = "｜仅数据" if not accepted and interpretation and interpretation.status in {"ok", "partial"} else ""
        lines.extend([f"## {profile.code} {_text(profile.name)}{suffix}", ""])
        age = (end_day - latest.nav_date).days
        timing = f"；截至报告日相隔{age}个自然日" if age > 0 else ""
        lines.append(f"单位净值 {latest.unit_nav:.6f}｜{latest.nav_date}｜{_link('净值来源', data.source_url)}{timing}。")
        bundle = (news_evidence or {}).get(profile.code)
        digest = select_news_digest(bundle, max_items=2) if bundle is not None else {"items": []}
        rendered_statements, decision_lines = set(), []
        if accepted:
            decision_lines, rendered_statements = _decision_lines(insight, interpretation, compact_facts=compact_facts)
        # A resolved metric placeholder is already a dated program fact in
        # the judgment. Do not print it again as a separate fact row.
        shown_metrics = {key for key in METRIC_LABELS
                         if (fact_text := compact_facts.get(f"F{profile.code}:{key}"))
                         and any(_text(fact_text) in line for line in decision_lines)}
        lines.extend(_metric_brief(analysis, end_day, shown_metrics=shown_metrics))
        lines.extend(decision_lines)
        if accepted:
            lines.extend(_reference_brief(analysis, insight, interpretation, digest, end_day))
        if profile.is_qdii:
            lines.append("- QDII：海外估值、假期与汇率可能错位；所列变化截至净值日。")
        for warning in _material_warnings(analysis):
            lines.append(f"- 数据提醒：{_text(warning)}")
        lines.extend(_brief_news(bundle, news.get(profile.code), latest.nav_date,
                                 fund_code=profile.code, insight=insight if accepted else None,
                                 interpretation=interpretation if accepted else None, digest=digest,
                                 rendered_statements=rendered_statements, compact_facts=compact_facts,
                                 is_feeder="联接" in profile.name))
        if bundle is not None and not digest["items"]:
            no_news.append(profile.code)
        lines.append("")
    if interpretation is not None and interpretation.status in {"ok", "partial"}:
        if interpretation.comparison_status.status == "ok" and interpretation.comparison:
            lines.extend(["## 横向观察", ""])
            lines.extend(_statement_line(statement, interpretation, compact_facts=compact_facts)
                         for statement in interpretation.comparison)
            lines.append("")
    if no_news:
        lines.extend([f"未取得可分析关联新闻：{'、'.join(no_news)}；不代表没有相关事件。", ""])
    if failed:
        lines.extend(["## 数据获取或校验失败", ""])
        lines.extend(f"- fund:{_text(code)}：{_text(reason)}" for code, reason in failed.items())
        lines.append("")
    lines.append("口径：单位净值变化，非红利再投资总回报；同源接口核对不等于独立复核。")
    if interpretation is not None and (interpretation.provider or interpretation.model):
        lines.append(f"实际模型：{_text(interpretation.model or '未返回')}；供应方：{_text(interpretation.provider or '未返回')}。")
    if interpretation is not None:
        repair_model, repair_provider = getattr(interpretation, "repair_model", ""), getattr(interpretation, "repair_provider", "")
        if (repair_model or repair_provider) and (repair_model, repair_provider) != (interpretation.model, interpretation.provider):
            lines.append(f"修正请求实际模型：{_text(repair_model or '未返回')}；供应方：{_text(repair_provider or '未返回')}。")
    if interpretation is not None and interpretation.status == "disabled" and "历史截止日" in interpretation.reason:
        lines.append(_text(interpretation.reason))
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
        repair_status = getattr(scope, "repair_status", "not_attempted")
        scope.repair_status = repair_status if isinstance(repair_status, str) and repair_status in {
            "not_attempted", "succeeded", "failed", "skipped_budget", "skipped_busy",
        } else "not_attempted"
        scope.repair_diagnostics = safe_interpretation_diagnostics(getattr(scope, "repair_diagnostics", {}))
        if scope.repair_status != "not_attempted":
            logger.info("[基金] 模型修正状态 %s: %s %s", code, scope.repair_status,
                        json.dumps(scope.repair_diagnostics, ensure_ascii=True, sort_keys=True))
        if scope.status == "model_failed" and scope.diagnostics:
            logger.info("[基金] 模型解读范围安全诊断 %s: %s", code,
                        json.dumps(scope.diagnostics, ensure_ascii=True, sort_keys=True))
    for name in ("repair_model", "repair_provider"):
        value = getattr(interpretation, name, "")
        setattr(interpretation, name, value[:200] if isinstance(value, str) and not any(ord(c) < 32 for c in value) else "")
    interpretation.diagnostics = safe_interpretation_diagnostics(interpretation.diagnostics)
    if interpretation.diagnostics:
        logger.info("[基金] 模型解读安全诊断: %s", json.dumps(interpretation.diagnostics, ensure_ascii=True, sort_keys=True))
    markdown = render_fund_report(
        analyses, failed, news, generated_at, interpretation,
        action_references=action_references, as_of=end_day, news_evidence=news_evidence,
    )
    report = FundReport(markdown, analyses, failed, interpretation_status=interpretation.status,
                        interpretation=interpretation, action_references=action_references)
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
