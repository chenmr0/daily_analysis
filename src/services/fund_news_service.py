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
# These describe what a headline can support, not verification of the event.
EVIDENCE_SCOPES = ("fund_event", "related_event", "sector_context", "weak_background")
FILTER_REASONS = ("invalid_title", "invalid_url", "identity_mismatch", "not_announcement",
                  "static_page", "third_party_product", "no_event_signal", "duplicate_url", "selection_limit")
REASON_LABELS = {
    "invalid_title": "标题不可用", "invalid_url": "链接不可用", "identity_mismatch": "身份或关联不匹配",
    "not_announcement": "无公告标题语境", "static_page": "静态行情或资料页",
    "third_party_product": "其他产品持仓提及", "no_event_signal": "缺少事件内容",
    "duplicate_url": "重复链接", "selection_limit": "条数上限未保留",
    "fund_event": "基金事件线索", "related_event": "关联对象事件线索",
    "sector_context": "具体行业或主题背景", "weak_background": "宽泛行业弱背景",
}
_BROAD_INDUSTRIES = frozenset({
    "制造业", "工业", "服务业", "科技", "金融", "金融业", "农业", "采矿业", "建筑业", "房地产业",
    "农、林、牧、渔业", "电力、热力、燃气及水生产和供应业", "批发和零售业",
    "交通运输、仓储和邮政业", "住宿和餐饮业", "信息传输、软件和信息技术服务业",
    "租赁和商务服务业", "科学研究和技术服务业", "水利、环境和公共设施管理业",
    "居民服务、修理和其他服务业", "教育", "卫生和社会工作", "文化、体育和娱乐业", "综合",
})
_ANNOUNCEMENT_CONTEXT = re.compile(r"公告|季度报告|年度报告|中期报告|招募说明书|基金合同")
_EVENT_CONTEXT = re.compile(
    r"公告|报告|招募说明书|基金合同|财报|季报|年报|中报|业绩|营收|营业收入|净利|毛利|盈利|亏损|"
    r"回购|分红|派息|收购|并购|增持|减持|融资|董事会|股东大会|上市|停牌|复牌|"
    r"发布|公布|披露|宣布|召开|推出|新产品|中标|订单|签订|获批|获准|处罚|诉讼|"
    r"涨停|跌停|上涨|下跌|[涨跌][0-9]|增长|减少|增加|开放申购|暂停申购|恢复申购|"
    r"限购|清盘|变更|调整|新政|政策|新进展|数据更新|"
    r"\b(?:announces?|announced|launch(?:es|ed)?|earnings|revenue|profit|dividends?|"
    r"buyback|merger|acquisition|rises?|falls?)\b", re.I,
)
_PRODUCT_CONTEXT = re.compile(r"ETF|基金|\bfund\b", re.I)
_PORTFOLIO_CONTEXT = re.compile(r"重仓|持仓|成[分份]股|投资组合|\b(?:holdings?|portfolio|constituents?)\b", re.I)
_STATIC_TITLE = re.compile(
    r"股票股价|股价行情|行情走势|实时行情|历史行情|净值估值|历史净值|净值走势|"
    r"基金档案|基金概况|基金资料|基本资料|基本信息|\b(?:stock quote|fund profile)\b", re.I,
)
_EVENT_ACTION = re.compile(
    r"发布|公布|披露|宣布|召开|推出|中标|签订|获批|获准|处罚|诉讼|回购|收购|并购|"
    r"分红|派息|涨停|跌停|上涨|下跌|[涨跌][0-9]|增长|减少|增加|暂停|恢复|变更|调整|修订|出炉|"
    r"\b(?:announces?|announced|launch(?:es|ed)?|rises?|falls?|buyback|merger|acquisition)\b", re.I,
)
_ISSUER_EVENT = re.compile(
    r"财报|季报|年报|中报|业绩|营收|营业收入|净利|毛利|盈利|亏损|董事会|股东大会|新产品|停牌|复牌|"
    r"\b(?:earnings|revenue|profit|dividends?)\b", re.I,
)
_SPECIFIC_EVENT = re.compile(
    r"(?:暂停|恢复|限制|开放|停止|取消|延长|提高|降低).{0,12}(?:申购|赎回|转换|定投|募集|限额|费率)|"
    r"(?:申购|赎回|转换|定投|募集|限额|费率).{0,12}(?:暂停|恢复|限制|开放|停止|取消|延长|提高|降低)|"
    r"(?:基金经理|经理).{0,16}(?:离任|卸任|上任|变动|增聘|解聘|履新)|"
    r"(?:离任|卸任|上任|增聘|解聘).{0,16}基金经理|"
    r"创(?:下)?(?:历史|年内|阶段性)?新[高低]|刷新.{0,8}(?:高点|低点|纪录)|"
    r"(?:股价|净值).{0,8}(?:突破|跌破)", re.I,
)
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
    evidence_scope: str = "weak_background"
    model_eligible: bool = False


