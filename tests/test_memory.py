from datetime import UTC, datetime
from pathlib import Path

import pytest

from memory import JsonlMemory
from protocol.models import AcceptanceRule, FailureCode, Task, TaskResult, TaskStatus
from runtime.profiles import load_profiles


def make_task(task_id: str = "research-1", agent: str = "researcher") -> Task:
    return Task(
        task_id=task_id,
        title="Research competitor pricing with official sources",
        assignee=agent,
        acceptance=[
            AcceptanceRule(type="text_contains", path="outputs/pricing.md", text="Sources")
        ],
        outputs=["outputs/pricing.md"],
    )


def make_result(task: Task, *, status: TaskStatus, failure_code=None) -> TaskResult:
    return TaskResult(
        task_id=task.task_id,
        agent_id=task.assignee,
        status=status,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        summary=(
            "Verified official source URLs"
            if status == TaskStatus.SUCCESS
            else "Acceptance failed"
        ),
        failure_code=failure_code,
    )


@pytest.mark.asyncio
async def test_reflect_and_retrieve_are_agent_isolated(tmp_path):
    memory = JsonlMemory(tmp_path)
    task = make_task()
    await memory.reflect(task, make_result(task, status=TaskStatus.SUCCESS))

    assert "Verified official source URLs" in await memory.retrieve("researcher", task)
    assert await memory.retrieve("writer", task) == ""
    assert memory.path_for("researcher").is_file()
    assert not memory.path_for("writer").exists()


@pytest.mark.asyncio
async def test_failed_reflection_handles_missing_critique(tmp_path):
    memory = JsonlMemory(tmp_path)
    task = make_task()
    failed = make_result(task, status=TaskStatus.FAILED, failure_code=FailureCode.TIMEOUT)

    await memory.reflect(task, failed)
    text = await memory.retrieve("researcher", task)

    assert "timeout" in text
    assert "Acceptance failed" in text


@pytest.mark.asyncio
async def test_retrieval_skips_malformed_and_cross_agent_records(tmp_path):
    memory = JsonlMemory(tmp_path)
    path = memory.path_for("researcher")
    path.parent.mkdir(parents=True)
    path.write_text(
        "not-json\n"
        '{"memory_id":"other","agent_id":"writer","task_summary":"pricing",'
        '"outcome":"success","evidence_task_id":"x","created_at":"2026-11-06T00:00:00Z"}\n',
        encoding="utf-8",
    )

    assert await memory.retrieve("researcher", make_task()) == ""
    assert len(memory.diagnostics) == 2


@pytest.mark.asyncio
async def test_retrieval_is_bounded_to_three_records(tmp_path):
    memory = JsonlMemory(tmp_path, max_records=3)
    task = make_task()
    for index in range(5):
        current = make_task(f"research-{index}")
        current_result = make_result(current, status=TaskStatus.SUCCESS)
        current_result.summary = f"Pricing sources lesson {index}"
        await memory.reflect(current, current_result)

    text = await memory.retrieve("researcher", task)
    assert text.count("Previous success task") == 3
    assert len(text) <= 2400


def test_profiles_load_from_repository():
    profiles = load_profiles(Path(__file__).parents[1] / "profiles")

    assert profiles["planner"].role == "planner"
    assert {profiles["researcher"].agent_id, profiles["writer"].agent_id} == {
        "researcher",
        "writer",
    }
