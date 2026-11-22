"""T1 source matching and two-column citation acceptance."""

import pytest

from harness.search_evidence import SOURCES, matched_source_urls, source_pair_defects

RELEASE, PEP = (url for url, _ in SOURCES)
RELEASE_DATE, BUILD_FLAG = (fact for _, fact in SOURCES)


def _table(*rows: tuple[str, str]) -> str:
    return "\n".join(["| URL | Fact |", "| --- | --- |", *(f"| {u} | {f} |" for u, f in rows)])


def test_search_rows_are_deduplicated_with_canonical_url_spelling():
    rows = [
        {"url": PEP.removesuffix("/") + "#build-configuration"},
        {"url": RELEASE.replace("www.python.org", "WWW.PYTHON.ORG")},
        {"url": PEP},
        {"url": "https://www.python.org/"},
        {"url": 42},
        None,
    ]
    assert matched_source_urls(rows) == [PEP, RELEASE]
    assert matched_source_urls({"results": rows}) == []


@pytest.mark.parametrize(
    "url",
    [
        RELEASE.replace("www.python.org", "www.python.org.lookalike.example"),
        RELEASE.replace("www.python.org", "www.python.org@lookalike.example"),
        RELEASE.replace("www.python.org", "account@www.python.org"),
        RELEASE.replace("https://", "http://"),
        RELEASE + "?redirect=lookalike.example",
        PEP.replace("pep-0703", "pep-0702"),
    ],
)
def test_lookalikes_and_wrong_pages_do_not_count_as_evidence(url: str):
    assert matched_source_urls([{"url": url}]) == []
    defects = source_pair_defects(_table((url, RELEASE_DATE), (PEP, BUILD_FLAG)))
    assert any("allowlisted" in defect for defect in defects)
    assert any(f"missing source row for {RELEASE}" == defect for defect in defects)


def test_markdown_accepts_exact_pairs_with_backticks_and_url_fragment():
    markdown = "# Research note\n\n" + _table(
        (RELEASE.removesuffix("/"), f"`{RELEASE_DATE}`"),
        (PEP + "#build-configuration", f"``{BUILD_FLAG}``"),
    )
    assert source_pair_defects(markdown) == []


def test_correct_facts_cannot_be_swapped_between_sources():
    defects = source_pair_defects(_table((RELEASE, BUILD_FLAG), (PEP, RELEASE_DATE)))
    assert len(defects) == 2
    assert all("fact does not match" in defect for defect in defects)


def test_missing_source_and_duplicate_canonical_source_are_reported():
    defects = source_pair_defects(_table((RELEASE, RELEASE_DATE), (RELEASE + "#notes", BUILD_FLAG)))
    assert any("duplicate source row" in defect for defect in defects)
    assert any("fact does not match" in defect for defect in defects)
    assert f"missing source row for {PEP}" in defects


def test_prose_and_fenced_examples_do_not_count_as_citation_rows():
    table = _table((RELEASE, RELEASE_DATE), (PEP, BUILD_FLAG))
    assert source_pair_defects(f"```markdown\n{table}\n```\n")
    assert source_pair_defects(_table((RELEASE, RELEASE_DATE)) + f"\n\n{PEP} {BUILD_FLAG}")


def test_fact_must_be_the_complete_value_and_row_must_have_two_columns():
    assert source_pair_defects(_table((RELEASE, f"Released on {RELEASE_DATE}"), (PEP, BUILD_FLAG)))
    assert source_pair_defects(_table((RELEASE, RELEASE_DATE), (PEP, BUILD_FLAG + " | note")))
