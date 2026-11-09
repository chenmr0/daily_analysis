"""Install daily-analysis requirements, isolating only manual funds-only runs."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"
MACRO_PACKAGE = b"fxmacrodata-public-client"
# Read only the distribution-name token, never match URL/comment substrings.
NAME = re.compile(rb"^[ \t]*([A-Za-z0-9][A-Za-z0-9._-]*)(?=[ \t\[<=>!~;@]|$)")


def fund_requirements(contents: bytes) -> bytes:
    """Remove one standalone named requirement; preserve every other byte.

    This deliberately supports the current one-line requirement, not a second
    pip parser. A missing/duplicate name or continued target needs review.
    """
    lines = contents.splitlines(keepends=True)
    matches = []
    continued = False
    for index, line in enumerate(lines):
        body = line.rstrip(b"\r\n")
        name = NAME.match(body)
        if name and re.sub(rb"[-_.]+", b"-", name[1]).lower() == MACRO_PACKAGE:
            if continued or body.endswith(b"\\"):
                raise ValueError("Macro requirement must be a standalone line")
            matches.append(index)
        continued = body.endswith(b"\\") and not body.lstrip().startswith(b"#")
    if len(matches) != 1:
        raise ValueError("Expected exactly one fxmacrodata-public-client requirement")
    del lines[matches[0]]
    return b"".join(lines)


def _pip_install(requirements: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements)], check=False
    ).returncode


def install_dependencies(env: Mapping[str, str], requirements: Path) -> int:
    if not (
        env.get("ANALYSIS_EVENT_NAME") == "workflow_dispatch"
        and env.get("ANALYSIS_INPUT_MODE") == "funds-only"
    ):
        # Schedules, stock modes and unknown contexts retain the full dependency set.
        return _pip_install(requirements)

    filtered = fund_requirements(requirements.read_bytes())
    temporary_path = None
    try:
        # Same directory preserves pip's relative -r/-c include resolution.
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=requirements.parent, prefix=".requirements-funds-", suffix=".txt", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(filtered)
        print("Manual funds-only: omitting fxmacrodata-public-client; all other requirements unchanged", flush=True)
        return _pip_install(temporary_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink()


def main() -> int:
    try:
        return install_dependencies(os.environ, REQUIREMENTS)
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
