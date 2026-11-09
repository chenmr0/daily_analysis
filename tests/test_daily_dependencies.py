"""Offline contracts for dependency isolation in the daily analysis workflow."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/scripts/install_daily_dependencies.py"
SPEC = importlib.util.spec_from_file_location("install_daily_dependencies", HELPER)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
MANUAL_FUNDS = {"ANALYSIS_EVENT_NAME": "workflow_dispatch", "ANALYSIS_INPUT_MODE": "funds-only"}
MACRO = b"fxmacrodata-public-client @ https://example.invalid/macro.whl#sha256=abc123\n"


def install_step():
    workflow = yaml.safe_load((ROOT / ".github/workflows/00-daily-analysis.yml").read_text())
    return next(step for step in workflow["jobs"]["analyze"]["steps"] if step["name"] == "安装依赖")


@pytest.mark.parametrize("env", [
    {}, {"ANALYSIS_INPUT_MODE": "funds-only"},
    {"ANALYSIS_EVENT_NAME": "schedule", "ANALYSIS_INPUT_MODE": "funds-only"},
    {"ANALYSIS_EVENT_NAME": "push", "ANALYSIS_INPUT_MODE": "funds-only"},
    *({**MANUAL_FUNDS, "ANALYSIS_INPUT_MODE": mode} for mode in (
        "full", "market-only", "stocks-only", "", "unknown", "FUNDS-ONLY", " funds-only ",
        "funds-only; touch /tmp/injected", "$(false)",
    )),
])
def test_all_other_contexts_install_original_requirements_without_filtering(tmp_path, env):
    requirements = tmp_path / "requirements.txt"
    # Even a missing macro entry must not affect non-fund installs.
    requirements.write_bytes(b"other-package==1\r\n")
    with patch.object(installer.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
        assert installer.install_dependencies(env, requirements) == 0
    run.assert_called_once_with(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements)], check=False
    )
    assert list(tmp_path.iterdir()) == [requirements]


@pytest.mark.parametrize("spelling", [
    b"fxmacrodata-public-client", b"FXMacroData_Public_Client", b"fxmacrodata.public.client",
])
def test_exact_distribution_name_only_and_other_bytes_preserved(spelling):
    others = (
        "# 中文注释和 fxmacrodata-public-client 不应被删除\r\n".encode()
        + b"# fxmacrodata-public-client @ https://example.invalid/comment.whl\r\n"
        + b"not-fxmacrodata-public-client==1\r\n"
        + b"fxmacrodata-public-client-extra==1\r\n"
        + b"fxmacrodata-public-client2==1\r\n"
        + b"other @ https://example.invalid/fxmacrodata-public-client.whl#sha256=abc123 ; python_version > '3.10'\r\n"
        + b"another==1 \\\r\n    --hash=sha256:deadbeef\r\n"
        + b"-r nested/requirements.txt\r\n-c nested/constraints.txt\r\n"
    )
    target = spelling + b" @ https://example.invalid/macro.whl#sha256=abc123\r\n"
    assert installer.fund_requirements(others + target + b"last==2") == others + b"last==2"


@pytest.mark.parametrize("contents", [
    b"other==1\n", b"# fxmacrodata-public-client==1\n",
    MACRO + MACRO, MACRO + b"FXMACRODATA_public_client==0.1\n",
    b"fxmacrodata-public-client @ \\\n  https://example.invalid/macro.whl\n",
    b"fxmacrodata-public-client==0.1 \\\n  --hash=sha256:abc123\n",
    b"other==1 \\\n" + MACRO,
    b"fxmacrodata-public-\\\nclient==0.1\n",
])
def test_missing_duplicate_or_continued_macro_fails_before_pip(tmp_path, contents):
    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(contents)
    with patch.dict(installer.os.environ, MANUAL_FUNDS, clear=True), patch.object(
        installer, "REQUIREMENTS", requirements
    ), patch.object(installer.subprocess, "run") as run:
        assert installer.main() == 2
    run.assert_not_called()
    assert requirements.read_bytes() == contents
    assert list(tmp_path.iterdir()) == [requirements]


def test_comment_backslash_is_not_a_requirement_continuation():
    comment = b"# comment ending with a backslash \\\n"
    assert installer.fund_requirements(comment + MACRO) == comment


@pytest.mark.parametrize("returncode", [0, 1, 2, 17])
@pytest.mark.parametrize("funds_only", [True, False])
def test_pip_return_code_and_cleanup_on_success_or_failure(tmp_path, returncode, funds_only):
    requirements = tmp_path / "requirements.txt"
    original = b"other==1\n" + MACRO
    requirements.write_bytes(original)
    installed = []

    def fake_pip(command, **kwargs):
        assert command[:5] == [sys.executable, "-m", "pip", "install", "-r"]
        assert kwargs == {"check": False}
        selected = Path(command[-1])
        installed.append(selected)
        assert selected.parent == requirements.parent
        assert selected.read_bytes() == (b"other==1\n" if funds_only else original)
        return SimpleNamespace(returncode=returncode)

    env = MANUAL_FUNDS if funds_only else {**MANUAL_FUNDS, "ANALYSIS_INPUT_MODE": "full"}
    with patch.dict(installer.os.environ, env, clear=True), patch.object(
        installer, "REQUIREMENTS", requirements
    ), patch.object(installer.subprocess, "run", side_effect=fake_pip) as run:
        assert installer.main() == returncode
    assert run.call_count == 1
    assert installed[0].exists() is (not funds_only)
    assert requirements.read_bytes() == original
    assert list(tmp_path.iterdir()) == [requirements]


def test_subprocess_start_failure_still_cleans_temporary_file(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(MACRO)
    with patch.dict(installer.os.environ, MANUAL_FUNDS, clear=True), patch.object(
        installer, "REQUIREMENTS", requirements
    ), patch.object(installer.subprocess, "run", side_effect=OSError("test start failure")):
        assert installer.main() == 2
    assert list(tmp_path.iterdir()) == [requirements]


def test_interruption_still_cleans_temporary_file(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(MACRO)
    with patch.object(installer.subprocess, "run", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
        installer.install_dependencies(MANUAL_FUNDS, requirements)
    assert list(tmp_path.iterdir()) == [requirements]


def test_nested_relative_includes_and_constraints_keep_real_pip_semantics(tmp_path):
    from pip._internal.network.session import PipSession
    from pip._internal.req.req_file import parse_requirements

    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(b"-r nested/requirements.txt\n-c nested/constraints.txt\n" + MACRO)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "requirements.txt").write_text("-r deeper/more.txt\n")
    (nested / "deeper").mkdir()
    (nested / "deeper/more.txt").write_text("some-package==1\n")
    (nested / "constraints.txt").write_text("another-package<2\n")

    def fake_pip(requirement_path):
        with PipSession() as session, patch.object(session, "request", side_effect=AssertionError("No network")):
            parsed = list(parse_requirements(str(requirement_path), session=session))
        assert [(item.requirement, item.constraint) for item in parsed] == [
            ("some-package==1", False), ("another-package<2", True),
        ]
        return 0

    with patch.object(installer, "_pip_install", side_effect=fake_pip):
        assert installer.install_dependencies(MANUAL_FUNDS, requirements) == 0
    assert not list(tmp_path.glob(".requirements-funds-*"))


def test_real_requirements_filter_removes_only_the_macro_line():
    original = (ROOT / "requirements.txt").read_bytes()
    target = next(line for line in original.splitlines(keepends=True) if line.startswith(b"fxmacrodata-public-client @"))
    assert b"#sha256=" in target
    assert installer.fund_requirements(original) == original.replace(target, b"", 1)


def test_workflow_installs_with_existing_mode_context_before_analysis():
    step = install_step()
    assert step["env"] == {
        "ANALYSIS_EVENT_NAME": "${{ github.event_name }}",
        "ANALYSIS_INPUT_MODE": "${{ inputs.mode }}",
    }
    assert step["run"].splitlines() == [
        "pip install --upgrade pip",
        "python .github/scripts/install_daily_dependencies.py",
        'python -c "import futu; print(\'✅ futu SDK import OK\')"',
    ]
    assert "${{" not in step["run"]
    assert "continue-on-error" not in step


@pytest.mark.parametrize("pip_code,expected_smoke", [(0, True), (17, False)])
def test_real_workflow_shell_propagates_installer_failure_without_running_futu(tmp_path, pip_code, expected_smoke):
    """Exercise bash -e and the real helper using a local fake pip, never installing."""
    helper = tmp_path / ".github/scripts/install_daily_dependencies.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(HELPER.read_bytes())
    (tmp_path / "requirements.txt").write_bytes(MACRO + b"other==1\n")
    fake = tmp_path / "fake"
    fake.mkdir()
    log = tmp_path / "calls.txt"
    (fake / "pip").write_text('#!/bin/sh\nprintf "upgrade\\n" >> "$DEPENDENCY_TEST_LOG"\n')
    (fake / "pip").chmod(0o700)
    (fake / "pip.py").write_text(
        "import os, sys\nfrom pathlib import Path\n"
        "with Path(os.environ['DEPENDENCY_TEST_LOG']).open('a') as log: log.write('install\\n')\n"
        "assert sys.argv[1:3] == ['install', '-r']\n"
        "assert Path(sys.argv[3]).read_bytes() == b'other==1\\n'\n"
        f"sys.exit({pip_code})\n"
    )
    (fake / "futu.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "with Path(os.environ['DEPENDENCY_TEST_LOG']).open('a') as log: log.write('futu\\n')\n"
    )
    env = {
        **os.environ, **MANUAL_FUNDS, "PYTHONPATH": str(fake), "DEPENDENCY_TEST_LOG": str(log),
        "PATH": os.pathsep.join([str(fake), str(Path(sys.executable).parent), os.environ.get("PATH", "")]),
    }
    result = subprocess.run(["bash", "-e", "-c", install_step()["run"]], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == pip_code, result.stderr.decode()
    assert log.read_text().splitlines() == ["upgrade", "install"] + (["futu"] if expected_smoke else [])
    assert not list(tmp_path.glob(".requirements-funds-*"))
