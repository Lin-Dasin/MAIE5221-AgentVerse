"""Behavioral controls for the Mid Test allocation and unsaved-repair regressions."""

import errno

import pytest

from orchestrator.delivery import _repair_allowance
from orchestrator.discussion import RoundRobinDiscussionEngine
from protocol.delivery import DeliveryPlan
from protocol.models import ModelResponse, ToolCall
from runtime.client import ModelOutputLimitError
from sandbox.executor import RegisteredTool, SandboxToolExecutor
from tests.helpers import profile
from tests.test_delivery_lifecycle import reply, requirement, setup, write
from tests.test_discussion import HumanQueue, ScriptedModel, discussion
from tests.test_discussion_delivery import call
from tools.fs import FILE_LIST, FILE_READ, FILE_WRITE, list_dir, read_file, write_file


def file_executor(root):
    return SandboxToolExecutor(
        root,
        {
            "file_list": RegisteredTool(FILE_LIST, list_dir, ("path",)),
            "file_read": RegisteredTool(FILE_READ, read_file, ("path",)),
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
        },
    )


def test_repair_allowance_never_splits_below_a_workable_turn():
    assert _repair_allowance(36_000, 3) == 25_000
    assert _repair_allowance(36_000, 2) == 25_000
    assert _repair_allowance(10_000, 3) == 10_000
    assert _repair_allowance(36_000, 0) == 36_000


async def managed_run(tmp_path, model, *, repair=False, max_steps=8):
    executor = file_executor(tmp_path)
    target = executor.workspace("writer") / "outputs/final.md"
    target.parent.mkdir(parents=True)
    target.write_text("draft")
    engine = RoundRobinDiscussionEngine(
        [FILE_LIST, FILE_READ, FILE_WRITE],
        max_steps_per_turn=max_steps,
        prompt_token_counter=lambda *_: 100,
    )
    result = await engine.for_delivery(repair=repair).run(
        discussion(max_rounds=1, max_output_tokens=8192),
        profiles={"writer": profile(tools=["file_list", "file_read", "file_write"])},
        history=[],
        model=model,
        executor=executor,
        human=HumanQueue(),
        emit=None,
    )
    return result, target, engine


async def test_midtest_prompt_cost_allows_four_owners_to_inspect_write_and_check(tmp_path):
    owners = ["writer", "dev", "editor", "reviewer"]
    runner, config, args = setup(tmp_path, requirements=[requirement(o) for o in owners])
    executor = file_executor(tmp_path)
    for owner in owners:
        source = executor.workspace(owner) / "inputs/draft.md"
        source.parent.mkdir(parents=True)
        source.write_text("Draft with pending marker")
        args["profiles"][owner].tools = ["file_list", "file_read", "file_write"]
    # The first saved Mid Test call estimated 4,511 prompt tokens. Use that cost
    # and plausible returned usage on every exchange, including the final reply.
    counter = lambda *_: 4511  # noqa: E731
    runner.counter = counter
    runner.engine = RoundRobinDiscussionEngine(
        [FILE_LIST, FILE_READ, FILE_WRITE],
        prompt_token_counter=counter,
    )
    config.max_task_tokens, config.max_output_tokens, config.max_rounds = 240000, 8192, 4
    responses = []
    for i, _ in enumerate(owners):
        for response in (
            call("file_list", f"{i}-list", path="inputs"),
            call("file_read", f"{i}-read", path="inputs/draft.md"),
            write("READY", f"{i}-write"),
            call("file_read", f"{i}-verify", path="outputs/final.md"),
        ):
            responses.append(response.model_copy(update={"tokens": 5000}))
        responses.append(reply().model_copy(update={"tokens": 4600}))
    model = ScriptedModel(
        *responses, *(reply().model_copy(update={"tokens": 4600}) for _ in range(40))
    )
    args["executor"] = executor
    result = await runner.run(config, **args, model=model)
    assert result.status == "passed"
    assert result.tokens <= config.max_task_tokens
    for index, owner in enumerate(owners):
        assert (executor.workspace(owner) / "outputs/final.md").read_text() == "READY"
        requests = model.requests[index * 5 : index * 5 + 5]
        assert "file_read" in {t.name for t in requests[1][1]}
        assert {t.name for t in requests[2][1]} == {"file_write"}
        assert "file_read" in {t.name for t in requests[3][1]}
        assert [r[2] for r in requests[:3]] == [2048, 2048, 8192]
        assert not any("last tool step" in m.content for r in requests for m in r[0])
    assert not runner.engine._delivery_mode  # The shared standalone engine was not mutated.


