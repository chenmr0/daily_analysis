"""Offline regression for optional conditional research and bound news impacts."""
import json

import pytest

from src.services import fund_interpretation as module
from tests.test_fund_interpretation import analysis, valid_output


def statement(text, *refs):
    return {"text": text, "evidence_ids": list(refs)}


def decision_output(direction="consider_buy"):
    result = valid_output()
    result["funds"][0]["decision"] = {
        "direction": direction,
        "rationale": statement("历史股票持仓提示经营风险需要跟踪，区间表现仅作研究参考。",
                               "F000001:holdings", "F000001:return_1m"),
        "conditions": statement("需核验历史股票持仓所对应企业的经营变化，确认研究假设适用。", "F000001:holdings"),
        "invalidation": statement("若后续披露改变历史股票持仓依据，则需重新评估研究假设。", "F000001:holdings"),
    }
    return result


def parse(output, *, holdings=True, news=None):
    analyses = [analysis(holdings=holdings)]
    facts, comparison = module._build_context(analyses, news)
    return module._parse_response(json.dumps(output, ensure_ascii=False), analyses, facts, comparison)


@pytest.mark.parametrize("direction", sorted(module.RESEARCH_DIRECTIONS))
def test_decision_is_additive_and_enum_owned(direction):
    funds, _, statuses, _ = parse(decision_output(direction))
    assert statuses["000001"].status == "ok"
    assert funds["000001"].decision.direction == direction
    assert funds["000001"].decision.conditions.evidence_ids == ["F000001:holdings"]
    legacy = valid_output()
    legacy["funds"][0].pop("decision")
    legacy["funds"][0].pop("news_assessment")
    assert parse(legacy)[0]["000001"].decision is None


@pytest.mark.parametrize("refs", [["F000001:return_1m"], ["F000001:holdings"],
                                  ["F000001:return_1m", "F000001:profile"]])
def test_action_needs_metric_and_substantive_relationship(refs):
    output = decision_output()
    output["funds"][0]["decision"]["rationale"] = statement(
        "区间表现仅能提供历史参考，相关研究假设还需要核验。", *refs)
    funds, _, statuses, _ = parse(output)
    assert not funds and statuses["000001"].diagnostics["code"] == "decision_evidence"


@pytest.mark.parametrize("text", [
    "应该立即买入并加仓，等待后续行情上升。",
    "本基金目前持有历史公司，因此值得关注。",
    "历史收益达到了百分之二十，值得进一步关注。",
    "未来一定会盈利，应当保持长期乐观。",
    "新闻推动净值上涨，后续表现仍然值得关注。",
    "当前应该卖出，后续无需承担任何风险。",
    "本基金表现强于其他基金，可以观察。",
])
@pytest.mark.parametrize("section", ["rationale", "conditions", "invalidation"])
def test_decision_never_relaxes_prose_guards(text, section):
    output = decision_output()
    output["funds"][0]["decision"][section]["text"] = text
    assert not parse(output)[0]


def test_invalid_direction_and_null_are_not_silently_watch():
    output = decision_output("buy_now")
    assert not parse(output)[0]
    output["funds"][0]["decision"] = None
    assert not parse(output)[0]


def news_bundle(**changes):
    item = dict(category="holding", title="历史公司经营计划公告", url="https://example.test/news",
                published_date="2026-06-30", date_status="fresh", evidence_scope="related_event",
                model_eligible=True, source_url="https://example.test/holdings", source_date="2026-03-31",
                relation_basis="历史报告期股票关系")
    item.update(changes)
    return {"000001": {"items": [item], "related_assets": [dict(category="holding", name="历史公司",
                code="TEST", source_url="https://example.test/holdings", source_date="2026-03-31", verified=True)]}}


def impact_output():
    output = decision_output()
    output["funds"][0]["news_assessment"] = {"status": "conditional_impacts", "explanation": None}
    output["funds"][0]["news_impacts"] = [{
        "news_evidence_id": "F000001:news:0", "relationship_evidence_id": "F000001:holdings",
        "transmission": statement("若待核验线索得到确认，可能改变相关企业经营预期，需核验历史股票持仓资料。",
                                  "F000001:news:0", "F000001:holdings"),
    }]
    return output


