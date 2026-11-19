"""Run the normal UI goal workflow and record successful file reads for P2."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

from harness.p2_check import inspect
from protocol.models import AcceptanceRule
from ui.goal import _build_file_tools, run_goal

BASE = Path(__file__).resolve().parent / "suites" / "p2"


def audited_tools(root):
    real_tools, definitions = _build_file_tools(root)

    class ReadAudit:
        async def execute(self, call, context):
            result = await real_tools.execute(call, context)
            if call.name == "file_read" and result.ok:
                text = result.data["text"]
                row = {
                    "agent_id": context.agent_id,
                    "task_id": context.task_id,
                    "path": call.arguments["path"],
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
                with (root / "file-reads.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + "\n")
            return result

    return ReadAudit(), definitions


async def run(work_dir: Path, guided: bool) -> Path:
    rules = TypeAdapter(list[AcceptanceRule]).validate_json((BASE / "acceptance.json").read_bytes())
    path, _ = await run_goal(
        (BASE / "goal.txt").read_text(encoding="utf-8").strip(),
        work_dir,
        rules=rules,
        profiles_dir=BASE / ("profiles-guided" if guided else "profiles"),
        tool_factory=audited_tools,
    )
    return path.parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    live = commands.add_parser("run", help="Run a live DeepSeek goal and check its evidence")
    live.add_argument("--work-dir", type=Path, default=Path("results/p2"))
    live.add_argument("--guided", action="store_true")
    check = commands.add_parser("check", help="Recheck saved evidence offline")
    check.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        root = (
            asyncio.run(run(args.work_dir, args.guided)) if args.command == "run" else args.run_dir
        )
        result = inspect(root)
        output = root / "p2-check.json"
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        print(f"P2 evidence unavailable: {error}", file=sys.stderr)
        return 2
    print(f"P2 passed: {result['p2_passed']}; evidence: {output}")
    return int(not result["p2_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
