import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from memory import JsonlMemory
from protocol.models import (
    AcceptanceRule,
    FailureCode,
    RetrievedMemory,
    Task,
    TaskResult,
    TaskStatus,
)
from runtime.profiles import load_profiles


def make_task(
    task_id: str = "research-1",
    agent: str = "researcher",
    title: str = "Research competitor pricing with official sources",
) -> Task:
    return Task(
        task_id=task_id,
        title=title,
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

    assert "Verified official source URLs" in (await memory.retrieve("researcher", task)).text
    assert await memory.retrieve("writer", task) == RetrievedMemory()
    assert memory.path_for("researcher").is_file()
    assert not memory.path_for("writer").exists()


@pytest.mark.asyncio
async def test_failed_reflection_handles_missing_critique(tmp_path):
    memory = JsonlMemory(tmp_path)
    task = make_task()
    failed = make_result(task, status=TaskStatus.FAILED, failure_code=FailureCode.TIMEOUT)

    await memory.reflect(task, failed)
    text = (await memory.retrieve("researcher", task)).text

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

    assert await memory.retrieve("researcher", make_task()) == RetrievedMemory()
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

    text = (await memory.retrieve("researcher", task)).text
    assert text.count("Previous success task") == 3
    assert len(text) <= 2400


@pytest.mark.asyncio
async def test_chinese_query_ranks_the_related_private_reflection_first(tmp_path):
    memory = JsonlMemory(tmp_path, max_records=1)
    seed_cases = [
        ("invoice", "核对发票总额", "先逐项比对再四舍五入。"),
        ("backup", "验证备份完整性", "签字前先还原一份样本。"),
        ("schedule", "核对轮班安排", "发布前先比对交班时间。"),
    ]
    for task_id, title, advice in seed_cases:
        seed = make_task(task_id, title=title)
        result = make_result(seed, status=TaskStatus.SUCCESS)
        result.summary = advice
        await memory.reflect(seed, result)

    query = make_task("invoice-query", title="重新核对发票总额")
    text = (await memory.retrieve("researcher", query)).text

    assert "先逐项比对再四舍五入。" in text
    assert "签字前先还原一份样本。" not in text
    assert "发布前先比对交班时间。" not in text


def test_profiles_load_from_repository():
    profiles = load_profiles(Path(__file__).parents[1] / "profiles")

    assert profiles["planner"].role == "planner"
    assert {profiles["researcher"].agent_id, profiles["writer"].agent_id} == {
        "researcher",
        "writer",
    }


async def test_retrieval_ids_match_text_after_character_limit(tmp_path):
    memory = JsonlMemory(tmp_path, max_records=3)
    query = make_task()
    for index in range(2):
        item = make_task(f"seed-{index}")
        result = make_result(item, status=TaskStatus.SUCCESS)
        result.summary = f"Pricing lesson exclusively numbered {index}"
        await memory.reflect(item, result)
    records = [
        json.loads(line) for line in memory.path_for("researcher").read_text().splitlines()
    ]
    # Isolate one record's exact size using the public adapter, then apply that cap.
    newest_only = await JsonlMemory(tmp_path, max_records=1).retrieve("researcher", query)
    memory.max_context_chars = len(newest_only.text) + 1
    trimmed = await memory.retrieve("researcher", query)
    assert trimmed == newest_only
    assert trimmed.memory_ids == [records[1]["memory_id"]]
    assert records[1]["execution_summary"] in trimmed.text
    assert records[0]["execution_summary"] not in trimmed.text
    memory.max_context_chars = 1
    assert await memory.retrieve("researcher", query) == RetrievedMemory()
