"""Exercise managed newsroom repair through the real discussion and file tools."""

import json
from collections import deque
from datetime import date, timedelta

import pytest

from harness import scenario_newsroom, scenario_newsroom_factcheck
from protocol.models import ModelResponse, ToolCall, ToolResult
from tools import web_search
from ui import scenario, scenario_engine

ROLES = {
    "chief_editor": ["file_read", "file_write"],
    "reporter": ["web_search", "file_write"],
    "fact_checker": ["web_search", "file_read", "file_write"],
    "editor": ["file_read", "file_write"],
}
EVIDENCE = "outputs/event-evidence.json"
ARTICLE = "outputs/hkust-news.md"
REPORTER_INPUT = "inputs/shared/reporter/event-evidence.json"
STAGED_EVIDENCE = "outputs/reporter/event-evidence.json"
STAGED_ARTICLE = "outputs/editor/hkust-news.md"
TASK_ONE = "Report a recent event."
HUMAN_NOTE = "Explain why the event matters to both students and faculty."


def _evidence(days_ago, slug):
    event_date = (date.today() - timedelta(days=days_ago)).isoformat()
    return {
        "event_name": "Campus AI Forum",
        "event_date": event_date,
        "venue": "Shaw Auditorium",
        "source_url": f"https://hkust.edu.hk/news/{slug}",
        "source_quote": f"HKUST opened the Campus AI Forum at Shaw Auditorium on {event_date}.",
    }


def _article(event):
    return (
        f"## Website\n{event['source_quote']} [Source]({event['source_url']})\n\n"
        "## Social\nStudents can read about the event.\n\n"
        "## Faculty\nFaculty can consult the event details.\n"
    )


def _prepare_newsroom(tmp_path):
    packet = tmp_path / "scenario_2_newsroom"
    packet.mkdir()
    roles = "".join(f"## {role} ({role})\n```text\nYou are {role}.\n```\n" for role in ROLES)
    (packet / "01_role_initialization.md").write_text("# Newsroom\n" + roles, encoding="utf-8")
    (packet / "02_task_1.md").write_text(f"```text\n{TASK_ONE}\n```\n", encoding="utf-8")
    (packet / "03_task_2.md").write_text("```text\nCheck a claim.\n```\n", encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "s2_newsroom.yaml").write_text(
        json.dumps({"scenario_id": "scenario_2_newsroom", "roles": ROLES}), encoding="utf-8"
    )
    session = tmp_path / "session"
    scenario.init_session(packet, session)
    return session, profiles


def test_run_command_forwards_managed_limits(tmp_path, monkeypatch):
    session, profiles = _prepare_newsroom(tmp_path)
    options = {}

    def fake_make_live_engine(**kwargs):
        options.update(kwargs)

        async def engine(request, human, emit):
            assert request.task.text == TASK_ONE
            return None

        return engine

    monkeypatch.setattr(scenario_engine, "make_live_engine", fake_make_live_engine)
    assert (
        scenario.main(
            [
                "run",
                str(session),
                "1",
                "--profiles-dir",
                str(profiles),
                "--managed",
                "--max-rework-rounds",
                "0",
                "--max-output-tokens",
                "1024",
            ]
        )
        == 1
    )
    assert options == {
        "managed_task": ("scenario-2-newsroom", 1),
        "max_rounds": 1,
        "max_rework_rounds": 0,
        "max_output_tokens": 1024,
        "max_task_tokens": 180_000,
        "hitl_pause_timeout_sec": 0.0,
        "generic_verifier": False,
    }


def test_managed_defaults_output_tokens_to_8192(tmp_path, monkeypatch):
    session, profiles = _prepare_newsroom(tmp_path)
    options = {}

    def fake_make_live_engine(**kwargs):
        options.update(kwargs)

        async def engine(request, human, emit):
            return None

        return engine

    monkeypatch.setattr(scenario_engine, "make_live_engine", fake_make_live_engine)
    assert (
        scenario.main(
            [
                "run",
                str(session),
                "1",
                "--profiles-dir",
                str(profiles),
                "--managed",
            ]
        )
        == 1
    )
    assert options["max_output_tokens"] == 8192


