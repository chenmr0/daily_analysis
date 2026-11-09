"""Run 8 source projection and synthetic claims, not a replay of lost output."""
import copy
import json
import unicodedata
from dataclasses import asdict, replace
from datetime import date, datetime
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.core.fund_analysis import (
    FundAnalysis, FundData, FundProfile, FundRelatedAsset, Holding,
    HoldingsSnapshot, Metric, NavPoint,
)
from src.services import fund_interpretation as module
from src.services.fund_analysis_service import run_fund_analysis
from tests.test_fund_interpretation import configuration, fake_factory, valid_output

FIXTURE = Path(__file__).parent / "fixtures/fund_claim_guard_run8.json"
RETURN_RISK = "历史净值表现不保证收益，仍需审慎评估风险。"
RELATION_RISK = "来源关联不构成收益归因，仍需独立核验。"
NEWS_RISK = "关联线索不构成收益归因，仍需独立核验。"


def run8_inputs():
    projection = json.loads(FIXTURE.read_text())
    analyses = []
    for item in projection["analyses"]:
        data = copy.deepcopy(item["data"])
        profile = data["profile"]
        profile["inception_date"] = date.fromisoformat(profile["inception_date"])
        data["profile"] = FundProfile(**profile)
        data["fetched_at"] = datetime.fromisoformat(data["fetched_at"])
        data["navs"] = [NavPoint(**{**p, "nav_date": date.fromisoformat(p["nav_date"])}) for p in data["navs"]]
        holdings = data["holdings"]
        holdings["holdings"] = [Holding(**h) for h in holdings["holdings"]]
        for key in ("report_period_end", "published_at"):
            holdings[key] = date.fromisoformat(holdings[key]) if holdings[key] else None
        data["holdings"] = HoldingsSnapshot(**holdings)
        for asset in data["related_assets"]:
            for key in ("source_date", "published_at"):
                asset[key] = date.fromisoformat(asset[key]) if asset[key] else None
        data["related_assets"] = [FundRelatedAsset(**asset) for asset in data["related_assets"]]
        metrics = {key: Metric(**{**m, **{
            field: date.fromisoformat(m[field]) if m[field] else None for field in ("start_date", "end_date")
        }}) for key, m in item["metrics"].items()}
        analyses.append(FundAnalysis(FundData(**data), metrics, item["warnings"]))
    return analyses, projection["news_evidence"]


def interpret_claim(text, refs=None, *, code="018123", section="conclusion", output=None, reverse=False):
    analyses, news = run8_inputs()
    output = copy.deepcopy(output or valid_output(tuple(a.data.profile.code for a in analyses)))
    item = next(f for f in output["funds"] if f["fund_code"] == code)
    item[section] = [{"text": text, "evidence_ids": refs or [f"F{code}:return_1d"]}]
    if reverse:
        analyses.reverse()
        output["funds"].reverse()
    return module.interpret_funds(analyses, news, config=configuration(), analyzer_factory=fake_factory(output))


def test_projection_preserves_actual_sources_and_old_ambiguous_diagnostics():
    projection = json.loads(FIXTURE.read_text())
    assert len(projection["provenance"]["source_sha256"]) == 64
    assert "not Run 8 replay" in projection["provenance"]["model_output"]
    analyses, news = run8_inputs()
    assert [a.data.profile.code for a in analyses] == ["018123", "025500"]
    for item in analyses:
        assert item.data.navs[-1].nav_date == date(2026, 11, 6)
        assert item.data.holdings.report_period_end == date(2026, 9, 30)
        assert item.data.holdings.source_url.startswith("https://fundf10.eastmoney.com/")
        assert item.data.profile.currency is None
    assert not news["018123"]["items"] and len(news["025500"]["items"]) == 3
    for code, length, index in (("018123", 59, 1), ("025500", 62, 4)):
        diagnostic = projection["observed_diagnostics"][code]
        assert diagnostic["code"] == "unsafe_claim" and "claim_category" not in diagnostic
        assert diagnostic["section"] == "conclusion" and diagnostic["fund_index"] == index
        assert diagnostic["counts"]["text_chars"] == length
        assert diagnostic["counts"]["citation_count"] == 3
        assert diagnostic["counts"]["placeholder_count"] == 0


