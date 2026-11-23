"""Run Mid Test with deterministic checks and Role A's bounded rework loop."""

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from harness import scenario_newsroom, scenario_portfolio
from harness.verdict import FileVerifier
from orchestrator.artifacts import archive_path, copy_file, inside
from protocol.ports import ModelClient
from ui.scenario import TaskEngine, TaskRequest, _require_unlinked, file_sha256
from ui.scenario_engine import (
    _file_tools,
    _history,
    _HumanInput,
    _prepare_discussion,
    _share_outputs,
)

if TYPE_CHECKING:
    from protocol import delivery
    from protocol.models import DiscussionTurn

NEWSROOM = "scenario-2-newsroom"
FUND = "scenario-3-securities-fund"
EVENT = "reporter-event"
ARTICLE = "editor-article"
PORTFOLIO = "portfolio"


def validate_managed_task(scenario_id: str, number: int) -> None:
    if number != 1 or scenario_id not in {NEWSROOM, FUND}:
        raise ValueError(
            "Managed checks are available for Task 1 in the newsroom and fund scenarios"
        )


def _requirements(scenario_id: str) -> list["delivery.DeliveryRequirement"]:
    from protocol.delivery import DeliveryRequirement

    if scenario_id == NEWSROOM:
        return [
            DeliveryRequirement(
                requirement_id=EVENT,
                owner="reporter",
                description=scenario_newsroom.EVENT_GUIDANCE,
                artifacts=[scenario_newsroom.EVENT_ARTIFACT],
            ),
            DeliveryRequirement(
                requirement_id=ARTICLE,
                owner="editor",
                description=scenario_newsroom.ARTICLE_GUIDANCE,
                artifacts=[scenario_newsroom.ARTICLE_ARTIFACT],
            ),
        ]
    return [
        DeliveryRequirement(
            requirement_id=PORTFOLIO,
            owner="fund_manager",
            description=scenario_portfolio.PORTFOLIO_GUIDANCE,
            artifacts=[
                scenario_portfolio.ALLOCATION_ARTIFACT,
                scenario_portfolio.PROPOSAL_ARTIFACT,
            ],
        )
    ]


class _ScenarioReviewer:
    """Use result excerpts from the current run without granting new tools or making API calls."""

    def __init__(self, workspaces: dict[str, Path], today: date) -> None:
        self.workspaces = workspaces
        self.today = today
        self.observations: dict[str, list[str]] = defaultdict(list)

    def observe(self, turn: "DiscussionTurn") -> None:
        if turn.speaker != "reporter":
            return
        for call, result in zip(turn.tool_calls, turn.tool_results, strict=True):
            data = result.data
            if (
                call.name != "web_search"
                or not result.ok
                or not isinstance(data, dict)
                or data.get("provider") != "tavily"
                or not isinstance(data.get("results"), list)
            ):
                continue
            for item in data["results"]:
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    title, content = item.get("title"), item.get("content")
                    if isinstance(title, str) and isinstance(content, str) and content.strip():
                        self.observations[item["url"]].append(f"{title}\n{content}")

    async def review(self, *, config, requirement, root, history) -> "delivery.DeliveryReview":
        from protocol.delivery import DeliveryReview

        if requirement.requirement_id == EVENT:
            defects = scenario_newsroom.check_event(root, self.observations, self.today)
            evidence = "Event date and source excerpt match the reporter's recorded Tavily result."
        elif requirement.requirement_id == ARTICLE:
            defects = scenario_newsroom.check_article(
                root, self.workspaces["reporter"], self.observations, self.today
            )
            evidence = "Article uses the current event evidence and includes the required channels."
        elif requirement.requirement_id == PORTFOLIO:
            defects = scenario_portfolio.check_portfolio(root)
            evidence = "Recalculated allocation, asset-class subtotals, and stress scenario."
        else:
            return DeliveryReview(status="unverified", defects=["Unknown scenario review rule"])
        if defects:
            return DeliveryReview(status="failed", defects=defects)
        return DeliveryReview(status="passed", evidence=[evidence])