async def test_repair_blocks_repeated_reads_then_persists_and_reopens_verification(tmp_path):
    model = ScriptedModel(
        call("file_read", 1, path="outputs/final.md"),
        call("file_list", 2, path="outputs"),  # Ignores the offered tool list.
        call("file_read", 3, path="outputs/final.md"),
        write("READY", 4),
        call("file_read", 5, path="outputs/final.md"),
        reply(),
    )
    result, target, _ = await managed_run(tmp_path, model, repair=True)
    turn = result.turns[0]
    assert target.read_text() == "READY"
    assert [r.ok for r in turn.tool_results] == [True, False, False, True, True]
    assert {t.name for t in model.requests[1][1]} == {"file_write"}
    assert "file_read" in {t.name for t in model.requests[4][1]}
    assert turn.tool_results[-1].data["text"] == "READY"


async def test_batched_directory_discovery_has_a_dispatch_quota(tmp_path):
    model = ScriptedModel(
        ModelResponse(
            tokens=1000,
            tool_calls=[
                ToolCall(call_id=str(i), name="file_list", arguments={"path": path})
                for i, path in enumerate([".", "outputs", "outputs/missing", "elsewhere"])
            ],
        ),
        write("READY", 5),
        reply(),
    )
    result, _, _ = await managed_run(tmp_path, model)
    assert [r.ok for r in result.turns[0].tool_results[:4]] == [True, True, False, False]
    assert "file_list" not in {t.name for t in model.requests[1][1]}


async def test_large_early_write_gets_bounded_retry_with_production_output_cap(tmp_path):
    model = ScriptedModel(ModelOutputLimitError(2000), write("READY", 2), reply())
    result, target, _ = await managed_run(tmp_path, model)
    assert target.read_text() == "READY" and result.turns[0].tokens == 3100
    assert model.requests[0][2] == 2048 and model.requests[1][2] == 8192
    assert {t.name for t in model.requests[1][1]} == {"file_write"}


@pytest.mark.parametrize("repeats", [False, True])
async def test_plain_dsml_is_never_executed_and_only_gets_one_retry(tmp_path, repeats):
    markup = reply('<｜DSML｜invoke name="file_write">READY</｜DSML｜invoke>')
    model = ScriptedModel(markup, markup if repeats else write("READY", 2), reply())
    result, target, _ = await managed_run(tmp_path, model)
    assert target.read_text() == ("draft" if repeats else "READY")
    assert result.stopped_reason == ("invalid_model_output" if repeats else "max_rounds")
    assert len(model.requests) == (2 if repeats else 3)


async def test_no_progress_is_reported_then_a_targeted_change_is_accepted(tmp_path):
    runner, config, args = setup(tmp_path)
    model = ScriptedModel(write("draft"), reply(), write("draft"), reply(), write("READY"), reply())
    result = await runner.run(config, **args, model=model)
    assert result.status == "passed"
    assert result.attempts[1].unchanged_requirements == ["writer"]
    assert not result.attempts[1].changed_requirements
    assert result.attempts[2].changed_requirements == ["writer"]
    assert "no_artifact_progress" in model.requests[4][0][0].content
    assert "wrote outputs/final.md" in model.requests[4][0][0].content
    assert "Team delivery map" in model.requests[0][0][0].content
    assert (args["workspaces"]["writer"] / "outputs/final.md").read_text() == "READY"


