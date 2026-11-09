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
    assert "## 001672" in report.markdown
    assert "1.100000" in report.markdown
    assert "2026-11-06" in report.markdown
    assert "10.00%" in report.markdown
    assert "N/A" in report.markdown
    assert report.notification_status == "disabled"
    saved = json.loads(open(report.snapshot_path, encoding="utf-8").read())
    assert saved["asset_type"] == "mutual_fund"
    assert saved["schema_version"] == 4
    assert saved["interpretation"]["fund_statuses"]["001672"]["status"] == "disabled"
    assert saved["interpretation"]["comparison_status"]["status"] == "disabled"
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
    assert "示例基金A基金公告" not in report.markdown
    assert "无关股票" not in report.markdown
    assert "javascript:" not in report.markdown
    assert "<script>" not in report.markdown
    assert search.search_topic_news_bounded.call_count == 2
    saved = json.loads(open(report.snapshot_path, encoding="utf-8").read())
    assert saved["news_evidence"]["001672"]["items"] == []
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
    assert "平安银行" not in report.markdown


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
    assert len(saved["news_evidence"][code]["items"]) == int(accepted)
    assert ("关联依据：标题含本基金全名" in news[0]) is accepted
    assert ("未找到通过本基金身份匹配" in news[0]) is not accepted
    assert "标题或摘要" not in report.markdown
    if accepted:
        assert "example.com/news" in news[0]
        assert "来源：example.com" in news[0]
        assert "发布日期：2026-11-06" in news[0]
    assert search.search_topic_news_bounded.call_count == 2
    for call in search.search_topic_news_bounded.call_args_list:
        assert call.args[0] == name
        assert call.kwargs["timeout_seconds"] <= 12.0
    assert set(saved["news_evidence"][code]["coverage"]) >= {"announcement", "direct_fund", "target_etf", "holding", "industry"}


def test_unexpected_news_component_error_keeps_nav_and_degrades(tmp_path):
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    with patch("src.services.fund_analysis_service.gather_fund_news", side_effect=RuntimeError("private raw detail")):
        report = run_fund_analysis("001672", fetcher=fetcher, search_service=Mock(), output_dir=tmp_path)
    assert not report.failed_codes and len(report.analyses) == 1
    assert "新闻组件不可用" in report.markdown
    assert "private raw detail" not in report.markdown


def test_interpretation_persists_model_and_cited_sections(tmp_path):
    from src.services.fund_interpretation import EvidenceFact, FundInsight, FundInterpretation, Statement
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    fact = EvidenceFact("F001672:nav", ["001672"], "nav", "程序净值事实", "https://example.test/nav", "2026-11-06")
    statement = Statement("数据反映已披露状况，后续仍需观察披露变化。", [fact.evidence_id])
    result = FundInterpretation("ok", "模型推理观点", "openai", "test-configured-model", "litellm",
                                {"001672": FundInsight(*[[statement] for _ in range(4)])}, facts={fact.evidence_id: fact})
    interpreter = Mock(return_value=result)
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, include_interpretation=True,
                               interpreter=interpreter, output_dir=tmp_path)
    assert report.interpretation_status == "ok"
    interpreter.assert_called_once()
    assert interpreter.call_args.kwargs == {"enabled": True}
    for text in ("核心结论", "风险提示", "后续观察", "test-configured-model", "openai", "https://example.test/nav"):
        assert text in report.markdown
    saved = json.loads(open(report.snapshot_path, encoding="utf-8").read())
    assert saved["schema_version"] == 4
    assert saved["interpretation"]["facts"][fact.evidence_id]["data_date"] == "2026-11-06"
    assert saved["notification"]["status"] == "disabled"


