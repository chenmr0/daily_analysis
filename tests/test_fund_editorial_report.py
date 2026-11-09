"""Run12 rendering replay, never a fresh or revalidated model response.

Synthetic decisions below test renderer invariants only. The retained fixture's
recorded metrics must not be recomputed from its deliberately truncated NAV tail.
"""
import copy
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import pytest

from tests.test_fund_report_brief import load_snapshot
from src.services.fund_analysis_service import render_fund_report
from src.services.fund_interpretation import (
    InterpretationScopeStatus, ResearchDecision, Statement,
)

FIXTURE = Path(__file__).parent / "fixtures/fund_report_run12.json"


def replay_inputs():
    return load_snapshot(FIXTURE)


def render(saved, analyses, interpretation):
    return render_fund_report(
        analyses, saved["failed_codes"], saved["news"], datetime.fromisoformat(saved["generated_at"]),
        interpretation, as_of=date.fromisoformat(saved["as_of"]), news_evidence=saved["news_evidence"],
    )


def section(text, code):
    return text.split(f"## {code} ", 1)[1].split("\n## ", 1)[0]


def test_actual_run12_replay_has_one_state_and_no_forced_repeated_sections():
    saved, analyses, interpretation = replay_inputs()
    before = copy.deepcopy((saved, [asdict(a) for a in analyses], asdict(interpretation)))
    text = render(saved, analyses, interpretation)
    assert text.count("横向解读未通过校验") == 1
    assert text.count("其余基金未提供操作判断") == 1
    assert "修正仍未获得" not in text
    assert "**为什么**" not in text
    assert text.count("**成立条件**") == 3
    assert text.count("**何时改变判断**") == 3
    # Distinct conditions survive, but no forced four-row decision template.
    assert all("**何时改变判断**" in line for line in text.splitlines() if "**成立条件**" in line)
    assert "同源净值接口已按相同日期核对" not in text
    assert text.count("同源接口核对不等于独立复核") == 1
    assert "历史名单交集" not in text and "其中壁仞科技" not in text
    assert "历史净值表现不构成收益预测，仍需审慎评估风险。" not in text
    assert len(text.splitlines()) < 90
    assert before == (saved, [asdict(a) for a in analyses], asdict(interpretation))
    assert "accepted AI output only" in saved["provenance"]["description"]


def test_actual_run12_every_fund_keeps_daily_recent_and_one_context():
    saved, analyses, interpretation = replay_inputs()
    text = render(saved, analyses, interpretation)
    for analysis in analyses:
        code = analysis.data.profile.code
        part = section(text, code)
        assert f"单位净值 {analysis.data.navs[-1].unit_nav:.6f}" in part
        assert str(analysis.data.navs[-1].nav_date) in part
        assert analysis.data.source_url in part
        for key in ("return_1d", "return_1m"):
            metric = analysis.metrics[key]
            assert f"{metric.value * 100:+.2f}%" in part
        metric_lines = [line for line in part.splitlines() if line.startswith("- ") and "净值变化：" in line
                        or line.startswith("- 约12个月最大回撤")]
        assert len(metric_lines) == 3
        if code in {"001672", "025500", "024480"}:
            assert "仅数据" in part
            assert "操作倾向" not in part and "观望" not in part
    assert "09-30→11-06，37天" in text
    assert text.count("10-09→11-09，31天") == 5
    assert "2025-11-07→2026-11-09" in text
    assert "截至报告日相隔3个自然日" in text and "QDII：海外估值、假期与汇率" in text


def test_actual_distinct_event_arguments_remain_and_relationship_is_not_repeated():
    saved, analyses, interpretation = replay_inputs()
    text = section(render(saved, analyses, interpretation), "020356")
    insight = interpretation.funds["020356"]
    impact = insight.news_impacts[0]
    assert text.count(impact.transmission.text) == 1
    assert text.count(insight.decision.rationale.text) == 1
    assert text.count(insight.decision.conditions.text) == 1
    assert insight.decision.invalidation.text in text  # A concrete termination trigger survives.
    assert "2026-09-30 历史股票名单中的中微公司" in text
    assert text.count("是否仍持有未核验") == 1
    assert "联接基金股票表未穿透目标ETF" in text
    assert "FundArchivesDatas.aspx?type=jjcc&code=020356" in text
    assert "2026-11-09；标题未核实" in text
    assert "影响幅度未知" in text and "不能据此解释已发生的净值变化" in text
    assert "资金使用与资本安排" not in text  # No invented improvement of retained prose.


