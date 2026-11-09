"""Conservative, deterministic off-exchange fund NAV analysis.

Returns are unit-NAV changes only, never reinvested total returns. A window is
usable only when every point has accumulated NAV, its accumulated-minus-unit
NAV offset is stable, and no distribution/split event is reported. This is
supporting evidence, not a guarantee that the upstream event history is complete.
"""
from __future__ import annotations

import calendar
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Iterable, Optional


@dataclass
class FundProfile:
    code: str
    name: str
    fund_type: str = ""
    currency: Optional[str] = None
    manager: Optional[str] = None
    inception_date: Optional[date] = None
    source_url: str = ""

    def __post_init__(self):
        if not isinstance(self.code, str) or not re.fullmatch(r"[0-9]{6}", self.code):
            raise ValueError("Fund code must be a six-digit string, preserving leading zeros")

    @property
    def canonical_id(self) -> str:
        return f"fund:{self.code}"

    @property
    def is_qdii(self) -> bool:
        return "QDII" in (self.fund_type + " " + self.name).upper()


@dataclass
class NavPoint:
    nav_date: date
    unit_nav: float
    accumulated_nav: Optional[float] = None
    daily_return_pct: Optional[float] = None
    event: str = ""


@dataclass
class Holding:
    code: str
    name: str
    weight_pct: float


@dataclass
class HoldingsSnapshot:
    report_period_end: date
    holdings: list[Holding]
    source_url: str
    published_at: Optional[date] = None


@dataclass
class FundRelatedAsset:
    """Source-bound relationship, not an assertion about current exposure.

    source_date is the disclosed period end, never retrieval time. verified
    means identity-bound provider evidence, not independent source confirmation.
    """
    category: str
    name: str
    source_url: str
    source_date: Optional[date] = None
    code: Optional[str] = None
    weight_pct: Optional[float] = None
    published_at: Optional[date] = None
    identity_url: str = ""
    basis: str = ""
    verified: bool = False


@dataclass
class FundData:
    profile: FundProfile
    navs: list[NavPoint]
    fetched_at: datetime
    source_url: str
    holdings: Optional[HoldingsSnapshot] = None
    warnings: list[str] = field(default_factory=list)
    related_assets: list[FundRelatedAsset] = field(default_factory=list)


@dataclass
class Metric:
    value: Optional[float]
    reason: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


@dataclass
class FundAnalysis:
    data: FundData
    metrics: dict[str, Metric]
    warnings: list[str]


def parse_fund_codes(value: str | Iterable[str]) -> list[str]:
    """Parse explicit fund inputs; identity must still be checked by the provider.

    This deliberately does not call or modify the stock-code parser. Six-digit
    identifiers are ambiguous across markets, so syntax alone proves no identity.
    """
    tokens = re.split(r"[,\s]+", value.strip()) if isinstance(value, str) else list(value)
    if not tokens or (isinstance(value, str) and not value.strip()):
        raise ValueError("基金代码不能为空")
    result = []
    for token in tokens:
        if not isinstance(token, str):
            raise ValueError("Fund codes must be strings; numeric inputs lose leading zeros")
        match = re.fullmatch(r"(?:fund:)?([0-9]{6})", token.strip())
        if not match:
            raise ValueError(f"Invalid fund identifier: {token!r}; use six digits or fund:NNNNNN")
        code = match.group(1)
        if code not in result:
            result.append(code)
    return result


def _months_before(day: date, months: int) -> date:
    year, month0 = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _positive_finite(value: float) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _window_reason(points: list[NavPoint]) -> Optional[str]:
    if any(p.event and p.event.strip() for p in points):
        return "存在分红/拆分事件，缺少经调整的总回报历史"
    if any(p.accumulated_nav is None for p in points):
        return "缺少累计净值，无法核实区间无分红/拆分事件"
    offsets = [p.accumulated_nav - p.unit_nav for p in points]
    if max(offsets) - min(offsets) > 1e-6 + 1e-12:
        return "累计净值与单位净值之差发生变化，无法完成分红/拆分调整"
    if any((b.nav_date - a.nav_date).days > 14 for a, b in zip(points, points[1:])):
        return "历史缺失：净值间隔超过14个自然日（可能包含假期），不推算交易日"
    return None


