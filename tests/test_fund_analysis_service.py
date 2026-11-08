from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.core.fund_analysis import FundData, FundProfile, NavPoint
from src.services.fund_analysis_service import run_fund_analysis


def sample(code="001672", name="示例基金A"):
    return FundData(
        FundProfile(code, name, "股票型", source_url=f"https://fundf10.eastmoney.com/jbgk_{code}.html"),
        [NavPoint(date(2026, 11, 5), 1.0, 1.0), NavPoint(date(2026, 11, 6), 1.1, 1.1)],
        datetime(2026, 11, 8, tzinfo=timezone.utc),
        f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
    )


def test_report_is_fund_specific_and_snapshot_is_isolated(tmp_path):
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    report = run_fund_analysis("fund:001672", fetcher=fetcher, include_news=False,
                               output_dir=tmp_path, as_of=date(2026, 11, 8))
    assert "fund:001672" in report.markdown
    assert "1.100000" in report.markdown
    assert "2026-11-06" in report.markdown
    assert "10.00%" in report.markdown
    assert "N/A" in report.markdown
    assert report.notification_status == "disabled"
    saved = json.loads(open(report.snapshot_path, encoding="utf-8").read())
    assert saved["asset_type"] == "mutual_fund"
    assert saved["analyses"][0]["data"]["profile"]["code"] == "001672"
    for key in ("open", "high", "low", "close", "volume", "rating", "sentiment_score"):
        assert key not in saved["analyses"][0]["data"]["navs"][0]


def test_one_fund_failure_does_not_discard_other_or_repeat_notification(tmp_path):
    fetcher, notifier = Mock(), Mock()
    fetcher.fetch.side_effect = [sample(), ValueError("invalid NAV")]
    notifier.is_available.return_value = True
    notifier.send_with_results.return_value = SimpleNamespace(status="sent")
    report = run_fund_analysis(["001672", "024480"], fetcher=fetcher, include_news=False,
                               send_notification=True, notifier=notifier, output_dir=tmp_path)
    assert len(report.analyses) == 1
    assert report.failed_codes == {"024480": "invalid NAV"}
    assert report.notification_status == "sent"
    notifier.send_with_results.assert_called_once()
    kwargs = notifier.send_with_results.call_args.kwargs
    assert kwargs["route_type"] == "report"
    assert "基金" in kwargs["email_subject"]
    assert "email_stock_codes" not in kwargs
    assert "email_send_to_all" not in kwargs


def test_unknown_send_outcome_never_retries(tmp_path):
    fetcher, notifier = Mock(), Mock()
    fetcher.fetch.return_value = sample()
    notifier.send_with_results.side_effect = TimeoutError("unknown send outcome")
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False,
                               send_notification=True, notifier=notifier, output_dir=tmp_path)
    assert report.notification_status == "unknown"
    notifier.send_with_results.assert_called_once()
    assert report.report_path


def test_no_notify_never_initializes_notification_service(tmp_path):
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    with patch("src.notification.NotificationService", side_effect=AssertionError("must not instantiate")):
        run_fund_analysis("001672", fetcher=fetcher, include_news=False, output_dir=tmp_path)


def test_mismatched_identity_is_failed_not_relabelled(tmp_path):
    fetcher = Mock()
    fetcher.fetch.return_value = sample("000001")
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, output_dir=tmp_path)
    assert not report.analyses
    assert "不一致" in report.failed_codes["001672"]


def test_external_names_are_html_escaped(tmp_path):
    fetcher = Mock()
    fetcher.fetch.return_value = sample(name='<img src=x onerror="alert(1)">')
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, output_dir=tmp_path)
    assert "<img" not in report.markdown
    assert "&lt;img" in report.markdown


def test_news_reuses_topic_search_filters_identity_and_rejects_script_links(tmp_path):
    fetcher, search = Mock(), Mock()
    fetcher.fetch.return_value = sample()
    search.search_topic_news_bounded.return_value = SimpleNamespace(success=True, results=[
        SimpleNamespace(title="示例基金A基金公告", snippet="示例基金A", url="javascript:alert(1)",
                        source="<script>", published_date="2026-11-06"),
        SimpleNamespace(title="无关股票", snippet="不相关", url="https://example.com/stock", source="x", published_date=None),
    ])
    report = run_fund_analysis("001672", fetcher=fetcher, search_service=search, output_dir=tmp_path)
    assert "示例基金A基金公告" in report.markdown
    assert "无关股票" not in report.markdown
    assert "javascript:" not in report.markdown
    assert "<script>" not in report.markdown
    search.search_topic_news_bounded.assert_called_once()
    search.search_stock_news.assert_not_called()


def test_news_failure_is_visible_and_does_not_fail_nav(tmp_path):
    fetcher, search = Mock(), Mock()
    fetcher.fetch.return_value = sample()
    search.search_topic_news_bounded.side_effect = TimeoutError()
    report = run_fund_analysis("001672", fetcher=fetcher, search_service=search, output_dir=tmp_path)
    assert not report.failed_codes
    assert "新闻检索失败" in report.markdown


def test_normalized_analysis_controls_report_and_snapshot(tmp_path):
    fetcher = Mock()
    data = sample()
    data.navs = [NavPoint(date(2026, 12, 1), 99.0, 99.0), *reversed(data.navs)]
    fetcher.fetch.return_value = data
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, output_dir=tmp_path,
                               as_of=date(2026, 11, 8))
    assert "99.000000" not in report.markdown
    assert "最新已披露单位净值：1.100000" in report.markdown


