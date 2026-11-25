"""Managed S1 acceptance using real bookings in a disposable offline container."""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from typing import BinaryIO, Literal
from urllib.parse import urlencode

BOOKING_ARTIFACT = "outputs/booking_app.py"
BOOKING_GUIDE_ARTIFACT = "outputs/usage.md"
DASHBOARD_ARTIFACT = "outputs/usage_dashboard.py"
DASHBOARD_GUIDE_ARTIFACT = "outputs/dashboard_usage.md"

BOOKING_GUIDANCE = (
    f"Deliver {BOOKING_ARTIFACT} and {BOOKING_GUIDE_ARTIFACT}. Write a short usable "
    f"{BOOKING_GUIDE_ARTIFACT} with file_write before the longer program write. "
    "Include the explicit Python launch command with --host, --port and --db, the browser URL, "
    "and numbered steps to check availability, book a room, and look up and cancel a booking. "
    "Describe actions without guessing labels or screen layout before you write the program. "
    f"Then use a separate file_write to save {BOOKING_ARTIFACT}. Save both before any optional "
    "smoke scripts, extra documentation, or final reply. In rework, keep passing files and "
    "write the missing or reported artifact first. "
    "The Python 3 booking app must start with --host, --port and --db, using only the standard "
    "library. At /, offer availability, booking and cancellation controls. Seed at least two "
    "rooms; GET /api/rooms returns a JSON list of objects with id and name. GET /api/availability "
    "takes date and returns {rooms: [{room_id, busy: [{start, end}]}]}. POST /api/bookings takes "
    "room_id, date, start, end, booker and title, returns 201 with an id, and rejects overlaps "
    "with 409. POST /api/bookings/<id>/cancel returns 200, frees the slot, and preserves the "
    "record. GET /api/bookings?date=... returns a JSON array of records with id and status "
    "(active/cancelled). "
    "Store the records in the named SQLite database and accept valid times throughout the day. "
    "Use YYYY-MM-DD dates and HH:MM times. Describe only controls users can see. If giving an "
    "exact button label, put it in backticks or bold and copy it from the program's HTML. Refer "
    "users to the live room list and avoid guessing room names or capacities. If you include "
    "HTTP examples, verify their response shapes against the actual app. Demonstrate the "
    "working browser controls using their actual labels. The verifier exercises the live HTTP API "
    "against a fresh database in Docker. The operator reviews the browser flow at acceptance."
)

DASHBOARD_GUIDANCE = (
    f"Deliver {DASHBOARD_ARTIFACT} and {DASHBOARD_GUIDE_ARTIFACT}. Start the dashboard with "
    "--host, --port and --db. "
    f"Write {DASHBOARD_ARTIFACT} first with file_write and run code_exec to self-test it; "
    f"then write {DASHBOARD_GUIDE_ARTIFACT} with the launch commands, separate ports, "
    "browser URL, date selection and what the summaries mean. Save both before extras. "
    "In rework, keep passing files and write the missing or reported artifact first. "
    "Use the standard library and the same SQLite database as the "
    "accepted inputs/dev/booking_app.py. Do not change the accepted app or replace its records "
    "with sample data. Inspect the prior app for its actual database schema. At / expose two "
    "date inputs and render charts and summaries using GET /api/dashboard with start/end dates. "
    "Use inclusive YYYY-MM-DD visit dates; reject reversed ranges with HTTP 400. Return range "
    "with start, end and days; totals with active_bookings, reserved_minutes, cancelled_bookings "
    "and cancellation_rate_pct; rooms with name, active, minutes and cancelled, including idle "
    "rooms; busiest_days with date and active. Count cancellations on the scheduled visit date "
    "and exclude them from reserved minutes. Return bands with width_hours=2 and "
    "by_covered_minutes containing all 12 {band, value} pairs across the day (e.g. 08:00-10:00; "
    "value is occupied minutes). Split minutes by overlap when a booking crosses a boundary. "
    "Show the busiest dates and time bands by those measurements. Re-read records on refresh "
    "and leave the database unchanged. In the guide show both Python launch commands with the "
    "same --db argument, state that the chart reports occupied minutes in two-hour bands, "
    "and explain date selection and cancellations. Keep the guide, code and UI consistent. "
    "Use actual CLI options. Keep button instructions action-based unless you quote the exact "
    "label from the HTML. Describe time bands as ranked only if the chart "
    "renders the occupied-minute peaks first; otherwise say the bands are in clock order. "
    "The verifier will check real booking records and HTTP results; the operator reviews the "
    "browser flow at final acceptance."
)

