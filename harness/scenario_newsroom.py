"""Read-only evidence and draft checks for the Mid Test newsroom report."""

import json
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from unicodedata import normalize
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, ValidationError, model_validator

from orchestrator.artifacts import inside
from protocol.models import Contract

EVENT_ARTIFACT = "outputs/event-evidence.json"
ARTICLE_ARTIFACT = "outputs/hkust-news.md"
EVENT_GUIDANCE = (
    "Start with one targeted web_search for a completed HKUST campus AI event and ask for at "
    "most three results. Look for an official recap that explicitly states when the event took "
    "place. Use the event's occurrence date rather than the page's publication date. "
    f"Write {EVENT_ARTIFACT} with only event_name, event_date (YYYY-MM-DD), venue, source_url "
    "and source_ref. Copy the result's exact HTTPS URL and the short ID shown beside its "
    "title (e.g. R1, without brackets) from the same result row. The returned title and excerpt "
    "must identify the event and venue and explicitly confirm it happened on that date. "
    "Use the short event name and venue exactly as they appear in the result. Do not add an "
    "address, district or campus name that the result does not say. "
    "A calendar announcement or planned date is insufficient. Save the evidence before writing "
    "other notes, and keep limitations, commentary and extra fields out of the JSON. "
    "If a promising recent story only has a dateline or lacks a venue, use its exact event name "
    "in a follow-up search for an official post-event news release. Confirm the occurrence "
    "date and venue before writing. Discard a lead with an occurrence date outside the range."
)
ARTICLE_GUIDANCE = (
    "Read inputs/shared/reporter/verified-event.json after the reporter's handoff. It contains "
    "the checked occurrence start and end dates, venue and source. Use the full verified date "
    "range in each channel. If it is unavailable, the reporter's event evidence is provisional. "
    f"Write {ARTICLE_ARTIFACT}, keeping the event name, date, venue and exact source URL visible. "
    "State when the event itself happened, and why it matters to students and faculty. Include "
    "labelled Markdown sections for Website, Social and Faculty. Use only supported facts and "
    "do not claim the newsroom conducted interviews or contacted anyone."
)

_MAX_ARTICLE_BYTES = 1_048_576
_OFFICIAL_DOMAINS = ("hkust.edu.hk", "ust.hk")
_INDEX_PATHS = {
    "",
    "home",
    "index",
    "news",
    "news-and-stories",
    "events",
    "calendar",
    "latest",
    "mba-pulse",
}
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
_EVENT_KIND = r"(?:summit|hackathon|symposium|conference|forum|seminar|workshop|festival|showcase)"
_TITLE_TAIL = rf"(?:\s+(?:and|ai|{_EVENT_KIND}))*(?:[\s'\u2019\u201d\")]*)"
_NAMED_EVENT_YEAR = re.compile(
    rf"\b(?:{_EVENT_KIND}\s+(20\d{{2}})|"
    rf"(20\d{{2}})\s+(?:(?-i:[A-Z])[^\W_]*\s+){{0,5}}{_EVENT_KIND})"
    rf"{_TITLE_TAIL}(?:\s*(?:officially|successfully|recently|was|were|has|had))*\s*$",
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
    source_ref: str | None = Field(default=None, pattern=r"^R[1-9]\d*$", max_length=12)
    source_quote: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=1200)]
        | None
    ) = None

    @model_validator(mode="after")
    def evidence_selector(self) -> "_EventEvidence":
        if (self.source_ref is None) == (self.source_quote is None):
            raise ValueError("supply one source_ref or verbatim source_quote")
        return self


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


def official_event_source(url: str) -> bool:
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


def initial_event_search(today: date) -> str:
    """Find a recent HKUST recap without preselecting a story or source."""
    first = today - timedelta(days=120)
    periods = []
    for index in range(first.year * 12 + first.month - 1, today.year * 12 + today.month):
        year, month = divmod(index, 12)
        periods.append(f"{_MONTHS[month]} {year}")
    return (
        "site:hkust.edu.hk HKUST Hong Kong campus artificial intelligence completed event recap "
        "news issued " + " ".join(periods) + " hackathon symposium took place HKUST"
    )