@pytest.mark.parametrize("failure", [RuntimeError("private provider detail"), object()])
def test_interpretation_component_failure_preserves_facts(tmp_path, failure):
    fetcher, interpreter = Mock(), Mock()
    fetcher.fetch.return_value = sample()
    if isinstance(failure, Exception):
        interpreter.side_effect = failure
    else:
        interpreter.return_value = failure
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, include_interpretation=True,
                               interpreter=interpreter, output_dir=tmp_path)
    assert not report.failed_codes and report.interpretation_status == "model_failed"
    assert "1.100000" in report.markdown and "未采用模型结论" in report.markdown
    assert "private provider detail" not in report.markdown


def test_default_service_and_historical_analysis_do_not_request_model(tmp_path):
    from src.services.fund_interpretation import FundInterpretation
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    interpreter = Mock(side_effect=lambda *args, **kwargs: FundInterpretation("disabled", "disabled"))
    run_fund_analysis("001672", fetcher=fetcher, include_news=False, interpreter=interpreter, output_dir=tmp_path)
    assert interpreter.call_args.kwargs["enabled"] is False
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, interpreter=interpreter,
                               include_interpretation=True, as_of=date(2026, 11, 6), output_dir=tmp_path)
    assert interpreter.call_args.kwargs["enabled"] is False
    assert "历史截止日不混入" in report.markdown


def test_real_interpretation_adapter_resolves_program_facts_in_report(tmp_path):
    from functools import partial
    from src.services.fund_interpretation import interpret_funds
    from src.llm.generation_backend import GenerationResult
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    statement = {"text": "已披露资料可用于观察历史情况：{{F001672:nav}}", "evidence_ids": ["F001672:nav"]}
    payload = {"funds": [{"fund_code": "001672", **{key: [statement] for key in ("conclusion", "risks", "evidence", "observations")}}], "comparison": []}
    analyzer = Mock()
    analyzer.get_generation_backend_identity.return_value = ("litellm", "openai/test")
    analyzer.is_available.return_value = True
    analyzer.generate_text_with_metadata.return_value = GenerationResult(json.dumps(payload), "test-model", "openai", "litellm", {})
    config = SimpleNamespace(generation_backend="litellm", generation_fallback_backend="", litellm_model="openai/test",
                             litellm_fallback_models=[], llm_model_list=[], openai_base_url="")
    interpreter = partial(interpret_funds, config=config, analyzer_factory=lambda **kwargs: analyzer)
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, include_interpretation=True,
                               interpreter=interpreter, output_dir=tmp_path)
    assert report.interpretation_status == "ok"
    assert "{{" not in report.markdown and "test-model" in report.markdown
    assert "2026-11-06" in report.markdown
    analyzer.generate_text_with_metadata.assert_called_once()


def test_component_diagnostic_is_consistent_and_never_leaks_exception(tmp_path, caplog):
    caplog.set_level('INFO', logger='src.services.fund_analysis_service')
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    secret = 'sk-private-provider-response-do-not-log'
    report = run_fund_analysis('001672', fetcher=fetcher, include_news=False,
                               include_interpretation=True, interpreter=Mock(side_effect=RuntimeError(secret)),
                               output_dir=tmp_path)
    saved = json.loads(open(report.snapshot_path, encoding='utf-8').read())
    assert saved['interpretation']['diagnostics'] == {'stage': 'component', 'code': 'component_failed'}
    for scope in (saved['interpretation']['fund_statuses']['001672'], saved['interpretation']['comparison_status']):
        assert scope['status'] == 'model_failed'
        assert scope['diagnostics'] == saved['interpretation']['diagnostics']
    assert r'component\_failed' not in report.markdown and 'component_failed' in caplog.text
    assert secret not in report.markdown + caplog.text + json.dumps(saved)
    assert report.notification_status == 'disabled'
    assert '1.100000' in report.markdown


