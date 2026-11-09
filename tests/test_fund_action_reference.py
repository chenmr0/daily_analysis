"""Offline tests of evidence boundaries, not tests of a trading strategy."""
import copy
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from functools import partial
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.core.fund_analysis import FundData, FundProfile, NavPoint, analyze_fund
from src.core.fund_action_reference import build_action_reference, unavailable_reference
from src.services.fund_analysis_service import render_fund_report, run_fund_analysis
from src.services.fund_interpretation import interpret_funds
from tests.test_fund_interpretation import configuration, fake_factory, valid_output


END = date(2026, 11, 6)
MONDAY = date(2026, 11, 9)


def sample(code="001672", *, qdii=False, count=371, end=END):
    points = [NavPoint(end - timedelta(days=count - i - 1), 1 + i * .001, 1 + i * .001)
              for i in range(count)]
    return FundData(FundProfile(code, "示例QDII联接基金" if qdii else "示例基金", "股票型"),
                    points, datetime(2026, 11, 9, 6, 45, tzinfo=timezone.utc), "https://example.test/nav")


def analyzed(**kwargs):
    return analyze_fund(sample(**kwargs), as_of=MONDAY)


def action_contract(reference):
    return (reference.status, reference.action, reference.headline, reference.not_held, reference.held,
            reference.reason_codes, reference.next_checks)


@pytest.mark.parametrize("first", [-.3, -1e-10, 0, 1e-10, .3])
@pytest.mark.parametrize("third", [-.2, 0, .2])
def test_direction_is_descriptive_never_a_trade_mapping(first, third):
    value = analyzed()
    baseline = build_action_reference(value, as_of=MONDAY)
    value.metrics["return_1m"].value = first
    value.metrics["return_3m"].value = third
    reference = build_action_reference(value, as_of=MONDAY)
    assert action_contract(reference) == action_contract(baseline)
    assert reference.status == "conditional" and reference.action == "observe"
    for item, number in zip(reference.evidence[:2], (first, third)):
        assert item.value == number
        assert item.direction == ("positive" if number > 0 else "negative" if number < 0 else "flat")
    assert reference.holding_context == "unknown"
    assert "暂不依据本报告新增买入" in reference.not_held
    assert "是否卖出尚不能判断" in reference.held
    assert "不表示继续持有安全" in reference.held
    assert "未核实" in reference.next_checks[0]
    assert not any(x in asdict(reference) for x in ("position", "target_price", "score", "expected_return"))


@pytest.mark.parametrize("number,label", [(1e-10, "正值"), (-1e-10, "负值"), (0, "0.00%")])
def test_small_unrounded_directions_do_not_render_as_zero(number, label):
    value = analyzed()
    value.metrics["return_1m"].value = number
    report = render_fund_report([value], {}, {}, value.data.fetched_at, as_of=MONDAY)
    section = report.split("## 横向数据对照")[0]
    assert label in section
    if number:
        assert "绝对值不足0.01%" in section
        assert "区间单位净值持平" not in section


@pytest.mark.parametrize("key", ["return_1m", "return_3m", "max_drawdown_12m"])
@pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), -float("inf"), "0.2", 1e308])
def test_invalid_numeric_evidence_never_reaches_json_or_direction(key, bad):
    value = analyzed()
    value.metrics[key].value = bad
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.status == "unavailable"
    assert "metric_invalid" in reference.reason_codes
    invalid = next(item for item in reference.evidence if item.metric == key)
    assert invalid.value is None and invalid.direction is None
    json.dumps(asdict(reference), default=str, allow_nan=False)


@pytest.mark.parametrize("key,bad", [("return_1m", -1), ("return_3m", -2),
                                    ("max_drawdown_12m", -0.1), ("max_drawdown_12m", 1)])
def test_out_of_domain_metrics_are_not_actions(key, bad):
    value = analyzed()
    value.metrics[key].value = bad
    assert build_action_reference(value, as_of=MONDAY).status == "unavailable"


