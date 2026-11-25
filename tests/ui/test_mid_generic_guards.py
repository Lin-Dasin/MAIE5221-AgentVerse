"""Guards for generic owner selection and research-before-delivery acceptance."""

from types import SimpleNamespace

from protocol.models import AcceptanceRule, AgentProfile
from ui.scenario_delivery import _generic_requirements, _GenericFileVerifier


def _profile(agent_id, tools):
    return AgentProfile(
        agent_id=agent_id,
        role="worker",
        system_prompt=f"You are {agent_id}.",
        tools=tools,
    )


def test_code_artifact_prefers_a_code_capable_producer_even_when_qa_is_last():
    speakers = ["coordinator", "analyst", "qa"]
    profiles = {
        "coordinator": _profile("coordinator", ["file_read", "file_write"]),
        "analyst": _profile("analyst", ["file_read", "file_write", "code_exec"]),
        "qa": _profile("qa", ["file_read", "file_write"]),
    }
    request = SimpleNamespace(task=SimpleNamespace(text="Write outputs/report.py."))

    requirements = _generic_requirements(request, speakers, profiles, 2)

    assert requirements[0].owner == "analyst"
    assert requirements[0].artifacts == ["outputs/report.py"]


async def test_research_gate_fails_until_a_search_returns_a_result(tmp_path):
    output = tmp_path / "outputs/report.md"
    output.parent.mkdir()
    output.write_text("Uncited draft", encoding="utf-8")

    class Tracker:
        results = 0

    tracker = Tracker()
    verifier = _GenericFileVerifier(research_required=True, search_tracker=tracker)
    rules = [AcceptanceRule(type="file_exists", path="outputs/report.md")]

    failed = await verifier.check(rules, tmp_path)
    assert not failed.passed
    assert any("research_required" in defect for defect in failed.defects)

    tracker.results = 1
    passed = await verifier.check(rules, tmp_path)
    assert passed.passed and passed.checks_total == 2