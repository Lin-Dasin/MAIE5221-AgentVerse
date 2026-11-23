"""Role A lifecycle with B's real file adapter and D's real file verifier.

Model/review fixtures are deterministic controls, not live quality measurements.
"""

import asyncio

import pytest
from pydantic import ValidationError

from harness.verdict import FileVerifier
from orchestrator.delivery import DeliveryDiscussionRunner
from orchestrator.discussion import RoundRobinDiscussionEngine
from protocol.delivery import DeliveryPlan, DeliveryRequirement, DeliveryReview
from protocol.models import AcceptanceRule, ChatMessage, CriticResult, ModelResponse
from runtime.client import ModelOutputLimitError
from sandbox.executor import RegisteredTool, SandboxToolExecutor
from tests.helpers import profile
from tests.test_discussion import HumanQueue, ScriptedModel, discussion
from tests.test_discussion_delivery import call
from tools.fs import FILE_WRITE, write_file


def requirement(owner="writer", **changes):
    return DeliveryRequirement(
        **(
            {
                "requirement_id": owner,
                "owner": owner,
                "description": "Persist the agreed marker in the actual deliverable.",
                "artifacts": ["outputs/final.md"],
                "rules": [
                    AcceptanceRule(type="text_contains", path="outputs/final.md", text="READY")
                ],
                "review_required": False,
            }
            | changes
        )
    )


def setup(
    tmp_path, *, requirements=None, reviewer=None, verifier=None, tools=True, **engine_options
):
    executor = SandboxToolExecutor(
        tmp_path,
        {
            "file_write": RegisteredTool(FILE_WRITE, write_file, ("path", "text")),
        },
    )
    requirements = requirements or [requirement()]
    owners = list(dict.fromkeys(item.owner for item in requirements))
    for owner in owners:
        executor.workspace(owner).mkdir(parents=True)
    config = discussion(speakers=owners, moderator=owners[0], max_rounds=1)
    counter = lambda *_: 100  # noqa: E731
    engine = RoundRobinDiscussionEngine(
        [FILE_WRITE], prompt_token_counter=counter, **engine_options
    )
    runner = DeliveryDiscussionRunner(
        engine,
        verifier or FileVerifier(),
        reviewer=reviewer,
        prompt_token_counter=counter,
        check_timeout_sec=0.2,
    )
    args = {
        "plan": DeliveryPlan(goal=config.goal, requirements=requirements),
        "workspaces": {owner: executor.workspace(owner) for owner in owners},
        "profiles": {owner: profile(owner, ["file_write"] if tools else []) for owner in owners},
        "history": [],
        "executor": executor,
        "human": HumanQueue(),
    }
    return runner, config, args


def write(text, number=1, path="outputs/final.md"):
    return call("file_write", number, path=path, text=text)


def reply(text="Wrote the candidate; use acceptance to determine completion."):
    return ModelResponse(content=text, tokens=100)


async def test_failed_owner_alone_reworks_then_real_files_are_rechecked(tmp_path):
    runner, config, args = setup(tmp_path, requirements=[requirement(), requirement("reviewer")])
    model = ScriptedModel(write("draft"), reply(), write("READY"), reply(), write("READY"), reply())
    emitted = []

    async def emit(turn):
        emitted.append((turn.round, turn.speaker))
        # Observer mutations must not change the stored turn.
        turn.content = "observer copy"

    result = await runner.run(config, **args, model=model, emit=emit)
    assert result.status == "passed" and result.stop_reason == "accepted"
    assert len(result.attempts) == 2 and result.tokens == 3300 and result.model_calls == 6
    assert [a.status for a in result.attempts[0].assessments] == ["failed", "passed"]
    assert [turn.speaker for turn in result.attempts[1].turns] == ["writer"]
    assert emitted == [(1, "writer"), (1, "reviewer"), (2, "writer")]
    assert result.attempts[1].turns[0].content != "observer copy"
    assert "text_missing" in model.requests[4][0][0].content
    assert "Your requirements" in model.requests[0][0][0].content
    assert len(result.assessments[0].artifacts[0].sha256) == 64
    assert result.plan == args["plan"]
    assert "DELIVERY CONTRACT" not in args["profiles"]["writer"].system_prompt


