"""Non-personal action references, independent of model output and market timing.

Historical direction is descriptive only: no positive/negative return, drawdown,
news item or calendar-day age maps to a buy/sell recommendation. This module has
no personal context, transaction rules, network calls or strategy backtest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Optional

from src.core.fund_analysis import FundAnalysis, Metric, _months_before


REFERENCE_VERSION = "evidence_boundary_v1"
EVIDENCE_KEYS = ("return_1m", "return_3m", "max_drawdown_12m")
REQUIRED_KEYS = EVIDENCE_KEYS[:2]
REASON_LABELS = {
    "personal_context_unknown": "未取得个人持有情况、资金期限、原定退出条件与可承受风险范围。",
    "dealing_terms_unknown": "未核验本份额当前申赎状态、限额、费用和确认/到账规则。",
    "nav_unavailable": "没有可用的已披露净值日期。",
    "nav_invalid": "净值日期或数值校验未通过。",
    "metric_unavailable": "必要区间证据不足或未通过分红/拆分等指标门控。",
    "metric_invalid": "指标数值或实际起止日期校验未通过。",
    "component_failed": "操作参考生成失败，保留已取得的净值和独立AI解读状态。",
}
NEXT_CHECKS = (
    "核对是否持有，以及个人原定退出条件、用款期限和可承受风险范围是否已触发；本报告未核实这些条件。",
    "核验本份额当前申赎状态、限额、适用费用和确认/到账时间，再判断实际可行性与代价。",
    "在取得新的有效净值或补齐缺失证据后重新核对；历史涨跌、回撤或新闻本身不自动触发买卖。",
)
LIMITATIONS = (
    "这是非个性化的证据边界提示，不是经回测验证的择时策略，不提供买卖价格、仓位或目标收益。",
    "收益仅为经过现有事件门控的单位净值变化，非红利再投资总回报；历史方向不预测未来。",
    "最大回撤是所标区间的历史最差峰谷跌幅，不是当前回撤、个人亏损或止损阈值。",
    "净值所属日期不等于实际披露时间；周末、假期和QDII不能用自然日间隔机械判定失效或买卖。",
)


@dataclass(frozen=True)
class ReferenceEvidence:
    metric: str
    value: Optional[float]
    start_date: Optional[date]
    end_date: Optional[date]
    direction: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class FundActionReference:
    fund_code: str
    status: str
    action: str
    headline: str
    not_held: str
    held: str
    as_of: date
    nav_date: Optional[date]
    method: str = REFERENCE_VERSION
    holding_context: str = "unknown"
    evidence: tuple[ReferenceEvidence, ...] = ()
    reason_codes: tuple[str, ...] = ()
    next_checks: tuple[str, ...] = NEXT_CHECKS
    limitations: tuple[str, ...] = LIMITATIONS


def unavailable_reference(code: str, as_of: date, reason: str = "component_failed", *,
                          nav_date: Optional[date] = None,
                          evidence: tuple[ReferenceEvidence, ...] = ()) -> FundActionReference:
    """Bounded fail-closed contract; never expose component exception text."""
    if reason not in {"nav_unavailable", "nav_invalid", "metric_unavailable", "metric_invalid", "component_failed"}:
        reason = "component_failed"
    return FundActionReference(
        code, "unavailable", "undetermined", "无法判断：操作参考所需证据不可用。",
        "若未持有：暂不依据本报告新增买入。",
        "若已持有：本报告不能判断是否卖出，也不能据此认定继续持有安全。",
        as_of, nav_date, evidence=evidence,
        reason_codes=(reason, "personal_context_unknown", "dealing_terms_unknown"),
    )


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _evidence(key: str, metric: Optional[Metric], end: date) -> tuple[ReferenceEvidence, Optional[str]]:
    """Consume the existing metric gate; never recalculate or fill N/A values."""
    if metric is None or metric.value is None:
        # Missing optional yearly history does not invalidate usable shorter windows.
        start = metric.start_date if metric is not None and type(metric.start_date) is date and metric.start_date <= end else None
        stop = end if metric is not None and metric.end_date == end else None
        return ReferenceEvidence(key, None, start, stop, reason=(
            metric.reason if metric is not None and isinstance(metric.reason, str) else "指标不可用"
        )), "metric_unavailable" if key in REQUIRED_KEYS else None
    anchor = _months_before(end, {"return_1m": 1, "return_3m": 3, "max_drawdown_12m": 12}[key])
    if (not _finite(metric.value) or not math.isfinite(metric.value * 100) or metric.reason is not None
            or type(metric.start_date) is not date or type(metric.end_date) is not date
            or metric.start_date >= metric.end_date or metric.end_date != end
            or not 0 <= (anchor - metric.start_date).days <= 7
            or (key == "max_drawdown_12m" and not 0 <= metric.value < 1)
            or (key in REQUIRED_KEYS and metric.value <= -1)):
        return ReferenceEvidence(key, None, None, None, reason=REASON_LABELS["metric_invalid"]), "metric_invalid"
    direction = None
    if key in REQUIRED_KEYS:
        direction = "positive" if metric.value > 0 else "negative" if metric.value < 0 else "flat"
    return ReferenceEvidence(key, metric.value, metric.start_date, metric.end_date, direction), None


def build_action_reference(analysis: FundAnalysis, *, as_of: date) -> FundActionReference:
    """Describe what current evidence can support, without inferring a trade.

    The only inputs are validated public fund analysis and its cutoff date. No
    user holdings, costs, risk preferences, AI text or news enter this contract.
    """
    if type(as_of) is not date:
        raise ValueError("as_of must be a date")
    code = analysis.data.profile.code
    if not analysis.data.navs:
        return unavailable_reference(code, as_of, "nav_unavailable")
    latest = analysis.data.navs[-1]
    if (type(latest.nav_date) is not date or latest.nav_date > as_of
            or not _finite(latest.unit_nav) or latest.unit_nav <= 0):
        return unavailable_reference(code, as_of, "nav_invalid")
    evidence, blockers = [], []
    for key in EVIDENCE_KEYS:
        item, blocker = _evidence(key, analysis.metrics.get(key), latest.nav_date)
        evidence.append(item)
        if blocker:
            blockers.append(blocker)
    if blockers:
        reason = "metric_invalid" if "metric_invalid" in blockers else "metric_unavailable"
        return unavailable_reference(code, as_of, reason, nav_date=latest.nav_date, evidence=tuple(evidence))
    return FundActionReference(
        code, "conditional", "observe", "观望：现有资料不足以支持明确的个人买卖判断。",
        "若未持有：暂不依据本报告新增买入。",
        "若已持有：是否卖出尚不能判断；需核对个人退出条件、资金期限与风险范围，观望不表示继续持有安全。",
        as_of, latest.nav_date, evidence=tuple(evidence),
        reason_codes=("personal_context_unknown", "dealing_terms_unknown"),
    )
