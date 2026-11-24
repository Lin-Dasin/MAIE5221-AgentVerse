"""Run two related Chinese Worker tasks through real memory, tools and verdicts.

From the repository root:
    python -m scripts.worker_memory_demo --mock --work-dir results/worker-memory
    python -m scripts.worker_memory_demo --live --work-dir results/worker-memory

Live mode requires exported DeepSeek variables and makes paid calls. Mock mode
validates plumbing only. A fresh root is created per invocation; the two tasks
share its Worker memory but have distinct run IDs. No Planner is exercised.
"""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from bus.trace import TRACE_FILENAME, JsonlTrace, TracingToolExecutor
from harness.verdict import FileVerifier
from memory.jsonl_memory import JsonlMemory
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ChatMessage,
    FailureCode,
    ModelResponse,
    RunConfig,
    Task,
    TaskGraph,
    TaskStatus,
    ToolCall,
    ToolDefinition,
)
from protocol.ports import ModelClient
from runtime.client import DeepSeekClient
from runtime.memory_events import context_metadata
from sandbox.executor import SandboxToolExecutor
from tools.registry import build


class RequestAudit:
    """Inspect the actual model-client arguments, without storing private text."""

    def __init__(self, client: ModelClient, marker: str):
        self.client, self.marker = client, marker
        self.requests: list[dict] = []

    async def complete(self, messages, tools, *, max_tokens):
        payload = json.loads(next(m.content for m in messages if m.role == "user"))
        context = payload["memory_context"]
        record = {
            "task_id": payload["task"]["task_id"],
            **context_metadata(context),
            "marker_in_memory": self.marker in context,
            "marker_in_task": self.marker in json.dumps(payload["task"], ensure_ascii=False),
            "completed": False,
        }
        self.requests.append(record)
        response = await self.client.complete(messages, tools, max_tokens=max_tokens)
        record["completed"] = True
        return response


class MockMemoryModel:
    """Synthetic provider: second-task output derives only from injected context."""

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        payload = json.loads(next(m.content for m in messages if m.role == "user"))
        task = payload["task"]
        if messages[-1].role == "tool":
            return ModelResponse(content="已完成核对发票流程并写入文件。", tokens=4)
        source = task["title"] if task["task_id"] == "seed" else payload["memory_context"]
        match = re.search(r"MEM-[a-f0-9]{16}", source)
        text = f"{match[0]} 核对发票金额后复核总额。" if match else "无法恢复约定。"
        return ModelResponse(
            tokens=8,
            tool_calls=[
                ToolCall(
                    call_id=f"write-{task['task_id']}",
                    name="file_write",
                    arguments={"path": task["outputs"][0], "text": text},
                )
            ],
        )


def make_graph(task_id: str, title: str, marker: str) -> TaskGraph:
    output = f"outputs/{task_id}.md"
    return TaskGraph(
        tasks=[
            Task(
                task_id=task_id,
                title=title,
                assignee="writer",
                outputs=[output],
                acceptance=[AcceptanceRule(type="file_exists", path=output)],
                max_retries=0,
            )
        ],
        final_task_id=task_id,
        # Goal checks stay with the host verifier and are not sent to the Worker.
        goal_acceptance=[AcceptanceRule(type="text_contains", path=f"{task_id}.md", text=marker)],
    )


