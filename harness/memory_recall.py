"""Measure top-one recall on three paired English and Chinese reflection queries."""

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from memory import JsonlMemory
from protocol.models import AcceptanceRule, Task, TaskResult, TaskStatus


@dataclass(frozen=True)
class RecallCase:
    topic: str
    language: str
    seed_title: str
    query: str
    advice: str


CASES = (
    RecallCase(
        "invoice",
        "en",
        "Review invoice totals",
        "Recheck invoice totals",
        "Compare line items before rounding.",
    ),
    RecallCase("invoice", "zh", "核对发票总额", "重新核对发票总额", "先逐项比对再四舍五入。"),
    RecallCase(
        "backup",
        "en",
        "Verify backup integrity",
        "Retest backup integrity",
        "Restore a sample before signing off.",
    ),
    RecallCase("backup", "zh", "验证备份完整性", "再次验证备份完整性", "签字前先还原一份样本。"),
    RecallCase(
        "schedule",
        "en",
        "Review shift schedules",
        "Recheck shift schedules",
        "Compare handover times before publishing.",
    ),
    RecallCase("schedule", "zh", "核对轮班安排", "重新核对轮班安排", "发布前先比对交班时间。"),
)


def _task(task_id: str, title: str) -> Task:
    # Hold required paths and rules constant across languages and topics.
    output = "outputs/结果"
    return Task(
        task_id=task_id,
        title=title,
        assignee="researcher",
        outputs=[output],
        acceptance=[AcceptanceRule(type="file_exists", path=output)],
    )


async def _measure(root: Path) -> dict:
    rows = []
    summary = {}
    for language in ("en", "zh"):
        corpus = [case for case in CASES if case.language == language]
        memory = JsonlMemory(root / language, max_records=1)
        for case in corpus:
            seed = _task(f"seed-{case.topic}", case.seed_title)
            await memory.reflect(
                seed,
                TaskResult(
                    task_id=seed.task_id,
                    agent_id=seed.assignee,
                    status=TaskStatus.SUCCESS,
                    summary=case.advice,
                ),
            )

        hits = 0
        for case in corpus:
            query = _task(f"query-{case.topic}", case.query)
            text = await memory.retrieve(query.assignee, query)
            hit = case.advice in text
            hits += int(hit)
            rows.append(
                {
                    "case_id": f"{language}-{case.topic}",
                    "language": language,
                    "seed_title": case.seed_title,
                    "query": case.query,
                    "expected_marker": case.advice,
                    "retrieved_text": text,
                    "hit": hit,
                }
            )
        summary[language] = {"hits": hits, "total": len(corpus), "hit_rate": hits / len(corpus)}
    return {
        "benchmark": "m2-bilingual-recall-v1",
        "max_records": 1,
        "cases": rows,
        "summary": summary,
    }


def run_memory_recall() -> dict:
    with TemporaryDirectory(prefix="agentverse-m2-") as directory:
        return asyncio.run(_measure(Path(directory)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run_memory_recall()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for language, stats in report["summary"].items():
        print(f"{language}: {stats['hits']}/{stats['total']}")
    print(f"Evidence: {args.output}")


if __name__ == "__main__":
    main()
