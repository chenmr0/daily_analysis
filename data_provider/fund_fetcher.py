"""Published off-exchange NAV data from Eastmoney; never a stock quote fallback.

Eastmoney is an aggregator, not an independent confirmation of fund-manager data.
JavaScript endpoints are parsed as JSON literals, never evaluated.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.core.fund_analysis import (
    FundData, FundProfile, FundRelatedAsset, Holding, HoldingsSnapshot, NavPoint,
)


class FundDataError(ValueError):
    """The source did not supply a trustworthy published NAV series."""


class UnsupportedFundError(FundDataError):
    """Verified product type is incompatible with this NAV analysis."""


class ProfileIdentityError(FundDataError):
    """Metadata page does not identify the requested fund."""


class ProfileSchemaError(FundDataError):
    """Metadata page does not contain the required fields."""


def _diagnostic(exc: Exception) -> str:
    """Only error class/status, never arbitrary server or exception text."""
    cause = exc.__cause__ or exc
    if isinstance(cause, HTTPError):
        return f"HTTPError HTTP {cause.code}"
    return type(cause).__name__


def _verify_profile_identity(page: str, code: str, name: str):
    title = re.search(r"<title\b[^>]*>(.*?)</title>", page, re.S | re.I)

    def normalize(text):
        return re.sub(r"\s+", "", text).replace("（", "(").replace("）", ")")

    if not title or not normalize(_text(title[1])).startswith(normalize(f"{name}({code})")):
        raise ProfileIdentityError("Profile identity could not be verified")


def _literal(text: str, name: str):
    match = re.search(r"\bvar\s+" + re.escape(name) + r"\s*=\s*", text)
    if not match:
        raise FundDataError(f"Missing source field: {name}")
    try:
        value, end = json.JSONDecoder().raw_decode(text[match.end():])
    except (ValueError, TypeError) as exc:
        raise FundDataError(f"Invalid JSON literal: {name}") from exc
    if not text[match.end() + end:].lstrip().startswith(";"):
        raise FundDataError(f"Invalid literal boundary: {name}")
    return value


def _number(value, *, positive=False) -> float:
    if isinstance(value, bool):
        raise FundDataError("Boolean supplied as numeric fund data")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise FundDataError("Invalid numeric fund data") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise FundDataError("Non-finite or nonpositive fund data")
    return result


def _nav_date(timestamp) -> date:
    # Source timestamps represent midnight in China, not UTC calendar dates.
    return datetime.fromtimestamp(
        _number(timestamp) / 1000, timezone(timedelta(hours=8))
    ).date()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    return " ".join(" ".join(parser.parts).split())


class FundFetcher:
    """Small bounded HTTP adapter with injectable stdlib-compatible opener."""

    def __init__(self, timeout: float = 15, retries: int = 1,
                 opener: Callable = urlopen):
        if not 0 < timeout <= 60 or not 0 <= retries <= 2:
            raise ValueError("Use timeout (0, 60] and retries [0, 2]")
        self.timeout = timeout
        self.retries = retries
        self.opener = opener

    def _get(self, url: str) -> str:
        request = Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://fundf10.eastmoney.com/",
        })
        for attempt in range(self.retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    body = response.read(8 * 1024 * 1024 + 1)
                    if len(body) > 8 * 1024 * 1024:
                        raise FundDataError("Fund response exceeds size limit")
                    return body.decode("utf-8-sig")
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise FundDataError(f"Fund source request failed: {url}") from exc
                time.sleep(0.2 * (attempt + 1))
        raise AssertionError("Unreachable retry state")

    @staticmethod
    def _navs(script: str, as_of: Optional[date]) -> list[NavPoint]:
        raw = _literal(script, "Data_netWorthTrend")
        accumulated = _literal(script, "Data_ACWorthTrend")
        if not isinstance(raw, list) or not raw or not isinstance(accumulated, list):
            raise FundDataError("Missing published NAV history")
        acc = {}
        for row in accumulated:
            day = _nav_date(row[0])
            if day in acc:
                raise FundDataError("Duplicate accumulated NAV date")
            acc[day] = _number(row[1], positive=True)
        points = []
        previous = None
        for row in raw:
            day = _nav_date(row["x"])
            if previous is not None and day <= previous:
                raise FundDataError("NAV history must have unique ascending dates")
            previous = day
            nav = _number(row["y"], positive=True)
            daily = row.get("equityReturn")
            daily = None if daily in (None, "") else _number(daily)
            if as_of is None or day <= as_of:
                points.append(NavPoint(
                    nav_date=day, unit_nav=nav, accumulated_nav=acc.get(day),
                    daily_return_pct=daily, event=str(row.get("unitMoney") or ""),
                ))
        if not points:
            raise FundDataError("No published NAV on or before the requested date")
        return points

    @staticmethod
    def _profile(code: str, name: str, script: str, page: str,
                 source_url: str) -> FundProfile:
        plain = _text(page)
        fields = {}
        for label, following in [("基金类型", "发行日期"),
                                 ("基金经理人", "成立来分红")]:
            match = re.search(re.escape(label) + r"\s+(.*?)\s+" + following, plain)
            fields[label] = match.group(1) if match else ""
        if "货币" in fields.get("基金类型", ""):
            raise UnsupportedFundError("Money-market fund NAV convention is unsupported")
        inception = re.search(r"成立日期/规模\s+(\d{4})年(\d{2})月(\d{2})日", plain)
        established = date(*map(int, inception.groups())) if inception else None
        managers = fields.get("基金经理人")
        if not managers:
            try:
                managers = "、".join(x["name"] for x in _literal(script, "Data_currentFundManager"))
            except (FundDataError, KeyError, TypeError):
                managers = None
        return FundProfile(
            code=code, name=name, fund_type=fields.get("基金类型", ""),
            manager=managers or None, inception_date=established,
            source_url=source_url,
        )

    @staticmethod
    def _homepage_profile(code: str, name: str, page: str, url: str) -> FundProfile:
        _verify_profile_identity(page, code, name)
        # Restrict fields to the first fund-information table, not news/sidebar text.
        section = re.search(r'<div\b[^>]*class=["\']infoOfFund["\'][^>]*>.*?'
                            r'<table\b[^>]*>(.*?)</table>', page, re.S | re.I)
        if not section:
            raise ProfileSchemaError("Fund information table missing")
        fields = [_text(cell) for cell in re.findall(
            r"<td\b[^>]*>(.*?)</td>", section[1], re.S | re.I)]
        # Inspect verified type before parsing unrelated optional metadata.
        for cell in fields:
            if cell.startswith("类型：") and "货币" in cell.split("：", 1)[1].split("|", 1)[0]:
                raise UnsupportedFundError("Money-market fund NAV convention is unsupported")
        fund_type = manager = ""
        inception = None
        for cell in fields:
            if cell.startswith("类型："):
                fund_type = cell.split("：", 1)[1].split("|", 1)[0].strip()
            elif cell.startswith("基金经理："):
                manager = cell.split("：", 1)[1].strip()
            else:
                match = re.fullmatch(r"成\s*立\s*日\s*[：:]\s*(\d{4}-\d{2}-\d{2})", cell)
                if match:
                    try:
                        inception = date.fromisoformat(match[1])
                    except ValueError as exc:
                        raise ProfileSchemaError("Invalid inception date") from exc
        if not fund_type or not manager or inception is None:
            raise ProfileSchemaError("Incomplete homepage profile")
        return FundProfile(code=code, name=name, fund_type=fund_type,
                           manager=manager, inception_date=inception, source_url=url)

    def _load_profile(self, code: str, name: str, script: str, source: str,
                      warnings: list[str]) -> FundProfile:
        primary_url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
        fallback_url = f"https://fund.eastmoney.com/{code}.html"
        profile = self._profile(code, name, script, "", source)
        try:
            page = self._get(primary_url)
            _verify_profile_identity(page, code, name)
            candidate = self._profile(code, name, script, page, primary_url)
            profile = candidate
            if candidate.fund_type and candidate.inception_date is not None and candidate.manager:
                return candidate
            raise ProfileSchemaError("Incomplete primary profile")
        except UnsupportedFundError:
            raise
        except (FundDataError, UnicodeError, ValueError) as exc:
            warnings.append(f"基金概况请求失败或资料校验未通过 [{_diagnostic(exc)}]：{primary_url}")
        try:
            candidate = self._homepage_profile(code, name, self._get(fallback_url), fallback_url)
        except UnsupportedFundError:
            raise
        except (FundDataError, UnicodeError, ValueError) as exc:
            warnings.append(f"备用基金概况不可用 [{_diagnostic(exc)}]：{fallback_url}；保留已验证资料。")
        else:
            warnings.append(f"基金概况已使用身份核验通过的同源备用页面：{fallback_url}；非独立数据源复核。")
            profile = candidate
        missing = []
        if not profile.fund_type:
            missing.append("类型")
        if profile.inception_date is None:
            missing.append("成立日期")
        if not profile.manager:
            missing.append("经理")
        if missing:
            warnings.append("基金概况仍缺少：" + "、".join(missing) + "；未补造数据。")
        return profile

    @staticmethod
    def _holdings(script: str, url: str,
                  as_of: Optional[date]) -> Optional[HoldingsSnapshot]:
        # apidata is a JS object with unquoted keys. Decode only its JSON string
        # value, then parse the HTML. Do not rewrite/evaluate the whole object.
        match = re.search(r"\bcontent\s*:\s*", script)
        if not match:
            raise FundDataError("Missing holdings content field")
        try:
            markup, _ = json.JSONDecoder().raw_decode(script[match.end():])
        except ValueError as exc:
            raise FundDataError("Invalid holdings content") from exc
        if not isinstance(markup, str):
            raise FundDataError("Holdings content is not a string")
        if not markup.strip():
            return None
        # Historical report publication dates are absent. An as-of report must
        # not infer what was known at the time from period-end alone.
        if as_of is not None:
            return None
        heading = re.search(r"<h4\b[^>]*>(.*?)</h4>", markup, re.S | re.I)
        table = re.search(r"<table\b[^>]*>(.*?)</table>", markup, re.S | re.I)
        period = re.search(r"截止至\s*[：:]\s*(\d{4}-\d{2}-\d{2})",
                           _text(heading[1]) if heading else "")
        if not table or not period:
            raise FundDataError("Holdings report period or table missing")
        holdings = []
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table[1], re.S | re.I):
            cells = [_text(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S | re.I)]
            if not cells:
                continue
            if len(cells) < 7 or not cells[6].endswith("%"):
                raise FundDataError("Unrecognized holdings table schema")
            weight = _number(cells[6][:-1])
            if not 0 <= weight <= 100:
                raise FundDataError("Invalid holdings weight")
            holdings.append(Holding(code=cells[1], name=cells[2], weight_pct=weight))
        if not holdings:
            return None
        return HoldingsSnapshot(
            report_period_end=date.fromisoformat(period[1]), holdings=holdings,
            source_url=url, published_at=None,
        )

    @staticmethod
    def _industry_assets(payload: str, profile: FundProfile, url: str,
                         today: date) -> list[FundRelatedAsset]:
        """Use identity-bound JSON, not potentially stale static HTML summaries."""
        result = json.loads(payload)
        data = result.get("Data") or {}
        if result.get("ErrCode") != 0 or data.get("FundCode") != profile.code or data.get("ShortName") != profile.name:
            raise ProfileIdentityError("Industry response fund identity mismatch")
        periods = data.get("QuarterInfos") or []
        if not isinstance(periods, list):
            raise FundDataError("Invalid industry periods")
        dated = []
        for period in periods[:8]:
            day = date.fromisoformat(period["JZRQ"])
            if day > today:
                raise FundDataError("Future industry report period")
            dated.append((day, period))
        if not dated:
            return []
        day, period = max(dated, key=lambda item: item[0])
        assets = []
        for row in (period.get("HYPZInfo") or [])[:50]:
            if row.get("BZDM") != profile.code or row.get("SHORTNAME") != profile.name or row.get("FSRQ") != day.isoformat():
                raise ProfileIdentityError("Industry row identity or period mismatch")
            name = row.get("HYMC")
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise FundDataError("Invalid industry name")
            weight = _number(row.get("ZJZBL"))
            if not 0 <= weight <= 100:
                raise FundDataError("Invalid industry weight")
            if weight > 0:
                assets.append(FundRelatedAsset(
                    "industry", name.strip(), url, source_date=day, weight_pct=weight, verified=True,
                    basis="身份核验通过的行业配置接口；行业分类来自数据源，真实披露日期未知，不代表当前配置"))
        return sorted(assets, key=lambda item: -item.weight_pct)[:10]

    @staticmethod
    def _target_etf_code(page: str, profile: FundProfile) -> Optional[str]:
        _verify_profile_identity(page, profile.code, profile.name)
        section = re.search(r'<div\b[^>]*class=["\']fundDetail-tit["\'][^>]*>(.*?)'
                            r'<div\b[^>]*class=["\']fundDetail-tools["\']', page, re.S | re.I)
        if not section:
            return None
        codes = []
        for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', section[1], re.S | re.I):
            if _text(label) != "查看相关ETF>":
                continue
            match = re.fullmatch(r'https?://fund\.eastmoney\.com/([0-9]{6})\.html', href)
            if not match or match[1] == profile.code:
                raise ProfileIdentityError("Invalid target ETF link")
            codes.append(match[1])
        if len(set(codes)) > 1:
            raise ProfileIdentityError("Conflicting target ETF links")
        return codes[0] if codes else None

    def _load_related_assets(self, profile: FundProfile, today: date,
                            warnings: list[str]) -> list[FundRelatedAsset]:
        assets = []
        industry_url = f"https://api.fund.eastmoney.com/f10/HYPZ/?fundCode={profile.code}&year="
        try:
            assets.extend(self._industry_assets(self._get(industry_url), profile, industry_url, today))
        except (FundDataError, ValueError, KeyError, TypeError, AttributeError) as exc:
            warnings.append(f"行业配置不可用或身份/日期未通过核验 [{_diagnostic(exc)}]；不从基金名称猜测行业比例。")
        if "联接" not in profile.name or "ETF" not in profile.name.upper():
            return assets
        own_url = f"https://fund.eastmoney.com/{profile.code}.html"
        try:
            code = self._target_etf_code(self._get(own_url), profile)
            if code is None:
                raise ProfileSchemaError("Explicit target ETF link absent")
            target_url = f"https://fund.eastmoney.com/{code}.html"
            page = self._get(target_url)
            title = re.search(r"<title\b[^>]*>(.*?)</title>", page, re.S | re.I)
            name = re.match(r"(.+?)\(" + code + r"\)基金", _text(title[1]) if title else "")
            if not name:
                raise ProfileIdentityError("Target ETF identity mismatch")
            target = self._homepage_profile(code, name[1], page, target_url)
            if "ETF" not in (target.name + target.fund_type).upper() or "联接" in target.name:
                raise ProfileIdentityError("Related asset is not a verified ETF")
            assets.append(FundRelatedAsset(
                "target_etf", target.name, own_url, code=code, identity_url=target_url,
                basis="身份核验通过的基金标题区域明确链接相关ETF，并核验ETF页面名称/代码；同源资料，非独立复核；资料发布日期未知",
                verified=True))
        except (FundDataError, ValueError, TypeError) as exc:
            warnings.append(f"目标ETF关系未核验 [{_diagnostic(exc)}]；未根据名称推断或补造映射。")
        return assets

    def fetch(self, code: str, as_of: Optional[date] = None) -> FundData:
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code, flags=re.ASCII):
            raise ValueError("Off-exchange fund code must be exactly six ASCII digits")
        now = datetime.now(timezone.utc)
        if as_of is not None and (not isinstance(as_of, date) or isinstance(as_of, datetime)):
            raise ValueError("as_of must be a date")
        if as_of is not None and as_of > now.astimezone(timezone(timedelta(hours=8))).date():
            raise ValueError("as_of cannot be in the future")
        source = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
        script = self._get(source)
        if _literal(script, "ishb") is not False:
            raise FundDataError("Money-market or unknown fund NAV convention is unsupported")
        if _literal(script, "fS_code") != code:
            raise FundDataError("Source fund identity mismatch")
        name = _literal(script, "fS_name")
        if not isinstance(name, str) or not name.strip():
            raise FundDataError("Missing fund name")
        try:
            navs = self._navs(script, as_of)
        except (KeyError, TypeError, IndexError, OverflowError) as exc:
            raise FundDataError("Malformed published NAV history") from exc
        if navs[-1].nav_date > now.astimezone(timezone(timedelta(hours=8))).date():
            raise FundDataError("Published NAV date is in the future")
        warnings = []
        profile = self._load_profile(code, name, script, source, warnings)
        if "货币" in profile.fund_type:
            raise FundDataError("Money-market fund NAV convention is unsupported")
        if not profile.fund_type:
            warnings.append("基金类型缺失，无法完整确认产品类别。")
        # Both endpoints are the same provider: consistency check, not a second
        # independent source. Disagreement fails closed, never averages values.
        if as_of is None:
            history_url = ("https://api.fund.eastmoney.com/f10/lsjz?"
                           f"fundCode={code}&pageIndex=1&pageSize=1")
            try:
                history = json.loads(self._get(history_url))
                if history.get("ErrCode") != 0:
                    raise FundDataError("Historical NAV source reports an error")
                latest = history["Data"]["LSJZList"][0]
                if latest.get("NAVTYPE") != "1":
                    raise FundDataError("Unexpected historical NAV type")
                confirmed = (date.fromisoformat(latest["FSRQ"]),
                             _number(latest["DWJZ"], positive=True))
            except (FundDataError, ValueError, KeyError, IndexError, TypeError):
                warnings.append("历史净值接口交叉核对不可用；净值仅取自同源净值走势。")
            else:
                if confirmed[0] != navs[-1].nav_date or abs(confirmed[1] - navs[-1].unit_nav) > 1e-8:
                    raise FundDataError("Published NAV endpoints disagree; analysis blocked")
        else:
            warnings.append("历史截止日只过滤净值；基金概况为当前资料，非历史时点快照。")
        holdings_url = ("https://fundf10.eastmoney.com/FundArchivesDatas.aspx?"
                        f"type=jjcc&code={code}&topline=10&year=&month=")
        holdings = None
        try:
            holdings = self._holdings(self._get(holdings_url), holdings_url, as_of)
        except (FundDataError, ValueError, TypeError):
            warnings.append("股票持仓接口请求或解析失败，不能视为零持仓。")
        if holdings is not None and holdings.report_period_end > now.astimezone(
                timezone(timedelta(hours=8))).date():
            holdings = None
            warnings.append("持仓报告期在未来，已拒绝该持仓数据。")
        if holdings is None:
            warnings.append("未取得可用股票持仓披露；不代表零持仓，也不代表基金或ETF穿透持仓。")
        else:
            warnings.append("持仓为定期报告期末股票明细，非实时仓位；实际公告日期未知，不能用于历史时点回测。")
        related_assets = []
        if as_of is None:
            related_assets = self._load_related_assets(
                profile, now.astimezone(timezone(timedelta(hours=8))).date(), warnings)
        return FundData(profile=profile, navs=navs, fetched_at=now,
                        source_url=source, holdings=holdings, warnings=warnings,
                        related_assets=related_assets)
