"""Bounded corrective generation uses only rejected scopes and safe feedback."""
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.llm.generation_backend import GenerationResult
from src.services import fund_interpretation as module
from tests.test_fund_interpretation import analysis, configuration, valid_output


def sequence_factory(outputs, calls):
    def factory(*, config):
        def generate(prompt, **options):
            calls.append((prompt, options, config))
            value = outputs[len(calls) - 1]
            if isinstance(value, Exception):
                raise value
            if callable(value):
                value = value()
            if isinstance(value, GenerationResult):
                return value
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            return GenerationResult(text, 'actual-cloud-model', 'anthropic', 'litellm', {})
        return SimpleNamespace(get_generation_backend_identity=lambda: ('litellm', config.litellm_model),
                               is_available=lambda: True, generate_text_with_metadata=generate)
    return factory


def run_pair(initial, repair, codes=('000001', '000002'), **kwargs):
    calls = []
    result = module.interpret_funds([analysis(code) for code in codes], config=configuration(),
                                   analyzer_factory=sequence_factory([initial, repair], calls), **kwargs)
    return result, calls


def broken(codes=('000001', '000002')):
    output = valid_output(codes)
    output['funds'][-1]['conclusion'][0]['evidence_ids'] = ['SECRET_UNKNOWN_ID']
    return output


def test_repairs_only_failed_fund_without_resurrecting_comparison():
    initial = broken()
    result, calls = run_pair(initial, valid_output(('000002',)))
    assert len(calls) == 2
    assert set(result.funds) == {'000001', '000002'}
    assert result.fund_statuses['000001'].repair_status == 'not_attempted'
    repaired = result.fund_statuses['000002']
    assert repaired.status == 'ok' and repaired.repair_status == 'succeeded'
    assert repaired.diagnostics['code'] == 'citation_unknown'
    assert result.comparison == [] and result.comparison_status.status == 'model_failed'
    prompt, options, config = calls[1]
    assert 'F000001:' not in prompt and 'SECRET_UNKNOWN_ID' not in prompt
    assert '"fund_codes": ["000002"]' in prompt
    assert 'citation_unknown' in prompt and 'comparison必须为空数组' in prompt
    assert options['max_tokens'] == 1200 and 10 <= options['timeout_seconds'] <= 30
    assert options['allow_retries'] is False and config.litellm_fallback_models == []


@pytest.mark.parametrize('repair', ['not json', RuntimeError('SECRET_PROVIDER_EXCEPTION'),
    GenerationResult('{}', '', 'anthropic', 'litellm', {}), broken(('000002',))])
def test_failed_repair_preserves_initial_success_and_original_diagnostics(repair):
    result, calls = run_pair(broken(), repair)
    assert len(calls) == 2 and set(result.funds) == {'000001'}
    scope = result.fund_statuses['000002']
    assert scope.repair_status == 'failed' and scope.diagnostics['code'] == 'citation_unknown'
    assert scope.repair_diagnostics
    assert 'SECRET' not in repr(scope)


def test_multiple_failed_funds_share_one_call_and_require_empty_comparison():
    initial = broken()
    initial['funds'][0]['risks'] = []
    repair = valid_output(('000001', '000002'))
    repair['comparison'] = []
    result, calls = run_pair(initial, repair)
    assert len(calls) == 2 and len(result.funds) == 2
    assert calls[1][1]['max_tokens'] == 2400
    result, calls = run_pair(initial, valid_output(('000001', '000002')))
    assert len(calls) == 2 and not result.funds
    assert all(s.repair_diagnostics['code'] == 'single_fund_comparison' for s in result.fund_statuses.values())


@pytest.mark.parametrize('initial', ['not json', {'funds': [], 'comparison': []},
    RuntimeError('provider failure')])
def test_global_or_provider_failures_never_retry(initial):
    result, calls = run_pair(initial, valid_output())
    assert len(calls) == 1
    assert all(s.repair_status == 'not_attempted' for s in result.fund_statuses.values())


def test_comparison_only_failure_never_retries():
    initial = valid_output(('000001', '000002'))
    initial['comparison'][0]['evidence_ids'] = ['BAD']
    result, calls = run_pair(initial, {})
    assert len(calls) == 1 and len(result.funds) == 2


def test_insufficient_total_budget_skips_repair():
    result, calls = run_pair(broken(), {}, timeout_seconds=10)
    assert len(calls) == 1
    assert result.fund_statuses['000002'].repair_status == 'skipped_budget'


def test_first_timeout_never_retries_and_keeps_inflight_slot():
    release = threading.Event()
    entered = threading.Event()
    done = threading.Event()
    def slow():
        entered.set()
        release.wait(2)
        done.set()
        return broken()
    try:
        result, calls = run_pair(slow, {}, timeout_seconds=.01)
        assert entered.is_set() and len(calls) == 1
        assert result.diagnostics['code'] == 'request_timeout'
        busy, other_calls = run_pair(valid_output(), {})
        assert busy.diagnostics['code'] == 'request_busy' and not other_calls
    finally:
        release.set()
        assert done.wait(2)
        # Synchronize with finally releasing the slot, not just provider return.
        assert module._CALL_SLOT.acquire(timeout=2)
        module._CALL_SLOT.release()


