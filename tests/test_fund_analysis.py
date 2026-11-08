"""Offline fund analysis contract and safety regression tests."""
from datetime import date, datetime, timedelta, timezone

import pytest

from src.core.fund_analysis import FundData, FundProfile, HoldingsSnapshot, NavPoint, analyze_fund, parse_fund_codes


def dataset(points, qdii=False):
    return FundData(FundProfile("000001", "Example QDII" if qdii else "Example"), points,
                    datetime(2026, 1, 1, tzinfo=timezone.utc), "https://example.test/nav")


def series(start, end):
    return [NavPoint(start + timedelta(days=i), 1 + i * .001, 1 + i * .001)
            for i in range((end - start).days + 1)]


def test_identifiers_preserve_namespace_and_leading_zeros():
    assert parse_fund_codes("000001, fund:000002 000001") == ["000001", "000002"]
    assert parse_fund_codes(["fund:000001", "000001"]) == ["000001"]
    profile = FundProfile("000001", "Example", fund_type="qdii")
    assert profile.canonical_id == "fund:000001"
    assert profile.is_qdii


@pytest.mark.parametrize("value", ["", [], "AAPL", "sh600000", "00001", "0000000", "fund:AAPL", "fund:１１２３４５", [1], "000001,"])
def test_bad_identifiers_rejected(value):
    with pytest.raises(ValueError):
        parse_fund_codes(value)


@pytest.mark.parametrize("code", [1, "123", "fund:000001"])
def test_profile_rejects_bad_code(code):
    with pytest.raises(ValueError):
        FundProfile(code, "Example")


def test_calendar_month_end_leap_year_and_decimal_ratios():
    points = series(date(2023, 3, 1), date(2024, 3, 31))
    result = analyze_fund(dataset(points), date(2024, 3, 31))
    month = result.metrics["return_1m"]
    assert month.start_date == date(2024, 2, 29)
    assert month.end_date == date(2024, 3, 31)
    start = next(p for p in points if p.nav_date == month.start_date)
    assert month.value == pytest.approx(points[-1].unit_nav / start.unit_nav - 1)
    assert result.metrics["return_ytd"].start_date == date(2023, 12, 31)
    assert result.metrics["max_drawdown_12m"].value == 0


def test_drawdown_is_positive_peak_to_trough():
    points = series(date(2023, 3, 31), date(2024, 3, 31))
    points[-2] = NavPoint(points[-2].nav_date, 2, 2)
    points[-1] = NavPoint(points[-1].nav_date, 1.5, 1.5)
    assert analyze_fund(dataset(points), points[-1].nav_date).metrics["max_drawdown_12m"].value == .25


@pytest.mark.parametrize("last", [NavPoint(date(2024, 1, 2), .9, 1),
                                  NavPoint(date(2024, 1, 2), .9, .9, event="dividend"),
                                  NavPoint(date(2024, 1, 2), .9, None)])
def test_dividend_split_or_unknown_accumulated_nav_not_false_loss(last):
    result = analyze_fund(dataset([NavPoint(date(2024, 1, 1), 1, 1), last]), date(2024, 1, 2))
    assert result.metrics["return_1d"].value is None
    assert result.metrics["return_1d"].reason


def test_reported_daily_return_not_chained_or_substituted():
    points = [NavPoint(date(2024, 1, 1), 1, None, daily_return_pct=50),
              NavPoint(date(2024, 1, 2), .9, None, daily_return_pct=10)]
    assert analyze_fund(dataset(points), date(2024, 1, 2)).metrics["return_1d"].value is None


def test_changed_offset_interior_even_if_endpoints_match():
    points = series(date(2024, 1, 1), date(2024, 2, 1))
    points[15].accumulated_nav += .1
    assert analyze_fund(dataset(points), date(2024, 2, 1)).metrics["return_1m"].value is None


def test_rounding_tolerance_and_stable_nonzero_offset():
    points = [NavPoint(date(2024, 1, 1), 1, 2), NavPoint(date(2024, 1, 2), 1.1, 2.1000005)]
    assert analyze_fund(dataset(points), date(2024, 1, 2)).metrics["return_1d"].value == pytest.approx(.1)


def test_incomplete_year_and_long_gaps_unavailable():
    points = series(date(2024, 1, 1), date(2024, 3, 31))
    result = analyze_fund(dataset(points), date(2024, 3, 31))
    assert result.metrics["return_12m"].value is None
    assert result.metrics["max_drawdown_12m"].value is None
    points = [NavPoint(date(2024, 1, 1), 1, 1), NavPoint(date(2024, 2, 1), 1.1, 1.1)]
    result = analyze_fund(dataset(points), date(2024, 2, 1))
    assert "间隔超过" in result.metrics["return_1m"].reason
    assert result.metrics["return_1d"].value is None


def test_anchor_tolerance_no_interpolation():
    points = series(date(2024, 1, 1), date(2024, 3, 31))
    points = [p for p in points if not date(2024, 2, 21) <= p.nav_date <= date(2024, 2, 29)]
    result = analyze_fund(dataset(points), date(2024, 3, 31))
    assert "日历锚点" in result.metrics["return_1m"].reason


