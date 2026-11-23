"""Read-only evidence and draft checks for the Mid Test newsroom task."""

import re
import stat
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Annotated
from unicodedata import normalize
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, ValidationError

from orchestrator.artifacts import inside
from protocol.models import Contract

EVENT_ARTIFACT = "outputs/event-evidence.json"
ARTICLE_ARTIFACT = "outputs/hkust-news.md"
EVENT_GUIDANCE = (
    "Search for a concrete HKUST campus AI event that occurred within the last 120 days. "
    f"Write {EVENT_ARTIFACT} as JSON with event_name, event_date (YYYY-MM-DD), venue, "
    "source_url (the exact HTTPS link of a specific official HKUST search result) and source_quote "
    "(copy the passage that states when the event occurred, including its name or venue). Choose "
    "a result whose title and excerpt together give the event name and venue. A schedule, "
    "publication date or dateline cannot establish that an event occurred."
)
ARTICLE_GUIDANCE = (
    "Read the reporter's verified event evidence under inputs/shared/reporter/event-evidence.json. "
    f"Write {ARTICLE_ARTIFACT}, keeping the event name, date, venue and exact source URL visible. "
    "State when the event itself happened, and why it matters to students and faculty. Include "
    "labelled Markdown sections for Website, Social and Faculty. Use only supported facts and "
    "do not claim the newsroom conducted interviews or contacted anyone."
)

_MAX_ARTICLE_BYTES = 1_048_576
_OFFICIAL_DOMAINS = ("hkust.edu.hk", "ust.hk")
_INDEX_PATHS = {"", "home", "index", "news", "news-and-stories", "events", "calendar", "latest"}
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
_PASSAGES = re.compile(r"(?<=[.!?;])\s+(?![\d\s])|(?<=[。！？；])\s*|\n+")
_EVENT_VERB = re.compile(
    r"\b(?:(?:co-)?held|took place|(?:co-)?hosted|opened|concluded|launched|unveiled"
    r"|gathered|met|ran|kicked off|was inaugurated|started|began|ended|celebrated)\b"
    r"|举办|舉辦|举行|舉行|召开|召開|开幕|開幕",
    re.I,
)
_NON_OCCURRENCE = re.compile(
    r"\b(?:will|would|shall|can|may|might|could|should|never|not|"
    r"(?:planned|scheduled|expected|slated|set|due)\s+to)\s+"
    r"(?:(?:have|be|been|[a-z]+ly)\s+)*$"
    r"|(?:將於|将于|將在|将在|預計|预计|擬於|拟于|定於|定于|原定|尚未|並未|并未)$",
    re.I,
)
_METADATA_DATE = re.compile(
    r"(?:dateline|published|updated|last modified|posted|retrieved|accessed|prepared|article date"
    r"|release date|发布日期|發佈日期)(?:\s+(?:on|at))?\s*[:：–—-]?\s*$",
    re.I,
)
_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+([^\n]+)", re.M)
_CHANNELS = {
    "website": re.compile(r"\b(?:web|website|online article)\b|网站|網站|网页|網頁", re.I),
    "social": re.compile(r"\b(?:social|instagram|facebook|linkedin)\b|社交|社群", re.I),
    "faculty": re.compile(r"\b(?:faculty|staff bulletin)\b|教职员|教職員|教师|教師", re.I),
}
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_OUTREACH = re.compile(
    r"\b(?:we|(?:our|the) (?:newsroom|team|desk)|(?:the|this) (?:editor|reporter))\s+"
    r"(?:(?:have|has|had|did|already|also|personally)\s+)*"
    r"(?:contact(?:ed)?|interview(?:ed)?|spoke (?:with|to)|sent (?:a|an) (?:request|email)"
    r"|e-?mailed|called|reached out|requested (?:a|an) (?:comment|response|interview))\b"
    r"|(?:我們|我们|本部|本組|本组|記者團隊|记者团队)(?:已|曾|已经|已經)?\s*"
    r"(?:聯絡了|联络了|联系了|聯繫了|采访了|採訪了)",
    re.I,
)

_Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)
]


class _EventEvidence(Contract):
    event_name: _Description
    event_date: date = Field(strict=True)
    venue: _Description
    source_url: Annotated[str, StringConstraints(min_length=8, max_length=2048)]
    source_quote: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=20, max_length=1200)
    ]


