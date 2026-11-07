"""Write machine-readable evaluation reports without clobbering old results."""

import json
from pathlib import Path

from harness.metrics import EvaluationReport


def write_report(report: EvaluationReport, path: Path) -> Path:
    payload = json.dumps(
        report.model_dump(mode="json"), ensure_ascii=False, indent=2, allow_nan=False
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an accidental rerun from replacing past evidence.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload + "\n")
    return path
