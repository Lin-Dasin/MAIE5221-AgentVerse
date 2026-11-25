"""Check claim attribution against synthetic search responses for the two fund tasks."""

import json
from datetime import date

import pytest

from harness.scenario_fund_sources import (
    RESEARCH_ARTIFACT,
    SLIDES_ARTIFACT,
    check_citations,
    check_research,
)
from harness.scenario_portfolio import PROPOSAL_ARTIFACT

TODAY = date(2026, 11, 23)
FAB_URL = "https://investor.tsmc.com/english/quarterly-results/2026/q3"
CAPEX_URL = "https://abc.xyz/investor/events/2026/q3-earnings-call"
FAB = "The second Japanese fab is scheduled to use the 3-nanometer process."
CAPEX = "Infrastructure depreciation already contributed to operating costs."
CLAIMS = (
    {"claim": FAB, "source_url": FAB_URL, "source_date": "2026-10-15", "source_quote": FAB},
    {"claim": CAPEX, "source_url": CAPEX_URL, "source_date": None, "source_quote": CAPEX},
)
OBSERVED = {
    FAB_URL: [f"Third-quarter results, 2026-10-15\n{FAB}"],
    CAPEX_URL: [f"Quarterly earnings call\n{CAPEX}"],
}


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _research(root, claims=CLAIMS, observed=OBSERVED):
    _write(root, RESEARCH_ARTIFACT, json.dumps({"claims": claims}))
    return check_research(root, observed, TODAY)


def _slides(first, second):
    return (
        "<!doctype html><html><head><title>Semiconductor outlook</title></head><body>"
        '<section id="s1"><h1>End-2026 semiconductor outlook</h1>'
        f'{first}<a href="#s2">Next</a></section>'
        '<section id="s2"><h2>Investment implications and risks</h2>'
        f'{second}<a href="#s1">Previous</a></section></body></html>'
    )


@pytest.fixture
def verified(tmp_path):
    claims, defects = _research(tmp_path)
    assert defects == []
    assert len(claims) == 2
    return claims


def test_empty_search_results_do_not_verify_self_reported_sources(tmp_path):
    _, defects = _research(tmp_path, observed={})
    assert defects


def test_schema_error_does_not_hide_the_missing_search_defect(tmp_path):
    _write(
        tmp_path,
        RESEARCH_ARTIFACT,
        json.dumps({"claims": CLAIMS, "_status": "draft", "notes": "internal"}),
    )

    claims, defects = check_research(tmp_path, {}, TODAY)

    assert claims == []
    joined = " ".join(defects)
    assert "No usable web_search results" in joined
    assert "Extra inputs are not permitted" in joined


@pytest.mark.parametrize(
    "observed",
    [
        {FAB_URL: ["Unrelated release dated 2026-10-15."], CAPEX_URL: [FAB, CAPEX]},
        {
            FAB_URL: ["Issuer notice dated 2026-10-15.", f"Different, undated page\n{FAB}"],
            CAPEX_URL: [CAPEX],
        },
    ],
    ids=["quote-on-wrong-url", "date-and-quote-in-separate-results"],
)
def test_source_date_and_excerpt_belong_to_one_observed_result(tmp_path, observed):
    _, defects = _research(tmp_path, observed=observed)
    assert defects


@pytest.mark.parametrize("source_date", ["2026-10-16", "2026-11-24"])
def test_claimed_source_date_requires_matching_evidence_and_cannot_be_future(tmp_path, source_date):
    claims = [CLAIMS[0] | {"source_date": source_date}, CLAIMS[1]]
    observed = dict(OBSERVED)
    if source_date > TODAY.isoformat():
        observed[FAB_URL] = [f"Future release, {source_date}\n{FAB}"]
    _, defects = _research(tmp_path, claims, observed)
    assert defects


def test_issuer_homepage_cannot_stand_in_for_a_direct_source(tmp_path):
    homepage = "https://www.wsts.org/"
    claims = [CLAIMS[0] | {"source_url": homepage}, CLAIMS[1]]
    _, defects = _research(tmp_path, claims, OBSERVED | {homepage: OBSERVED[FAB_URL]})
    assert defects


def test_observed_excerpt_cannot_support_a_different_process_size(tmp_path):
    wrong_size = [CLAIMS[0] | {"claim": FAB.replace("3-nanometer", "2 nm")}, CLAIMS[1]]
    _, defects = _research(tmp_path, wrong_size)
    assert len(defects) == 1 and "process node" in defects[0]