def test_untrusted_diagnostic_details_are_removed_before_snapshot_render_and_log(tmp_path, caplog):
    from src.services.fund_interpretation import FundInterpretation
    caplog.set_level('INFO', logger='src.services.fund_analysis_service')
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    interpretation = FundInterpretation('model_failed', '失败，未采用模型结论。', diagnostics={
        'stage': 'component', 'code': 'component_failed', 'raw_response': 'PRIVATE_RESPONSE',
        'exception': 'SECRET_KEY', 'section': 'secret-section', 'fund_code': 'private-account',
        'counts': {'response_bytes': 80, 'private-body': 123, 'text_chars': 'SECRET_VALUE'},
    })
    report = run_fund_analysis('001672', fetcher=fetcher, include_news=False,
                               interpreter=Mock(return_value=interpretation), output_dir=tmp_path)
    saved_text = open(report.snapshot_path, encoding='utf-8').read()
    diag = json.loads(saved_text)['interpretation']['diagnostics']
    assert diag == {'stage': 'component', 'code': 'component_failed', 'counts': {'response_bytes': 80}}
    for marker in ('PRIVATE_RESPONSE', 'SECRET_KEY', 'secret-section', 'private-account', 'private-body', 'SECRET_VALUE'):
        assert marker not in saved_text + report.markdown + caplog.text


def test_search_initialization_exception_does_not_leak_credentials(tmp_path, caplog):
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    with patch('src.search_service.get_search_service', side_effect=RuntimeError('https://provider.test?api_key=PRIVATE_KEY')):
        report = run_fund_analysis('001672', fetcher=fetcher, include_news=True, output_dir=tmp_path)
    assert 'news_initialization_failed' in caplog.text
    assert 'PRIVATE_KEY' not in caplog.text + report.markdown
    assert not report.failed_codes


def test_news_report_separates_weak_background_and_sanitizes_filter_counts(tmp_path, caplog):
    from src.services.fund_news_service import FundNewsBundle, FundNewsCoverage, FundNewsItem
    caplog.set_level('INFO', logger='src.services.fund_analysis_service')
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    item = FundNewsItem('industry', '建设银行推出多项措施支持先进制造业', 'https://www.36kr.com/newsflashes/example',
                        '36氪', date(2026, 11, 6), 'fresh', datetime.now(timezone.utc),
                        '仅宽泛行业关系，非基金事件', evidence_scope='weak_background', model_eligible=False)
    coverage = FundNewsCoverage(status='ok', queries=1, accepted=1, fresh=1,
                               reason_counts={'static_page': 1, 'weak_background': 1,
                                              'SECRET_PRIVATE_TITLE': 1, 'related_event': 'PRIVATE_BODY'})
    bundle = FundNewsBundle(items=[item], coverage={'industry': coverage})
    with patch('src.services.fund_analysis_service.gather_fund_news', return_value=bundle):
        report = run_fund_analysis('001672', fetcher=fetcher, search_service=Mock(), output_dir=tmp_path)
    saved_text = open(report.snapshot_path, encoding='utf-8').read()
    saved = json.loads(saved_text)
    assert item.title not in report.markdown
    assert '以下仅为宽泛背景' in '\n'.join(saved['news']['001672'])
    assert '不作为模型事件证据' in '\n'.join(saved['news']['001672'])
    assert '静态行情或资料页 1条' in '\n'.join(saved['news']['001672'])
    assert item.title not in report.markdown
    assert saved['news_evidence']['001672']['items'][0]['model_eligible'] is False
    assert saved['news_evidence']['001672']['coverage']['industry']['reason_counts'] == {'static_page': 1, 'weak_background': 1}
    for secret in ('SECRET_PRIVATE_TITLE', 'PRIVATE_BODY'):
        assert secret not in report.markdown + caplog.text + saved_text
    assert item.title not in caplog.text


