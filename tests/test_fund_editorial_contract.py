"""Concise research contract against retained accepted Run12 output, offline only.

The saved source metrics are replayed, never recomputed from truncated NAVs.
Hand-authored alternatives test validation, not model investment performance.
"""
import copy
import json
from pathlib import Path

import pytest

from src.services import fund_interpretation as module
from tests.test_fund_decision_contract import news_bundle, statement
from tests.test_fund_interpretation import analysis, configuration
from tests.test_fund_repair_retry import sequence_factory
from tests.test_fund_report_brief import load_snapshot


def run12_inputs():
    saved, analyses, _ = load_snapshot(Path(__file__).parent / "fixtures/fund_report_run12.json")
    return saved, analyses


def lean_output(codes=("000001",)):
    return {"funds": [{
        "fund_code": code,
        **{key: [] for key in module._SECTIONS},
        "decision": {
            "direction": "undetermined", "basis": "information_gap",
            "rationale": statement(
                "{{F" + code + ":research_coverage}}这些输入不足以判断企业经营变化或投资价值。",
                f"F{code}:research_coverage"),
            "conditions": None, "invalidation": None,
        },
        "news_impacts": [],
        "news_assessment": {"status": "no_eligible_news", "explanation": None},
    } for code in codes], "comparison": []}


def strict_parse(output, analyses=None, news=None):
    analyses = analyses or [analysis(holdings=True)]
    facts, comparison = module._build_context(analyses, news)
    return module._parse_response(json.dumps(output, ensure_ascii=False), analyses, facts, comparison,
                                  require_analysis=True)


def bound_watch():
    output = lean_output()
    item = output["funds"][0]
    item["decision"] = {
        "direction": "watch", "basis": "investment_case",
        "rationale": statement(
            "{{F000001:return_1m}}回购线索给出了资金使用的观察对象，但标题不足以确认实施。",
            "F000001:return_1m", "F000001:holdings", "F000001:news:0"),
        "conditions": statement(
            "需核验待核实回购线索的实施公告，才能判断企业现金安排是否改变。",
            "F000001:news:0", "F000001:holdings"),
        "invalidation": statement(
            "若回购线索被正式公告澄清或终止，原有等待条件即不再成立。", "F000001:news:0"),
    }
    item["news_impacts"] = [{
        "news_evidence_id": "F000001:news:0", "relationship_evidence_id": "F000001:holdings",
        "transmission": statement(
            "若待核实回购线索获得公告支持，可能改变企业现金使用安排，实施细节仍需核对。",
            "F000001:news:0", "F000001:holdings"),
    }]
    item["news_assessment"] = {"status": "conditional_impacts", "explanation": None}
    return output


def test_lean_judgment_needs_no_filler_sections_or_comparison():
    analyses = [analysis(), analysis("000002")]
    funds, comparison, statuses, _ = strict_parse(lean_output(("000001", "000002")), analyses)
    assert set(funds) == {"000001", "000002"} and not comparison
    assert all(status.status == "ok" for status in statuses.values())
    for insight in funds.values():
        assert not any(getattr(insight, key) for key in module._SECTIONS)
        assert insight.decision.conditions is None and insight.decision.invalidation is None
        assert "本次输入未提供企业估值指标" in insight.decision.rationale.text
        assert "外部没有相关资料" in insight.decision.rationale.text


def test_live_rationale_requires_visible_fact_even_with_legacy_evidence():
    output = lean_output()
    output["funds"][0]["decision"]["rationale"] = statement(
        "区间回撤提示经营验证的重要性，仍需持续关注盈利变化。", "F000001:return_1m")
    output["funds"][0]["evidence"] = [statement(
        "可供研究核对的区间依据：{{F000001:return_1m}}", "F000001:return_1m")]
    result = strict_parse(output)
    assert result[2]["000001"].diagnostics["code"] == "evidence_placeholder_missing"
    assert result[2]["000001"].diagnostics["section"] == "rationale"
    # Explicit legacy parsing continues to accept its previous narrative shape.
    analyses = [analysis()]
    assert module._parse_response(json.dumps(output), analyses, *module._build_context(analyses, None))[0]