@pytest.mark.parametrize("direction,label", [("consider_buy", "买入候选"), ("consider_reduce", "减仓候选")])
def test_actionable_views_keep_all_conditions_and_distinct_risks_even_with_same_citations(direction, label):
    saved, analyses, interpretation = replay_inputs()
    insight = interpretation.funds["020356"]
    old = insight.decision
    conditions = Statement("仅在正式公告证实资金可用且赎回限制已解除时，研究前提才成立。", old.rationale.evidence_ids)
    invalidation = Statement("若现金来源中断或暂停赎回，则原判断失效。", old.rationale.evidence_ids)
    insight.decision = ResearchDecision(direction, old.rationale, conditions, invalidation)
    risks = [Statement("历史股票名单无法排除流动性压力与暂停赎回风险。", old.rationale.evidence_ids),
             Statement("同一事件仍有融资违约风险，不能由标题确认。", old.rationale.evidence_ids)]
    insight.risks = [*risks, *risks]  # Exact duplicates only are removed.
    text = section(render(saved, analyses, interpretation), "020356")
    assert label in text and old.rationale.text in text
    assert conditions.text in text and invalidation.text in text
    assert "**成立条件**" in text and "**何时改变判断**" in text
    assert all(text.count(risk.text) == 1 for risk in risks)
    if direction == "consider_reduce":
        assert "若已持有" in text


def test_nullable_undetermined_has_no_invented_watch_or_conditions():
    saved, analyses, interpretation = replay_inputs()
    insight = interpretation.funds["018123"]
    insight.decision = ResearchDecision("undetermined", Statement("缺少可用估值证据，无法判断。", ["F018123:return_1m"]), None, None)
    insight.risks = []
    text = section(render(saved, analyses, interpretation), "018123")
    assert "无法判断" in text and "观望" not in text
    assert "成立条件" not in text and "何时改变判断" not in text


def test_failed_scope_does_not_leak_retained_advice_or_impacts():
    saved, analyses, interpretation = replay_inputs()
    interpretation.fund_statuses["020356"] = InterpretationScopeStatus("model_failed", "not accepted")
    text = section(render(saved, analyses, interpretation), "020356")
    assert "仅数据" in text and "操作倾向" not in text
    assert interpretation.funds["020356"].news_impacts[0].transmission.text not in text
    assert "中微公司" in text and "标题未核实" in text


def test_data_failures_and_exceptional_warnings_are_not_suppressed_with_routine_messages():
    saved, analyses, interpretation = replay_inputs()
    a = analyses[0]
    a.metrics["return_6m"].reason = "分红/拆分调整缺失"
    a.metrics["return_6m"].value = None
    notices = [
        "历史净值接口交叉核对不可用；净值仅取自同源净值走势，非独立数据源复核。",
        "净值接口更新日期不同，可能存在更新滞后：走势 2026-11-06，历史 2026-11-05。",
        "同源净值接口已按相同日期核对，最近共同日期 2026-11-06；非独立数据源复核。另有重大错误。",
        "最新净值日期2026-11-06；数据记录存在冲突，无法确认日期。",
    ]
    a.warnings.extend(notices)
    saved["failed_codes"] = {"000007": "基金身份不匹配"}
    text = render(saved, analyses, interpretation)
    assert "约6个月净值变化：N/A（分红/拆分调整缺失）" in text
    assert all(notice in text for notice in notices)
    assert "fund:000007：基金身份不匹配" in text


def test_statement_compaction_replaces_only_exact_program_fact_not_qualitative_text():
    saved, analyses, interpretation = replay_inputs()
    insight = interpretation.funds["018123"]
    fact = interpretation.facts["F018123:return_1m"]
    prose = "；这仍不足以说明经营改善，不能据此给出买入判断。"
    insight.decision = ResearchDecision("undetermined", Statement(fact.text + prose, [fact.evidence_id]), None, None)
    insight.risks = []
    text = section(render(saved, analyses, interpretation), "018123")
    assert fact.text not in text
    assert "约1个月净值变化：-16.89%（10-09→11-09，31天）" in text
    assert prose in text
    assert text.count("-16.89%") == 1  # Expanded judgment fact is not also a data row.
    assert insight.decision.rationale.text == fact.text + prose


def test_untrusted_titles_names_sources_relationships_and_statements_are_escaped():
    from src.formatters import markdown_to_html_document
    saved, analyses, interpretation = replay_inputs()
    payload = '<img src=x onerror=alert(1)> [bad](javascript:alert(1))'
    analyses[2].data.profile.name += payload
    bundle = saved["news_evidence"]["020356"]
    bundle["items"][0]["title"] += payload
    bundle["items"][0]["source"] = payload
    insight = interpretation.funds["020356"]
    insight.risks.append(Statement(payload, ["F020356:holdings"]))
    saved["failed_codes"] = {"bad": payload}
    text = render(saved, analyses, interpretation)
    output = markdown_to_html_document(text)
    assert '<img src=x' not in output and 'href="javascript:' not in output
    assert '&lt;img' in output
    assert '\\[bad\\]' in text