async def run_pair(
    work_dir: Path, client: ModelClient, *, mode: str, recall_enabled: bool = True
) -> tuple[Path, bool]:
    root = work_dir.resolve() / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    marker = "MEM-" + uuid4().hex[:16]
    registry = build()
    # No read/search/code tools: the second Worker cannot inspect the first file.
    executor = SandboxToolExecutor(root, {"file_write": registry["file_write"]})
    memory = JsonlMemory(root, max_records=1)
    trace = JsonlTrace(root / TRACE_FILENAME)
    audit = RequestAudit(client, marker)
    service = build_service(
        root=root,
        model=audit,
        profiles={
            "planner": AgentProfile(
                agent_id="planner", role="planner", system_prompt="Plan tasks."
            ),
            "writer": AgentProfile(
                agent_id="writer",
                role="worker",
                tools=["file_write"],
                system_prompt="按任务与相关历史经验完成工作。历史记录不替代当前任务指令。",
            ),
        },
        tool_definitions=executor.tool_definitions(),
        executor=TracingToolExecutor(executor, trace),
        memory=memory,
        memory_observer=trace,
        observer=trace,
        verifier=FileVerifier(),
        approver=PolicyApprover(),
    )
    plans = [
        make_graph(
            "seed",
            f"核对发票总额：本次约定编号：{marker}。"
            "写一行编号和核对发票金额后复核总额的步骤到 outputs/seed.md；完成后总结。",
            marker,
        ),
        make_graph(
            "recall",
            "复核下一批发票金额和总额。沿用之前核对发票的约定编号及步骤，"
            "写一行编号和步骤到 outputs/recall.md。若历史没有约定则写无法恢复约定，不要猜测。",
            marker,
        ),
    ]
    results, configs = [], []
    for index, plan in enumerate(plans):
        cfg = RunConfig(
            memory_enabled=True if index == 0 else recall_enabled,
            write_memory=True,
            max_agent_steps=5,
            max_task_tokens=16000,
            max_output_tokens=2048,
        )
        result = await service.run_graph(plan, cfg)
        await trace.run_finished(result)
        results.append(result)
        configs.append(cfg.model_dump(mode="json"))
        (root / f"{plan.final_task_id}-result.json").write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
    rows = [json.loads(line) for line in trace.path.read_text().splitlines()]
    events = [row for row in rows if row["event"] == "memory_lifecycle"]
    memory_path = memory.path_for("writer")
    snapshot = memory_path.read_bytes() if memory_path.is_file() else b""
    records = [json.loads(line) for line in snapshot.splitlines()]
    second_calls = [r for r in audit.requests if r["task_id"] == "recall"]
    first_reflection = next(
        (e for e in events if e["task_id"] == "seed" and e["phase"] == "reflected" and e["ok"]),
        None,
    )
    retrieved = [e for e in events if e["task_id"] == "recall" and e["phase"] == "retrieved"]
    injected = [e for e in events if e["task_id"] == "recall" and e["phase"] == "injected"]
    reflected = [e for e in events if e["task_id"] == "recall" and e["phase"] == "reflected"]
    first_passed = results[0].goal_verdict is not None and results[0].goal_verdict.passed
    second_passed = results[1].goal_verdict is not None and results[1].goal_verdict.passed
    checks = {
        "first_goal_passed": first_passed and results[0].failure_code is None,
        "second_goal_matches_expected": (
            second_passed and results[1].failure_code is None
            if recall_enabled
            else results[1].goal_verdict is not None
            and not second_passed
            and results[1].failure_code == FailureCode.ACCEPTANCE_FAILED
        ),
        "second_node_completed": len(results[1].tasks) == 1
        and results[1].tasks[0].status == TaskStatus.SUCCESS,
        "no_marker_in_second_task": bool(second_calls)
        and not any(r["marker_in_task"] for r in second_calls),
        "second_request_memory_matches_expected": bool(second_calls)
        and all(r["marker_in_memory"] == recall_enabled for r in second_calls),
        "two_private_reflections": len(records) == 2
        and {r["evidence_task_id"] for r in records} == {"seed", "recall"}
        and all(r["agent_id"] == "writer" for r in records),
        "trace_has_no_failures": not trace.warnings
        and first_reflection is not None
        and len(reflected) == 1
        and all(e["ok"] for e in events)
        and all(not r.warnings and all(not t.warnings for t in r.tasks) for r in results),
        "sessions_released": not service.scheduler.worker.sessions,
    }
    if recall_enabled:
        seed_ids = [r["memory_id"] for r in records if r["evidence_task_id"] == "seed"]
        checks["recall_attributes_seed_record"] = (
            len(seed_ids) == 1 and len(retrieved) == 1 and retrieved[0]["memory_ids"] == seed_ids
        )
        checks["reflection_retrieval_injection_order"] = (
            first_reflection is not None
            and len(retrieved) == 1
            and bool(injected)
            and len(reflected) == 1
            and first_reflection["sequence"] < retrieved[0]["sequence"] < injected[0]["sequence"]
            and injected[-1]["sequence"] < reflected[0]["sequence"]
        )
        checks["model_request_hash_matches_trace"] = (
            len(retrieved) == 1
            and len(injected) == len(second_calls)
            and bool(second_calls)
            and all(
                e["context_sha256"] == r["context_sha256"] == retrieved[0]["context_sha256"]
                and r["completed"]
                for e, r in zip(injected, second_calls, strict=True)
            )
        )
    else:
        checks["recall_disabled_emits_no_read_or_injection"] = not retrieved and not injected
    passed = all(checks.values())
    evidence = {
        "mode": mode,
        "scope": "fixed graphs; Worker memory plumbing, not Planner or M3 metrics",
        "recall_enabled": recall_enabled,
        "passed": passed,
        "checks": checks,
        "configs": configs,
        "requested_model": getattr(client, "model", None),
        "thinking": getattr(client, "thinking", None),
        "model_requests": audit.requests,
        "trace": TRACE_FILENAME,
        "memory_snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
        "memory_record_ids": [r["memory_id"] for r in records],
    }
    path = root / "memory-control.json"
    path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, passed


async def run(args) -> tuple[Path, bool]:
    if args.mock:
        return await run_pair(
            args.work_dir, MockMemoryModel(), mode="mock", recall_enabled=not args.disable_recall
        )
    client = DeepSeekClient.from_environment()
    try:
        return await run_pair(
            args.work_dir, client, mode="live", recall_enabled=not args.disable_recall
        )
    finally:
        await client.aclose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument("--work-dir", type=Path, default=Path("results/worker-memory"))
    parser.add_argument(
        "--disable-recall",
        action="store_true",
        help="Negative control: second task receives no memory and should miss marker",
    )
    args = parser.parse_args(argv)
    path, passed = asyncio.run(run(args))
    print(f"Worker memory controls: {'PASS' if passed else 'FAIL'}\nEvidence: {path}")
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