@pytest.mark.parametrize("key", ["return_1m", "return_3m"])
@pytest.mark.parametrize("remove", [False, True])
def test_required_missing_evidence_is_not_neutral_observation(key, remove):
    value = analyzed()
    if remove:
        del value.metrics[key]
    else:
        value.metrics[key].value = None
        value.metrics[key].reason = "历史不足以覆盖完整目标区间"
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.status == "unavailable" and reference.action == "undetermined"
    assert "metric_unavailable" in reference.reason_codes
    assert "无法判断" in reference.headline
    assert "继续持有安全" in reference.held


def test_missing_full_year_is_visible_without_inventing_a_year_metric():
    value = analyzed(count=150)
    assert value.metrics["max_drawdown_12m"].value is None
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.status == "conditional"
    assert reference.evidence[-1].value is None and reference.evidence[-1].reason


@pytest.mark.parametrize("change", ["dividend", "split", "missing_accumulated", "changed_offset", "gap", "anchor_gap"])
def test_existing_metric_gate_cannot_be_bypassed(change):
    data = sample()
    point = data.navs[-10]
    if change in {"dividend", "split"}:
        point.event = change
    elif change == "missing_accumulated":
        point.accumulated_nav = None
    elif change == "changed_offset":
        point.accumulated_nav += .1
    elif change == "gap":
        data.navs = data.navs[:-25] + data.navs[-2:]
    else:
        data.navs = [p for p in data.navs if not date(2026, 9, 28) <= p.nav_date <= date(2026, 10, 6)]
    value = analyze_fund(data, as_of=MONDAY)
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.status == "unavailable"
    assert "metric_unavailable" in reference.reason_codes
    assert any(item.value is None and item.reason for item in reference.evidence[:2])
    assert reference.action != "observe"


@pytest.mark.parametrize("field,new", [("start_date", date(2027, 1, 1)), ("end_date", date(2027, 1, 1)),
                                      ("start_date", "2026-10-06"), ("end_date", None),
                                      ("reason", "分红事件未完成调整")])
def test_inconsistent_metric_dates_or_gate_are_rejected(field, new):
    value = analyzed()
    setattr(value.metrics["return_1m"], field, new)
    assert build_action_reference(value, as_of=MONDAY).status == "unavailable"


@pytest.mark.parametrize("key", ["return_1m", "return_3m", "max_drawdown_12m"])
@pytest.mark.parametrize("days", [1, 450])
def test_mislabeled_calendar_window_is_unavailable(key, days):
    value = analyzed()
    value.metrics[key].start_date = END - timedelta(days=days)
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.status == "unavailable" and "metric_invalid" in reference.reason_codes


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, False, 0, -1])
def test_invalid_latest_nav_is_unavailable(bad):
    value = analyzed()
    value.data.navs[-1].unit_nav = bad
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.status == "unavailable" and "nav_invalid" in reference.reason_codes


def test_future_data_is_filtered_by_core_and_cannot_be_reintroduced():
    data = sample()
    data.navs.append(NavPoint(date(2027, 1, 1), 100, 100))
    value = analyze_fund(data, as_of=MONDAY)
    assert value.data.navs[-1].nav_date == END
    assert build_action_reference(value, as_of=MONDAY).nav_date == END
    value.data.navs[-1].nav_date = date(2027, 1, 1)
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.status == "unavailable" and reference.nav_date is None


def test_no_nav_and_invalid_as_of_fail_closed():
    value = analyzed()
    value.data.navs = []
    assert build_action_reference(value, as_of=MONDAY).status == "unavailable"
    with pytest.raises(ValueError, match="as_of"):
        build_action_reference(value, as_of=datetime(2026, 11, 9))


@pytest.mark.parametrize("qdii,end", [(False, END), (True, date(2026, 11, 5))])
def test_weekends_holidays_and_qdii_age_never_change_action(qdii, end):
    value = analyzed(qdii=qdii, end=end)
    initial = build_action_reference(value, as_of=MONDAY)
    later = build_action_reference(value, as_of=date(2026, 11, 20))
    assert initial.nav_date == end and later.nav_date == end
    assert action_contract(initial) == action_contract(later)
    assert "自然日" in initial.limitations[-1]
    assert value.data.profile.currency is None
    assert "人民币" not in json.dumps(asdict(initial), ensure_ascii=False, default=str)