def _stage_verified(
    request: TaskRequest, workspaces: dict[str, Path], result: "delivery.DeliveryResult"
) -> dict[str, str]:
    candidates = {}
    if result.status != "passed" or result.stop_reason != "accepted":
        return candidates
    for assessment in result.assessments:
        root = workspaces[assessment.owner]
        for artifact in assessment.artifacts:
            source = inside(root, artifact.path)
            for path in (source, *source.parents):
                _require_unlinked(path)
                if path == root:
                    break
            relative = f"outputs/{assessment.owner}/{archive_path(artifact.path)}"
            target = inside(request.workspace, relative)
            copy_file(source, target)
            if file_sha256(target) != artifact.sha256:
                raise ValueError(f"Managed candidate changed after verification: {relative}")
            candidates[relative] = artifact.sha256
    return candidates


def managed_discussion_engine(
    model: ModelClient,
    *,
    scenario_id: str,
    number: int,
    max_rounds: int = 1,
    max_task_tokens: int = 60_000,
    max_output_tokens: int = 2048,
    max_rework_rounds: int = 2,
) -> TaskEngine:
    """Stage the exact model-produced bytes after all deterministic checks pass."""
    from orchestrator.delivery import DeliveryDiscussionRunner
    from orchestrator.discussion import RoundRobinDiscussionEngine
    from protocol.delivery import DeliveryPlan

    validate_managed_task(scenario_id, number)
    requirements = _requirements(scenario_id)

    async def run(
        request: TaskRequest, human: Callable[[], str | None], emit: Callable[[str, str], None]
    ) -> None:
        if request.task.task_id != f"task-{number}":
            raise ValueError("Managed scenario policy does not match the current task")
        for requirement in requirements:
            profile = request.profiles.get(requirement.owner)
            if profile is None or "file_write" not in profile.tools:
                raise ValueError(f"Managed checks require file_write for {requirement.owner}")
        if scenario_id == NEWSROOM and (
            "web_search" not in request.profiles["reporter"].tools
            or "file_read" not in request.profiles["editor"].tools
        ):
            raise ValueError("Managed newsroom requires reporter web_search and editor file_read")
        config, executor = _prepare_discussion(
            request,
            max_rounds=max_rounds,
            max_task_tokens=max_task_tokens,
            max_output_tokens=max_output_tokens,
        )
        speakers = config.speakers
        if scenario_id == NEWSROOM and speakers.index("reporter") > speakers.index("editor"):
            speakers.remove("reporter")
            speakers.insert(speakers.index("editor"), "reporter")
            config.moderator = speakers[0]
        workspaces = {who: executor.workspace(who).resolve() for who in speakers}
        history = _history(request)
        plan = DeliveryPlan(
            goal=config.goal,
            human_instructions=[event.content for event in history if event.role == "user"],
            requirements=requirements,
            max_rework_rounds=max_rework_rounds,
        )
        reviewer = _ScenarioReviewer(workspaces, date.today())

        async def publish(turn: "DiscussionTurn") -> None:
            reviewer.observe(turn)
            _share_outputs(request, executor, turn.speaker)
            emit(turn.speaker, turn.content)

        engine = RoundRobinDiscussionEngine(_file_tools(executor, request.inputs))
        result = await DeliveryDiscussionRunner(engine, FileVerifier(), reviewer=reviewer).run(
            config,
            plan=plan,
            workspaces=workspaces,
            profiles=request.profiles,
            history=history,
            model=model,
            executor=executor,
            human=_HumanInput(human),
            emit=publish,
        )
        candidates = _stage_verified(request, workspaces, result)
        summary = {
            "mode": "managed",
            "status": result.status,
            "stop_reason": result.stop_reason,
            "tokens": result.tokens,
            "model_calls": result.model_calls,
            "assessments": [item.model_dump(mode="json") for item in result.assessments],
            "attempts": [
                item.model_dump(mode="json", exclude={"turns"}) | {"turns": len(item.turns)}
                for item in result.attempts
            ],
            "candidates": candidates,
        }
        location = request.workspace / "discussion-summary.json"
        location.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Managed checks: {result.status} ({result.stop_reason}). Attempt: {request.run_id}")
        print(f"Discussion summary: {location}")
        if candidates:
            print("Review the full candidate set before accepting:")
            for path in candidates:
                print(f"  {path}")

    return run