_BOOKING_PORT = 8020
_DASHBOARD_PORT = 8120
_MAX_BYTES = 512 * 1024


@dataclass
class SoftwareCheck:
    status: Literal["passed", "failed", "unverified"]
    defects: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _artifact(path: Path, root: Path) -> Path:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Deliver {path.name} as an ordinary file in its workspace.")
    if not 0 < path.stat().st_size <= _MAX_BYTES:
        raise ValueError(f"Deliver a nonempty {path.name} of at most {_MAX_BYTES // 1024} KiB.")
    return path.resolve()


def _guide_db(guide: str, script: str) -> str | None:
    command = re.search(
        rf"(?im)^\s*(?:python[\d.]*|py)(?:\.exe)?\s+[^\n]*?\b{re.escape(script)}[\"']?"
        r"[^\n]*?\s--db(?:\s+|=)(?:\"([^\"]+)\"|'([^']+)'|([^\s`]+))",
        guide,
    )
    return next((part for part in command.groups() if part), None) if command else None


def _guide_defects(guide: str, *, dashboard: bool) -> list[str]:
    guide_name = Path(DASHBOARD_GUIDE_ARTIFACT if dashboard else BOOKING_GUIDE_ARTIFACT).name
    booking_db = _guide_db(guide, "booking_app.py")
    lowered = guide.casefold().replace("–", "-").replace("‑", "-")
    defects = []
    if not dashboard:
        if not booking_db:
            defects.append("In usage.md, give a Python booking_app.py launch command with --db.")
        if not all(text in lowered for text in ("availab", "book", "cancel")):
            defects.append("In usage.md, explain checking availability, booking and cancelling.")
        booking_window = re.search(
            r"\b(?:bookable window|booking hours?)\b[^\n\d]*(\d\d:\d\d)"
            r"\s*(?:-|to)\s*(\d\d:\d\d)",
            lowered,
        )
        if booking_window and booking_window.groups() != ("00:00", "23:59"):
            defects.append("In usage.md, document the supported all-day HH:MM booking times.")
        return defects
    dashboard_db = _guide_db(guide, "usage_dashboard.py")
    describes_shared_database = "booking_app.py" in lowered and bool(
        re.search(r"\bsame\s+\*{0,2}`?(?:--db|sqlite|database)", lowered)
    )
    if (
        not dashboard_db
        or (booking_db is not None and dashboard_db != booking_db)
        or (booking_db is None and not describes_shared_database)
    ):
        defects.append(f"In {guide_name}, show both Python commands with the same --db value.")
    two_hour_bands = r"\b(?:(?:two|2)[ -]hour|120[ -]minute)\s+(?:time\s+)?bands?\b"
    covered_minutes = r"\b(?:covered|occupied|reserved|overlap(?:ping)?) minutes\b"
    phrases = " ".join(lowered.split())
    if not re.search(two_hour_bands, phrases) or not re.search(covered_minutes, phrases):
        defects.append(f"In {guide_name}, explain two-hour bands and covered minutes.")
    wrong_bands = r"\b24(?:\s+bands?|[ -](?:(?:one|1)[ -])?hours?\s+bands?)\b"
    if re.search(wrong_bands + r"|\b(?:one|1)[ -]hour bands?\b", lowered):
        defects.append(f"In {guide_name}, describe 12 two-hour bands throughout.")
    if not all(text in lowered for text in ("date", "cancel")):
        defects.append(f"In {guide_name}, explain date selection and cancellation counts.")
    return defects


def _literal_cli(nodes: Iterable[ast.AST]) -> tuple[set[str], int | None]:
    options, port = set(), None
    for node in nodes:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        flags = {
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        }
        options.update(flags)
        if "--port" in flags:
            port = next(
                (
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant)
                ),
                None,
            )
    return options, port if type(port) is int else None


def _guide_port(guide: str, script: str, source: str) -> int | None:
    launch = re.search(
        rf"(?im)^\s*(?:python[\d.]*|py)(?:\.exe)?\s+[^\n]*?\b{re.escape(script)}[\"']?"
        r"([^\n]*)",
        guide,
    )
    if launch is None:
        return None
    port = re.search(r"(?<!\S)--port(?:\s+|=)[\"']?(\d{1,5})\b", launch[1])
    if port is not None:
        return int(port[1])
    try:
        return _literal_cli(ast.walk(ast.parse(source)))[1]
    except SyntaxError:
        return None


