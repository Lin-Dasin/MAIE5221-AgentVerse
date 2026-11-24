"""Deterministic evidence checks for the T1 official-source search pilot."""

import re
from urllib.parse import urlsplit

SOURCES = (
    ("https://www.python.org/downloads/release/python-3130/", "2024-10-07"),
    ("https://peps.python.org/pep-0703/", "--disable-gil"),
)

_OFFICIAL_PAGES = {
    (parts.netloc, parts.path.removesuffix("/")): url
    for url, _ in SOURCES
    for parts in [urlsplit(url)]
}


def _official_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.query:
        return None
    return _OFFICIAL_PAGES.get((parts.netloc.lower(), parts.path.removesuffix("/")))


def matched_source_urls(rows: object) -> list[str]:
    """Return distinct canonical T1 URLs in the order found in B's result rows."""
    if not isinstance(rows, list):
        return []
    matched = []
    for row in rows:
        if isinstance(row, dict):
            url = _official_url(row.get("url"))
            if url is not None and url not in matched:
                matched.append(url)
    return matched


def _table_cells(line: str) -> list[str]:
    if "|" not in line:
        return []
    row = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in row.split("|")]


def visible_markdown_lines(markdown: str) -> list[str]:
    lines = []
    fence = ""
    for line in markdown.splitlines():
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if fence:
            if marker and line.strip() == marker[1] and marker[1].startswith(fence):
                fence = ""
            lines.append("")
        elif marker:
            fence = marker[1]
            lines.append("")
        else:
            lines.append(line)
    return lines


def source_pair_defects(markdown: str) -> list[str]:
    """Check the official URLs and exact facts in one two-column Markdown table."""
    lines = visible_markdown_lines(markdown)
    cells = [_table_cells(line) for line in lines]
    headers = []
    for index, (header, separator) in enumerate(zip(cells, cells[1:], strict=False)):
        if [cell.casefold() for cell in header] == ["url", "fact"] and (
            len(separator) == 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        ):
            headers.append(index)
    if len(headers) != 1:
        return ["expected one two-column URL | Fact table"]

    expected = dict(SOURCES)
    seen: set[str] = set()
    defects = []
    for row in cells[headers[0] + 2 :]:
        if not row:
            break
        if len(row) != 2:
            defects.append("expected a URL and a fact in each source row")
            continue
        url = _official_url(row[0])
        if url is None:
            defects.append("source row does not cite an allowlisted official page")
            continue
        if url in seen:
            defects.append(f"duplicate source row for {url}")
        seen.add(url)
        code_span = re.fullmatch(r"(`+)([^`]+)\1", row[1])
        fact = code_span[2].strip() if code_span else row[1]
        if fact != expected[url]:
            defects.append(f"fact does not match source {url}")
    defects.extend(f"missing source row for {url}" for url in expected if url not in seen)
    return defects