@pytest.mark.parametrize("drawdown", [0, .1, .47, .63, .99])
def test_historical_drawdown_changes_evidence_not_action(drawdown):
    value = analyzed()
    initial = build_action_reference(value, as_of=MONDAY)
    value.metrics["max_drawdown_12m"].value = drawdown
    reference = build_action_reference(value, as_of=MONDAY)
    assert action_contract(initial) == action_contract(reference)
    assert reference.evidence[-1].value == drawdown
    assert "不是当前回撤、个人亏损或止损阈值" in reference.limitations[2]


def test_reference_is_pure_and_contains_no_invented_volatility_or_personal_inputs():
    value = analyzed()
    original = copy.deepcopy(value)
    reference = build_action_reference(value, as_of=MONDAY)
    assert value == original
    assert reference == build_action_reference(value, as_of=MONDAY)
    assert len(reference.evidence) == 3
    assert "volatility" not in json.dumps(asdict(reference), default=str)
    assert unavailable_reference("001672", MONDAY, "SECRET_EXCEPTION").reason_codes[0] == "component_failed"


def test_reference_failure_is_isolated_and_does_not_change_notification_count(tmp_path, caplog):
    fetcher, notifier = Mock(), Mock()
    fetcher.fetch.side_effect = [sample(), sample("024480"), ValueError("invalid NAV")]
    notifier.is_available.return_value = True
    notifier.send_with_results.return_value = SimpleNamespace(status="sent", channel_results=[])
    def build(value, **kwargs):
        if value.data.profile.code == "024480":
            raise RuntimeError("SECRET_EXCEPTION")
        return build_action_reference(value, **kwargs)
    with patch("src.services.fund_analysis_service.build_action_reference", side_effect=build):
        report = run_fund_analysis(["001672", "024480", "020356"], fetcher=fetcher, include_news=False,
                                   send_notification=True, notifier=notifier, as_of=MONDAY, output_dir=tmp_path)
    assert len(report.analyses) == 2 and set(report.failed_codes) == {"020356"}
    assert report.action_references["001672"].status == "conditional"
    assert report.action_references["024480"].reason_codes[0] == "component_failed"
    assert report.action_references["020356"].reason_codes[0] == "nav_unavailable"
    assert "SECRET_EXCEPTION" not in report.markdown + caplog.text
    notifier.send_with_results.assert_called_once()
    kwargs = notifier.send_with_results.call_args.kwargs
    assert kwargs["route_type"] == "report" and "email_stock_codes" not in kwargs
    saved = json.loads(open(report.snapshot_path).read())
    assert saved["schema_version"] == 4 and len(saved["action_references"]) == 3
    assert saved["notification"]["status"] == "sent"
    assert saved["interpretation"]["status"] == "disabled"
    assert saved["action_references"] == json.loads(json.dumps(
        {key: asdict(item) for key, item in report.action_references.items()}, default=str))
    assert "操作参考" not in report.markdown and "观望" not in report.markdown
    assert all(code in report.markdown for code in ("001672", "024480", "020356"))
    assert "数据获取或校验失败" in report.markdown



@pytest.mark.parametrize("bad", [None, object(), "buy", replace(unavailable_reference("024480", MONDAY), fund_code="024480")])
def test_invalid_component_return_does_not_crash_report(tmp_path, bad):
    fetcher = Mock()
    fetcher.fetch.return_value = sample()
    with patch("src.services.fund_analysis_service.build_action_reference", return_value=bad):
        report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, as_of=MONDAY, output_dir=tmp_path)
    assert len(report.analyses) == 1
    assert report.action_references["001672"].reason_codes[0] == "component_failed"


