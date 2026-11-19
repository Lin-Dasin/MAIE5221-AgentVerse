"""Check the P2 plan, handoff, tool metadata and accepted files of one CLI run."""

import hashlib
import json
from pathlib import Path

SOURCES = (
    ("owner_a", "alpha.md", "P2-ALPHA-731"),
    ("owner_b", "beta.md", "P2-BETA-946"),
)


def file_bytes(root, *parts):
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    return path.read_bytes()


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def inspect(root: Path) -> dict:
    report = json.loads((root / "evaluation.json").read_text(encoding="utf-8"))
    case = report["tasks"][0]
    raw = case["raw_result"] or {}
    graph = raw.get("graph") or {}
    run_id = report["run_id"]
    trace_file = root / "trace.jsonl"
    events = []
    if trace_file.is_file():
        events = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]
    read_file = root / "file-reads.jsonl"
    reads = []
    if read_file.is_file():
        reads = [json.loads(line) for line in read_file.read_text(encoding="utf-8").splitlines()]

    tasks = {task["task_id"]: task for task in graph.get("tasks", [])}
    final_id = graph.get("final_task_id")
    final = tasks.get(final_id, {})
    handoffs = [
        e for e in events if e["event"] == "artifacts_handed_off" and e["task_id"] == final_id
    ]
    tool_events = [
        e
        for e in events
        if e["event"] == "tool_finished"
        and e["ok"]
        and e["task_id"] == final_id
        and e["agent_id"] == "writer"
    ]
    write_seq = max((e["sequence"] for e in tool_events if e["tool"] == "file_write"), default=0)
    distinct_reads = {
        e["call_id"]
        for e in tool_events
        if e["tool"] == "file_read"
        and any(h["sequence"] < e["sequence"] < write_seq for h in handoffs)
    }
    # Older audits identify calls by order within a task. Both the audit and
    # the tool trace append immediately after each successful read returns.
    read_events = [event for event in tool_events if event["tool"] == "file_read"]
    writer_reads = [r for r in reads if r["agent_id"] == "writer" and r["task_id"] == final_id]
    ordered_reads = [
        row
        for event, row in zip(read_events, writer_reads, strict=False)
        if event["call_id"] in distinct_reads
    ]
    archive = root / "artifacts"
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("invalid run identifier")
    summary = file_bytes(archive, run_id, final_id, "summary.md") if final_id else None
    final_lines = (summary or b"").decode("utf-8").splitlines()

    checks = {
        "live_goal_passed": report["mode"] == "live" and case["status"] == "success",
        "three_subtasks_passed": case["subtasks_passed"] == case["subtasks_total"] == 3,
        "both_fixed_rules_passed": case["acceptance_passed"] == case["acceptance_total"] == 2,
        "trace_complete": bool(events)
        and events[-1]["event"] == "run_finished"
        and events[-1]["status"] == ("success" if case["status"] == "success" else "failed")
        and not case["warnings"]
        and all(e["run_id"] == run_id and e["sequence"] == seq for seq, e in enumerate(events, 1)),
        "three_planned_nodes": len(tasks) == 3 and final.get("assignee") == "writer",
        "writer_declared_summary": "outputs/summary.md" in final.get("outputs", []),
        "writer_read_calls_after_handoff_before_write": len(distinct_reads) >= 2,
        "read_audit_matches_trace_count": len(read_events) == len(writer_reads),
    }
    sources = []
    source_ids = []
    destinations = []
    for owner, filename, marker in SOURCES:
        candidates = [
            t
            for t in tasks.values()
            if t["assignee"] == owner and f"outputs/{filename}" in t["outputs"]
        ]
        upstream = candidates[0] if len(candidates) == 1 else {}
        task_id = upstream.get("task_id")
        source_ids.append(task_id)
        checks[f"{owner}_independent_producer"] = bool(upstream) and not upstream["depends_on"]
        refs = [
            r
            for r in final.get("inputs", [])
            if r["from_task"] == task_id and r["path"] == filename
        ]
        ref = refs[0] if len(refs) == 1 else None
        dest = ref["destination"] if ref else None
        destinations.append(dest)
        checks[f"{owner}_handoff_recorded"] = bool(ref) and any(
            ref in h["inputs"] for h in handoffs
        )
        original = file_bytes(archive, run_id, task_id, filename) if task_id else None
        staged = file_bytes(root / "workspaces" / "writer", dest) if dest else None
        read_hash = digest(staged.replace(b"\r\n", b"\n").replace(b"\r", b"\n")) if staged else None
        matching_reads = [
            r
            for r in ordered_reads
            if r["agent_id"] == "writer"
            and r["task_id"] == final_id
            and r["path"] == dest
            and r["text_sha256"] == read_hash
        ]
        checks[f"{owner}_read_returned_staged_text"] = bool(read_hash and matching_reads)
        lines = (original or b"").decode("utf-8").splitlines()
        checks[f"{owner}_staged_bytes_match"] = original is not None and original == staged
        checks[f"{owner}_labeled_source_line"] = (
            len(lines) == 1
            and lines[0].startswith(marker)
            and bool(lines[0][len(marker) :].strip())
        )
        checks[f"{owner}_whole_line_in_summary"] = len(lines) == 1 and lines[0] in final_lines
        sources.append(
            {
                "agent": owner,
                "task_id": task_id,
                "archive_file": filename,
                "destination": dest,
                "archive_sha256": digest(original),
                "staged_sha256": digest(staged),
                "read_text_sha256": read_hash,
                "matched_read_count": len(matching_reads),
                "source_lines": lines,
            }
        )
    checks["writer_depends_on_both_producers"] = bool(all(source_ids)) and (
        set(final.get("depends_on", [])) == set(source_ids)
    )
    checks["two_distinct_destinations"] = bool(all(destinations)) and len(set(destinations)) == 2
    checks["exactly_two_summary_lines"] = len(final_lines) == 2
    return {
        "run_id": run_id,
        "p2_passed": all(checks.values()),
        "checks": checks,
        "summary": report["summary"],
        "worker_tokens": case["tokens"],
        "writer_task_id": final_id,
        "successful_writer_reads_between_handoff_and_write": len(distinct_reads),
        "read_scope": "Tool trace records counts and order. file-reads.jsonl records paths and "
        "hashes of the text returned by successful file_read calls.",
        "sources": sources,
        "final_lines": final_lines,
        "raw_failure_reason": case["failure_reason"],
    }
