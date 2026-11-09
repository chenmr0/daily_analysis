"""Offline regressions for reconstructed fund evidence and bounded selection."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.core.fund_analysis import FundData, FundProfile, FundRelatedAsset, Holding, HoldingsSnapshot, NavPoint
from src.services.fund_news_service import (
    EVIDENCE_SCOPES, FILTER_REASONS, REASON_LABELS, FundNewsBundle, FundNewsItem,
    _headline_matches_fund, gather_fund_news, select_news_digest,
)

TODAY = date(2026, 11, 9)


def sample(name="测试基金C"):
    return FundData(FundProfile("018123", name, source_url="https://fund.example.com/018123"),
                    [NavPoint(TODAY, 1, 1)], datetime(2026, 11, 9, tzinfo=timezone.utc),
                    "https://fund.example.com/nav")


def item(title="测试基金C公告", url="https://news.example.com/a", day="2026-11-08", **kwargs):
    return SimpleNamespace(title=title, url=url, published_date=day, source="测试来源", **kwargs)


def search(*results):
    service = Mock(is_available=True)
    service.search_topic_news_bounded.return_value = SimpleNamespace(success=True, results=list(results))
    return service


def holding_data(code, name):
    data = sample()
    data.holdings = HoldingsSnapshot(date(2026, 9, 30), [Holding(code, name, 4)],
                                     "https://fund.example.com/holdings")
    return data


def test_nba_snippet_cannot_establish_identity():
    data = sample("东方阿尔法科技智选混合发起C")
    service = search(item("fmvp得分仅排第十？盘点25-26赛季NBA得分前十榜单", snippet=data.profile.name))
    bundle = gather_fund_news(data, service, as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["direct_fund"].status == "none_filtered"
    assert service.search_topic_news_bounded.call_count == 2
    service.search_stock_news.assert_not_called()


@pytest.mark.parametrize("headline,accepted", [
    ("测试基金C公告", True), ("测试基金A公告", False), ("测试基金CA公告", False),
    ("测试基金C1公告", False), ("测试基金Ｃ公告", True),
])
def test_share_class_is_part_of_headline_identity(headline, accepted):
    assert _headline_matches_fund(headline, "测试基金C") is accepted


def test_full_width_and_html_escaped_fund_identity():
    name = "南方亚太精选ETF联接(QDII)C"
    for title in ["南方亚太精选ＥＴＦ联接（QDII）Ｃ基金公告", "南方亚太精选ETF联接&#40;QDII&#41;&nbsp;C 基金公告"]:
        assert _headline_matches_fund(title, name)


def test_target_requires_verified_source_and_identity_not_guessed_name():
    data = sample("测试市场ETF联接C")
    data.related_assets = [FundRelatedAsset("target_etf", "真实半导体ETF", "https://fund.example.com/018123",
                                           code="159999", identity_url="https://fund.example.com/159999", verified=False)]
    service = search(item("真实半导体ETF公布公告"))
    assert not gather_fund_news(data, service, as_of=TODAY).items
    data.related_assets[0].verified = True
    bundle = gather_fund_news(data, service, as_of=TODAY)
    assert bundle.items[0].category == "target_etf"
    assert "未核验当前配置比例" in bundle.items[0].relation_basis
    assert bundle.items[0].source_date is None
    assert bundle.items[0].source_url == data.related_assets[0].source_url
    data.related_assets[0].identity_url = ""
    assert not gather_fund_news(data, service, as_of=TODAY).items


def test_holding_article_has_period_not_current_holding():
    data = holding_data("000001", "可靠公司")
    data.holdings.report_period_end = date(2025, 12, 31)
    bundle = gather_fund_news(data, search(item("可靠公司发布新产品")), as_of=TODAY)
    evidence = bundle.items[0]
    assert evidence.category == "holding"
    assert evidence.source_date == date(2025, 12, 31)
    assert "真实披露日期 未知" in evidence.relation_basis
    assert "超过180天" in evidence.relation_basis
    assert "不代表当前持仓" in evidence.relation_basis
    assert evidence.date_status == "fresh"


def test_future_holding_and_unverified_industry_cannot_enable_queries():
    data = holding_data("000001", "可靠公司")
    data.holdings.report_period_end = date(2026, 12, 31)
    data.related_assets = [FundRelatedAsset("industry", "半导体", "https://fund.example.com/industry", verified=False)]
    service = search(item("可靠公司与半导体产业合作"))
    bundle = gather_fund_news(data, service, as_of=TODAY)
    assert not bundle.items and not bundle.related_assets
    assert service.search_topic_news_bounded.call_count == 2


def test_industry_period_preserved_and_theme_is_not_allocation():
    data = sample("测试数字经济混合C")
    data.related_assets = [FundRelatedAsset("industry", "制造业", "https://fund.example.com/industry",
                                           source_date=date(2026, 9, 30), weight_pct=20, verified=True)]
    bundle = gather_fund_news(data, search(item("制造业发展数据发布")), as_of=TODAY)
    assert bundle.items[0].category == "industry"
    assert "2026-09-30" in bundle.items[0].relation_basis
    data.related_assets = []
    bundle = gather_fund_news(data, search(item("数字经济产业新进展")), as_of=TODAY)
    assert bundle.items[0].category == "theme"
    assert "不是已核验行业配置" in bundle.items[0].relation_basis
    assert bundle.related_assets[0].verified is False


@pytest.mark.parametrize("day,status", [(None, "unknown"), ("昨天", "unknown"), ("11-08", "unknown"),
                                       ("2026-99-99", "unknown"), ("2026-11-10", "future"),
                                       ("2025-01-01", "stale"), ("2026年11月08日", "fresh"),
                                       ("2026-11-08T12:00:00Z", "fresh")])
def test_publication_status_is_not_retrieval_date(day, status):
    bundle = gather_fund_news(sample(), search(item(day=day)), as_of=TODAY)
    assert bundle.items[0].date_status == status
    assert bundle.coverage["announcement"].fresh == int(status == "fresh")
    assert (bundle.items[0].published_date is None) == (status == "unknown")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<script>", "https://user:pass@example.com/a",
                                 "https://127.0.0.1/a", "https://localhost/a", "https://example.com\\@evil.com/a",
                                 "https://example.com/a\n#x", "https://example.com:invalid/a"])
def test_malicious_urls_rejected(url):
    assert not gather_fund_news(sample(), search(item(url=url)), as_of=TODAY).items


def test_markup_removed_duplicate_url_only_once():
    bundle = gather_fund_news(sample(), search(
        item("<b>测试基金C</b>公告<script>bad</script>", url="https://news.example.com/a#x"),
        item(url="https://news.example.com/a#y")), as_of=TODAY)
    assert len(bundle.items) == 1
    assert "<" not in bundle.items[0].title
    assert bundle.items[0].url == "https://news.example.com/a"


def test_configuration_timeout_failure_and_empty_are_distinct():
    assert gather_fund_news(sample(), None, as_of=TODAY).coverage["direct_fund"].status == "not_configured"
    service = search()
    assert gather_fund_news(sample(), service, as_of=TODAY).coverage["direct_fund"].status == "no_results"
    service.search_topic_news_bounded.side_effect = TimeoutError("secret")
    bundle = gather_fund_news(sample(), service, as_of=TODAY)
    assert bundle.coverage["direct_fund"].status == "timeout" and "secret" not in str(bundle)
    service.search_topic_news_bounded.side_effect = RuntimeError("secret")
    assert gather_fund_news(sample(), service, as_of=TODAY).coverage["direct_fund"].status == "unavailable"
    service.search_topic_news_bounded.side_effect = None
    service.search_topic_news_bounded.return_value.success = False
    assert gather_fund_news(sample(), service, as_of=TODAY).coverage["direct_fund"].status == "unavailable"


def test_limits_queries_results_items_and_total_budget():
    data = sample("测试数字经济ETF联接C")
    data.holdings = HoldingsSnapshot(date(2026, 9, 30), [Holding(str(i), f"公司{i}", 5) for i in range(10)],
                                     "https://fund.example.com/holdings")
    data.related_assets = [FundRelatedAsset("target_etf", "真实ETF", "https://fund.example.com/own",
                                           code="159999", identity_url="https://fund.example.com/etf", verified=True),
                           FundRelatedAsset("industry", "制造业", "https://fund.example.com/industry",
                                           source_date=date(2026, 9, 30), weight_pct=40, verified=True)]
    service = search()
    gather_fund_news(data, service, as_of=TODAY)
    assert service.search_topic_news_bounded.call_count == 6
    for call in service.search_topic_news_bounded.call_args_list:
        assert call.kwargs["max_results"] == 5
        assert 0 < call.kwargs["timeout_seconds"] <= 12
    with patch("src.services.fund_news_service.time.monotonic", side_effect=[0, 1, 40, 40, 40, 40, 40, 40]):
        service.reset_mock()
        bundle = gather_fund_news(data, service, as_of=TODAY)
    assert service.search_topic_news_bounded.call_count == 1
    assert bundle.coverage["direct_fund"].status == "skipped_budget"


def test_cached_original_retrieval_preserved():
    stamp = "2026-11-01T10:00:00+00:00"
    bundle = gather_fund_news(sample(), search(item(retrieved_at=stamp)), as_of=TODAY)
    assert bundle.items[0].retrieved_at == datetime.fromisoformat(stamp)


def test_newsflood_does_not_crowd_out_related_evidence():
    service = search()
    service.search_topic_news_bounded.side_effect = [
        SimpleNamespace(success=True, results=[item(url=f"https://news.example.com/a{i}") for i in range(100)]),
        SimpleNamespace(success=True, results=[item("测试基金C开放申购", url=f"https://news.example.com/d{i}") for i in range(100)]),
        SimpleNamespace(success=True, results=[item("可靠公司公告", url=f"https://news.example.com/h{i}") for i in range(100)]),
    ]
    bundle = gather_fund_news(holding_data("000001", "可靠公司"), service, as_of=TODAY)
    assert [x.category for x in bundle.items] == ["announcement", "direct_fund", "holding", "holding"]
    assert len(bundle.items) <= 10


@pytest.mark.parametrize("code,name,title,accepted", [
    ("AAPL", "苹果", "苹果进入秋收季，果农迎来丰收", False),
    ("AAPL", "苹果", "苹果公司发布季度财报", False),
    ("AAPL", "苹果", "苹果(AAPL)季度营收增长", True),
    ("AAPL", "苹果", "苹果(AAPL)进入秋收季，果农迎来丰收", False),
    ("AAPL", "苹果", "苹果(XAAPL)季度营收增长", False),
    ("AAPL", "苹果", "苹果(AAPL.B)季度营收增长", False),
    ("AAPL", "苹果", "苹果基金(AAPL)股票投资公告", False),
    ("AAPL", "苹果", "AAPL股价上涨，其他公司发布财报", False),
    ("AAPL", "Apple", "Apple harvest creates a bumper crop", False),
    ("AAPL", "Apple", "Apple AAPL announces earnings", True),
    ("01810", "小米", "小米熬粥营养足，秋季丰收迎好年", False),
    ("01810", "小米", "小米公司公告营收增长", False),
    ("01810", "小米", "小米(01810)发布全年业绩报告", True),
    ("01810.HK", "小米", "小米(01810.US)发布全年业绩报告", False),
    ("01810.HK", "小米", "小米(01810.HK)发布全年业绩报告", True),
    ("000063", "中兴", "中兴名臣生平：古代历史故事", False),
    ("000063", "中兴", "中兴公司获得新订单", False),
    ("000063", "中兴", "中兴(000063)公布公司回购进展", True),
    ("000063", "中兴", "中兴(000063.SH)公布公司回购进展", False),
    ("000063", "中兴", "中兴股票上涨，基金代码000063推出新公告", False),
    ("000063", "中兴通讯", "中兴通讯发布季度财报", True),
    ("300285", "国瓷材料", "国瓷材料跌5.95%，基金重仓该股", True),
    ("300285", "国瓷材料", "国瓷材料名称由来及文字演变故事", False),
    ("AAPL", "苹果股份有限公司", "苹果股份有限公司推出新产品", True),
])
def test_holding_identity_name_context_and_disclosed_code(code, name, title, accepted):
    service = search(item(title, snippet=f"{name} {code} 股票财报"))
    bundle = gather_fund_news(holding_data(code, name), service, as_of=TODAY)
    matching = [e for e in bundle.items if e.category == "holding"]
    assert bool(matching) is accepted
    assert f'"{code}"' in service.search_topic_news_bounded.call_args.kwargs["focus_keywords"]
    assert "严格筛选可能漏报" in bundle.coverage["holding"].detail
    if matching:
        assert f"披露代码：{code}" in matching[0].relation_basis


def test_missing_ambiguous_code_does_not_invent_alias():
    bundle = gather_fund_news(holding_data("", "苹果"),
                              search(item("Apple Inc.与苹果公司发布季度财报")), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["holding"].status == "none_filtered"


@pytest.mark.parametrize("title,accepted", [("科技小说评选结果揭晓", False),
                                          ("科技有限公司发布财报", False),
                                          ("科技行业政策发布，产业链迎新变化", True)])
def test_generic_sector_not_company_identity(title, accepted):
    data = sample()
    data.related_assets = [FundRelatedAsset("industry", "科技", "https://fund.example.com/industry",
                                           source_date=date(2026, 9, 30), verified=True)]
    bundle = gather_fund_news(data, search(item(title)), as_of=TODAY)
    assert bool(bundle.items) is accepted
    if bundle.items:
        assert bundle.items[0].category == "industry"
        assert "不证明任何公司的身份" in bundle.items[0].relation_basis
    assert "不核验公司身份" in bundle.coverage["industry"].detail


def test_fresh_third_result_cannot_be_crowded_out_by_unknown_and_future():
    service = search(
        item("苹果(AAPL)财报预告", url="https://news.example.com/future", day="2026-11-10"),
        item("苹果(AAPL)公布回购计划", url="https://news.example.com/unknown", day=None),
        item("苹果(AAPL)季度营收发布", url="https://news.example.com/fresh", day="2026-11-08"))
    bundle = gather_fund_news(holding_data("AAPL", "苹果"), service, as_of=TODAY)
    assert [(e.url, e.date_status) for e in bundle.items] == [
        ("https://news.example.com/fresh", "fresh"), ("https://news.example.com/unknown", "unknown")]
    assert bundle.coverage["holding"].fresh == 1
    assert service.search_topic_news_bounded.call_count == 3
    assert "仅列作线索" in " ".join(bundle.warnings)


def test_invalid_relationship_dates_never_support_current_claims():
    data = sample()
    for relation in [FundRelatedAsset("industry", "制造业", "https://fund.example.com/sector", verified=True),
                     FundRelatedAsset("industry", "制造业", "https://fund.example.com/sector",
                                      source_date=date(2027, 1, 1), verified=True),
                     FundRelatedAsset("industry", "制造业", "https://fund.example.com/sector",
                                      source_date=date(2026, 9, 30), published_at=date(2027, 1, 1), verified=True)]:
        data.related_assets = [relation]
        assert not gather_fund_news(data, search(item("制造业行业数据更新")), as_of=TODAY).items


@pytest.mark.parametrize("bounds", [{"max_queries": 7}, {"max_items": 11}, {"timeout_seconds": 37}])
def test_public_call_bounds_cannot_expand_request_or_item_budget(bounds):
    service = search()
    with pytest.raises(ValueError):
        gather_fund_news(sample(), service, as_of=TODAY, **bounds)
    service.search_topic_news_bounded.assert_not_called()


@pytest.mark.parametrize("title,url", [
    ("壁仞科技(06082)股票股价_股价行情_讨论_资讯_财报_数据报告", "https://xueqiu.com/S/06082"),
    ("壁仞科技(06082)股票股价_股价行情_讨论_资讯_财报_数据报告", "https://news.example.com/profile"),
    ("壁仞科技股价上涨", "https://xueqiu.com/S/06082"),
    ("壁仞科技财报数据", "https://quote.eastmoney.com/hk/06082.html"),
    ("壁仞科技股票资料", "https://finance.sina.com.cn/realstock/company/hk06082/nc.shtml"),
])
def test_quote_pages_are_not_events_even_with_fresh_search_dates(title, url):
    # First fixture is a preserved run #4 result; its date did not prove an event.
    bundle = gather_fund_news(holding_data("06082", "壁仞科技"), search(item(title, url)), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["holding"].reason_counts == {"static_page": 1}


@pytest.mark.parametrize("title,accepted", [
    ("11月6日新材料ETF天弘基金份额减少60万份，重仓股北方华创、宁德时代、三环集团", False),
    ("新材料ETF公布股票投资组合，北方华创为第一大重仓股", False),
    ("新材料ETF增持北方华创，最新持仓公布", False),
    ("北方华创股票为新材料ETF重仓股，基金份额增长", False),
    ("新材料ETF持仓名单：北方华创股票资料报告", False),
    ("北方华创股价上涨5%，新材料ETF重仓该股", True),
    ("新材料ETF重仓股北方华创发布季度财报", True),
    ("北方华创公布回购计划，新材料ETF持仓受关注", True),
    ("北方华创盈利数据分析，新材料ETF重仓该股", True),
])
def test_other_product_portfolio_mentions_do_not_establish_issuer_events(title, accepted):
    # The first title was incorrectly accepted as a company event in run #4.
    bundle = gather_fund_news(holding_data("002371", "北方华创"), search(item(title)), as_of=TODAY)
    assert bool(bundle.items) is accepted
    if accepted:
        assert bundle.items[0].evidence_scope == "related_event"
        assert bundle.items[0].model_eligible is True
    else:
        assert bundle.coverage["holding"].reason_counts == {"third_party_product": 1}


def test_run4_broad_manufacturing_news_is_display_only_background():
    data = sample()
    data.related_assets = [FundRelatedAsset("industry", "制造业", "https://fund.example.com/industry",
                                           source_date=date(2026, 9, 30), verified=True)]
    bundle = gather_fund_news(data, search(item(
        "建设银行推出多项措施支持先进制造业-36氪", "https://www.36kr.com/newsflashes/4053282417513609")), as_of=TODAY)
    news = bundle.items[0]
    assert news.category == "industry" and news.date_status == "fresh"
    assert news.evidence_scope == "weak_background" and news.model_eligible is False
    assert news.source_date == date(2026, 9, 30)
    assert news.source_url == data.related_assets[0].source_url
    assert news.source == "测试来源" and news.published_date == date(2026, 11, 8)
    assert "不作为模型事件证据" in news.relation_basis
    assert bundle.coverage["industry"].reason_counts == {"weak_background": 1}


@pytest.mark.parametrize("category,name,title", [
    ("industry", "半导体", "半导体产业新政策发布"),
    ("theme", "数字经济", "数字经济产业新进展"),
])
def test_precise_sector_and_product_theme_remain_context_not_fund_events(category, name, title):
    data = sample(f"测试{name}混合C")
    if category == "industry":
        data.related_assets = [FundRelatedAsset(category, name, "https://fund.example.com/industry",
                                               source_date=date(2026, 9, 30), verified=True)]
    bundle = gather_fund_news(data, search(item(title)), as_of=TODAY)
    news = bundle.items[0]
    assert news.evidence_scope == "sector_context" and news.model_eligible is True
    assert "不是本基金具体事件" in news.relation_basis


@pytest.mark.parametrize("title,reason", [
    ("测试基金C基金档案与基本资料", "static_page"),
    ("测试基金C历史净值与净值走势", "static_page"),
    ("测试基金C资讯中心", "no_event_signal"),
])
def test_identity_matching_is_not_event_content(title, reason):
    bundle = gather_fund_news(sample(), search(item(title)), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["direct_fund"].reason_counts == {reason: 1}


@pytest.mark.parametrize("title", ["测试基金C公告：暂停申购", "测试基金C净值走势发生调整公告",
                                   "测试基金C招募说明书", "测试基金C基金合同"])
def test_announcement_leads_are_not_claimed_to_be_officially_verified(title):
    bundle = gather_fund_news(sample(), search(item(title)), as_of=TODAY)
    news = bundle.items[0]
    assert news.evidence_scope == "fund_event" and news.model_eligible is True
    assert "仅身份匹配" in news.relation_basis
    assert "未将搜索摘要当作公告全文核验" in news.relation_basis
    assert news.source_url == sample().profile.source_url


@pytest.mark.parametrize("day", [None, "2025-01-01", "2026-11-10"])
def test_event_scope_does_not_override_freshness_requirement(day):
    bundle = gather_fund_news(sample(), search(item(day=day)), as_of=TODAY)
    assert bundle.items[0].evidence_scope == "fund_event"
    assert bundle.items[0].model_eligible is False


def test_diagnostic_counts_have_one_finite_outcome_per_inspected_result():
    service = search(
        item(url="https://news.example.com/stale", day="2025-01-01"),
        item(url="https://news.example.com/fresh"),
        item(url="https://news.example.com/fresh#duplicate"),
        item(title="", snippet="untrusted diagnostic content"),
        item(title="其他基金A公告"),
        item(url="https://news.example.com/not-inspected"))
    bundle = gather_fund_news(sample(), service, as_of=TODAY, max_queries=1)
    assert [news.url for news in bundle.items] == ["https://news.example.com/fresh"]
    counts = bundle.coverage["announcement"].reason_counts
    assert counts == {"invalid_title": 1, "identity_mismatch": 1, "fund_event": 1,
                      "duplicate_url": 1, "selection_limit": 1}
    assert sum(counts.values()) == 5
    assert set(REASON_LABELS) == set(EVIDENCE_SCOPES) | set(FILTER_REASONS)
    assert "untrusted" not in str(counts)
    assert service.search_topic_news_bounded.call_count == 1


def test_failed_search_has_no_unbounded_diagnostic_reason():
    service = search()
    service.search_topic_news_bounded.side_effect = RuntimeError("provider payload and secret")
    bundle = gather_fund_news(sample(), service, as_of=TODAY)
    assert all(not coverage.reason_counts for coverage in bundle.coverage.values())
    assert "provider payload" not in str(bundle)


@pytest.mark.parametrize("title", [
    "测试基金C暂停大额申购", "测试基金C恢复大额赎回", "测试基金C限制定投金额",
    "测试基金C申购限额降低", "测试基金C基金经理离任", "测试基金C基金经理履新",
    "测试基金C增聘基金经理", "测试基金C基金经理发生变动", "测试基金C创历史新高",
])
def test_concrete_fund_operational_events_do_not_need_announcement_word(title):
    bundle = gather_fund_news(sample(), search(item(title)), as_of=TODAY)
    assert len(bundle.items) == 1
    assert bundle.items[0].category == "direct_fund"
    assert bundle.items[0].evidence_scope == "fund_event" and bundle.items[0].model_eligible


@pytest.mark.parametrize("title", [
    "北方华创股价创历史新高", "北方华创股价创下年内新低",
    "北方华创股价突破前期高点，基金持仓关注度提高",
    "北方华创业绩创历史新高，基金持仓比例增加",
])
def test_concrete_issuer_events_survive_context_filter(title):
    bundle = gather_fund_news(holding_data("002371", "北方华创"), search(item(title)), as_of=TODAY)
    assert bundle.items[0].evidence_scope == "related_event"
    assert bundle.items[0].model_eligible


def test_new_event_patterns_do_not_turn_static_quote_endpoint_into_article():
    bundle = gather_fund_news(holding_data("002371", "北方华创"), search(item(
        "北方华创股价创历史新高", "https://quote.eastmoney.com/sz002371.html")), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["holding"].reason_counts == {"static_page": 1}


@pytest.mark.parametrize("separator", ["而", "但是", "同时", "此外", "另外", "并且", "以及", "、", "；"])
def test_another_constituents_event_is_not_attributed_across_subject_boundary(separator):
    title = f"新材料ETF重仓北方华创{separator}宁德时代宣布回购"
    bundle = gather_fund_news(holding_data("002371", "北方华创"), search(item(title)), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["holding"].reason_counts == {"third_party_product": 1}


def test_issuer_event_before_subject_change_remains_eligible():
    title = "新材料ETF重仓北方华创业绩增长，而宁德时代宣布回购"
    bundle = gather_fund_news(holding_data("002371", "北方华创"), search(item(title)), as_of=TODAY)
    assert bundle.items[0].model_eligible


def digest_bundle(*titles, name="三环集团", code="300408"):
    """Deterministic headline fixtures, not generated model interpretation."""
    asset = FundRelatedAsset("holding", name, "https://fund.example.com/holdings",
                             source_date=date(2026, 9, 30), code=code, verified=True)
    return FundNewsBundle(items=[FundNewsItem(
        "holding", title, f"https://news.example.com/{i}", "测试来源", date(2026, 11, 7), "fresh",
        datetime(2026, 11, 9, tzinfo=timezone.utc),
        f"{name}为2026-09-30已披露对象；不代表当前持仓，未作涨跌归因。",
        source_date=asset.source_date, source_url=asset.source_url,
        evidence_scope="related_event", model_eligible=True,
    ) for i, title in enumerate(titles)], related_assets=[asset])


def assert_digest_counts(digest):
    counts = digest["counts"]
    assert counts["raw_items"] == sum(counts[key] for key in (
        "ineligible", "duplicate_urls", "duplicate_events", "entity_limit", "selection_limit", "selected"))
    assert counts["grouped_events"] == counts["entity_limit"] + counts["selection_limit"] + counts["selected"]


@pytest.mark.parametrize("saved_json", [False, True])
def test_run10_loan_headline_variants_group_without_losing_evidence(saved_json):
    # Exact titles preserved from Run10. The fixture dates are supplied search
    # dates, not independent verification of either publication or event.
    bundle = digest_bundle(
        "三环集团取得不超8.1亿元金融机构股票回购贷款承诺_公司新闻_财经_证券之星",
        "三环集团(300408.SZ)：取得金融机构股票回购贷款承诺书_公司新闻",
    )
    if saved_json:
        bundle = json.loads(json.dumps(asdict(bundle), default=str, ensure_ascii=False))
    original = deepcopy(bundle)
    digest = select_news_digest(bundle, max_items=2)
    assert bundle == original
    assert digest["counts"]["duplicate_events"] == 1
    assert digest["counts"]["selected"] == 1
    group = digest["items"][0]
    assert group["entity_name"] == "三环集团"
    assert group["entity_key"] == "holding:三环集团:300408"
    assert len(group["sources"]) == 2
    raw_items = bundle["items"] if saved_json else bundle.items
    assert group["item"] is raw_items[0]
    assert group["sources"] == raw_items
    assert_digest_counts(digest)


def test_run10_board_notice_is_concentration_omission_not_duplicate():
    # Exact saved titles. No model summary or causal claim is fabricated.
    bundle = digest_bundle(
        "君正股份：第六届董事会第十六次会议决议公告",
        "君正股份(300223):变更回购股份用途并注销- CFi.CN 中财网",
        name="君正股份", code="300223",
    )
    bundle.items[0].published_date = date(2026, 11, 8)
    other = digest_bundle("兆易创新回购 11.65 亿，股份将全部注销|兆易创新|财联社|回购_新浪新闻",
                          name="兆易创新", code="603986")
    bundle.items += other.items
    bundle.related_assets += other.related_assets
    digest = select_news_digest(bundle, max_items=2)
    assert digest["counts"]["grouped_events"] == 3
    assert digest["counts"]["duplicate_events"] == 0
    assert digest["counts"]["entity_limit"] == 1
    assert {g["item"].title for g in digest["items"]} == {bundle.items[1].title, bundle.items[2].title}
    assert_digest_counts(digest)


@pytest.mark.parametrize("first,second", [
    ("三环集团取得不超8.1亿元金融机构股票回购贷款承诺", "三环集团取得不超9.1亿元金融机构股票回购贷款承诺"),
    ("三环集团取得不超8.1亿元金融机构股票回购贷款承诺", "三环集团取得不少于8.1亿元金融机构股票回购贷款承诺"),
    ("三环集团取得金融机构股票回购贷款承诺", "三环集团取消金融机构股票回购贷款承诺"),
    ("三环集团取得金融机构股票回购贷款承诺", "三环集团未取得金融机构股票回购贷款承诺"),
    ("三环集团取得金融机构股票回购贷款承诺", "三环集团取得金融机构股票回购贷款承诺并变更用途"),
    ("三环集团11月6日取得金融机构股票回购贷款承诺", "三环集团11月7日取得金融机构股票回购贷款承诺"),
    ("三环集团公布回购计划", "三环集团完成回购计划"),
    ("三环集团上调回购价格", "三环集团下调回购价格"),
    ("三环集团公布增持计划", "三环集团公布减持计划"),
    ("三环集团公布回购计划", "三环集团公布回购计划|取消回购"),
])
def test_digest_does_not_merge_amount_date_opposition_or_material_updates(first, second):
    digest = select_news_digest(digest_bundle(first, second))
    assert digest["counts"]["grouped_events"] == 2
    assert digest["counts"]["duplicate_events"] == 0
    assert_digest_counts(digest)


@pytest.mark.parametrize("field,value", [
    ("published_date", date(2026, 11, 8)),
    ("source_date", date(2026, 6, 30)),
    ("source_url", "https://fund.example.com/other-period"),
    ("relation_basis", "另一关联依据；不代表当前持仓"),
    ("evidence_scope", "sector_context"),
    ("category", "industry"),
])
def test_digest_does_not_merge_across_dates_relationships_or_scopes(field, value):
    bundle = digest_bundle("三环集团公布回购计划", "三环集团公布回购计划")
    setattr(bundle.items[1], field, value)
    digest = select_news_digest(bundle)
    assert digest["counts"]["grouped_events"] == 2
    assert digest["counts"]["duplicate_events"] == 0
    assert_digest_counts(digest)


def test_digest_keeps_same_headline_event_for_different_companies_separate():
    bundle = digest_bundle("三环集团公布回购计划")
    other = digest_bundle("君正股份公布回购计划", name="君正股份", code="300223")
    bundle.items += other.items
    bundle.related_assets += other.related_assets
    digest = select_news_digest(bundle)
    assert digest["counts"]["selected"] == 2
    assert digest["counts"]["duplicate_events"] == 0
    assert {g["entity_name"] for g in digest["items"]} == {"三环集团", "君正股份"}


@pytest.mark.parametrize("field,value", [
    ("date_status", "unknown"), ("date_status", "future"), ("date_status", "stale"),
    ("published_date", None), ("published_date", "昨天"),
    ("model_eligible", False), ("model_eligible", "true"), ("model_eligible", 1),
    ("evidence_scope", "weak_background"), ("evidence_scope", "unknown"),
    ("url", "javascript:alert(1)"), ("title", ""),
])
def test_digest_never_promotes_ineligible_evidence_or_invents_date(field, value):
    bundle = digest_bundle("三环集团公布回购计划", "三环集团公布回购计划")
    setattr(bundle.items[0], field, value)
    original = deepcopy(bundle)
    digest = select_news_digest(bundle)
    assert digest["counts"]["ineligible"] == 1
    assert digest["counts"]["selected"] == 1
    assert digest["items"][0]["sources"] == [bundle.items[1]]
    assert bundle == original
    assert_digest_counts(digest)


def test_digest_exact_url_duplicates_keep_records_but_not_repeated_slots():
    bundle = digest_bundle("三环集团公布回购计划", "三环集团：公布回购计划")
    bundle.items[1].url = bundle.items[0].url + "#source"
    digest = select_news_digest(bundle)
    assert digest["counts"]["duplicate_urls"] == 1
    assert digest["counts"]["duplicate_events"] == 0
    assert len(digest["items"][0]["sources"]) == 2
    assert_digest_counts(digest)


def test_digest_exact_url_duplicate_can_group_without_guessing_missing_relationship():
    bundle = digest_bundle("三环集团公布回购计划", "三环集团公布回购计划")
    bundle.related_assets = []
    bundle.items[1].url = bundle.items[0].url
    digest = select_news_digest(bundle)
    assert digest["counts"]["duplicate_urls"] == 1
    assert digest["items"][0]["entity_name"] == ""
    assert len(digest["items"][0]["sources"]) == 2


def test_same_url_cannot_hide_materially_updated_title():
    bundle = digest_bundle("三环集团公布回购计划", "三环集团取消回购计划")
    bundle.items[1].url = bundle.items[0].url
    digest = select_news_digest(bundle)
    assert digest["counts"]["grouped_events"] == 2
    assert digest["counts"]["selected"] == 2
    assert_digest_counts(digest)


def test_digest_prefers_entity_diversity_before_second_material_event():
    bundle = digest_bundle("三环集团公布季度财报", "三环集团公布回购计划", "三环集团获得新订单")
    other = digest_bundle("君正股份公布业绩增长", name="君正股份", code="300223")
    bundle.items += other.items
    bundle.related_assets += other.related_assets
    digest = select_news_digest(bundle, max_items=2)
    assert {g["entity_name"] for g in digest["items"]} == {"三环集团", "君正股份"}
    assert digest["counts"]["entity_limit"] == 1
    assert digest["counts"]["selection_limit"] == 1
    assert_digest_counts(digest)
    expanded = select_news_digest(bundle, max_items=3)
    assert expanded["counts"]["selected"] == 3
    assert expanded["counts"]["entity_limit"] == 1
    assert_digest_counts(expanded)


def test_digest_different_verbs_alone_do_not_create_second_company_slot():
    bundle = digest_bundle("三环集团取得金融机构股票回购贷款承诺", "三环集团获得金融机构股票回购贷款承诺")
    digest = select_news_digest(bundle)
    assert digest["counts"]["duplicate_events"] == 0
    assert digest["counts"]["entity_limit"] == 1
    assert digest["counts"]["selected"] == 1


def test_digest_missing_or_ambiguous_relationship_never_guesses_company():
    bundle = digest_bundle("苹果公布回购计划", "苹果公布回购计划", name="苹果", code="AAPL")
    # Existing holding identity gate requires the disclosed code for short names.
    digest = select_news_digest(bundle)
    assert all(group["entity_key"] == "" for group in digest["items"])
    assert digest["counts"]["duplicate_events"] == 0
    bundle = digest_bundle("三环集团公布回购计划", "三环集团公布回购计划")
    bundle.related_assets[0] = replace(bundle.related_assets[0], verified=False)
    assert all(group["entity_key"] == "" for group in select_news_digest(bundle)["items"])


@pytest.mark.parametrize("max_items", [0, 4, True, 1.5])
def test_digest_rejects_expanded_or_invalid_bounds(max_items):
    with pytest.raises(ValueError):
        select_news_digest(FundNewsBundle(), max_items=max_items)


@pytest.mark.parametrize("saved_json", [False, True])
def test_digest_exposes_canonical_indices_and_relationship_without_upgrading_evidence(saved_json):
    bundle = digest_bundle(
        "三环集团(300408.SZ)：取得金融机构股票回购贷款承诺书_公司新闻",
        "三环集团取得不超8.1亿元金融机构股票回购贷款承诺_公司新闻_财经_证券之星",
    )
    if saved_json:
        bundle = json.loads(json.dumps(asdict(bundle), default=str, ensure_ascii=False))
    raw = bundle['items'] if saved_json else bundle.items
    original = deepcopy(bundle)
    group = select_news_digest(bundle)['items'][0]
    assert group['item_index'] == 1
    assert group['source_indices'] == [1, 0]
    assert group['item'] is raw[1]
    assert group['sources'] == [raw[i] for i in group['source_indices']]
    assert group['reporting_context'] == 'historical_holding'
    assert group['verification_status'] == 'headline_only_unverified'
    assert group['impact_status'] == 'unknown'
    assert group['relationship'] == {
        'category': 'holding', 'name': '三环集团', 'code': '300408',
        'source_date': '2026-09-30' if saved_json else date(2026, 9, 30),
        'source_url': 'https://fund.example.com/holdings', 'verified': True,
    }
    assert '不等于本基金重大事件' in group['relevance_note']
    assert bundle == original


@pytest.mark.parametrize('mismatch', ['source_date', 'source_url', 'identity', 'ambiguous', 'verified'])
def test_digest_does_not_bind_an_unrelated_or_ambiguous_asset(mismatch):
    bundle = digest_bundle('三环集团公布回购计划')
    asset = bundle.related_assets[0]
    if mismatch == 'source_date':
        asset.source_date = date(2026, 6, 30)
    elif mismatch == 'source_url':
        asset.source_url = 'https://fund.example.com/another'
    elif mismatch == 'identity':
        asset.name = '其他公司'
    elif mismatch == 'verified':
        asset.verified = False
    else:
        bundle.related_assets.append(replace(asset, code='999999'))
    group = select_news_digest(bundle)['items'][0]
    assert group['relationship'] is None
    assert group['entity_key'] == ''
    assert group['reporting_context'] == 'unresolved'
    assert group['impact_status'] == 'unknown'


def test_digest_prioritizes_fund_then_target_before_historical_company_and_sector():
    bundle = digest_bundle('三环集团公布回购计划')
    stock = bundle.items[0]
    target = FundRelatedAsset('target_etf', '测试ETF', 'https://fund.example.com/profile',
                              code='510000', verified=True)
    sector = FundRelatedAsset('industry', '半导体', 'https://fund.example.com/industry',
                              source_date=date(2026, 9, 30), verified=True)
    bundle.related_assets.extend([target, sector])
    bundle.items.extend([
        replace(stock, category='industry', title='半导体行业发布新政策', evidence_scope='sector_context',
                url='https://news.example.com/sector', source_url=sector.source_url),
        replace(stock, category='target_etf', title='测试ETF恢复申购',
                url='https://news.example.com/target', source_url=target.source_url, source_date=None),
        replace(stock, category='direct_fund', title='测试基金C暂停申购', evidence_scope='fund_event',
                url='https://news.example.com/fund', source_url=target.source_url, source_date=None),
    ])
    digest = select_news_digest(bundle)
    assert [g['reporting_context'] for g in digest['items']] == ['fund', 'direct_target', 'historical_holding']
    assert [g['item_index'] for g in digest['items']] == [3, 2, 0]
    assert digest['items'][1]['relationship']['code'] == '510000'
    assert all(g['impact_status'] == 'unknown' for g in digest['items'])
    assert_digest_counts(digest)


def test_run10_digest_news_is_historical_background_not_confirmed_fund_events():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'fund_brief_run10.json').read_text())
    selected = []
    for bundle in fixture['news_evidence'].values():
        original = deepcopy(bundle)
        digest = select_news_digest(bundle)
        selected.extend(digest['items'])
        assert bundle == original
        assert_digest_counts(digest)
    assert len(selected) == 4
    assert all(g['reporting_context'] == 'historical_holding' for g in selected)
    assert all(g['verification_status'] == 'headline_only_unverified' for g in selected)
    assert all(g['impact_status'] == 'unknown' for g in selected)
    assert all(g['relationship']['source_date'] == '2026-09-30' for g in selected)


# Exact Run11 title and URL from news-source-review.json; the remaining cases
# are synthetic counterexamples, not claims that their events occurred.
_RUN11_NAV_TITLE = "壁仞科技股吧在哪？2026年港股上市行情与投资者讨论入口 ..."
_RUN11_NAV_URL = "https://kuku.baidu.com/landing/tscp_doc/b7417accc35f61494dc6ca2250f311ae"


@pytest.mark.parametrize("title,url", [
    (_RUN11_NAV_TITLE, _RUN11_NAV_URL),
    ("壁仞科技上市公告在哪里看？", "https://news.example.com/guide"),
    ("如何查询壁仞科技股票上市行情", "https://news.example.com/guide"),
    ("壁仞科技股吧：上市资料与讨论区", "https://news.example.com/forum"),
    ("壁仞科技发布财报：股票论坛入口导航", "https://news.example.com/portal"),
    ("壁仞科技公布回购计划", "https://guba.eastmoney.com/list,06082.html"),
    ("壁仞科技 stock forum directory：上市资料", "https://news.example.com/directory"),
])
def test_navigation_cannot_become_event_from_fresh_date_or_snippet(title, url):
    bundle = gather_fund_news(holding_data("06082", "壁仞科技"), search(item(
        title, url, snippet="壁仞科技发布财报并宣布股票回购")), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["holding"].status == "none_filtered"
    assert bundle.coverage["holding"].reason_counts == {"static_page": 1}
    assert bundle.coverage["holding"].fresh == 0


@pytest.mark.parametrize("title,url", [
    ("壁仞科技公布上市计划", _RUN11_NAV_URL),
    ("壁仞科技发布季度财报_壁仞科技股吧", "https://guba.eastmoney.com/news,06082,123456.html"),
    ("壁仞科技股价上涨5%，投资者讨论业绩增长", "https://news.example.com/event"),
    ("壁仞科技公告：投资者关系网站入口变更", "https://news.example.com/notice"),
    ("壁仞科技股票停牌公告", "https://kuku.baidu.com/landing/other"),
])
def test_navigation_filter_preserves_event_articles_without_domain_bans(title, url):
    bundle = gather_fund_news(holding_data("06082", "壁仞科技"), search(item(title, url)), as_of=TODAY)
    assert len(bundle.items) == 1
    assert bundle.items[0].model_eligible is True
    digest = select_news_digest(bundle)
    assert digest["items"][0]["verification_status"] == "headline_only_unverified"
    assert digest["items"][0]["impact_status"] == "unknown"


@pytest.mark.parametrize("title", [
    "测试基金C公告在哪里看", "测试基金C净值查询入口与分红公告",
    "测试基金C如何查看财报公告",
])
def test_direct_fund_navigation_is_also_filtered(title):
    bundle = gather_fund_news(sample(), search(item(title)), as_of=TODAY)
    assert not bundle.items
    assert bundle.coverage["announcement"].reason_counts == {"static_page": 1}
    assert bundle.coverage["direct_fund"].reason_counts == {"static_page": 1}


@pytest.mark.parametrize("saved_json", [False, True])
def test_old_navigation_records_cannot_reenter_digest_and_remain_auditable(saved_json):
    bundle = digest_bundle(_RUN11_NAV_TITLE, "壁仞科技公布回购计划", name="壁仞科技", code="06082")
    bundle.items[0].url = _RUN11_NAV_URL
    if saved_json:
        bundle = json.loads(json.dumps(asdict(bundle), default=str, ensure_ascii=False))
    original = deepcopy(bundle)
    digest = select_news_digest(bundle)
    assert digest["counts"]["ineligible"] == 1
    assert digest["counts"]["selected"] == 1
    assert digest["items"][0]["item_index"] == 1
    assert digest["items"][0]["source_indices"] == [1]
    assert bundle == original
    assert_digest_counts(digest)


@pytest.mark.parametrize("name,code,title,url", [
    ("中微公司", "688012", "中微公司(688012):以集中竞价交易方式首次回购公司股份",
     "https://www.cfi.net.cn/p20261109002084.html"),
    ("兆易创新", "603986", "兆易创新回购 11.65 亿，股份将全部注销|兆易创新|财联社|回购_新浪新闻",
     "https://www.sina.cn/news/detail/5362438211896744.html"),
    ("三环集团", "300408", "三环集团取得不超8.1亿元金融机构股票回购贷款承诺_公司新闻_财经_证券之星",
     "https://wap.stockstar.com/detail/IG2026110600039256"),
])
def test_run11_concrete_title_leads_survive_without_claiming_body_verification(name, code, title, url):
    # Saved Run11 metadata, not a fresh request or independent event/date check.
    bundle = gather_fund_news(holding_data(code, name), search(item(title, url)), as_of=TODAY)
    assert bundle.items[0].title == title
    selected = select_news_digest(bundle)["items"][0]
    assert selected["verification_status"] == "headline_only_unverified"
    assert selected["impact_status"] == "unknown"
    assert selected["reporting_context"] == "historical_holding"