@pytest.mark.parametrize("codes", ["", [], "600519.SH", "001672,"])
def test_invalid_fund_input_is_rejected_before_network(codes, tmp_path):
    fetcher = Mock()
    with pytest.raises(ValueError):
        run_fund_analysis(codes, fetcher=fetcher, include_news=False, output_dir=tmp_path)
    fetcher.fetch.assert_not_called()


def test_historical_cutoff_is_forwarded_and_current_news_omitted(tmp_path):
    fetcher, search = Mock(), Mock()
    data = sample()
    data.warnings.append("基金概况为当前资料，非历史时点快照。")
    fetcher.fetch.return_value = data
    report = run_fund_analysis("001672", fetcher=fetcher, search_service=search, output_dir=tmp_path,
                               as_of=date(2026, 11, 6))
    fetcher.fetch.assert_called_once_with("001672", as_of=date(2026, 11, 6))
    search.search_topic_news_bounded.assert_not_called()
    assert "非历史时点快照" in report.markdown
    assert "避免前视信息" in report.markdown


def test_same_numeric_stock_code_is_not_fund_news(tmp_path):
    fetcher, search = Mock(), Mock()
    fetcher.fetch.return_value = sample(code="000001", name="华夏成长混合")
    search.search_topic_news_bounded.return_value = SimpleNamespace(success=True, results=[
        SimpleNamespace(title="平安银行(000001.SZ)发布年度报告", snippet="银行股票公告", url="https://example.com/a",
                        source="example", published_date="2026-11-06"),
    ])
    report = run_fund_analysis("000001", fetcher=fetcher, search_service=search, output_dir=tmp_path)
    assert "平安银行" not in report.markdown
    assert "未找到通过本基金身份匹配" in report.markdown


def test_notification_transport_feedback_is_persisted_without_recipients(tmp_path):
    fetcher, notifier = Mock(), Mock()
    fetcher.fetch.return_value = sample()
    notifier.send_with_results.return_value = SimpleNamespace(
        status="sent", channel_results=[SimpleNamespace(channel="email", success=True, error_code=None)],
    )
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, send_notification=True,
                               notifier=notifier, output_dir=tmp_path)
    snapshot = json.loads(open(report.snapshot_path, encoding="utf-8").read())
    assert snapshot["notification"] == {
        "status": "sent", "channels": [{"channel": "email", "success": True, "error_code": None}],
    }
    notifier.send_with_results.assert_called_once()


@pytest.mark.parametrize("code,name,title,snippet,accepted", [
    ("025500", "东方阿尔法科技智选混合发起C",
     "fmvp得分仅排第十？盘点25-26赛季NBA得分前十榜单加短评",
     "东方阿尔法科技智选混合发起C", False),
    ("001672", "国寿安保智慧生活股票A", "基金重仓股调整引关注",
     "国寿安保智慧生活股票A基金重仓该股", False),
    ("001672", "国寿安保智慧生活股票A",
     "11月6日国瓷材料跌5.95%，国寿安保智慧生活股票A基金重仓该股_大风号_凤凰网", "", True),
    ("001672", "国寿安保智慧生活股票A",
     "11月6日联瑞新材跌5.24%，国寿安保智慧生活股票A基金重仓该股", "", True),
    ("001672", "国寿安保智慧生活股票A", "国寿安保智慧生活股票C基金公告",
     "国寿安保智慧生活股票A", False),
    ("001672", "国寿安保智慧生活股票A", "国寿安保智慧生活股票AC基金公告", "", False),
    ("021190", "南方亚太精选ETF联接(QDII)C",
     "南方亚太精选ＥＴＦ联接（QDII）Ｃ基金公告", "", True),
    ("021190", "南方亚太精选ETF联接(QDII)C",
     "南方亚太精选ETF联接&#40;QDII&#41;&nbsp;C 基金公告", "", True),
])
def test_news_requires_full_fund_identity_in_headline(tmp_path, code, name, title, snippet, accepted):
    fetcher, search = Mock(), Mock()
    fetcher.fetch.return_value = sample(code=code, name=name)
    search.search_topic_news_bounded.return_value = SimpleNamespace(success=True, results=[
        SimpleNamespace(title=title, snippet=snippet, url="https://example.com/news",
                        source="example.com", published_date="2026-11-06"),
    ])
    report = run_fund_analysis(code, fetcher=fetcher, search_service=search, output_dir=tmp_path)
    saved = json.loads(open(report.snapshot_path, encoding="utf-8").read())
    news = saved["news"][code]
    assert len(news) == 1
    assert ("关联依据：标题含本基金全名" in news[0]) is accepted
    assert ("未找到通过本基金身份匹配" in news[0]) is not accepted
    assert "标题或摘要" not in report.markdown
    if accepted:
        assert "example.com/news" in news[0]
        assert "来源：example.com" in news[0]
        assert "发布日期：2026-11-06" in news[0]
    search.search_topic_news_bounded.assert_called_once_with(
        name, max_results=5, focus_keywords=[f'"{name}"', f'"{code}"', "基金 公告"], timeout_seconds=12.0,
    )