@pytest.mark.parametrize("section", ["conclusion", "risks", "observations"])
@pytest.mark.parametrize("spaced", [False, True])
@pytest.mark.parametrize("text,ref,code", [
    (RETURN_RISK, "return_1d", "018123"),
    ("历史净值表现不保证回报，仍需审慎评估风险。", "return_1d", "025500"),
    ("历史净值表现不构成收益预测，仍需审慎评估风险。", "return_1d", "018123"),
    (RELATION_RISK, "holdings", "018123"),
    (RELATION_RISK, "related:0", "025500"),
    (NEWS_RISK, "news:0", "025500"),
])
def test_complete_supported_risk_statements_pass_real_parser(text, ref, code, section, spaced):
    if spaced:
        text = "　".join(text)
    result = interpret_claim(text, [f"F{code}:{ref}"], code=code, section=section)
    assert result.status == "ok", result.diagnostics
    assert getattr(result.funds[code], section)[0].text == text
    assert result.comparison_status.status == "ok"


@pytest.mark.parametrize("transform", [lambda s: s, lambda s: unicodedata.normalize("NFKC", s), lambda s: "　".join(s)])
def test_nfkc_and_visible_spacing_do_not_change_the_complete_contract(transform):
    result = interpret_claim(transform(RETURN_RISK))
    assert result.status == "ok", result.diagnostics


@pytest.mark.parametrize("text,category", [
    ("今天基金表现值得观察，应继续核验资料。", "relative_time"),
    ("本基金评分值得关注，应继续核验资料。", "rating"),
    ("建议买入本基金，后续仍需注意风险。", "trading_or_forecast"),
    ("本基金目标收益值得关注，应继续核验资料。", "trading_or_forecast"),
    ("本基金保证收益，后续仍需核验资料。", "guarantee"),
    ("本基金无需承担市场风险，应继续核验资料。", "guarantee"),
    ("新闻推动净值上涨，后续仍需核验资料。", "causal_attribution"),
    ("本基金优于其他基金，应继续核验资料。", "ranking_or_comparison"),
    ("本基金风险较高，应继续核验资料。", "ranking_or_comparison"),
    # Safe synonyms remain deliberately rejected outside the bounded templates.
    ("历史业绩不保证收益，应继续核验资料。", "guarantee"),
    ("关联新闻不构成收益归因，应继续核验资料。", "causal_attribution"),
    ("本基金并非无风险，应继续核验资料。", "guarantee"),
])
def test_fixed_claim_categories_preserve_scope_diagnostics(text, category):
    result = interpret_claim(text)
    assert result.status == "partial" and set(result.funds) == {"025500"}
    diagnostic = result.fund_statuses["018123"].diagnostics
    assert diagnostic["code"] == "unsafe_claim" and diagnostic["claim_category"] == category
    assert diagnostic["fund_code"] == "018123" and diagnostic["fund_index"] == 0
    assert diagnostic["section"] == "conclusion" and diagnostic["item_index"] == 0
    assert result.comparison_status.diagnostics["code"] == "citation_invalid_fund"
    assert text not in json.dumps(asdict(result), ensure_ascii=False)


@pytest.mark.parametrize("text", [
    "不能保证收益，但保证回报，应继续核验资料。",
    "不保证收益就是保证回报，应继续核验资料。",
    "不构成归因，因此新闻推动净值上涨，应继续核验资料。",
    "不能不保证收益，应继续核验资料。",
    "以下说法是错误的：" + RETURN_RISK,
    "不能说：" + RETURN_RISK,
    "并不是：" + RETURN_RISK,
    RETURN_RISK + "但是保证回报。",
    RETURN_RISK + "建议买入本基金。",
    RETURN_RISK + "新闻推动净值上涨。",
    RETURN_RISK + "表现优于其他基金。",
    RETURN_RISK + "当前持仓集中于科技公司。",
    RETURN_RISK + "历史持仓集中于科技公司。",
    RETURN_RISK.replace("，", "，并且"),
    RETURN_RISK.replace("，", "；"),
    RETURN_RISK.replace("不保证收益", "不保，证收益") + "建议买入本基金。",
    RETURN_RISK + "{{F018123:return_1d}}",
    "{{F018123:return_1d}}" + RETURN_RISK,
    RETURN_RISK.replace("不保证收益", "不保证{{F018123:return_1d}}收益"),
    RETURN_RISK.replace("不保证收益", "不保{{F018123:return_1d}}证收益"),
    RETURN_RISK.replace("不保证收益", "不{{F018123:return_1d}}保证收益"),
    RETURN_RISK.replace("不保证收益", "不保证收{{F018123:return_1d}}益"),
    RETURN_RISK.replace("，", "{{F018123:return_1d}}，"),
    RETURN_RISK.replace("不保证收益", "不保\u200b证收益"),
    RETURN_RISK.replace("不保证收益", "不保证收\n益"),
    RETURN_RISK + "预计收益达到２０％。",
    RETURN_RISK + "预计收益达到百分之二十。",
])
@pytest.mark.parametrize("reverse", [False, True])
def test_templates_cannot_exempt_mixed_wrapped_split_or_hidden_claims(text, reverse):
    result = interpret_claim(text, reverse=reverse)
    assert result.status == "partial" and set(result.funds) == {"025500"}
    diagnostic = result.fund_statuses["018123"].diagnostics
    assert diagnostic["fund_index"] == int(reverse)
    assert diagnostic["code"] in {"unsafe_claim", "control_character", "numeric_or_symbolic", "chinese_numeral"}
    assert not result.comparison


