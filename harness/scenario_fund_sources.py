"""Check the chain from this run's research results to the fund deliverables."""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from unicodedata import normalize
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator

from harness.search_evidence import visible_markdown_lines
from orchestrator.artifacts import inside
from protocol.models import Contract

RESEARCH_ARTIFACT = "outputs/research-evidence.json"
SLIDES_ARTIFACT = "outputs/semiconductor-outlook.html"
RESEARCH_GUIDANCE = (
    "Use web_search before writing. Search for specific releases on issuer, regulator, exchange "
    "or industry association sites, using recent dated material for an as-of analysis. Use a "
    "release URL instead of a homepage or an article republishing the release. "
    f"Write {RESEARCH_ARTIFACT} as an object with one key, claims, whose value is a list. "
    "Each row has only claim, source_url, source_ref and source_date; omit prose and extra "
    "keys from the JSON. Keep each claim to one short factual statement. Select the ID "
    "displayed at the start of one result title, such as "
    "[R7], as source_ref (write R7 without brackets). Use only a displayed ID and copy its "
    "exact HTTPS URL. Use only facts, periods, percentages and process sizes stated in that "
    "result. Include the period for time-sensitive figures. For a fund fee percentage, "
    "use a dated issuer document and give its date in the claim, or omit the optional figure. Set "
    "source_date to null unless the same result explicitly shows the publication date; "
    "never use the reporting period or search date as the publication date. Cover the "
    "material factual premises, and keep house scenarios separate. On repair, focus on the "
    "named claim: search for a result that states the relevant fact and period together, then "
    "update the claim and source reference or drop the unsupported premise."
)
OUTLOOK_RESEARCH_GUIDANCE = (
    "For the end-of-year semiconductor outlook, find at least one material industry outlook "
    "or forecast published in the evaluation year and available by the evaluation date. Put "
    "its full publication date in source_date if the search result states it. If the result "
    "gives a named release edition (e.g. Spring with its year and the word Forecast) but no "
    "day, put that exact edition in the claim and use null for source_date. Search for a newer "
    "release rather than relying only on an older forecast about the evaluation year. "
    "Use older releases as clearly dated historical comparisons."
)
CITATION_GUIDANCE = (
    "Read the current research evidence at inputs/shared/research/research-evidence.json. "
    "In the visible deliverable quote each claim sentence as written and include its exact "
    "source link in the same paragraph or bullet (same Markdown line, or same HTML p/li). "
    "Put supporting evidence beside the analysis it supports. Separate factual statements, "
    "attributed forecasts and your own assumptions. Keep the stated period next to each dated "
    "claim and avoid calling historical figures current. When research is repaired, read the new "
    "shared file and update any affected claims, explanations and citations."
)
SLIDES_GUIDANCE = (
    f"Write a single self-contained {SLIDES_ARTIFACT} playable offline in a browser. "
    "Give each slide a unique section id and use local fragment links to let readers move "
    "through the slides. Include research findings, investment implications and key risks. "
    "Show the publication date by each dated outlook source in the visible slide content. "
    "Identify older forecasts as historical releases and base the current outlook on the "
    "evaluation year's evidence."
)

_MAX_BYTES = 256 * 1024
_INDEX_PAGES = {
    "home",
    "index",
    "latest",
    "news",
    "newsroom",
    "press-releases",
    "recent-news-release",
    "press-archive",
    "investor",
    "investors",
    "investor-relations",
    "ir",
    "financial-calendar",
    "quarterly-results",
}
_MARKDOWN_LINK = re.compile(r"\[[^\]\n]+\]\(<?([^\s)>]+)>?\)")
_INLINE_URL = re.compile(r"https://[^\s<>()\[\]\"']+")
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_YEAR = re.compile(r"(?<!\d)20\d\d(?!\d)")
_LATEST = re.compile(r"\blatest (?:reported|available|quarter|data|figures?)\b", re.I)
_OUTLOOK_CUE = re.compile(
    r"\b(?:outlook|forecast\w*|project(?:s|ed|ion\w*)|predict\w*|estimat\w*|"
    r"expect\w*|anticipat\w*|guidance)\b",
    re.I,
)
_SECTOR = re.compile(
    r"\b(?:semiconductor\w*|chip\w*|wafer\w*|foundr\w*|fab(?:s|rication)?|memory)\b",
    re.I,
)
_MATERIAL = re.compile(
    r"\b(?:market|demand|orders?|sales|revenue|growth|capacity|supply|production|shipments?|"
    r"price\w*|capex|capital spending|capital expenditure\w*|spending|margins?)\b",
    re.I,
)
_PERIOD_PREFIX = re.compile(
    r"\b(?:(?:quarter|period|year)\s+(?:end(?:s|ed|ing)?|through)|"
    r"forecast (?:for|through)|as of)\s*[:,-]?\s*$",
    re.I,
)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_UNIT_AMOUNT = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)[\s-]*(nm\b|nanomet(?:er|re)s?\b|纳米|奈米|%|percent\b)",
    re.I,
)
_APPROX_PERCENT = re.compile(
    r"(?:\b(?:about|roughly|approximately|around|approx\.?)\s+|(?:约|約)\s*)"
    r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)",
    re.I,
)
_FUND_FEE = re.compile(r"\b(?:expense ratios?|management fees?|fund fees?)\b", re.I)