def _dashboard_cli_defects(guide: str, source: str) -> list[str]:
    """Cross-check literal argparse declarations against explicit statements in the guide."""
    try:
        nodes = list(ast.walk(ast.parse(source)))
    except SyntaxError:
        return ["Fix the Python syntax in usage_dashboard.py so the dashboard can start."]
    options, port = _literal_cli(nodes)
    if not {"--host", "--port", "--db"} <= options:
        return []  # A different argument parser needs runtime verification.
    defects = []
    documented_commands = re.findall(
        r"(?im)^\s*(?:python[\d.]*|py)(?:\.exe)?\s+[^\n]*?\busage_dashboard\.py[\"']?([^\n]*)",
        guide,
    )
    documented_options = {
        flag
        for command in documented_commands
        for flag in re.findall(r"(?<!\S)--[a-z][a-z0-9-]*\b", command.partition(" #")[0])
    }
    program_literals = {
        node.value
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for flag in sorted(documented_options - options - program_literals):
        defects.append(
            f"In dashboard_usage.md, remove the {flag} example or implement that option."
        )
    matches = re.findall(
        r"\b(?:dashboard(?:'s)?\s+default\s+port|default\s+port\s+for\s+(?:the\s+)?dashboard)"
        r"\b[^\n\d]{0,40}(\d{2,5})\b",
        guide,
        re.IGNORECASE,
    )
    matches += re.findall(r"(?im)^\s*[-*]\s+`--port`[^\n]*?\bdefault\s*[` ]*(\d{2,5})\b", guide)
    if type(port) is int and any(int(value) != port for value in matches):
        defects.append(f"In dashboard_usage.md, state the dashboard's actual default port {port}.")
    return defects


def _documented_buttons(guide: str, *, descriptions: bool = False) -> tuple[str, ...]:
    label = r"(?:\*\*([^*\n]{1,40})\*\*|`([^`\n]{1,40})`)"
    actions = r"\b(?:press(?:ing)?|click(?:ing)?|tap(?:ping)?)\s+(?:the\s+)?" + label
    matches = re.findall(actions, guide, re.IGNORECASE)
    if descriptions:
        matches += re.findall(label + r"\s+button\b", guide, re.IGNORECASE)
    names = ((left or right).strip().strip("`").strip() for left, right in matches)
    return tuple(dict.fromkeys(name for name in names if name))


def _documents_wrapped_history(guide: str) -> bool:
    """Find an explicit wrapped response example for the booking-history GET."""
    lines = guide.splitlines()
    wrapper = re.compile(r"""\{\s*["']?bookings["']?\s*:\s*\[""", re.I)
    for index, line in enumerate(lines):
        route = re.search(r"/api/bookings\?[^\s`<>\"'|]*\bdate=", line, re.I)
        if route is None:
            continue
        before, after = line[: route.start()], line[route.end() :]
        negated = re.search(r"\b(?:does not|doesn't|never)\s+returns?\b", after, re.I)
        if (
            wrapper.search(after)
            and not negated
            and (
                re.match(r"^\s*\|\s*`?GET\b", before, re.I)
                or re.search(r"\b(?:returns?|responds? with)\s+`?\{", after, re.I)
            )
        ):
            return True
        if re.search(r"\bcurl(?:\.exe)?\b", before, re.I) and index + 1 < len(lines):
            reply = re.match(r"^\s*#\s*(?:->|=>)\s*(.*)$", lines[index + 1])
            if reply and wrapper.search(reply[1]):
                return True
    return False


class _ButtonNames(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.names: list[str] = []
        self._label: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        label = attributes.get("aria-label") or ""
        if tag == "button":
            self._label = ""
            if label:
                self.names.append(label)
        if tag == "input" and attributes.get("type") in {"button", "submit"}:
            self.names.append(label or attributes.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._label is not None:
            self.names.append(" ".join(self._label.split()))
            self._label = None

    def handle_data(self, data: str) -> None:
        if self._label is not None:
            self._label += data


def _button_defects(page: str, buttons: tuple[str, ...], guide: str) -> list[str]:
    parser = _ButtonNames()
    parser.feed(page)
    # Include straightforward buttons created at runtime with literal labels. Do not run JS.
    created = re.findall(
        r"""\b([\w$]+)\s*=\s*document\.createElement\(\s*["']button["']\s*\)""", page
    )
    for variable in dict.fromkeys(created):
        assignments = re.findall(
            rf"""(?<![\w$]){re.escape(variable)}\.(?:textContent|innerText)"""
            r"""\s*=\s*(["'])([^"'<\n]{1,80})\1""",
            page,
        )
        parser.names.extend(label for _, label in assignments)
    parser.names.extend(re.findall(r"<button\b[^>]*>([^<>{}\n]{1,80})</button>", page, re.I))
    names = {" ".join(label.casefold().split()): label.strip() for label in parser.names if label}
    defects = []
    for label in buttons:
        if names and " ".join(label.casefold().split()) not in names:
            examples = ", ".join(name[:60] for name in list(names.values())[:5])
            defects.append(
                f"In {guide}, use a button shown on the page: {examples}. "
                f"The documented button '{label}' is absent. Use the actual label or "
                "describe the action without inventing a label. Check the surrounding "
                "steps against the same page."
            )
    return defects


def _dashboard_presentation_defects(
    page: str,
    wide: dict,
    buttons: tuple[str, ...] = (),
    *,
    ranked: bool = False,
    clock_order: bool = False,
    clock_start: str | None = None,
) -> list[str]:
    defects = _button_defects(page, buttons, "dashboard_usage.md")
    if clock_order and re.search(r"\bbands are ranked busiest first\b", page, re.I):
        defects.append(
            "In dashboard_usage.md, remove the clock-order claim. The chart ranks bands "
            "busiest first."
        )
    # If the browser renders the API's order directly, its ranking claim is verifiable by HTTP.
    direct_order = re.search(r"\.bands\s*\.\s*by_covered_minutes\s*\.map\s*\(", page)
    chart_claims_rank = re.search(
        r"\branked by (?:occupied|covered) minutes\b", page, re.IGNORECASE
    )
    explicitly_chronological = re.search(
        r"\btime bands\s*\([^<\n]*\b(?:clock|chronological) order\b", page, re.IGNORECASE
    )
    if ranked and explicitly_chronological:
        defects.append(
            "In dashboard_usage.md, describe the time bands in clock order as shown in the chart."
        )
    elif (
        direct_order
        and not re.search(r"\.(?:toSorted|sort)\s*\(", page)
        and (ranked or chart_claims_rank)
    ):
        values = [row["value"] for row in wide["bands"]["by_covered_minutes"]]
        if values != sorted(values, reverse=True):
            defects.append(
                "The time chart claims to be ranked by occupied minutes but renders clock order. "
                "Sort the displayed bands by covered minutes or label both the chart and guide as "
                "chronological. Keep chart and guide aligned."
            )
    if clock_start is not None and _draws_bands_in_response_order(page):
        first = wide["bands"]["by_covered_minutes"][0]["band"][:5]
        if first != clock_start:
            defects.append(
                f"In dashboard_usage.md, the time chart starts at {first}, not {clock_start}. "
                "Correct the starting time or omit it."
            )
    return defects


def _draws_bands_in_response_order(page: str) -> bool:
    """Recognize direct rendering and defer when a JS reorder cannot be ruled out."""
    direct = r"\.bands\s*\.\s*by_covered_minutes\s*\.\s*"
    # A forEach loop drawing band labels is direct rendering, unlike a loop for the maximum.
    draws_labels = re.search(
        direct + r"(?:map|forEach)\s*\([^;\n]{0,240}\.\s*band\b",
        page,
    )
    reordered = (
        re.search(
            r"\.(?:toSorted|sort|reverse|toReversed|splice|toSpliced|shift|unshift)\s*\(",
            page,
        )
        or re.search(r"\.slice\s*\(", page)
        and re.search(r"\.concat\s*\(", page)
    )
    return bool(draws_labels and not reordered)


def _documented_clock_start(guide: str) -> str | None:
    match = re.search(
        r"\b(?:clock|chronological)\s+order[\s*`(,]+starting\s+at[\s*`]+"
        r"((?:[01]\d|2[0-3]):[0-5]\d)\b",
        guide,
        re.I,
    )
    return match[1] if match else None


def check_booking(root: Path) -> SoftwareCheck:
    """Check a booking candidate and its guide without executing it on the host."""
    return _check(root, None, dashboard=False)


def check_dashboard(root: Path, booking_path: Path) -> SoftwareCheck:
    """Check dashboard figures against bookings made by the exact prior accepted app."""
    return _check(root, booking_path, dashboard=True)


def _check(root: Path, booking_path: Path | None, *, dashboard: bool) -> SoftwareCheck:
    script, guide = (
        (DASHBOARD_ARTIFACT, DASHBOARD_GUIDE_ARTIFACT)
        if dashboard
        else (BOOKING_ARTIFACT, BOOKING_GUIDE_ARTIFACT)
    )
    try:
        script_path = _artifact(root / script, root)
        guide_text = _artifact(root / guide, root).read_text(encoding="utf-8-sig")
        source = script_path.read_text(encoding="utf-8-sig") if dashboard else ""
    except UnicodeError:
        return SoftwareCheck("failed", [f"Save {script} and {guide} in UTF-8."])
    except (OSError, ValueError) as error:
        return SoftwareCheck("failed", [str(error)])

    defects = _guide_defects(guide_text, dashboard=dashboard)
    if booking_path is not None:
        try:
            booking = _artifact(booking_path, booking_path.parent)
            booking_source = booking.read_text(encoding="utf-8-sig")
        except (OSError, ValueError):
            return SoftwareCheck("unverified", ["The accepted Task 1 booking app is unavailable."])
        mounts = [(booking, "booking.py"), (script_path, "dashboard.py")]
        defects.extend(_dashboard_cli_defects(guide_text, source))
        booking_port = _guide_port(guide_text, "booking_app.py", booking_source)
        if booking_port is not None and booking_port == _guide_port(
            guide_text, "usage_dashboard.py", source
        ):
            defects.append(
                f"In dashboard_usage.md, both launch commands use port {booking_port}. "
                "Give them different ports and update the dashboard URL and curl example."
            )
        ranked = (
            re.search(
                r"\b(?:ranks?\s+(?:the\s+)?(?:time\s+)?bands\s+by|"
                r"time\s+bands\b[^\n]*(?:\n[ \t]+[^\n]*){0,2}\branked\s+by)"
                r"\s+(?:occupied|covered)\s+minutes\b",
                guide_text.casefold(),
            )
            is not None
        )
        runtime = _run_fixture(
            "dashboard",
            mounts,
            buttons=_documented_buttons(guide_text, descriptions=True),
            ranked=ranked,
            clock_order=bool(re.search(r"\b(?:clock|chronological) order\b", guide_text, re.I)),
            clock_start=_documented_clock_start(guide_text),
        )
    else:
        runtime = _run_fixture(
            "booking",
            [(script_path, "booking.py")],
            buttons=_documented_buttons(guide_text, descriptions=True),
            wrapped_history=_documents_wrapped_history(guide_text),
        )
    if runtime.status == "unverified":
        return SoftwareCheck("unverified", [*runtime.defects, *defects], runtime.evidence)
    return SoftwareCheck(
        "failed" if defects or runtime.defects else "passed",
        [*runtime.defects, *defects],
        runtime.evidence,
    )


def _run_fixture(
    mode: str,
    candidates: list[tuple[Path, str]],
    *,
    buttons: tuple[str, ...] = (),
    ranked: bool = False,
    clock_order: bool = False,
    clock_start: str | None = None,
    wrapped_history: bool = False,
) -> SoftwareCheck:
    from tools.code_exec import DEFAULT_IMAGE, IMAGE_ENV

    image = os.environ.get(IMAGE_ENV, "").strip() or DEFAULT_IMAGE
    name = f"agentverse-s1-{uuid.uuid4().hex}"
    fixture = (Path(__file__).resolve(), "verifier.py")
    mounts = [
        item
        for path, target in [fixture, *candidates]
        for item in ("--mount", f"type=bind,src={path.as_posix()},dst=/fixture/{target},readonly")
    ]
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "192m",
        "--cpus",
        "1",
        "--user",
        "65532:65532",
        *mounts,
        "--entrypoint",
        "python",
        image,
        "-I",
        "-B",
        "/fixture/verifier.py",
        mode,
    ]
    claims = {}
    if buttons:
        claims["buttons"] = buttons
    if ranked:
        claims["ranked"] = ranked
    if clock_order:
        claims["clock_order"] = clock_order
    if clock_start is not None:
        claims["clock_start"] = clock_start
    if wrapped_history:
        claims["wrapped_history"] = wrapped_history
    if claims:
        command.append(json.dumps(claims))
    try:
        result = subprocess.run(
            command, capture_output=True, encoding="utf-8", errors="replace", timeout=30
        )
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        return SoftwareCheck("unverified", ["The isolated S1 verifier exceeded its time limit."])
    except OSError:
        return SoftwareCheck("unverified", ["Docker could not start the isolated S1 verifier."])
    if result.returncode:
        return SoftwareCheck(
            "unverified", [f"The isolated S1 verifier did not run. Check Docker and image {image}."]
        )
    try:
        record = json.loads(result.stdout)
        defects, evidence = record["defects"], record["evidence"]
        if not all(
            isinstance(items, list) and all(isinstance(item, str) for item in items)
            for items in (defects, evidence)
        ) or not (defects or evidence):
            raise ValueError("empty or invalid record")
    except (ValueError, KeyError, TypeError):
        return SoftwareCheck("unverified", ["The isolated S1 verifier produced no valid result."])
    return SoftwareCheck("failed" if defects else "passed", defects, evidence)


def _http(port: int, path: str, data: dict | None = None) -> tuple[int, str]:
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(
            "GET" if data is None else "POST",
            path,
            None if data is None else json.dumps(data),
            {"Content-Type": "application/json"},
        )
        result = connection.getresponse()
        return result.status, result.read(_MAX_BYTES).decode("utf-8", errors="replace")
    finally:
        connection.close()


class _CheckFailed(ValueError):
    """An actionable check failure produced inside Docker."""


class _RequestFailed(_CheckFailed):
    """An application transport or server failure that may have a traceback."""


def _expect(condition: bool, defect: str) -> None:
    if not condition:
        raise _CheckFailed(defect)


def _json(port: int, path: str, status: int = 200, data: dict | None = None) -> object:
    route = path.partition("?")[0]
    verb = "GET" if data is None else "POST"
    try:
        code, body = _http(port, path, data)
    except (OSError, HTTPException) as error:
        raise _RequestFailed(
            f"{verb} {route} did not return an HTTP response ({type(error).__name__})."
        ) from error
    if code >= 500 and code != status:
        raise _RequestFailed(f"{verb} {route} must return HTTP {status}; received {code}.")
    _expect(code == status, f"{verb} {route} must return HTTP {status}; received {code}.")
    try:
        return json.loads(body)
    except ValueError as error:
        raise _CheckFailed(f"{verb} {route} must return a JSON response.") from error


def _last_app_exception(stderr: BinaryIO) -> str | None:
    """Return a bounded exception line from candidate stderr without forwarding its traceback."""
    end = stderr.seek(0, os.SEEK_END)
    stderr.seek(max(0, end - 8192))
    tail = stderr.read(8192).decode("utf-8", errors="replace")
    _, marker, traceback = tail.rpartition("Traceback (most recent call last):")
    if not marker:
        return None
    for line in reversed(traceback.splitlines()):
        if re.match(r"^(?:\w+\.)*[A-Z]\w*(?:Error|Exception): ", line):
            return "".join(character for character in line if character.isprintable())[:200]
    return None


@contextmanager
def _serve(script: str, port: int, database: Path) -> Iterator[None]:
    delivered_name = "booking_app.py" if script == "booking" else "usage_dashboard.py"
    with tempfile.TemporaryFile(dir=database.parent) as stderr:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                f"/fixture/{script}.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--db",
                str(database),
            ],
            cwd=database.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
        try:
            deadline = time.monotonic() + 4
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    code, _ = _http(port, "/")
                    if code == 200:
                        break
                except (OSError, HTTPException):
                    pass
                time.sleep(0.05)
            else:
                raise _RequestFailed(
                    f"{delivered_name} did not serve / on the requested --host, --port and --db."
                )
            yield
        except _RequestFailed as error:
            if exception := _last_app_exception(stderr):
                raise _CheckFailed(f"{error} {delivered_name} reported {exception}") from error
            raise _CheckFailed(str(error)) from error
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def _rooms() -> list[dict]:
    rooms = _json(_BOOKING_PORT, "/api/rooms")
    _expect(
        isinstance(rooms, list)
        and len(rooms) >= 2
        and all(
            isinstance(room, dict)
            and type(room.get("id")) in (int, str)
            and isinstance(room.get("name"), str)
            and room["name"].strip()
            for room in rooms
        )
        and len({room["id"] for room in rooms}) == len(rooms)
        and len({room["name"] for room in rooms}) == len(rooms),
        "GET /api/rooms must include at least two rooms with distinct ids and names.",
    )
    return rooms


def _book(room: dict, day: date, start: str, end: str, status: int = 201) -> object:
    result = _json(
        _BOOKING_PORT,
        "/api/bookings",
        status,
        {
            "room_id": room["id"],
            "date": day.isoformat(),
            "start": start,
            "end": end,
            "booker": "Independent review",
            "title": "Room use fixture",
        },
    )
    if status == 201:
        _expect(isinstance(result, dict) and "id" in result, "Return an id for the new booking.")
        return result["id"]
    return None


def _cancel(booking_id: object) -> None:
    _json(_BOOKING_PORT, f"/api/bookings/{booking_id}/cancel", data={})


def _busy(room: dict, day: date) -> set[tuple[str, str]]:
    availability = _json(_BOOKING_PORT, f"/api/availability?date={day.isoformat()}")
    try:
        for item in availability["rooms"]:
            if item["room_id"] == room["id"]:
                return {(slot["start"], slot["end"]) for slot in item["busy"]}
    except (KeyError, TypeError):
        pass
    raise _CheckFailed("Availability must list each room_id with a busy array of start/end slots.")


def _booking_flow(
    database: Path,
    defects: list[str],
    evidence: list[str],
    *,
    buttons: tuple[str, ...] = (),
    wrapped_history: bool = False,
) -> None:
    day = date.today() + timedelta(days=4)
    with _serve("booking", _BOOKING_PORT, database):
        page = _http(_BOOKING_PORT, "/")[1]
        _expect(
            all(word in page.casefold() for word in ("availab", "book", "cancel")),
            "Serve a booking page with availability, booking, and cancellation controls.",
        )
        defects.extend(_button_defects(page, buttons, "usage.md"))
        room = _rooms()[0]
        _expect(not _busy(room, day), "A room without bookings should have no busy slots.")
        booked = _book(room, day, "09:00", "10:00")
        _expect(("09:00", "10:00") in _busy(room, day), "Availability must show the booking.")
        _book(room, day, "09:30", "10:30", status=409)
        _cancel(booked)
        _expect(not _busy(room, day), "Cancelled bookings must free the room on availability.")
        replacement = _book(room, day, "09:00", "10:00")
        _book(room, day, "06:00", "07:00")
        _book(room, day, "22:30", "23:30")
    with _serve("booking", _BOOKING_PORT, database):
        rows = _json(_BOOKING_PORT, f"/api/bookings?date={day.isoformat()}")
        _expect(
            isinstance(rows, list)
            and all(isinstance(row, dict) and "id" in row and "status" in row for row in rows),
            "GET /api/bookings?date=... must return a JSON array of records with id and status.",
        )
        history = {row["id"]: row["status"] for row in rows}
        _expect(
            history.get(booked) == "cancelled" and history.get(replacement) == "active",
            "Persist the cancelled original and active replacement across an app restart.",
        )
    if wrapped_history:
        defects.append(
            "In usage.md, show GET /api/bookings?date=... as a top-level JSON array of "
            'records, like [{"id": 1, "status": "active"}], or omit that API example.'
        )
    evidence.append(
        "Docker HTTP verified availability, booking, overlap rejection, cancellation, "
        "slot reuse and persistent history."
    )


def _query_dashboard(start: date, end: date, status: int = 200) -> object:
    query = urlencode({"start": start.isoformat(), "end": end.isoformat()})
    return _json(_DASHBOARD_PORT, f"/api/dashboard?{query}", status)


def _dashboard_object(value: object, location: str, keys: tuple[str, ...]) -> dict:
    _expect(isinstance(value, dict), f"GET /api/dashboard {location} must be a JSON object.")
    missing = [key for key in keys if key not in value]
    if missing:
        raise _CheckFailed(f"GET /api/dashboard {location} is missing {', '.join(missing)}.")
    return value


def _totals(report: dict) -> tuple[int, int, int, float]:
    keys = ("active_bookings", "reserved_minutes", "cancelled_bookings", "cancellation_rate_pct")
    root = _dashboard_object(report, "response", ("totals",))
    total = _dashboard_object(root["totals"], "totals", keys)
    active, reserved, cancelled, rate = (total[key] for key in keys)
    return active, reserved, cancelled, rate


def _bands(report: dict) -> dict[str, int]:
    root = _dashboard_object(report, "response", ("bands",))
    bands = _dashboard_object(root["bands"], "bands", ("width_hours", "by_covered_minutes"))
    _expect(bands["width_hours"] == 2, "Return bands.width_hours=2 for the dashboard's time chart.")
    rows = bands["by_covered_minutes"]
    _expect(isinstance(rows, list), "Return bands.by_covered_minutes as a JSON array.")
    values = {}
    for row in rows:
        item = _dashboard_object(row, "bands.by_covered_minutes item", ("band", "value"))
        _expect(isinstance(item["band"], str), "Return each time-band label as a string.")
        label = item["band"].replace("–", "-")
        if label == "22:00-00:00":
            label = "22:00-24:00"
        values[label] = item["value"]
    expected = {f"{hour:02}:00-{hour + 2:02}:00" for hour in range(0, 24, 2)}
    _expect(
        len(rows) == 12 and values.keys() == expected,
        "Cover the full day with 12 distinct two-hour bands, including 22:00–24:00.",
    )
    return values


def _dashboard_results(
    first: date, second: date, outside: date, rooms: list[dict]
) -> tuple[str, dict]:
    page = _http(_DASHBOARD_PORT, "/")[1]
    _expect(
        len(re.findall(r"<input\b[^>]*\btype\s*=\s*['\"]?date\b", page, re.IGNORECASE)) >= 2,
        "The dashboard page needs a start-date and an end-date input.",
    )
    keys = ("range", "totals", "rooms", "busiest_days", "bands")
    wide = _dashboard_object(_query_dashboard(first, second), "response", keys)
    interval = _dashboard_object(wide["range"], "range", ("start", "end", "days"))
    _expect(
        (interval["start"], interval["end"], interval["days"])
        == (first.isoformat(), second.isoformat(), 2),
        "Return both inclusive date boundaries and the matching number of days.",
    )
    _expect(
        _totals(wide) == (3, 210, 1, 25),
        "The two-day range must report 3 active, 210 reserved minutes and 1 of 4 cancelled.",
    )
    per_room = {
        item["name"]: (item["active"], item["minutes"], item["cancelled"]) for item in wide["rooms"]
    }
    expected = {room["name"]: (0, 0, 0) for room in rooms}
    expected[rooms[0]["name"]] = (3, 210, 1)
    _expect(per_room == expected, "Room metrics must include the active room and all idle rooms.")
    days = {entry["date"]: entry["active"] for entry in wide["busiest_days"]}
    _expect(
        days == {first.isoformat(): 2, second.isoformat(): 1},
        "The first day has two active starts; the second has one. Exclude cancellations.",
    )
    bands = _bands(wide)
    _expect(
        (bands["08:00-10:00"], bands["10:00-12:00"], bands["14:00-16:00"], sum(bands.values()))
        == (105, 15, 90, 210)
        and max(bands, key=bands.get) == "08:00-10:00",
        "Split 09:45–10:15 into 15 minutes in each band; peak 08:00–10:00 is 105 minutes.",
    )
    narrow = _query_dashboard(second, second)
    _expect(
        _totals(narrow) == (1, 90, 1, 50),
        "Changing to the second date alone must give 1 active, 90 minutes and 50% cancelled.",
    )
    early_late = _bands(_query_dashboard(outside, outside))
    _expect(
        early_late["06:00-08:00"] == 90 and early_late["22:00-24:00"] == 60,
        "Keep the 90 early and 60 late reserved minutes accepted by the original booking app.",
    )
    _query_dashboard(second, first, 400)
    return page, wide


def _database_contents(database: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def _dashboard_flow(database: Path, defects: list[str], evidence: list[str], **claims) -> None:
    first = date.today() + timedelta(days=4)
    second, outside = first + timedelta(days=1), first + timedelta(days=2)
    with _serve("booking", _BOOKING_PORT, database):
        rooms = _rooms()
        room = rooms[0]
        _book(room, first, "08:00", "09:30")
        _book(room, first, "09:45", "10:15")
        _book(room, second, "14:00", "15:30")
        _cancel(_book(room, second, "05:00", "06:00"))
        _book(room, outside, "06:00", "07:30")
        _book(room, outside, "22:30", "23:30")
        before = _database_contents(database)
        with _serve("dashboard", _DASHBOARD_PORT, database):
            page, wide = _dashboard_results(first, second, outside, rooms)
            defects.extend(_dashboard_presentation_defects(page, wide, **claims))
            _expect(
                _database_contents(database) == before,
                "Reading the dashboard must leave the original booking database unchanged.",
            )
            _book(room, second, "16:00", "17:00")
            active, minutes, cancelled, rate = _totals(_query_dashboard(second, second))
            _expect(
                (active, minutes, cancelled) == (2, 150, 1) and abs(rate - 100 / 3) < 0.1,
                "Refresh must reflect new bookings from the original booking app.",
            )
    evidence.extend(
        [
            "Docker HTTP reconciled real Task 1 bookings, cancellations and two-hour bands.",
            "The dashboard preserved the records and refreshed after a new Task 1 booking.",
        ]
    )


def _inside_container(mode: str, claims: dict) -> None:
    defects: list[str] = []
    evidence: list[str] = []
    with tempfile.TemporaryDirectory(prefix="s1-") as directory:
        database = Path(directory) / "bookings.db"
        try:
            if mode == "booking":
                _booking_flow(database, defects, evidence, **claims)
            else:
                _dashboard_flow(database, defects, evidence, **claims)
        except _CheckFailed as error:
            defects.append(str(error))
        except (AttributeError, KeyError, TypeError, ValueError, OSError, HTTPException):
            defects.append("A required HTTP response did not match the documented JSON shape.")
    print(json.dumps({"defects": defects, "evidence": evidence}))


if __name__ == "__main__":
    _inside_container(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
