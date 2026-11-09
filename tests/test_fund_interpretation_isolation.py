"""Actual Run 7 input projection with synthetic narratives, never a model replay."""
import copy
import json
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path

import pytest

from src.core.fund_analysis import FundAnalysis, FundData, FundProfile, FundRelatedAsset, Metric, NavPoint
from src.services import fund_interpretation as module
from tests.test_fund_interpretation import analysis, configuration, fake_factory, valid_output


def run7_analysis():
    value = json.loads((Path(__file__).parent / "fixtures/fund_missing_evidence_run7.json").read_text())
    item = value["analysis"]
    data = copy.deepcopy(item["data"])
    data["profile"]["inception_date"] = date.fromisoformat(data["profile"]["inception_date"])
    data["profile"] = FundProfile(**data["profile"])
    data["fetched_at"] = datetime.fromisoformat(data["fetched_at"])
    data["navs"] = [NavPoint(**{**p, "nav_date": date.fromisoformat(p["nav_date"])}) for p in data["navs"]]
    data["related_assets"] = [FundRelatedAsset(**asset) for asset in data["related_assets"]]
    metrics = {
        key: Metric(**{**m, "start_date": date.fromisoformat(m["start_date"]),
                       "end_date": date.fromisoformat(m["end_date"])})
        for key, m in item["metrics"].items()
    }
    return FundAnalysis(FundData(**data), metrics, item["warnings"])


def interpret(items, output, **kwargs):
    return module.interpret_funds(items, config=configuration(), analyzer_factory=fake_factory(output), **kwargs)


def narrative_output(text, refs, section="evidence"):
    output = valid_output(("021190",))
    if section == "evidence":
        text += "；核对本次输入依据：{{" + refs[0] + "}}"
    output["funds"][0][section] = [{"text": text, "evidence_ids": refs}]
    return output


def test_run7_projection_preserves_real_missing_holdings_not_a_synthetic_zero_position():
    item = run7_analysis()
    assert item.data.profile.code == "021190" and item.data.holdings is None
    assert item.data.profile.currency is None
    assert item.data.navs[-1].nav_date == date(2026, 11, 5)
    assert item.metrics["return_1m"].value == -0.0929558476575667
    assert item.data.related_assets[0].category == "target_etf"
    assert item.data.related_assets[0].weight_pct is None
    facts, _ = module._build_context([item], {})
    assert not any(f.kind in {"dated_holdings", "dated_allocation"} for f in facts.values())
    assert facts["F021190:holdings"].kind == "missing_holdings"
    assert facts["F021190:allocation"].kind == "missing_allocation"


@pytest.mark.parametrize("clause", module._MISSING_HOLDINGS_LIMITATIONS)
@pytest.mark.parametrize("section", module._SECTIONS)
def test_supported_missing_holdings_clauses_work_in_every_field(clause, section):
    output = narrative_output(clause + "，应继续核对后续资料。", ["F021190:holdings"], section)
    result = interpret([run7_analysis()], output)
    assert result.status == "ok", result.diagnostics
    assert result.fund_statuses["021190"].status == "ok"
    assert result.comparison_status.status == "not_applicable"


@pytest.mark.parametrize("clause", module._MISSING_ALLOCATION_LIMITATIONS)
def test_supported_industry_absence_requires_its_own_typed_fact(clause):
    output = narrative_output(clause + "，应继续核对后续资料。", ["F021190:allocation"])
    assert interpret([run7_analysis()], output).status == "ok"
    output = narrative_output(clause + "，应继续核对后续资料。", ["F021190:holdings"])
    result = interpret([run7_analysis()], output)
    assert result.status == "model_failed"
    assert result.diagnostics["code"] == "unsupported_exposure_limitation"


