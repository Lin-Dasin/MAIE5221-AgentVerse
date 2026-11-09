from pathlib import Path

import pytest
from pydantic import ValidationError

from protocol.models import AcceptanceRule, CriticResult, InputRef, TaskGraph, ToolResult
from tests.helpers import graph, task


def test_task_graph_round_trip_keeps_as_alias():
    first = task()
    second = task(
        "t2",
        depends_on=["t1"],
        inputs=[
            InputRef(
                from_task="t1",
                path="t1.md",
                destination="inputs/t1.md",
            )
        ],
    )
    original = graph(first, second)
    encoded = original.model_dump_json(by_alias=True)
    assert '"as":"inputs/t1.md"' in encoded
    assert TaskGraph.model_validate_json(encoded) == original


def test_checked_in_example_matches_current_contract():
    path = Path(__file__).resolve().parents[1] / "protocol/example_graph.json"
    example = TaskGraph.model_validate_json(path.read_text(encoding="utf-8"))
    assert example.final_task_id == "compare"


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError, match="unknown task"):
        graph(task(depends_on=["unknown"]))


def test_cycles_and_duplicate_ids_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        graph(task("a", depends_on=["b"]), task("b", depends_on=["a"]))
    with pytest.raises(ValidationError, match="unique"):
        graph(task(), task())


def test_parent_wait_edges_cannot_deadlock():
    with pytest.raises(ValidationError, match="cycle"):
        graph(
            task("parent"),
            task("child", parent_task="parent", depth=1, depends_on=["parent"]),
            final="parent",
        )


def test_plain_text_acceptance_is_not_silently_accepted():
    with pytest.raises(ValidationError):
        AcceptanceRule.model_validate("high quality output")
    with pytest.raises(ValidationError, match="requires non-empty"):
        AcceptanceRule(type="text_contains", path="outputs/report.md")


def test_tool_and_critic_results_are_consistent():
    with pytest.raises(ValidationError, match="require an error"):
        ToolResult(ok=False)
    with pytest.raises(ValidationError, match="agree"):
        CriticResult(passed=True, checks_passed=0, checks_total=1)