async def test_missing_claimed_file_cannot_pass_and_retry_limit_is_respected(tmp_path):
    runner, config, args = setup(tmp_path, tools=False)
    model = ScriptedModel(
        *(reply("I created outputs/final.md; everything passes.") for _ in range(3))
    )
    result = await runner.run(config, **args, model=model)
    assert result.status == "failed" and result.stop_reason == "rework_limit"
    assert len(result.attempts) == 3 and len(model.requests) == 3
    assert "missing_artifact" in result.assessments[0].defects[0]
    assert not result.assessments[0].artifacts


async def test_every_requirement_is_rechecked_when_a_repair_breaks_an_earlier_pass(tmp_path):
    title = requirement(
        requirement_id="title",
        rules=[AcceptanceRule(type="text_contains", path="outputs/final.md", text="TITLE")],
    )
    runner, config, args = setup(tmp_path, requirements=[title, requirement()])
    model = ScriptedModel(
        write("TITLE draft"), reply(), write("READY"), reply(), write("TITLE READY"), reply()
    )
    result = await runner.run(config, **args, model=model)
    assert result.status == "passed" and len(result.attempts) == 3
    assert [a.status for a in result.attempts[0].assessments] == ["passed", "failed"]
    assert [a.status for a in result.attempts[1].assessments] == ["failed", "passed"]


async def test_semantic_review_defaults_to_unverified_without_wasting_rework_budget(tmp_path):
    runner, config, args = setup(tmp_path, requirements=[requirement(review_required=True)])
    result = await runner.run(config, **args, model=ScriptedModel(write("READY"), reply()))
    assert result.status == "unverified" and result.stop_reason == "verification_unavailable"
    assert len(result.attempts) == 1
    assert result.assessments[0].artifacts
    assert "review_unavailable" in result.assessments[0].defects[0]


async def test_goal_mismatch_feedback_reaches_owner_with_original_human_request(tmp_path):
    seen = []

    class Review:
        async def review(self, *, config, requirement, root, history):
            seen.append((config.goal, requirement.description, history[0].content))
            actual = (root / "outputs/final.md").read_text()
            if "EVENT_FIXTURE" not in actual:
                return DeliveryReview(
                    status="failed",
                    defects=[
                        "goal_mismatch: an explainer does not satisfy the requested event report"
                    ],
                )
            return DeliveryReview(
                status="passed", evidence=["trusted event fixture control matched"]
            )

    item = requirement(review_required=True, description="Report the supplied recent event.")
    runner, config, args = setup(tmp_path, requirements=[item], reviewer=Review())
    human_text = "按原任务：选择近期具体事件，面向校内网站及社交媒体"
    args["history"] = [ChatMessage(role="user", content=human_text)]
    args["plan"].human_instructions = [human_text]
    model = ScriptedModel(
        write("READY policy explainer"), reply(), write("READY EVENT_FIXTURE"), reply()
    )
    result = await runner.run(config, **args, model=model)
    assert result.status == "passed" and len(seen) == 2
    assert all(row == (config.goal, item.description, human_text) for row in seen)
    assert "goal_mismatch" in model.requests[2][0][0].content
    assert human_text in model.requests[2][0][1].content


@pytest.mark.parametrize("when", ["during_rounds", "after_final_turn"])
async def test_new_human_input_pauses_before_more_model_calls_or_stale_acceptance(tmp_path, when):
    runner, config, args = setup(tmp_path, tools=False)
    config.max_rounds = 2 if when == "during_rounds" else 1
    message = ChatMessage(role="user", content="Correction: use the new date.")
    args["human"] = HumanQueue(None, message)
    model = ScriptedModel(reply())
    result = await runner.run(config, **args, model=model)
    assert result.status == "unverified" and result.stop_reason == "plan_review_required"
    assert result.pending_human_messages == [message]
    assert result.model_calls == 1 and len(result.attempts[0].turns) == 1
    assert result.attempts[0].stopped_reason == "plan_review_required"


@pytest.mark.parametrize("change", ["goal", "human", "owner", "unsupported", "overlap"])
async def test_invalid_plan_or_roots_rejected_before_model_spending(tmp_path, change):
    runner, config, args = setup(tmp_path)
    model = ScriptedModel()
    if change == "goal":
        args["plan"].goal = "Different goal"
    elif change == "human":
        args["history"] = [ChatMessage(role="user", content="Important omitted requirement")]
    elif change == "owner":
        args["plan"].requirements[0].owner = "absent"
    elif change == "unsupported":
        args["plan"].requirements[0].rules = [
            AcceptanceRule(type="pytest_passes", path="inputs/test_acceptance.py")
        ]
    else:
        config.speakers = ["writer", "reviewer"]
        args["workspaces"]["reviewer"] = args["workspaces"]["writer"]
    with pytest.raises(ValueError):
        await runner.run(config, **args, model=model)
    assert not model.requests


