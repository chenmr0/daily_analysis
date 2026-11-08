"""Fund configuration is opt-in and isolated from the stock watchlist."""
import os
from unittest.mock import patch

from src.config import Config
from src.core.config_registry import WEB_SETTINGS_HIDDEN_FROM_UI


def load_env(values):
    with patch.dict(os.environ, values, clear=True), patch('src.config.setup_env'), \
            patch.object(Config, '_parse_litellm_yaml', return_value=[]), \
            patch.object(Config, '_parse_stock_email_groups', return_value=[]):
        return Config._load_from_env()


def test_fund_list_defaults_empty_without_changing_stocks():
    config = load_env({'STOCK_LIST': '600519,000001'})
    assert config.fund_list == []
    assert config.stock_list == ['600519', '000001']


def test_fund_list_preserves_identity_and_leading_zeroes():
    config = load_env({'STOCK_LIST': '600519', 'FUND_LIST': '021190，fund:001672 018123;020356'})
    assert config.fund_list == ['021190', 'fund:001672', '018123', '020356']
    assert config.stock_list == ['600519']


def test_unused_bad_fund_configuration_does_not_break_stock_config():
    config = load_env({'STOCK_LIST': '600519', 'FUND_LIST': 'invalid,001672.SH'})
    assert config.stock_list == ['600519']
    assert config.fund_list == ['invalid', '001672.SH']


def test_fund_config_is_hidden_from_web_settings():
    assert 'FUND_LIST' in WEB_SETTINGS_HIDDEN_FROM_UI