@pytest.mark.parametrize("text,refs,expected", [
    (RETURN_RISK, ["F018123:profile"], "unsafe_claim"),
    (RETURN_RISK, ["F018123:nav"], "unsafe_claim"),
    (RETURN_RISK, ["F018123:holdings"], "unsafe_claim"),
    (RELATION_RISK, ["F018123:return_1d"], "unsafe_claim"),
    (NEWS_RISK, ["F018123:holdings"], "unsafe_claim"),
    (RETURN_RISK, ["F025500:return_1d"], "citation_wrong_fund"),
    (RETURN_RISK, ["SECRET_UNKNOWN_EVIDENCE"], "citation_unknown"),
    (RETURN_RISK, ["F018123:return_1d", "F018123:return_1d"], "citation_duplicate"),
])
def test_template_requires_correct_existing_same_fund_evidence(text, refs, expected):
    result = interpret_claim(text, refs)
    assert result.fund_statuses["018123"].diagnostics["code"] == expected
    assert "SECRET_UNKNOWN_EVIDENCE" not in json.dumps(asdict(result))


def test_unavailable_metric_and_untrusted_fact_text_cannot_grant_exception():
    analyses, news = run8_inputs()
    item = analyses[0]
    item.metrics["return_1d"] = replace(item.metrics["return_1d"], value=None, reason=RETURN_RISK)
    output = valid_output(("018123", "025500"))
    output["funds"][0]["conclusion"] = [{"text": RETURN_RISK, "evidence_ids": ["F018123:return_1d"]}]
    result = module.interpret_funds(analyses, news, config=configuration(), analyzer_factory=fake_factory(output))
    assert result.fund_statuses["018123"].diagnostics["code"] == "unsafe_claim"
    assert RETURN_RISK not in result.facts["F018123:return_1d"].allowed_limitations
    # Neither arbitrary allowlist entries nor a wrong-kind copy grants a template.
    facts, _ = module._build_context(analyses, news)
    facts["F018123:nav"] = replace(facts["F018123:nav"], allowed_limitations=[RETURN_RISK])
    with pytest.raises(module._ValidationError):
        module._guard_chinese_prose(RETURN_RISK, ["F018123:nav"], facts)


def test_evidence_section_still_requires_placeholder_and_never_uses_template_exemption():
    result = interpret_claim(RETURN_RISK, section="evidence")
    assert result.fund_statuses["018123"].diagnostics["code"] == "evidence_placeholder_missing"
    result = interpret_claim(RETURN_RISK + "{{F018123:return_1d}}", section="evidence")
    assert result.fund_statuses["018123"].diagnostics["code"] == "unsafe_claim"


def test_comparison_template_needs_all_cited_scopes_and_retains_alignment_boundary():
    analyses, news = run8_inputs()
    output = valid_output(("018123", "025500"))
    output["comparison"] = [{"text": RETURN_RISK, "evidence_ids": ["F018123:return_1d", "F025500:profile"]}]
    result = module.interpret_funds(analyses, news, config=configuration(), analyzer_factory=fake_factory(output))
    assert len(result.funds) == 2 and result.comparison_status.diagnostics["code"] == "unsafe_claim"
    output["comparison"][0]["evidence_ids"] = ["F018123:return_1d", "F025500:return_1d"]
    result = module.interpret_funds(analyses, news, config=configuration(), analyzer_factory=fake_factory(output))
    assert result.comparison_status.diagnostics["code"] == "metric_unaligned"
    output["comparison"] = [{"text": RELATION_RISK, "evidence_ids": ["F018123:holdings", "F025500:holdings"]}]
    result = module.interpret_funds(analyses, news, config=configuration(), analyzer_factory=fake_factory(output))
    assert result.status == "ok" and len(result.comparison) == 1


