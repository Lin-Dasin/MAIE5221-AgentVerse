from unittest.mock import Mock

import pytest

from harness.verdict import FileVerifier
from protocol.models import AcceptanceRule


@pytest.mark.parametrize(
    ("runner_result", "passed", "detail"),
    [
        ({"passed": True, "exit_code": 0, "summary": "1 passed"}, True, None),
        ({"passed": False, "exit_code": 1, "summary": "1 failed"}, False, "1 failed"),
        ({"passed": False, "exit_code": 5, "summary": "no tests"}, False, "no tests"),
        ({"passed": False, "exit_code": None, "summary": "timeout"}, False, "timeout"),
        ({"passed": True, "exit_code": 1}, False, "exit_code=1"),
        ({"passed": False, "exit_code": 0}, False, "exit_code=0"),
        ({"passed": True, "exit_code": False}, False, "exit_code=False"),
        ({}, False, "missing runner summary"),
        (None, False, "pytest_runner_error"),
    ],
)
async def test_opt_in_runner_controls_only_its_own_verdict(tmp_path, runner_result, passed, detail):
    target = "outputs/test_example.py"
    (tmp_path / "outputs").mkdir()
    (tmp_path / target).write_text("def test_example(): pass\n", encoding="utf-8")
    runner = Mock(return_value=runner_result)
    verifier = FileVerifier(pytest_runner=runner)
    assert verifier.supported_acceptance_types == FileVerifier().supported_acceptance_types | {
        "pytest_passes"
    }
    result = await verifier.check(
        [
            AcceptanceRule(type="file_exists", path=target),
            AcceptanceRule(type="pytest_passes", path=target),
        ],
        tmp_path,
    )
    runner.assert_called_once_with(tmp_path.resolve(), target)
    assert result.passed is passed
    assert result.checks_passed == 1 + int(passed)
    if detail:
        assert detail in result.defects[0]


async def test_missing_target_does_not_invoke_runner(tmp_path):
    runner = Mock()
    result = await FileVerifier(pytest_runner=runner).check(
        [AcceptanceRule(type="pytest_passes", path="missing_test.py")], tmp_path
    )
    runner.assert_not_called()
    assert "missing_file" in result.defects[0]


async def test_runner_exception_is_a_failed_check(tmp_path):
    target = "test_example.py"
    (tmp_path / target).touch()
    result = await FileVerifier(pytest_runner=Mock(side_effect=OSError("unavailable"))).check(
        [AcceptanceRule(type="pytest_passes", path=target)], tmp_path
    )
    assert not result.passed
    assert "pytest_runner_error: OSError" in result.defects[0]
