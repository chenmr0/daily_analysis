"""Published-NAV fund reports, separate from stock OHLCV and LLM decisions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import html
import json
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from src.core.fund_analysis import FundAnalysis, analyze_fund, parse_fund_codes

logger = logging.getLogger(__name__)

METRIC_LABELS = {
    "return_1d": "相邻净值日收益",
    "return_1m": "近1个月收益",
    "return_3m": "近3个月收益",
    "return_6m": "近6个月收益",
    "return_12m": "近12个月收益",
    "return_ytd": "今年以来收益",
    "max_drawdown_12m": "近12个月最大回撤",
}


@dataclass
class FundReport:
    markdown: str
    analyses: List[FundAnalysis] = field(default_factory=list)
    failed_codes: Dict[str, str] = field(default_factory=dict)
    report_path: Optional[str] = None
    snapshot_path: Optional[str] = None
    notification_status: str = "disabled"


def _text(value: object) -> str:
    """Keep external names/news readable without accepting HTML or Markdown."""
    text = html.escape(" ".join(str(value).split()), quote=True)
    return re.sub(r"([\\`*_\[\]#|])", r"\\\1", text)


def _link(label: str, url: str) -> str:
    parts = urlsplit(str(url))
    if parts.scheme not in {"https", "http"} or not parts.netloc or parts.username or parts.password:
        return _text(label)
    return f"[{_text(label)}]({quote(url, safe=':/?=&%+,-._~')})"


def _fund_news(data, search_service) -> List[str]:
    if search_service is None or not search_service.is_available:
        return ["未配置可用新闻检索；不影响净值及指标计算。"]
    profile = data.profile
    try:
        response = search_service.search_topic_news_bounded(
            profile.name,
            max_results=5,
            focus_keywords=[f'"{profile.name}"', f'"{profile.code}"', "基金 公告"],
            timeout_seconds=12.0,
        )
        if not response.success:
            return ["新闻检索不可用；不能据此判断没有相关事件。"]
        lines = []
        for item in response.results:
            identity_text = f"{item.title} {item.snippet}"
            # A bare six-digit code can be a stock. Require the actual fund
            # name in retrieved text, not the numeric coincidence alone.
            if profile.name not in identity_text:
                continue
            lines.append(
                f"{_link(item.title, item.url)}；来源：{_text(item.source)}；"
                f"发布日期：{_text(item.published_date or '未提供')}；"
                "关联依据：标题或摘要含本基金全名，未作涨跌归因。"
            )
        return lines or ["未找到通过本基金身份匹配的近期新闻；不代表没有相关事件。"]
    except Exception as exc:
        logger.warning("[基金] %s 新闻检索失败: %s", profile.code, exc)
        return ["新闻检索失败；不影响净值及指标计算。"]


def render_fund_report(analyses: List[FundAnalysis], failed: Dict[str, str], news, generated_at: datetime) -> str:
    lines = [
        "# 场外基金已披露净值日报",
        "",
        f"生成时间：{generated_at.isoformat()}；各基金以各自最新已披露净值日期为准。",
        "",
        "仅作数据研究，不是交易指令、评分或收益承诺。没有持仓权重，不计算个人组合收益。",
        "收益由程序计算，仅在区间未发现分红/拆分且累计净值与单位净值差额稳定时使用净值比值。",
        "遇到分红、拆分、缺数或历史不足显示 N/A，不将累计净值比值冒充总回报。",
        "",
    ]
    for analysis in analyses:
        data = analysis.data
        profile = data.profile
        latest = data.navs[-1]
        lines.extend([
            f"## {_text(profile.name)}（{profile.canonical_id}）",
            "",
            f"- 份额代码：{profile.code}；类型：{_text(profile.fund_type or '未提供')}；"
            f"币种：{_text(profile.currency or '未核验')}。",
            f"- 最新已披露单位净值：{latest.unit_nav:.6f}；净值所属日期：{latest.nav_date.isoformat()}。",
            f"- 净值来源：{_link('东方财富/天天基金', data.source_url)}；抓取时间：{data.fetched_at.isoformat()}；"
            "真实披露时间：未提供（抓取时间不等于披露时间）。",
            f"- 基金资料：{_link('资料来源', profile.source_url)}；基金经理：{_text(profile.manager or '未提供')}；"
            f"成立日期：{profile.inception_date or '未提供'}。",
        ])
        if latest.accumulated_nav is not None:
            lines.append(f"- 累计净值：{latest.accumulated_nav:.6f}（不用于直接计算总回报）。")
        for key, label in METRIC_LABELS.items():
            metric = analysis.metrics[key]
            if metric.value is None:
                lines.append(f"- {label}：N/A（{_text(metric.reason or '数据不足')}）。")
            else:
                lines.append(
                    f"- {label}：{metric.value * 100:.2f}%"
                    f"（实际区间 {metric.start_date} 至 {metric.end_date}）。"
                )
        lines.extend(["", "### 最新披露持仓", ""])
        portfolio = data.holdings
        if portfolio is None or not portfolio.holdings:
            lines.append("持仓数据不可用或该接口未覆盖；不代表空仓。")
        else:
            lines.append(
                f"报告期末：{portfolio.report_period_end}；真实披露日期：{portfolio.published_at or '未提供'}；"
                f"{_link('持仓来源', portfolio.source_url)}。只展示已披露股票，不代表今天持仓。"
            )
            for holding in portfolio.holdings[:10]:
                lines.append(f"- {_text(holding.name)}（{_text(holding.code)}）：占基金净值 {holding.weight_pct:.2f}%。")
        if "联接" in profile.name:
            lines.append("这是ETF联接基金：股票持仓表不等于目标ETF或底层完整持仓，未进行穿透和收益归因。")
        lines.extend(["", "### 数据质量与边界", ""])
        for warning in dict.fromkeys([*data.warnings, *analysis.warnings]):
            lines.append(f"- {_text(warning)}")
        lines.append("- 不提供盘中估值、成交量、盘口、个股买卖点或未经验证的评级。")
        lines.extend(["", "### 基金相关新闻", ""])
        lines.extend(f"- {line}" for line in news.get(profile.code, ["本次未启用新闻检索。 "]))
        lines.append("")
    if failed:
        lines.extend(["## 数据获取或校验失败", ""])
        lines.extend(f"- fund:{code}：{_text(reason)}" for code, reason in failed.items())
        lines.append("")
    lines.append("当前版本不支持交易执行、历史时点回测、合同基准超额收益、持仓穿透或申赎规则判断。")
    return "\n".join(lines) + "\n"


def run_fund_analysis(
    codes,
    *,
    send_notification: bool = False,
    include_news: bool = True,
    fetcher=None,
    notifier=None,
    search_service=None,
    as_of: Optional[date] = None,
    output_dir=None,
) -> FundReport:
    """Save an isolated fund snapshot and report; optionally reuse notifications.

    One fund/news failure does not discard other funds. Notifications are
    attempted once, never retried here, and never routed by ambiguous stock codes.
    """
    fund_codes = parse_fund_codes(codes)
    if fetcher is None:
        from data_provider.fund_fetcher import FundFetcher
        fetcher = FundFetcher()
    if include_news and search_service is None:
        try:
            from src.search_service import get_search_service
            search_service = get_search_service()
        except Exception as exc:
            logger.warning("[基金] 新闻服务不可用: %s", exc)
    generated_at = datetime.now(timezone.utc)
    end_day = as_of or generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    analyses, failed, news = [], {}, {}
    for code in fund_codes:
        try:
            data = fetcher.fetch(code, as_of=as_of) if as_of is not None else fetcher.fetch(code)
            if data.profile.code != code:
                raise ValueError(f"返回基金代码 {data.profile.code} 与请求 {code} 不一致")
            analysis = analyze_fund(data, as_of=end_day)
            analyses.append(analysis)
            if as_of is not None and as_of < generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date():
                news[code] = ["历史截止日分析不加入当前新闻，避免前视信息。"]
            else:
                news[code] = _fund_news(analysis.data, search_service) if include_news else ["本次未启用新闻检索。"]
        except Exception as exc:
            failed[code] = str(exc) or type(exc).__name__
            logger.warning("[基金] %s 获取或校验失败: %s", code, failed[code])
    report = FundReport(render_fund_report(analyses, failed, news, generated_at), analyses, failed)
    directory = Path(output_dir) if output_dir is not None else Path(__file__).resolve().parents[2] / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    basename = f"fund_report_{generated_at:%Y%m%d_%H%M%S_%f}"
    report_path, snapshot_path = directory / f"{basename}.md", directory / f"{basename}.json"
    snapshot = {
        "schema_version": 1, "asset_type": "mutual_fund", "generated_at": generated_at,
        "as_of": end_day,
        "analyses": [dict(canonical_id=item.data.profile.canonical_id, **asdict(item)) for item in analyses],
        "failed_codes": failed,
        "news": news,
    }
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")
    report_path.write_text(report.markdown, encoding="utf-8")
    report.report_path, report.snapshot_path = str(report_path), str(snapshot_path)
    logger.info("[基金] 报告已保存: %s；数据快照: %s", report_path, snapshot_path)
    channel_results = []
    if send_notification:
        try:
            if notifier is None:
                from src.notification import NotificationService
                notifier = NotificationService()
            if not notifier.is_available():
                report.notification_status = "unavailable"
            else:
                result = notifier.send_with_results(
                    report.markdown, route_type="report", email_subject=f"场外基金已披露净值日报 - {end_day}"
                )
                report.notification_status = result.status
                channel_results = [
                    {"channel": item.channel, "success": bool(item.success), "error_code": item.error_code}
                    for item in getattr(result, "channel_results", [])
                ]
        except Exception as exc:
            report.notification_status = "unknown"
            logger.warning("[基金] 通知结果不确定，未重试: %s", exc)
    # Persist transport feedback separately from NAV success. A transport's
    # acceptance does not prove inbox delivery, and is never retried here.
    snapshot["notification"] = {"status": report.notification_status, "channels": channel_results}
    pending_path = snapshot_path.with_suffix(".json.tmp")
    pending_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")
    pending_path.replace(snapshot_path)
    return report
