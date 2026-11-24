"""Deterministic allocation and stress checks for the simulated Mid Test proposal."""

import json
import re
import unicodedata
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from harness.search_evidence import visible_markdown_lines
from protocol.models import Contract

ALLOCATION_ARTIFACT = "outputs/allocation.json"
PROPOSAL_ARTIFACT = "outputs/investment-proposal.md"
SUMMARY_PLACEHOLDER = "<!-- generated-allocation-summary -->"

_START = "<!-- allocation-summary:start -->"
_END = "<!-- allocation-summary:end -->"
_CLASSES = {
    "us_equity": "US equity",
    "other_equity": "Other equity",
    "fixed_income": "Fixed income",
    "cash": "Cash",
}
_BUDGET = 1_000_000
_FULL_WEIGHT_BPS = 10_000
_MAX_ARTIFACT_BYTES = 256 * 1024

PORTFOLIO_GUIDANCE = (
    f"First write the allocation to {ALLOCATION_ARTIFACT}, then the simulated proposal to "
    f"{PROPOSAL_ARTIFACT}. The allocation JSON has exactly positions and stock_decline_bps. "
    "Each position has exactly asset (distinct name), asset_class (us_equity, other_equity, "
    "fixed_income or cash), weight_bps (positive integer basis points) and amount_hkd "
    "(positive integer HKD). The weights total 10000 bps and the amounts total HKD 1,000,000; "
    "each amount_hkd is weight_bps × 100. Declare one hypothetical stock_decline_bps "
    "as a positive integer up to 10000. The stress applies the same decline to both equity "
    "classes. Fixed income, cash and exchange rates are held unchanged. "
    f"Put {SUMMARY_PLACEHOLDER} alone on an unindented Markdown line where the allocation "
    "summary belongs. The managed writer expands it from the saved ledger, including class "
    "totals and stress results. If the ledger changes, use the placeholder again when writing "
    "the revised proposal. "
    "Explain assumptions, rationale and risks in prose without restating allocation numbers."
)

_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_SUM = re.compile(
    rf"(?<![\w+.,-])((?:{_NUMBER}%?\s*\+\s*)+{_NUMBER}%?)\s*=\s*({_NUMBER}%?)"
    r"(?![\w+,-]|\.[0-9])"
)
_RESERVE_BASELINE = re.compile(
    r"\b(?:(?:bonds?|fixed income)\s*(?:\+|and)\s*cash|total reserve)"
    r"\s+from\s+([0-9]+(?:\.[0-9]+)?)\s*%",
    re.IGNORECASE,
)
# Match explicit exposure at the start of a holding name. Domicile and ticker
# symbols alone say nothing about where a fund invests.
_US_STOCK_NAME = re.compile(
    r"^(?:us|u\.s\.?|united states)\s+(?:equity|equities|stocks?|"
    r"(?:small|mid|large)[ -]cap|total[ -]market)\b"
)
_AMBIGUOUS_REGION = re.compile(
    r"\b(?:international|global|world(?:wide)?|ex[ -]us|non[ -]us|listed|domiciled)\b"
)


class _Position(Contract):
    asset: str = Field(strict=True, min_length=1, max_length=200)
    asset_class: Literal["us_equity", "other_equity", "fixed_income", "cash"]
    weight_bps: int = Field(strict=True, ge=1, le=_FULL_WEIGHT_BPS)
    amount_hkd: int = Field(strict=True, ge=1, le=_BUDGET)


class _Allocation(Contract):
    positions: list[_Position] = Field(min_length=1, max_length=64)
    stock_decline_bps: int = Field(strict=True, ge=1, le=_FULL_WEIGHT_BPS)


def _read_text(root: Path, relative: str) -> str:
    target = root / relative
    try:
        if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Deliver {relative} as an ordinary file inside the workspace.")
        if target.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise ValueError(f"Keep {relative} within {_MAX_ARTIFACT_BYTES // 1024} KiB.")
        return target.read_text(encoding="utf-8-sig")
    except (UnicodeError, OSError) as error:
        raise ValueError(f"Deliver {relative} as a readable UTF-8 file.") from error


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON property names are not allowed.")
    return result