@dataclass
class FundNewsCoverage:
    status: str = "no_verified_relation"
    queries: int = 0
    accepted: int = 0
    fresh: int = 0
    rejected: int = 0
    detail: str = ""
    # One finite-enum outcome per inspected search result. No provider content.
    reason_counts: dict[str, int] = field(default_factory=dict)


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


def _static_page(title: str, url: str) -> bool:
    """A search timestamp cannot turn a quote/profile endpoint into an article."""
    parts = urlsplit(url)
    host, path = parts.hostname or "", parts.path.rstrip("/")
    if ((host == "xueqiu.com" or host.endswith(".xueqiu.com"))
            and re.fullmatch(r"/S/[A-Za-z0-9.]+", path, re.I)):
        return True
    if host == "quote.eastmoney.com" or host.endswith(".quote.eastmoney.com"):
        return True
    if host == "finance.sina.com.cn" and re.fullmatch(r"/realstock/company/[^/]+/nc.shtml", path):
        return True
    return bool(_STATIC_TITLE.search(title) and not (_EVENT_ACTION.search(title) or _SPECIFIC_EVENT.search(title)))


def _other_product_portfolio(title: str, asset: FundRelatedAsset) -> bool:
    """Mention in another product's portfolio is not an issuer event.

    Keep a separately stated company event (including price/earnings analysis),
    even if the same headline also mentions a fund holding that company.
    """
    if not (_PRODUCT_CONTEXT.search(title) and _PORTFOLIO_CONTEXT.search(title)):
        return False
    name = unicodedata.normalize("NFKC", _plain(asset.name, 100))
    for clause in re.split(r"[,，;；。|｜]", unicodedata.normalize("NFKC", title)):
        match = re.search(re.escape(name).replace(r"\ ", r"\s+"), clause, re.I)
        if not match:
            continue
        # Stop before an explicit change of subject. An event later in the
        # headline may belong to a different constituent, not this issuer.
        after = re.split(r"然而|但是|同时|此外|另外|并且|以及|而|但|却|、", clause[match.end():], maxsplit=1)[0]
        signals = [match for pattern in (_EVENT_ACTION, _ISSUER_EVENT, _SPECIFIC_EVENT)
                   if (match := pattern.search(after))]
        event = min(signals, key=lambda match: match.start()) if signals else None
        # A subsequent fund/portfolio subject owns its event, not the company.
        if event and not (_PRODUCT_CONTEXT.search(after[:event.end()])
                          or _PORTFOLIO_CONTEXT.search(after[:event.end()])):
            return False
    return True


def _evidence_scope(category: str, asset: Optional[FundRelatedAsset]) -> str:
    if category in {"announcement", "direct_fund"}:
        return "fund_event"
    if category in {"industry", "theme"}:
        return "weak_background" if asset and asset.name in _BROAD_INDUSTRIES else "sector_context"
    return "related_event"