def test_provider_response_validation_diagnostic_reaches_final_report_without_raw_body(tmp_path, caplog):
    from functools import partial
    from src.services.fund_interpretation import interpret_funds
    from src.llm.generation_backend import GenerationResult
    caplog.set_level('INFO', logger='src.services.fund_analysis_service')
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    statement = {'text': '公开资料仍需审慎核验，不推断未经证实的结论。', 'evidence_ids': ['PRIVATE_UNKNOWN_CITATION']}
    payload = {'funds': [{'fund_code': '001672', **{key: [statement] for key in ('conclusion', 'risks', 'evidence', 'observations')}}], 'comparison': []}
    analyzer = Mock()
    analyzer.get_generation_backend_identity.return_value = ('litellm', 'openai/test')
    analyzer.is_available.return_value = True
    analyzer.generate_text_with_metadata.return_value = GenerationResult(json.dumps(payload), 'test-model', 'openai', 'litellm', {})
    config = SimpleNamespace(generation_backend='litellm', generation_fallback_backend='', litellm_model='openai/test',
                             litellm_fallback_models=[], llm_model_list=[], openai_base_url='')
    interpreter = partial(interpret_funds, config=config, analyzer_factory=lambda **kwargs: analyzer)
    report = run_fund_analysis('001672', fetcher=fetcher, include_news=False, include_interpretation=True,
                               interpreter=interpreter, output_dir=tmp_path)
    saved_text = open(report.snapshot_path, encoding='utf-8').read()
    saved = json.loads(saved_text)
    diag = saved['interpretation']['diagnostics']
    assert diag['stage'] == 'citation' and diag['code'] == 'citation_unknown'
    assert diag['fund_code'] == '001672' and diag['section'] == 'conclusion'
    assert diag['item_index'] == 0
    assert saved['interpretation']['funds'] == {} and saved['interpretation']['comparison'] == []
    assert r'citation\_unknown' not in report.markdown and 'citation_unknown' in caplog.text
    assert 'PRIVATE_UNKNOWN_CITATION' not in saved_text + report.markdown + caplog.text
    assert '1.100000' in report.markdown
    assert report.notification_status == 'disabled'
    analyzer.generate_text_with_metadata.assert_called_once()


@pytest.mark.parametrize('reject_fund', [True, False])
def test_partial_provider_result_preserves_accepted_insights_and_notifies_once(tmp_path, reject_fund):
    from functools import partial
    from src.services.fund_interpretation import interpret_funds
    from src.llm.generation_backend import GenerationResult
    codes = ['001672', '024480']
    fetcher, notifier, analyzer = Mock(), Mock(), Mock()
    fetcher.fetch.side_effect = [sample(code) for code in codes]
    funds = []
    for code in codes:
        statement = {'text': '已披露资料可用于观察历史情况：{{F' + code + ':nav}}',
                     'evidence_ids': [f'F{code}:nav']}
        funds.append({'fund_code': code, **{key: [statement] for key in
                                          ('conclusion', 'risks', 'evidence', 'observations')}})
    if reject_fund:
        funds[1]['conclusion'] = [{'text': '应拒绝的原始模型文字。', 'evidence_ids': ['PRIVATE_UNKNOWN_ID']}]
    payload = {'funds': funds, 'comparison': [
        {'text': '应拒绝的横向模型文字。', 'evidence_ids': ['PRIVATE_COMPARISON_ID']},
    ]}
    analyzer.get_generation_backend_identity.return_value = ('litellm', 'openai/test')
    analyzer.is_available.return_value = True
    analyzer.generate_text_with_metadata.return_value = GenerationResult(json.dumps(payload), 'test-model', 'openai', 'litellm', {})
    config = SimpleNamespace(generation_backend='litellm', generation_fallback_backend='', litellm_model='openai/test',
                             litellm_fallback_models=[], llm_model_list=[], openai_base_url='')
    notifier.is_available.return_value = True
    notifier.send_with_results.return_value = SimpleNamespace(status='sent', channel_results=[])
    report = run_fund_analysis(codes, fetcher=fetcher, include_news=False, include_interpretation=True,
                               interpreter=partial(interpret_funds, config=config, analyzer_factory=lambda **kwargs: analyzer),
                               notifier=notifier, send_notification=True, output_dir=tmp_path)
    saved_text = open(report.snapshot_path, encoding='utf-8').read()
    saved = json.loads(saved_text)
    interpretation = saved['interpretation']
    assert saved['schema_version'] == 4
    assert report.interpretation_status == interpretation['status'] == 'partial'
    assert interpretation['fund_statuses']['001672']['status'] == 'ok'
    assert interpretation['fund_statuses']['024480']['status'] == ('model_failed' if reject_fund else 'ok')
    assert interpretation['comparison_status']['status'] == 'model_failed'
    assert interpretation['comparison'] == []
    assert set(interpretation['funds']) == ({'001672'} if reject_fund else set(codes))
    assert len(saved['analyses']) == 2 and not report.failed_codes
    assert report.markdown.count('最新已披露单位净值：1.100000') == 2
    assert report.markdown.count('### AI基金解读（推理观点，非事实认证）') == (1 if reject_fund else 2)
    assert '部分AI解读通过校验' in report.markdown
    assert '未生成可采用的模型结论' not in report.markdown
    assert '横向解读失败' in report.markdown
    for marker in ('应拒绝的原始模型文字', '应拒绝的横向模型文字', 'PRIVATE_UNKNOWN_ID', 'PRIVATE_COMPARISON_ID'):
        assert marker not in report.markdown + saved_text
    analyzer.generate_text_with_metadata.assert_called_once()
    notifier.send_with_results.assert_called_once_with(
        report.markdown, route_type='report', email_subject=f"场外基金已披露净值日报 - {saved['as_of']}",
    )
    assert saved['notification']['status'] == report.notification_status == 'sent'