def test_run_command_forwards_hitl_pause(tmp_path, monkeypatch):
    session, profiles = _prepare_newsroom(tmp_path)
    options = {}

    def fake_make_live_engine(**kwargs):
        options.update(kwargs)

        async def engine(request, human, emit):
            return None

        return engine

    monkeypatch.setattr(scenario_engine, "make_live_engine", fake_make_live_engine)
    assert (
        scenario.main(
            [
                "run",
                str(session),
                "1",
                "--profiles-dir",
                str(profiles),
                "--managed",
                "--hitl-pause-sec",
                "2.5",
                "--generic-checks",
            ]
        )
        == 1
    )
    assert options["hitl_pause_timeout_sec"] == 2.5
    assert options["generic_verifier"] is True


class _NewsroomModel:
    def __init__(self, old, current, *, repair_article):
        steps = []

        def tool(role, name, **arguments):
            call = ToolCall(call_id=f"{name}-{len(steps)}", name=name, arguments=arguments)
            steps.append((role, ModelResponse(tool_calls=[call], tokens=50)))

        def write(role, path, text):
            tool(role, "file_write", path=path, text=text)
            steps.append((role, ModelResponse(content=f"I saved {path}.", tokens=50)))

        write("chief_editor", "outputs/desk.md", "Report a recent event with a direct source.")
        tool("reporter", "web_search", query="older campus AI event")
        write("reporter", EVIDENCE, json.dumps(old))
        write("fact_checker", "outputs/check.md", "Awaiting review of the source date.")
        tool("editor", "file_read", path=REPORTER_INPUT)
        write("editor", ARTICLE, _article(old))

        tool("reporter", "web_search", query="recent campus AI event")
        write("reporter", EVIDENCE, json.dumps(_reference_evidence(current)))
        tool("editor", "file_read", path=REPORTER_INPUT)
        write("editor", ARTICLE, _article(current if repair_article else old))

        self.steps = deque(steps)
        self.repair_roles = []
        self.editor_reads = []
        self.contract_errors = []

    async def complete(self, messages, tools, *, max_tokens):
        role, response = self.steps.popleft()
        prompt = messages[0].content
        offered = {tool.name for tool in tools}
        if f"You are {role}." not in prompt:
            self.contract_errors.append((role, "persona missing"))
        if not any(f"Goal: {TASK_ONE}" in message.content for message in messages):
            self.contract_errors.append((role, "original task missing"))
        if not any(HUMAN_NOTE in message.content for message in messages):
            self.contract_errors.append((role, "original instructor message missing"))
        is_repair = "Rework attempt: 1/" in prompt or "Rework attempt: 2/" in prompt
        if is_repair and role not in self.repair_roles:
            self.repair_roles.append(role)
        allowed = set(ROLES[role]) | ({"file_read"} if role == "reporter" else set())
        if "file_write" in allowed:
            allowed.add("file_patch")
        if offered - allowed:
            self.contract_errors.append((role, offered))
        for call in response.tool_calls:
            if call.name not in offered:
                self.contract_errors.append((role, f"{call.name} missing from {offered}"))
            if role == "editor" and call.name == "file_write":
                read = next(
                    message
                    for message in messages
                    if (message.tool_call_id or "").startswith("file_read-")
                )
                result = ToolResult.model_validate_json(read.content)
                assert result.ok
                self.editor_reads.append(json.loads(result.data["text"]))
        return response


def _reference_evidence(event):
    return {key: value for key, value in event.items() if key != "source_quote"} | {
        "source_ref": "R2"
    }


