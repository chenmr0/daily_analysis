"""Public Run 9 input projection; rejected narratives below are synthetic only."""
import copy
import hashlib
import json
from dataclasses import asdict, replace
from datetime import date, datetime
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.core.fund_analysis import (
    FundAnalysis, FundData, FundProfile, FundRelatedAsset, Holding, HoldingsSnapshot, Metric, NavPoint,
)
from src.services import fund_interpretation as module
from src.services.fund_analysis_service import run_fund_analysis
from tests.test_fund_interpretation import configuration, fake_factory, valid_output

FIXTURE = Path(__file__).parent / "fixtures/fund_evidence_guards_run9.json"
RETAIL_REF = "F025500:related:1"
RETAIL_TEXT = "报告期行业配置涉及批发和零售业，需核对适用资料：{{" + RETAIL_REF + "}}"


def run9_inputs():
    projection = json.loads(FIXTURE.read_text())
    analyses = []
    for item in projection["analyses"]:
        data = copy.deepcopy(item["data"])
        profile = data["profile"]
        profile["inception_date"] = date.fromisoformat(profile["inception_date"])
        data["profile"] = FundProfile(**profile)
        data["fetched_at"] = datetime.fromisoformat(data["fetched_at"])
        data["navs"] = [NavPoint(**{**p, "nav_date": date.fromisoformat(p["nav_date"])}) for p in data["navs"]]
        if data["holdings"]:
            holdings = data["holdings"]
            holdings["holdings"] = [Holding(**h) for h in holdings["holdings"]]
            for key in ("report_period_end", "published_at"):
                holdings[key] = date.fromisoformat(holdings[key]) if holdings[key] else None
            data["holdings"] = HoldingsSnapshot(**holdings)
        for asset in data["related_assets"]:
            for key in ("source_date", "published_at"):
                asset[key] = date.fromisoformat(asset[key]) if asset[key] else None
        data["related_assets"] = [FundRelatedAsset(**asset) for asset in data["related_assets"]]
        metrics = {key: Metric(**{**metric, **{
            field: date.fromisoformat(metric[field]) if metric[field] else None for field in ("start_date", "end_date")
        }}) for key, metric in item["metrics"].items()}
        analyses.append(FundAnalysis(FundData(**data), metrics, item["warnings"]))
    return analyses


def synthetic_output(text, refs, *, code="025500", section="evidence", analyses=None):
    analyses = analyses or run9_inputs()
    output = valid_output(tuple(a.data.profile.code for a in analyses))
    target = output if section == "comparison" else next(x for x in output["funds"] if x["fund_code"] == code)
    target[section] = [{"text": text, "evidence_ids": refs}]
    return output


def interpret(text, refs, *, code="025500", section="evidence", analyses=None, reverse=False):
    analyses = analyses or run9_inputs()
    output = synthetic_output(text, refs, code=code, section=section, analyses=analyses)
    if reverse:
        analyses.reverse()
        output["funds"].reverse()
    return module.interpret_funds(analyses, config=configuration(), analyzer_factory=fake_factory(output))


def test_projection_retains_real_inputs_and_never_invents_old_diagnostic_details():
    p = json.loads(FIXTURE.read_text())
    assert len(p["provenance"]["source_sha256"]) == hashlib.sha256().digest_size * 2
    assert "not Run 9 replay" in p["provenance"]["model_output"]
    inputs = {a.data.profile.code: a for a in run9_inputs()}
    assert inputs["025500"].data.related_assets[1].name == "批发和零售业"
    assert inputs["001672"].data.holdings.report_period_end == date(2026, 9, 30)
    assert inputs["021190"].data.holdings is None
    assert inputs["021190"].data.related_assets[0].category == "target_etf"
    for code, reason, index, size in (("001672", "historical_evidence_missing", 3, 91),
                                      ("025500", "chinese_numeral", 4, 87)):
        diagnostic = p["observed_diagnostics"][code]
        assert diagnostic["code"] == reason and diagnostic["fund_index"] == index
        assert diagnostic["section"] == "evidence" and diagnostic["item_index"] == 0
        assert diagnostic["counts"]["text_chars"] == size
        assert diagnostic["counts"]["citation_count"] == diagnostic["counts"]["placeholder_count"] == 2
        assert "guard_reason" not in diagnostic and "detection_view" not in diagnostic
        assert module.safe_interpretation_diagnostics(diagnostic) == diagnostic