def test_partial_scope_diagnostics_are_sanitized_at_reporting_boundary(tmp_path, caplog):
    from src.services.fund_interpretation import (
        FundInsight, FundInterpretation, InterpretationScopeStatus, Statement,
    )
    caplog.set_level('INFO', logger='src.services.fund_analysis_service')
    fetcher = Mock()
    fetcher.fetch.side_effect = [sample('001672'), sample('024480')]
    unsafe = {'stage': 'citation', 'code': 'citation_unknown', 'raw_response': 'PRIVATE_RESPONSE',
              'exception': 'SECRET_KEY', 'section': 'secret-section', 'fund_code': 'private-account',
              'counts': {'citation_count': 1, 'private-body': 123, 'text_chars': 'SECRET_VALUE'}}
    interpretation = FundInterpretation(
        'partial', '部分基金观点通过校验。', funds={'001672': FundInsight(conclusion=[Statement('已接受的观点。', [])])},
        fund_statuses={'001672': InterpretationScopeStatus('ok', '通过校验。'),
                       '024480': InterpretationScopeStatus('model_failed', '引用校验失败。', dict(unsafe))},
        comparison_status=InterpretationScopeStatus('model_failed', '引用校验失败。', dict(unsafe)),
    )
    report = run_fund_analysis(['001672', '024480'], fetcher=fetcher, include_news=False,
                               interpreter=Mock(return_value=interpretation), output_dir=tmp_path)
    saved_text = open(report.snapshot_path, encoding='utf-8').read()
    saved = json.loads(saved_text)['interpretation']
    expected = {'stage': 'citation', 'code': 'citation_unknown', 'counts': {'citation_count': 1}}
    assert saved['fund_statuses']['024480']['diagnostics'] == expected
    assert saved['comparison_status']['diagnostics'] == expected
    assert '模型解读范围安全诊断 024480:' in caplog.text
    assert '模型解读范围安全诊断 comparison:' in caplog.text
    assert '已接受的观点' in report.markdown and '引用校验失败' in report.markdown
    assert r'citation\_unknown' not in report.markdown
    for marker in ('PRIVATE_RESPONSE', 'SECRET_KEY', 'secret-section', 'private-account', 'private-body', 'SECRET_VALUE'):
        assert marker not in saved_text + report.markdown + caplog.text