def _record_outcome(coverage: FundNewsCoverage, reason: str) -> None:
    # The enum is code-owned; no result titles, URLs, or exception strings enter it.
    if reason not in REASON_LABELS:
        raise ValueError("Unknown fund news outcome")
    coverage.reason_counts[reason] = coverage.reason_counts.get(reason, 0) + 1


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


def _news_value(record, key, default=None):
    """Read either live evidence or its backwards-compatible JSON snapshot."""
    return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)


def _digest_entity(item, assets):
    category = _news_value(item, "category", "")
    if category in {"announcement", "direct_fund"}:
        return "fund:" + _news_value(item, "source_url", ""), "", "", {
            "category": "fund", "name": "", "code": "",
            "source_date": _news_value(item, "source_date"),
            "source_url": _news_value(item, "source_url", ""), "verified": False,
        }
    matches = []
    for asset in assets:
        if (_news_value(asset, "category") != category
                or _news_value(asset, "source_url") != _news_value(item, "source_url")
                or str(_news_value(asset, "source_date")) != str(_news_value(item, "source_date"))
                or (category != "theme" and _news_value(asset, "verified") is not True)):
            continue
        candidate = FundRelatedAsset(category, _news_value(asset, "name", ""),
                                     _news_value(asset, "source_url", ""), code=_news_value(asset, "code"))
        if _headline_matches_related(_news_value(item, "title", ""), candidate):
            matches.append((candidate, asset))
    # Do not guess which company owns a multi-company headline or invent aliases.
    if len(matches) != 1:
        return "", "", "", None
    asset, original = matches[0]
    relationship = {"category": category, "name": asset.name, "code": asset.code or "",
                    "source_date": _news_value(original, "source_date"),
                    "source_url": _news_value(original, "source_url", ""),
                    "verified": _news_value(original, "verified") is True}
    return f"{category}:{asset.name}:{asset.code or ''}", asset.name, asset.code or "", relationship


def _digest_title(item, entity_name, code):
    title = unicodedata.normalize("NFKC", _plain(_news_value(item, "title"))).casefold()
    # Remove only known trailing publisher navigation, never arbitrary clauses.
    title = re.sub(r"_(?:公司新闻|新浪新闻)(?:_财经)?(?:_证券之星)?$", "", title)
    title = re.sub(r"\s*-\s*cfi\.cn\s*中财网$", "", title)
    if code:
        title = re.sub(r"\(" + re.escape(code.casefold()) + r"(?:\.(?:sh|sz|bj|hk|us))?\)", "", title)
    if entity_name:
        title = title.removeprefix(unicodedata.normalize("NFKC", entity_name).casefold())
    # Ignore spacing and headline punctuation, not words or numeric quantities.
    return re.sub(r"[\s:：,，。;；!?！？]", "", title)


_DIGEST_AMOUNT = re.compile(r"(?:不超过|不超|不低于|不少于|至多|最高|约)?\d+(?:\.\d+)?(?:亿|万)?元")
_DIGEST_UPDATE = re.compile(
    r"变更|调整|修订|取消|终止|撤销|失败|未获|不获|完成|实施|进展|首次|新增|延长|延期|"
    r"增加|减少|提高|降低|上调|下调|暂停|恢复|注销|拟|计划|通过|取得|获得|签订|签署"
)
_DIGEST_FAMILIES = (
    ("buyback", r"回购|\bbuyback\b"), ("earnings", r"财报|季报|年报|中报|业绩|营收|净利|盈利|亏损|\bearnings\b"),
    ("dividend", r"分红|派息|\bdividend\b"), ("acquisition", r"收购|并购|\bmerger\b|\bacquisition\b"),
    ("operations", r"申购|赎回|限额|基金经理"), ("legal", r"诉讼|处罚"),
    ("contract", r"中标|订单|签订|签署"),
)