def test_real_retained_accepted_statements_roundtrip_without_claiming_recovered_raw_output():
    analyses = run9_inputs()
    facts, _ = module._build_context(analyses, {})
    retained = json.loads(FIXTURE.read_text())["retained_accepted"]["018123"]
    # The retained record predates the display-only metric-label clarification.
    # Translate labels before recovering placeholders; do not change old bytes.
    retained = copy.deepcopy(retained)
    labels = {"近一个月收益": "约1个月区间净值变化", "近三个月收益": "约3个月区间净值变化",
              "近六个月收益": "约6个月区间净值变化", "近十二个月收益": "约12个月区间净值变化",
              "近十二个月最大回撤": "约12个月区间最大回撤"}
    for items in retained.values():
        for item in items:
            for old, new in labels.items():
                item["text"] = item["text"].replace(old, new)
    derived = copy.deepcopy(retained)
    for items in derived.values():
        for item in items:
            for ref in sorted(item["evidence_ids"], key=lambda key: -len(facts[key].text)):
                item["text"] = item["text"].replace(facts[ref].text, "{{" + ref + "}}")
    output = valid_output(tuple(a.data.profile.code for a in analyses))
    next(x for x in output["funds"] if x["fund_code"] == "018123").update(derived)
    result = module.interpret_funds(analyses, config=configuration(), analyzer_factory=fake_factory(output))
    assert result.status == "ok", result.diagnostics
    expanded = asdict(result.funds["018123"])
    assert {key: expanded[key] for key in module._SECTIONS} == retained
    # New mandatory fields are synthetic test scaffolding, not recovered output.
    assert expanded["decision"]["basis"] == "information_gap" and expanded["news_impacts"] == []
    assert set(expanded) == {*module._SECTIONS, "decision", "news_impacts", "news_assessment"}


@pytest.mark.parametrize("section", [*module._SECTIONS, "comparison"])
@pytest.mark.parametrize("spacing", ["", "　"])
def test_exact_cited_retail_industry_is_nonquantitative_in_every_scope(section, spacing):
    text = RETAIL_TEXT
    if spacing:
        text = text.replace("批发和零售业", spacing.join("批发和零售业"))
    result = interpret(text, [RETAIL_REF], section=section)
    assert result.status == "ok", result.diagnostics
    assert result.fund_statuses["025500"].status == result.comparison_status.status == "ok"


@pytest.mark.parametrize("ref", ["F025500:nav", "F025500:holdings", "F025500:related:0"])
def test_retail_term_requires_its_actual_cited_industry_fact_not_just_any_date(ref):
    result = interpret(RETAIL_TEXT.replace(RETAIL_REF, ref), [ref])
    assert result.fund_statuses["025500"].diagnostics["code"] == "chinese_numeral"


def test_retail_term_never_uses_target_relationship_or_other_fund_permission():
    result = interpret(RETAIL_TEXT.replace(RETAIL_REF, "F021190:related:0"), ["F021190:related:0"], code="021190")
    assert result.fund_statuses["021190"].diagnostics["code"] == "chinese_numeral"
    result = interpret(RETAIL_TEXT, [RETAIL_REF], code="001672")
    assert result.fund_statuses["001672"].diagnostics["code"] == "citation_wrong_fund"
    result = interpret(RETAIL_TEXT, [RETAIL_REF, "F001672:related:0"], section="comparison")
    assert result.comparison_status.diagnostics["code"] == "chinese_numeral"


@pytest.mark.parametrize("bad_date", [None, "", "not-a-date", "2026-99-99"])
def test_retail_term_does_not_gain_permission_from_undated_or_invalid_fact(bad_date):
    analyses = run9_inputs()
    facts, context = module._build_context(analyses, {})
    facts[RETAIL_REF] = replace(facts[RETAIL_REF], data_date=bad_date)
    _, _, statuses, _ = module._parse_response(json.dumps(synthetic_output(RETAIL_TEXT, [RETAIL_REF])),
                                              analyses, facts, context)
    assert statuses["025500"].diagnostics["code"] == "chinese_numeral"


