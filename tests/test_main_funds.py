import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import main
from src.services.stock_list_parser import ParseStatus, parse_analysis_target


@pytest.mark.parametrize('flag', ['--stocks', '--portfolio', '--schedule', '--market-review', '--etf-rotation',
                                  '--backtest', '--serve', '--serve-only', '--webui', '--check-notify'])
def test_fund_mode_conflicts_are_explicit(flag):
    argv = ['main.py', '--funds', '001672', flag]
    if flag == '--stocks':
        argv.append('600519')
    if flag == '--portfolio':
        argv.append('futu')
    with patch.object(sys, 'argv', argv), pytest.raises(SystemExit) as exc:
        main.parse_arguments()
    assert exc.value.code == 2


def test_fund_prefix_never_becomes_stock():
    assert parse_analysis_target('fund:001672').asset_type == ParseStatus.UNSUPPORTED
    assert parse_analysis_target('001672').asset_type == ParseStatus.STOCK
    assert parse_analysis_target('510300').asset_type == ParseStatus.STOCK


@pytest.mark.parametrize('flags,send,news', [
    (['--no-notify'], False, True),
    (['--dry-run'], False, False),
    (['--fund-no-news'], True, False),
])
def test_main_fund_path_bypasses_stock_runtime(flags, send, news):
    config = SimpleNamespace(log_dir='logs')
    report = SimpleNamespace(analyses=[object()], failed_codes={}, notification_status='disabled')
    with patch.object(sys, 'argv', ['main.py', '--funds', '001672', *flags]), \
            patch.object(main, '_setup_bootstrap_logging'), patch.object(main, '_setup_runtime_logging'), \
            patch.object(main, 'get_config', return_value=config), \
            patch.object(main, '_refresh_stock_index_cache_for_analysis', side_effect=AssertionError('no stocks')), \
            patch.object(main, 'run_full_analysis', side_effect=AssertionError('no stocks')), \
            patch('src.services.fund_analysis_service.run_fund_analysis', return_value=report) as runner:
        assert main.main() == 0
        runner.assert_called_once_with('001672', send_notification=send, include_news=news)


def test_fund_no_news_requires_fund_mode():
    with patch.object(sys, 'argv', ['main.py', '--fund-no-news']), pytest.raises(SystemExit):
        main.parse_arguments()


def test_email_subject_is_optional_and_preserves_stock_defaults():
    from src.notification import NotificationChannel, NotificationService
    service = object.__new__(NotificationService)
    service._stock_email_groups = []
    service._should_use_image_for_channel = Mock(return_value=False)
    service.send_to_email = Mock(return_value=True)
    kwargs = dict(image_bytes=None, email_stock_codes=None, email_send_to_all=False, route_type='report')
    service._send_to_static_channel(NotificationChannel.EMAIL, 'report', **kwargs)
    service.send_to_email.assert_called_with('report', receivers=None)
    service._send_to_static_channel(NotificationChannel.EMAIL, 'report', **kwargs, email_subject='基金日报')
    service.send_to_email.assert_called_with('report', receivers=None, subject='基金日报')
    service._should_use_image_for_channel.return_value = True
    service._send_email_with_inline_image = Mock(return_value=True)
    service._send_to_static_channel(NotificationChannel.EMAIL, 'report', **kwargs, email_subject='基金日报')
    service._send_email_with_inline_image.assert_called_with(None, receivers=None, subject='基金日报')


def test_bare_fund_switch_resolves_config_once_without_stock_runtime():
    config = SimpleNamespace(log_dir='logs', fund_list=['fund:001672', '021190', '001672'])
    report = SimpleNamespace(analyses=[object()], failed_codes={}, notification_status='disabled')
    with patch.object(sys, 'argv', ['main.py', '--funds', '--no-notify', '--fund-no-news']), \
            patch.object(main, '_setup_bootstrap_logging'), patch.object(main, '_setup_runtime_logging'), \
            patch.object(main, 'get_config', return_value=config), \
            patch.object(main, 'run_full_analysis', side_effect=AssertionError('no stocks')), \
            patch('src.services.fund_analysis_service.run_fund_analysis', return_value=report) as runner:
        assert main.main() == 0
        runner.assert_called_once_with(['001672', '021190'], send_notification=False, include_news=False)


@pytest.mark.parametrize('funds', [[], ['invalid'], ['001672.SH']])
def test_bad_fund_config_fails_without_fallback_or_notification(funds):
    config = SimpleNamespace(log_dir='logs', fund_list=funds, stock_list=['600519'])
    with patch.object(sys, 'argv', ['main.py', '--funds']), \
            patch.object(main, '_setup_bootstrap_logging'), patch.object(main, '_setup_runtime_logging'), \
            patch.object(main, 'get_config', return_value=config), \
            patch.object(main, 'run_full_analysis', side_effect=AssertionError('no fallback')), \
            patch('src.services.fund_analysis_service.run_fund_analysis') as runner:
        assert main.main() == 1
        runner.assert_not_called()


def test_explicit_fund_codes_override_invalid_fund_config():
    config = SimpleNamespace(log_dir='logs', fund_list=['invalid'])
    report = SimpleNamespace(analyses=[object()], failed_codes={}, notification_status='disabled')
    with patch.object(sys, 'argv', ['main.py', '--funds', '001672', '--no-notify']), \
            patch.object(main, '_setup_bootstrap_logging'), patch.object(main, '_setup_runtime_logging'), \
            patch.object(main, 'get_config', return_value=config), \
            patch('src.services.fund_analysis_service.run_fund_analysis', return_value=report) as runner:
        assert main.main() == 0
        runner.assert_called_once_with('001672', send_notification=False, include_news=True)


@pytest.mark.parametrize('status', ['unknown', 'all_failed', 'partial_failed', 'unavailable', 'no_channel'])
def test_fund_notification_failure_is_not_a_green_actions_run(status):
    config = SimpleNamespace(log_dir='logs')
    report = SimpleNamespace(analyses=[object()], failed_codes={}, notification_status=status)
    with patch.object(sys, 'argv', ['main.py', '--funds', '001672']), \
            patch.object(main, '_setup_bootstrap_logging'), patch.object(main, '_setup_runtime_logging'), \
            patch.object(main, 'get_config', return_value=config), \
            patch('src.services.fund_analysis_service.run_fund_analysis', return_value=report):
        assert main.main() == 1
