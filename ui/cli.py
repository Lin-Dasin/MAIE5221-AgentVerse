"""AgentVerse command-line interface."""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import TypeAdapter

from harness import delegation_case, permission_cases
from harness import run as evaluation
from harness.metrics import CaseReport, Summary
from protocol.models import AcceptanceRule
from ui import h1_demo, replay


def _run_goal(argv: list[str]) -> int:
    from ui.goal import run_goal

    parser = argparse.ArgumentParser(
        prog="agentverse run", description="Run a natural-language goal"
    )
    parser.add_argument("goal")
    _add_goal_options(parser, default_work_dir=Path("results/cli-goals"))
    args = parser.parse_args(argv)
    goal = args.goal.strip()
    if not goal:
        parser.error("goal must contain text")

    rules = _goal_rules(parser, args.acceptance)
    evaluation_path, report = asyncio.run(
        run_goal(goal, args.work_dir, rules=rules, profiles_dir=args.profiles_dir)
    )
    return _display_goal_report(evaluation_path, report)


def _add_goal_options(parser: argparse.ArgumentParser, *, default_work_dir: Path) -> None:
    parser.add_argument("--work-dir", type=Path, default=default_work_dir)
    parser.add_argument("--profiles-dir", type=Path)
    parser.add_argument("--acceptance", type=Path, help="JSON array of fixed goal acceptance rules")


def _goal_rules(parser: argparse.ArgumentParser, path: Path | None) -> list[AcceptanceRule] | None:
    if path is None:
        return None
    rules = TypeAdapter(list[AcceptanceRule]).validate_json(path.read_bytes())
    if not rules:
        parser.error("--acceptance must contain at least one rule")
    return rules


def _display_goal_report(evaluation_path: Path, report: CaseReport) -> int:
    run_dir = evaluation_path.parent
    print(f"Goal: {report.status}")
    print(f"Subtasks: {report.subtasks_passed}/{report.subtasks_total}")
    if report.failure_reason:
        print(f"Reason: {report.failure_reason}")
    print(f"Evaluation: {evaluation_path}")
    for artifact in report.artifacts:
        print(f"Artifact: {run_dir / artifact}")
    if report.trace_path:
        print(f"Trace: {run_dir / report.trace_path}")
    return int(report.status != "success" or bool(report.warnings))


def _summarize(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentverse summary")
    parser.add_argument("report", type=Path, help="Evaluation JSON file")
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    if not isinstance(report, dict) or "summary" not in report:
        raise ValueError("Expected an evaluation JSON report with a summary")
    summary = Summary.model_validate(report["summary"])
    print(f"Goals: {summary.goals_passed}/{summary.goals_total}")
    print(f"Subtasks: {summary.subtasks_passed}/{summary.subtasks_total}")
    if summary.avg_duration_sec is not None:
        print(f"Average duration: {summary.avg_duration_sec:.2f}s")
    return 0


def _messages(argv: list[str]) -> int:
    from bus.messages import MessageBus

    parser = argparse.ArgumentParser(prog="agentverse messages")
    parser.add_argument("--db", type=Path, default=Path("results/messages.sqlite3"))
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="Create a conversation with fixed membership")
    create.add_argument("conversation")
    create.add_argument("--kind", choices=("private", "group"), required=True)
    create.add_argument("--member", action="append", required=True)
    publish = actions.add_parser("publish", help="Record a message or collaboration event")
    publish.add_argument("conversation")
    publish.add_argument("--sender", required=True)
    publish.add_argument(
        "--kind",
        choices=("message", "assignment", "delegation", "negotiation", "handoff"),
        default="message",
    )
    publish.add_argument("--text", required=True)
    chat = actions.add_parser("chat", help="Ask one Worker to reply in a private or group chat")
    chat.add_argument("conversation")
    chat.add_argument("--sender", required=True)
    chat.add_argument("--agent", required=True)
    chat.add_argument("--text", required=True)
    chat.add_argument("--profiles-dir", type=Path, default=Path("profiles"))
    group_run = actions.add_parser("run", help="Start a goal and report its results in a group")
    group_run.add_argument("conversation")
    group_run.add_argument("--sender", required=True)
    group_run.add_argument("--notifier", default="orchestrator", help="Group member posting events")
    group_run.add_argument("--goal", required=True)
    group_run.add_argument(
        "--clarification-timeout", type=float, default=60, help="Seconds to wait for a group reply"
    )
    _add_goal_options(group_run, default_work_dir=Path("results/group-goals"))
    reply = actions.add_parser("reply", help="Answer a pending group clarification")
    reply.add_argument("conversation")
    reply.add_argument("--sender", required=True)
    reply.add_argument("--request-id", required=True)
    reply.add_argument("--text", required=True)
    history = actions.add_parser("history", help="Read a conversation as one of its members")
    history.add_argument("conversation")
    history.add_argument("--as-id", required=True)
    history.add_argument("--after", type=int, default=0)
    args = parser.parse_args(argv)
    bus = MessageBus(args.db)
    if args.action == "create":
        bus.create_conversation(args.conversation, args.kind, args.member)
        print(f"Conversation created: {args.conversation}")
    elif args.action == "publish":
        event = bus.publish(args.conversation, args.sender, args.kind, args.text)
        print(f"Event {event.sequence} recorded")
    elif args.action == "chat":
        from ui.chat import chat as request_reply

        reply = asyncio.run(
            request_reply(
                bus, args.conversation, args.sender, args.agent, args.text, args.profiles_dir
            )
        )
        print(f"{reply.sender}: {json.dumps(reply.text, ensure_ascii=False)}")
    elif args.action == "run":
        from ui.group_run import run_group_goal

        rules = _goal_rules(group_run, args.acceptance)
        evaluation_path, report = asyncio.run(
            run_group_goal(
                bus,
                args.conversation,
                args.sender,
                args.notifier,
                args.goal,
                args.work_dir,
                rules=rules,
                profiles_dir=args.profiles_dir,
                clarification_timeout_sec=args.clarification_timeout,
            )
        )
        return _display_goal_report(evaluation_path, report)
    elif args.action == "reply":
        event = bus.reply_to_clarification(
            args.conversation, args.sender, args.request_id, args.text
        )
        print(f"Clarification answer recorded as event {event.sequence}")
    else:
        for event in bus.history(args.conversation, args.as_id, after=args.after):
            print(
                f"{event.sequence:>4}  {event.timestamp}  {event.sender}  {event.kind}  "
                f"{json.dumps(event.text, ensure_ascii=False)}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    commands = {
        "run": _run_goal,
        "h1": h1_demo.main,
        "permission": permission_cases.main,
        "p3": delegation_case.main,
        "evaluate": evaluation.main,
        "summary": _summarize,
        "replay": replay.main,
        "messages": _messages,
    }
    parser = argparse.ArgumentParser(
        prog="agentverse",
        description=__doc__,
        epilog="Run agentverse <command> --help for command options.",
    )
    parser.add_argument("command", choices=commands)
    parser.add_argument("options", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return 0
    args = parser.parse_args(arguments)
    try:
        return commands[args.command](args.options)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(f"AgentVerse error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
