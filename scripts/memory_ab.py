"""Run C's reproducible synthetic M3 memory-on/off control.

The control seeds equivalent private JSONL history for three recurring Worker
tasks, then runs the same fixed graph with memory injection enabled and disabled.
It is deliberately synthetic: it proves the retrieval/injection mechanism and
the experiment accounting, not a performance claim about a live model.

From the repository root:
    python -m scripts.memory_ab --work-dir results/m3-synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from harness.metrics import summarize_batch, summarize_result
from harness.verdict import FileVerifier
from memory.jsonl_memory import JsonlMemory
from memory.models import MemoryRecord
from orchestrator.approvals import PolicyApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ChatMessage,
    ModelResponse,
    RunConfig,
    Task,
    TaskGraph,
    ToolCall,
    ToolDefinition,
)
from sandbox.executor import SandboxToolExecutor
from tools.registry import build


@dataclass(frozen=True)
class ComparisonCase:
    case_id: str
    seed_title: str
    recall_title: str
    marker: str


CASES = (
    ComparisonCase(
        "invoice",
        "Review invoice totals before submitting the monthly report.",
        "Recheck the new invoice totals before submitting the monthly report.",
        "MEM-INVOICE-VERIFY-LINES",
    ),
    ComparisonCase(
        "backup",
        "Verify backup integrity before approving the maintenance window.",
        "Retest the next backup before approving the maintenance window.",
        "MEM-BACKUP-RESTORE-SAMPLE",
    ),
    ComparisonCase(
        "schedule",
        "Review shift schedules before publishing the roster.",
        "Recheck the updated shift roster before publishing it.",
        "MEM-SCHEDULE-COMPARE-HANDOVER",
    ),
)

_MARKER = re.compile(r"MEM-[A-Z0-9-]+")


class MemoryControlModel:
    """Fixture model whose recall response reads only injected memory context."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.requests: list[dict[str, bool | str]] = []

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        if messages[-1].role == "tool":
            return ModelResponse(content="Recorded the requested procedure.", tokens=2)

        prompt = next(message.content for message in messages if message.role == "user")
        payload = json.loads(prompt)
        task = payload["task"]
        context = payload["memory_context"]
        self.requests.append(
            {
                "task_id": task["task_id"],
                "marker_in_title": self.marker in task["title"],
                "marker_in_memory": self.marker in context,
            }
        )
        # The fixture intentionally ignores acceptance rules and can only recover
        # the marker from injected memory. This isolates the experimental variable.
        match = _MARKER.search(context)
        text = match.group(0) if match else "No prior procedure available."
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


def _graph(case: ComparisonCase) -> TaskGraph:
    output = f"outputs/{case.case_id}.md"
    task = Task(
        task_id=f"recall-{case.case_id}",
        title=case.recall_title,
        assignee="writer",
        outputs=[output],
        acceptance=[AcceptanceRule(type="text_contains", path=output, text=case.marker)],
        max_retries=0,
    )
    return TaskGraph(
        tasks=[task],
        final_task_id=task.task_id,
        goal_acceptance=[
            AcceptanceRule(type="text_contains", path=f"{case.case_id}.md", text=case.marker)
        ],
    )


def _seed_snapshot(memory: JsonlMemory, case: ComparisonCase) -> str:
    """Write deterministic private history so both groups start byte-for-byte equal."""
    record = MemoryRecord(
        memory_id=f"mem-{case.case_id}",
        agent_id="writer",
        task_summary=case.seed_title,
        outcome="success",
        execution_summary=f"Use the reusable procedure {case.marker}.",
        effective_strategy=[f"Apply {case.marker} before producing the output."],
        reusable_advice=f"Reuse {case.marker} for a related task.",
        evidence_task_id=f"seed-{case.case_id}",
        created_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    path = memory.path_for("writer")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run_memory_ab(work_dir: Path) -> dict[str, object]:
    """Run the frozen-history control and return JSON-serializable evidence."""
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    registry = build()
    groups: dict[str, object] = {}

    for group_name, memory_enabled in (("memory-off", False), ("memory-on", True)):
        reports = []
        cases = []
        for case in CASES:
            root = work_dir / group_name / case.case_id
            root.mkdir(parents=True, exist_ok=False)
            memory = JsonlMemory(root, max_records=1)
            snapshot_sha256 = _seed_snapshot(memory, case)
            model = MemoryControlModel(case.marker)
            executor = SandboxToolExecutor(root, {"file_write": registry["file_write"]})
            service = build_service(
                root=root,
                model=model,
                profiles={
                    "planner": AgentProfile(
                        agent_id="planner", role="planner", system_prompt="Plan fixed controls."
                    ),
                    "writer": AgentProfile(
                        agent_id="writer",
                        role="worker",
                        tools=["file_write"],
                        system_prompt="Use relevant private memory when it is provided.",
                    ),
                },
                tool_definitions=executor.tool_definitions(),
                executor=executor,
                verifier=FileVerifier(),
                approver=PolicyApprover(),
                memory=memory,
            )
            config = RunConfig(
                run_id=f"m3-{group_name}-{case.case_id}",
                memory_enabled=memory_enabled,
                write_memory=False,
                max_agent_steps=4,
                max_task_tokens=16000,
                max_output_tokens=2048,
            )
            graph = _graph(case)
            result = await service.run_graph(graph, config)
            raw_path = root / "raw-result.json"
            raw_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            reports.append(summarize_result(case.case_id, 1, result, config, root))
            cases.append(
                {
                    "case_id": case.case_id,
                    "marker": case.marker,
                    "starting_memory_sha256": snapshot_sha256,
                    "marker_absent_from_recall_title": all(
                        not request["marker_in_title"] for request in model.requests
                    ),
                    "model_requests": model.requests,
                    "raw_result": raw_path.relative_to(work_dir).as_posix(),
                }
            )

        groups[group_name] = {
            "memory_enabled": memory_enabled,
            "write_memory": False,
            "summary": summarize_batch(reports).model_dump(mode="json"),
            "cases": cases,
        }

    return {
        "benchmark": "m3-synthetic-frozen-history-v1",
        "scope": (
            "Deterministic Worker-memory control. It validates the retrieval/injection "
            "path and accounting only; it is not a live-model performance result."
        ),
        "controlled_variables": {
            "cases": [case.case_id for case in CASES],
            "model": "MemoryControlModel",
            "tools": ["file_write"],
            "acceptance": "text_contains exact marker",
            "starting_history": "one identical fixed private JSONL record per case",
            "write_memory": False,
        },
        "variable": "RunConfig.memory_enabled",
        "groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("results/m3-synthetic"))
    args = parser.parse_args()
    report = asyncio.run(run_memory_ab(args.work_dir))
    path = args.work_dir / "evaluation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, group in report["groups"].items():
        summary = group["summary"]
        print(
            f"{name}: goals {summary['goals_passed']}/{summary['goals_total']}; "
            f"subtasks {summary['subtasks_passed']}/{summary['subtasks_total']}"
        )
    print(f"Evidence: {path}")


if __name__ == "__main__":
    main()
