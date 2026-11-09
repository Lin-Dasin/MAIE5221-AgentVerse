# Section 5 - Evaluation Harness and File Workflow Baseline

Role D implements deterministic file acceptance, fixed-case loading, serial
batch execution, dual completion metrics, JSON export and per-run JSONL traces.
The offline suite exercises expected successes and failures. A separate live
file pilot measures three fixed task graphs with DeepSeek. SQLite event storage,
replay and the conversational CLI remain planned.

## Current progress and execution

| File | Responsibility |
|---|---|
| `harness/verdict.py` | File and text acceptance |
| `harness/cases.py` | Strict JSON/Pydantic suite loading |
| `harness/offline.py` | Scripted Worker connected to A's real scheduler |
| `harness/runner.py` | Serial runs with separate case directories and IDs |
| `harness/metrics.py` | Typed reports and goal/node aggregation |
| `harness/reporting.py` | UTF-8 JSON export, refusing to overwrite prior reports |
| `harness/run.py` | Evaluation command entry point |
| `harness/suites/offline-smoke.json` | Six data-only fixture cases |
| `bus/trace.py` | JSONL scheduler events and tool-call metadata |

From the synchronized Role D checkout, run:

```bash
python -m harness.run --suite offline-smoke --mock --output results/offline-smoke.json
```

`--suite` also accepts a JSON path. `--mock` is required to acknowledge offline
execution. `--work-dir` selects the parent directory for fresh per-batch/per-case
workspaces; `--case-timeout` bounds each run. A failed case does not stop later
cases. Invalid suite input fails before execution. Use
`--fail-on-goal-failure` to return exit code 1 if any goal fails; otherwise a
completed batch returns 0 even when the fixtures deliberately include failures.
Input/output errors return 2. Cancellation propagates instead of becoming a
normal failed-case record.

| Fixed case | Goal passes | Passed nodes / all nodes |
|---|---|---|
| `success` | Yes | 1 / 1 |
| `content-failure` | No | 0 / 1 |
| `partial-completion` | No | 2 / 3 |
| `retry-success` | Yes | 1 / 1 |
| `delegation-success` | Yes | 2 / 2 |
| `blocked-dependency` | No | 0 / 2 |
| **Total** | **3 / 6 (50%)** | **6 / 10 (60%)** |

The Worker writes actual UTF-8 Markdown and never supplies precomputed verdicts.
Retries consume subsequent scripted steps; delegated nodes enter A's scheduler
and pass through the same acceptance checks. No live model client is invoked.

## Completion metrics and JSON

- A goal passes only when the returned goal verdict passes and no run-level
  failure is present. Failed goals remain in the denominator.
- Node completion is total successful task records divided by all returned task
  records, including failed, blocked and dynamically inserted nodes. Retrying a
  node does not create another record. Do not average individual case percentages.
- A planning failure with no graph has zero generated nodes. Zero-denominator
  rates are JSON `null` with an explicit reason, never 100%.
- If a graph exists but records are missing, report `unreported_task_ids`, a warning
  and a batch missing-record count. The metric still uses returned records under
  A's current contract; do not silently invent results for unreported nodes.
- Invalid returned records, such as duplicate IDs, produce a failed case and are
  retained in `rejected_result` for diagnosis, excluded from completion metrics.
- Reports preserve the actual `RunResult`, final graph, node records, goal verdict,
  failure reasons, memory flags, HITL decisions, warnings and suite content hash.
- `duration_scope=fixed_graph` excludes autonomous planning because this version
  uses `run_graph()`. `token_scope=worker_only` must not be presented as complete
  system usage; these scripted cases have no model token consumption.
- `trace_path` links the case report to its JSONL trace when tracing is enabled.
  Events share the case `run_id` and include a sequence number and UTC timestamp.
  Scheduler records cover node status, file handoffs, delegation and approval
  decisions. Tool records include the agent, node, tool, outcome, elapsed time
  and failure category. File contents and raw tool arguments are omitted.
  A trace write failure is reported as a warning so the evaluation can finish.
- Fresh reports use local paths. In the archived pilot report, trace and
  artifact paths are relative to the directory containing the JSON.

The top-level JSON retains the project-plan fields `run_id`, `suite`, `tasks`
and `summary`, with the run mode and raw execution evidence added.

## Component and contract

