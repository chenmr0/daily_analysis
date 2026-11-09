"""Offline final-report/one-notification contract for a bounded model repair."""
import json
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services import fund_interpretation as module
from src.services.fund_analysis_service import run_fund_analysis, _repair_note
from src.llm.generation_backend import GenerationResult
from tests.test_fund_interpretation import analysis, configuration, valid_output, fake_factory


@pytest.mark.parametrize('repair_valid', [True, False])
def test_repair_finishes_before_single_notification_and_does_not_recompute_data(tmp_path, repair_valid):
    analyses = [analysis('000001'), analysis('000002')]
    initial = valid_output(('000001', '000002'))
    initial['funds'][1]['conclusion'][0]['text'] = '本基金保证收益，拒绝原文不得再次转存。'
    repaired = valid_output(('000002',))
    if not repair_valid:
        repaired['funds'][0]['conclusion'][0]['text'] = '本基金保证收益，第二段私密文字不得转存。'
    replies = iter([initial, repaired]); calls = []
    def generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return GenerationResult(json.dumps(next(replies), ensure_ascii=False), 'fake-model', 'fake-provider', 'litellm', {})
    backend = SimpleNamespace(get_generation_backend_identity=lambda: ('litellm', 'test'), is_available=lambda: True,
                              generate_text_with_metadata=generate)
    fetcher = Mock(fetch=Mock(side_effect=[a.data for a in analyses]))
    notifier = Mock()
    notifier.is_available.return_value = True
    notifier.send_with_results.return_value = SimpleNamespace(status='sent', channel_results=[])
    report = run_fund_analysis(['000001', '000002'], fetcher=fetcher, include_news=False, include_interpretation=True,
                               interpreter=partial(module.interpret_funds, config=configuration(), analyzer_factory=lambda **kw: backend),
                               notifier=notifier, send_notification=True, output_dir=tmp_path)
    assert len(calls) == 2 and fetcher.fetch.call_count == 2
    assert 'F000001:' not in calls[1][0] and '拒绝原文' not in calls[1][0]
    assert '"fund_codes": ["000002"]' in calls[1][0]
    assert report.interpretation.fund_statuses['000001'].repair_status == 'not_attempted'
    assert report.interpretation.fund_statuses['000002'].repair_status == ('succeeded' if repair_valid else 'failed')
    saved = json.loads(open(report.snapshot_path).read())
    assert saved['interpretation']['fund_statuses']['000002']['repair_status'] == ('succeeded' if repair_valid else 'failed')
    assert report.notification_status == 'sent'
    assert f"AI解读 {2 if repair_valid else 1}/2只通过校验" in report.markdown
    assert ('000002 示例基金000002｜仅数据' in report.markdown) is not repair_valid
    assert report.markdown.count('横向解读未通过校验') == 1
    assert saved['interpretation']['fund_statuses']['000002']['status'] == ('ok' if repair_valid else 'model_failed')
    assert '修正一次后通过校验' not in report.markdown and '修正仍未获得可用解读' not in report.markdown
    assert '拒绝原文' not in json.dumps(saved,ensure_ascii=False)+report.markdown
    assert '第二段私密文字' not in json.dumps(saved,ensure_ascii=False)+report.markdown
    assert not report.interpretation.comparison  # Original invalid comparison never resurrected.
    notifier.send_with_results.assert_called_once_with(report.markdown, route_type='report',
        email_subject=f"场外基金已披露净值日报 - {saved['as_of']}")


@pytest.mark.parametrize('status,label', [
    ('succeeded', '修正一次后通过校验'), ('failed', '修正仍未获得可用解读'),
    ('skipped_budget', '剩余时间不足'), ('skipped_busy', '已有请求尚未结束'),
])
def test_repair_display_uses_finite_status_not_raw_reason(status, label):
    assert label in _repair_note(SimpleNamespace(repair_status=status, reason='SECRET_DO_NOT_RENDER'))
    assert 'SECRET_DO_NOT_RENDER' not in _repair_note(SimpleNamespace(repair_status=status, reason='SECRET_DO_NOT_RENDER'))


@pytest.mark.parametrize('status', ['not_attempted', 'SECRET_STATUS', {}, [], None, 1])
def test_unknown_repair_status_not_rendered(status):
    assert _repair_note(SimpleNamespace(repair_status=status)) is None


def test_service_sanitizes_repair_metadata_before_snapshot_or_log(tmp_path, caplog):
    a = analysis()
    result = module.interpret_funds([a], config=configuration(), analyzer_factory=fake_factory())
    result.fund_statuses['000001'].repair_status = {'raw': 'SECRET_STATUS'}
    result.fund_statuses['000001'].repair_diagnostics = {
        'stage': 'generation', 'code': 'provider_failed', 'exception': 'SECRET_EXCEPTION',
        'raw_response': 'SECRET_OUTPUT', 'matched_text': 'SECRET_MATCH',
    }
    report = run_fund_analysis('000001', fetcher=Mock(fetch=Mock(return_value=a.data)), include_news=False, include_interpretation=True,
                               interpreter=Mock(return_value=result), output_dir=tmp_path)
    raw = open(report.snapshot_path).read()
    saved = json.loads(raw)
    scope = saved['interpretation']['fund_statuses']['000001']
    assert scope['repair_status'] == 'not_attempted'
    assert scope['repair_diagnostics'] == {'stage':'generation','code':'provider_failed'}
    assert 'SECRET_' not in raw+report.markdown+caplog.text