@pytest.mark.parametrize("text", [
    "持仓信息缺失但当前配置集中于科技行业，应核对后续资料。",
    "持仓信息缺失当前配置集中于科技行业，应核对后续资料。",
    "无法确认持仓变化目前持有科技公司，应核对后续资料。",
    "持仓信息缺失，基金配置集中于科技行业，应核对后续资料。",
    "缺少可用持仓信息且历史配置集中于科技行业，应核对后续资料。",
    "持仓信息缺失：当前配置集中于科技行业，应核对后续资料。",
    "持仓信息缺失（当前配置集中于科技行业），应核对后续资料。",
    "持仓信息缺失、当前配置集中于科技行业，应核对后续资料。",
    "持仓信息缺失；本基金为空仓，应核对后续资料。",
    "本基金没有持仓，所以风险有限，需要继续观察。",
    "持仓信息缺失基金已经空仓，应核对后续资料。",
    "持仓信息缺失，基金已经空仓，应核对后续资料。",
    "持仓信息缺失。基金已经空仓，应核对后续资料。",
    "持仓信息缺失股票仓位为空，应核对后续资料。",
    "持仓信息缺失，股票仓位为空，应核对后续资料。",
    "持仓信息缺失。股票仓位为空，应核对后续资料。",
    "持仓信息缺失无需承担股票风险，应核对后续资料。",
    "持仓信息缺失，无需承担股票风险，应核对后续资料。",
    "持仓信息缺失。无需承担股票风险，应核对后续资料。",
    "本基金股票持仓未披露，应核对后续资料。",
    "缺失持仓证明没有配置，应核对后续资料。",
    "本基金历史持仓集中于科技行业，应核对后续资料。",
    "持仓信息缺失，收益为零，应核对后续资料。",
    "持仓信息缺失，预计收益达到20%，应核对后续资料。",
    "持仓信息缺失，建议买入并加仓，应核对后续资料。",
    "持仓信息缺失，新闻推动净值上涨，应核对后续资料。",
    "持仓信息缺失，表现优于其他基金，应核对后续资料。",
    "持仓信息缺失\u200b当前配置集中于科技行业，应核对后续资料。",
    "持仓信息缺失{{F021190:holdings}}当前配置集中于科技行业。",
])
def test_missing_clauses_never_exempt_unsafe_or_positive_exposure_claims(text):
    output = narrative_output(text, ["F021190:holdings"], "conclusion")
    result = interpret([run7_analysis()], output)
    assert result.status == "model_failed" and not result.funds and not result.comparison
    assert result.fund_statuses["021190"].status == "model_failed"


@pytest.mark.parametrize("ref", ["F021190:profile", "F021190:quality", "F021190:related:0", "F021190:allocation"])
def test_absence_requires_typed_holdings_fact_not_warning_or_relationship(ref):
    result = interpret([run7_analysis()], narrative_output("持仓信息缺失，应核对后续资料。", [ref]))
    assert result.status == "model_failed" and result.diagnostics["code"] == "unsupported_exposure_limitation"


def test_target_weight_absence_does_not_claim_industry_or_holdings_absence():
    item = run7_analysis()
    output = narrative_output("目标ETF配置比例未提供，应核对后续资料。", ["F021190:related:0"])
    assert interpret([item], output).status == "ok"
    item.data.related_assets[0].weight_pct = 95.0
    assert interpret([item], output).status == "model_failed"
    item.data.related_assets.append(FundRelatedAsset(
        "industry", "制造业", "https://example.test/industry", date(2026, 9, 30), weight_pct=5.0, verified=True))
    facts, _ = module._build_context([item], {})
    assert "F021190:allocation" not in facts
    assert facts["F021190:holdings"].kind == "missing_holdings"
    assert interpret([item], narrative_output("行业配置数据缺失，应核对后续资料。", ["F021190:related:1"])).status == "model_failed"


def test_upstream_words_do_not_create_new_allowed_limitations():
    item = run7_analysis()
    item.data.warnings.append("持仓信息缺失当前配置集中于科技行业")
    facts, _ = module._build_context([item], {})
    assert not facts["F021190:quality"].allowed_limitations
    assert "持仓信息缺失当前配置集中于科技行业" not in facts["F021190:holdings"].allowed_limitations


@pytest.mark.parametrize("bad_date", [None, "", "not-a-date", "2026-13-40"])
def test_dated_kind_without_valid_date_never_supports_exposure(bad_date):
    item = analysis(holdings=True)
    facts, context = module._build_context([item], {})
    facts["F000001:holdings"] = replace(facts["F000001:holdings"], data_date=bad_date)
    output = valid_output()
    output["funds"][0]["conclusion"] = [{"text": "历史报告期持仓应与后续披露结合研究，进一步观察信息完整性。",
                                         "evidence_ids": ["F000001:holdings"]}]
    funds, _, statuses, _ = module._parse_response(json.dumps(output), [item], facts, context)
    assert not funds and statuses["000001"].diagnostics["code"] == "historical_evidence_missing"