def _quote(text: str) -> str:
    return " ".join(normalize("NFKC", text).casefold().split())


def _words(text: str) -> str:
    normalized = normalize("NFKC", text).casefold().replace("&", " and ")
    return " ".join(re.findall(r"[^\W_]+", normalized))


def _contains_phrase(text: str, value: str) -> bool:
    # English phrases use word boundaries. Chinese text can abut surrounding characters.
    pattern = _words(value)
    if re.search(r"[\u3400-\u9fff]", pattern):
        return bool(pattern) and pattern in text
    return bool(pattern) and f" {pattern} " in f" {text} "


@lru_cache(maxsize=512)
def _date_pattern(event: date, *, omit_year: bool = False) -> re.Pattern[str]:
    month = _MONTHS[event.month - 1]
    month_name = rf"(?:{month}|{month[:3]}{'t?' if event.month == 9 else ''}\.?)"
    day = rf"0?{event.day}(?:st|nd|rd|th)?"
    range_separator = r"\s*(?:[-–—]|\b(?:to|and)\b)\s*"
    any_day = r"(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?"
    range_end = rf"(?:{range_separator}{any_day})?"
    year = str(event.year)
    day_month = rf"{day}{range_end}\s+{month_name}"
    month_day = rf"{month_name}\s+(?:{day}{range_end}|{any_day}{range_separator}{day})"
    if omit_year:
        # An explicit following year takes precedence over the named edition. Also prevent
        # backtracking into the first day of a range that has a following explicit year.
        return re.compile(
            rf"(?<!\w)(?:{day_month}|{month_day})"
            r"(?!\w|\s*,?\s*\d{4}\b|\s*(?:[-–—]|\b(?:to|and)\b)\s*\d{1,2}\b)",
            re.I,
        )
    return re.compile(
        rf"(?<!\d)(?:{year}-0?{event.month}-0?{event.day}|"
        rf"{day_month}\s*,?\s*{year}|{month_day}\s*,?\s*{year}|"
        rf"{year}年\s*0?{event.month}月\s*0?{event.day}日)(?!\d)",
        re.I,
    )


def _completed_on(passage: str, pattern: re.Pattern[str], *, named_year: int | None = None) -> bool:
    actions = list(_EVENT_VERB.finditer(passage))
    if not actions:
        return False
    for match in pattern.finditer(passage):
        before_date = passage[: match.start()].rstrip(" *_`")
        if _METADATA_DATE.search(before_date) or _NON_OCCURRENCE.search(before_date):
            continue
        action = min(
            actions,
            key=lambda verb: min(abs(verb.start() - match.end()), abs(verb.end() - match.start())),
        )
        subject = passage[: action.start()]
        if _NON_OCCURRENCE.search(subject):
            continue
        if named_year is not None:
            named = _NAMED_EVENT_YEAR.search(subject)
            if not named or (named[1] or named[2]) != str(named_year):
                continue
        return True
    return False


def _occurrence_on(passage: str, event_date: date) -> bool:
    return _completed_on(passage, _date_pattern(event_date)) or _completed_on(
        passage, _date_pattern(event_date, omit_year=True), named_year=event_date.year
    )


def _states_event_date(text: str, event: _EventEvidence) -> bool:
    return any(
        _occurrence_on(passage, event.event_date)
        and any(
            _contains_phrase(_words(passage), detail) for detail in (event.event_name, event.venue)
        )
        for passage in _PASSAGES.split(normalize("NFKC", text))
    )


def _confirmed_end_date(event: _EventEvidence, snippets: Sequence[str]) -> date:
    """Use a later date only when it appears in the same verified occurrence passage."""
    end = event.event_date
    for text in snippets:
        for passage in _PASSAGES.split(normalize("NFKC", text)):
            if not _states_event_date(passage, event):
                continue
            for offset in range(1, 8):
                possible_end = event.event_date + timedelta(days=offset)
                if _occurrence_on(passage, possible_end):
                    end = max(end, possible_end)
    return end


def completed_event_passage(text: str, today: date) -> tuple[date, str] | None:
    """Extract an in-window occurrence passage from a real result for the reporter."""
    periods = [today - timedelta(days=offset) for offset in range(121)]
    for passage in _PASSAGES.split(normalize("NFKC", text)):
        if not _EVENT_VERB.search(passage):
            continue
        for day in reversed(periods):
            if _occurrence_on(passage, day):
                return day, passage
    return None