def test_ytd_holiday_tolerance_and_actual_start():
    points = series(date(2023, 12, 20), date(2024, 1, 10))
    points = [p for p in points if not date(2023, 12, 21) <= p.nav_date <= date(2023, 12, 31)]
    metric = analyze_fund(dataset(points), date(2024, 1, 10)).metrics["return_ytd"]
    assert metric.start_date == date(2023, 12, 20)
    assert metric.value is not None


def test_sort_duplicate_and_exclude_future_without_mutation():
    a = NavPoint(date(2024, 1, 1), 1, 1)
    b = NavPoint(date(2024, 1, 2), 1.1, 1.1)
    points = [NavPoint(date(2025, 1, 1), 100, 100), b, a, a]
    result = analyze_fund(dataset(points), date(2024, 1, 2))
    assert result.metrics["return_1d"].value == pytest.approx(.1)
    assert len(points) == 4
    assert result.data.navs == [a, b]
    assert any("晚于分析日期" in w for w in result.warnings)


def test_conflicting_duplicates_fail_closed():
    with pytest.raises(ValueError, match="Conflicting"):
        analyze_fund(dataset([NavPoint(date(2024, 1, 1), 1, 1), NavPoint(date(2024, 1, 1), 2, 2)]))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, True])
@pytest.mark.parametrize("field", ["unit_nav", "accumulated_nav"])
def test_invalid_nav_rejected(value, field):
    point = NavPoint(date(2024, 1, 1), 1, 1)
    setattr(point, field, value)
    with pytest.raises(ValueError, match="Invalid"):
        analyze_fund(dataset([point]))


def test_stale_nav_age_qdii_and_no_fixed_delay():
    result = analyze_fund(dataset(series(date(2024, 1, 1), date(2024, 1, 2)), qdii=True), date(2024, 1, 20))
    assert any("18个自然日" in w for w in result.warnings)
    assert any("时效提醒" in w for w in result.warnings)
    assert any("QDII" in w for w in result.warnings)
    assert result.metrics["return_1d"].end_date == date(2024, 1, 2)


def test_empty_history_and_date_contract():
    with pytest.raises(ValueError, match="没有可用净值"):
        analyze_fund(dataset([]))
    data = dataset([])
    data.fetched_at = datetime(2024, 1, 1)
    with pytest.raises(ValueError, match="timezone-aware"):
        analyze_fund(data)
    with pytest.raises(ValueError, match="as_of"):
        analyze_fund(dataset([]), datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="NAV dates"):
        analyze_fund(dataset([NavPoint("2024-01-01", 1, 1)]))


def test_one_nav_does_not_invent_daily_return():
    result = analyze_fund(dataset([NavPoint(date(2024, 1, 1), 1, 1)]), date(2024, 1, 1))
    assert result.metrics["return_1d"].value is None


def test_leap_day_year_anchor_and_boundary_tolerance():
    points = series(date(2023, 2, 28), date(2024, 2, 29))
    result = analyze_fund(dataset(points), date(2024, 2, 29))
    assert result.metrics["return_12m"].start_date == date(2023, 2, 28)
    assert result.metrics["max_drawdown_12m"].value == 0
    points = series(date(2024, 1, 1), date(2024, 3, 31))
    points = [p for p in points if not date(2024, 2, 23) <= p.nav_date <= date(2024, 2, 29)]
    result = analyze_fund(dataset(points), date(2024, 3, 31))
    assert result.metrics["return_1m"].start_date == date(2024, 2, 22)
    assert result.metrics["return_1m"].value is not None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "bad"])
def test_invalid_reported_daily_return_rejected(value):
    with pytest.raises(ValueError, match="daily return"):
        analyze_fund(dataset([NavPoint(date(2024, 1, 1), 1, 1, daily_return_pct=value)]))


def test_money_market_rejected():
    data = dataset([NavPoint(date(2024, 1, 1), 1, 1)])
    data.profile.fund_type = "货币型"
    with pytest.raises(ValueError, match="货币基金"):
        analyze_fund(data)


@pytest.mark.parametrize("published", [None, date(2024, 2, 1)])
def test_historical_holdings_without_known_publication_excluded(published):
    data = dataset([NavPoint(date(2024, 1, 1), 1, 1)])
    data.holdings = HoldingsSnapshot(date(2023, 12, 31), [], "https://example.test", published)
    result = analyze_fund(data, date(2024, 1, 1))
    assert result.data.holdings is None
    assert data.holdings is not None


def test_historical_holdings_known_before_as_of_retained():
    data = dataset([NavPoint(date(2024, 1, 1), 1, 1)])
    data.holdings = HoldingsSnapshot(date(2023, 9, 30), [], "https://example.test", date(2023, 10, 25))
    assert analyze_fund(data, date(2024, 1, 1)).data.holdings == data.holdings