def _digest_signature(title):
    families = tuple(key for key, pattern in _DIGEST_FAMILIES if re.search(pattern, title))
    updates = tuple(sorted(set(_DIGEST_UPDATE.findall(title))))
    numbers = tuple(re.findall(r"\d+(?:\.\d+)?", title))
    return families, updates, numbers


def _distinct_digest_event(left, right):
    families, updates, numbers = left
    other_families, other_updates, other_numbers = right
    if not families or not other_families:
        return False
    if families != other_families or (numbers and other_numbers and numbers != other_numbers):
        return True
    # Different verbs alone (e.g. obtained/received) are not separate events.
    # Keep explicit contradictory stages, rather than hiding an announced plan
    # behind a cancellation or vice versa. Everything else remains in raw data.
    opposing_stages = (({"暂停"}, {"恢复"}),
                       ({"取消", "终止", "撤销", "失败", "未获", "不获"},
                        {"拟", "计划", "通过", "取得", "获得", "实施", "完成"}))
    a, b = set(updates), set(other_updates)
    return any((a & first and b & second) or (a & second and b & first)
               for first, second in opposing_stages)


def _same_digest_event(left, right):
    """Only exact normalized titles or a narrow loan-commitment variant.

    This groups headline leads, not independently verified events. Dates and
    qualification must agree, and every original source remains in the group.
    """
    if left["context"] != right["context"]:
        return False
    if left["title"] == right["title"]:
        return True
    # A common syndicated title omits the amount. Accept that omission only
    # when the remaining specific commitment wording is identical. Differing
    # amounts, caps, dates or update wording must never disappear in a merge.
    a, b = left["title"], right["title"]
    required = ("取得", "金融机构", "股票回购", "贷款承诺")
    if not all(token in a and token in b for token in required):
        return False
    amounts_a, amounts_b = _DIGEST_AMOUNT.findall(a), _DIGEST_AMOUNT.findall(b)
    if amounts_a and amounts_b and amounts_a != amounts_b:
        return False
    return (_DIGEST_AMOUNT.sub("", a).replace("承诺书", "承诺")
            == _DIGEST_AMOUNT.sub("", b).replace("承诺书", "承诺"))


def _digest_reporting_context(item, entity_key):
    """Relationship priority is not an assessment of materiality or direction."""
    if not entity_key:
        return "unresolved", 4, "关联对象未能唯一绑定；仅保留标题线索，不推断基金影响。"
    category = _news_value(item, "category", "")
    if category in {"announcement", "direct_fund"}:
        return "fund", 0, "本基金标题线索；应先核验公告或报道全文及适用范围，再判断影响。"
    if category == "target_etf":
        return "direct_target", 1, "目标ETF标题线索；身份关联不证明当前配置比例，基金影响方向与程度未知。"
    if category == "holding":
        return "historical_holding", 2, "历史披露股票的公司线索；应核对事件原文与当前敞口，不等于本基金重大事件。"
    return "sector_background", 3, "行业或产品名称主题背景；不证明本基金当前配置，也不支持收益归因。"


