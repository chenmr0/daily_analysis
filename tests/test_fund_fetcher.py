"""Deterministic fund transport/schema regressions, with synthetic data only."""
import io
import json
import unittest
from datetime import date
from unittest.mock import patch
from urllib.error import URLError

from data_provider.fund_fetcher import FundDataError, FundFetcher, _literal


CODE = "001234"
SCRIPT = ('var ishb=false;var fS_code="001234";var fS_name="测试基金C";'
          'var Data_netWorthTrend=[{"x":1704038400000,"y":1.0,"equityReturn":0,"unitMoney":""},'
          '{"x":1704124800000,"y":1.01,"equityReturn":1,"unitMoney":""}];'
          'var Data_ACWorthTrend=[[1704038400000,1.0],[1704124800000,1.01]];'
          'var Data_currentFundManager=[{"name":"测试经理"}];')
PROFILE = ('<table><tr><td>基金类型</td><td>混合型-偏股</td><td>发行日期</td></tr>'
           '<tr><td>成立日期/规模</td><td>2020年01月01日 / 1亿份</td></tr></table>')
HISTORY = json.dumps({"ErrCode": 0, "Data": {"LSJZList": [{"FSRQ": "2024-01-02", "DWJZ": "1.0100", "NAVTYPE": "1"}]}})
MARKUP = ('<h4>股票投资明细 截止至：<font>2023-12-31</font></h4>'
          '<table><tr><th>序号</th></tr><tr><td>1</td><td>000001</td><td>测试股票</td>'
          '<td></td><td></td><td>相关资讯</td><td>2.50%</td><td>10</td></tr></table>')
HOLDINGS = 'var apidata={content:' + json.dumps(MARKUP) + ',arryear:[2023],curyear:2023};'


def opener_for(script=SCRIPT, profile=PROFILE, history=HISTORY, holdings=HOLDINGS):
    def opener(request, timeout):
        assert timeout > 0
        url = request.full_url
        value = script if "pingzhongdata" in url else (
            profile if "jbgk_" in url else history if "lsjz?" in url else holdings)
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(value.encode("utf-8"))
    return opener


class FundFetcherTests(unittest.TestCase):
    def test_fetch_identity_nav_and_dated_holdings(self):
        result = FundFetcher(opener=opener_for()).fetch(CODE)
        self.assertEqual(result.profile.code, CODE)
        self.assertEqual(result.profile.fund_type, "混合型-偏股")
        self.assertEqual(result.profile.manager, "测试经理")
        self.assertEqual(result.profile.inception_date, date(2020, 1, 1))
        self.assertEqual(result.navs[-1].nav_date, date(2024, 1, 2))
        self.assertEqual(result.navs[-1].accumulated_nav, 1.01)
        self.assertEqual(result.holdings.report_period_end, date(2023, 12, 31))
        self.assertIsNone(result.holdings.published_at)
        self.assertEqual(result.holdings.holdings[0].weight_pct, 2.5)
        self.assertIsNone(result.profile.currency)

    def test_historical_cutoff_never_leaks_later_nav_or_holdings(self):
        result = FundFetcher(opener=opener_for()).fetch(CODE, date(2024, 1, 1))
        self.assertEqual(len(result.navs), 1)
        self.assertIsNone(result.holdings)
        self.assertTrue(any("当前资料" in warning for warning in result.warnings))

    def test_money_market_fund_rejected(self):
        for script, profile in [(SCRIPT.replace("ishb=false", "ishb=true"), PROFILE),
                                (SCRIPT, PROFILE.replace("混合型-偏股", "货币型"))]:
            with self.subTest(profile=profile), self.assertRaises(FundDataError):
                FundFetcher(opener=opener_for(script=script, profile=profile)).fetch(CODE)

    def test_error_status_or_unexpected_nav_type_never_confirm(self):
        for history in [HISTORY.replace('"ErrCode": 0', '"ErrCode": 1'),
                        HISTORY.replace('"NAVTYPE": "1"', '"NAVTYPE": "2"')]:
            result = FundFetcher(opener=opener_for(history=history)).fetch(CODE)
            self.assertTrue(any("交叉核对不可用" in warning for warning in result.warnings))

    def test_future_holdings_period_is_omitted(self):
        result = FundFetcher(opener=opener_for(holdings=HOLDINGS.replace(
            "2023-12-31", "2099-12-31"))).fetch(CODE)
        self.assertIsNone(result.holdings)
        self.assertTrue(any("未来" in warning for warning in result.warnings))

    def test_identity_mismatch_fails(self):
        with self.assertRaises(FundDataError):
            FundFetcher(opener=opener_for(script=SCRIPT.replace('"001234"', '"999999"'))).fetch(CODE)

    def test_html_success_response_fails_schema_validation(self):
        with self.assertRaises(FundDataError):
            FundFetcher(opener=opener_for(script="<html>页面未找到</html>")).fetch(CODE)

    def test_nav_endpoint_disagreement_fails_closed(self):
        with self.assertRaisesRegex(FundDataError, "disagree"):
            FundFetcher(opener=opener_for(history=HISTORY.replace("1.0100", "9.0000"))).fetch(CODE)

    def test_secondary_nav_endpoint_failure_is_explicit(self):
        result = FundFetcher(opener=opener_for(history="<html>bad</html>")).fetch(CODE)
        self.assertTrue(any("交叉核对不可用" in warning for warning in result.warnings))

    def test_empty_holdings_are_unknown_not_zero(self):
        result = FundFetcher(opener=opener_for(holdings='var apidata={content:"",arryear:[]};')).fetch(CODE)
        self.assertIsNone(result.holdings)
        self.assertTrue(any("不代表零持仓" in warning for warning in result.warnings))

    def test_failed_profile_retains_nav_and_marks_missing_type(self):
        result = FundFetcher(retries=0, opener=opener_for(profile=URLError("offline"))).fetch(CODE)
        self.assertEqual(result.profile.fund_type, "")
        self.assertEqual(len(result.navs), 2)
        self.assertTrue(any("概况请求失败" in warning for warning in result.warnings))

    def test_duplicate_dates_and_nonfinite_nav_rejected(self):
        for script in [SCRIPT.replace("1704124800000", "1704038400000"),
                       SCRIPT.replace('"y":1.01', '"y":NaN')]:
            with self.subTest(script=script), self.assertRaises(FundDataError):
                FundFetcher(opener=opener_for(script=script)).fetch(CODE)

    def test_numeric_code_cannot_lose_leading_zero(self):
        for code in [1234, "1234", "１２３４５６", "../../x"]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                FundFetcher(opener=opener_for()).fetch(code)

    def test_js_never_evaluated_and_string_semicolon_supported(self):
        self.assertEqual(_literal('var name="a;b";', 'name'), 'a;b')
        with self.assertRaises(FundDataError):
            _literal('var name=process.exit();', 'name')
        with self.assertRaises(FundDataError):
            _literal('var name="a"+evil();', 'name')

    @patch("data_provider.fund_fetcher.time.sleep")
    def test_bounded_same_source_retry(self, sleep):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            raise URLError("offline")
        with self.assertRaises(FundDataError):
            FundFetcher(retries=1, opener=opener).fetch(CODE)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
