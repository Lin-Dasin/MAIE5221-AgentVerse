"""Temporary live smoke test: one real model, real tools, real verification.

This is a stopgap so the team has a reproducible live run before the real entry
point exists. Application assembly belongs to the application owner (contract 14);
delete this script once that entry point lands.

Usage:

    export LLM_BASE_URL=https://api.deepseek.com
    export LLM_API_KEY=sk-...
    export LLM_MODEL=deepseek-flash
    export SEARCH_API_KEY=tvly-...        # only needed for goals that search

    python scripts/live_smoke.py

Nothing loads .env automatically, so the variables must be exported.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.verdict import FileVerifier
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import AcceptanceRule, RunConfig
from runtime.client import DeepSeekClient
from runtime.profiles import load_profiles
from sandbox.executor import SandboxToolExecutor
from tools.registry import build, definitions

REQUIRED_ENV = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")

DEFAULT_GOAL = (
    "Search the web for the official release date of Python 3.13 and write a short note "
    "to outputs/notes.md. The note must name the release date and include the source URL."
)

FINAL_ARTIFACT = "outputs/notes.md"


def check_environment() -> None:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        print("Export them first; nothing loads .env automatically.", file=sys.stderr)
        print("See docs/local-setup.md.", file=sys.stderr)
        raise SystemExit(2)
    if not os.environ.get("SEARCH_API_KEY"):
        print(
            "Warning: SEARCH_API_KEY is not set, so web_search will fail. "
            "The rest of the flow can still be exercised.",
            file=sys.stderr,
        )


async def preflight(client: DeepSeekClient) -> bool:
    """Send one minimal request and print the raw answer.

    `runtime/client.py` turns every non-200 into a message that carries only the
    status code, so a 404 "Model Not Exist" and a 400 "invalid request" look the
    same from the outside. This prints the body so the cause is visible.
    """
    print(f"endpoint    : {client.url}")
    print(f"model       : {client.model}")
    payload = {
        "model": client.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    headers = {"Authorization": f"Bearer {client.api_key}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as probe:
            response = await probe.post(client.url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        print(f"preflight   : network error {type(exc).__name__}: {exc}", file=sys.stderr)
        return False

    if response.status_code != 200:
        print(f"preflight   : HTTP {response.status_code}", file=sys.stderr)
        print(f"body        : {response.text[:600]}", file=sys.stderr)
        print(
            "hints       : 401 key wrong, 402 balance empty, 404 model id not served, "
            "400 malformed request",
            file=sys.stderr,
        )
        return False

    print("preflight   : OK")
    return True


async def run(goal: str, root: Path, max_steps: int, max_output_tokens: int) -> int:
    client = DeepSeekClient.from_environment()
    registry = build()
    trace = JsonlTrace(root / TRACE_FILENAME)
    executor = TracingToolExecutor(SandboxToolExecutor(root, registry), trace)

    service = build_service(
        root=root,
        model=client,
        profiles=load_profiles(Path("profiles")),
        tool_definitions=definitions(registry),
        executor=executor,
        verifier=FileVerifier(),
        approver=PolicyApprover(allowed_actions=frozenset({"file_write", "web_search"})),
    )

    config = RunConfig(
        memory_enabled=False,
        write_memory=False,
        max_agent_steps=max_steps,
        # Thinking mode is on by default and its tokens count as output, so the
        # default 2048 budget can be exhausted before the answer is written.
        max_output_tokens=max_output_tokens,
        task_timeout_sec=240.0,
        approval_timeout_sec=60.0,
    )
    goal_acceptance = [AcceptanceRule(type="file_exists", path=FINAL_ARTIFACT)]

    if not await preflight(client):
        await client.aclose()
        return 2

    started = datetime.now(UTC)
    try:
        graph = await service.planner.plan(
            goal,
            service.workers,
            config,
            memory_context="",
            goal_acceptance=goal_acceptance,
        )
    except Exception as exc:  # noqa: BLE001 - a smoke script must show the cause
        print(f"planning    : {type(exc).__name__}: {exc}", file=sys.stderr)
        await client.aclose()
        return 1

    print(f"graph       : {len(graph.tasks)} task(s)")
    try:
        result = await service.run_graph(graph, config)
    finally:
        await client.aclose()
    result.started_at = started
    if result.graph is None:
        result.graph = graph

    print("=" * 62)
    print(f"run_id      : {result.run_id}")
    print(f"duration    : {(result.finished_at - result.started_at).total_seconds():.1f}s")
    print(f"failure     : {result.failure_code.value if result.failure_code else '-'}")
    if result.failure_reason:
        print(f"reason      : {result.failure_reason}")

    print("-" * 62)
    if result.graph is None:
        print("No task graph was produced.")
    else:
        print(f"tasks       : {len(result.graph.tasks)}")
        for task in result.graph.tasks:
            print(f"  {task.task_id} -> {task.assignee}")

    for item in result.tasks:
        print("-" * 62)
        print(f"task        : {item.task_id} ({item.agent_id})")
        print(f"status      : {item.status.value}  attempts={item.attempts}  tokens={item.tokens}")
        if item.failure_code:
            print(f"failure     : {item.failure_code.value}: {item.failure_reason or ''}")
        for output in item.outputs:
            print(f"output      : {output}")
        if item.summary:
            print(f"summary     : {item.summary[:300]}")

    verdict = result.goal_verdict
    print("=" * 62)
    if verdict is None:
        print("goal verdict: none (the run did not reach verification)")
    else:
        print(f"goal verdict: {'PASS' if verdict.passed else 'FAIL'}")
        print(f"checks      : {verdict.checks_passed}/{verdict.checks_total}")
        for defect in verdict.defects:
            print(f"  defect    : {defect}")

    print(f"artifacts   : {(root / 'artifacts' / result.run_id).resolve()}")
    print(f"trace       : {(root / TRACE_FILENAME).resolve()}")
    return 0 if verdict is not None and verdict.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="natural-language goal")
    parser.add_argument("--root", type=Path, default=Path("results/live-smoke"))
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="raise this if a step fails with 'exceeded the response limit'",
    )
    args = parser.parse_args(argv)

    check_environment()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    try:
        return asyncio.run(run(args.goal, root, args.max_steps, args.max_output_tokens))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - a smoke script should print, not traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