async def test_changed_bytes_without_fixed_defect_are_still_failed(tmp_path):
    runner, config, args = setup(tmp_path)
    args["plan"].max_rework_rounds = 1
    result = await runner.run(
        config, **args, model=ScriptedModel(write("wrong"), reply(), write("still wrong"), reply())
    )
    assert result.status == "failed" and result.stop_reason == "rework_limit"
    assert result.attempts[1].changed_requirements == ["writer"]
    assert "text_missing" in result.assessments[0].defects[0]


async def test_fingerprints_cover_partial_deliveries_and_missing_files(tmp_path):
    runner, config, args = setup(
        tmp_path,
        requirements=[
            requirement(
                artifacts=["outputs/final.md", "outputs/missing.md"],
            )
        ],
    )
    args["plan"].max_rework_rounds = 1
    result = await runner.run(
        config, **args, model=ScriptedModel(write("draft"), reply(), write("READY"), reply())
    )
    assert result.status == "failed"
    assert result.attempts[1].changed_requirements == ["writer"]
    assert "missing_artifact" in result.assessments[0].defects[0]


async def test_unsafe_fingerprints_are_not_labeled_as_no_progress(tmp_path):
    from orchestrator.delivery_checks import delivery_fingerprints

    root = tmp_path / "writer"
    root.mkdir()
    try:
        (root / "outputs").symlink_to(tmp_path, target_is_directory=True)
    except NotImplementedError:
        pytest.skip("symlink creation unavailable on this host")
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP} or getattr(
            exc, "winerror", None
        ) == 1314:
            pytest.skip("symlink creation unavailable or not permitted on this host")
        raise
    plan = DeliveryPlan(goal="goal", requirements=[requirement()])
    assert delivery_fingerprints(plan, {"writer": root}) == {}


async def test_final_step_never_dispatches_tools_in_managed_mode(tmp_path):
    model = ScriptedModel(call("file_read", 1, path="outputs/final.md"), write("READY", 2))
    result, target, _ = await managed_run(tmp_path, model, max_steps=2)
    assert not model.requests[1][1]
    assert result.stopped_reason == "max_rounds" and target.read_text() == "draft"
    turn = result.turns[0]
    assert turn.handoff_issue == "invalid_report"
    assert [call.name for call in turn.tool_calls] == ["file_read"]


async def test_inspection_cannot_spend_reads_reserved_for_saved_artifact(tmp_path):
    model = ScriptedModel(
        ModelResponse(
            tokens=1000,
            tool_calls=[
                ToolCall(call_id=str(i), name="file_read", arguments={"path": "outputs/final.md"})
                for i in range(5)
            ],
        ),
        write("READY", 6),
        call("file_read", 7, path="outputs/final.md"),
        reply(),
    )
    result, target, _ = await managed_run(tmp_path, model)
    assert target.read_text() == "READY"
    results = result.turns[0].tool_results
    assert [r.ok for r in results[:5]] == [True, True, True, True, False]
    assert results[-1].data["text"] == "READY"


async def test_managed_production_still_denies_registered_protected_tool(tmp_path):
    from tools.fs import FILE_DELETE, delete_file

    runner, config, args = setup(tmp_path)
    executor = SandboxToolExecutor(
        tmp_path,
        {
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
            "file_delete": RegisteredTool(FILE_DELETE, delete_file, ("path",)),
        },
    )
    target = executor.workspace("writer") / "outputs/keep.md"
    target.parent.mkdir(parents=True)
    target.write_text("retained")
    runner.engine = RoundRobinDiscussionEngine(
        [FILE_WRITE, FILE_DELETE],
        prompt_token_counter=lambda *_: 100,
    )
    args["profiles"]["writer"].tools = ["file_write", "file_delete"]
    args["executor"] = executor
    model = ScriptedModel(
        reply("I will fix it."),
        call("file_delete", 2, path="outputs/keep.md"),
        write("READY", 3),
        reply(),
    )
    result = await runner.run(config, **args, model=model)
    assert result.status == "passed" and target.read_text() == "retained"
    assert not result.attempts[0].turns[0].tool_results[0].ok
    assert all("file_delete" not in {t.name for t in r[1]} for r in model.requests)