def recorded_event_occurrence(
    source_ref: str,
    source_url: str,
    today: date,
    references: Mapping[str, tuple[str, str]],
) -> tuple[date, str] | None:
    """Recover an occurrence hint from a bound official result in this run."""
    source = references.get(source_ref)
    if source is None or source[0] != source_url or not official_event_source(source_url):
        return None
    return completed_event_passage(source[1], today)


def _load_event(
    root: Path,
    observations: Mapping[str, Sequence[str]],
    today: date,
    references: Mapping[str, tuple[str, str]] | None,
) -> tuple[_EventEvidence | None, list[str]]:
    raw = _read_text(root, EVENT_ARTIFACT)
    if raw is None:
        return None, [f"Write an ordinary UTF-8 {EVENT_ARTIFACT} with the requested event fields."]
    try:
        payload = json.loads(raw)
    except ValueError:
        return None, [f"Write valid JSON in {EVENT_ARTIFACT}."]
    if not isinstance(payload, dict):
        return None, [f"Write one JSON object in {EVENT_ARTIFACT}."]

    allowed = _EventEvidence.model_fields
    extra = sorted(payload.keys() - allowed.keys())
    defects = [f"Remove unsupported evidence fields: {', '.join(extra)}."] if extra else []
    try:
        event = _EventEvidence.model_validate_json(
            json.dumps({key: value for key, value in payload.items() if key in allowed})
        )
    except ValidationError as error:
        defects.extend(
            f"Event evidence field {'.'.join(map(str, item['loc'])) or 'root'}: {item['msg']}."
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        )
        date_invalid = any(item["loc"] == ("event_date",) for item in error.errors())
        venue_invalid = any(item["loc"] == ("venue",) for item in error.errors())
        ref, url = payload.get("source_ref"), payload.get("source_url")
        hint = None
        if (date_invalid or venue_invalid) and isinstance(ref, str) and isinstance(url, str):
            hint = recorded_event_occurrence(ref, url, today, references or {})
        if hint:
            hint_date, passage = hint
            defects.append(
                f"Recorded result {ref} ({url}) has a dated occurrence on {hint_date}: "
                f"{passage[:350]} Copy a venue only if this result identifies it."
            )
        elif date_invalid:
            lead_name = payload.get("event_name")
            if isinstance(lead_name, str) and lead_name.strip():
                defects.append(
                    f"Follow up on the lead {lead_name.strip()[:160]!r} by its exact name. "
                    "Search for a completed official HKUST recap with the occurrence date and "
                    "venue. If the event cannot be verified in range, choose another lead."
                )
        return None, defects

    if not 0 <= (today - event.event_date).days <= 120:
        earliest = today - timedelta(days=120)
        defects.append(
            f"The event date {event.event_date} is outside the last 120 days "
            f"({earliest} through {today}). Search for another completed event in that range."
        )
        for ref, (url, excerpt) in (references or {}).items():
            if official_event_source(url) and (hint := completed_event_passage(excerpt, today)):
                hint_date, passage = hint
                defects.append(
                    f"Recorded result {ref} ({url}) has a dated occurrence on {hint_date}: "
                    f"{passage[:350]} Use it only if it confirms the event and venue."
                )
                break
    if not official_event_source(event.source_url):
        defects.append(
            "Cite a specific official HKUST event or news page using its exact HTTPS URL."
        )

    quote = event.source_quote
    if event.source_ref is not None:
        source = (references or {}).get(event.source_ref)
        if source is None or source[0] != event.source_url:
            match = next(
                (ref for ref, (url, _) in (references or {}).items() if url == event.source_url),
                None,
            )
            hint = (
                f" This exact URL was returned as {match}."
                if match
                else f" {event.source_ref} points to {source[0]}."
                if source
                else ""
            )
            defects.append(f"Use the ID and exact URL from the same result in this task.{hint}")
            return None, defects
        matching = [source[1]]
    else:
        matching = [
            excerpt
            for excerpt in observations.get(event.source_url, ())
            if quote is not None and _quote(quote) in _quote(excerpt)
        ]
    if not matching:
        defects.append(
            "Use the exact URL and a verbatim excerpt from a successful newsroom search "
            "in this run."
        )
    elif (quote is not None and not _states_event_date(quote, event)) or not any(
        _contains_phrase(_words(excerpt), event.event_name)
        and _contains_phrase(_words(excerpt), event.venue)
        and _states_event_date(excerpt, event)
        for excerpt in matching
    ):
        defects.append(
            "Use evidence that explicitly states when the event occurred. The official "
            "result must identify the event and venue. Schedules and publication dates "
            f"do not count. Search for an official post-event recap of {event.event_name!r} "
            "if this lead is still in range."
        )
        if not any(_contains_phrase(_words(excerpt), event.venue) for excerpt in matching):
            defects.append(
                f"The venue {event.venue!r} does not appear in the selected result. "
                "Copy the venue exactly and remove unsupported location qualifiers."
            )
        if not any(_contains_phrase(_words(excerpt), event.event_name) for excerpt in matching):
            defects.append(
                f"The name {event.event_name!r} does not appear verbatim in the selected "
                "result. Use the short event name that it actually shows."
            )
        for excerpt in matching:
            if hint := completed_event_passage(excerpt, today):
                hint_date, passage = hint
                defects.append(f"Recorded occurrence on {hint_date}: {passage[:350]}")
                break
    if not defects and _confirmed_end_date(event, matching) > today:
        defects.append("The recorded event range ends after the evaluation date.")
    return (None if defects else event), defects


