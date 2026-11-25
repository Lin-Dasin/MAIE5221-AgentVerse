"""Stage, drive and export one two-task Mid Test scenario."""

import argparse
import asyncio
import hashlib
import json
import sqlite3
import stat
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from bus.messages import MessageBus, MessageEvent
from harness.scenario import (
    INSTRUCTOR,
    ScenarioSpec,
    ScenarioTask,
    load_scenario,
    load_scenario_profiles,
)
from harness.scenario_export import export_scenario
from orchestrator.artifacts import archive_path, copy_file, inside
from protocol.models import AgentProfile, Contract, Identifier
from ui.scenario_metrics import record_task_metrics

NOTIFIER = "orchestrator"


class ScenarioSession(Contract):
    scenario: ScenarioSpec
    conversation_id: Identifier


@dataclass(frozen=True)
class TaskRequest:
    """The data a discussion engine adapter receives for one task."""

    task: ScenarioTask
    conversation_id: str
    run_id: str
    profiles: dict[str, AgentProfile]
    history: tuple[MessageEvent, ...]
    workspace: Path
    inputs: tuple[str, ...]


# An injected verifier can accept outputs immediately. Live discussions await human selection.
TaskEngine = Callable[
    [TaskRequest, Callable[[], str | None], Callable[[str, str], None]],
    Awaitable[tuple[str, ...] | None],
]