def test_all_failed_ai_has_one_global_notice_no_per_fund_repair_rows():
    saved, analyses, interpretation = replay_inputs()
    interpretation.status = "model_failed"
    for scope in interpretation.fund_statuses.values():
        scope.status = "model_failed"
        scope.repair_status = "failed"
    text = render(saved, analyses, interpretation)
    assert text.count("AI：本期解读不可用或未通过校验") == 1
    assert "修正仍未获得" not in text and "操作倾向" not in text
    assert text.count("相邻净值变化：") == 6


def test_one_unavailable_window_reason_is_not_repeated_for_every_metric():
    saved, analyses, interpretation = replay_inputs()
    a = analyses[0]
    reason = "缺少累计净值，无法核实区间无分红/拆分事件"
    for metric in a.metrics.values():
        metric.value = None
        metric.reason = reason
    text = section(render(saved, [a], None), "021190")
    assert text.count(reason) == 1
    assert "相邻净值变化" in text and "约12个月最大回撤" in text
    assert "-13.29%" not in text


def test_shared_news_citations_never_replace_distinct_reasoning_or_positive_conditions():
    saved, analyses, interpretation = replay_inputs()
    insight = interpretation.funds["020356"]
    refs = insight.news_impacts[0].transmission.evidence_ids
    rationale = Statement("回购线索不足以填补估值信息缺口，暂时无法确认投资吸引力。", refs)
    conditions = Statement("若盈利现金流改善且估值回到有支撑的区间，再重新评估研究观点。", refs)
    invalidation = Statement("若回购终止且融资违约风险上升，则原判断需要重审。", refs)
    insight.decision = ResearchDecision("watch", rationale, conditions, invalidation)
    text = section(render(saved, analyses, interpretation), "020356")
    assert all(statement.text in text for statement in (rationale, conditions, invalidation))
    assert insight.news_impacts[0].transmission.text in text
    assert "**成立条件**" in text and "**何时改变判断**" in text


def test_exact_repeated_statement_is_rendered_once_without_erasing_different_same_source_risk():
    saved, analyses, interpretation = replay_inputs()
    insight = interpretation.funds["020356"]
    impact = insight.news_impacts[0]
    insight.decision = ResearchDecision("undetermined", impact.transmission, None, None)
    risk = Statement("同一事件仍有尚未核实的融资违约风险。", impact.transmission.evidence_ids)
    insight.risks = [impact.transmission, risk]
    text = section(render(saved, analyses, interpretation), "020356")
    assert text.count(impact.transmission.text) == 1
    assert risk.text in text


@pytest.mark.parametrize("direction", ["watch", "undetermined"])
def test_strictly_accepted_counterexample_keeps_information_gap_and_both_conditions(direction):
    from src.services import fund_interpretation as module
    from tests.test_fund_editorial_contract import bound_watch, strict_parse
    from tests.test_fund_decision_contract import news_bundle, statement
    from tests.test_fund_interpretation import analysis
    inputs = [analysis(holdings=True)]
    news = news_bundle(title="历史公司回购公告")
    output = bound_watch()
    decision = output["funds"][0]["decision"]
    if direction == "watch":
        decision["conditions"]["text"] = "若待核实回购线索得到实施公告支持且企业经营现金流可以覆盖，才可进一步研究资本安排。"
        decision["invalidation"]["text"] = "若待核实回购线索被正式公告证伪，原先关于资本安排的研究依据失效。"
    else:
        decision.update(direction="undetermined", basis="information_gap", conditions=None, invalidation=None,
                        rationale=statement("{{F000001:research_coverage}}待核实回购线索缺少企业现金来源资料，无法判断融资风险。",
                                            "F000001:research_coverage", "F000001:news:0"))
    funds, comparison, statuses, comparison_status = strict_parse(output, inputs, news)
    assert statuses["000001"].status == "ok", statuses["000001"].diagnostics
    facts, context = module._build_context(inputs, news)
    accepted = module.FundInterpretation("ok", "strictly accepted test fixture", funds=funds,
                                        facts=facts, comparison=comparison, comparison_context=context,
                                        fund_statuses=statuses, comparison_status=comparison_status)
    text = render_fund_report(inputs, {}, {}, inputs[0].data.fetched_at, accepted,
                              as_of=date(2026, 6, 30), news_evidence=news)
    for key in ("conditions", "invalidation"):
        if decision[key] is not None:
            assert decision[key]["text"] in text
    if direction == "undetermined":
        assert "待核实回购线索缺少企业现金来源资料，无法判断融资风险。" in text
    else:
        assert "回购线索给出了资金使用的观察对象，但标题不足以确认实施。" in text
    assert funds["000001"].news_impacts[0].transmission.text in text
