import json
from datetime import date, timedelta

import pytest

from harness.scenario_newsroom import (
    ARTICLE_ARTIFACT,
    EVENT_ARTIFACT,
    check_article,
    check_event,
    completed_event_passage,
    recorded_event_occurrence,
    verified_event_handoff,
)

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


def test_result_hint_prioritizes_actual_dates_over_publication_and_future_events():
    completed = "The group held the event at Shaw Auditorium on 18 and 19 November 2026."
    text = (
        "Published 20 November 2026. The next event will be held on 21 November 2026. " + completed
    )
    assert completed_event_passage(text, TODAY) == (date(2026, 11, 18), completed)
    assert (
        completed_event_passage(
            "Published 19 November 2026. The event was held on May 17, 2026.", TODAY
        )
        is None
    )


_NAMED_EVENT = {
    "event_name": "Campus AI Summit 2026",
    "event_date": "2026-10-03",
    "venue": "HKUST Business School Central",
    "source_url": URL,
    "source_ref": "R9",
}
_NAMED_RECAP = (
    "The Campus AI Summit 2026 opened on 3 and 4 October with a two-day AI Hackathon "
    "and AI Symposium at HKUST Business School Central in Admiralty."
)


def test_dated_event_name_confirms_occurrence_and_range(tmp_path):
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(_NAMED_EVENT))
    references = {"R9": (URL, f"Published 5 October 2026. {_NAMED_RECAP}")}
    assert not check_event(tmp_path, {}, TODAY, references=references)
    assert recorded_event_occurrence("R9", URL, TODAY, references) == (
        date(2026, 10, 3),
        _NAMED_RECAP,
    )
    assert verified_event_handoff(tmp_path, {}, TODAY, references) == {
        "event_name": "Campus AI Summit 2026",
        "event_start": "2026-10-03",
        "event_end": "2026-10-04",
        "venue": "HKUST Business School Central",
        "source_url": URL,
    }


def test_invalid_event_fields_receive_hint_only_from_exact_bound_official_result(tmp_path):
    references = {"R9": (URL, _NAMED_RECAP)}
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(_NAMED_EVENT | {"event_date": "", "venue": ""}))
    defects = check_event(tmp_path, {}, TODAY, references=references)
    assert any(
        "R9" in defect and "2026-10-03" in defect and _NAMED_RECAP in defect for defect in defects
    )
    assert recorded_event_occurrence("R9", URL + "-wrong", TODAY, references) is None
    external = "https://example.com/news/campus-ai-forum"
    assert (
        recorded_event_occurrence("R9", external, TODAY, {"R9": (external, _NAMED_RECAP)}) is None
    )


@pytest.mark.parametrize(
    "unsupported",
    [
        "Published 5 October 2026. The Campus AI Summit opened on 3 and 4 October.",
        "The Campus AI Summit opened on 3 and 4 October, as reported in 2026.",
        "The Campus AI Summit 2025 opened on 3 and 4 October.",
        "The Campus AI Summit 2026 opened on October 3 and 4, 2025.",
        "The Campus AI Summit 2026 opened on 3 and 4 October 2025.",
        "The Campus AI Summit 2026 will be held on 3 and 4 October.",
        "The Campus AI Summit 2026 ended in May, and the campus fair opened on 3 October.",
    ],
)
def test_no_occurrence_hint_from_dateline_wrong_year_or_schedule(unsupported):
    assert completed_event_passage(unsupported, TODAY) is None


def test_event_range_cannot_end_after_evaluation_date(tmp_path):
    source = "The Campus AI Summit 2026 opened at HKUST Business School Central on November 23–24."
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(_NAMED_EVENT | {"event_date": TODAY.isoformat()}))
    defects = check_event(tmp_path, {}, TODAY, references={"R9": (URL, source)})
    assert any("ends after the evaluation date" in defect for defect in defects)


def test_event_reports_unsupported_venue_and_returns_original_passage(tmp_path):
    card = {key: value for key, value in EVENT.items() if key != "source_quote"}
    _write(
        tmp_path,
        EVENT_ARTIFACT,
        json.dumps(card | {"venue": "Shaw Auditorium, invented district", "source_ref": "R1"}),
    )
    defects = check_event(tmp_path, {}, TODAY, references={"R1": (URL, EVENT["source_quote"])})
    assert any("invented district" in defect and "does not appear" in defect for defect in defects)
    assert any(EVENT["source_quote"] in defect for defect in defects)


def test_editor_cannot_turn_a_two_day_occurrence_into_a_one_day_story(tmp_path):
    reporter, editor = tmp_path / "reporter", tmp_path / "editor"
    card = {key: value for key, value in EVENT.items() if key != "source_quote"}
    _write(reporter, EVENT_ARTIFACT, json.dumps(card | {"source_ref": "R1"}))
    occurrence = "HKUST held the Campus AI Forum at Shaw Auditorium on 18 and 19 November 2026."
    references = {"R1": (URL, occurrence)}
    _write(editor, ARTICLE_ARTIFACT, _article() + "\nIt was a single-day event.\n")
    defects = check_article(editor, reporter, {}, TODAY, references=references)
    assert any("2026-11-18 through 2026-11-19" in defect for defect in defects)
    _write(editor, ARTICLE_ARTIFACT, _article(EVENT | {"source_quote": occurrence}))
    assert not check_article(editor, reporter, {}, TODAY, references=references)


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


def test_reporter_gets_schema_date_and_occurrence_defects_together(tmp_path):
    schedule = "Campus AI Forum will be held at Shaw Auditorium on 7 January 2026."
    evidence = EVENT | {
        "event_date": "2026-01-07",
        "source_quote": schedule,
        "recency_status": "old",
        "verification_limitation": "unconfirmed",
    }
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(evidence))
    defects = check_event(tmp_path, {URL: [schedule]}, TODAY)
    assert len(defects) == 3
    assert "recency_status, verification_limitation" in defects[0]
    assert "2026-07-26 through 2026-11-23" in defects[1]
    assert "explicitly states when the event occurred" in defects[2]


def test_missing_occurrence_date_produces_a_follow_up_on_the_specific_lead(tmp_path):
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(EVENT | {"event_date": "pending"}))
    defects = check_event(tmp_path, {}, TODAY)
    assert any("event_date" in defect for defect in defects)
    assert any(
        "Campus AI Forum" in defect and "official HKUST recap" in defect for defect in defects
    )


@pytest.mark.parametrize("when", ["November 18–19, 2026", "18 and 19 November 2026"])
def test_reporter_can_use_current_reference_for_completed_multiday_event(tmp_path, when):
    evidence = {key: value for key, value in EVENT.items() if key != "source_quote"}
    _write(tmp_path, EVENT_ARTIFACT, json.dumps(evidence | {"source_ref": "R7"}))
    report = f"HKUST held the Campus AI Forum at Shaw Auditorium on {when}."
    assert not check_event(tmp_path, {}, TODAY, references={"R7": (URL, report)})
    defects = check_event(
        tmp_path,
        {},
        TODAY,
        references={"R7": (f"{URL}-other", report), "R2": (URL, report)},
    )
    assert any("This exact URL was returned as R2" in defect for defect in defects)


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