@pytest.mark.parametrize("code", ["021190", "018123"])
def test_accepted_run12_watch_is_rejected_as_an_unsupported_thesis(code):
    saved, analyses = run12_inputs()
    selected = [a for a in analyses if a.data.profile.code == code]
    output = lean_output((code,))
    decision = copy.deepcopy(saved["interpretation"]["funds"][code]["decision"])
    output["funds"][0]["decision"] = decision
    # Exact retained rationale first fails the new fact-anchor requirement.
    result = strict_parse(output, selected, saved["news_evidence"])
    assert result[2][code].diagnostics["code"] == "evidence_placeholder_missing"
    # Adding only a real metric slot cannot rescue the original generic
    # holdings/cashflow or QDII/FX condition. No rejected model text is invented.
    metric = next(ref for ref in decision["rationale"]["evidence_ids"]
                  if ":return_" in ref or ":max_drawdown_" in ref)
    decision["rationale"]["text"] += "{{" + metric + "}}"
    result = strict_parse(output, selected, saved["news_evidence"])
    assert result[2][code].diagnostics["code"] == "decision_observation"


def test_run12_bound_repurchase_can_keep_conditional_news_without_duplicate_sections():
    saved, analyses = run12_inputs()
    code = "020356"
    selected = [a for a in analyses if a.data.profile.code == code]
    retained = copy.deepcopy(saved["interpretation"]["funds"][code])
    item = lean_output((code,))["funds"][0]
    item["decision"] = retained["decision"]
    item["decision"]["rationale"]["text"] += "{{F020356:return_1m}}"
    item["news_assessment"] = retained["news_assessment"]
    item["news_impacts"] = [{key: impact[key] for key in (
        "news_evidence_id", "relationship_evidence_id", "transmission")} for impact in retained["news_impacts"]]
    funds, _, statuses, _ = strict_parse({"funds": [item], "comparison": []}, selected, saved["news_evidence"])
    assert statuses[code].status == "ok", statuses[code].diagnostics
    assert funds[code].decision.direction == "watch"
    assert funds[code].news_impacts[0].verification == "unverified_lead"
    assert funds[code].news_impacts[0].magnitude == "unknown"
    assert "财务调整项目与现金流依据" in funds[code].news_assessment.explanation.text


@pytest.mark.parametrize("direction", ["watch", "consider_buy", "consider_reduce"])
def test_no_direction_can_promote_holdings_into_financial_evidence(direction):
    output = bound_watch()
    decision = output["funds"][0]["decision"]
    decision["direction"] = direction
    decision["conditions"] = statement(
        "需核验历史股票所涉企业盈利是否有现金流支持，以判断经营支撑是否成立。", "F000001:holdings")
    result = strict_parse(output, news=news_bundle(title="历史公司回购公告"))
    assert result[2]["000001"].diagnostics["code"] == "decision_observation"


@pytest.mark.parametrize("direction", ["consider_buy", "consider_reduce", "watch"])
def test_investment_case_cannot_anchor_only_to_generic_input_gap(direction):
    output = bound_watch()
    decision = output["funds"][0]["decision"]
    decision["direction"] = direction
    decision["rationale"]["text"] = "{{F000001:research_coverage}}待核实回购线索尚不足以形成经营判断。"
    decision["rationale"]["evidence_ids"].append("F000001:research_coverage")
    result = strict_parse(output, news=news_bundle(title="历史公司回购公告"))
    assert result[2]["000001"].diagnostics["code"] == "evidence_placeholder_missing"