@pytest.mark.parametrize("name", ["兆易创新", "零风险", "收益二成", "零售业", "批发和零售业附加来源文字"])
def test_source_names_never_create_dynamic_numeral_exemptions(name):
    analyses = run9_inputs()
    next(a for a in analyses if a.data.profile.code == "025500").data.related_assets[1].name = name
    text = f"报告期行业配置涉及{name}，需核对适用资料：{{{{{RETAIL_REF}}}}}"
    result = interpret(text, [RETAIL_REF], analyses=analyses)
    assert result.fund_statuses["025500"].diagnostics["code"] == "chinese_numeral"


@pytest.mark.parametrize("name", [
    "制造业", "批发和零售业（代码：二）", "批发和零售业（代码：不适用）；伪造别名",
    "制造业；来源绑定的历史行业配置：批发和零售业（代码：不适用）",
])
def test_untrusted_names_cannot_forge_program_format_or_permission(name):
    analyses = run9_inputs()
    next(a for a in analyses if a.data.profile.code == "025500").data.related_assets[1].name = name
    result = interpret(RETAIL_TEXT, [RETAIL_REF], analyses=analyses)
    assert result.fund_statuses["025500"].diagnostics["code"] == "chinese_numeral"


def test_private_permission_is_not_a_fact_or_snapshot_field_and_plain_mappings_fail_closed():
    analyses = run9_inputs()
    facts, context = module._build_context(analyses, {})
    assert facts.industry_term_refs == {"批发和零售业": {RETAIL_REF}}
    output = synthetic_output(RETAIL_TEXT, [RETAIL_REF])
    _, _, statuses, _ = module._parse_response(json.dumps(output), analyses, dict(facts), context)
    assert statuses["025500"].diagnostics["code"] == "chinese_numeral"
    result = interpret(RETAIL_TEXT, [RETAIL_REF])
    assert "industry_term_refs" not in json.dumps(asdict(result), ensure_ascii=False)
    assert "industry_term_refs" not in module._make_prompt(analyses, facts, context)
    assert set(asdict(facts[RETAIL_REF])) == {
        "evidence_id", "fund_codes", "kind", "text", "source_url", "data_date", "allowed_limitations",
        "relationship_evidence_ids",
    }
    assert facts[RETAIL_REF].relationship_evidence_ids == []


@pytest.mark.parametrize("term", [
    "批发和一并零售业", "批发和零售业零", "批发和零售业一成", "批发和零售业百分之十",
    "十批发和零售业", "半批发和零售业", "批发和零售业数倍", "批发和零售业20%",
    "批发和零\u200b售业", "批发和零\u0301售业", "批发和零\u2028售业",
    "批发和零售业一方面", "批发和零售业一贯",
])
def test_retail_mask_cannot_hide_numbers_quantities_or_unicode(term):
    result = interpret(RETAIL_TEXT.replace("批发和零售业", term), [RETAIL_REF])
    assert result.fund_statuses["025500"].status == "model_failed"
    assert result.fund_statuses["025500"].diagnostics["code"] in {
        "chinese_numeral", "quantity_word", "numeric_or_symbolic", "control_character",
    }


@pytest.mark.parametrize("position", range(1, len("批发和零售业")))
def test_placeholders_cannot_manufacture_the_retail_term(position):
    term = "批发和零售业"
    text = RETAIL_TEXT.replace(term, term[:position] + "{{F025500:nav}}" + term[position:])
    result = interpret(text, [RETAIL_REF, "F025500:nav"])
    diagnostic = result.fund_statuses["025500"].diagnostics
    assert diagnostic["code"] == "chinese_numeral" and diagnostic["detection_view"] == "boundary"


@pytest.mark.parametrize("separator", [".", "．", "…", "。", "，", ";", "！", "："])
@pytest.mark.parametrize("reverse", [False, True])
def test_history_cannot_cross_a_sentence_boundary(separator, reverse):
    text = "历史资料可参考" + separator + "本基金股票持仓集中于科技公司，需核对适用资料：{{F001672:holdings}}"
    result = interpret(text, ["F001672:holdings"], code="001672", reverse=reverse)
    diagnostic = result.fund_statuses["001672"].diagnostics
    assert diagnostic["code"] == "historical_evidence_missing"
    assert diagnostic["guard_reason"] == "historical_marker_missing"
    assert diagnostic["exposure_topic"] == "holdings" and diagnostic["detection_view"] == "joined"
    assert diagnostic["clause_index"] >= 1
    assert result.status == "partial" and len(result.funds) == 3
    assert result.comparison_status.diagnostics["code"] == "citation_invalid_fund"


