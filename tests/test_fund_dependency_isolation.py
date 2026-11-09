"""Run the real fund entry path without the stock-only macro package, offline."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fund_main_news_ai_and_notification_route_without_macro_package(tmp_path):
    # A fresh interpreter prevents earlier tests' imported modules masking a dependency.
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("")
    env = {
        "PATH": os.defpath, "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
        "ENV_FILE": str(empty_env), "PYTHONDONTWRITEBYTECODE": "1", "USE_PROXY": "false",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True", "AGENT_MODE": "true", "AGENT_ARCH": "multi",
    }
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve())], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FUND_WITHOUT_MACRO_OK provider=1 news=2 email_transport=1" in result.stdout


def _run_isolated_probe():
    import importlib.abc
    import json
    import runpy
    import tempfile
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import patch
    from zoneinfo import ZoneInfo

    sys.path.insert(0, str(ROOT))
    tempfile.tempdir = os.environ["TMPDIR"]
    attempts = []
    blocked_operations = []

    class MissingMacro(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "fxmacrodata_public" or fullname.startswith("fxmacrodata_public."):
                attempts.append(fullname)
                raise ModuleNotFoundError("Test simulates missing macro package", name="fxmacrodata_public")

    assert not any(name.startswith("fxmacrodata_public") for name in sys.modules)
    sys.meta_path.insert(0, MissingMacro())

    def offline_guard(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            blocked_operations.append(event)
            raise AssertionError("Offline probe blocks external operation: " + event)

    sys.addaudithook(offline_guard)
    import main
    from src.analyzer import GeminiAnalyzer
    from src.config import get_config
    from src.notification import NotificationService
    from src.search_service import SearchResponse, get_search_service

    # Reuse the existing deterministic data/model fixtures, not a second contract.
    fixtures = runpy.run_path(str(ROOT / "tests/test_fund_interpretation.py"))
    config = get_config()
    assert config.agent_mode is True and config.agent_arch == "multi"
    config.litellm_model = "openai/offline-fixture"
    config.litellm_fallback_models = []
    config.llm_model_list = []
    config.generation_backend = "litellm"
    config.generation_fallback_backend = ""
    config.openai_base_url = ""
    config.bocha_api_keys = ["offline-fixture"]
    config.email_sender = "sender@example.invalid"
    config.email_password = "offline-fixture"
    config.email_receivers = ["receiver@example.invalid"]
    config.notification_report_channels = ["email"]
    now = datetime.now(timezone.utc)
    data = fixtures["analysis"](end=now.astimezone(ZoneInfo("Asia/Shanghai")).date()).data
    data.fetched_at = now
    response = SimpleNamespace(model="offline-fixture", usage=None, choices=[
        SimpleNamespace(message=SimpleNamespace(content=json.dumps(fixtures["valid_output"]())))
    ])
    writes = {}

    def memory_write(path, text, **kwargs):
        writes[str(path)] = text
        return len(text)

    # Service initialization, news filtering, model adapter and notification routing
    # are real. Mock data/provider/transport boundaries and output writes only.
    with patch.object(sys, "argv", ["main.py", "--funds", "000001"]), \
            patch.object(main, "_setup_bootstrap_logging"), patch.object(main, "_setup_runtime_logging"), \
            patch("data_provider.fund_fetcher.FundFetcher.fetch", return_value=data) as fetch, \
            patch("src.search_service._call_topic_news_in_subprocess", return_value=SearchResponse(
                query="offline-fixture", results=[], provider="offline-fixture", success=True,
            )) as news, \
            patch.object(GeminiAnalyzer, "_dispatch_litellm_completion", return_value=response) as provider, \
            patch.object(NotificationService, "send_to_email", return_value=True) as email, \
            patch("src.analyzer.persist_llm_usage"), \
            patch.object(Path, "mkdir"), patch.object(Path, "write_text", memory_write), \
            patch.object(Path, "replace"), \
            patch.object(main, "run_full_analysis", side_effect=AssertionError("No stock route")), \
            patch.object(main, "_refresh_stock_index_cache_for_analysis", side_effect=AssertionError("No stocks")):
        assert main.main() == 0
        snapshot = json.loads(next(text for path, text in writes.items() if path.endswith(".json.tmp")))
        assert snapshot["interpretation"]["status"] == "ok"
        assert snapshot["notification"]["status"] == "sent"  # Mock transport result, no SMTP connection.
        assert snapshot["notification"]["channels"] == [{"channel": "email", "success": True, "error_code": None}]
        fetch.assert_called_once_with("000001")
        assert get_search_service().is_available
        assert provider.call_count == 1 and news.call_count == 2 and email.call_count == 1
        assert "基金" in email.call_args.kwargs["subject"]
        assert attempts == []
        assert blocked_operations == []

    # Verify the blocker really makes the installed package unavailable in this process.
    try:
        importlib.import_module("fxmacrodata_public")
    except ModuleNotFoundError:
        assert attempts == ["fxmacrodata_public"]
    else:
        raise AssertionError("Macro package was not blocked")
    print("FUND_WITHOUT_MACRO_OK provider=1 news=2 email_transport=1")


if __name__ == "__main__":
    _run_isolated_probe()
