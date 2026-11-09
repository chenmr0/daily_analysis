"""Select exactly one daily analysis command without shell interpolation."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping

STOCK_SCHEDULE = "0 10 * * 1-5"
MODE_ARGS = {
    "full": [],
    "market-only": ["--market-review"],
    "stocks-only": ["--no-market-review"],
    "funds-only": ["--funds"],
}


def _boolean(env: Mapping[str, str], key: str) -> bool:
    value = env.get(key, "false")
    if value not in {"true", "false"}:
        raise ValueError(f"{key} must be true or false")
    return value == "true"


def plan_command(env: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Validate event/mode and return one argv plus non-sensitive notices."""
    event = env.get("ANALYSIS_EVENT_NAME")
    if event == "workflow_dispatch":
        mode = env.get("ANALYSIS_INPUT_MODE", "")
    elif event == "schedule":
        mode = "full"
    else:
        raise ValueError("Unsupported analysis event")
    if mode not in MODE_ARGS:
        raise ValueError("Invalid analysis mode; use full, market-only, stocks-only or funds-only")
    if event == "schedule":
        if env.get("ANALYSIS_SCHEDULE") != STOCK_SCHEDULE:
            raise ValueError("Schedule does not match analysis mode")
    try:
        attempt = int(env.get("ANALYSIS_RUN_ATTEMPT", "1"))
    except ValueError as exc:
        raise ValueError("Invalid run attempt") from exc
    if attempt < 1:
        raise ValueError("Invalid run attempt")
    force_run = _boolean(env, "ANALYSIS_FORCE_RUN")
    no_notify = _boolean(env, "ANALYSIS_NO_NOTIFY")
    no_news = _boolean(env, "ANALYSIS_FUND_NO_NEWS")
    no_ai = _boolean(env, "ANALYSIS_FUND_NO_AI")
    if no_ai and mode != "funds-only":
        raise ValueError("fund_no_ai requires funds-only mode")
    if no_news and mode != "funds-only":
        raise ValueError("fund_no_news requires funds-only mode")
    fund_codes = env.get("ANALYSIS_FUND_CODES", "")
    if fund_codes.strip() and mode != "funds-only":
        raise ValueError("fund_codes requires funds-only mode")
    command = [sys.executable, "main.py", *MODE_ARGS[mode]]
    if fund_codes.strip():
        # Bind the optional value even when it starts with a CLI option prefix.
        command[-1] = "--funds=" + fund_codes
    notices = [f"Analysis mode: {mode}"]
    if force_run:
        command.append("--force-run")
        notices.append("Force run enabled (skip trading-day check)")
    if mode == "funds-only" and attempt > 1:
        no_notify = True
        notices.append("Fund workflow rerun: notifications disabled to avoid repeating an uncertain send")
    if no_notify:
        command.append("--no-notify")
    if no_news:
        command.append("--fund-no-news")
    if no_ai:
        command.append("--fund-no-ai")
    return command, notices


def main() -> int:
    try:
        command, notices = plan_command(os.environ)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    for notice in notices:
        print(notice, flush=True)
    # A failed fund analysis must never invoke the stock path or retry a send.
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
