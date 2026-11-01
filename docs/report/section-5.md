# Section 5 - Evaluation Harness and Offline Validation

Role D now implements deterministic file acceptance, fixed-case loading, serial
batch execution, dual completion metrics and JSON export. Event persistence,
replay, the conversational CLI and live end-to-end evaluation are still pending.
The results below validate software behavior; they are not live-model scores.

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

In a checkout containing A's shared implementation and D's files, run:

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
- `trace_path` is explicitly `null` until the event/Trace implementation is added.
  Artifact references are absolute paths on the local host; copy the associated
  run directory when moving a report to another machine.

The top-level JSON retains the project-plan fields `run_id`, `suite`, `tasks`
and `summary`, with explicit offline provenance and raw execution evidence added.

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

Role A PR #8 at commit `6645428042313560875308d1a50961e00c6c98f3` supplies the
protocol, scheduler and test helpers used by this implementation. As checked on
2026-11-01, PR #8 has merged into `develop` at `b01c5379`, but this Role D progress
branch still uses its earlier base with placeholders for A's modules. No branches
were merged as part of publishing this progress. A's sources were not copied into
the Role D source checkout.

For local validation, an archive of the fixed A commit was extracted into a
separate directory. D's `harness/` sources, the fixed JSON suite and
`tests/harness/` tests were overlaid without changing any A source files.

This combined directory is the test target. Until A is integrated, a fresh
`Role_D` checkout alone cannot run these tests: it lacks the required shared
implementation. This is a progress branch, not a standalone integrated release.
Normal team integration with `develop` remains a separate step. Do not add
duplicate protocol models to work around that gap.

Latest validation on 2026-11-01, Windows with Python 3.11.5:

| Check | Observed result |
|---|---|
| Unmodified A baseline | 36 passed |
| D loading, metrics, batch, export, command and verifier tests | 52 passed, 3 skipped |
| Combined A and D suite | 88 passed, 3 skipped |
| Ruff in the combined source tree | Passed |

The three skips are real file/parent symlink tests: Windows returned WinError 1314
because the account cannot create symbolic links. Rerun these tests in the team's
Ubuntu environment before treating symlink behavior as fully validated. This host
currently lists only the `docker-desktop` WSL2 distribution, not Ubuntu.

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

## Reproduce this local validation

From the Role D repository, this PowerShell example prepares a fresh temporary
validation tree without merging branches or overwriting an existing directory:

```powershell
$sourceRoot = (Get-Location).Path
$aCommit = '6645428042313560875308d1a50961e00c6c98f3'
$validationRoot = Join-Path ([IO.Path]::GetTempPath()) ('agentverse-' + [guid]::NewGuid())
$archivePath = $validationRoot + '.zip'
git archive --format=zip --output=$archivePath $aCommit
if ($LASTEXITCODE -ne 0) { throw 'The fixed Role A commit must be available locally' }
Expand-Archive -LiteralPath $archivePath -DestinationPath $validationRoot
python -m venv (Join-Path $validationRoot '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$pythonExe = Join-Path $validationRoot '.venv/Scripts/python.exe'
Push-Location $validationRoot
try {
    & $pythonExe -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & $pythonExe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Role A baseline failed' }
    Copy-Item -Path (Join-Path $sourceRoot 'harness/*') `
        -Destination 'harness' -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'tests/harness') `
        -Destination 'tests' -Recurse
    & $pythonExe -m pytest -q -rs
    if ($LASTEXITCODE -ne 0) { throw 'Combined tests failed' }
    & $pythonExe -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
    & $pythonExe -m harness.run --suite offline-smoke --mock
    if ($LASTEXITCODE -ne 0) { throw 'Offline batch failed' }
} finally {
    Pop-Location
}
```

Once A and D are integrated in the same checkout, the normal commands are simply
`python -m pytest -q -rs` and `python -m ruff check .` after installing `.[dev]`.
If a restricted Windows execution environment blocks asyncio's local socketpair
initialization, use the approved normal terminal/test runner; do not change the
application's event-loop implementation to hide the environment restriction.

## Remaining integration decisions

- B/D: supply the isolated execution and result contract for `pytest_passes`.
- A/D: keep the structured rule vocabulary and the two acceptance roots aligned.
- A/D: define reporting for scheduler initialization failures that return a graph
  but no task records; this is a later Harness aggregation concern.
- Environment: repeat validation in Ubuntu/WSL2 and complete the real sandbox
  integration separately.
