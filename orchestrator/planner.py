"""Bounded planning, specialist bids and schema repair before deterministic execution."""

import json

from pydantic import ValidationError

from protocol.models import (
    AcceptanceRule,
    AgentProfile,
    ChatMessage,
    RoleBid,
    RunConfig,
    TaskGraph,
    extract_json,
)
from protocol.ports import ModelClient


class PlanningError(ValueError):
    pass


class Planner:
    def __init__(self, model: ModelClient, profile: AgentProfile):
        if profile.role != "planner":
            raise ValueError("planner requires a planner profile")
        self.model = model
        self.profile = profile

    async def plan(
        self,
        goal: str,
        workers: list[AgentProfile],
        config: RunConfig,
        *,
        memory_context: str = "",
        negotiate: bool = True,
        goal_acceptance: list[AcceptanceRule] | None = None,
    ) -> TaskGraph:
        if not goal.strip() or not workers or any(item.role != "worker" for item in workers):
            raise PlanningError("planning requires a goal and worker profiles")
        worker_ids = {worker.agent_id for worker in workers}
        if len(worker_ids) != len(workers):
            raise PlanningError("worker profile IDs must be unique")
        calls = 0
        tokens = 0

        async def ask(messages: list[ChatMessage]) -> str:
            nonlocal calls, tokens
            if calls >= config.max_planning_calls or tokens >= config.max_task_tokens:
                raise PlanningError("planning budget exhausted")
            response = await self.model.complete(
                messages,
                [],
                max_tokens=min(config.max_output_tokens, config.max_task_tokens - tokens),
            )
            calls += 1
            tokens += response.tokens
            if response.tool_calls or tokens > config.max_task_tokens:
                raise PlanningError("planning returned tool calls or exceeded its token budget")
            return response.content

        messages = [
            ChatMessage(
                role="system",
                content=self.profile.system_prompt + "\n"
                "Return only a JSON TaskGraph matching the supplied schema. "
                "Choose registered workers; tasks must have depth 0 and no parent. "
                "Inputs name accepted upstream artifacts relative to their archive root. "
                "Task acceptance paths are workspace-relative; goal acceptance paths are "
                "relative to the final task's archive (without outputs/). "
                "Use summary to explain role assignments. No tools may be executed in planning.",
            ),
            ChatMessage(
                role="user",
                content=json.dumps(
                    {
                        "goal": goal,
                        "workers": [worker.model_dump() for worker in workers],
                        "memory_context": memory_context if config.memory_enabled else "",
                        "max_tasks": config.max_tasks,
                        "required_goal_acceptance": (
                            [rule.model_dump() for rule in goal_acceptance]
                            if goal_acceptance is not None
                            else None
                        ),
                        "schema": TaskGraph.model_json_schema(),
                    }
                ),
            ),
        ]

        async def generate() -> TaskGraph:
            for _ in range(2):
                text = await ask(messages)
                try:
                    candidate = TaskGraph.model_validate_json(extract_json(text))
                    if len(candidate.tasks) > config.max_tasks or any(
                        task.depth or task.assignee not in worker_ids for task in candidate.tasks
                    ):
                        raise ValueError("invalid task count, depth or worker assignment")
                    if goal_acceptance is not None:
                        candidate.goal_acceptance = goal_acceptance
                    return candidate
                except (ValueError, ValidationError) as exc:
                    messages.extend(
                        [
                            ChatMessage(role="assistant", content=text),
                            ChatMessage(role="user", content=f"Repair this invalid plan: {exc}"),
                        ]
                    )
            raise PlanningError("planner could not produce a valid task graph")

        graph = await generate()
        if not negotiate:
            return graph
        bids = []
        for worker in workers:
            # Reserve two calls for final planning and one possible schema repair.
            if config.max_planning_calls - calls < 3:
                break
            text = await ask(
                [
                    ChatMessage(
                        role="system",
                        content=worker.system_prompt + "\n"
                        "Review this plan as a specialist. Return a JSON RoleBid with "
                        "task_ids you can take and a rationale. Do not execute tools.",
                    ),
                    ChatMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "plan": graph.model_dump(mode="json", by_alias=True),
                                "profile": worker.model_dump(),
                                "schema": RoleBid.model_json_schema(),
                            }
                        ),
                    ),
                ]
            )
            try:
                bid = RoleBid.model_validate_json(extract_json(text))
                if not set(bid.task_ids) <= {task.task_id for task in graph.tasks}:
                    raise ValueError("bid contains unknown task IDs")
            except ValueError:
                continue
            bids.append({"agent_id": worker.agent_id, **bid.model_dump()})
        if bids:
            messages.extend(
                [
                    ChatMessage(role="assistant", content=graph.model_dump_json(by_alias=True)),
                    ChatMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "specialist_bids": bids,
                                "instruction": (
                                    "Return the final TaskGraph; summarize accepted role decisions."
                                ),
                            }
                        ),
                    ),
                ]
            )
            graph = await generate()
            graph.summary += "\nSpecialist bids: " + "; ".join(
                f"{bid['agent_id']} requested {bid['task_ids']}: {bid['rationale']}" for bid in bids
            )
        return graph
