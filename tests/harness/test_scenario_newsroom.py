import json
from datetime import date, timedelta

import pytest

from harness.scenario_newsroom import ARTICLE_ARTIFACT, EVENT_ARTIFACT, check_article, check_event

TODAY = date(2026, 11, 23)
URL = "https://hkust.edu.hk/news/campus-ai-forum"
EVENT = {
    "event_name": "Campus AI Forum",
    "event_date": "2026-11-18",
    "venue": "Shaw Auditorium",
    "source_url": URL,
    "source_quote": "HKUST opened the Campus AI Forum at Shaw Auditorium on 18 November 2026.",
}


def _write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _article(event=EVENT):
    return (
        f"## Website\n{event['source_quote']} [Source]({event['source_url']})\n"
        "\n## Social\nRead about the forum.\n"
        "\n## Faculty\nThe forum discussed AI education.\n"
    )


@pytest.mark.parametrize("date_phrase", ["18 November 2026", "Nov. 18, 2026", "2026-11-18"])
def test_newsroom_review_checks_date_and_links_in_seen_source(tmp_path, date_phrase):
    reporter, editor = tmp_path / "reporter", tmp_path / "editor"
    sentence = f"HKUST opened the Campus AI Forum at Shaw Auditorium on {date_phrase}."
    _write(reporter, EVENT_ARTIFACT, json.dumps(EVENT | {"source_quote": sentence}))
    _write(editor, ARTICLE_ARTIFACT, _article().replace("18 November 2026", "2026-11-18"))
    seen = {URL: [f"Campus AI Forum\n\n{sentence}"]}

    assert not check_event(reporter, seen, TODAY)
    assert not check_article(editor, reporter, seen, TODAY)


@pytest.mark.parametrize("event_date", [TODAY + timedelta(days=1), TODAY - timedelta(days=121)])
def test_reporter_rejects_date_outside_window(tmp_path, event_date):
    evidence = EVENT | {"event_date": event_date.isoformat()}
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(evidence))
    assert "last 120 days" in check_event(tmp_path, {}, TODAY)[0]


@pytest.mark.parametrize(
    "url",
    [
        "https://hkust.edu.hk.evil.example/news/forum",
        "https://evil.example@hkust.edu.hk/news/forum",
        "http://hkust.edu.hk/news/forum",
        "https://www.hkust.edu.hk/en-hk/news/",
        "https://www.hkust.edu.hk/en-hk/news/index.html",
        "https://hkust.edu.hk:80/news/forum",
    ],
)
def test_reporter_requires_specific_official_https_link(tmp_path, url):
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(EVENT | {"source_url": url}))
    assert "official HKUST" in check_event(tmp_path, {url: [EVENT["source_quote"]]}, TODAY)[0]


def test_reporter_rejects_fresh_dateline_on_old_event(tmp_path):
    old_event = "HKUST opened the Campus AI Forum at Shaw Auditorium on 18 November 2023."
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(EVENT | {"source_quote": old_event}))
    merged = f"Dateline: 18 November 2026. {old_event} Published 18 November 2026."
    assert "when the event occurred" in check_event(tmp_path, {URL: [merged]}, TODAY)[0]


@pytest.mark.parametrize("action", ["will be held", "was scheduled to be held", "was never held"])
def test_reporter_rejects_unconfirmed_occurrence(tmp_path, action):
    scheduled = f"The Campus AI Forum {action} at Shaw Auditorium on 18 November 2026."
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(EVENT | {"source_quote": scheduled}))
    assert "when the event occurred" in check_event(tmp_path, {URL: [scheduled]}, TODAY)[0]


def test_reporter_cannot_omit_scheduled_language_from_quote(tmp_path):
    partial = "held at Shaw Auditorium on 18 November 2026."
    scheduled = f"The Campus AI Forum will be {partial}"
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(EVENT | {"source_quote": partial}))
    assert "when the event occurred" in check_event(tmp_path, {URL: [scheduled]}, TODAY)[0]


def test_reporter_quote_itself_needs_to_support_occurrence(tmp_path):
    unrelated = "Shaw Auditorium is centrally located on campus."
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(EVENT | {"source_quote": unrelated}))
    seen = {URL: [f"{EVENT['source_quote']} {unrelated}"]}
    assert "when the event occurred" in check_event(tmp_path, seen, TODAY)[0]


def test_reporter_requires_quote_from_exact_observed_source(tmp_path):
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(EVENT))
    observation = {f"{URL}-different": [EVENT["source_quote"]], URL: ["Unrelated content"]}
    assert "verbatim excerpt" in check_event(tmp_path, observation, TODAY)[0]


def test_repaired_reporter_evidence_can_unlock_editor_review(tmp_path):
    reporter, editor = tmp_path / "reporter", tmp_path / "editor"
    _write(editor, ARTICLE_ARTIFACT, _article())
    seen = {URL: [EVENT["source_quote"]]}
    assert "once its source" in check_article(editor, reporter, seen, TODAY)[0]
    _write(reporter, EVENT_ARTIFACT, json.dumps(EVENT))
    assert not check_article(editor, reporter, seen, TODAY)


def test_editor_must_use_occurrence_date_headings_and_no_outreach(tmp_path):
    reporter, editor = tmp_path / "reporter", tmp_path / "editor"
    _write(reporter, EVENT_ARTIFACT, json.dumps(EVENT))
    draft = (
        "# Website\nDateline: 18 November 2026. The Campus AI Forum at Shaw Auditorium "
        f"opened in 2023. We have contacted HKUST. [Source]({URL})"
    )
    _write(editor, ARTICLE_ARTIFACT, draft)
    defects = check_article(editor, reporter, {URL: [EVENT["source_quote"]]}, TODAY)
    assert len(defects) == 4
    for expected in ("occurrence date", "social channel", "faculty channel", "contacted"):
        assert any(expected in defect for defect in defects)


def test_editor_explicitly_denies_outreach(tmp_path):
    reporter, editor = tmp_path / "reporter", tmp_path / "editor"
    _write(reporter, EVENT_ARTIFACT, json.dumps(EVENT))
    _write(editor, ARTICLE_ARTIFACT, _article() + "\nWe did not contact or interview anyone.")
    assert not check_article(editor, reporter, {URL: [EVENT["source_quote"]]}, TODAY)