@pytest.mark.parametrize("separator", [".", "．", "…", "。"])
def test_each_separately_qualified_clause_still_passes(separator):
    text = "历史股票持仓需核验" + separator + "报告期行业配置需核验，参考程序资料：{{F001672:holdings}}"
    assert interpret(text, ["F001672:holdings", "F001672:related:0"], code="001672").status == "ok"


@pytest.mark.parametrize("text,refs,reason,topic,missing,view", [
    ("历史目标ETF配置应核验，资料为{{F021190:related:0}}", ["F021190:related:0"],
     "target_not_snapshot", "target", None, "joined"),
    ("历史敞口集中于科技领域，资料为{{F001672:holdings}}", ["F001672:holdings", "F001672:related:0"],
     "topic_unspecified", "unspecified", None, "joined"),
    ("历史股票持仓需要核验，资料为{{F001672:related:0}}", ["F001672:related:0"],
     "dated_fact_missing", "holdings", "dated_holdings", "joined"),
    ("历史行业配置需要核验，资料为{{F001672:holdings}}", ["F001672:holdings"],
     "dated_fact_missing", "allocation", "dated_allocation", "joined"),
    ("历史股票持仓与行业配置需要核验，资料为{{F001672:nav}}", ["F001672:nav"],
     "dated_fact_missing", "mixed", "both", "joined"),
    ("股票持仓需要核验，资料为{{F001672:holdings}}", ["F001672:holdings"],
     "historical_marker_missing", "holdings", None, "joined"),
    ("报告期{{F001672:holdings}}持仓资料需要核验。", ["F001672:holdings"],
     "historical_marker_missing", "holdings", None, "boundary"),
])
def test_historical_subreasons_survive_parser_rewrap(text, refs, reason, topic, missing, view):
    code = "021190" if refs[0].startswith("F021190:") else "001672"
    result = interpret(text, refs, code=code)
    diagnostic = result.fund_statuses[code].diagnostics
    assert diagnostic["code"] == "historical_evidence_missing"
    assert diagnostic["guard_reason"] == reason and diagnostic["exposure_topic"] == topic
    assert diagnostic["detection_view"] == view and diagnostic.get("missing_fact_kind") == missing
    assert diagnostic == module.safe_interpretation_diagnostics(diagnostic)


def test_comparison_cannot_borrow_other_funds_or_topics_dates():
    result = interpret("历史股票持仓需要分别核验，资料为{{F001672:holdings}}",
                       ["F001672:holdings", "F021190:related:0"], section="comparison")
    diagnostic = result.comparison_status.diagnostics
    assert len(result.funds) == 4 and result.status == "partial"
    assert diagnostic["guard_reason"] == "dated_fact_missing" and diagnostic["missing_fact_kind"] == "dated_holdings"
    assert diagnostic["section"] == "comparison" and diagnostic["detection_view"] == "joined"


@pytest.mark.parametrize("text", ["持仓集中风险值得关注，应结合资料审慎评估。", "投资者应关注持仓风险，并结合资料评估不确定性。"])
def test_generic_risk_language_remains_a_documented_conservative_rejection(text):
    result = interpret(text, ["F001672:holdings"], code="001672", section="risks")
    assert result.fund_statuses["001672"].diagnostics["guard_reason"] == "historical_marker_missing"
    result = interpret("应核验持仓资料的适用时点", ["F001672:holdings"], code="001672", section="risks")
    assert result.status == "ok"


