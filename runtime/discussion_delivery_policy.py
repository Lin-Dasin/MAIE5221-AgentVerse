"""Bounded inspect -> produce -> verify opportunities for managed delivery only."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from protocol.models import ToolDefinition

PRODUCTION_TOOLS = frozenset({"file_write", "code_exec"})
INSPECTION_CALL_LIMITS = {"file_list": 2, "file_read": 6}


def call_limit(name: str, phase: str) -> int:
    # Preserve at least two reads to inspect a saved result after production.
    if name == "file_read" and phase == "inspect":
        return 4
    return INSPECTION_CALL_LIMITS.get(name, 10**9)


def output_path(value: object) -> str | None:
    """A workflow gate, not a replacement for B's filesystem/symlink checks."""
    if not isinstance(value, str) or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        return None
    return str(path) if path.parts[0] == "outputs" else None


@dataclass(frozen=True)
class DeliveryStep:
    phase: str
    tools: list[ToolDefinition]
    output_cap: int
    instruction: str


def delivery_step(
    *,
    tools: list[ToolDefinition],
    step: int,
    max_steps: int,
    repair: bool,
    wrote: bool,
    needs_production: bool,
    inspection_spent: dict[str, int],
    available: int,
    prompt_tokens: int,
    closing_reserve: int,
    output_cap: int,
) -> DeliveryStep:
    """Keep a closing allowance and prioritise production over more exploration.

    A successful write opens verification; it is never an acceptance verdict.
    Large early writes can use the existing bounded output-limit retry, which
    switches to production with the configured (larger) output cap.
    """
    phase = "inspect"
    if wrote:
        phase = "verify"
    elif needs_production or step > (1 if repair else 2):
        phase = "produce"
    elif step > 1 and available < 2 * prompt_tokens + output_cap + closing_reserve + 2048:
        phase = "produce"
    if (
        repair
        and step == 2
        and not wrote
        and not needs_production
        and any(tool.name == "web_search" for tool in tools)
        and available >= 2 * prompt_tokens + output_cap + closing_reserve + 2048
    ):
        phase = "source_check"
    if step == max_steps or available - prompt_tokens - closing_reserve < 128:
        phase = "report"
    if phase == "report":
        return DeliveryStep(phase, [], min(512, output_cap), "")
    if phase in {"produce", "source_check"}:
        selected = [
            tool
            for tool in tools
            if tool.name in PRODUCTION_TOOLS
            or (phase == "source_check" and tool.name == "web_search")
        ]
        instruction = (
            "PRODUCE: inspection is closed for this contribution. Use file_write to persist "
            "the draft or defect fix at the exact required path; code_exec may implement/test "
            "with existing evidence. Prefer small complete files; file_write replaces a file. "
            "Do not list/read "
            "directories again or merely promise a fix. If essential evidence or human input "
            "is unavailable, report the specific blocker; never invent it. "
            "After a successful file_write, bounded file_read checks reopen."
        )
        if phase == "source_check":
            instruction = (
                "SOURCE CHECK: one bounded opportunity to resolve specific source defects with "
                "web_search before production. Search only the missing evidence, cite returned "
                "URLs, and distinguish excerpts from full sources. Directory listings and more "
                "local reads are closed. You may instead save the repair now. The next step "
                "prioritises file_write/code_exec; do not postpone writing or invent evidence."
            )
        cap = output_cap
    else:
        selected = [
            tool
            for tool in tools
            if (phase != "verify" or tool.name not in {"file_list", "web_search"})
            and inspection_spent.get(tool.name, 0) < call_limit(tool.name, phase)
        ]
        instruction = (
            "VERIFY: inspect the saved artifact or run a short check; fix any remaining defect "
            "with file_write. Report actual paths and limitations. A write is not acceptance."
            if phase == "verify"
            else "INSPECT: read exact candidate/required paths, batching necessary reads. "
            "At most two directory listings and four inspection reads are available; "
            "two further reads are reserved for checking after a write. "
            "Inspection lasts at most two steps (one during repair); then persist the work. "
            "Early small writes are allowed; save larger implementations in the production phase."
        )
        cap = output_cap if phase == "verify" else min(2048, output_cap)
    return DeliveryStep(
        phase, selected, max(1, min(cap, available - prompt_tokens - closing_reserve)), instruction
    )