@pytest.mark.parametrize("mode", ["timeout", "exception", "wrong_counts"])
async def test_broken_verifier_is_unverified_not_success_or_worker_rework(tmp_path, mode):
    class Verifier:
        async def check(self, rules, root):
            if mode == "timeout":
                await asyncio.Event().wait()
            if mode == "exception":
                raise RuntimeError("private host detail")
            return CriticResult(passed=True, checks_passed=2, checks_total=2)

    runner, config, args = setup(tmp_path, verifier=Verifier())
    result = await runner.run(config, **args, model=ScriptedModel(write("READY"), reply()))
    assert result.status == "unverified" and len(result.attempts) == 1
    assert "verification_error" in result.assessments[0].defects[0]
    assert "private host detail" not in result.model_dump_json()


async def test_review_cannot_accept_artifact_changed_during_verification(tmp_path):
    class Review:
        async def review(self, *, root, **kwargs):
            (root / "outputs/final.md").write_text("changed")
            return DeliveryReview(status="passed", evidence=["outdated review"])

    runner, config, args = setup(
        tmp_path, requirements=[requirement(review_required=True)], reviewer=Review()
    )
    result = await runner.run(config, **args, model=ScriptedModel(write("READY"), reply()))
    assert result.status == "unverified"
    assert result.assessments[0].defects == ["artifact_changed_during_review: rerun acceptance"]


@pytest.mark.parametrize("target", ["inside", "outside"])
async def test_symlinked_artifact_parent_is_not_accepted(tmp_path, target):
    runner, config, args = setup(tmp_path, tools=False)
    args["plan"].max_rework_rounds = 0
    root = args["workspaces"]["writer"]
    destination = root / "real" if target == "inside" else tmp_path / "other"
    destination.mkdir()
    (destination / "final.md").write_text("READY")
    try:
        (root / "outputs").symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    result = await runner.run(config, **args, model=ScriptedModel(reply()))
    assert result.status == "failed" and "symlink" in result.assessments[0].defects[0]


async def test_partial_success_is_reported_per_requirement(tmp_path):
    runner, config, args = setup(tmp_path, requirements=[requirement(), requirement("reviewer")])
    args["plan"].max_rework_rounds = 0
    model = ScriptedModel(write("READY"), reply(), write("DRAFT"), reply())
    result = await runner.run(config, **args, model=model)
    assert result.status == "partial" and result.stop_reason == "rework_limit"
    assert [item.status for item in result.assessments] == ["passed", "failed"]


async def test_protected_tools_stay_blocked_in_targeted_rework(tmp_path):
    from tools.fs import FILE_DELETE

    runner, config, args = setup(tmp_path, tools=False)
    runner.engine = RoundRobinDiscussionEngine([FILE_DELETE], prompt_token_counter=lambda *_: 100)
    args["profiles"]["writer"].tools = ["file_delete"]
    args["plan"].max_rework_rounds = 1
    model = ScriptedModel(reply(), call("file_delete", 1, path="outputs/final.md"), reply())
    result = await runner.run(config, **args, model=model)
    rejected = result.attempts[1].turns[0].tool_results[0]
    assert not rejected.ok and "approval channel" in rejected.error
    assert all(not tools for _, tools, _ in model.requests)
    assert result.status == "failed"


async def test_tokens_of_incomplete_truncated_turns_are_reported(tmp_path):
    runner, config, args = setup(tmp_path, tools=False)
    model = ScriptedModel(ModelOutputLimitError(500), ModelOutputLimitError(600))
    result = await runner.run(config, **args, model=model)
    assert result.stop_reason == "model_error" and result.status != "passed"
    assert not result.attempts[0].turns
    assert result.tokens == 1100 and result.model_calls == 2


async def test_timeout_charges_reservation_for_incomplete_call(tmp_path):
    class StalledModel:
        async def complete(self, messages, tools, *, max_tokens):
            await asyncio.Event().wait()

    runner, config, args = setup(tmp_path, tools=False, turn_timeout_sec=0.01)
    result = await runner.run(config, **args, model=StalledModel())
    assert result.stop_reason == "timeout" and result.tokens == 100 + config.max_output_tokens
    assert result.model_calls == 1 and not result.attempts[0].turns