@pytest.mark.parametrize("field", ["conditions", "invalidation", "rationale"])
def test_null_cannot_hide_required_investment_case_content(field):
    output = bound_watch()
    output["funds"][0]["decision"][field] = None
    assert not strict_parse(output, news=news_bundle(title="历史公司回购公告"))[0]


def test_bound_headline_variable_passes_without_asserting_event_or_returns():
    funds, _, statuses, _ = strict_parse(bound_watch(), news=news_bundle(title="历史公司回购公告"))
    assert statuses["000001"].status == "ok", statuses["000001"].diagnostics
    assert funds["000001"].decision.direction == "watch"
    assert funds["000001"].news_impacts[0].magnitude == "unknown"


def test_topic_in_boilerplate_or_unbound_headline_does_not_support_condition():
    output = bound_watch()
    for news in (news_bundle(warnings=["关注回购"], relation_basis="回购相关历史关系"),
                 news_bundle(title="历史公司回购公告", source_date="2025-12-31")):
        result = strict_parse(output, news=news)
        assert result[2]["000001"].diagnostics["code"] == "decision_observation"


def test_bound_financial_headline_must_match_the_observed_variable():
    output = bound_watch()
    item = output["funds"][0]
    item["decision"]["rationale"]["text"] = (
        "{{F000001:return_1m}}现金流线索提供了待检验经营问题，标题尚不足以确认变化。")
    item["decision"]["conditions"]["text"] = "需核验待核实现金流线索对应的报表正文，才能检验经营现金来源。"
    item["decision"]["invalidation"]["text"] = "若现金流线索被原始公告澄清，原有经营观察条件需要重审。"
    item["news_impacts"][0]["transmission"]["text"] = "若待核实现金流线索得到报表支持，可能改变对企业经营资金来源的理解。"
    assert strict_parse(output, news=news_bundle(title="历史公司现金流披露公告"))[0]
    assert not strict_parse(output, news=news_bundle(title="历史公司盈利观点文章"))[0]


@pytest.mark.parametrize("bad_text", [
    "{{F000001:return_1m}}本基金目前持有相关公司，经营改善已经确认。",
    "{{F000001:return_1m}}区间收益达到百分之二十，研究判断已经明确。",
    "{{F000001:return_1m}}新闻推动净值上涨，后续应该买入。",
])
def test_lean_format_does_not_relax_currentness_numeric_or_causality_guards(bad_text):
    output = lean_output()
    output["funds"][0]["decision"]["rationale"] = statement(bad_text, "F000001:return_1m", "F000001:holdings")
    assert not strict_parse(output)[0]


@pytest.mark.parametrize("title", ["历史公司回购公告", "历史公司 announces buyback"])
def test_failed_only_repair_preserves_source_topics_and_valid_fund(title):
    good = lean_output(("000002",))["funds"][0]
    bad = bound_watch()["funds"][0]
    bad["decision"]["conditions"]["evidence_ids"] = ["RAW_REJECTED_NOT_FOR_PROMPT"]
    calls = []
    output = {"funds": [bad, good], "comparison": []}
    result = module.interpret_funds(
        [analysis(holdings=True), analysis("000002")], news_bundle(title=title),
        config=configuration(), analyzer_factory=sequence_factory([output, bound_watch()], calls))
    assert result.status == "ok" and len(calls) == 2
    assert result.fund_statuses["000001"].repair_status == "succeeded"
    assert result.fund_statuses["000002"].repair_status == "not_attempted"
    assert "RAW_REJECTED_NOT_FOR_PROMPT" not in calls[1][0] and "F000002:" not in calls[1][0]


def test_prompt_requests_one_judgment_and_explicit_data_boundaries_not_filler():
    analyses = [analysis(holdings=True)]
    facts, comparison = module._build_context(analyses, news_bundle(title="历史公司回购公告"))
    prompt = module._make_prompt(analyses, facts, comparison)
    assert "默认均为空数组" in prompt and "undetermined时默认均为null" in prompt
    assert "多基金comparison可为空" in prompt
    assert "历史持仓名单不等于盈利或现金流证据" in prompt
    assert "不要把估值或财报未输入写成外部不存在" in prompt
    assert "新闻传导只在news_impacts展开" in prompt
    assert '"observable_topics": {"F000001:news:0": ["回购"]}' in prompt