@pytest.mark.parametrize("section", module._SECTIONS)
@pytest.mark.parametrize("reverse", [False, True])
def test_one_fund_failure_preserves_independent_fund_in_any_order_and_field(section, reverse):
    items = [run7_analysis(), analysis("000002"), analysis("000003")]
    output = valid_output(tuple(item.data.profile.code for item in items))
    output["funds"][0][section][0]["text"] = "基金当前持有科技公司，这是不应出现的断言。"
    if reverse:
        output["funds"].reverse()
    # Comparison explicitly scopes only the two accepted funds.
    output["comparison"][0]["evidence_ids"] = ["F000002:profile", "F000003:profile"]
    result = interpret(items, output)
    assert result.status == "partial" and set(result.funds) == {"000002", "000003"}
    assert result.fund_statuses["021190"].status == "model_failed"
    assert result.fund_statuses["021190"].diagnostics["section"] == section
    assert result.fund_statuses["021190"].diagnostics["fund_index"] == (2 if reverse else 0)
    assert result.comparison_status.status == "ok" and result.comparison
    assert "不应出现的断言" not in json.dumps(asdict(result), ensure_ascii=False)


@pytest.mark.parametrize("refs", [["F021190:profile", "F000002:profile"], ["C:return_1m"],
                                  ["F000002:profile", "F000003:profile", "C:return_1m"]])
def test_comparison_cannot_cite_failed_scope_including_aggregate_facts(refs):
    items = [run7_analysis(), analysis("000002"), analysis("000003")]
    output = valid_output(("021190", "000002", "000003"))
    output["funds"][0]["conclusion"][0]["text"] = "基金当前持有科技公司，应该核对后续资料。"
    output["comparison"][0]["evidence_ids"] = refs
    result = interpret(items, output)
    assert result.status == "partial" and len(result.funds) == 2 and not result.comparison
    assert result.comparison_status.diagnostics["code"] == "citation_invalid_fund"


@pytest.mark.parametrize("cross_ref", ["F000002:holdings", "F000002:allocation"])
def test_cross_fund_missing_facts_are_rejected_but_valid_fund_survives(cross_ref):
    items = [run7_analysis(), analysis("000002")]
    output = valid_output(("021190", "000002"))
    output["funds"][0]["evidence"] = [{"text": "持仓信息缺失，应核对后续资料：{{" + cross_ref + "}}",
                                       "evidence_ids": [cross_ref]}]
    result = interpret(items, output)
    assert result.status == "partial" and set(result.funds) == {"000002"}
    assert result.fund_statuses["021190"].diagnostics["code"] == "citation_wrong_fund"
    assert not result.comparison


def test_missing_claim_in_comparison_requires_support_for_all_cited_funds():
    items = [run7_analysis(), analysis("000002", holdings=True)]
    output = valid_output(("021190", "000002"))
    output["comparison"] = [{"text": "持仓信息缺失，应核对后续资料后再研究。",
                             "evidence_ids": ["F021190:holdings", "F000002:holdings"]}]
    result = interpret(items, output)
    assert result.status == "partial" and len(result.funds) == 2 and not result.comparison
    assert result.comparison_status.diagnostics["code"] == "unsupported_exposure_limitation"


@pytest.mark.parametrize("mutation,code", [
    (lambda o: o["funds"][1].update(fund_code="021190"), "fund_identity"),
    (lambda o: o["funds"][1].update(fund_code="999999"), "fund_identity"),
    (lambda o: o["funds"][1].update(extra="bad"), "fund_schema"),
    (lambda o: o["funds"].pop(), "fund_count"),
    (lambda o: o.update(comparison=None), "section_size"),
    (lambda o: o.update(comparison=[]), "section_size"),
    (lambda o: o.update(extra="bad"), "root_schema"),
])
def test_global_preflight_cannot_be_bypassed_by_failed_early_fund(mutation, code):
    items = [run7_analysis(), analysis("000002")]
    output = valid_output(("021190", "000002"))
    output["funds"][0]["conclusion"][0]["text"] = "基金当前持有科技公司，应该核对后续资料。"
    mutation(output)
    result = interpret(items, output)
    assert result.status == "model_failed" and not result.funds and not result.comparison
    assert result.diagnostics["code"] == code
    assert set(result.fund_statuses) == {"021190", "000002"}
    assert all(scope.status == "model_failed" for scope in result.fund_statuses.values())


