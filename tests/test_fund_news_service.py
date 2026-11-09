"""Offline regressions for reconstructed fund evidence and bounded selection."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.core.fund_analysis import FundData, FundProfile, FundRelatedAsset, Holding, HoldingsSnapshot, NavPoint
from src.services.fund_news_service import _headline_matches_fund, gather_fund_news

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
        SimpleNamespace(success=True, results=[item("测试基金C新闻", url=f"https://news.example.com/d{i}") for i in range(100)]),
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