class ResearchClaim(Contract):
    claim: str = Field(min_length=12, max_length=800)
    source_url: str = Field(min_length=8, max_length=2048)
    source_date: date | None = Field(strict=True)
    source_ref: str | None = Field(default=None, pattern=r"^R[1-9]\d*$", max_length=12)
    source_quote: str | None = Field(default=None, min_length=20, max_length=1200)

    @field_validator("claim", "source_url", "source_quote")
    @classmethod
    def nonempty_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("supply text without leading or trailing whitespace")
        return value

    @model_validator(mode="after")
    def evidence_selector(self) -> "ResearchClaim":
        if (self.source_ref is None) == (self.source_quote is None):
            raise ValueError("supply one recorded source_ref or verbatim source_quote")
        return self


class _Research(Contract):
    claims: list[ResearchClaim] = Field(min_length=1, max_length=12)


def _read(root: Path, relative: str) -> str:
    try:
        path = inside(root, relative)
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                content = stream.read(_MAX_BYTES + 1)
            if len(content) <= _MAX_BYTES:
                return content.decode("utf-8-sig")
    except (OSError, ValueError):
        pass
    raise ValueError(f"Write {relative} as a readable UTF-8 file within 256 KiB.")


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _claim_tokens(text: str) -> str:
    """Ignore display punctuation while retaining words, values and comparison operators."""
    text = normalize("NFKC", text).casefold()
    return " ".join(re.findall(r"\d+(?:[.,]\d+)*|[^\W\d_]+|[<>]=?|[%≤≥]", text))


def _direct_source(url: str) -> bool:
    if any(char.isspace() or ord(char) < 32 or char == "\\" for char in url):
        return False
    try:
        parts = urlsplit(url)
        leaf = parts.path.rstrip("/").rsplit("/", 1)[-1].casefold()
        return (
            parts.scheme == "https"
            and parts.hostname is not None
            and parts.username is None
            and parts.port in (None, 443)
            and bool(leaf)
            and re.sub(r"\.(?:html?|aspx?)$", "", leaf) not in _INDEX_PAGES
        )
    except ValueError:
        return False


def _includes_date(text: str, value: date, *, publication: bool = False) -> bool:
    month = _MONTHS[value.month - 1]
    short = "Sept?" if value.month == 9 else month[:3]
    month_name = rf"(?:{month}|{short}\.?)"
    month_num = rf"0?{value.month}"
    day = rf"0?{value.day}"
    year = str(value.year)
    day_name = rf"{day}(?:st|nd|rd|th)?"
    pattern = re.compile(
        rf"(?<![\w\d])(?:{year}-{month_num}-{day}|{year}/{month_num}/{day}|"
        rf"{year}年{month_num}月{day}日|{month_name}\s+{day_name}(?:,\s*|\s+){year}|"
        rf"{day_name}\s+{month_name}(?:,\s*|\s+){year})(?!\d)",
        re.I,
    )
    return any(
        not publication or not _PERIOD_PREFIX.search(text[: match.start()])
        for match in pattern.finditer(text)
    )


def _material_outlook(text: str, context: str = "") -> bool:
    return bool(
        _OUTLOOK_CUE.search(text)
        and _MATERIAL.search(text)
        and (_SECTOR.search(text) or _SECTOR.search(context))
    )


def _editions(text: str, today: date) -> set[str]:
    seasons = {"winter": 1, "spring": 3, "summer": 6, "autumn": 9, "fall": 9}
    return {
        match[1].casefold()
        for match in re.finditer(
            rf"\b({'|'.join(seasons)})\s+{today.year}\s+"
            r"(?:[a-z-]+\s+){0,5}(?:forecast|outlook)\b",
            text,
            re.I,
        )
        if seasons[match[1].casefold()] <= today.month
    }