def init_session(scenario_dir: Path, session_dir: Path) -> ScenarioSession:
    scenario = load_scenario(scenario_dir)
    agents = [role.agent_id for role in scenario.roles]
    if NOTIFIER in agents or INSTRUCTOR in agents:
        raise ValueError("Scenario Agent IDs must be separate from the instructor and event source")
    session = ScenarioSession(scenario=scenario, conversation_id=f"mid-{uuid4().hex}")
    if session_dir.exists():
        raise FileExistsError(f"Choose a new session directory: {session_dir}")
    session_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=session_dir.parent) as stage:
        prepared = Path(stage) / "session"
        bus = MessageBus(prepared / "messages.sqlite3")
        bus.create_conversation(session.conversation_id, "group", [INSTRUCTOR, NOTIFIER, *agents])
        (prepared / "scenario.json").write_text(
            session.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        prepared.rename(session_dir)
    return session


def _read_session(session_dir: Path) -> tuple[ScenarioSession, MessageBus]:
    session = ScenarioSession.model_validate_json((session_dir / "scenario.json").read_bytes())
    database = session_dir / "messages.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(f"Missing scenario message database: {database}")
    return session, MessageBus(database)


def _require_unlinked(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("Accepted artifacts must use ordinary workspace paths")


def _ordinary_files(source: Path) -> Iterator[Path]:
    for path in sorted(source.iterdir()):
        _require_unlinked(path)
        if path.is_dir():
            yield from _ordinary_files(path)
        else:
            yield path


def _copy_accepted(source: Path, destination: Path) -> tuple[str, ...]:
    if not source.is_dir():
        raise ValueError("Task 2 requires the accepted artifacts from Task 1")
    _require_unlinked(source)
    files = []
    for path in _ordinary_files(source):
        relative = path.relative_to(source).as_posix()
        copy_file(inside(source, relative), inside(destination, relative))
        files.append(relative)
    if not files:
        raise ValueError("Task 1 has no accepted artifacts")
    return tuple(files)


def file_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _record_run_messages(workspace: Path, start: int, end: int) -> None:
    payload = {"start": start, "end": end}
    (workspace / "run-messages.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    summary_path = workspace / "discussion-summary.json"
    if not summary_path.is_file():
        return
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data["run_messages"] = payload
        summary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _accepted_ranges(session_dir: Path, session: ScenarioSession) -> list[tuple[int, int]]:
    ranges = []
    for task in session.scenario.tasks:
        task_dir = session_dir / "tasks" / task.task_id
        meta_path = task_dir / "accepted.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        attempt = meta.get("attempt") if isinstance(meta, dict) else None
        if not isinstance(attempt, str):
            continue
        summary_path = inside(task_dir / "attempts", attempt) / "discussion-summary.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        window = summary.get("run_messages") if isinstance(summary, dict) else None
        if not isinstance(window, dict):
            continue
        start, end = window.get("start"), window.get("end")
        if type(start) is int and type(end) is int and 0 <= start <= end:
            ranges.append((start, end))
    return ranges


def _publish_accepted(
    workspace: Path,
    outputs: tuple[str, ...],
    destination: Path,
    *,
    expected_hashes: dict[str, str] | None = None,
) -> None:
    if not outputs:
        raise ValueError("A passing scenario task must publish an artifact")
    relatives = [archive_path(output) for output in outputs]
    if len({path.casefold() for path in relatives}) != len(relatives):
        raise ValueError("Accepted output paths must be distinct")
    with TemporaryDirectory(dir=destination.parent) as stage:
        accepted = Path(stage) / "accepted"
        accepted.mkdir()
        for output, name in zip(outputs, relatives, strict=True):
            source_path = workspace / output
            for path in (source_path, *source_path.parents):
                _require_unlinked(path)
                if path == workspace:
                    break
            target = inside(accepted, name)
            copy_file(inside(workspace, output), target)
            if expected_hashes is not None and file_sha256(target) != expected_hashes[output]:
                raise ValueError(f"Candidate contents do not match the discussion digest: {output}")
        accepted.rename(destination)


def _post_task(
    bus: MessageBus, session: ScenarioSession, task: ScenarioTask, inputs: tuple[str, ...]
) -> tuple[MessageEvent, ...]:
    conversation = session.conversation_id
    history = bus.history(conversation, INSTRUCTOR)
    if not any(
        event.sender == INSTRUCTOR and event.kind == "message" and event.text == task.text
        for event in history
    ):
        bus.publish(conversation, INSTRUCTOR, "message", task.text)
    if inputs:
        handoff = json.dumps(
            {
                "event": "scenario_inputs_ready",
                "from_task": "task-1",
                "task_id": task.task_id,
                "inputs": [
                    {"from_task": "task-1", "path": path, "destination": f"inputs/{path}"}
                    for path in inputs
                ],
            },
            ensure_ascii=False,
        )
        if not any(
            event.sender == NOTIFIER and event.kind == "handoff" and event.text == handoff
            for event in history
        ):
            bus.publish(conversation, NOTIFIER, "handoff", handoff)
    return tuple(bus.history(conversation, INSTRUCTOR))


@contextmanager
def _reserve_task(task_dir: Path) -> Iterator[None]:
    task_dir.mkdir(parents=True, exist_ok=True)
    # A process exit releases this lock, even when an attempt leaves partial files behind.
    with closing(sqlite3.connect(task_dir / "run-lock.sqlite3", timeout=0)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                raise RuntimeError(f"{task_dir.name} already has a running attempt") from error
            raise
        yield


async def run_task(
    session_dir: Path, number: int, profiles_dir: Path, engine: TaskEngine
) -> Path | None:
    if number not in (1, 2):
        raise ValueError("Select Task 1 or Task 2")
    session, bus = _read_session(session_dir)
    task = session.scenario.tasks[number - 1]
    profiles = load_scenario_profiles(session.scenario, profiles_dir)
    task_dir = session_dir / "tasks" / task.task_id
    accepted = task_dir / "accepted"
    previous = session_dir / "tasks" / "task-1" / "accepted"
    if number == 2 and not previous.is_dir():
        raise ValueError("Complete and accept Task 1 before running Task 2")
    with _reserve_task(task_dir):
        if accepted.exists():
            raise FileExistsError(f"{task.task_id} is already accepted in this session")
        run_id = uuid4().hex
        workspace = task_dir / "attempts" / run_id
        (workspace / "inputs").mkdir(parents=True)
        (workspace / "outputs").mkdir()
        inputs = _copy_accepted(previous, workspace / "inputs") if number == 2 else ()
        before = bus.history(session.conversation_id, INSTRUCTOR)
        start_sequence = before[-1].sequence + 1 if before else 1
        history = _post_task(bus, session, task, inputs)
        cursor = history[-1].sequence

        def human() -> str | None:
            nonlocal cursor
            for event in bus.history(session.conversation_id, INSTRUCTOR, after=cursor):
                cursor = event.sequence
                if event.sender == INSTRUCTOR and event.kind == "message":
                    return event.text
            return None

        def emit(speaker: str, content: str) -> None:
            if speaker not in profiles:
                raise ValueError("A public turn must come from a scenario Agent")
            bus.publish(session.conversation_id, speaker, "message", content)

        request = TaskRequest(
            task, session.conversation_id, run_id, profiles, history, workspace, inputs
        )
        started_at = time.monotonic()
        outputs = await engine(request, human, emit)
        end_sequence = bus.history(session.conversation_id, INSTRUCTOR)[-1].sequence
        _record_run_messages(workspace, start_sequence, end_sequence)
        record_task_metrics(session_dir, task.task_id, time.monotonic() - started_at)
        if outputs is None:
            return None
        _publish_accepted(workspace, outputs, accepted)
        return accepted


def accept_attempt(
    session_dir: Path, number: int, attempt: str, artifacts: tuple[str, ...]
) -> Path:
    """Publish only the human-selected candidates from a completed discussion attempt."""
    if number not in (1, 2) or not artifacts:
        raise ValueError("Select a task and at least one candidate artifact")
    session, _ = _read_session(session_dir)
    task = session.scenario.tasks[number - 1]
    task_dir = session_dir / "tasks" / task.task_id
    if number == 2 and not (session_dir / "tasks" / "task-1" / "accepted").is_dir():
        raise ValueError("Complete and accept Task 1 before accepting Task 2")
    attempts = task_dir / "attempts"
    workspace = inside(attempts, attempt)
    if workspace.parent != attempts or not workspace.is_dir():
        raise ValueError(f"Unknown attempt for {task.task_id}: {attempt}")
    for directory in (task_dir.parent, task_dir, attempts, workspace):
        _require_unlinked(directory)
    accepted = task_dir / "accepted"
    with _reserve_task(task_dir):
        if accepted.exists():
            raise FileExistsError(f"{task.task_id} is already accepted in this session")
        summary_file = workspace / "discussion-summary.json"
        if not summary_file.is_file():
            raise ValueError("This attempt has no completed discussion summary to accept")
        _require_unlinked(summary_file)
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ValueError("Only a normally completed discussion can be accepted")
        managed = summary.get("mode") == "managed"
        if managed:
            if summary.get("status") != "passed" or summary.get("stop_reason") != "accepted":
                raise ValueError("All managed checks must pass before final acceptance")
        elif summary.get("stop_reason") != "max_rounds":
            raise ValueError("Only a normally completed discussion can be accepted")
        candidates = summary.get("candidates")
        if not isinstance(candidates, dict) or not candidates:
            raise ValueError("The completed discussion listed no candidate artifacts")
        if managed and set(artifacts) != candidates.keys():
            raise ValueError("Accept the full managed candidate set together")
        owners = {role.agent_id for role in session.scenario.roles}
        for output in artifacts:
            owner, separator, _ = archive_path(output).partition("/")
            if not separator or owner not in owners or output not in candidates:
                raise ValueError(
                    f"Artifact is not a listed candidate from a scenario agent: {output}"
                )
        _publish_accepted(workspace, artifacts, accepted, expected_hashes=candidates)
        meta_path = task_dir / "accepted.json"
        if meta_path.exists():
            _require_unlinked(meta_path)
        meta_path.write_text(
            json.dumps(
                {
                    "attempt": attempt,
                    "task_id": task.task_id,
                    "artifacts": list(artifacts),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        record_task_metrics(session_dir, task.task_id)
    return accepted


def export_session(session_dir: Path, destination: Path) -> Path:
    session, bus = _read_session(session_dir)
    accepted = {
        task.task_id: session_dir / "tasks" / task.task_id / "accepted"
        for task in session.scenario.tasks
    }
    if not all(path.is_dir() for path in accepted.values()):
        raise ValueError("Accept both tasks before exporting the scoring bundle")
    history = bus.history(session.conversation_id, INSTRUCTOR)
    ranges = _accepted_ranges(session_dir, session)
    if ranges:
        history = [
            event
            for event in history
            if event.sender == INSTRUCTOR
            or event.kind == "handoff"
            or any(start <= event.sequence <= end for start, end in ranges)
        ]
    return export_scenario(
        destination,
        scenario_id=session.scenario.scenario_id,
        conversation_id=session.conversation_id,
        roles={role.agent_id: role.role_label for role in session.scenario.roles},
        messages=history,
        accepted_artifacts=accepted,
    )


def main(argv: list[str] | None = None, *, engine: TaskEngine | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentverse mid-test", description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    init = subcommands.add_parser("init", help="Load a teacher scenario and create its group")
    init.add_argument("scenario_dir", type=Path)
    init.add_argument("--run-dir", required=True, type=Path)
    say = subcommands.add_parser("say", help="Post a human message to the scenario group")
    say.add_argument("run_dir", type=Path)
    say.add_argument("--text", required=True)
    run = subcommands.add_parser("run", help="Run one adjudicated scenario task")
    run.add_argument("run_dir", type=Path)
    run.add_argument("number", type=int, choices=(1, 2))
    run.add_argument("--profiles-dir", required=True, type=Path)
    run.add_argument("--max-rounds", type=int, help="Default: 4 in open mode, 1 in managed mode")
    run.add_argument(
        "--max-task-tokens",
        type=int,
        default=None,
        help="Default: 180000 in managed mode, 60000 in open mode",
    )
    run.add_argument("--max-output-tokens", type=int, default=2048)
    run.add_argument(
        "--managed", action="store_true", help="Run automated checks and owner-directed rework"
    )
    run.add_argument(
        "--generic-checks",
        action="store_true",
        help="Use generic file checks instead of Mid-specific verifiers (requires --managed)",
    )
    run.add_argument(
        "--max-rework-rounds", type=int, choices=range(4), help="Managed mode, default: 3"
    )
    run.add_argument(
        "--hitl-pause-sec",
        type=float,
        default=0.0,
        help="Seconds to pause for a human reply after a [HITL] turn; 0 keeps auto mode",
    )
    accept = subcommands.add_parser("accept", help="Accept selected files from one discussion")
    accept.add_argument("run_dir", type=Path)
    accept.add_argument("number", type=int, choices=(1, 2))
    accept.add_argument("--attempt", required=True, help="Run ID printed by the discussion")
    accept.add_argument("--artifact", required=True, action="append", help="Repeat for each file")
    export = subcommands.add_parser("export", help="Package the dialogue and accepted artifacts")
    export.add_argument("run_dir", type=Path)
    export.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    session_dir = args.run_dir.resolve()
    if args.action == "init":
        session = init_session(args.scenario_dir, session_dir)
        print(
            f"Conversation: {session.conversation_id}\nDatabase: {session_dir / 'messages.sqlite3'}"
        )
    elif args.action == "say":
        session, bus = _read_session(session_dir)
        event = bus.publish(session.conversation_id, INSTRUCTOR, "message", args.text)
        print(f"Posted message {event.sequence}")
    elif args.action == "run":
        if args.max_rework_rounds is not None and not args.managed:
            parser.error("--max-rework-rounds requires --managed")
        if args.generic_checks and not args.managed:
            parser.error("--generic-checks requires --managed")
        if engine is None:
            from ui.scenario_engine import make_live_engine

            rounds = args.max_rounds
            if rounds is None:
                rounds = 1 if args.managed else 4
            max_task_tokens = args.max_task_tokens
            if max_task_tokens is None:
                max_task_tokens = 180_000 if args.managed else 60_000
            limits = dict(
                max_rounds=rounds,
                max_task_tokens=max_task_tokens,
                max_output_tokens=args.max_output_tokens,
            )
            if args.managed:
                session, _ = _read_session(session_dir)
                engine = make_live_engine(
                    managed_task=(session.scenario.scenario_id, args.number),
                    max_rework_rounds=(
                        3 if args.max_rework_rounds is None else args.max_rework_rounds
                    ),
                    hitl_pause_timeout_sec=args.hitl_pause_sec,
                    generic_verifier=args.generic_checks,
                    **limits,
                )
            else:
                engine = make_live_engine(
                    hitl_pause_timeout_sec=args.hitl_pause_sec, **limits
                )
        accepted = asyncio.run(run_task(session_dir, args.number, args.profiles_dir, engine))
        if accepted is None:
            print(f"Task {args.number} is unaccepted. Review any candidates with mid-test accept.")
            return 1
        print(f"Accepted artifacts: {accepted}")
    elif args.action == "accept":
        accepted = accept_attempt(session_dir, args.number, args.attempt, tuple(args.artifact))
        print(f"Accepted artifacts: {accepted}")
    else:
        destination = export_session(session_dir, args.output_dir)
        print(f"Evidence exported to: {destination}")
    return 0