def _ledger_defects(allocation: _Allocation) -> list[str]:
    defects = []
    seen = set()
    for index, position in enumerate(allocation.positions, start=1):
        label = " ".join(unicodedata.normalize("NFKC", position.asset).casefold().split())
        if not label or label in seen:
            defects.append(f"Position {index} must have a nonempty, distinct asset name.")
        seen.add(label)
        if (
            position.asset_class == "other_equity"
            and _US_STOCK_NAME.match(label)
            and not _AMBIGUOUS_REGION.search(label)
        ):
            defects.append(
                f"Position {index} ({position.asset}) names US stock exposure. "
                "Use us_equity for that holding and update the proposal's class totals."
            )
        if position.amount_hkd != position.weight_bps * (_BUDGET // _FULL_WEIGHT_BPS):
            defects.append(f"Position {index} amount_hkd does not match weight_bps.")
    if sum(position.weight_bps for position in allocation.positions) != _FULL_WEIGHT_BPS:
        defects.append("Position weights must total 10000 basis points (100%).")
    if sum(position.amount_hkd for position in allocation.positions) != _BUDGET:
        defects.append("Position amounts must total HKD 1,000,000.")
    if not any(position.asset_class == "us_equity" for position in allocation.positions):
        defects.append("Identify at least one proposed US equity position.")
    return defects


def _expected_rows(allocation: _Allocation) -> dict[str, tuple[Decimal | None, Decimal | None]]:
    weights = dict.fromkeys([*_CLASSES, "equity", "reserve"], 0)
    amounts = weights.copy()
    for position in allocation.positions:
        key = position.asset_class
        group = "equity" if key in {"us_equity", "other_equity"} else "reserve"
        weights[key] += position.weight_bps
        weights[group] += position.weight_bps
        amounts[key] += position.amount_hkd
        amounts[group] += position.amount_hkd
    expected = {
        title: (Decimal(weights[key]) / 100, Decimal(amounts[key]))
        for key, title in (
            _CLASSES | {"equity": "Total equity", "reserve": "Total reserve"}
        ).items()
    }
    decline = Decimal(allocation.stock_decline_bps)
    loss = Decimal(amounts["equity"]) * decline / _FULL_WEIGHT_BPS
    expected.update(
        {
            "Total portfolio": (Decimal(100), Decimal(_BUDGET)),
            "Stock decline": (decline / 100, None),
            "Stress loss": (None, loss),
            "Stress remaining": (None, _BUDGET - loss),
        }
    )
    return expected


def _cells(line: str) -> list[str]:
    row = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in row.split("|")]


class _SummaryMarkers(HTMLParser):
    """Locate the standalone comments in top-level Markdown, outside literal HTML."""

    def __init__(self, proposal: str) -> None:
        super().__init__()
        self.lines = visible_markdown_lines(proposal)
        self.positions: dict[str, list[int]] = {_START: [], _END: [], SUMMARY_PLACEHOLDER: []}
        self.tags: list[str] = []
        try:
            self.feed("\n".join(self.lines))
            self.close()
        except AssertionError as error:
            raise ValueError("Use ordinary Markdown around the allocation summary.") from error

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {
            "pre",
            "code",
            "script",
            "style",
            "template",
            "details",
            "div",
            "section",
            "aside",
            "span",
            "table",
            "textarea",
            "noscript",
        } or "hidden" in dict(attrs):
            self.tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.tags and self.tags[-1] == tag:
            self.tags.pop()

    def handle_comment(self, data: str) -> None:
        marker = f"<!--{data}-->"
        line, column = self.getpos()
        if (
            marker in self.positions
            and not self.tags
            and column < 4
            and self.lines[line - 1].rstrip() == " " * column + marker
        ):
            self.positions[marker].append(line - 1)


def _summary(proposal: str, boundaries: dict[str, list[int]]) -> tuple[list[list[str]], int, int]:
    lines = proposal.splitlines()
    starts, ends = boundaries[_START], boundaries[_END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise ValueError("Add one visible allocation summary between the required markers.")
    start, end = starts[0], ends[0]
    table = [line for line in lines[start + 1 : end] if line.strip()]
    if any(line.startswith(("    ", "\t")) for line in table):
        raise ValueError("Put the allocation table outside indented code.")
    cells = [_cells(line) for line in table]
    if (
        len(cells) < 2
        or [cell.casefold() for cell in cells[0]] != ["item", "weight", "hkd"]
        or len(cells[1]) != 3
        or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells[1])
    ):
        raise ValueError("Use a Markdown Item | Weight | HKD table in the allocation summary.")
    return cells[2:], start, end


def _cell_value(value: str, *, percentage: bool) -> Decimal | None:
    value = value.strip(" *`").strip()
    if value in {"-", "–", "—"}:
        return None
    pattern = rf"({_NUMBER})\s*%" if percentage else rf"(?:(?:HKD|HK\$)\s*)?({_NUMBER})"
    match = re.fullmatch(pattern, value, re.IGNORECASE)
    if not match:
        raise ValueError("Use a numeric value or dash.")
    return Decimal(match[1].replace(",", ""))


def _read_cell(value: str, expected: Decimal | None, *, percentage: bool) -> bool:
    try:
        return _cell_value(value, percentage=percentage) == expected
    except ValueError:
        return False


def _ledger_value(expected: Decimal | None, *, percentage: bool) -> str:
    if expected is None:
        return "a dash"
    return f"{expected.normalize():f}%" if percentage else f"HKD {expected.normalize():,f}"


