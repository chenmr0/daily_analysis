"""Offline contracts for daily stock/fund dispatch and notification safety."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_daily_analysis", ROOT / ".github/scripts/run_daily_analysis.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def manual(mode="funds-only", **overrides):
    return {"ANALYSIS_EVENT_NAME": "workflow_dispatch", "ANALYSIS_INPUT_MODE": mode, **overrides}


@pytest.mark.parametrize("mode,flags", [
    ("full", []), ("market-only", ["--market-review"]),
    ("stocks-only", ["--no-market-review"]), ("funds-only", ["--funds"]),
])
def test_one_command_for_each_mode(mode, flags):
    command, _ = runner.plan_command(manual(mode))
    assert command == [runner.sys.executable, "main.py", *flags]
    command, _ = runner.plan_command(manual(mode, ANALYSIS_FORCE_RUN="true"))
    assert command == [runner.sys.executable, "main.py", *flags, "--force-run"]


def test_stock_schedule_remains_full_even_with_fund_configuration():
    command, _ = runner.plan_command({
        "ANALYSIS_EVENT_NAME": "schedule", "ANALYSIS_SCHEDULE": "0 10 * * 1-5",
        "FUND_LIST": "000001", "DAILY_ANALYSIS_MODE": "funds-only",
        "ANALYSIS_INPUT_MODE": "funds-only",
    })
    assert command == [runner.sys.executable, "main.py"]


@pytest.mark.parametrize("mode", ["", " ", "unknown", "FULL", 'funds-only; touch /tmp/injected', '$(false)'])
def test_invalid_manual_mode_fails_without_execution(mode):
    with patch.dict(runner.os.environ, manual(mode), clear=True), patch.object(runner.subprocess, "run") as run:
        assert runner.main() == 2
        run.assert_not_called()


@pytest.mark.parametrize("attempt", ["2", "3", "100"])
def test_fund_reruns_disable_notifications(attempt):
    command, notices = runner.plan_command(manual(ANALYSIS_RUN_ATTEMPT=attempt))
    assert command == [runner.sys.executable, "main.py", "--funds", "--no-notify"]
    assert any("uncertain send" in notice for notice in notices)


def test_new_manual_run_can_send_and_explicit_no_notify_is_respected():
    assert "--no-notify" not in runner.plan_command(manual(ANALYSIS_RUN_ATTEMPT="1"))[0]
    command, _ = runner.plan_command(manual(ANALYSIS_NO_NOTIFY="true", ANALYSIS_FUND_NO_NEWS="true"))
    assert command == [runner.sys.executable, "main.py", "--funds", "--no-notify", "--fund-no-news"]


def test_stock_reruns_keep_existing_notification_behavior():
    command, _ = runner.plan_command(manual("full", ANALYSIS_RUN_ATTEMPT="2"))
    assert "--no-notify" not in command
    assert "--no-notify" in runner.plan_command(manual("full", ANALYSIS_NO_NOTIFY="true"))[0]


@pytest.mark.parametrize("env", [
    manual(ANALYSIS_NO_NOTIFY="$(false)"), manual(ANALYSIS_FORCE_RUN="yes"),
    manual(ANALYSIS_FUND_NO_NEWS="1"), manual(ANALYSIS_RUN_ATTEMPT="zero"),
    manual(ANALYSIS_RUN_ATTEMPT="0"), manual("full", ANALYSIS_FUND_NO_NEWS="true"),
    {"ANALYSIS_EVENT_NAME": "push"},
    {"ANALYSIS_EVENT_NAME": "schedule", "ANALYSIS_SCHEDULE": "30 14 * * 1-5"},
])
def test_invalid_context_fails_closed(env):
    with pytest.raises(ValueError):
        runner.plan_command(env)


@pytest.mark.parametrize("code", [0, 1, 2])
def test_invocation_once_and_exit_status_propagation(code):
    with patch.dict(runner.os.environ, manual(), clear=True), patch.object(
        runner.subprocess, "run", return_value=SimpleNamespace(returncode=code)
    ) as run:
        assert runner.main() == code
        run.assert_called_once_with([runner.sys.executable, "main.py", "--funds"], check=False)


def test_workflow_wiring_preserves_stock_schedule_and_existing_services():
    path = ROOT / ".github/workflows/00-daily-analysis.yml"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"] == [{"cron": "0 10 * * 1-5"}]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["default"] == "full"
    assert inputs["mode"]["options"] == ["full", "market-only", "stocks-only", "funds-only"]
    for name in ("force_run", "no_notify", "fund_no_news"):
        assert inputs[name]["type"] == "boolean"
        assert inputs[name]["default"] is False
    assert inputs["fund_codes"]["type"] == "string"
    assert inputs["fund_codes"]["default"] == ""
    assert inputs["fund_codes"]["required"] is False
    job = workflow["jobs"]["analyze"]
    assert "if" not in job
    step = next(step for step in job["steps"] if step["name"] == "执行股票分析")
    env = step["env"]
    assert env["FUND_LIST"] == "${{ vars.FUND_LIST || secrets.FUND_LIST }}"
    assert "DAILY_ANALYSIS_MODE" not in env
    assert env["ANALYSIS_INPUT_MODE"] == "${{ inputs.mode }}"
    assert env["ANALYSIS_FUND_CODES"] == "${{ inputs.fund_codes }}"
    assert env["ANALYSIS_RUN_ATTEMPT"] == "${{ github.run_attempt }}"
    assert env["ANALYSIS_EVENT_NAME"] == "${{ github.event_name }}"
    assert env["ANALYSIS_SCHEDULE"] == "${{ github.event.schedule }}"
    for name in ("force_run", "no_notify", "fund_no_news"):
        assert env[f"ANALYSIS_{name.upper()}"] == "${{ inputs." + name + " || 'false' }}"
    for key in ("EMAIL_SENDER", "EMAIL_PASSWORD", "EMAIL_RECEIVERS", "TAVILY_API_KEYS", "BOCHA_API_KEYS"):
        assert key in env
    assert step["run"].count("python .github/scripts/run_daily_analysis.py") == 1
    assert "python main.py" not in step["run"]
    assert "${{" not in step["run"]
    artifact = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert artifact["if"] == "always()"
    assert artifact["with"]["path"].split() == ["reports/", "logs/"]


@pytest.mark.parametrize("codes", ["021190,018123", " 021190,018123 ", "000001; touch /tmp/injected", "$(false)"])
def test_explicit_fund_codes_passed_as_one_argument(codes):
    env = manual(ANALYSIS_FUND_CODES=codes, FUND_LIST="000002")
    command, _ = runner.plan_command(env)
    assert command == [runner.sys.executable, "main.py", "--funds=" + codes]
    with patch.dict(runner.os.environ, env, clear=True), patch.object(
        runner.subprocess, "run", return_value=SimpleNamespace(returncode=2)
    ) as run:
        assert runner.main() == 2
        run.assert_called_once_with(command, check=False)


@pytest.mark.parametrize("codes", ["", " ", "\t"])
def test_blank_fund_codes_leave_config_resolution_to_main(codes):
    command, _ = runner.plan_command(manual(ANALYSIS_FUND_CODES=codes, FUND_LIST="000002"))
    assert command == [runner.sys.executable, "main.py", "--funds"]


@pytest.mark.parametrize("mode", ["full", "stocks-only", "market-only"])
def test_explicit_fund_codes_rejected_in_stock_modes(mode):
    with pytest.raises(ValueError, match="fund_codes requires funds-only"):
        runner.plan_command(manual(mode, ANALYSIS_FUND_CODES="000001"))


@pytest.mark.parametrize("codes", ["--no-notify", "--fund-no-news", "--stocks=600519"])
def test_option_shaped_fund_codes_are_bound_and_rejected_by_real_parser(codes):
    import main as application

    command, _ = runner.plan_command(manual(ANALYSIS_FUND_CODES=codes, FUND_LIST="000002"))
    assert command == [runner.sys.executable, "main.py", "--funds=" + codes]
    with patch.object(application.sys, "argv", command[1:]), pytest.raises(SystemExit) as exc:
        application.parse_arguments()
    assert exc.value.code == 2