async def test_repair_keeps_one_targeted_source_opportunity_before_production(tmp_path):
    from protocol.models import ToolResult
    from tools.web_search import WEB_SEARCH

    executor = file_executor(tmp_path)
    target = executor.workspace("writer") / "outputs/final.md"
    target.parent.mkdir(parents=True)
    target.write_text("Unsourced draft")

    class SearchAdapter:
        async def execute(self, call, context):
            if call.name == "web_search":
                return ToolResult(
                    ok=True,
                    data={
                        "results": [
                            {"url": "https://example.org/source", "content": "Evidence excerpt"}
                        ]
                    },
                )
            return await executor.execute(call, context)

    model = ScriptedModel(
        call("file_read", 1, path="outputs/final.md"),
        call("web_search", 2, query="specific missing source"),
        write("READY with source https://example.org/source", 3),
        reply(),
    )
    result = (
        await RoundRobinDiscussionEngine(
            [FILE_LIST, FILE_READ, FILE_WRITE, WEB_SEARCH],
            prompt_token_counter=lambda *_: 100,
        )
        .for_delivery(repair=True)
        .run(
            discussion(max_rounds=1),
            profiles={
                "writer": profile(tools=["file_list", "file_read", "file_write", "web_search"])
            },
            history=[],
            model=model,
            executor=SearchAdapter(),
            human=HumanQueue(),
            emit=None,
        )
    )
    assert (
        result.stopped_reason == "max_rounds" and "https://example.org/source" in target.read_text()
    )
    assert {t.name for t in model.requests[1][1]} == {"file_write", "web_search"}
    assert {t.name for t in model.requests[2][1]} == {"file_write"}


async def test_current_write_receipt_survives_final_prompt_compaction(tmp_path):
    model = ScriptedModel(
        write("READY", 1),
        *(call("file_read", i, path="outputs/final.md") for i in range(2, 8)),
        reply(),
    )
    result, target, _ = await managed_run(tmp_path, model)
    assert target.read_text() == "READY" and result.stopped_reason == "max_rounds"
    final_messages, final_tools, cap = model.requests[-1]
    assert not final_tools
    assert cap == 1024
    assert [m.role for m in final_messages] == ["system", "user"]
    assert "wrote outputs/final.md" in final_messages[1].content
    assert "quality not verified" in final_messages[1].content
    assert all(not m.tool_calls and m.reasoning_content is None for m in final_messages)


async def test_unrelated_placeholder_does_not_unlock_verification(tmp_path):
    runner, config, args = setup(tmp_path)
    executor = file_executor(tmp_path)
    args["executor"] = executor
    args["profiles"]["writer"].tools = ["file_write", "file_read"]
    runner.engine = RoundRobinDiscussionEngine(
        [FILE_WRITE, FILE_READ],
        prompt_token_counter=lambda *_: 100,
    )
    model = ScriptedModel(
        write("placeholder", 1, path="inputs/noop.md"),
        write("helper", 2, path="outputs/helper.md"),
        call("file_read", 3, path="outputs/helper.md"),
        write("READY", 4),
        call("file_read", 5, path="outputs/final.md"),
        reply(),
    )
    result = await runner.run(config, **args, model=model)
    assert result.status == "passed"
    results = result.attempts[0].turns[0].tool_results
    assert [r.ok for r in results] == [False, True, False, True, True]
    assert not (executor.workspace("writer") / "inputs/noop.md").exists()
    assert {t.name for t in model.requests[2][1]} == {"file_write"}
    assert "file_read" in {t.name for t in model.requests[4][1]}


@pytest.mark.parametrize(
    "path", ["inputs/noop.md", "outputs/../inputs/noop.md", "/outputs/a.md", r"outputs\a.md"]
)
def test_managed_write_path_gate_rejects_input_mutation_and_traversal(path):
    from runtime.discussion_delivery_policy import output_path

    assert output_path(path) is None