`harness.verdict.FileVerifier` implements the async `Verifier` interface from
`protocol/ports.py` and returns the shared `CriticResult`. It imports
`AcceptanceRule` from `protocol/models.py`; it does not redefine shared models.

```python
from pathlib import Path

from harness.verdict import FileVerifier
from protocol.models import AcceptanceRule

# Call within the application's async function.
result = await FileVerifier().check(
    [
        AcceptanceRule(type="file_exists", path="outputs/report.md"),
        AcceptanceRule(
            type="text_contains", path="outputs/report.md", text="## Conclusion"
        ),
    ],
    Path("workspaces/writer"),
)
```

| Rule | Behavior |
|---|---|
| `file_exists` | Pass only for an existing regular file; directories do not count |
| `text_contains` | Read strict UTF-8 and check a case-sensitive literal substring |
| `pytest_passes` | Return an explicit `unsupported_rule` defect until B's isolated executor is connected; never run generated tests on the host |

The verifier evaluates every rule and returns `checks_passed`, `checks_total`,
`passed` and an ordered `defects` list. Missing files, invalid encoding and file
read errors fail the relevant check without hiding other checks. Empty rule lists
and missing/non-directory roots raise `ValueError` as configuration errors.

File checks prove only the specified condition. A Sources heading, for example,
does not establish that sources are real or that a report is factually accurate.

## Paths and execution boundaries

| Call site | Root | Example rule path |
|---|---|---|
| Node acceptance | `workspaces/<agent>/` | `outputs/report.md` |
| Goal acceptance | `artifacts/<run_id>/<final_task_id>/` | `report.md` |

A removes the `outputs/` prefix when archiving. D uses the supplied root and path
without rewriting either. Paths use the shared relative POSIX-path validator;
resolved targets must remain inside the root. File symlinks are rejected, matching
A's artifact publisher. Parent symlinks resolving outside the root are rejected.

File I/O runs through `asyncio.to_thread` so it does not block the event loop.
The current contract assumes stable artifacts during serial verification. This
path check is not a replacement for B's container isolation, nor is it a defense
against a concurrent process swapping files during the read. Cancelling the await
does not forcibly terminate an in-progress filesystem read in the thread.

## Validation baseline and dependency

Role A PR #8 supplies the shared protocol, scheduler and test helpers. `Role_D`
incorporates these through the merge of `develop` at `b01c5379`. The full A+D
suite and the offline smoke command ran from the same repository checkout.

Latest validation on 2026-11-05, Windows with Python 3.11.5:

| Check | Observed result |
|---|---|
| Combined A and D suite, including Trace | 93 passed, 3 skipped |
| Ruff | Passed |
| Offline smoke batch | 3/6 goals and 6/10 nodes passed |

The three skips are real file/parent symlink tests: Windows returned WinError 1314
because the account cannot create symbolic links. These cases still need a run
in the team's Ubuntu environment.

Tests cover Chinese UTF-8 text, literal matching, mixed verdict counts, absent
files, directories, invalid UTF-8, simulated read errors, unsupported code checks,
invalid roots and parent traversal. They also cover strict suite loading, weighted
node metrics, exception/timeout continuation, run isolation, cancellation,
invalid-result evidence, JSON round trips and refusing to overwrite reports.
Two verifier tests exercise A's real scheduler:

1. A Worker writes an invalid report, receives the verifier's defects, retries
   with corrected content and archives a passing result without duplicating nodes.
2. Node acceptance passes but the independent goal acceptance fails, leaving a
   successful node and an `acceptance_failed` run outcome.

No live model calls, API credentials or Docker containers are used by these tests.

## Live file pilot

On 2026-11-05, the requested model ID `deepseek-chat` ran three fixed task graphs
with thinking disabled and memory disabled. The isolated checkout used Role D
base `bf41a98` with the Trace code later committed as `e2b0b3d`, plus Role B's
file-tool implementation at PR #11 head `bfc8681`. The run evaluates file
creation, handoff and acceptance with the real model. Its timing covers fixed
graph execution and its token count covers Worker calls.

| Case | Goals passed | Nodes passed | Seconds | Worker tokens |
|---|---:|---:|---:|---:|
| Chinese status brief | 1/1 | 1/1 | 3.23 | 3,617 |
| Two-node Chinese handoff | 1/1 | 2/2 | 6.40 | 8,985 |
| Two-source summary | 1/1 | 3/3 | 8.58 | 13,434 |
| **Batch** | **3/3** | **6/6** | **6.07 mean** | **26,036 total** |