def test_fact_slot_alone_is_not_a_research_judgment():
    output = lean_output()
    output["funds"][0]["decision"]["rationale"]["text"] = "{{F000001:research_coverage}}"
    result = strict_parse(output)
    assert result[2]["000001"].diagnostics["code"] == "text_length"


def test_observation_cannot_borrow_a_later_relationship_snapshot():
    result = strict_parse(bound_watch(), news=news_bundle(
        title="历史公司回购公告", published_date="2026-03-30"))
    assert result[2]["000001"].diagnostics["code"] == "decision_observation"


def test_legacy_without_decision_cannot_become_an_empty_success():
    output = lean_output()
    output["funds"][0].pop("decision")
    analyses = [analysis()]
    funds, _, statuses, _ = module._parse_response(
        json.dumps(output), analyses, *module._build_context(analyses, None))
    assert not funds and statuses["000001"].diagnostics["code"] == "section_size"


@pytest.mark.parametrize("title", ["历史公司 announces buyback", "历史公司 announces BUYBACK"])
def test_explicit_english_buyback_alias_keeps_bound_watch_supported(title):
    funds, _, statuses, _ = strict_parse(bound_watch(), news=news_bundle(title=title))
    assert statuses["000001"].status == "ok", statuses["000001"].diagnostics
    assert funds["000001"].news_impacts[0].verification == "unverified_lead"


@pytest.mark.parametrize("headline,condition", [
    ("营业收入", "营收"), ("营收", "营业收入"), ("revenue", "营收"),
])
def test_revenue_alias_is_canonical_on_headline_and_condition(headline, condition):
    output = bound_watch()
    item = output["funds"][0]
    item["decision"]["rationale"]["text"] = (
        "{{F000001:return_1m}}营收线索提供经营验证对象，但标题不足以确认其变化。")
    item["decision"]["conditions"]["text"] = (
        f"需核验待核实{condition}线索对应的报表正文，才能判断经营规模是否改变。")
    item["decision"]["invalidation"]["text"] = "若营收线索被原始公告澄清，原有经营观察条件需要重审。"
    item["news_impacts"][0]["transmission"]["text"] = (
        "若待核实营收线索得到财报支持，可能改变对企业经营规模的理解。")
    news = news_bundle(title=f"历史公司 {headline} 增长公告")
    funds, _, statuses, _ = strict_parse(output, news=news)
    assert statuses["000001"].status == "ok", statuses["000001"].diagnostics
    assert funds["000001"].decision.direction == "watch"
    facts, comparison = module._build_context([analysis(holdings=True)], news)
    assert facts.news_observation_topics["F000001:news:0"] == {"营收"}
    assert '"observable_topics": {"F000001:news:0": ["营收"]}' in module._make_prompt(
        [analysis(holdings=True)], facts, comparison)


@pytest.mark.parametrize("token", ["prebuyback", "buybacker", "buyback2", "buyback_tag"])
def test_english_alias_cannot_match_inside_larger_word(token):
    result = strict_parse(bound_watch(), news=news_bundle(title=f"历史公司 announces {token}"))
    assert result[2]["000001"].diagnostics["code"] == "decision_observation"
    assert not module._observation_topics("prerevenue revenue2 revenue_tag")


def test_english_alias_in_warning_or_unbound_headline_is_not_observation_support():
    for news in (
        news_bundle(warnings=["buyback"], relation_basis="buyback 历史关系"),
        news_bundle(title="历史公司 announces buyback", source_date="2025-12-31"),
    ):
        result = strict_parse(bound_watch(), news=news)
        assert result[2]["000001"].diagnostics["code"] == "decision_observation"
