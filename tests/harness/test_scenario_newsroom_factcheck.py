"""Test the detector evidence and reader-facing checks without network or model calls."""

import json
from datetime import date

from harness.scenario_newsroom_factcheck import (
    ARTICLE_ARTIFACT,
    EVIDENCE_ARTIFACT,
    check_article,
    check_evidence,
)

TODAY = date(2026, 11, 23)
URL = "https://journal.example.edu/issues/2026/detector-evaluation"
CLAIM = "AI-writing detectors falsely flagged 14% of human texts in the study sample."


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_evidence(root, text=CLAIM, **updates):
    claim = {"claim": text, "source_url": URL, "source_ref": "R1", "source_date": None}
    _write(root, EVIDENCE_ARTIFACT, json.dumps({"claims": [claim | updates]}))


def _evidence(root):
    return check_evidence(root, {}, TODAY, references={"R1": (URL, CLAIM)})


def test_detector_evidence_uses_the_current_result_and_exact_figures(tmp_path):
    _write_evidence(tmp_path)
    assert not _evidence(tmp_path)[1]

    _write_evidence(tmp_path, source_ref="R3")
    assert any("source_ref" in defect for defect in _evidence(tmp_path)[1])

    _write_evidence(tmp_path, CLAIM.replace("14%", "40%"))
    assert any("percentage" in defect for defect in _evidence(tmp_path)[1])


def test_detector_evidence_rejects_the_right_percentage_with_the_wrong_metric(tmp_path):
    _write_evidence(tmp_path, "AI-writing detectors achieved 14% overall accuracy in the study.")
    _, defects = _evidence(tmp_path)
    assert any("metric and tested population" in defect for defect in defects)


def test_detector_evidence_does_not_turn_uncertainty_into_a_universal_negative(tmp_path):
    _write_evidence(tmp_path, "No study in the review establishes reliability of AI detectors.")
    _, defects = _evidence(tmp_path)
    assert any("no study found a result" in defect for defect in defects)


def test_unrelated_claim_is_not_enough_to_pass_evidence(tmp_path):
    claim = "Students borrowed more printed books during the autumn term."
    _write_evidence(tmp_path, claim)
    _, defects = check_evidence(tmp_path, {}, TODAY, references={"R1": (URL, claim)})
    assert any("AI-writing detector" in defect for defect in defects)


def test_article_needs_visible_verdict_both_errors_and_current_citations(tmp_path):
    _write_evidence(tmp_path)
    claims, defects = _evidence(tmp_path)
    assert not defects
    draft = (
        "**Verdict:** A detector score alone cannot reliably determine assignment authorship.\n"
        f"\n{CLAIM} [Evaluation]({URL})\n"
        "\nA false positive flags human writing. A false negative misses AI writing.\n"
        "\nThe error rates for other writing samples are uncertain.\n"
        "\nStudents can retain drafts. Faculty should compare evidence before acting.\n"
    )
    _write(tmp_path, ARTICLE_ARTIFACT, draft)
    assert not check_article(tmp_path, claims)

    hidden = "```markdown\n" + draft + "```\n"
    _write(tmp_path, ARTICLE_ARTIFACT, hidden)
    defects = check_article(tmp_path, claims)
    for expected in (
        "adjacent source link",
        "Verdict",
        "false positive",
        "false negative",
        "uncertain",
        "students",
        "faculty",
    ):
        assert any(expected in defect for defect in defects)

    different_url = draft.replace(URL, f"{URL}-other")
    _write(tmp_path, ARTICLE_ARTIFACT, different_url)
    assert any("adjacent source link" in defect for defect in check_article(tmp_path, claims))

    _write(tmp_path, ARTICLE_ARTIFACT, draft + "The detectors had 14% overall accuracy.\n")
    assert any("unsupported figures" in defect for defect in check_article(tmp_path, claims))