@pytest.mark.parametrize("suffix,expected", [
    ("当前行业配置集中于批发和零售业。", "current_exposure"),
    ("报告期行业配置未披露。", "unsupported_exposure_limitation"),
    ("建议买入本基金。", "unsafe_claim"),
    ("新闻推动净值上涨。", "unsafe_claim"),
    ("本基金保证收益。", "unsafe_claim"),
    ("基金净值表现为正。", "return_direction"),
])
def test_lexical_exception_never_skips_later_gates(suffix, expected):
    result = interpret(RETAIL_TEXT + suffix, [RETAIL_REF, "F025500:return_1m"])
    assert result.fund_statuses["025500"].diagnostics["code"] == expected


@pytest.mark.parametrize("value", ["SECRET_TEXT", "", None, [], {}, True, 123])
def test_new_diagnostic_fields_are_finite_typed_and_code_specific(value):
    details = dict(detection_view=value, guard_reason=value, exposure_topic=value,
                   missing_fact_kind=value, clause_index=value)
    safe = module.safe_interpretation_diagnostics({"stage": "narrative", "code": "historical_evidence_missing", **details})
    assert safe == {"stage": "narrative", "code": "historical_evidence_missing"} or (
        value == 123 and safe == {"stage": "narrative", "code": "historical_evidence_missing", "clause_index": 123})
    assert module.safe_interpretation_diagnostics({"stage": "citation", "code": "citation_unknown",
        "detection_view": "joined", "guard_reason": "dated_fact_missing", "exposure_topic": "holdings",
        "missing_fact_kind": "dated_holdings", "clause_index": 0}) == {"stage": "citation", "code": "citation_unknown"}


def test_new_details_are_scrubbed_and_preserved_across_full_service(tmp_path, caplog):
    caplog.set_level("INFO", logger="src.services.fund_analysis_service")
    data = {a.data.profile.code: a.data for a in run9_inputs()}
    output = valid_output(tuple(data))
    next(f for f in output["funds"] if f["fund_code"] == "025500")["evidence"] = [
        {"text": RETAIL_TEXT, "evidence_ids": [RETAIL_REF]}]
    rejected = "历史资料可参考.本基金股票持仓集中于科技公司，私密原句不可转存：{{F001672:holdings}}"
    next(f for f in output["funds"] if f["fund_code"] == "001672")["evidence"] = [
        {"text": rejected, "evidence_ids": ["F001672:holdings"]}]
    output["comparison"] = [{"text": "历史股票持仓需要核验，资料为{{F018123:nav}}", "evidence_ids": ["F018123:nav"]}]
    real = partial(module.interpret_funds, config=configuration(), analyzer_factory=fake_factory(output))

    def interpreter(*args, **kwargs):
        result = real(*args, **kwargs)
        for diagnostic in [result.diagnostics, result.fund_statuses["001672"].diagnostics,
                           result.comparison_status.diagnostics]:
            diagnostic.update(raw_response="SECRET_RAW", matched_word="SECRET_HIT", unknown_ref="SECRET_REF")
        result.comparison_status.diagnostics["detection_view"] = "SECRET_VIEW"
        return result

    report = run_fund_analysis(tuple(data), fetcher=Mock(fetch=Mock(side_effect=lambda code: data[code])),
                               include_news=False, include_interpretation=True, interpreter=interpreter, output_dir=tmp_path)
    raw = Path(report.snapshot_path).read_text()
    saved = json.loads(raw)
    assert saved["schema_version"] == 4 and len(saved["action_references"]) == 4
    assert saved["interpretation"]["status"] == "partial"
    assert set(saved["interpretation"]["funds"]) == {"021190", "018123", "025500"}
    for diagnostic in [saved["interpretation"]["diagnostics"],
                       saved["interpretation"]["fund_statuses"]["001672"]["diagnostics"]]:
        assert diagnostic["guard_reason"] == "historical_marker_missing" and diagnostic["detection_view"] == "joined"
    comparison = saved["interpretation"]["comparison_status"]["diagnostics"]
    assert comparison["missing_fact_kind"] == "dated_holdings" and "detection_view" not in comparison
    assert "historical\\_marker\\_missing" not in report.markdown
    assert "dated\\_fact\\_missing" not in report.markdown and "dated_fact_missing" in caplog.text
    assert "historical_marker_missing" in caplog.text
    for private in ("SECRET_RAW", "SECRET_HIT", "SECRET_REF", "SECRET_VIEW", "私密原句不可转存"):
        assert private not in raw + report.markdown + caplog.text
