"""Bounded fund headlines with identity, relationship and date provenance.

Snippets cannot establish identity. Neither disclosed holdings nor related
headlines establish current exposure, look-through holdings or return causation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
import html
import ipaddress
from itertools import islice
import math
import re
import time
import unicodedata
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from src.core.fund_analysis import FundData, FundRelatedAsset

CATEGORIES = ("announcement", "direct_fund", "target_etf", "holding", "industry", "theme")
PRODUCT_THEMES = ("数字经济", "人工智能", "半导体", "新能源", "机器人", "医药", "医疗", "消费")
_ISSUER_CONTEXT = re.compile(
    r"公司|集团|股份|董事|股东|财报|季报|年报|中报|业绩|营收|营业收入|净利|毛利|盈利|亏损|"
    r"股票|股价|证券|回购|分红|派息|市值|每股|上市|停牌|复牌|涨停|跌停|增持|减持|"
    r"融资|收购|并购|投资者|[涨跌](?:幅|[0-9])|"
    r"\b(?:company|corporation|earnings|revenue|profit|shares?|stocks?|dividends?|buyback|merger|acquisition)\b", re.I,
)
_SECTOR_CONTEXT = re.compile(
    r"行业|产业|板块|产能|供应链|政策|景气|市场需求|经济|工业|制造业|服务业|金融业|"
    r"\b(?:industry|sector|industrial|supply chain|economic)\b", re.I,
)
_HOLDING_IDENTITY_LIMIT = (
    "股票标题须同时匹配披露名称及公司/证券语境；短名称或单个英文词还须含原样披露代码，"
    "不猜测别名或证券市场，不以代码或摘要单独认定身份；严格筛选可能漏报。"
)


@dataclass
class FundNewsItem:
    category: str
    title: str
    url: str
    source: str
    published_date: Optional[date]
    date_status: str
    retrieved_at: datetime
    relation_basis: str
    source_date: Optional[date] = None
    source_url: str = ""


@dataclass
class FundNewsCoverage:
    status: str = "no_verified_relation"
    queries: int = 0
    accepted: int = 0
    fresh: int = 0
    rejected: int = 0
    detail: str = ""


@dataclass
class FundNewsBundle:
    items: list[FundNewsItem] = field(default_factory=list)
    coverage: dict[str, FundNewsCoverage] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    related_assets: list[FundRelatedAsset] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _plain(value, limit=300) -> str:
    value = re.sub(r"<[^>]*>", " ", html.unescape(str(value or "")))
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C") or c.isspace())
    return " ".join(value.split())[:limit]


def _safe_url(value) -> str:
    raw = str(value or "").strip()
    if len(raw) > 2048 or re.search(r"[\s\\<>\x00-\x1f\x7f]", raw):
        return ""
    try:
        parts = urlsplit(raw)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.lower()
        if "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        _ = parts.port
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
    except (ValueError, TypeError):
        return ""


def _headline_matches_fund(title: str, fund_name: str) -> bool:
    """Full fund identity including share class must occur in the headline."""
    def normalized(value):
        return "".join(unicodedata.normalize("NFKC", html.unescape(str(value or ""))).split())
    name, headline = normalized(fund_name), normalized(title)
    if not name:
        return False
    pattern = re.escape(name)
    if name[0].isascii() and name[0].isalnum():
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if name[-1].isascii() and name[-1].isalnum():
        pattern += r"(?![A-Za-z0-9])"
    return re.search(pattern, headline) is not None


def _holding_code(value) -> str:
    """Keep source-qualified identifiers intact, never infer an exchange."""
    code = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    pattern = (r"(?:[A-Z]{1,6}(?:[.-][A-Z0-9]{1,4})?|"
               r"[0-9]{5,6}(?:\.(?:SH|SZ|BJ|HK|US))?|"
               r"(?:SH|SZ|HK|BJ)[0-9]{5,6}|"
               r"(?:NYSE|NASDAQ|AMEX|SH|SZ|HK|BJ):[A-Z0-9.]{1,10})")
    return code if re.fullmatch(pattern, code) else ""


def _headline_matches_holding(title: str, asset: FundRelatedAsset) -> bool:
    """A short stock label is not a legal issuer name; never invent aliases."""
    name = unicodedata.normalize("NFKC", _plain(asset.name, 100))
    headline = unicodedata.normalize("NFKC", title)
    if not name or re.fullmatch(r"[0-9.]+", name):
        return False
    # Preserve English word boundaries rather than compacting Apple AAPL.
    pattern = re.escape(name).replace(r"\ ", r"\s+")
    if name[0].isascii() and name[0].isalnum():
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if name[-1].isascii() and name[-1].isalnum():
        pattern += r"(?![A-Za-z0-9])"
    if not re.search(pattern, headline, re.I):
        return False
    if re.search(re.escape(name) + r"(?:指数|股票|混合|债券|货币)?(?:基金|ETF)", headline, re.I):
        return False
    if re.search(r"(?:股份有限公司|有限责任公司|有限公司)$|\b(?:Inc\.?|Corporation|Limited|Ltd\.?|plc)$", name, re.I):
        return True  # Only the full legal name actually supplied in the source.
    if not _ISSUER_CONTEXT.search(headline):
        return False
    ambiguous = len(re.sub(r"\s+", "", name)) <= 3 or bool(re.fullmatch(r"[A-Za-z]+", name))
    if not ambiguous:
        return True
    code = _holding_code(asset.code)
    if not code or not re.search(r"(?<![A-Za-z0-9_.])" + re.escape(code) + r"(?![A-Za-z0-9_.])", headline, re.I):
        return False
    return not re.search(r"(?:基金代码\s*[:：]?\s*|fund:)" + re.escape(code), headline, re.I)


def _headline_matches_related(title: str, asset: FundRelatedAsset) -> bool:
    if asset.category == "holding":
        return _headline_matches_holding(title, asset)
    if asset.category in {"industry", "theme"}:
        return _headline_matches_fund(title, asset.name) and bool(_SECTOR_CONTEXT.search(title))
    return _headline_matches_fund(title, asset.name)


def _publication(value) -> Optional[date]:
    match = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?:[T ][0-9:.+Z -]+)?", str(value or "").strip())
    if not match:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


def _retrieval(value, fallback: datetime) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.utcoffset() is not None and result <= fallback:
            return result
    except (ValueError, TypeError):
        pass
    return fallback


def _date_status(day, as_of, fresh_days):
    if day is None:
        return "unknown"
    if day > as_of:
        return "future"
    return "fresh" if (as_of - day).days <= fresh_days else "stale"


def _relations(data: FundData, as_of: date):
    assets, warnings = [], []
    for asset in data.related_assets[:20]:
        if not isinstance(asset, FundRelatedAsset) or not asset.verified:
            continue
        if asset.category not in {"target_etf", "industry"} or not _plain(asset.name) or not _safe_url(asset.source_url):
            continue
        if asset.source_date is not None and (type(asset.source_date) is not date or asset.source_date > as_of):
            warnings.append("关系资料报告期无效或在未来，未使用该关联。")
            continue
        if asset.published_at is not None and (type(asset.published_at) is not date or asset.published_at > as_of):
            warnings.append("关系资料披露日期无效或在未来，未使用该关联。")
            continue
        if asset.category == "target_etf" and (not _safe_url(asset.identity_url) or not re.fullmatch(r"[0-9]{6}", asset.code or "")):
            continue
        if asset.category == "industry" and asset.source_date is None:
            continue
        assets.append(replace(asset, name=_plain(asset.name, 100), source_url=_safe_url(asset.source_url),
                              identity_url=_safe_url(asset.identity_url), basis=_plain(asset.basis)))
    portfolio = data.holdings
    if portfolio and type(portfolio.report_period_end) is date and portfolio.report_period_end <= as_of and _safe_url(portfolio.source_url):
        if portfolio.published_at is None or (type(portfolio.published_at) is date and portfolio.published_at <= as_of):
            for holding in portfolio.holdings[:10]:
                weight = holding.weight_pct
                if not _plain(holding.name) or isinstance(weight, bool) or not isinstance(weight, (float, int)) or not math.isfinite(weight) or not 0 < weight <= 100:
                    continue
                assets.append(FundRelatedAsset(
                    "holding", _plain(holding.name, 100), _safe_url(portfolio.source_url),
                    source_date=portfolio.report_period_end, code=_plain(holding.code, 30),
                    weight_pct=weight, published_at=portfolio.published_at,
                    basis="已披露股票持仓明细；不是当前持仓或目标ETF穿透持仓", verified=True,
                ))
    for theme in PRODUCT_THEMES:
        if theme in data.profile.name and _safe_url(data.profile.source_url):
            assets.append(FundRelatedAsset(
                "theme", theme, _safe_url(data.profile.source_url),
                basis=f"产品名称含“{theme}”；仅主题观察，不是已核验行业配置或投资比例", verified=False,
            ))
            break
    return assets, warnings


def _basis(asset: FundRelatedAsset, as_of: date) -> str:
    if asset.category == "theme":
        return asset.basis + "；不作基金涨跌归因。"
    if asset.category == "target_etf":
        return (f"身份匹配的基金主页明确关联目标ETF {asset.name}（{asset.code}），并核验ETF名称/代码；"
                "资料发布日期未知，未核验当前配置比例、未穿透持仓、未作涨跌归因。")
    stale = "；报告期距今超过180天，关系可能已变化" if (as_of - asset.source_date).days > 180 else ""
    label = "股票持仓" if asset.category == "holding" else "行业配置"
    identity = f"{asset.name}（披露代码：{asset.code or '未提供'}）" if asset.category == "holding" else asset.name
    scope = ("股票名称与公司/证券语境匹配；不以数字代码单独认定身份" if asset.category == "holding"
             else "仅行业层面线索，不证明任何公司的身份")
    return (f"{label}报告期末 {asset.source_date}，真实披露日期 {asset.published_at or '未知'}；"
            f"{identity} 为该期已披露对象{stale}；{scope}；不代表当前持仓/配置，未作涨跌归因。")


def gather_fund_news(data: FundData, search_service, *, as_of: Optional[date] = None,
                     max_queries: int = 6, max_items: int = 10,
                     timeout_seconds: float = 36.0, fresh_days: int = 7) -> FundNewsBundle:
    """At most six searches, <=12 seconds each, <=36 seconds total, no retries.

    The bounded search adapter owns enforcing each supplied timeout. Unknown,
    future and stale article dates never support fresh-event conclusions.
    """
    if not 1 <= max_queries <= 6 or not 1 <= max_items <= 10 or not 0 < timeout_seconds <= 36 or not 0 <= fresh_days <= 30:
        raise ValueError("Fund news bounds exceeded")
    now = datetime.now(timezone.utc)
    as_of = as_of or now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if type(as_of) is not date:
        raise ValueError("as_of must be a date")
    assets, warnings = _relations(data, as_of)
    bundle = FundNewsBundle(coverage={c: FundNewsCoverage() for c in CATEGORIES}, warnings=warnings,
                            related_assets=assets, retrieved_at=now)
    for coverage in bundle.coverage.values():
        coverage.detail = "未取得可核验关联；不猜测目标ETF或行业。"
    if search_service is None or not getattr(search_service, "is_available", False):
        for coverage in bundle.coverage.values():
            coverage.status, coverage.detail = "not_configured", "未配置可用新闻检索；不影响净值及指标计算。"
        return bundle
    name = _plain(data.profile.name, 160)
    if not name:
        for coverage in bundle.coverage.values():
            coverage.status, coverage.detail = "no_identity", "基金名称不可用，未执行新闻检索。"
        return bundle
    queries = [("announcement", name, None, [f'"{name}"', f'"{data.profile.code}"', "公告 定期报告"]),
               ("direct_fund", name, None, [f'"{name}"', "基金 最新消息"])]
    targets = [a for a in assets if a.category == "target_etf"][:1]
    holdings = sorted((a for a in assets if a.category == "holding"), key=lambda a: -(a.weight_pct or 0))[:2]
    industries = sorted((a for a in assets if a.category == "industry"), key=lambda a: -(a.weight_pct or 0))[:1]
    themes = [a for a in assets if a.category == "theme"][:1]
    for asset in [*targets, *holdings, *industries, *themes]:
        keywords = [f'"{asset.name}"']
        if asset.category == "holding":
            code = _holding_code(asset.code)
            if code:
                keywords.append(f'"{code}"')
            keywords.append("公司 股票 财报 公告")
        else:
            keywords.append("最新消息 公告" if asset.category == "target_etf" else "行业 产业 最新消息")
        queries.append((asset.category, asset.name, asset, keywords))
    deadline = time.monotonic() + timeout_seconds
    seen = set()
    for index, (category, topic, asset, keywords) in enumerate(queries):
        coverage = bundle.coverage[category]
        remaining = deadline - time.monotonic()
        if index >= max_queries or remaining <= 0 or len(bundle.items) >= max_items:
            if coverage.queries == 0:
                coverage.status, coverage.detail = "skipped_budget", "达到查询、时间或条数上限，未继续检索。"
            continue
        coverage.queries += 1
        try:
            response = search_service.search_topic_news_bounded(
                topic, max_results=5, focus_keywords=keywords, timeout_seconds=min(12.0, remaining))
            if not response.success:
                coverage.status, coverage.detail = "unavailable", "新闻检索不可用；不能据此判断没有相关事件。"
                continue
            results = list(islice(response.results or [], 5))
            acquired_at = datetime.now(timezone.utc)
        except TimeoutError:
            coverage.status, coverage.detail = "timeout", "新闻检索失败：请求超时；未重试，不影响净值及指标计算。"
            continue
        except Exception:
            coverage.status, coverage.detail = "unavailable", "新闻检索失败；不影响净值及指标计算。"
            continue
        coverage.status, coverage.detail = "no_results", "检索未返回结果；不代表没有相关事件。"
        candidates = []
        for result in results:
            title, url = _plain(getattr(result, "title", "")), _safe_url(getattr(result, "url", ""))
            matches = _headline_matches_related(title, asset) if asset else _headline_matches_fund(title, topic)
            if not title or not url or not matches:
                coverage.rejected += 1
                continue
            if category == "announcement" and not re.search(r"公告|季度报告|年度报告|中期报告|招募说明书|基金合同", title):
                coverage.rejected += 1
                continue
            if url in seen:
                continue
            published = _publication(getattr(result, "published_date", None))
            candidates.append((result, title, url, published, _date_status(published, as_of, fresh_days)))
        rank = {"fresh": 0, "stale": 1, "unknown": 2, "future": 3}
        candidates.sort(key=lambda c: (rank[c[4]], -(c[3].toordinal() if c[3] else 0)))
        accepted_this_query = 0
        for result, title, url, published, status in candidates:
            if url in seen:
                continue
            relation = _basis(asset, as_of) if asset else "标题含本基金全名（含份额类别），仅身份匹配，未作涨跌归因。"
            if category == "announcement":
                relation += "公告相关检索结果，未将搜索摘要当作公告全文核验。"
            bundle.items.append(FundNewsItem(
                category, title, url, _plain(getattr(result, "source", ""), 100), published, status,
                _retrieval(getattr(result, "retrieved_at", None), acquired_at), relation,
                source_date=asset.source_date if asset else None,
                source_url=asset.source_url if asset else _safe_url(data.profile.source_url)))
            seen.add(url)
            coverage.accepted += 1
            coverage.fresh += status == "fresh"
            accepted_this_query += 1
            if len(bundle.items) >= max_items or accepted_this_query >= (1 if asset is None else 2):
                break
        if coverage.accepted:
            coverage.status = "ok" if coverage.fresh else "no_fresh_evidence"
            coverage.detail = "已保留身份/关联匹配结果，优先选择日期明确且在窗口内的近期条目；其余仅作线索。"
        elif results:
            coverage.status, coverage.detail = "none_filtered", "未找到通过本基金身份匹配或已核验关联筛选的新闻（重复链接只保留一次）；不代表没有相关事件。"
    if any(i.date_status != "fresh" for i in bundle.items):
        bundle.warnings.append("日期未知、未来日期和过期新闻仅列作线索，不支持近期事件或涨跌原因结论。")
    if any(a.category in {"holding", "industry"} for a in assets):
        bundle.warnings.append("股票/行业新闻依赖所标注报告期的关系；定期披露滞后，不代表当前敞口或收益归因。")
    if bundle.coverage["holding"].queries:
        bundle.coverage["holding"].detail += " " + _HOLDING_IDENTITY_LIMIT
    for category in ("industry", "theme"):
        if bundle.coverage[category].queries:
            bundle.coverage[category].detail += " 标题须有行业/产业语境；仅行业或主题线索，不核验公司身份。"
    bundle.retrieved_at = datetime.now(timezone.utc)
    return bundle