@pytest.mark.parametrize("repair_article", [True, False])
async def test_managed_newsroom_rechecks_report_against_repaired_source(
    tmp_path, monkeypatch, repair_article
):
    pytest.importorskip("orchestrator.delivery")
    from ui.scenario_delivery import managed_discussion_engine

    old = _evidence(365, "campus-ai-forum-old")
    current = _evidence(7, "campus-ai-forum-recent")
    queries = []

    async def fetch(query, **kwargs):
        queries.append(query)
        event = old if query == scenario_newsroom.initial_event_search(date.today()) else current
        return [
            {
                "url": event["source_url"],
                "content": event["source_quote"],
                "title": event["event_name"],
            }
        ]

    monkeypatch.setenv("SEARCH_API_KEY", "fixture")
    monkeypatch.setattr(web_search, "fetch_tavily", fetch)
    session, profiles = _prepare_newsroom(tmp_path)
    assert scenario.main(["say", str(session), "--text", HUMAN_NOTE]) == 0
    model = _NewsroomModel(old, current, repair_article=repair_article)
    engine = managed_discussion_engine(
        model,
        scenario_id="scenario-2-newsroom",
        number=1,
        max_task_tokens=180000,
        max_rework_rounds=2,
    )

    assert await scenario.run_task(session, 1, profiles, engine) is None
    assert not model.contract_errors
    assert not model.steps
    assert model.repair_roles == ["reporter", "editor"]
    assert queries == [
        scenario_newsroom.initial_event_search(date.today()),
        "recent campus AI event",
    ]
    assert model.editor_reads == [old, _reference_evidence(current)]

    task_dir = session / "tasks/task-1"
    (attempt,) = (task_dir / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "managed"
    first, repair_reporter, repair_editor = summary["attempts"]
    assert [(row["owner"], row["status"]) for row in first["assessments"]] == [
        ("reporter", "failed"),
        ("editor", "failed"),
    ]
    assert (
        first["turns"],
        repair_reporter["turns"],
        repair_editor["turns"],
    ) == (4, 1, 1)
    assert not (task_dir / "accepted").exists()
    with pytest.raises(ValueError, match="accept Task 1"):
        await scenario.run_task(session, 2, profiles, engine)

    outputs = (STAGED_EVIDENCE, STAGED_ARTICLE)
    if repair_article:
        assert summary["status"] == "passed"
        assert summary["stop_reason"] == "accepted"
        assert summary["candidates"] == {
            path: scenario.file_sha256(attempt / path) for path in outputs
        }
        assert json.loads((attempt / STAGED_EVIDENCE).read_bytes()) == _reference_evidence(current)
        assert (attempt / STAGED_ARTICLE).read_text(encoding="utf-8") == _article(current)
        handoff = attempt / "workspaces/editor/inputs/shared/reporter/verified-event.json"
        checked = json.loads(handoff.read_bytes())
        assert checked["event_start"] == checked["event_end"] == current["event_date"]
        assert checked["source_url"] == current["source_url"]
        with pytest.raises(ValueError, match="full managed candidate set"):
            scenario.accept_attempt(session, 1, attempt.name, (STAGED_ARTICLE,))
        with pytest.raises(ValueError, match="distinct"):
            scenario.accept_attempt(session, 1, attempt.name, outputs + (STAGED_ARTICLE,))
        accepted = scenario.accept_attempt(session, 1, attempt.name, outputs)

        async def inspect_handoff(request, human, emit):
            assert set(request.inputs) == {path.removeprefix("outputs/") for path in outputs}
            for path in request.inputs:
                assert (request.workspace / "inputs" / path).read_bytes() == (
                    accepted / path
                ).read_bytes()
            return None

        assert await scenario.run_task(session, 2, profiles, inspect_handoff) is None
    else:
        assert summary["status"] == "partial"
        assert summary["stop_reason"] == "rework_limit"
        assert set(summary["candidates"]) == set(outputs)
        with pytest.raises(ValueError):
            scenario.accept_attempt(session, 1, attempt.name, outputs)


async def test_managed_fact_check_repairs_evidence_before_editor_and_waits_for_acceptance(
    tmp_path, monkeypatch
):
    pytest.importorskip("orchestrator.delivery")
    from ui.scenario_delivery import managed_discussion_engine

    session, profiles = _prepare_newsroom(tmp_path)
    evidence_artifact = scenario_newsroom_factcheck.EVIDENCE_ARTIFACT
    article_artifact = scenario_newsroom_factcheck.ARTICLE_ARTIFACT

    async def first_task(request, human, emit):
        path = request.workspace / STAGED_ARTICLE
        path.parent.mkdir()
        path.write_text("Previously reviewed event article.", encoding="utf-8")
        return (STAGED_ARTICLE,)

    await scenario.run_task(session, 1, profiles, first_task)
    url = "https://journals.example.org/research/detector-evaluation"
    statement = "The evaluation found false positives and false negatives in AI-writing detectors."
    evidence = json.dumps(
        {
            "claims": [
                {
                    "claim": statement,
                    "source_url": url,
                    "source_ref": "R1",
                    "source_date": None,
                }
            ]
        }
    )
    article = (
        "Verdict: A detector score cannot reliably determine authorship by itself.\n\n"
        f"{statement} [Evaluation]({url})\n\n"
        "False positives flag human writing. False negatives miss generated writing. "
        "The error rates vary by tool, language and sample. Students can retain drafts. "
        "Faculty should review context and ask about the writing process."
    )
    replies = deque()

    def tool(role, name, **arguments):
        replies.append(
            (
                role,
                ModelResponse(
                    tool_calls=[
                        ToolCall(call_id=f"{name}-{len(replies)}", name=name, arguments=arguments)
                    ],
                    tokens=50,
                ),
            )
        )

    def write(role, path, text):
        tool(role, "file_write", path=path, text=text)
        replies.append((role, ModelResponse(content="File saved.", tokens=50)))

    write("chief_editor", "outputs/desk.md", "Choose published detector evaluations.")
    write("reporter", "outputs/leads.md", "Report the limitations of the evidence.")
    tool("fact_checker", "web_search", query="first query")
    write("fact_checker", evidence_artifact, evidence)
    write("editor", article_artifact, "Verdict: The evidence needs checking.")
    tool("fact_checker", "web_search", query="repair query")
    write("fact_checker", evidence_artifact, evidence)
    tool("editor", "file_read", path="inputs/shared/fact_checker/detector-evidence.json")
    write("editor", article_artifact, article)

    async def fetch(query, **kwargs):
        if query in {scenario_newsroom_factcheck.INITIAL_SEARCH, "first query"}:
            return []
        return [{"url": url, "title": "AI-writing detector evaluation", "content": statement}]

    monkeypatch.setenv("SEARCH_API_KEY", "fixture")
    monkeypatch.setattr(web_search, "fetch_tavily", fetch)
    repaired = []

    class Model:
        async def complete(self, messages, tools, *, max_tokens):
            role, response = replies.popleft()
            prompt = messages[0].content
            assert f"You are {role}." in prompt
            is_repair = "Rework attempt: 1/" in prompt or "Rework attempt: 2/" in prompt
            if is_repair and role not in repaired:
                repaired.append(role)
            if (
                role == "editor"
                and "Rework attempt: 1/1" in prompt
                and any(call.name == "file_write" for call in response.tool_calls)
            ):
                result = next(message for message in messages if message.role == "tool")
                assert ToolResult.model_validate_json(result.content).data["text"] == evidence
            return response

    engine = managed_discussion_engine(
        Model(),
        scenario_id="scenario-2-newsroom",
        number=2,
        max_task_tokens=180_000,
        max_rework_rounds=2,
    )
    assert await scenario.run_task(session, 2, profiles, engine) is None
    assert not replies and repaired == ["fact_checker", "editor"]
    task = session / "tasks/task-2"
    (attempt,) = (task / "attempts").iterdir()
    summary = json.loads((attempt / "discussion-summary.json").read_bytes())
    assert summary["status"] == "passed"
    assert summary["search_evidence"] == {
        "owner": "fact_checker",
        "calls": 3,
        "calls_with_results": 1,
    }
    assert not (task / "accepted").exists()
    accepted = scenario.accept_attempt(session, 2, attempt.name, tuple(summary["candidates"]))
    delivered = (accepted / "editor/ai-detector-fact-check.md").read_text("utf-8")
    assert delivered.startswith(article)
    assert "## Checked evidence" in delivered and f"[Source]({url})" in delivered