def test_percents_may_be_explicitly_rounded_within_the_reported_precision(tmp_path):
    statement = {
        "claim": "The evaluation found about 61% false positives.",
        "source_url": FAB_URL,
        "source_date": None,
        "source_ref": "R1",
    }
    observed = {"R1": (FAB_URL, "The evaluation found 61.22% false positives.")}
    for description, accepted in (
        (statement["claim"], True),
        (statement["claim"].replace("about ", ""), False),
        (statement["claim"].replace("61%", "62%"), False),
    ):
        _write(
            tmp_path,
            RESEARCH_ARTIFACT,
            json.dumps(
                {
                    "claims": [
                        statement
                        | {
                            "claim": description,
                        }
                    ]
                }
            ),
        )
        _, defects = check_research(tmp_path, {}, TODAY, references=observed)
        assert (not defects) == accepted


def test_search_reference_supports_claim_without_manual_quotation(tmp_path):
    claim = {
        "claim": "HPC revenue share was 57% in 2025.",
        "source_url": FAB_URL,
        "source_date": None,
        "source_ref": "R1",
    }
    references = {"R1": (FAB_URL, "In the third quarter of 2025, HPC accounted for 57%.")}
    for changes, expected in (
        ({}, None),
        ({"source_url": CAPEX_URL}, "exact paired URL"),
        ({"source_ref": "R2"}, "returned in this task"),
        ({"claim": "HPC revenue share was 57% in 2026."}, "stated year"),
        ({"claim": "Latest reported HPC revenue share was 57%."}, "reporting period"),
    ):
        _write(tmp_path, RESEARCH_ARTIFACT, json.dumps({"claims": [claim | changes]}))
        _, defects = check_research(tmp_path, {}, TODAY, references=references)
        assert (expected in defects[0]) if expected else not defects


def test_visible_plain_urls_work_as_adjacent_citations(tmp_path, verified):
    _write(tmp_path, PROPOSAL_ARTIFACT, f"{FAB} ({FAB_URL})\n\n{CAPEX} <{CAPEX_URL}>")
    assert not check_citations(tmp_path, PROPOSAL_ARTIFACT, verified)


def test_citations_ignore_typography_but_preserve_values_and_comparison(tmp_path):
    claim = {
        "claim": "The study's 2020-2026 sample reports '<80%' on the validation set.",
        "source_url": FAB_URL,
        "source_date": None,
        "source_ref": "R1",
    }
    _write(tmp_path, RESEARCH_ARTIFACT, json.dumps({"claims": [claim]}))
    claims, defects = check_research(
        tmp_path,
        {},
        TODAY,
        references={"R1": (FAB_URL, claim["claim"])},
    )
    assert not defects
    polished = f'The study’s *2020–2026 sample* reports "<80%" on the validation set. ({FAB_URL})'
    _write(tmp_path, PROPOSAL_ARTIFACT, polished)
    assert not check_citations(tmp_path, PROPOSAL_ARTIFACT, claims)
    _write(tmp_path, PROPOSAL_ARTIFACT, polished.replace("<80%", ">80%"))
    assert check_citations(tmp_path, PROPOSAL_ARTIFACT, claims)


def test_markdown_observations_have_visible_adjacent_source_links(tmp_path, verified):
    paragraph = f"{FAB} [Quarterly release]({FAB_URL})\n\n{CAPEX} [Earnings call]({CAPEX_URL})"
    _write(tmp_path, PROPOSAL_ARTIFACT, paragraph)
    assert check_citations(tmp_path, PROPOSAL_ARTIFACT, verified) == []

    source_index = f"{FAB}\n\n{CAPEX}\n\n## References\n[First]({FAB_URL})\n[Second]({CAPEX_URL})"
    _write(tmp_path, PROPOSAL_ARTIFACT, source_index)
    assert check_citations(tmp_path, PROPOSAL_ARTIFACT, verified)


def test_slide_evidence_is_visible_in_each_claims_paragraph_or_list_item(tmp_path, verified):
    first = f'<p>{FAB} <a href="{FAB_URL}">Quarterly release</a></p>'
    second = f'<ul><li>{CAPEX} <a href="{CAPEX_URL}">Earnings call</a></li></ul>'
    _write(tmp_path, SLIDES_ARTIFACT, _slides(first, second))
    assert check_citations(tmp_path, SLIDES_ARTIFACT, verified) == []

    _write(tmp_path, SLIDES_ARTIFACT, _slides(f"<script>{first}</script>", second))
    assert check_citations(tmp_path, SLIDES_ARTIFACT, verified)


