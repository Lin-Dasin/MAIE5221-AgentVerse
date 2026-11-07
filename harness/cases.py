"""Typed, data-only fixtures for offline Harness evaluation."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from protocol.models import Contract, FailureCode, Identifier, RelativePath, Task, TaskGraph


class FixtureStep(Contract):
    """One scripted Worker response; these fixtures never execute user code."""

    kind: Literal["write", "delegate", "fail"] = "write"
    files: dict[RelativePath, str] = Field(default_factory=dict)
    children: list[Task] = Field(default_factory=list, max_length=2)
    failure_code: FailureCode | None = None
    summary: str = ""

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if any(not path.startswith("outputs/") for path in self.files):
            raise ValueError("fixture files must be under outputs/")
        if self.kind == "delegate":
            if not self.children:
                raise ValueError("delegate fixtures require children")
        elif self.children:
            raise ValueError("only delegate fixtures may contain children")
        if self.kind == "fail":
            if self.failure_code is None:
                raise ValueError("fail fixtures require a failure_code")
            if self.files:
                raise ValueError("fail fixtures must not write files")
        elif self.failure_code is not None:
            raise ValueError("only fail fixtures may specify a failure_code")
        return self


class EvaluationCase(Contract):
    """A top-level goal, its fixed graph and explicit offline Worker actions."""

    task_id: Identifier
    goal: str = Field(min_length=1)
    graph: TaskGraph
    steps: dict[Identifier, Annotated[list[FixtureStep], Field(min_length=1)]]

    @model_validator(mode="after")
    def known_scripts(self) -> Self:
        if not self.goal.strip():
            raise ValueError("goal must contain non-whitespace text")
        if any(task.depth for task in self.graph.tasks):
            raise ValueError("fixed initial graphs must contain only depth-0 tasks")
        known_ids = {task.task_id for task in self.graph.tasks}
        known_ids.update(
            child.task_id
            for steps in self.steps.values()
            for step in steps
            for child in step.children
        )
        unknown = set(self.steps) - known_ids
        if unknown:
            raise ValueError(f"scripts refer to unknown task IDs: {sorted(unknown)}")
        return self


class EvaluationSuite(Contract):
    suite: Identifier
    mode: Literal["offline"] = "offline"
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> Self:
        identifiers = [case.task_id for case in self.cases]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("evaluation case task_id values must be unique")
        return self


def load_suite(path: Path) -> EvaluationSuite:
    """Load strict JSON, validate all cases before any execution, accept UTF-8 BOM."""
    return EvaluationSuite.model_validate_json(path.read_text(encoding="utf-8-sig"))
