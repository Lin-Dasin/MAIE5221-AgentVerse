"""Exercise targeted portfolio repair through A's runner and the real file tools."""

import json
from collections import deque
from datetime import date

import pytest

from harness.scenario_fund_sources import RESEARCH_ARTIFACT, SLIDES_ARTIFACT
from harness.scenario_portfolio import ALLOCATION_ARTIFACT, PROPOSAL_ARTIFACT
from protocol.models import ModelResponse, ToolCall
from ui import scenario

ROLES = ("fund_manager", "research", "risk", "marketing")
SOURCE_URL = "https://example.com/releases/quarterly-results-2025"
SOURCE_QUOTE = "The company reported an increase in annual operating revenue."
CLAIM = SOURCE_QUOTE
OUTLOOK_DATE = date.today().replace(month=1, day=1).isoformat()
OUTLOOK_CLAIM = (
    f"The global semiconductor market forecast projects growth of 14% in {date.today().year}."
)
RESEARCH = json.dumps(
    {
        "claims": [
            {
                "claim": CLAIM,
                "source_url": SOURCE_URL,
                "source_date": None,
                "source_quote": SOURCE_QUOTE,
            }
        ]
    }
)
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

{claim}
"""
PROPOSAL = PROPOSAL.format(claim=f"{CLAIM} [Company release]({SOURCE_URL})")
DEFECT = "An explicit sum in the proposal does not equal its stated result."


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
                "roles": dict.fromkeys(ROLES, ["file_read", "file_write"])
                | {"research": ["web_search", "file_write"]},
            }
        ),
        encoding="utf-8",
    )
    session = tmp_path / "session"
    scenario.init_session(packet, session)
    return session, profiles


class _PortfolioModel:
    def __init__(self, *, slides=False):
        self.responses = deque()
        self.requests = []
        self.observed_source_ref = False
        if slides:
            self._slides_turns()
            return
        self._turn("research", {RESEARCH_ARTIFACT: RESEARCH}, search=True)
        self._turn(
            "fund_manager",
            {
                ALLOCATION_ARTIFACT: ALLOCATION,
                PROPOSAL_ARTIFACT: (
                    PROPOSAL.replace("180,000", "120,000")
                    + "\nAn inaccurate narrative: 60% + 40% = 90%.\n"
                ),
            },
        )
        for role in ("risk", "marketing"):
            self._turn(role, {f"outputs/{role}.md": "Draft review note."})
        self._turn("fund_manager", {PROPOSAL_ARTIFACT: PROPOSAL})

    def _slides_turns(self):
        deck = (
            '<section id="one"><h1>Research</h1>{claim}<a href="#two">Next</a></section>'
            '<section id="two"><p>Investment implications and risks</p>'
            '<a href="#one">Previous</a></section>'
        )
        first_research = RESEARCH.replace(SOURCE_QUOTE, "A claim with no verified source.")
        self._turn("research", {RESEARCH_ARTIFACT: first_research}, search=True)
        for role in ("fund_manager", "risk"):
            self._turn(role, {f"outputs/{role}.md": "Draft review note."})
        self._turn("marketing", {SLIDES_ARTIFACT: deck.format(claim="")})
        referenced = json.loads(RESEARCH)
        referenced["claims"][0].pop("source_quote")
        referenced["claims"][0]["source_ref"] = "R99"
        referenced["claims"][0]["source_date"] = OUTLOOK_DATE
        referenced["claims"][0]["claim"] = OUTLOOK_CLAIM
        self._turn("research", {RESEARCH_ARTIFACT: json.dumps(referenced)}, search=True)
        cited_claim = f'<p>{OUTLOOK_CLAIM} <a href="{SOURCE_URL}">Release {OUTLOOK_DATE}</a></p>'
        self._turn(
            "marketing",
            {SLIDES_ARTIFACT: deck.format(claim=cited_claim)},
            read=True,
        )

    def _turn(self, role, files, *, search=False, read=False):
        if search:
            self.responses.append(
                (
                    role,
                    ModelResponse(
                        tool_calls=[
                            ToolCall(
                                call_id="search", name="web_search", arguments={"query": "Release"}
                            )
                        ],
                        tokens=50,
                    ),
                )
            )
        if read:
            self.responses.append(
                (
                    role,
                    ModelResponse(
                        tool_calls=[
                            ToolCall(
                                call_id="read",
                                name="file_read",
                                arguments={"path": "inputs/shared/research/research-evidence.json"},
                            )
                        ],
                        tokens=50,
                    ),
                )
            )
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
        if any(message.role == "tool" and "[R1]" in message.content for message in messages):
            self.observed_source_ref = True
        return response


def _mock_search(monkeypatch, *, empty_first=False):
    from tools import web_search

    replies = deque(
        ([[]] if empty_first else [])
        + [
            [
                {
                    "url": SOURCE_URL,
                    "title": "Results",
                    "content": f"Published {OUTLOOK_DATE}. {OUTLOOK_CLAIM}"
                    if empty_first
                    else SOURCE_QUOTE,
                }
            ]
        ]
    )

    async def fetch(query, **kwargs):
        rows = replies.popleft()
        if rows and empty_first:
            # Oversized responses use Role A's compact display, which only keeps title/URL/text.
            rows += [
                {
                    "url": f"https://example.com/releases/other-{i}",
                    "title": "Other release",
                    "content": "Additional context. " * 60,
                }
                for i in range(4)
            ]
        return rows

    monkeypatch.setenv("SEARCH_API_KEY", "synthetic")
    monkeypatch.setattr(web_search, "fetch_tavily", fetch)
    return replies


async def test_managed_portfolio_repairs_stress_then_waits_for_operator_acceptance(
    tmp_path, monkeypatch
):
    pytest.importorskip("orchestrator.delivery")
    from ui.scenario_delivery import managed_discussion_engine

    session, profiles = _scenario_fixture(tmp_path)
    replies = _mock_search(monkeypatch)
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
    assert not model.responses and not replies
    assert all(f"You are {role}." in prompt for role, prompt in model.requests)
    repair = [(role, prompt) for role, prompt in model.requests if "Rework attempt: 1/1" in prompt]
    assert repair and all(role == "fund_manager" and DEFECT in prompt for role, prompt in repair)

    task = session / "tasks/task-1"
    (attempt,) = (task / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "passed" and summary["stop_reason"] == "accepted"
    first, second = summary["attempts"]
    assert first["assessments"][1]["defects"] == [DEFECT]
    assert second["changed_requirements"] == ["portfolio"] and second["turns"] == 1
    manifest, proposal = (
        f"outputs/fund_manager/{path.removeprefix('outputs/')}"
        for path in (ALLOCATION_ARTIFACT, PROPOSAL_ARTIFACT)
    )
    research = "outputs/research/research-evidence.json"
    assert summary["candidates"] == {
        path: scenario.file_sha256(attempt / path) for path in (research, manifest, proposal)
    }
    assert (attempt / manifest).read_text(encoding="utf-8") == ALLOCATION
    assert (attempt / proposal).read_text(encoding="utf-8") == PROPOSAL.replace("| - |", "| — |")
    assert not (task / "accepted").exists()
    with pytest.raises(ValueError, match="accept Task 1"):
        await scenario.run_task(session, 2, profiles, engine)

    accepted = scenario.accept_attempt(session, 1, attempt.name, (research, manifest, proposal))
    assert (accepted / "fund_manager/investment-proposal.md").read_bytes() == (
        attempt / proposal
    ).read_bytes()


async def test_managed_fund_slides_route_missing_evidence_back_to_research_then_marketing(
    tmp_path, monkeypatch
):
    pytest.importorskip("orchestrator.delivery")
    from ui.scenario_delivery import managed_discussion_engine

    session, profiles = _scenario_fixture(tmp_path)

    async def reviewed_task_one(request, human, emit):
        prior = request.workspace / "outputs/fund_manager/investment-proposal.md"
        prior.parent.mkdir()
        prior.write_text(PROPOSAL, encoding="utf-8")
        return ("outputs/fund_manager/investment-proposal.md",)

    await scenario.run_task(session, 1, profiles, reviewed_task_one)
    replies = _mock_search(monkeypatch, empty_first=True)
    model = _PortfolioModel(slides=True)
    engine = managed_discussion_engine(
        model,
        scenario_id="scenario-3-securities-fund",
        number=2,
        max_task_tokens=180_000,
        max_output_tokens=1024,
        max_rework_rounds=2,
    )
    assert await scenario.run_task(session, 2, profiles, engine) is None
    assert not replies and not model.responses
    assert model.observed_source_ref
    repairs = [
        (role, prompt)
        for role, prompt in model.requests
        if "Rework attempt: 1/" in prompt or "Rework attempt: 2/" in prompt
    ]
    assert list(dict.fromkeys(role for role, _ in repairs)) == ["research", "marketing"]
    assert "Claim 1" in repairs[0][1]
    assert "semiconductor industry outlook" in repairs[0][1]
    assert "Use this research claim" in repairs[-1][1]
    task = session / "tasks/task-2"
    (attempt,) = (task / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_bytes())
    assert summary["status"] == "passed"
    assert summary["attempts"][1]["changed_requirements"] == ["fund-sources"]
    assert summary["attempts"][2]["changed_requirements"] == ["fund-slides"]
    assert summary["search_evidence"] == {"owner": "research", "calls": 3, "calls_with_results": 1}
    stored = json.loads((attempt / "outputs/research/research-evidence.json").read_bytes())
    index = json.loads(
        (attempt / "workspaces/research/inputs/recorded-search-results.json").read_bytes()
    )
    assert stored["claims"][0]["source_ref"] == index[0]["id"] == "R1"
    assert index[0]["url"] == stored["claims"][0]["source_url"] == SOURCE_URL
    assert not (task / "accepted").exists()
    accepted = scenario.accept_attempt(session, 2, attempt.name, tuple(summary["candidates"]))
    delivered = (accepted / "marketing/semiconductor-outlook.html").read_text("utf-8")
    assert SOURCE_URL in delivered and 'id="verified-evidence"' in delivered


async def test_managed_fund_slides_report_zero_search_and_schema_before_repairing_upstream(
    tmp_path, monkeypatch
):
    pytest.importorskip("orchestrator.delivery")
    from ui.scenario_delivery import managed_discussion_engine

    session, profiles = _scenario_fixture(tmp_path)

    async def reviewed_task_one(request, human, emit):
        prior = request.workspace / "outputs/fund_manager/investment-proposal.md"
        prior.parent.mkdir()
        prior.write_text(PROPOSAL, encoding="utf-8")
        return ("outputs/fund_manager/investment-proposal.md",)

    await scenario.run_task(session, 1, profiles, reviewed_task_one)
    replies = _mock_search(monkeypatch, empty_first=True)
    bad = json.dumps(
        {
            "claims": [
                {
                    "claim": OUTLOOK_CLAIM,
                    "source_url": SOURCE_URL,
                    "source_ref": "R9",
                    "source_date": OUTLOOK_DATE,
                }
            ],
            "_status": "draft",
            "notes": "internal",
        }
    )
    good = json.dumps(
        {
            "claims": [
                {
                    "claim": OUTLOOK_CLAIM,
                    "source_url": SOURCE_URL,
                    "source_date": OUTLOOK_DATE,
                    "source_quote": f"Published {OUTLOOK_DATE}. {OUTLOOK_CLAIM}",
                }
            ]
        }
    )
    deck = (
        '<!doctype html><html><body><section id="one"><h1>Outlook</h1>'
        f'<p>{OUTLOOK_CLAIM} <a href="{SOURCE_URL}">Release {OUTLOOK_DATE}</a></p>'
        "</section></body></html>"
    )
    responses = deque()
    requests = []

    def turn(role, files, *, search=False):
        if search:
            responses.append(
                (
                    role,
                    ModelResponse(
                        tool_calls=[
                            ToolCall(
                                call_id="search", name="web_search", arguments={"query": "Release"}
                            )
                        ],
                        tokens=50,
                    ),
                )
            )
        calls = [
            ToolCall(
                call_id=f"write-{index}",
                name="file_write",
                arguments={"path": path, "text": text},
            )
            for index, (path, text) in enumerate(files.items())
        ]
        responses.append((role, ModelResponse(tool_calls=calls, tokens=50)))
        responses.append((role, ModelResponse(content="Files saved for review.", tokens=50)))

    turn("research", {RESEARCH_ARTIFACT: bad})
    turn("fund_manager", {"outputs/fund_manager/note.md": "note"})
    turn("risk", {"outputs/risk/note.md": "note"})
    turn("marketing", {SLIDES_ARTIFACT: deck})
    turn("research", {RESEARCH_ARTIFACT: good}, search=True)

    class Model:
        async def complete(self, messages, tools, *, max_tokens):
            role, response = responses.popleft()
            requests.append((role, messages[0].content))
            return response

    engine = managed_discussion_engine(
        Model(),
        scenario_id="scenario-3-securities-fund",
        number=2,
        max_task_tokens=180_000,
        max_output_tokens=1024,
        max_rework_rounds=1,
    )
    assert await scenario.run_task(session, 2, profiles, engine) is None
    assert not replies and not responses
    repairs = [
        (role, prompt)
        for role, prompt in requests
        if "Rework attempt: 1/" in prompt
    ]
    assert list(dict.fromkeys(role for role, _ in repairs)) == ["research"]

    task = session / "tasks/task-2"
    (attempt,) = (task / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_bytes())
    first = summary["attempts"][0]
    research = next(item for item in first["assessments"] if item["owner"] == "research")
    joined = " ".join(research["defects"])
    assert "No usable web_search results" in joined
    assert "Extra inputs are not permitted" in joined
    assert summary["status"] == "partial"
    assert summary["candidates"]
