"""Tests for the verifier-side pytest runner.

Each case writes a real test file into a temporary workspace and runs real
pytest, so the reported summary and exit codes are the ones the caller will see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.test_runner import MAX_OUTPUT_CHARS, _decode, run_pytest


def write(workspace: Path, relative: str, text: str) -> Path:
    target = workspace / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def test_passing_suite_is_reported_as_passed(tmp_path: Path) -> None:
    write(tmp_path, "test_ok.py", "def test_adds():\n    assert 1 + 1 == 2\n")

    result = run_pytest(tmp_path, "test_ok.py")

    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert "1 passed" in result["summary"]


def test_failing_suite_is_reported_with_its_assertion(tmp_path: Path) -> None:
    write(tmp_path, "test_bad.py", "def test_wrong():\n    assert 1 + 1 == 3\n")

    result = run_pytest(tmp_path, "test_bad.py")

    assert result["passed"] is False
    assert result["exit_code"] == 1
    assert "assert" in result["output"]


def test_collecting_nothing_is_not_a_pass(tmp_path: Path) -> None:
    write(tmp_path, "test_empty.py", "VALUE = 1\n")

    result = run_pytest(tmp_path, "test_empty.py")

    assert result["passed"] is False
    assert result["exit_code"] == 5
    assert result["summary"] == "no tests collected"


def test_slow_suite_times_out_instead_of_hanging(tmp_path: Path) -> None:
    # Writes to stderr before sleeping: on the timeout path subprocess reports the
    # captured streams as bytes even with text=True, so this case fails loudly if the
    # decoding regresses.
    write(
        tmp_path,
        "test_slow.py",
        "import sys, time\n"
        "\n"
        "def test_sleeps():\n"
        "    print('starting', file=sys.stderr, flush=True)\n"
        "    time.sleep(30)\n",
    )

    result = run_pytest(tmp_path, "test_slow.py", timeout_sec=1.0)

    assert result["passed"] is False
    assert result["exit_code"] is None
    assert "timeout" in result["summary"]
    # The regression was a TypeError while concatenating the captured streams, so the
    # contract under test is "a str comes back", not "the child's text survives".
    # pytest captures the test function's own stderr, so only plugin-level output
    # reaches the pipe before the timeout fires.
    assert isinstance(result["output"], str)


def test_target_outside_the_workspace_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_test.py"
    outside.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    result = run_pytest(tmp_path, "../outside_test.py")

    assert result["passed"] is False
    assert result["exit_code"] is None
    assert "invalid target" in result["summary"]


def test_missing_target_is_reported_not_raised(tmp_path: Path) -> None:
    result = run_pytest(tmp_path, "test_missing.py")

    assert result["passed"] is False
    assert "does not exist" in result["summary"]


def test_long_output_is_clipped(tmp_path: Path) -> None:
    body = "\n".join(f'    print("line {i}")' for i in range(2000))
    write(tmp_path, "test_noisy.py", f"def test_noise():\n{body}\n    assert True\n")

    result = run_pytest(tmp_path, "test_noisy.py")

    assert result["passed"] is True
    assert len(str(result["output"])) <= MAX_OUTPUT_CHARS + 80


def test_decode_normalises_the_timeout_stream_types() -> None:
    # subprocess reports captured output as bytes on the timeout path even with
    # text=True. This guard is platform-independent, unlike the timeout test above.
    assert _decode(b"warning on stderr") == "warning on stderr"
    assert _decode(None) == ""
    assert _decode("already text") == "already text"


def _docker_ready() -> bool:
    import os
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        return False
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if info.returncode != 0:
            return False
        image = os.environ.get("CODE_EXEC_IMAGE", "").strip() or "agentverse-sandbox:latest"
        probe = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True, timeout=20
        )
        return probe.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon or sandbox image unavailable")
def test_pytest_can_run_inside_the_container(tmp_path: Path) -> None:
    write(tmp_path, "test_ok.py", "def test_adds():\n    assert 1 + 1 == 2\n")

    result = run_pytest(tmp_path, "test_ok.py", backend="docker")

    assert result["passed"] is True
    assert result["exit_code"] == 0


def test_unknown_pytest_backend_is_a_result_not_an_exception(tmp_path: Path) -> None:
    result = run_pytest(tmp_path, "test_ok.py", backend="podman")

    assert result["passed"] is False
    assert result["exit_code"] is None
    assert "must be 'host' or 'docker'" in str(result["summary"])