def analyze_fund(data: FundData, as_of: Optional[date] = None) -> FundAnalysis:
    """Return decimal-ratio metrics anchored on the latest nonfuture NAV.

    The 1d label means change from the previous available NAV, not a fabricated
    trading session. All periods expose their actual boundary dates. Drawdown is
    a positive peak-to-trough fraction. Missing/unsafe windows have value=None.
    """
    if not isinstance(data.fetched_at, datetime) or data.fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be a timezone-aware datetime")
    as_of = as_of or datetime.now(timezone.utc).date()
    if type(as_of) is not date:
        raise ValueError("as_of must be a date")
    warnings = list(data.warnings)
    if "货币" in data.profile.fund_type:
        raise ValueError("暂不支持货币基金的万份收益/七日年化口径")
    by_date = {}
    future_count = 0
    for point in data.navs:
        if type(point.nav_date) is not date:
            raise ValueError("NAV dates must be datetime.date values")
        if point.nav_date > as_of:
            future_count += 1
            continue
        if not _positive_finite(point.unit_nav):
            raise ValueError(f"Invalid unit NAV on {point.nav_date}")
        if point.accumulated_nav is not None and not _positive_finite(point.accumulated_nav):
            raise ValueError(f"Invalid accumulated NAV on {point.nav_date}")
        if point.daily_return_pct is not None and (
            not isinstance(point.daily_return_pct, (int, float))
            or isinstance(point.daily_return_pct, bool)
            or not math.isfinite(point.daily_return_pct)
        ):
            raise ValueError(f"Invalid reported daily return on {point.nav_date}")
        if point.nav_date in by_date and by_date[point.nav_date] != point:
            raise ValueError(f"Conflicting duplicate NAV date: {point.nav_date}")
        by_date[point.nav_date] = point
    points = sorted(by_date.values(), key=lambda point: point.nav_date)
    holdings = data.holdings
    if holdings is not None:
        if (holdings.report_period_end > as_of
                or (holdings.published_at is not None and holdings.published_at > as_of)
                or (as_of < data.fetched_at.date() and holdings.published_at is None)):
            holdings = None
            warnings.append("历史时点不可确认持仓已披露，已省略持仓快照。")
    data = replace(data, navs=points, holdings=holdings)
    if future_count:
        warnings.append(f"已排除{future_count}条晚于分析日期{as_of}的净值记录")
    warnings.append("口径：仅在有证据支持区间无分红/拆分时计算单位净值收益，非红利再投资总回报。"
                    "净值差额容差为0.000001；数据源舍入及事件记录完整性可能影响核实。")
    if data.profile.is_qdii:
        warnings.append("QDII净值可能因海外估值及假期延迟披露，不假设固定T+N时效。")
    if not points:
        raise ValueError("分析日期当日及之前没有可用净值")
    end = points[-1].nav_date
    age = (as_of - end).days
    warnings.append(f"最新净值日期{end}；截至{as_of}已过去{age}个自然日。")
    if age > 7:
        warnings.append("净值时效提醒：最新净值已超过7个自然日，收益计算截至该净值日期。")

    def calculate(anchor: Optional[date], tolerance: int = 7, drawdown: bool = False) -> Metric:
        if anchor is None:
            start_index = len(points) - 2
        else:
            eligible = [i for i, point in enumerate(points) if point.nav_date <= anchor]
            start_index = eligible[-1] if eligible else -1
        if start_index < 0:
            return Metric(None, "历史不足以覆盖完整目标区间", end_date=end)
        start = points[start_index].nav_date
        if anchor is not None and (anchor - start).days > tolerance:
            return Metric(None, "日历锚点附近缺少净值，不进行插值", start, end)
        window = points[start_index:]
        reason = _window_reason(window)
        if reason:
            return Metric(None, reason, start, end)
        if drawdown:
            peak = window[0].unit_nav
            result = 0.0
            for point in window:
                peak = max(peak, point.unit_nav)
                result = max(result, 1 - point.unit_nav / peak)
        else:
            result = window[-1].unit_nav / window[0].unit_nav - 1
        if not math.isfinite(result):
            return Metric(None, "净值比率超出有限数值范围", start, end)
        return Metric(result, start_date=start, end_date=end)

    metrics = {"return_1d": calculate(None)}
    for months in (1, 3, 6, 12):
        metrics[f"return_{months}m"] = calculate(_months_before(end, months))
    metrics["return_ytd"] = calculate(date(end.year - 1, 12, 31), tolerance=14)
    metrics["max_drawdown_12m"] = calculate(_months_before(end, 12), drawdown=True)
    return FundAnalysis(data, metrics, warnings)
