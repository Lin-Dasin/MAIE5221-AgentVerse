"""Validate executable plan contracts; acceptance evaluation remains owned by D."""

from collections.abc import Collection

from orchestrator.artifacts import archive_path
from protocol.models import AcceptanceRule, RunConfig, TaskGraph

FILE_RULE_TYPES = frozenset({"file_exists", "text_contains"})
KNOWN_RULE_TYPES = FILE_RULE_TYPES | {"pytest_passes"}


class PlanContractError(ValueError):
    """A plan cannot execute under the configured artifact/verifier contracts."""


class PlanPreflight:
    def __init__(
        self,
        *,
        worker_ids: Collection[str] | None = None,
        supported_acceptance_types: Collection[str] = FILE_RULE_TYPES,
    ):
        self.worker_ids = frozenset(worker_ids) if worker_ids is not None else None
        self.supported_acceptance_types = frozenset(supported_acceptance_types)
        if not self.supported_acceptance_types or not (
            self.supported_acceptance_types <= KNOWN_RULE_TYPES
        ):
            raise ValueError("supported_acceptance_types must name known acceptance rules")

    def check_rules(self, rules: list[AcceptanceRule], location: str) -> None:
        unsupported = {rule.type for rule in rules} - self.supported_acceptance_types
        if unsupported:
            raise PlanContractError(
                f"{location}: unsupported acceptance types {sorted(unsupported)}; "
                f"configured verifier supports {sorted(self.supported_acceptance_types)}"
            )

    def validate(self, graph: TaskGraph, config: RunConfig, *, initial: bool = True) -> None:
        # Revalidate nested fields too: callers can mutate lists after model creation.
        TaskGraph.model_validate(graph.model_dump())
        if len(graph.tasks) > config.max_tasks or (initial and any(t.depth for t in graph.tasks)):
            raise PlanContractError(
                "initial graph must fit the task budget and contain depth-0 tasks"
                if initial
                else "delegated graph exceeds the run task budget"
            )
        published = {
            task.task_id: {archive_path(output) for output in task.outputs} for task in graph.tasks
        }
        for task in graph.tasks:
            if self.worker_ids is not None and task.assignee not in self.worker_ids:
                raise PlanContractError(
                    f"task {task.task_id}: assignee {task.assignee!r} is not a registered worker"
                )
            self.check_rules(task.acceptance, f"task {task.task_id}")
            for ref in task.inputs:
                if ref.path not in published[ref.from_task]:
                    raise PlanContractError(
                        f"task {task.task_id}: input {ref.path!r} is not published by "
                        f"{ref.from_task}; archive-relative choices: "
                        f"{sorted(published[ref.from_task])}"
                    )
        self.check_rules(graph.goal_acceptance, "goal")
        final_outputs = published[graph.final_task_id]
        for rule in graph.goal_acceptance:
            if rule.type in FILE_RULE_TYPES and rule.path not in final_outputs:
                raise PlanContractError(
                    f"goal: path {rule.path!r} is not published by final task "
                    f"{graph.final_task_id}; archive-relative choices: {sorted(final_outputs)}"
                )