All nine recorded file calls succeeded. The handoff checks matched the three
downstream reads to the files copied from A's archive. The batch ID is
`77fd23d74a984c6a88580c890134e62a` and the suite SHA-256 is
`fc86ef96371b1f96a57a07ca1f03d9b087043a2699b31d8d30261d4fc1f004cc`.

The local archive is `results/baselines/live-file-dev-20261105/`. Its README
describes how to find the report, traces, accepted Markdown and runnable source
snapshot. The `results/` directory is ignored by Git.

## Proposed Dev Set coverage

The team plan calls for three cases in each of five categories, with first
metrics due November 8. The five groups below are proposed for team review.
The measured pilot covers F1–F3. The other rows are prepared scenarios and
acceptance checks for the next integrations.

| ID | Category | Task and acceptance | Status or dependency |
|---|---|---|---|
| F1 | File workflows | Create `status-brief.md` with the Chinese scope and owner sections. Verify required text in the accepted file. | Live fixed-graph pass |
| F2 | File workflows | Read the handed-off `inputs/brief.md`. Verify the returned text matches the handoff and `final.md` contains the heading and source text. | Live fixed-graph pass |
| F3 | File workflows | Read `inputs/metrics.md` and `inputs/checks.md`. Verify both reads and both source bodies in `summary.md`. | Live fixed-graph pass |
| P1 | Planning and delegation | Submit a natural-language request through `run_goal` for a brief containing `W10` and `Owner D`. Verify the generated graph and accepted brief. | Pending live `run_goal` integration |
| P2 | Planning and delegation | Ask distinct Workers to produce and combine two labeled sources. Verify generated dependency links, both handoffs and both labels in the final file. | Pending live `run_goal` integration |
| P3 | Planning and delegation | Have a parent Worker request a specialist. Verify a depth-1 child is inserted, accepted and counted once, then the parent resumes. | Pending live delegation case |
| H1 | Human approval | Clarify an ambiguous report label interactively. Verify the pause and response in the trace and the chosen label in the accepted file. | Pending interactive callback |
| H2 | Human approval | Approve deletion of a disposable marker file. Verify the decision precedes the tool call and the marker is removed. | Pending interactive callback and B tool integration |
| H3 | Human approval | Deny the same deletion under unattended policy. Verify a bounded failed or blocked result, the preserved marker and absence of a delete call. | Pending policy run with B tool integration |
| T1 | Extended tools | Search for two fixed public references. Verify search calls and compare the archived URLs and facts with the case fixtures. | Pending B search adapter |
| T2 | Extended tools | Produce a small Python module and pass a fixed pytest suite. Verify the isolated executor exit code and test evidence. | Pending B isolation and test adapter |
| T3 | Extended tools | Publish a fixture file to an authorized test repository branch. Verify the resulting commit SHA and the file content. | Pending B Git adapter and test repository authorization |
| M1 | Memory and evolution | Complete a task and verify a private reflection belongs to the Worker and refers to the observed outcome. | Pending C memory adapter |
| M2 | Memory and evolution | Run a related task and verify retrieval and injection of the relevant reflection. | Pending C retrieval and trace integration |
| M3 | Memory and evolution | Compare the same case with memory enabled and disabled against separate snapshots. Verify isolation and report goals, nodes, time and tokens for both runs. | Pending C adapter and comparable snapshots |

For H3, the denial can satisfy the control's expected behavior while the raw
goal outcome remains failed or blocked. Report these two observations separately.

## Reproduce this local validation

Activate a Python 3.11 or newer virtual environment and run these commands from
the repository root. The editable install binds imports to this checkout.

```bash
python -m pip install -e '.[dev]'
python -m pytest -q -rs
python -m ruff check .
python -m harness.run --suite offline-smoke --mock
```

## Remaining integration decisions

- B/D: supply the isolated execution and result contract for `pytest_passes`.
- A/D: keep the structured rule vocabulary and the two acceptance roots aligned.
- A/D: define reporting for scheduler initialization failures that return a graph
  but no task records; this is a later Harness aggregation concern.
- Environment: repeat validation in Ubuntu/WSL2 and complete the real sandbox
  integration separately.
