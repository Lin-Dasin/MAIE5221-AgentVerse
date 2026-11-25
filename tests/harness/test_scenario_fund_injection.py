"""Deterministic verified-evidence slide injection for the fund scenario."""

from datetime import date

from harness.scenario_fund_sources import (
    SLIDES_ARTIFACT,
    ResearchClaim,
    attach_verified_evidence,
    check_citations,
)


def _deck() -> str:
    return (
        "<html><body><nav><a href=\"#one\">Start</a></nav><main>"
        "<section id=\"one\"><p>Opening</p><a href=\"#two\">Next</a></section>"
        "<section id=\"two\"><p>Closing</p><a href=\"#one\">Previous</a></section>"
        "</main></body></html>"
    )


def _claims() -> list[ResearchClaim]:
    return [
        ResearchClaim(
            claim=(
                "WSTS Spring 2026 Forecast projects global semiconductor sales to grow "
                "90% to USD 1.5 trillion in 2026."
            ),
            source_url="https://www.wsts.org/76/example-release",
            source_date=date(2026, 6, 5),
            source_ref="R1",
        ),
        ResearchClaim(
            claim=(
                "SIA reported that April 2026 global semiconductor sales were USD 110.5 billion, "
                "11% above March 2026."
            ),
            source_url="https://www.semiconductors.org/global-semiconductor-sales-increase-11",
            source_date=date(2026, 6, 5),
            source_ref="R2",
        ),
    ]


def test_verified_evidence_is_visible_idempotent_and_passes_citations(tmp_path):
    deck = attach_verified_evidence(_deck(), _claims())
    twice = attach_verified_evidence(deck, _claims())

    assert twice.count('id="verified-evidence"') == 1
    assert twice.count('href="#verified-evidence"') >= 1
    assert "<ul>" in twice and "target=\"_blank\"" in twice
    assert "hidden" not in twice

    artifact = tmp_path / SLIDES_ARTIFACT
    artifact.parent.mkdir()
    artifact.write_text(twice, encoding="utf-8")
    assert check_citations(tmp_path, SLIDES_ARTIFACT, _claims(), as_of=date(2026, 11, 25)) == []


def test_verified_evidence_works_without_a_nav():
    deck = _deck().replace("<nav><a href=\"#one\">Start</a></nav>", "")
    injected = attach_verified_evidence(deck, _claims())
    assert injected.count('id="verified-evidence"') == 1
    assert injected.count('href="#verified-evidence"') >= 1
    assert "</main>" in injected