def test_news_impact_is_bound_hypothesis_not_verified_event():
    funds, _, statuses, _ = parse(impact_output(), news=news_bundle())
    assert statuses["000001"].status == "ok", statuses["000001"].diagnostics
    impact = funds["000001"].news_impacts[0]
    assert impact.verification == "unverified_lead" and impact.magnitude == "unknown"


@pytest.mark.parametrize("changes", [dict(source_date="2025-12-31"), dict(source_url="https://example.test/other"),
                                     dict(title="无关公司经营计划公告"), dict(model_eligible=False),
                                     dict(date_status="stale")])
def test_news_relationship_mismatch_or_ineligible_lead_fails_closed(changes):
    assert not parse(impact_output(), news=news_bundle(**changes))[0]


@pytest.mark.parametrize("mutation", [
    lambda i: i.update(relationship_evidence_id="F000001:profile"),
    lambda i: i.update(verification="verified"),
    lambda i: i["transmission"].update(text="该待核验线索改变相关企业经营预期，需核验历史股票持仓资料。"),
    lambda i: i["transmission"].update(text="若待核验线索得到确认，净值可能上涨，需要继续核验资料。"),
    lambda i: i["transmission"].update(text="若待核验线索得到确认，可能推动净值上涨，需要继续核验资料。"),
    lambda i: i["transmission"].update(evidence_ids=["F000001:news:0", "F000001:holdings", "F000001:profile"]),
])
def test_news_impact_cannot_upgrade_or_decouple_evidence(mutation):
    output = impact_output()
    mutation(output["funds"][0]["news_impacts"][0])
    assert not parse(output, news=news_bundle())[0]


def test_news_claim_with_placeholders_still_cannot_split_guard():
    output = impact_output()
    output["funds"][0]["news_impacts"][0]["transmission"]["text"] = (
        "若待核验线索得到确认，可能推{{F000001:news:0}}动净值上涨，需核验历史股票持仓资料。")
    assert not parse(output, news=news_bundle())[0]


def test_news_binding_preserves_raw_index_after_malformed_item():
    bundle = news_bundle()
    bundle["000001"]["items"].insert(0, "invalid record")
    output = impact_output()
    impact = output["funds"][0]["news_impacts"][0]
    impact["news_evidence_id"] = "F000001:news:1"
    impact["transmission"]["evidence_ids"][0] = "F000001:news:1"
    assert parse(output, news=bundle)[0]["000001"].news_impacts[0].news_evidence_id == "F000001:news:1"


def test_cross_fund_news_citation_rejected_without_discarding_other_fund():
    output = valid_output(("000001", "000002"))
    output["funds"][0]["decision"] = decision_output()["funds"][0]["decision"]
    output["funds"][0]["decision"]["conditions"]["evidence_ids"] = ["F000002:holdings"]
    analyses = [analysis(holdings=True), analysis("000002", holdings=True)]
    facts, comparison = module._build_context(analyses, None)
    funds, _, statuses, _ = module._parse_response(json.dumps(output, ensure_ascii=False), analyses, facts, comparison)
    assert "000001" not in funds and "000002" in funds
    assert statuses["000001"].diagnostics["code"] == "citation_wrong_fund"


@pytest.mark.parametrize("changes", [
    {"value": None}, {"value": True}, {"value": False}, {"value": float("nan")},
    {"value": float("inf")}, {"value": -1}, {"value": -1.2},
    {"reason": "区间数据不完整"}, {"reason": ""}, {"start_date": None},
    {"start_date": "2026-05-30"}, {"end_date": None}, {"end_date": "2026-06-30"},
])
@pytest.mark.parametrize("direction", ["consider_buy", "consider_reduce"])
def test_action_rechecks_underlying_metric_not_display_fact(changes, direction):
    analyses = [analysis(holdings=True)]
    facts, comparison = module._build_context(analyses, None)
    # Keep a stale/displayable fact to prove kind='metric' is not sufficient.
    for key, value in changes.items():
        setattr(analyses[0].metrics["return_1m"], key, value)
    funds, _, statuses, _ = module._parse_response(
        json.dumps(decision_output(direction), ensure_ascii=False), analyses, facts, comparison)
    assert not funds and statuses["000001"].diagnostics["code"] == "decision_evidence"