def current_outlook_choice(url: str, text: str, today: date) -> str | None:
    """Highlight a dated forecast edition already present in a direct search result."""
    if not _direct_source(url) or not _material_outlook(text):
        return None
    editions = sorted(_editions(text, today))
    return f"{editions[0].title()} {today.year} Forecast" if editions else None


def _unit_amounts(text: str) -> set[tuple[Decimal, str]]:
    return {
        (Decimal(amount), "%" if unit.casefold() in {"%", "percent"} else "nm")
        for amount, unit in _UNIT_AMOUNT.findall(text)
    }


def _supports_amounts(claim: str, source: str) -> bool:
    source_amounts = _unit_amounts(source)
    unsupported = _unit_amounts(claim) - source_amounts
    approximations = {Decimal(number) for number in _APPROX_PERCENT.findall(claim)}
    return all(
        unit == "%"
        and amount in approximations
        and any(
            source_unit == "%"
            and abs(observed - amount) <= Decimal(5).scaleb(amount.as_tuple().exponent - 1)
            for observed, source_unit in source_amounts
        )
        for amount, unit in unsupported
    )


def check_research(
    root: Path,
    observations: Mapping[str, Sequence[str]],
    today: date,
    *,
    references: Mapping[str, tuple[str, str]] | None = None,
    artifact: str = RESEARCH_ARTIFACT,
    require_current_outlook: bool = False,
) -> tuple[list[ResearchClaim], list[str]]:
    """Match every cited source and excerpt to this researcher's search results."""
    no_search = not (observations or references)
    no_search_defect = (
        "No usable web_search results were recorded for research in this task. Run a "
        "targeted web_search and save claims backed by the returned URLs and exact excerpts."
    )
    try:
        research = _Research.model_validate_json(_read(root, artifact))
    except ValidationError as error:
        defects = [
            f"Research evidence field {'.'.join(map(str, item['loc'])) or 'root'}: {item['msg']}."
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        ]
        if no_search:
            defects.insert(0, no_search_defect)
        return [], defects
    except ValueError as error:
        defects = [str(error)]
        if no_search:
            defects.insert(0, no_search_defect)
        return [], defects
    if no_search:
        return [], [no_search_defect]
    defects = []
    current_outlook = False
    for index, item in enumerate(research.claims, start=1):
        label = f"Claim {index} ({item.claim[:100]})"
        if not _direct_source(item.source_url):
            defects.append(f"{label}: find a specific HTTPS source document, not an index.")
            continue
        if item.source_ref is not None:
            source = (references or {}).get(item.source_ref)
            if not source or source[0] != item.source_url:
                alternate = next(
                    (ref for ref, (url, _) in (references or {}).items() if url == item.source_url),
                    None,
                )
                hint = (
                    f" That exact URL was returned as {alternate}."
                    if alternate
                    else f" {item.source_ref} refers to {source[0]}."
                    if source
                    else ""
                )
                defects.append(
                    f"{label}: use a source_ref returned in this task with its exact paired URL."
                    f"{hint}"
                )
                continue
            matches = [_normalize(source[1])]
            supporting_text = source[1]
        else:
            supporting_text = item.source_quote or ""
            matches = [
                _normalize(observation)
                for observation in observations.get(item.source_url, ())
                if _normalize(supporting_text) in _normalize(observation)
            ]
        if not matches:
            defects.append(
                f"{label}: URL and excerpt must match the same recorded search result. "
                "If this URL came from search, copy one complete contiguous sentence exactly; "
                "otherwise find a result for this claim. Revise or remove the claim if unsupported."
            )
        elif not _supports_amounts(item.claim, supporting_text):
            defects.append(
                f"{label}: match each percentage or process node to the quoted source, "
                "or find a result that supports the stated figure."
            )
        elif not set(_YEAR.findall(item.claim)) <= set(_YEAR.findall(supporting_text)):
            defects.append(f"{label}: match the stated year to the recorded source.")
        elif item.source_date is None and _LATEST.search(item.claim):
            defects.append(f"{label}: state the documented reporting period instead of 'latest'.")
        elif item.source_date is not None and (
            item.source_date > today
            or not any(_includes_date(text, item.source_date) for text in matches)
        ):
            defects.append(f"{label}: correct the unsupported source_date or set it to null.")
        elif (
            artifact == RESEARCH_ARTIFACT
            and _FUND_FEE.search(item.claim)
            and any(unit == "%" for _, unit in _unit_amounts(item.claim))
            and (item.source_date is None or not _includes_date(item.claim, item.source_date))
        ):
            defects.append(
                f"{label}: state the date of a documented fee from an issuer source, "
                "or omit the optional figure. An undated excerpt does not establish a current fee."
            )
        elif require_current_outlook and any(
            _material_outlook(item.claim, text)
            and (
                (
                    item.source_date is not None
                    and item.source_date.year == today.year
                    and _includes_date(text, item.source_date, publication=True)
                )
                or bool(_editions(item.claim, today) & _editions(text, today))
            )
            for text in matches
        ):
            current_outlook = True
    if require_current_outlook and not current_outlook:
        defects.append(
            f"Add a material semiconductor industry outlook published in {today.year} "
            f"by {today.isoformat()}. Use a full publication date or its explicitly named "
            "release edition from the same recorded result. A reporting period alone "
            "does not establish the release edition."
        )
        for ref, (url, text) in (references or {}).items():
            if edition := current_outlook_choice(url, text, today):
                defects.append(
                    f"Recorded result {ref} ({url}) explicitly names the {edition}. "
                    "Choose a short semiconductor outlook claim supported by that result "
                    "and include the release edition. If there is no full date, use null."
                )
                break
    return research.claims, defects


