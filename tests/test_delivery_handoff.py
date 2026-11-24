"""Reproduce the Mid Test report failures at the delivery/scheduler boundary."""

import pytest

from protocol.delivery import DeliveryHandoffTurn, DeliveryResult
from protocol.models import ChatMessage
from runtime.client import ModelCallError, ModelOutputLimitError
from tests.test_delivery_lifecycle import reply, requirement, setup, write
from tests.test_discussion import ScriptedModel


async def test_last_step_truncation_gets_one_tool_free_retry(tmp_path):
    runner, config, args = setup(tmp_path, max_steps_per_turn=2)
    model = ScriptedModel(write("READY"), ModelOutputLimitError(600), reply("Short handoff."))
    result = await runner.run(config, **args, model=model)

    assert result.status == "passed" and result.tokens == 1700
    assert result.attempts[0].turns[0].content == "Short handoff."
    assert [req[2] for req in model.requests[1:]] == [1024, 1024]
    assert all(req[1] == [] for req in model.requests[1:])
    assert model.requests[2][0][-1].content == "Previous report was cut off. Use one line."


async def test_work_and_report_truncation_have_separate_recovery(tmp_path):
    runner, config, args = setup(tmp_path, max_steps_per_turn=3)
    config.max_output_tokens = 700
    model = ScriptedModel(
        ModelOutputLimitError(520), write("READY"), ModelOutputLimitError(590), reply("Saved.")
    )

    result = await runner.run(config, **args, model=model)

    assert result.status == "passed" and result.tokens == 2210
    assert result.attempts[0].turns[0].content == "Saved."
    assert [req[2] for req in model.requests[2:]] == [700, 700]
    assert all(req[1] == [] for req in model.requests[2:])


@pytest.mark.parametrize(
    "failure, issue",
    [
        ("length", "report_truncated"),
        ("markup", "invalid_report"),
        ("tool", "invalid_report"),
        ("empty", "invalid_report"),
    ],
)
async def test_bad_report_keeps_write_and_hands_off_to_final_owner(tmp_path, failure, issue):
    runner, config, args = setup(
        tmp_path, requirements=[requirement("editor")], max_steps_per_turn=2
    )
    config.speakers = ["lead", "editor"]
    config.moderator = "lead"
    lead = args["executor"].workspace("lead")
    lead.mkdir()
    args["workspaces"]["lead"] = lead
    args["profiles"]["lead"] = args["profiles"]["editor"].model_copy(update={"agent_id": "lead"})
    failures = {
        "length": [ModelOutputLimitError(600), ModelOutputLimitError(700)],
        "markup": [reply('<｜DSML｜invoke name="file_write">invalid</｜DSML｜invoke>')],
        "tool": [write("do not execute", 9, "outputs/unsafe.md")],
        "empty": [reply(" ")],
    }
    model = ScriptedModel(
        write("draft content", path="outputs/brief.md"), *failures[failure], write("READY"), reply()
    )
    emitted = []

    async def emit(turn):
        emitted.append(turn)
        if isinstance(turn, DeliveryHandoffTurn):
            assert (lead / "outputs/brief.md").read_text() == "draft content"
            target = args["workspaces"]["editor"] / "inputs/shared/lead/brief.md"
            target.parent.mkdir(parents=True)
            target.write_bytes((lead / "outputs/brief.md").read_bytes())

    result = await runner.run(config, **args, model=model, emit=emit)

    assert result.stop_reason == "accepted" and result.model_calls == 3 + len(failures[failure])
    handoff, editor = result.attempts[0].turns
    assert isinstance(handoff, DeliveryHandoffTurn)
    assert handoff.handoff_issue == issue and handoff.tool_results[0].ok
    assert "outputs/brief.md" in handoff.content and "no acceptance verdict" in handoff.content
    assert "DSML" not in handoff.content and not (lead / "outputs/unsafe.md").exists()
    assert [turn.speaker for turn in emitted] == ["lead", "editor"]
    assert editor.tool_results[0].ok
    assert "inputs/shared/lead/brief.md" in model.requests[-2][0][1].content
    assert DeliveryResult.model_validate_json(result.model_dump_json()) == result