def check_event(
    root: Path,
    observations: Mapping[str, Sequence[str]],
    today: date,
    *,
    references: Mapping[str, tuple[str, str]] | None = None,
) -> list[str]:
    """Return reporter defects based only on the artifact and observed search excerpts."""
    _, defects = _load_event(root, observations, today, references)
    return defects


def _event_snippets(
    event: _EventEvidence,
    references: Mapping[str, tuple[str, str]] | None,
) -> tuple[str, ...]:
    if event.source_ref is not None and references is not None:
        return (references[event.source_ref][1],)
    return (event.source_quote,) if event.source_quote else ()


def verified_event_handoff(
    root: Path,
    observations: Mapping[str, Sequence[str]],
    today: date,
    references: Mapping[str, tuple[str, str]],
) -> dict[str, str] | None:
    """Give the editor the verifier's dates without changing the reporter's artifact bytes."""
    event, _ = _load_event(root, observations, today, references)
    if event is None:
        return None
    return {
        "event_name": event.event_name,
        "event_start": event.event_date.isoformat(),
        "event_end": _confirmed_end_date(event, _event_snippets(event, references)).isoformat(),
        "venue": event.venue,
        "source_url": event.source_url,
    }


def check_article(
    root: Path,
    reporter_root: Path,
    observations: Mapping[str, Sequence[str]],
    today: date,
    *,
    references: Mapping[str, tuple[str, str]] | None = None,
) -> list[str]:
    """Tie the editor's draft to the current good reporter artifact before manual review."""
    event, _ = _load_event(reporter_root, observations, today, references)
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
        defects.append(
            "Match the reporter's verified event name, venue, occurrence date and URL. "
            f"Use name {event.event_name!r}, venue {event.venue!r}, "
            f"occurrence date {event.event_date}, and {event.source_url}."
        )
    end_date = _confirmed_end_date(event, _event_snippets(event, references))
    if end_date != event.event_date and (
        not _date_pattern(end_date).search(article)
        or re.search(r"\b(?:single|one)[ -]day event\b|为期一天|為期一天", article, re.I)
    ):
        defects.append(
            f"The recorded occurrence covers {event.event_date} through {end_date}. "
            "Use both dates, remove any single-day description, and update every channel."
        )
    headings = _HEADING.findall(article)
    for channel, pattern in _CHANNELS.items():
        if not any(pattern.search(heading) for heading in headings):
            defects.append(f"Add a labelled Markdown section for the {channel} channel.")
    if _OUTREACH.search(article):
        defects.append(
            "Remove the unsupported claim that this newsroom contacted or interviewed anyone."
        )
    return defects