@pytest.mark.parametrize("category", ["SECRET_MATCHED_TEXT", "", [], {}, True, None])
def test_category_diagnostic_allowlist_rejects_unknown_or_nonstring_values(category):
    original = {"stage": "narrative", "code": "unsafe_claim", "claim_category": category,
                "match": "SECRET_MATCHED_TEXT", "text": "SECRET_MODEL_TEXT", "evidence_id": "SECRET_UNKNOWN_ID"}
    assert module.safe_interpretation_diagnostics(original) == {"stage": "narrative", "code": "unsafe_claim"}
    assert "claim_category" not in module.safe_interpretation_diagnostics({
        "stage": "citation", "code": "citation_unknown", "claim_category": "guarantee",
    })


def test_real_service_keeps_accepted_fund_and_scrubs_categories_in_all_outputs(tmp_path, caplog):
    caplog.set_level("INFO", logger="src.services.fund_analysis_service")
    analyses, _ = run8_inputs()
    data = {a.data.profile.code: a.data for a in analyses}
    output = valid_output(tuple(data))
    output["funds"][0]["conclusion"] = [{"text": RETURN_RISK, "evidence_ids": ["F018123:return_1d"]}]
    unsafe = "本基金保证收益，私密模型原句不可转存。"
    output["funds"][1]["conclusion"] = [{"text": unsafe, "evidence_ids": ["F025500:return_1d"]}]
    real_interpreter = partial(module.interpret_funds, config=configuration(), analyzer_factory=fake_factory(output))

    def interpreter(*args, **kwargs):
        result = real_interpreter(*args, **kwargs)
        for diagnostic in (result.diagnostics, result.fund_statuses["025500"].diagnostics,
                           result.comparison_status.diagnostics):
            diagnostic.update(raw_response="SECRET_MODEL_RAW", matched_text="SECRET_MATCH",
                              unknown_evidence="SECRET_UNKNOWN_ID", exception="SECRET_PROVIDER_KEY")
        result.comparison_status.diagnostics["claim_category"] = "SECRET_BAD_CATEGORY"
        return result

    report = run_fund_analysis(tuple(data), fetcher=Mock(fetch=Mock(side_effect=lambda code: data[code])),
                               include_news=False, include_interpretation=True, interpreter=interpreter,
                               output_dir=tmp_path)
    saved_text = Path(report.snapshot_path).read_text()
    saved = json.loads(saved_text)
    assert saved["schema_version"] == 4
    assert saved["interpretation"]["status"] == "partial"
    assert set(saved["interpretation"]["funds"]) == {"018123"}
    assert saved["interpretation"]["fund_statuses"]["025500"]["diagnostics"]["claim_category"] == "guarantee"
    assert "claim_category=guarantee" in report.markdown.replace("\\", "")
    assert '"claim_category": "guarantee"' in caplog.text
    assert RETURN_RISK in report.markdown
    assert report.notification_status == "disabled"
    for secret in (unsafe, "SECRET_MODEL_RAW", "SECRET_MATCH", "SECRET_UNKNOWN_ID", "SECRET_PROVIDER_KEY", "SECRET_BAD_CATEGORY"):
        assert secret not in saved_text + report.markdown + caplog.text


def test_template_support_does_not_skip_news_qualification_or_global_identity():
    # A metric disclaimer has no news qualification, so an extra news citation
    # must still reject it even when the metric itself supports the template.
    result = interpret_claim(RETURN_RISK, ["F025500:return_1d", "F025500:news:0"], code="025500")
    assert result.fund_statuses["025500"].diagnostics["code"] == "news_not_qualified"
    analyses, news = run8_inputs()
    output = valid_output(("018123", "025500"))
    output["funds"][0]["conclusion"] = [{"text": RETURN_RISK, "evidence_ids": ["F018123:return_1d"]}]
    output["funds"][1]["fund_code"] = "018123"
    result = module.interpret_funds(analyses, news, config=configuration(), analyzer_factory=fake_factory(output))
    assert result.status == "model_failed" and not result.funds
    assert result.diagnostics["code"] == "fund_identity"