async def test_failed_reports_do_not_stop_owner_rework_or_certify_a_draft(tmp_path):
    runner, config, args = setup(tmp_path, max_steps_per_turn=2)
    result = await runner.run(
        config,
        **args,
        model=ScriptedModel(
            write("draft"), ModelOutputLimitError(600), ModelOutputLimitError(700),
            write("READY", 2), reply("Then recheck the file."),
        ),
    )

    assert result.status == "passed"
    initial, rework = result.attempts
    assert initial.turns[0].handoff_issue == "report_truncated"
    assert initial.assessments[0].status == "failed"
    assert rework.assessments[0].status == "passed" and rework.changed_requirements == ["writer"]


async def test_repeated_truncated_work_isolated_without_executing_a_partial_write(tmp_path):
    runner, config, args = setup(tmp_path, requirements=[requirement(), requirement("editor")])
    args["plan"].max_rework_rounds = 0
    model = ScriptedModel(
        ModelOutputLimitError(800), ModelOutputLimitError(900), write("READY"), reply()
    )
    result = await runner.run(config, **args, model=model)

    assert result.status == "partial" and result.stop_reason == "rework_limit"
    handoff, editor = result.attempts[0].turns
    assert handoff.handoff_issue == "work_truncated" and handoff.tokens == 1700
    assert not handoff.tool_calls and not (args["workspaces"]["writer"] / "outputs").exists()
    assert editor.speaker == "editor" and editor.tool_results[0].ok


async def test_member_budget_stop_does_not_consume_the_next_members_opportunity(tmp_path):
    runner, config, args = setup(tmp_path, requirements=[requirement(), requirement("editor")])
    config.max_task_tokens = 8000
    args["plan"].max_rework_rounds = 0
    # Provider usage exceeded the first turn's 4,000 share but fits the task as a whole.
    too_expensive = write("must remain unapplied").model_copy(update={"tokens": 4001})
    model = ScriptedModel(too_expensive, write("READY"), reply())

    result = await runner.run(config, **args, model=model)

    first, second = result.attempts[0].turns
    assert first.handoff_issue == "contribution_budget" and first.tokens == 4001
    assert not first.tool_calls and not (args["workspaces"]["writer"] / "outputs").exists()
    assert second.speaker == "editor" and second.tool_results[0].ok
    assert result.status == "partial" and result.tokens == 5101


async def test_global_budget_exhausted_by_report_cannot_start_next_member(tmp_path):
    runner, config, args = setup(
        tmp_path, requirements=[requirement(), requirement("editor")], max_steps_per_turn=2
    )
    args["plan"].max_rework_rounds = 0
    config.max_task_tokens = 8000
    model = ScriptedModel(write("READY"), ModelOutputLimitError(7000))

    result = await runner.run(config, **args, model=model)

    assert result.stop_reason == "token_budget" and result.status == "partial"
    assert result.model_calls == 2 and result.tokens == 8000
    assert [t.speaker for t in result.attempts[0].turns] == ["writer"]
    assert result.attempts[0].turns[0].handoff_issue == "report_truncated"


async def test_attempt_stops_when_no_member_can_afford_a_report(tmp_path):
    runner, config, args = setup(tmp_path, requirements=[requirement(), requirement("editor")])
    config.max_task_tokens, config.max_rounds = 90, 3
    args["plan"].max_rework_rounds = 0

    result = await runner.run(config, **args, model=ScriptedModel())

    assert result.stop_reason == "token_budget" and result.status == "failed"
    assert result.model_calls == result.tokens == 0
    assert [t.handoff_issue for t in result.attempts[0].turns] == ["contribution_budget"] * 2
    assert {t.round for t in result.attempts[0].turns} == {1}


async def test_human_correction_after_interrupted_turn_prevents_more_work(tmp_path):
    runner, config, args = setup(
        tmp_path, requirements=[requirement(), requirement("editor")], max_steps_per_turn=2
    )

    async def emit(turn):
        args["human"].messages.append(ChatMessage(role="user", content="Use a new date."))

    model = ScriptedModel(write("READY"), reply(" "))
    result = await runner.run(config, **args, model=model, emit=emit)
    assert result.stop_reason == "plan_review_required" and result.status == "unverified"
    assert result.model_calls == 2 and result.pending_human_messages[0].content == "Use a new date."


async def test_provider_failure_is_not_treated_as_a_truncated_report(tmp_path):
    runner, config, args = setup(tmp_path, max_steps_per_turn=2)
    model = ScriptedModel(
        write("READY"), ModelCallError("invalid envelope", tokens=4321, usage_source="provider")
    )
    result = await runner.run(config, **args, model=model)
    assert result.status != "passed" and result.stop_reason == "model_error"
    assert result.model_calls == 2
    assert result.tokens == 5321  # Include rejected response usage without a recovery retry.
