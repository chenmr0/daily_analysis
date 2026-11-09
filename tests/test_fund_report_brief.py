"""Offline report contracts using retained dates, figures and news, not live AI."""
import copy
from datetime import date, datetime
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pytest

from src.core.fund_analysis import (
    FundAnalysis, FundData, FundProfile, FundRelatedAsset, Holding, HoldingsSnapshot, Metric, NavPoint,
)
from src.services.fund_analysis_service import render_fund_report, run_fund_analysis
from src.services.fund_interpretation import (
    EvidenceFact, FundInsight, FundInterpretation, InterpretationScopeStatus, Statement,
    ResearchDecision, NewsImpact, NewsAssessment, _build_context,
)

FIXTURE = Path(__file__).parent / 'fixtures/fund_brief_run10.json'


def load_snapshot(path=FIXTURE):
    saved = json.loads(Path(path).read_text())
    saved.setdefault('news', {})
    analyses = []
    for original in saved['analyses']:
        row = copy.deepcopy(original)
        data = row['data']
        profile = data['profile']
        if profile['inception_date']:
            profile['inception_date'] = date.fromisoformat(profile['inception_date'])
        data['profile'] = FundProfile(**profile)
        data['navs'] = [NavPoint(**{**p, 'nav_date': date.fromisoformat(p['nav_date'])}) for p in data['navs']]
        data['fetched_at'] = datetime.fromisoformat(data['fetched_at'])
        portfolio = data['holdings']
        if portfolio:
            portfolio['holdings'] = [Holding(**h) for h in portfolio['holdings']]
            for key in ('published_at', 'report_period_end'):
                portfolio[key] = date.fromisoformat(portfolio[key]) if portfolio[key] else None
            data['holdings'] = HoldingsSnapshot(**portfolio)
        for asset in data['related_assets']:
            for key in ('published_at', 'source_date'):
                asset[key] = date.fromisoformat(asset[key]) if asset[key] else None
        data['related_assets'] = [FundRelatedAsset(**a) for a in data['related_assets']]
        metrics = {}
        for key, metric in row['metrics'].items():
            for field in ('start_date', 'end_date'):
                metric[field] = date.fromisoformat(metric[field]) if metric[field] else None
            metrics[key] = Metric(**metric)
        analyses.append(FundAnalysis(FundData(**data), metrics, row['warnings']))
    raw = copy.deepcopy(saved['interpretation'])
    raw['fund_statuses'] = {code: InterpretationScopeStatus(**scope) for code, scope in raw['fund_statuses'].items()}
    raw['comparison_status'] = InterpretationScopeStatus(**raw['comparison_status'])
    raw['facts'] = ({key: EvidenceFact(**fact) for key, fact in raw['facts'].items()}
                    if 'facts' in raw else _build_context(analyses, saved.get('news_evidence'))[0])
    converted = {}
    for code, insight in raw['funds'].items():
        values = {key: [Statement(**entry) for entry in insight.get(key, [])]
                  for key in ('conclusion', 'risks', 'evidence', 'observations')}
        decision = insight.get('decision')
        if decision is not None:
            values['decision'] = ResearchDecision(**{
                **decision, **{key: Statement(**decision[key]) if decision[key] is not None else None
                               for key in ('rationale', 'conditions', 'invalidation')}})
        values['news_impacts'] = [NewsImpact(**{**item, 'transmission': Statement(**item['transmission'])})
                                  for item in insight.get('news_impacts', [])]
        assessment = insight.get('news_assessment')
        if assessment is not None:
            values['news_assessment'] = NewsAssessment(assessment['status'],
                Statement(**assessment['explanation']) if assessment['explanation'] is not None else None)
        converted[code] = FundInsight(**values)
    raw['funds'] = converted
    raw['comparison'] = [Statement(**s) for s in raw['comparison']]
    return saved, analyses, FundInterpretation(**raw)


def render_retained():
    saved, analyses, interpretation = load_snapshot()
    text = render_fund_report(analyses, saved['failed_codes'], saved['news'], datetime.fromisoformat(saved['generated_at']),
                              interpretation, as_of=date.fromisoformat(saved['as_of']), news_evidence=saved['news_evidence'])
    return text, saved, analyses


