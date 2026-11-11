"""Command-line entry point for explicitly offline fixed-graph evaluation."""

import argparse
import asyncio
import sys
from pathlib import Path

from harness.cases import load_suite
from harness.offline import build_traced_offline_service
from harness.reporting import write_report
from harness.runner import run_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", default="offline-smoke", help="dev, offline-smoke, or a JSON file path"
    )
    parser.add_argument(
        "--mock", action="store_true", help="required for offline-smoke and custom suites"
    )
    parser.add_argument(
        "--output", type=Path, help="new JSON output file; existing files are refused"
    )
    parser.add_argument("--work-dir", type=Path, default=Path("results/workspaces"))
    parser.add_argument("--case-timeout", type=float, default=30.0)
    parser.add_argument("--fail-on-goal-failure", action="store_true")
    args = parser.parse_args(argv)
    if args.suite != "dev" and not args.mock:
        parser.error("pass --mock for offline-smoke or a custom suite")
    suite_path = (
        Path(__file__).parent / "suites" / f"{args.suite}.json"
        if args.suite in {"dev", "offline-smoke"}
        else Path(args.suite)
    )
    try:
        if args.output is not None and args.output.exists():
            raise FileExistsError(f"Report already exists: {args.output}")
        suite = load_suite(suite_path)
        report = asyncio.run(
            run_suite(
                suite,
                args.work_dir,
                service_factory=build_traced_offline_service,
                case_timeout_sec=args.case_timeout,
            )
        )
        output = args.output or Path("results") / f"{report.suite}-{report.run_id}.json"
        write_report(report, output)
    except (OSError, ValueError) as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Evaluation interrupted", file=sys.stderr)
        return 130
    summary = report.summary
    print("OFFLINE: scripted Worker, real scheduler and verifier; no live model metrics.")
    if args.suite == "dev":
        print("Dev Set fixture subset: F1-F3 and P3.")
    print(f"Goals: {summary.goals_passed}/{summary.goals_total}")
    print(f"Subtasks: {summary.subtasks_passed}/{summary.subtasks_total}")
    print(f"JSON: {output.resolve()}")
    return int(args.fail_on_goal_failure and summary.goals_passed < summary.goals_total)


if __name__ == "__main__":
    raise SystemExit(main())
