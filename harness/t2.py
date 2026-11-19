"""Check pytest acceptance end to end with four trusted, offline T2 fixtures."""

import argparse
import asyncio
import json
from pathlib import Path

from bus.trace import TRACE_FILENAME, JsonlTrace
from harness.cases import EvaluationCase, EvaluationSuite, FixtureStep
from harness.offline import build_offline_service
from harness.reporting import write_report
from harness.runner import TracedService, run_suite
from harness.verdict import FileVerifier
from protocol.models import AcceptanceRule, Task, TaskGraph
from sandbox.test_runner import run_pytest

CORRECT = "def add(a, b):\n    return a + b\n"
INCORRECT = "def add(a, b):\n    return a - b\n"
FIXED_TEST = "from solution import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def make_case(name: str, implementation: str, test: str) -> EvaluationCase:
    files = {"outputs/solution.py": implementation, "outputs/test_solution.py": test}
    node = Task(
        task_id="implement",
        title="Implement integer addition",
        assignee="writer",
        outputs=list(files),
        max_retries=0,
        acceptance=[AcceptanceRule(type="pytest_passes", path="outputs/test_solution.py")],
    )
    graph = TaskGraph(
        tasks=[node],
        final_task_id=node.task_id,
        goal_acceptance=[AcceptanceRule(type="pytest_passes", path="test_solution.py")],
    )
    return EvaluationCase(
        task_id=name, goal=node.title, graph=graph, steps={node.task_id: [FixtureStep(files=files)]}
    )


async def run(work_dir: Path) -> tuple[Path, bool]:
    suite = EvaluationSuite(
        suite="t2",
        cases=[
            make_case("correct", CORRECT, FIXED_TEST),
            make_case("incorrect", INCORRECT, FIXED_TEST),
            make_case("empty", CORRECT, "# No tests defined.\n"),
            make_case("timeout", CORRECT, "import time\n\ndef test_wait():\n    time.sleep(60)\n"),
        ],
    )
    evidence: dict[str, list[dict]] = {}

    def build(case: EvaluationCase, root: Path) -> TracedService:
        calls = evidence[case.task_id] = []

        def record_pytest(workspace: Path, target: str) -> dict:
            result = run_pytest(
                workspace, target, timeout_sec=2 if case.task_id == "timeout" else 20
            )
            calls.append(
                {"root": workspace.relative_to(root).as_posix(), "target": target, **result}
            )
            return result

        trace = JsonlTrace(root / TRACE_FILENAME)
        service = build_offline_service(
            case, root, observer=trace, verifier=FileVerifier(pytest_runner=record_pytest)
        )
        return TracedService(service, trace)

    report = await run_suite(suite, work_dir, service_factory=build, case_timeout_sec=60)
    directory = work_dir / report.run_id
    write_report(report, directory / "evaluation.json")
    expected_codes = {"correct": [0, 0], "incorrect": [1], "empty": [5], "timeout": [None]}
    checks = {}
    for task in report.tasks:
        calls = evidence[task.task_id]
        should_pass = task.task_id == "correct"
        targets = (
            ["outputs/test_solution.py", "test_solution.py"]
            if should_pass
            else ["outputs/test_solution.py"]
        )
        checks[task.task_id] = (
            (task.status == "success") == should_pass
            and task.subtasks_total == 1
            and task.subtasks_passed == int(should_pass)
            and task.acceptance_passed == int(should_pass)
            and [c["target"] for c in calls] == targets
            and [c["exit_code"] for c in calls] == expected_codes[task.task_id]
            and all(c["passed"] is should_pass for c in calls)
            and (task.task_id != "timeout" or calls[0]["summary"].startswith("timeout"))
        )
    passed = checks.keys() == expected_codes.keys() and all(checks.values())
    control = {"passed": passed, "checks": checks, "pytest_calls": evidence}
    (directory / "control.json").write_text(json.dumps(control, indent=2) + "\n", encoding="utf-8")
    return directory, control["passed"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("results/t2"))
    args = parser.parse_args()
    directory, passed = asyncio.run(run(args.work_dir))
    print(f"T2 controls: {'PASS' if passed else 'FAIL'}\nEvidence: {directory}")
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