def test_retained_brief_starts_with_fund_changes_not_boilerplate():
    text, saved, analyses = render_retained()
    assert text.splitlines()[:5] == [
        '# 场外基金日报｜2026-11-09', '', 'AI：本期请求超时，未取得可用解读。', '',
        '## 021190 南方亚太精选ETF联接(QDII)C',
    ]
    assert text.count('AI：本期请求超时') == 1
    assert 'AI：超时' not in text
    assert all(x not in text for x in ('观望', '操作参考', '全部基金速览', '若已持有', '若未持有',
                                       '简要口径与边界', '仅作数据研究', '日历月锚点', '安全诊断（', '不作优劣排名'))
    assert '09-30→11-05，36天' in text
    assert text.count('09-30→11-06，37天') == 5
    for a in analyses:
        m = a.metrics['return_1m']
        assert f'{m.value * 100:+.2f}%' in text
        assert f'{a.data.navs[-1].unit_nav:.6f}' in text
    assert text.count('## ') == 6
    assert '历史名单交集' not in text
    assert len(text.splitlines()) < 90
    assert text.count('相邻净值变化：') == 6
    assert '| ---' not in text and '<table' not in text
    visible = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    assert max(map(len, visible.splitlines())) < 120
    assert all(a.data.profile.currency is None for a in analyses)
    assert not any(label in text for label in ('人民币', '排名', '最好', '最差基金', '你的亏损'))
    assert len(saved['provenance']['source_sha256']) == 64


def test_retained_news_dates_and_historical_relationships_are_explicit():
    text, saved, _ = render_retained()
    assert text.count('晚于所列净值日，仅作后续线索') == 4
    assert '2026-09-30 历史股票名单中的中微公司' in text
    assert '是否仍持有未核验' in text
    assert '联接基金股票表未穿透目标ETF' in text
    assert '2026-11-05' in text and 'QDII：海外估值、假期与汇率' in text
    assert text.count('同主体同日相近线索合并2条来源') == 1
    assert '董事会会议决议' not in text
    assert len(saved['news_evidence']['024480']['items']) == 2
    assert '关系来源' in text
    assert 'FundArchivesDatas.aspx?type=jjcc&code=020356' in text
    assert len(saved['analyses'][2]['data']['holdings']['holdings']) == 10
    assert '当前持仓' not in text and '导致' not in text


@pytest.mark.parametrize('key', ['return_1m', 'return_3m', 'return_12m', 'max_drawdown_12m'])
@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf'), 1e308])
def test_missing_or_invalid_metrics_never_drive_brief_findings(key, value):
    saved, analyses, interpretation = load_snapshot()
    a = analyses[0]
    a.metrics[key].value = value
    a.metrics[key].reason = '存在分红/拆分事件，缺少经调整的总回报历史'
    text = render_fund_report([a], {}, {}, datetime.fromisoformat(saved['generated_at']), interpretation)
    assert 'N/A（存在分红/拆分事件' in text
    assert 'nan%' not in text and 'inf%' not in text
    if key in {'return_1m', 'return_12m'}:
        assert '两个区间方向不同' not in text


def test_missing_dates_never_become_invented_periods():
    saved, analyses, interpretation = load_snapshot()
    analyses[0].metrics['return_1m'].start_date = None
    text = render_fund_report(analyses[:1], {}, {}, datetime.fromisoformat(saved['generated_at']), interpretation)
    assert '约1个月净值变化：N/A' in text
    assert 'None 至' not in text


def test_safe_html_and_news_title_projection():
    from src.formatters import markdown_to_html_document
    saved, analyses, interpretation = load_snapshot()
    analyses[2].data.profile.name = '<img src=x onerror=alert(1)> [x](javascript:alert(1))'
    bundle = saved['news_evidence']['020356']
    bundle['items'][0]['title'] = '<script>alert(1)</script>公司回购公告'
    text = render_fund_report(analyses[2:3], {}, {}, datetime.fromisoformat(saved['generated_at']), interpretation,
                              news_evidence={'020356': bundle})
    rendered = markdown_to_html_document(text)
    assert '<img src=x' not in rendered and '<script>alert' not in rendered
    assert 'href="javascript:' not in rendered
    assert '&lt;img' in rendered and '&lt;script' in rendered
    assert '<table' not in rendered