class _Slides(HTMLParser):
    """Collect visible citation blocks and local navigation targets without executing the page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[str] = []
        self.targets: set[str] = set()
        self.blocks: list[tuple[str, set[str]]] = []
        self.block: str | None = None
        self.words: list[str] = []
        self.links: set[str] = set()
        self.ignored: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.ignored is not None:
            return
        if tag in {"head", "script", "style", "template"}:
            self.ignored = tag
            return
        values = dict(attrs)
        if tag == "section" and values.get("id"):
            self.sections.append(values["id"] or "")
        if tag in {"p", "li"}:
            self._save_block()
            if "hidden" not in values:
                self.block = tag
        if tag == "a" and "hidden" not in values and (href := values.get("href")):
            if href.startswith("#"):
                self.targets.add(href[1:])
            if self.block is not None:
                self.links.add(href)

    def handle_endtag(self, tag: str) -> None:
        if self.ignored is not None:
            if tag == self.ignored:
                self.ignored = None
        elif tag == self.block:
            self._save_block()

    def handle_data(self, data: str) -> None:
        if self.ignored is None and self.block is not None:
            self.words.append(data)

    def _save_block(self) -> None:
        if self.block is not None:
            self.blocks.append(("".join(self.words), self.links))
            self.block, self.words, self.links = None, [], set()


def check_citations(
    root: Path,
    artifact: str,
    claims: Sequence[ResearchClaim],
    *,
    as_of: date | None = None,
) -> list[str]:
    """Tie the checked claims to a visible statement and link in the actual deliverable."""
    try:
        text = _read(root, artifact)
    except ValueError as error:
        return [str(error)]
    defects = []
    if artifact == SLIDES_ARTIFACT:
        deck = _Slides()
        deck.feed(text)
        deck.close()
        ids = set(deck.sections)
        if len(ids) < 2 or len(ids) != len(deck.sections) or not ids <= deck.targets:
            defects.append("Give each slide a unique section id and a local link targeting it.")
        blocks = deck.blocks
    else:
        blocks = []
        for line in visible_markdown_lines(_COMMENTS.sub("", text)):
            links = set(_MARKDOWN_LINK.findall(line))
            plain = _MARKDOWN_LINK.sub("", line)
            links.update(url.rstrip(".,;:") for url in _INLINE_URL.findall(plain))
            blocks.append((_INLINE_URL.sub("", plain), links))
    for item in claims:
        attributed = [
            block
            for block, urls in blocks
            if item.source_url in urls and (
                f" {_claim_tokens(item.claim)} " in f" {_claim_tokens(block)} "
            )
        ]
        if not attributed:
            defects.append(
                f"Use this research claim with its adjacent source link in {artifact}: "
                f"{item.claim} ({item.source_url})"
            )
        elif (
            artifact == SLIDES_ARTIFACT
            and as_of is not None
            and item.source_date is not None
            and _material_outlook(item.claim)
            and not any(_includes_date(block, item.source_date) for block in attributed)
        ):
            kind = "historical release" if item.source_date.year < as_of.year else "outlook"
            defects.append(
                f"Show the {kind} publication date {item.source_date.isoformat()} next to "
                f"its claim and link in {artifact}."
            )
    return defects