@pytest.mark.parametrize("window", ["equal", "reversed", "stale", "future", "missing_metric"])
def test_action_rejects_invalid_metric_windows(window):
    from datetime import timedelta
    analyses = [analysis(holdings=True)]
    facts, comparison = module._build_context(analyses, None)
    metric = analyses[0].metrics["return_1m"]
    if window == "missing_metric":
        del analyses[0].metrics["return_1m"]
    elif window == "equal":
        metric.start_date = metric.end_date
    elif window == "reversed":
        metric.start_date = metric.end_date + timedelta(days=1)
    else:
        metric.end_date += timedelta(days=1 if window == "future" else -1)
    funds, _, statuses, _ = module._parse_response(
        json.dumps(decision_output(), ensure_ascii=False), analyses, facts, comparison)
    assert not funds and statuses["000001"].diagnostics["code"] == "decision_evidence"


@pytest.mark.parametrize("value,accepted", [(0, True), (.5, True), (-.1, False), (1, False), (1.2, False)])
def test_action_drawdown_metric_range(value, accepted):
    analyses = [analysis(holdings=True)]
    facts, comparison = module._build_context(analyses, None)
    analyses[0].metrics["max_drawdown_12m"].value = value
    output = decision_output()
    output["funds"][0]["decision"]["rationale"]["evidence_ids"][1] = "F000001:max_drawdown_12m"
    result = module._parse_response(json.dumps(output, ensure_ascii=False), analyses, facts, comparison)
    assert bool(result[0]) is accepted


@pytest.mark.parametrize("text", [
    "若待核验线索得到确认，本基金当前持有相关股票可能面临经营变化。",
    "若待核验线索得到确认，历史股票持仓现在可能面临经营变化。",
    "若待核验线索得到确认，未知股票持仓可能面临经营变化。",
    "若待核验线索得到确认，基金持有相关股票可能面临经营变化。",
])
def test_conditional_news_does_not_excuse_unknown_or_current_exposure(text):
    output = impact_output()
    output["funds"][0]["news_impacts"][0]["transmission"]["text"] = text
    assert not parse(output, news=news_bundle())[0]


def test_news_impact_rejects_relationship_snapshot_later_than_event():
    # Even a source marked fresh cannot make a later snapshot contemporaneous.
    funds, _, statuses, _ = parse(impact_output(), news=news_bundle(published_date="2026-03-01"))
    assert not funds and statuses["000001"].diagnostics["code"] == "news_relationship"


@pytest.mark.parametrize("text", [
    "你的风险承受能力较强，该基金适合你的资金安排与投资目标。",
    "结合你的收入稳定且期限充裕，该基金符合你的个人需求。",
    "你此前投资经验丰富，可以承受本基金的相关风险。",
    "个人收入稳定且期限充裕，能够承受相关波动风险。",
    "用户投资经验丰富，可以承受本基金的相关风险。",
    "该基金符合自身投资目标，风险情况可以接受。",
])
@pytest.mark.parametrize("section", ["rationale", "conditions", "invalidation", "conclusion"])
def test_personal_financial_assertions_rejected_in_all_narrative(text, section):
    output = decision_output()
    if section == "conclusion":
        output["funds"][0][section][0]["text"] = text
    else:
        output["funds"][0]["decision"][section]["text"] = text
    funds, _, statuses, _ = parse(output)
    assert not funds
    diagnostic = statuses["000001"].diagnostics
    assert diagnostic["code"] == "unsafe_claim"
    assert diagnostic["claim_category"] == "personal_assumption"
    assert text not in str(diagnostic)


