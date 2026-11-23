"""Exercise targeted portfolio repair through A's runner and the real file tools."""

import json
from collections import deque

import pytest

from harness.scenario_portfolio import ALLOCATION_ARTIFACT, PROPOSAL_ARTIFACT
from protocol.models import ModelResponse, ToolCall
from ui import scenario

ROLES = ("fund_manager", "research", "risk", "marketing")
ALLOCATION = json.dumps(
    {
        "positions": [
            {
                "asset": "Stocks",
                "asset_class": "us_equity",
                "weight_bps": 6000,
                "amount_hkd": 600000,
            },
            {"asset": "Reserve", "asset_class": "cash", "weight_bps": 4000, "amount_hkd": 400000},
        ],
        "stock_decline_bps": 3000,
    }
)
PROPOSAL = """# Classroom proposal

<!-- allocation-summary:start -->
| Item | Weight | HKD |
| --- | ---: | ---: |
| US equity | 60% | 600,000 |
| Other equity | 0% | 0 |
| Fixed income | 0% | 0 |
| Cash | 40% | 400,000 |
| Total equity | 60% | 600,000 |
| Total reserve | 40% | 400,000 |
| Total portfolio | 100% | 1,000,000 |
| Stock decline | 30% | - |
| Stress loss | - | 180,000 |
| Stress remaining | - | 820,000 |
<!-- allocation-summary:end -->
"""
DEFECT = "The Stress loss HKD cell does not match the verified allocation."


def _scenario_fixture(tmp_path):
    packet = tmp_path / "scenario_3_securities_fund"
    packet.mkdir()
    roles = "".join(f"## {role} ({role})\n```text\nYou are {role}.\n```\n" for role in ROLES)
    (packet / "01_role_initialization.md").write_text("# Fund\n" + roles, encoding="utf-8")
    for name, task in (("02_task_1.md", "Write a proposal."), ("03_task_2.md", "Extend it.")):
        (packet / name).write_text(f"```text\n{task}\n```\n", encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "fund.yaml").write_text(
        json.dumps(
            {
                "scenario_id": "scenario_3_fund",
                "roles": dict.fromkeys(ROLES, ["file_read", "file_write"]),
            }
        ),
        encoding="utf-8",
    )
    session = tmp_path / "session"
    scenario.init_session(packet, session)
    return session, profiles


class _PortfolioModel:
    def __init__(self):
        self.responses = deque()
        self.requests = []
        self._turn(
            "fund_manager",
            {
                ALLOCATION_ARTIFACT: ALLOCATION,
                PROPOSAL_ARTIFACT: PROPOSAL.replace("180,000", "120,000"),
            },
        )
        for role in ROLES[1:]:
            self._turn(role, {f"outputs/{role}.md": "Draft review note."})
        self._turn("fund_manager", {PROPOSAL_ARTIFACT: PROPOSAL})

    def _turn(self, role, files):
        calls = [
            ToolCall(
                call_id=f"write-{index}", name="file_write", arguments={"path": path, "text": text}
            )
            for index, (path, text) in enumerate(files.items())
        ]
        self.responses.append((role, ModelResponse(tool_calls=calls, tokens=50)))
        self.responses.append((role, ModelResponse(content="Files saved for review.", tokens=50)))

    async def complete(self, messages, tools, *, max_tokens):
        expected_role, response = self.responses.popleft()
        self.requests.append((expected_role, messages[0].content))
        return response


async def test_managed_portfolio_repairs_stress_then_waits_for_operator_acceptance(tmp_path):
    pytest.importorskip("orchestrator.delivery")
    from ui.scenario_delivery import managed_discussion_engine

    session, profiles = _scenario_fixture(tmp_path)
    model = _PortfolioModel()
    engine = managed_discussion_engine(
        model,
        scenario_id="scenario-3-securities-fund",
        number=1,
        max_rounds=1,
        max_task_tokens=180_000,
        max_output_tokens=512,
        max_rework_rounds=1,
    )

    assert await scenario.run_task(session, 1, profiles, engine) is None
    assert not model.responses
    assert all(f"You are {role}." in prompt for role, prompt in model.requests)
    repair = [(role, prompt) for role, prompt in model.requests if "Rework attempt: 1/1" in prompt]
    assert repair and all(role == "fund_manager" and DEFECT in prompt for role, prompt in repair)

    task = session / "tasks/task-1"
    (attempt,) = (task / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "passed" and summary["stop_reason"] == "accepted"
    first, second = summary["attempts"]
    assert first["assessments"][0]["defects"] == [DEFECT]
    assert second["changed_requirements"] == ["portfolio"] and second["turns"] == 1
    manifest, proposal = (
        f"outputs/fund_manager/{path.removeprefix('outputs/')}"
        for path in (ALLOCATION_ARTIFACT, PROPOSAL_ARTIFACT)
    )
    assert summary["candidates"] == {
        path: scenario.file_sha256(attempt / path) for path in (manifest, proposal)
    }
    assert (attempt / manifest).read_text(encoding="utf-8") == ALLOCATION
    assert (attempt / proposal).read_text(encoding="utf-8") == PROPOSAL
    assert not (task / "accepted").exists()
    with pytest.raises(ValueError, match="accept Task 1"):
        await scenario.run_task(session, 2, profiles, engine)

    accepted = scenario.accept_attempt(session, 1, attempt.name, (manifest, proposal))
    assert (accepted / "fund_manager/investment-proposal.md").read_bytes() == (
        attempt / proposal
    ).read_bytes()