def test_original_accepted_comparison_and_insight_objects_remain_unchanged(monkeypatch):
    original = module._parse_response
    captured = []
    def parse(*args, **kwargs):
        value = original(*args, **kwargs)
        if not kwargs.get('comparison_disabled'):
            captured.append(value)
        return value
    monkeypatch.setattr(module, '_parse_response', parse)
    initial = broken()
    initial['comparison'][0]['evidence_ids'] = ['F000001:profile']
    result, _ = run_pair(initial, valid_output(('000002',)))
    assert result.funds['000001'] is captured[0][0]['000001']
    assert result.comparison is captured[0][1] and result.comparison
    assert result.comparison_status is captured[0][3]


def test_repair_timeout_preserves_success_and_never_makes_third_call(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    def late():
        now[0] += 31.0
        return valid_output(('000002',))
    result, calls = run_pair(broken(), late)
    assert len(calls) == 2 and set(result.funds) == {'000001'}
    assert result.fund_statuses['000002'].repair_diagnostics['code'] == 'request_timeout'


def test_repair_global_identity_rejects_batch_without_touching_success():
    repair = valid_output(('000002',))
    repair['funds'][0]['fund_code'] = '999999'
    result, calls = run_pair(broken(), repair)
    assert len(calls) == 2 and set(result.funds) == {'000001'}
    assert result.fund_statuses['000002'].repair_diagnostics['code'] == 'fund_identity'


def test_remaining_budget_bounds_repair_sdk_timeout(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    def initial():
        now[0] += 96.0
        return broken()
    result, calls = run_pair(initial, valid_output(('000002',)))
    assert len(calls) == 2 and calls[1][1]['timeout_seconds'] == 19.0
    assert result.fund_statuses['000002'].repair_status == 'succeeded'


def test_routed_repair_identity_is_reported_without_changing_initial_identity():
    response = GenerationResult(json.dumps(valid_output(('000002',)), ensure_ascii=False),
                                'routed-repair-model', 'routed-provider', 'litellm', {})
    result, calls = run_pair(broken(), response)
    assert len(calls) == 2 and result.fund_statuses['000002'].repair_status == 'succeeded'
    assert result.model == 'actual-cloud-model' and result.provider == 'anthropic'
    assert result.repair_model == 'routed-repair-model' and result.repair_provider == 'routed-provider'


def test_partial_repair_keeps_only_newly_valid_funds_without_third_call():
    initial = broken()
    initial['funds'][0]['risks'] = []
    repair = broken()
    repair['comparison'] = []
    result, calls = run_pair(initial, repair)
    assert len(calls) == 2 and set(result.funds) == {'000001'}
    assert result.fund_statuses['000001'].repair_status == 'succeeded'
    assert result.fund_statuses['000002'].repair_status == 'failed'


def test_unexpected_repair_validation_fault_preserves_original_success(monkeypatch):
    original = module._parse_response
    def parse(*args, **kwargs):
        if kwargs.get('comparison_disabled'):
            raise RuntimeError('SECRET_COMPONENT')
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_parse_response', parse)
    result, calls = run_pair(broken(), valid_output(('000002',)))
    assert len(calls) == 2 and set(result.funds) == {'000001'}
    assert result.fund_statuses['000002'].repair_diagnostics == {
        'stage': 'component', 'code': 'validation_internal'}


def test_repair_budget_below_ten_seconds_never_starts_call(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    def initial():
        now[0] += 106.0
        return broken()
    result, calls = run_pair(initial, {})
    assert len(calls) == 1
    assert result.fund_statuses['000002'].repair_status == 'skipped_budget'


def test_repair_preserves_full_original_fund_name_guard_without_prompt_leak():
    good, bad = analysis('000001'), analysis('000002')
    good.data.profile = replace(good.data.profile, name='晨光成长基金')
    repair = valid_output(('000002',))
    repair['funds'][0]['conclusion'][0]['text'] = (
        '晨光成长基金的已披露区间表现提供历史参考，仍需结合净值口径理解。')
    calls = []
    result = module.interpret_funds([good, bad], config=configuration(),
        analyzer_factory=sequence_factory([broken(), repair], calls))
    assert len(calls) == 2 and set(result.funds) == {'000001'}
    scope = result.fund_statuses['000002']
    assert scope.repair_status == 'failed'
    assert scope.repair_diagnostics['code'] == 'other_fund_name'
    assert '晨光成长基金' not in calls[1][0] and 'F000001:' not in calls[1][0]


def test_initial_exact_zero_sdk_budget_never_calls_provider(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    calls = []
    delegate = sequence_factory([valid_output()], calls)
    def factory(*, config):
        # Initial deadline is 1120, with 30 seconds reserved for handling.
        now[0] += 90.0
        return delegate(config=config)
    result = module.interpret_funds([analysis()], config=configuration(), analyzer_factory=factory)
    assert not calls
    assert result.diagnostics['code'] == 'request_timeout'