def test_unavailable_metric_is_presented_only_as_program_owned_fact_not_zero():
    item = run7_analysis()
    item.metrics["return_1m"] = Metric(None, "缺少核验资料")
    output = narrative_output("资料限制需保留不确定性，应核对程序计算依据。", ["F021190:return_1m"])
    result = interpret([item], output)
    assert result.status == "ok"
    assert result.facts["F021190:return_1m"].kind == "unavailable_metric"
    assert "不可用" in result.funds["021190"].evidence[0].text
    assert "不将缺数解释为无风险或零收益" in result.funds["021190"].evidence[0].text


@pytest.mark.parametrize("claim", ["报告期股票仓位为空", "历史持仓已清仓", "历史配置为无", "历史资料无需承担股票风险"])
def test_dated_evidence_does_not_authorize_negative_exposure_or_risk_denial(claim):
    output = valid_output()
    output["funds"][0]["conclusion"] = [{"text": claim + "，应继续核对后续资料。", "evidence_ids": ["F000001:holdings"]}]
    assert interpret([analysis(holdings=True)], output).status == "model_failed"


@pytest.mark.parametrize("text", [
    "持仓信息{{F021190:profile}}缺失，应继续核对后续资料。",
    "未取得可用股票{{F021190:profile}}持仓数据，应继续核对后续资料。",
    "目标ETF配置{{F021190:profile}}比例未提供，应继续核对后续资料。",
])
def test_fact_slots_cannot_glue_fragments_into_permitted_missing_clauses(text):
    output = narrative_output(text, ["F021190:holdings", "F021190:profile", "F021190:related:0"], "conclusion")
    assert interpret([run7_analysis()], output).status == "model_failed"


@pytest.mark.parametrize("section", module._SECTIONS)
@pytest.mark.parametrize("direction", ["holdings_as_allocation", "allocation_as_holdings"])
def test_dated_evidence_topics_cannot_substitute_in_any_section(section, direction):
    item = analysis(holdings=direction == "holdings_as_allocation")
    if direction == "holdings_as_allocation":
        ref = "F000001:holdings"
        claim = "历史行业配置集中于科技行业"
    else:
        item.data.related_assets = [FundRelatedAsset(
            "industry", "制造业", "https://example.test/industry", date(2026, 3, 31), weight_pct=5.0, verified=True)]
        ref = "F000001:related:0"
        claim = "历史股票持仓集中于科技公司"
    output = valid_output()
    text = claim + "，应继续核对后续资料。"
    if section == "evidence":
        text += "依据：{{" + ref + "}}"
    output["funds"][0][section] = [{"text": text, "evidence_ids": [ref]}]
    result = interpret([item], output)
    assert result.status == "model_failed"
    assert result.diagnostics["code"] == "historical_evidence_missing"


@pytest.mark.parametrize("claim,refs,accepted", [
    ("历史股票持仓集中于科技公司", ["F000001:holdings"], True),
    ("历史行业配置集中于制造行业", ["F000001:related:0"], True),
    ("历史股票持仓和行业配置存在交集", ["F000001:holdings", "F000001:related:0"], True),
    ("历史股票持仓和行业配置存在交集", ["F000001:holdings"], False),
    ("历史股票持仓和行业配置存在交集", ["F000001:related:0"], False),
    ("历史敞口集中于科技领域", ["F000001:holdings", "F000001:related:0"], False),
])
def test_historical_topics_require_correct_complete_evidence(claim, refs, accepted):
    item = analysis(holdings=True)
    item.data.related_assets = [FundRelatedAsset(
        "industry", "制造业", "https://example.test/industry", date(2026, 3, 31), weight_pct=5.0, verified=True)]
    output = valid_output()
    output["funds"][0]["conclusion"] = [{"text": claim + "，应继续核对后续资料。", "evidence_ids": refs}]
    result = interpret([item], output)
    assert (result.status == "ok") is accepted