def test_service_fake_keeps_raw_evidence_and_notifier_contract(tmp_path):
    saved, analyses, _ = load_snapshot()
    # Two observations intentionally test insufficient long history, not fresh retrieval.
    fetcher = Mock()
    fetcher.fetch.side_effect = [analyses[0].data, ValueError('identity mismatch')]
    interpreter = Mock(return_value=FundInterpretation('disabled', '未启用'))
    notifier = Mock()
    notifier.send_with_results.return_value = Mock(status='sent', channel_results=[])
    report = run_fund_analysis(['021190', '024480'], fetcher=fetcher, interpreter=interpreter,
                               include_news=False, send_notification=True, notifier=notifier, output_dir=tmp_path)
    snapshot = json.loads(Path(report.snapshot_path).read_text())
    assert snapshot['schema_version'] == 4 and len(snapshot['action_references']) == 2
    assert snapshot['failed_codes'] == {'024480': 'identity mismatch'}
    assert 'fund:024480' in report.markdown and 'AI未启用' in report.markdown
    assert 'N/A' in report.markdown
    notifier.send_with_results.assert_called_once()
    assert notifier.send_with_results.call_args.args == (report.markdown,)
    assert notifier.send_with_results.call_args.kwargs['route_type'] == 'report'
    assert not any(word in report.markdown for word in ('观望', '若未持有', '若已持有'))


def test_historical_list_intersection_requires_same_code_name_and_period():
    from src.services.fund_analysis_service import _historical_intersections
    _, analyses, _ = load_snapshot()
    text = '\n'.join(_historical_intersections(analyses, date(2026, 11, 9)))
    assert '018123（4.20%）、025500（7.39%）均列有长鑫科技（688825）' in text
    assert '001672（5.15%）、024480（7.00%）均列有南亚新材（688519）' in text
    assert '不是当前完整持仓重合度' in text
    analyses[4].data.holdings.report_period_end = date(2026, 6, 30)
    analyses[5].data.holdings.holdings[3].name = '不同证券同代码'
    assert _historical_intersections(analyses, date(2026, 11, 9)) == []


def test_failed_metric_with_numeric_value_is_not_presented_as_valid():
    saved, analyses, interpretation = load_snapshot()
    analyses[0].metrics['return_1m'].reason = '存在分红/拆分事件'
    text = render_fund_report(analyses[:1], {}, {}, datetime.fromisoformat(saved['generated_at']), interpretation)
    assert '-9.30%' not in text and 'N/A（存在分红/拆分事件' in text
    assert '两个区间方向不同' not in text


def test_news_incomplete_coverage_does_not_invent_a_failure_cause():
    text, _, _ = render_retained()
    assert '产品名称主题：部分检索未执行：查询、时间或条数预算用尽' in text
    assert '新闻检索失败' not in text and '未配置新闻检索' not in text
    assert text.index('约1个月净值变化') < text.index('产品名称主题：部分检索未执行')
    assert '**区间观察**' not in text
    assert '约3个月净值变化' not in text  # No fallback dump of every metric window.


@pytest.mark.parametrize('period', [None, date(2026, 12, 31)])
def test_undated_or_future_industry_and_holdings_do_not_form_report_facts(period):
    from src.services.fund_analysis_service import _holding_brief, _historical_intersections
    _, analyses, _ = load_snapshot()
    for a in analyses:
        for asset in a.data.related_assets:
            if asset.category == 'industry':
                asset.source_date = period
        if a.data.holdings:
            a.data.holdings.report_period_end = period
    text = '\n'.join(_holding_brief(analyses[1].data, date(2026, 11, 9)))
    assert '42.61%' not in text and '82.99%' not in text
    assert '报告期缺失或晚于截止日' in text
    assert _historical_intersections(analyses, date(2026, 11, 9)) == []


def test_compact_dates_do_not_hide_a_different_year():
    from src.services.fund_analysis_service import _period_dates
    assert _period_dates(date(2026, 9, 30), date(2026, 11, 6), date(2026, 11, 9)) == '09-30→11-06'
    assert _period_dates(date(2025, 11, 6), date(2026, 11, 6), date(2026, 11, 9)) == '2025-11-06→2026-11-06'
    assert _period_dates(date(2026, 9, 30), date(2026, 11, 6), date(2027, 1, 1)) == '2026-09-30→2026-11-06'
