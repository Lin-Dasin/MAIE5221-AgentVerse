"""General production guidance and evidence summaries, independent of task topics."""

from collections.abc import Sequence
from datetime import date

from protocol.models import DiscussionTurn
from runtime.discussion_delivery_policy import WRITE_TOOLS


def phase_instruction(round_number: int, max_rounds: int) -> str:
    if round_number == max_rounds:
        phase = (
            "DELIVER: finish the actual requested product in your contribution, repair known "
            "defects, and give exact paths and usage instructions. A future plan is not delivery."
        )
    elif round_number == 1:
        phase = (
            "BUILD: agree a small feasible scope and produce a first usable draft now. "
            "The lead names an implementer and reviewer from the team; other roles act on "
            "their responsibilities immediately rather than waiting for the next round."
        )
    elif round_number == max_rounds - 1:
        phase = (
            "REVIEW AND REPAIR: inspect the real candidate artifacts, check the requirements, "
            "run applicable tests, and fix concrete defects. Do not certify unseen work."
        )
    else:
        phase = (
            "COMPLETE: extend and integrate the existing draft to meet the current goal; "
            "do not restart planning or repeat research already supplied."
        )
    return (
        f"Current date: {date.today().isoformat()}. Distinguish observed events from forecasts.\n"
        f"Team phase: {phase}\n"
        "For an ordinary reversible deliverable, use explicit minimal assumptions instead "
        "of inventing extra requirements (such as mandatory interviews or production "
        "deployment) that block the user's requested scope. Preserve essential questions "
        "and permission gates. A research uncertainty belongs in the deliverable with a "
        "source and limitation; never fabricate a quote, date, number, or human answer.\n"
        "Every agent has a private workspace. Other agents' outputs/ paths are not your "
        "files. If the adapter supplies candidate copies, find them in inputs/shared/ "
        "and prior-task material in inputs/task1/. Copies are unaccepted drafts. Read "
        "before reviewing or extending them; preserve compatibility and retained records. "
        "Write your own deliverables under outputs/. Use small self-contained files and "
        "available dependencies; run short tests, not a persistent server, in code_exec. "
        "Use exact candidate paths below instead of repeatedly listing directories. "
        "Do not use code_exec merely to print whole source files. Before finishing, "
        "persist your contribution with file_write or file_patch: implementation, "
        "article, review findings "
        "or release instructions as appropriate to your role. "
        "When handing off, name the actual files, interfaces and checks. A valid public "
        "text deliverable is also useful, but a promise to create one is not."
    )


def progress_view(turns: Sequence[DiscussionTurn]) -> str:
    """Show actual successful writes/check outcomes without private trace contents."""
    entries: dict[tuple[str, str], str] = {}
    for turn in turns:
        for call, result in zip(turn.tool_calls, turn.tool_results, strict=True):
            data = result.data if isinstance(result.data, dict) else {}
            if result.ok and call.name in WRITE_TOOLS and isinstance(data.get("path"), str):
                path = data["path"]
                entries[(turn.speaker, path)] = (
                    f"{turn.speaker}: wrote {path[:200]} (candidate; quality not verified). "
                    f"Adapter copy convention: inputs/shared/{turn.speaker}/"
                    f"{path.removeprefix('outputs/')[:200]}"
                )
            elif call.name == "code_exec":
                status = (
                    f"exit_code={data.get('exit_code', 'unknown')}" if result.ok else "tool failed"
                )
                entries[(turn.speaker, "execution")] = (
                    f"{turn.speaker}: latest code execution {status}; "
                    "exit zero alone does not establish requirement coverage"
                )
    return "\n".join(list(entries.values())[-16:]) or "No recorded writes or execution checks yet."