def select_news_digest(bundle, *, max_items: int = 3) -> dict:
    """Read-only concise selection over live or saved qualified evidence.

    Only already-fresh, model-eligible event/context leads enter this digest.
    No source date is independently verified here and no relationship becomes
    current exposure or causal evidence. Original bundle items and coverage are
    untouched, including omissions; selected groups retain every source record.
    The raw item_index is the canonical evidence index; source_indices retain
    the other originals in a group. Relationship identity must resolve uniquely
    against supplied assets. Reporting priority never establishes materiality,
    a verified event, current exposure, or the direction of a fund's impact.
    Counts describe this presentation pass, not additional search outcomes.
    """
    if type(max_items) is not int or not 1 <= max_items <= 3:
        raise ValueError("News digest requires one to three items")
    raw = _news_value(bundle, "items", []) or []
    assets = _news_value(bundle, "related_assets", []) or []
    counts = dict.fromkeys(("raw_items", "ineligible", "grouped_events", "duplicate_events",
                            "duplicate_urls", "entity_limit", "selection_limit", "selected"), 0)
    counts["raw_items"] = len(raw)
    candidates = []
    for index, item in enumerate(raw):
        published = _publication(_news_value(item, "published_date"))
        scope = _news_value(item, "evidence_scope")
        if (_news_value(item, "date_status") != "fresh" or _news_value(item, "model_eligible") is not True
                or scope not in {"fund_event", "related_event", "sector_context"} or published is None
                or not _plain(_news_value(item, "title")) or not _safe_url(_news_value(item, "url"))):
            counts["ineligible"] += 1
            continue
        key, name, code, relationship = _digest_entity(item, assets)
        reporting_context, priority, relevance_note = _digest_reporting_context(item, key)
        title = _digest_title(item, name, code)
        families, updates, numbers = _digest_signature(title)
        # Direct fund and target ETF leads precede historical company/sector
        # background. This is reporting priority, not verified significance.
        rank = (priority,
                not bool(families), -published.toordinal(), -len(title), index)
        context = (key or "unresolved:" + _safe_url(_news_value(item, "url")),
                   _news_value(item, "category"), scope, published,
                   str(_news_value(item, "source_date")), _news_value(item, "source_url"),
                   _news_value(item, "relation_basis"))
        candidates.append({"item": item, "sources": [item], "entity_key": key, "entity_name": name,
                           "item_index": index, "source_indices": [index], "relationship": relationship,
                           "reporting_context": reporting_context, "relevance_note": relevance_note,
                           "verification_status": "headline_only_unverified", "impact_status": "unknown",
                           "title": title, "signature": (families, updates, numbers),
                           "context": context, "rank": rank})
    groups = []
    for candidate in sorted(candidates, key=lambda c: c["rank"]):
        duplicate = next((group for group in groups if _same_digest_event(group, candidate)), None)
        if duplicate is None:
            groups.append(candidate)
            continue
        url = _safe_url(_news_value(candidate["item"], "url"))
        same_url = any(_safe_url(_news_value(source, "url")) == url for source in duplicate["sources"])
        counts["duplicate_urls" if same_url else "duplicate_events"] += 1
        duplicate["sources"].append(candidate["item"])
        duplicate["source_indices"].append(candidate["item_index"])
    counts["grouped_events"] = len(groups)
    primary, additional, by_entity = [], [], {}
    for group in groups:
        key = group["entity_key"] or f"unresolved:{group['rank'][-1]}"
        previous = by_entity.setdefault(key, [])
        if not previous:
            primary.append(group)
            previous.append(group)
            continue
        # At most one extra material event per company, after other companies
        # get their first slot. A different publication day alone is not proof
        # of a new material event. Generic board notices stay in raw evidence.
        distinct = all(_distinct_digest_event(group["signature"], prior["signature"]) for prior in previous)
        if len(previous) < 2 and distinct:
            additional.append(group)
            previous.append(group)
        else:
            counts["entity_limit"] += 1
    # Preserve entity diversity within each relationship tier, without letting
    # a second direct event fall behind a historical-company headline.
    selection = sorted(primary + additional, key=lambda group: group["rank"][0])
    counts["selection_limit"] = max(0, len(selection) - max_items)
    selection = selection[:max_items]
    counts["selected"] = len(selection)
    fields = ("item", "sources", "entity_key", "entity_name", "item_index", "source_indices",
              "relationship", "reporting_context", "relevance_note", "verification_status", "impact_status")
    return {"items": [{key: group[key] for key in fields} for group in selection], "counts": counts}


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
            reason = ""
            if not title:
                reason = "invalid_title"
            elif not url:
                reason = "invalid_url"
            elif not matches:
                reason = "identity_mismatch"
            elif _static_page(title, url):
                reason = "static_page"
            elif category == "holding" and _other_product_portfolio(title, asset):
                reason = "third_party_product"
            elif category == "announcement" and not _ANNOUNCEMENT_CONTEXT.search(title):
                reason = "not_announcement"
            elif not (_EVENT_CONTEXT.search(title) or _SPECIFIC_EVENT.search(title)):
                reason = "no_event_signal"
            if reason:
                coverage.rejected += 1
                _record_outcome(coverage, reason)
                continue
            if url in seen:
                _record_outcome(coverage, "duplicate_url")
                continue
            published = _publication(getattr(result, "published_date", None))
            candidates.append((result, title, url, published, _date_status(published, as_of, fresh_days)))
        rank = {"fresh": 0, "stale": 1, "unknown": 2, "future": 3}
        candidates.sort(key=lambda c: (rank[c[4]], -(c[3].toordinal() if c[3] else 0)))
        accepted_this_query = 0
        for result, title, url, published, status in candidates:
            if url in seen:
                _record_outcome(coverage, "duplicate_url")
                continue
            if len(bundle.items) >= max_items or accepted_this_query >= (1 if asset is None else 2):
                _record_outcome(coverage, "selection_limit")
                continue
            relation = _basis(asset, as_of) if asset else "标题含本基金全名（含份额类别），仅身份匹配，未作涨跌归因。"
            if category == "announcement":
                relation += "公告相关检索结果，未将搜索摘要当作公告全文核验。"
            scope = _evidence_scope(category, asset)
            if scope == "weak_background":
                relation += " 宽泛行业名匹配，仅作弱背景；不作为模型事件证据。"
            elif scope == "sector_context":
                relation += " 仅具体行业/主题背景，不是本基金具体事件。"
            bundle.items.append(FundNewsItem(
                category, title, url, _plain(getattr(result, "source", ""), 100), published, status,
                _retrieval(getattr(result, "retrieved_at", None), acquired_at), relation,
                source_date=asset.source_date if asset else None,
                source_url=asset.source_url if asset else _safe_url(data.profile.source_url),
                evidence_scope=scope, model_eligible=(status == "fresh" and scope != "weak_background")))
            seen.add(url)
            coverage.accepted += 1
            coverage.fresh += status == "fresh"
            _record_outcome(coverage, scope)
            accepted_this_query += 1
        if coverage.accepted:
            coverage.status = "ok" if coverage.fresh else "no_fresh_evidence"
            coverage.detail = "已保留身份/关联匹配结果，优先选择日期明确且在窗口内的近期条目；其余仅作线索。"
        elif results:
            coverage.status, coverage.detail = "none_filtered", "未找到通过本基金身份匹配或已核验关联筛选的新闻（重复链接只保留一次）；不代表没有相关事件。"
    if any(i.date_status != "fresh" for i in bundle.items):
        bundle.warnings.append("日期未知、未来日期和过期新闻仅列作线索，不支持近期事件或涨跌原因结论。")
    if any(i.evidence_scope == "weak_background" for i in bundle.items):
        bundle.warnings.append("宽泛行业新闻单列为弱背景，不进入模型事件证据，不证明基金受影响。")
    if any(a.category in {"holding", "industry"} for a in assets):
        bundle.warnings.append("股票/行业新闻依赖所标注报告期的关系；定期披露滞后，不代表当前敞口或收益归因。")
    if bundle.coverage["holding"].queries:
        bundle.coverage["holding"].detail += " " + _HOLDING_IDENTITY_LIMIT
    for category in ("industry", "theme"):
        if bundle.coverage[category].queries:
            bundle.coverage[category].detail += " 标题须有行业/产业语境；仅行业或主题线索，不核验公司身份。"
    bundle.retrieved_at = datetime.now(timezone.utc)
    return bundle