def _proposal_defects(proposal: str, allocation: _Allocation) -> list[str]:
    try:
        rows, start, end = _summary(proposal, _SummaryMarkers(proposal).positions)
    except ValueError as error:
        return [str(error)]
    expected = _expected_rows(allocation)
    fields = {item.casefold(): item for item in expected}
    seen = set()
    defects = []
    for row in rows:
        if len(row) != 3:
            defects.append("Each allocation summary row needs an Item, Weight and HKD cell.")
            continue
        label = fields.get(row[0].strip(" *`").casefold())
        if label is None:
            defects.append("Use only the required item labels in the allocation summary.")
            continue
        if label in seen:
            defects.append(f"List {label} once in the allocation summary.")
            continue
        seen.add(label)
        weight, amount = expected[label]
        if not _read_cell(row[1], weight, percentage=True):
            defects.append(
                f"The {label} Weight cell does not match the verified allocation. "
                f"Use {_ledger_value(weight, percentage=True)} from {ALLOCATION_ARTIFACT}."
            )
        if not _read_cell(row[2], amount, percentage=False):
            defects.append(
                f"The {label} HKD cell does not match the verified allocation. "
                f"Use {_ledger_value(amount, percentage=False)} from {ALLOCATION_ARTIFACT}."
            )
    if missing := [label for label in expected if label not in seen]:
        defects.append(f"Add missing allocation summary rows: {', '.join(missing)}.")

    # Flag explicit numerical claims seen in the failed trial. Prose still needs review.
    lines = proposal.splitlines()
    narrative = "\n".join(lines[:start] + lines[end + 1 :])
    for terms, result in _SUM.findall(narrative):
        total = sum(Decimal(term.strip().rstrip("%").replace(",", "")) for term in terms.split("+"))
        if total != Decimal(result.rstrip("%").replace(",", "")):
            defects.append("An explicit sum in the proposal does not equal its stated result.")
            break
    reserve_weight = expected["Total reserve"][0]
    if any(Decimal(value) != reserve_weight for value in _RESERVE_BASELINE.findall(narrative)):
        defects.append("A reserve baseline in the proposal contradicts the allocation summary.")
    return list(dict.fromkeys(defects))


def _load_allocation(root: Path) -> tuple[_Allocation | None, list[str]]:
    try:
        raw = _read_text(root, ALLOCATION_ARTIFACT)
        payload = json.loads(raw, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError) as error:
        return None, [f"Invalid allocation JSON: {error}"]
    try:
        allocation = _Allocation.model_validate(payload)
    except ValidationError as error:
        return None, [
            f"Allocation field {'.'.join(map(str, item['loc'])) or 'root'}: {item['msg']}."
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        ]
    return allocation, _ledger_defects(allocation)


def attach_allocation_summary(root: Path, proposal: str) -> str:
    """Render the placeholder or refresh one standard numeric table from the valid ledger."""
    if not any(marker in proposal for marker in (SUMMARY_PLACEHOLDER, _START)):
        return proposal
    allocation, defects = _load_allocation(root)
    if allocation is None or defects:
        return proposal
    expected = _expected_rows(allocation)
    try:
        markers = _SummaryMarkers(proposal).positions
        if SUMMARY_PLACEHOLDER in proposal:
            if (
                len(markers[SUMMARY_PLACEHOLDER]) != 1
                or proposal.count(SUMMARY_PLACEHOLDER) != 1
                or _START in proposal
                or _END in proposal
            ):
                return proposal
            start = end = markers[SUMMARY_PLACEHOLDER][0]
        else:
            if proposal.count(_START) != 1 or proposal.count(_END) != 1:
                return proposal
            old_rows, start, end = _summary(proposal, markers)
            remaining = {label.casefold(): pair for label, pair in expected.items()}
            for label, weight, amount in old_rows:
                previous = remaining.pop(label.strip(" *`").casefold(), None)
                if previous is None:
                    return proposal
                weight_value = _cell_value(weight, percentage=True)
                amount_value = _cell_value(amount, percentage=False)
                if (weight_value is None, amount_value is None) != (
                    previous[0] is None,
                    previous[1] is None,
                ):
                    return proposal
            if remaining:
                return proposal
    except ValueError:
        return proposal
    rows = [_START, "| Item | Weight | HKD |", "| --- | ---: | ---: |"]
    for label, (weight, amount) in expected.items():
        percent = "—" if weight is None else f"{weight.normalize():f}%"
        hkd = "—" if amount is None else f"{amount.normalize():,f}"
        rows.append(f"| {label} | {percent} | {hkd} |")
    rows.append(_END)
    lines = proposal.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in proposal else "\n"
    original_end = lines[end]
    suffix = original_end[len(original_end.rstrip("\r\n")) :]
    lines[start : end + 1] = [newline.join(rows) + suffix]
    return "".join(lines)


def check_portfolio(root: Path) -> list[str]:
    """Return actionable defects in the ledger and its numeric Markdown summary."""
    allocation, defects = _load_allocation(root)
    if allocation is None or defects:
        return defects
    try:
        proposal = _read_text(root, PROPOSAL_ARTIFACT)
    except ValueError as error:
        return [str(error)]
    return _proposal_defects(proposal, allocation)