def test_failed_fund_does_not_support_a_whole_template_comparison():
    analyses, news = run8_inputs()
    output = valid_output(("018123", "025500"))
    output["funds"][0]["conclusion"][0]["text"] = "本基金保证收益，后续仍需核验资料。"
    output["comparison"] = [{"text": RELATION_RISK, "evidence_ids": ["F018123:holdings", "F025500:holdings"]}]
    result = module.interpret_funds(analyses, news, config=configuration(), analyzer_factory=fake_factory(output))
    assert result.status == "partial" and set(result.funds) == {"025500"}
    assert result.comparison_status.diagnostics["code"] == "citation_invalid_fund"


def test_first_match_category_is_text_order_not_category_priority():
    result = interpret_claim("本基金优于其他基金且保证收益，应继续核验资料。")
    assert result.diagnostics["claim_category"] == "ranking_or_comparison"
    result = interpret_claim("本基金保证收益且优于其他基金，应继续核验资料。")
    assert result.diagnostics["claim_category"] == "guarantee"


@pytest.mark.parametrize("mark", ["\u0301", "\u034f", "\ufe0e", "\ufe0f", "\U000e0100", "\u2028", "\u2029"])
@pytest.mark.parametrize("text", [RETURN_RISK, "本基金保证收益，后续仍需核验资料。"])
def test_unicode_marks_and_line_separators_cannot_split_templates_or_assertions(mark, text):
    result = interpret_claim(text.replace("保证", "保" + mark + "证"))
    diagnostic = result.fund_statuses["018123"].diagnostics
    assert result.status == "partial" and set(result.funds) == {"025500"}
    assert diagnostic["code"] == "control_character"
    assert "claim_category" not in diagnostic


@pytest.mark.parametrize("mark", ["\u034f", "\u2028", "\u2029"])
def test_unicode_format_rejection_reaches_service_without_echoing_failed_text(tmp_path, caplog, mark):
    caplog.set_level("INFO", logger="src.services.fund_analysis_service")
    analyses, _ = run8_inputs()
    data = analyses[0].data
    output = valid_output(("018123",))
    unsafe = "本基金保" + mark + "证收益，隐藏格式风险不得绕过。"
    output["funds"][0]["conclusion"] = [{"text": unsafe, "evidence_ids": ["F018123:return_1d"]}]
    interpreter = partial(module.interpret_funds, config=configuration(), analyzer_factory=fake_factory(output))
    report = run_fund_analysis("018123", fetcher=Mock(fetch=Mock(return_value=data)), include_news=False,
                               include_interpretation=True, interpreter=interpreter, output_dir=tmp_path)
    snapshot = Path(report.snapshot_path).read_text()
    saved = json.loads(snapshot)
    assert saved["interpretation"]["diagnostics"]["code"] == "control_character"
    assert saved["interpretation"]["status"] == "model_failed"
    assert unsafe not in snapshot + report.markdown + caplog.text
    assert "隐藏格式风险" not in snapshot + report.markdown + caplog.text


def test_nfkc_created_forbidden_categories_cannot_split_claims_or_templates():
    # Exhaustively derive the compatibility forms from the runtime's Unicode
    # database. This tests the category contract, not a growing character denylist.
    unsafe_categories = lambda ch: unicodedata.category(ch).startswith(("C", "M")) or unicodedata.category(ch) in {"Zl", "Zp"}
    marks = [chr(cp) for cp in range(0x110000)
             if not unsafe_categories(chr(cp))
             and any(unsafe_categories(ch) for ch in unicodedata.normalize("NFKC", chr(cp)))]
    assert "\u00a8" in marks and "\u309b" in marks
    for mark in marks:
        for text in (RETURN_RISK, "本基金保证收益，后续仍需核验资料。"):
            result = interpret_claim(text.replace("保证", "保" + mark + "证"))
            assert result.fund_statuses["018123"].diagnostics["code"] == "control_character", f"U+{ord(mark):04X}"


def test_visible_spacing_cannot_bypass_downstream_return_direction():
    analyses, _ = run8_inputs()
    value = analyses[0].metrics["return_1d"].value
    direction = "上涨" if value <= 0 else "下跌"
    text = "　".join("净值" + direction + "，后续仍需核验资料。")
    result = interpret_claim(text)
    assert result.fund_statuses["018123"].diagnostics["code"] == "return_direction"