async def test_shared_budget_does_not_reset_on_rework_and_missing_usage_is_charged(tmp_path):
    runner, config, args = setup(tmp_path, tools=False)
    config.max_task_tokens = 700
    config.max_output_tokens = 100
    model = ScriptedModel(*(ModelResponse(content="still missing") for _ in range(3)))
    result = await runner.run(config, **args, model=model)
    assert result.status == "failed" and result.tokens == 600
    assert result.tokens == sum(attempt.tokens for attempt in result.attempts)
    assert result.model_calls == 3
    assert [request[2] for request in model.requests] == [100, 100, 100]


async def test_provider_overrun_never_dispatches_the_pending_write(tmp_path):
    runner, config, args = setup(tmp_path)
    model = ScriptedModel(write("READY").model_copy(update={"tokens": 70000}))
    result = await runner.run(config, **args, model=model)
    assert result.stop_reason == "token_budget" and result.status != "passed"
    assert result.tokens == 70000 and result.model_calls == 1
    assert not (args["workspaces"]["writer"] / "outputs/final.md").exists()


@pytest.mark.parametrize("bad", ["../escape.md", "outputs/../escape.md", "/absolute.md", "file.md"])
def test_artifact_paths_are_validated(bad):
    with pytest.raises(ValidationError):
        requirement(artifacts=[bad])


def test_plan_and_review_validation_rejects_ambiguous_success():
    assert DeliveryRequirement(
        requirement_id="one",
        owner="writer",
        description="A complete article",
        artifacts=["outputs/final.md"],
    ).review_required
    with pytest.raises(ValidationError):
        DeliveryPlan(goal="task", requirements=[requirement(), requirement()])
    with pytest.raises(ValidationError):
        DeliveryReview(status="passed")
    with pytest.raises(ValidationError):
        DeliveryReview(status="failed")


async def test_queued_human_correction_prevents_initial_spending(tmp_path):
    runner, config, args = setup(tmp_path)
    args["human"] = HumanQueue(ChatMessage(role="user", content="Stop; revise the goal first."))
    result = await runner.run(config, **args, model=ScriptedModel())
    assert result.stop_reason == "plan_review_required" and result.status == "unverified"
    assert result.tokens == 0 and result.model_calls == 0 and not result.attempts


async def test_correction_arriving_during_review_prevents_stale_pass(tmp_path):
    class Review:
        async def review(self, **kwargs):
            args["human"].messages.append(ChatMessage(role="user", content="Use the updated date."))
            return DeliveryReview(status="passed", evidence=["checked old specification"])

    runner, config, args = setup(
        tmp_path, requirements=[requirement(review_required=True)], reviewer=Review()
    )
    result = await runner.run(config, **args, model=ScriptedModel(write("READY"), reply()))
    assert result.stop_reason == "plan_review_required" and result.status == "unverified"
    assert not result.assessments[0].artifacts and result.pending_human_messages


async def test_emit_failure_returns_usage_and_completed_turn_without_false_success(tmp_path):
    runner, config, args = setup(tmp_path)

    async def emit(turn):
        raise RuntimeError("private adapter information")

    result = await runner.run(
        config, **args, model=ScriptedModel(write("READY"), reply()), emit=emit
    )
    assert result.stop_reason == "discussion_error:RuntimeError" and result.status != "passed"
    assert result.tokens == 1100 and len(result.attempts[0].turns) == 1
    assert "private adapter information" not in result.model_dump_json()


async def test_human_correction_before_rework_invalidates_prior_passes(tmp_path):
    runner, config, args = setup(tmp_path, requirements=[requirement(), requirement("reviewer")])
    args["human"] = HumanQueue(
        None, None, None, ChatMessage(role="user", content="Revise both deliverables.")
    )
    result = await runner.run(
        config, **args, model=ScriptedModel(write("DRAFT"), reply(), write("READY"), reply())
    )
    assert result.stop_reason == "plan_review_required" and result.status == "unverified"
    assert [item.status for item in result.assessments] == ["unverified", "unverified"]
    assert [item.status for item in result.attempts[0].assessments] == ["failed", "passed"]
    assert result.model_calls == 4