def test_navigation_targets_must_reach_another_slide(tmp_path, verified):
    first = f'<p>{FAB} <a href="{FAB_URL}">Quarterly release</a></p>'
    second = f'<p>{CAPEX} <a href="{CAPEX_URL}">Earnings call</a></p>'
    html = _slides(first, second).replace('href="#s2"', 'href="#missing"')
    _write(tmp_path, SLIDES_ARTIFACT, html)
    assert check_citations(tmp_path, SLIDES_ARTIFACT, verified)


_OUTLOOK_URL = "https://industry.example.org/releases/spring-forecast"
_HISTORICAL_URL = "https://industry.example.org/releases/autumn-forecast"
_OUTLOOK = "The association expects semiconductor market sales to rise 14% in 2026."
_HISTORICAL = "The association's autumn forecast projected 8% semiconductor market growth in 2026."


def _outlook_evidence(
    root, *, current=True, published_on="2026-06-02", dated_as="June 2, 2026", period_only=False
):
    claims = [
        {
            "claim": _HISTORICAL,
            "source_url": _HISTORICAL_URL,
            "source_ref": "R1",
            "source_date": "2025-12-02",
        }
    ]
    references = {"R1": (_HISTORICAL_URL, f"Published December 2, 2025. {_HISTORICAL}")}
    if current:
        claims.append(
            {
                "claim": _OUTLOOK,
                "source_url": _OUTLOOK_URL,
                "source_ref": "R2",
                "source_date": published_on,
            }
        )
        prefix = "Reporting period ending" if period_only else "Published"
        references["R2"] = (_OUTLOOK_URL, f"{prefix} {dated_as}. {_OUTLOOK}")
    _write(root, RESEARCH_ARTIFACT, json.dumps({"claims": claims}))
    return check_research(root, {}, TODAY, references=references, require_current_outlook=True)


@pytest.mark.parametrize("dated_as", ["June 2, 2026", "2 Jun. 2026", "2026-06-02", "June 2nd 2026"])
def test_current_industry_forecast_is_dated_in_the_observed_result(tmp_path, dated_as):
    claims, defects = _outlook_evidence(tmp_path, dated_as=dated_as)
    assert not defects
    assert len(claims) == 2


def test_a_forecast_about_this_year_is_not_necessarily_a_current_release(tmp_path):
    _, defects = _outlook_evidence(tmp_path, current=False)
    assert any("published in 2026" in defect for defect in defects)

    _, defects = _outlook_evidence(tmp_path, period_only=True)
    assert any("reporting period alone" in defect for defect in defects)

    _, defects = _outlook_evidence(
        tmp_path, published_on="2026-11-24", dated_as="November 24, 2026"
    )
    assert any("source_date" in defect for defect in defects)
    assert any("published in 2026" in defect for defect in defects)


def test_named_current_release_edition_is_distinct_from_forecast_target_year(tmp_path):
    claim = {
        "claim": "The Spring 2026 Forecast projects growth in the semiconductor market.",
        "source_url": _OUTLOOK_URL,
        "source_ref": "R1",
        "source_date": None,
    }
    _write(tmp_path, RESEARCH_ARTIFACT, json.dumps({"claims": [claim]}))
    references = {"R1": (_OUTLOOK_URL, claim["claim"])}
    _, defects = check_research(
        tmp_path,
        {},
        TODAY,
        references=references,
        require_current_outlook=True,
    )
    assert not defects
    references = {"R1": (_OUTLOOK_URL, "The Autumn 2025 Forecast projects chip growth in 2026.")}
    _, defects = check_research(
        tmp_path,
        {},
        TODAY,
        references=references,
        require_current_outlook=True,
    )
    assert any("Add a material semiconductor industry outlook" in defect for defect in defects)


def test_dated_outlooks_show_release_date_by_the_visible_slide_citation(tmp_path):
    claims, defects = _outlook_evidence(tmp_path)
    assert not defects
    historical = (
        f"<p>Earlier forecast, 2 December 2025. {_HISTORICAL} "
        f'<a href="{_HISTORICAL_URL}">Autumn release</a></p>'
    )
    current = f'<p>{_OUTLOOK} <a href="{_OUTLOOK_URL}">Spring release</a></p>'
    _write(tmp_path, SLIDES_ARTIFACT, _slides(historical, current))
    defects = check_citations(tmp_path, SLIDES_ARTIFACT, claims, as_of=TODAY)
    assert len(defects) == 1 and "publication date 2026-06-02" in defects[0]

    _write(
        tmp_path, SLIDES_ARTIFACT, _slides(historical, current.replace("<p>", "<p>June 2, 2026. "))
    )
    assert not check_citations(tmp_path, SLIDES_ARTIFACT, claims, as_of=TODAY)