def _read_text(root: Path, artifact: str) -> str | None:
    try:
        target = inside(root, artifact)
        info = target.lstat()
        for path in (root, target.parent, target):
            link_info = path.lstat()
            if stat.S_ISLNK(link_info.st_mode) or (
                getattr(link_info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                return None
        if stat.S_ISREG(info.st_mode) and info.st_size <= _MAX_ARTICLE_BYTES:
            with target.open("rb") as stream:
                data = stream.read(_MAX_ARTICLE_BYTES + 1)
            if len(data) <= _MAX_ARTICLE_BYTES:
                return data.decode("utf-8-sig")
    except (OSError, ValueError):
        pass
    return None


def _official_source(url: str) -> bool:
    if any(char.isspace() or ord(char) < 32 or char == "\\" for char in url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        return (
            parts.scheme == "https"
            and parts.username is None
            and parts.port in (None, 443)
            and any(host == domain or host.endswith(f".{domain}") for domain in _OFFICIAL_DOMAINS)
            and Path(parts.path.strip("/").split("/")[-1].casefold()).stem not in _INDEX_PATHS
        )
    except ValueError:
        return False


def _quote(text: str) -> str:
    return " ".join(normalize("NFKC", text).casefold().split())


def _words(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", normalize("NFKC", text).casefold()))


def _contains_phrase(text: str, value: str) -> bool:
    # English phrases use word boundaries. Chinese text can abut surrounding characters.
    pattern = _words(value)
    if re.search(r"[\u3400-\u9fff]", pattern):
        return bool(pattern) and pattern in text
    return bool(pattern) and f" {pattern} " in f" {text} "


def _date_pattern(event: date) -> re.Pattern[str]:
    month = _MONTHS[event.month - 1]
    month_name = rf"(?:{month}|{month[:3]}{'t?' if event.month == 9 else ''}\.?)"
    day = rf"0?{event.day}(?:st|nd|rd|th)?"
    year = str(event.year)
    return re.compile(
        rf"(?<!\d)(?:{year}-0?{event.month}-0?{event.day}|"
        rf"{day}\s+{month_name}\s*,?\s*{year}|{month_name}\s+{day}\s*,?\s*{year}|"
        rf"{year}年\s*0?{event.month}月\s*0?{event.day}日)(?!\d)",
        re.I,
    )


def _states_event_date(text: str, event: _EventEvidence) -> bool:
    pattern = _date_pattern(event.event_date)
    for passage in _PASSAGES.split(normalize("NFKC", text)):
        actions = list(_EVENT_VERB.finditer(passage))
        words = _words(passage)
        if not actions or not any(
            _contains_phrase(words, detail) for detail in (event.event_name, event.venue)
        ):
            continue
        for match in pattern.finditer(passage):
            before_date = passage[: match.start()].rstrip(" *_`")
            if _METADATA_DATE.search(before_date) or _NON_OCCURRENCE.search(before_date):
                continue
            action = min(
                actions,
                key=lambda verb: min(
                    abs(verb.start() - match.end()), abs(verb.end() - match.start())
                ),
            )
            if not _NON_OCCURRENCE.search(passage[: action.start()]):
                return True
    return False


def _load_event(
    root: Path, observations: Mapping[str, Sequence[str]], today: date
) -> tuple[_EventEvidence | None, list[str]]:
    raw = _read_text(root, EVENT_ARTIFACT)
    if raw is None:
        return None, [f"Write an ordinary UTF-8 {EVENT_ARTIFACT} with the requested event fields."]
    try:
        event = _EventEvidence.model_validate_json(raw)
    except ValidationError:
        return None, [
            f"{EVENT_ARTIFACT} must contain event_name, event_date (YYYY-MM-DD), venue, "
            "source_url and source_quote as requested."
        ]
    if not 0 <= (today - event.event_date).days <= 120:
        return None, [
            "Find an event that occurred within the last 120 days and give its actual date."
        ]
    if not _official_source(event.source_url):
        return None, [
            "Cite a specific official HKUST event or news page using its exact HTTPS URL."
        ]

    excerpt_list = observations.get(event.source_url, ())
    matching = [
        excerpt for excerpt in excerpt_list if _quote(event.source_quote) in _quote(excerpt)
    ]
    if not matching:
        return None, [
            "Use the exact URL and a verbatim excerpt from a successful newsroom search "
            "in this run."
        ]
    if not _states_event_date(event.source_quote, event) or not any(
        _contains_phrase(_words(excerpt), event.event_name)
        and _contains_phrase(_words(excerpt), event.venue)
        and _states_event_date(excerpt, event)
        for excerpt in matching
    ):
        return None, [
            "Quote the passage that explicitly states when the event occurred. The official "
            "result must identify the event and venue. Schedules and publication dates "
            "do not count."
        ]
    return event, []


def check_event(root: Path, observations: Mapping[str, Sequence[str]], today: date) -> list[str]:
    """Return reporter defects based only on the artifact and observed search excerpts."""
    _, defects = _load_event(root, observations, today)
    return defects


def check_article(
    root: Path, reporter_root: Path, observations: Mapping[str, Sequence[str]], today: date
) -> list[str]:
    """Tie the editor's draft to the current good reporter artifact before manual review."""
    event, _ = _load_event(reporter_root, observations, today)
    if event is None:
        return [
            "Use the reporter's event evidence once its source and occurrence date are verified."
        ]
    article = _read_text(root, ARTICLE_ARTIFACT)
    if not article or not article.strip():
        return [f"Write the requested news report and channel copies in {ARTICLE_ARTIFACT}."]
    defects = []
    words = _words(article)
    urls = {url.rstrip(".,;:") for url in _URL.findall(article)}
    if (
        not _contains_phrase(words, event.event_name)
        or not _contains_phrase(words, event.venue)
        or not _states_event_date(article, event)
        or event.source_url not in urls
    ):
        defects.append("Match the reporter's verified event name, venue, occurrence date and URL.")
    headings = _HEADING.findall(article)
    for channel, pattern in _CHANNELS.items():
        if not any(pattern.search(heading) for heading in headings):
            defects.append(f"Add a labelled Markdown section for the {channel} channel.")
    if _OUTREACH.search(article):
        defects.append(
            "Remove the unsupported claim that this newsroom contacted or interviewed anyone."
        )
    return defects