@pytest.mark.parametrize("mode", ["disabled", "accepted", "unsafe", "unknown_field", "error", "partial", "comparison_rejected"])
def test_model_status_or_malicious_output_never_controls_program_reference(tmp_path, mode):
    codes = ("001672", "024480")
    output = valid_output(codes)
    if mode in {"unsafe", "partial"}:
        output["funds"][0]["conclusion"][0]["text"] = "应当立即买入并加仓，后续仍需注意风险。"
        if mode == "unsafe":
            output["funds"][1]["conclusion"][0]["text"] = "应当立即卖出并减仓，后续仍需注意风险。"
    elif mode == "unknown_field":
        output["action_references"] = {"001672": {"action": "buy"}}
    elif mode == "comparison_rejected":
        output["comparison"][0]["text"] = "本基金优于其他基金，应继续注意风险。"
    calls = []
    factory = fake_factory(output, error=RuntimeError("SECRET_MODEL") if mode == "error" else None, calls=calls)
    interpreter = partial(interpret_funds, config=configuration(), analyzer_factory=factory)
    fetcher = Mock()
    fetcher.fetch.side_effect = [sample(code) for code in codes]
    # No historical cutoff disables the synthetic adapter accidentally.
    report = run_fund_analysis(codes, fetcher=fetcher, include_news=False, include_interpretation=mode != "disabled",
                               interpreter=interpreter, output_dir=tmp_path)
    for analysis in report.analyses:
        reference = report.action_references[analysis.data.profile.code]
        assert reference == build_action_reference(analysis, as_of=reference.as_of)
        assert reference.action == "observe"
    if mode in {"unsafe", "unknown_field", "error"}:
        assert report.interpretation_status == "model_failed"
    elif mode in {"partial", "comparison_rejected"}:
        assert report.interpretation_status == "partial"
    elif mode == "accepted":
        assert report.interpretation_status == "ok"
    else:
        assert report.interpretation_status == "disabled" and calls == []
    for prompt, _, _ in calls:
        assert "action_references" not in prompt and "evidence_boundary_v1" not in prompt
        assert "是否卖出尚不能判断" not in prompt
    assert "应当立即买入并加仓" not in report.markdown
    assert "SECRET_MODEL" not in report.markdown


def test_reference_markup_and_no_personal_input_contract(tmp_path):
    fetcher = Mock()
    data = sample()
    data.profile.name = "<img src=x onerror=alert(1)>"
    fetcher.fetch.return_value = data
    report = run_fund_analysis("001672", fetcher=fetcher, include_news=False, as_of=MONDAY, output_dir=tmp_path)
    card = report.markdown.split("## 横向数据对照")[0]
    assert "<img" not in card and "&lt;img" in card
    assert "若未持有" not in card and "若已持有" not in card
    assert "非当前回撤" in card
    assert "买入候选" not in card and "卖出评估" not in card
    assert "最佳" not in card and "波动率" not in card



def test_direct_renderer_and_service_share_cards_and_cutoff_for_failed_and_valid_codes(tmp_path):
    fetcher = Mock()
    fetcher.fetch.side_effect = [sample(), ValueError("invalid NAV")]
    report = run_fund_analysis(["001672", "024480"], fetcher=fetcher, include_news=False,
                               as_of=MONDAY, output_dir=tmp_path)
    direct = render_fund_report(report.analyses, report.failed_codes, {}, datetime.now(timezone.utc), as_of=MONDAY)
    assert direct == report.markdown
    assert "fund:024480" in direct and "invalid NAV" in direct
    assert "2026-11-09" in direct



def test_extreme_history_does_not_assert_current_or_personal_loss():
    data = sample()
    # Historical 75% peak-to-trough drop followed by complete recovery.
    data.navs[-90] = NavPoint(data.navs[-90].nav_date, 4, 4)
    data.navs[-89] = NavPoint(data.navs[-89].nav_date, 1, 1)
    data.navs[-1] = NavPoint(END, 4, 4)
    value = analyze_fund(data, as_of=MONDAY)
    reference = build_action_reference(value, as_of=MONDAY)
    assert reference.action == "observe"
    assert reference.evidence[-1].value == .75
    report = render_fund_report([value], {}, {}, data.fetched_at, as_of=MONDAY)
    card = report.split("## 横向数据对照", 1)[0]
    assert "75.00%" in card and "历史峰谷，非当前回撤" in card
    assert "你的亏损" not in card and "当前回撤75" not in card
