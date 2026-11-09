"""Offline title-intent regressions for live and saved fund-news evidence."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json

import pytest

from src.core.fund_analysis import FundRelatedAsset, analyze_fund
from src.services.fund_interpretation import _build_context
from src.services.fund_news_service import gather_fund_news, select_news_digest
from tests.test_fund_news_service import TODAY, holding_data, item, sample, search


# Exact retained Run12 title/URL. Other titles below are synthetic examples,
# not claims that an IPO, code change, or company event actually occurred.
RUN12_TITLE = "兆易创新是哪家上市公司？股票代码、主营业务一文看懂"
RUN12_URL = "https://kuku.baidu.com/landing/tscp_doc/dcc55b9450c2b533879dd61fada6234b"


@pytest.mark.parametrize("title", [
    RUN12_TITLE,
    "兆易创新是哪家上市公司",
    "兆易创新是上市公司吗？主营业务介绍",
    "兆易创新是不是上市公司？",
    "兆易创新是做什么的？股票上市背景",
    "兆易创新公司简介：股票代码、上市时间和主营业务",
    "兆易创新股票代码是多少？上市公司业务范围详解",
    "兆易创新股票代码603986：上市公司主营业务",
    "兆易创新上市了吗？公司介绍",
    "兆易创新什么时候上市？公司主营产品有哪些",
    "兆易创新发布公司简介：上市公司主营业务一文看懂",
    "兆易创新回购是什么？上市公司回购科普",
    "兆易创新IPO获批了吗？一文读懂上市公司简介",
    "兆易创新上市公司回购指南：如何回购股份",
    "兆易创新上市公司简介：回购股份科普",
    "兆易创新 company profile：上市公司主营业务",
    "兆易创新公司简介：什么是处罚？",
    "兆易创新公司简介：什么是股票停牌？",
    "兆易创新公司：什么是处罚？",
    "兆易创新股票停牌是什么意思？",
    "兆易创新公司简介：如何签订订单？",
    "兆易创新公司简介包含订单业务",
    "兆易创新公司简介包含诉讼进展公告",
    "兆易创新公司简介介绍签订订单业务",
    "兆易创新是否受到处罚？公司简介",
    "兆易创新受到处罚了吗？公司简介",
    "兆易创新收到诉讼了吗？公司简介",
    "兆易创新股票停牌了吗？公司简介",
    "兆易创新如何签订重大订单？公司简介",
    "兆易创新上市公司处罚科普",
    "兆易创新上市公司诉讼指南",
    "兆易创新公司简介：诉讼指南公告",
])
def test_company_background_is_rejected_despite_fresh_date_and_event_snippet(title):
    bundle = gather_fund_news(holding_data("603986", "兆易创新"), search(item(
        title, RUN12_URL, day="2026-11-09",
        snippet="兆易创新发布财报、宣布股票回购并获批上市")), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["holding"].reason_counts == {"static_page": 1}
    assert bundle.coverage["holding"].fresh == 0
    data, legacy = legacy_bundle()
    legacy.items[0].title = title
    saved = json.loads(json.dumps(asdict(legacy), default=str, ensure_ascii=False))
    assert select_news_digest(saved)["counts"]["ineligible"] == 1
    facts, _ = _build_context([analyze_fund(data, as_of=TODAY)], {data.profile.code: saved})
    assert f"F{data.profile.code}:news:0" not in facts


@pytest.mark.parametrize("title", [
    "兆易创新今日上市，股票代码及主营业务一文看懂",
    "新股兆易创新11月9日上市，股票代码603986",
    "兆易创新正式上市，上市公司简介",
    "兆易创新将于11月10日上市，主营业务介绍",
    "兆易创新IPO获批，一文读懂公司主营业务",
    "兆易创新上市申请获准，公司介绍",
    "兆易创新主营业务变更，公司简介与调整内容一文看懂",
    "兆易创新股票代码变更为603987，公司简介",
    "兆易创新披露三季报：主营业务介绍与业绩解读",
    "兆易创新净利润增长20%，公司简介与业绩一文看懂",
    "兆易创新公布回购计划，上市公司回购科普",
    "兆易创新回购11.65亿元股份，公司介绍",
    "兆易创新首次回购股份，为什么回购？公司简介",
    "兆易创新今日上市：这家公司主营业务是什么？",
    "兆易创新是首家上市公司公布专项回购计划",
    "兆易创新因财务造假受到处罚，公司简介",
    "兆易创新收到重大诉讼公告，公司简介",
    "兆易创新股票停牌，公司简介",
    "兆易创新签订重大订单，公司简介",
    "兆易创新收到立案调查公告，公司简介",
    "兆易创新被起诉，诉讼公告与公司简介",
    "兆易创新股票复牌，公司简介",
    "兆易创新发布停牌公告，公司简介",
    "兆易创新中标重大项目，公司简介",
    "兆易创新获得重大订单，公司简介",
    "兆易创新因财务造假受到处罚，为什么？公司简介",
    "兆易创新公司公告：签订重大订单，主营业务介绍",
    "兆易创新公司公告：股票停牌，公司简介",
    "兆易创新重大诉讼进展公告，公司简介",
    "兆易创新关于行政处罚决定的公告，主营业务介绍",
])
def test_concrete_events_survive_educational_words_without_verification_upgrade(title):
    data = holding_data("603986", "兆易创新")
    bundle = gather_fund_news(data, search(item(
        title, RUN12_URL)), as_of=TODAY)
    assert len(bundle.items) == 1
    assert bundle.items[0].model_eligible is True
    selected = select_news_digest(bundle)["items"][0]
    assert selected["verification_status"] == "headline_only_unverified"
    assert selected["impact_status"] == "unknown"
    assert selected["reporting_context"] == "historical_holding"
    assert selected["relationship"]["source_date"] == date(2026, 9, 30)
    assert "不代表当前持仓" in selected["item"].relation_basis
    saved = json.loads(json.dumps(asdict(bundle), default=str, ensure_ascii=False))
    assert select_news_digest(saved)["items"][0]["verification_status"] == "headline_only_unverified"
    facts, _ = _build_context([analyze_fund(data, as_of=TODAY)], {data.profile.code: saved})
    prefix = f"F{data.profile.code}"
    event = facts[prefix + ":news:0"]
    assert event.relationship_evidence_ids == [prefix + ":holdings"]
    assert title in event.text and "待核验" in event.text


@pytest.mark.parametrize("title,accepted", [
    ("真实半导体ETF是什么基金？上市交易与产品介绍", False),
    ("真实半导体ETF简介：上市日期、交易代码一文看懂", False),
    ("真实半导体ETF今日上市，交易代码159999及产品介绍", True),
    ("真实半导体ETF暂停申购，基金简介与调整说明", True),
])
def test_target_etf_explainer_scope_is_distinct_from_listing_and_operation_events(title, accepted):
    data = sample()
    data.related_assets = [FundRelatedAsset(
        "target_etf", "真实半导体ETF", data.profile.source_url, code="159999",
        identity_url="https://fund.example.com/159999", verified=True)]
    bundle = gather_fund_news(data, search(item(title, RUN12_URL)), as_of=TODAY)
    assert bool(bundle.items) is accepted
    if accepted:
        assert select_news_digest(bundle)["items"][0]["verification_status"] == "headline_only_unverified"
    else:
        assert bundle.coverage["target_etf"].reason_counts == {"static_page": 1}


def test_direct_fund_explainer_is_not_an_announcement():
    bundle = gather_fund_news(sample(), search(item(
        "测试基金C是什么基金？基金公告、上市交易一文看懂", RUN12_URL)), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["announcement"].reason_counts == {"static_page": 1}
    assert bundle.coverage["direct_fund"].reason_counts == {"static_page": 1}


def legacy_bundle():
    """Simulate a saved, previously accepted Run12 explainer beside an event."""
    data = holding_data("603986", "兆易创新")
    bundle = gather_fund_news(data, search(
        item("兆易创新公布回购计划", RUN12_URL, day="2026-11-09"),
        item("兆易创新净利润增长20%", "https://news.example.com/earnings", day="2026-11-09"),
    ), as_of=TODAY)
    # Preserve the old fresh/related_event/model_eligible flags. Selection and
    # model-input construction must recheck page intent instead of trusting them.
    bundle.items[0].title = RUN12_TITLE
    return data, bundle


@pytest.mark.parametrize("saved_json", [False, True])
def test_legacy_background_is_excluded_from_digest_without_mutating_raw_evidence(saved_json):
    _, bundle = legacy_bundle()
    if saved_json:
        bundle = json.loads(json.dumps(asdict(bundle), default=str, ensure_ascii=False))
    original = deepcopy(bundle)
    digest = select_news_digest(bundle)
    assert bundle == original
    assert digest["counts"]["raw_items"] == 2
    assert digest["counts"]["ineligible"] == 1
    assert digest["counts"]["selected"] == 1
    assert digest["items"][0]["item_index"] == 1
    assert digest["items"][0]["source_indices"] == [1]
    assert digest["items"][0]["verification_status"] == "headline_only_unverified"


def test_saved_background_never_reaches_model_facts_or_citation_ids():
    data, bundle = legacy_bundle()
    saved = json.loads(json.dumps(asdict(bundle), default=str, ensure_ascii=False))
    original = deepcopy(saved)
    facts, _ = _build_context([analyze_fund(data, as_of=TODAY)], {data.profile.code: saved})
    prefix = f"F{data.profile.code}"
    assert prefix + ":news:0" not in facts
    assert RUN12_TITLE not in json.dumps({key: asdict(value) for key, value in facts.items()}, ensure_ascii=False)
    event = facts[prefix + ":news:1"]
    assert event.relationship_evidence_ids == [prefix + ":holdings"]
    assert "待核验" in event.text and "不构成净值涨跌归因" in event.text
    assert saved == original


def test_event_with_educational_words_still_requires_a_publication_date():
    bundle = gather_fund_news(holding_data("603986", "兆易创新"), search(item(
        "兆易创新IPO获批，一文读懂公司主营业务", RUN12_URL, day=None)), as_of=TODAY)
    assert bundle.items[0].date_status == "unknown"
    assert bundle.items[0].model_eligible is False
    assert not select_news_digest(bundle)["items"]