@pytest.mark.parametrize("bad_name,failed,code", [
    ("甲方基金", True, "citation_invalid_fund"),
    ("甲方基金", False, "identity_unbound"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_comparison_named_funds_must_be_accepted_and_cited(bad_name, failed, code, reverse):
    items = [analysis("000001"), analysis("000002"), analysis("000003")]
    for item, name in zip(items, ["甲方基金", "乙方基金", "丙方基金"]):
        item.data.profile.name = name
    output = valid_output(("000001", "000002", "000003"))
    if failed:
        output["funds"][0]["conclusion"][0]["text"] = "基金当前持有科技公司，应继续核对后续资料。"
    if reverse:
        output["funds"].reverse()
    output["comparison"] = [{"text": bad_name + "与乙方基金的类型存在差异，应分别核对产品资料。",
                             "evidence_ids": ["F000002:profile", "F000003:profile"]}]
    result = interpret(items, output)
    assert result.status == "partial" and len(result.funds) == (2 if failed else 3)
    assert not result.comparison and result.comparison_status.diagnostics["code"] == code
    output["comparison"][0]["text"] = "乙方基金与丙方基金的类型存在差异，应分别核对产品资料。"
    result = interpret(items, output)
    assert result.comparison_status.status == "ok" and result.comparison


def test_historical_topic_matching_applies_to_every_comparison_fund():
    items = [analysis(), analysis("000002", holdings=True)]
    items[0].data.related_assets = [FundRelatedAsset(
        "industry", "制造业", "https://example.test/industry", date(2026, 3, 31), weight_pct=5.0, verified=True)]
    output = valid_output(("000001", "000002"))
    output["comparison"] = [{"text": "历史行业配置集中于科技行业，应分别核对适用资料。",
                             "evidence_ids": ["F000001:related:0", "F000002:holdings"]}]
    result = interpret(items, output)
    assert result.status == "partial" and len(result.funds) == 2 and not result.comparison
    assert result.comparison_status.diagnostics["code"] == "historical_evidence_missing"


@pytest.mark.parametrize("claim", ["持仓数据暂未披露", "当前持仓数据未披露", "持仓数据未披露"])
def test_input_missing_does_not_prove_manager_non_disclosure(claim):
    result = interpret([run7_analysis()], narrative_output(claim + "，应继续核对后续资料。", ["F021190:holdings"]))
    assert result.status == "model_failed"
    assert result.diagnostics["code"] in {"current_exposure", "unsupported_exposure_limitation"}
    corrected = narrative_output("本次未取得可用股票持仓数据，应继续核对后续资料。", ["F021190:holdings"])
    assert interpret([run7_analysis()], corrected).status == "ok"


@pytest.mark.parametrize("text", [
    "基金买{{F000001:profile}}入科技公司，应继续核对后续资料。",
    "基金目前{{F000001:profile}}历史持仓集中于科技公司，应继续核对后续资料。",
    "基金历史持{{F000001:profile}}有科技公司，应继续核对后续资料。",
    "新闻推{{F000001:profile}}动净值上涨，应继续核对后续资料。",
    "本基金优{{F000001:profile}}于其他基金，应继续核对后续资料。",
    "基金现在持{{F000001:profile}}仓集中于科技公司，应继续核对后续资料。",
    "持仓信息缺失；持仓信息{{F000001:profile}}缺失，应继续核对后续资料。",
])
def test_placeholder_boundaries_cannot_split_original_guarded_words_or_currentness(text):
    output = valid_output()
    output["funds"][0]["conclusion"] = [{"text": text, "evidence_ids": ["F000001:profile", "F000001:holdings"]}]
    # A split exposure word must still require dated evidence; other cases
    # must fail even with genuine dated holdings beside the malicious text.
    has_holdings = "历史持{{" not in text and "持仓信息缺失" not in text
    assert interpret([analysis(holdings=has_holdings)], output).status == "model_failed"


@pytest.mark.parametrize("claim", [
    "已披露历史目标ETF配置集中于科技行业", "已披露历史目标ETF权重突出",
    "历史配置主要来自目标ETF", "历史目标基金持仓集中于科技公司",
    "历史联接标的配置集中于科技行业",
])
@pytest.mark.parametrize("source_kind", ["holdings", "industry", "both"])
def test_target_identity_cannot_borrow_other_topics_dates(claim, source_kind):
    item = run7_analysis()
    refs = ["F021190:related:0"]
    if source_kind in {"holdings", "both"}:
        item.data.holdings = analysis(holdings=True).data.holdings
        refs.append("F021190:holdings")
    if source_kind in {"industry", "both"}:
        item.data.related_assets.append(FundRelatedAsset(
            "industry", "制造业", "https://example.test/industry", date(2026, 9, 30), weight_pct=5.0, verified=True))
        refs.append("F021190:related:1")
    output = narrative_output(claim + "，应继续核对后续资料。", refs, "conclusion")
    result = interpret([item], output)
    assert result.status == "model_failed" and result.diagnostics["code"] == "historical_evidence_missing"