@pytest.mark.parametrize("text", [
    "需核对个人期限与风险边界，再评估研究条件是否满足。",
    "若资金期限无法承受波动，则需重新评估研究条件。",
    "应核验个人风险承受能力，再判断研究条件是否适用。",
    "如果收入变化影响资金期限，则需重新评估研究条件。",
])
def test_neutral_personal_checks_and_hypotheses_remain_allowed(text):
    output = decision_output()
    output["funds"][0]["decision"]["conditions"]["text"] = text
    assert parse(output)[0]


def test_personal_address_cannot_be_hidden_by_evidence_placeholder():
    output = decision_output()
    output["funds"][0]["decision"]["conditions"]["text"] = (
        "你{{F000001:holdings}}的收入稳定，可以承受本基金的相关风险。")
    funds, _, statuses, _ = parse(output)
    assert not funds and statuses["000001"].diagnostics["claim_category"] == "personal_assumption"


def test_only_canonical_news_source_receives_impact_binding():
    import copy
    bundle = news_bundle()
    other = copy.deepcopy(bundle["000001"]["items"][0])
    other["url"] = "https://example.test/syndicated"
    bundle["000001"]["items"].append(other)
    analyses = [analysis(holdings=True)]
    facts, comparison = module._build_context(analyses, bundle)
    assert facts["F000001:news:0"].relationship_evidence_ids == ["F000001:holdings"]
    assert facts["F000001:news:1"].relationship_evidence_ids == []
    output = impact_output()
    impact = output["funds"][0]["news_impacts"][0]
    impact["news_evidence_id"] = "F000001:news:1"
    impact["transmission"]["evidence_ids"][0] = "F000001:news:1"
    assert not module._parse_response(json.dumps(output, ensure_ascii=False), analyses, facts, comparison)[0]


def test_third_undisplayed_news_lead_cannot_receive_structured_impact():
    import copy
    from src.core.fund_analysis import Holding
    bundle = news_bundle()
    analyses = [analysis(holdings=True)]
    for index, name in enumerate(("东方示例公司", "西方示例公司"), 1):
        holding_code = f"TEST{index}"
        analyses[0].data.holdings.holdings.append(Holding(holding_code, name, 2.0))
        item = copy.deepcopy(bundle["000001"]["items"][0])
        item.update(title=name + "经营计划公告", url=f"https://example.test/news{index}")
        bundle["000001"]["items"].append(item)
        asset = copy.deepcopy(bundle["000001"]["related_assets"][0])
        asset.update(name=name, code=holding_code)
        bundle["000001"]["related_assets"].append(asset)
    facts, comparison = module._build_context(analyses, bundle)
    lead_ids = [f"F000001:news:{index}" for index in range(3)]
    assert sum(bool(facts[ref].relationship_evidence_ids) for ref in lead_ids) == 2
    unselected = next(ref for ref in lead_ids if not facts[ref].relationship_evidence_ids)
    output = impact_output()
    impact = output["funds"][0]["news_impacts"][0]
    impact["news_evidence_id"] = unselected
    impact["transmission"]["evidence_ids"][0] = unselected
    assert not module._parse_response(json.dumps(output, ensure_ascii=False), analyses, facts, comparison)[0]


def test_company_revenue_is_not_mistaken_for_personal_finances():
    output = impact_output()
    output["funds"][0]["news_impacts"][0]["transmission"]["text"] = (
        "若待核验线索得到确认，企业收入可能面临变化，需核验历史股票持仓资料。")
    assert parse(output, news=news_bundle())[0]


@pytest.mark.parametrize("text", [
    "应先核对个人风险边界，再评估研究条件是否满足。",
    "需优先核验个人期限与风险边界，再评估研究条件。",
    "应进一步评估个人资金安排，再判断研究条件是否适用。",
    "所列区间净值表现均承压，提示研究重点宜放在回撤过程与风险承受能力的匹配，而非仅凭产品主题形成判断。",
])
def test_neutral_research_focus_and_verification_adverbs_are_allowed(text):
    output = decision_output()
    output["funds"][0]["decision"]["conditions"]["text"] = text
    assert parse(output)[0]
