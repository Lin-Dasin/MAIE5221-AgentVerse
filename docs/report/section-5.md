# Section 5 - Evaluation Harness and File Workflow Baseline

Role D implements deterministic file acceptance, fixed-case loading, serial
batch execution, dual completion metrics, JSON export and per-run JSONL traces.
The offline suite exercises expected successes and failures. A separate live
file pilot measures three fixed task graphs with DeepSeek. An offline terminal
demo exercises human clarification through A's agent loop. The unified CLI
accepts file-workflow goals, runs the offline demos, reads evaluation summaries,
replays JSONL traces and stores SQLite conversation history. Users can direct
private and group messages to a Worker and launch a goal from a group.

## Current progress and execution

| File | Responsibility |
|---|---|
| `harness/verdict.py` | File and text acceptance, plus opt-in pytest acceptance |
| `harness/cases.py` | Strict JSON/Pydantic suite loading |
| `harness/offline.py` | Scripted Worker connected to A's real scheduler |
| `harness/runner.py` | Serial runs with separate case directories and IDs |
| `harness/metrics.py` | Typed reports and goal/node aggregation |
| `harness/reporting.py` | UTF-8 JSON export, refusing to overwrite prior reports |
| `harness/run.py` | Evaluation command entry point |
| `harness/suites/offline-smoke.json` | Six data-only fixture cases |
| `bus/trace.py` | JSONL scheduler events and tool-call metadata |
| `bus/replay.py`, `ui/replay.py` | Read and display a saved trace in recorded order |
| `bus/messages.py` | SQLite conversation membership and ordered message history |
| `ui/cli.py`, `ui/goal.py` | Unified terminal commands and natural-language file workflow |

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
| `pytest_passes` | The T2 verifier explicitly enables B's host-subprocess runner for trusted fixtures. A check passes only when the runner reports `passed=true` and integer exit code 0. |

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

The November 5 baseline used Windows and Python 3.11.5.

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

On 2026-11-05, the live pilot ran three fixed task graphs with `deepseek-chat`
as the requested model ID and with thinking and memory disabled. The isolated
checkout used Role D base `bf41a98` with the Trace code later committed as
`e2b0b3d`, plus Role B's file-tool implementation at PR #11 head `bfc8681`.
The run evaluates file creation, handoff and acceptance with the real model.
Its timing covers fixed graph execution and its token count covers Worker calls.

The archived report's `model` field comes from the request configuration
(`client.model`). The client retained response content, tool calls and token
usage. The provider-returned model ID and historical serving backend are
unknown from this evidence.

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

For a new live run, set `LLM_MODEL=deepseek-flash` in the local environment
file. This is the model ID listed in the current
[DeepSeek model table](https://api-docs.deepseek.com/quick_start/pricing/).

## T1 search evaluation

On 2026-11-10, T1 ran against `main` commit `3a8567e`, which includes Role B's
search adapter from PR #15. A fixed researcher-to-writer graph checked a Python
release date and a PEP build flag. The goal verifier required a matching official
URL from each search query and the corresponding fact in the final Markdown.

Both the scripted run and the DeepSeek run with simulated Tavily responses
passed 1/1 goals, 2/2 nodes and 3/3 goal checks. The DeepSeek request and response
both identified `deepseek-flash`. The run used an archive-relative handoff path
in the fixed graph. The local evidence is under
`results/baselines/search-t1-20261110/`.

On 2026-11-21, D reran the same two-source fixed graph with real DeepSeek and
Tavily on a branch based on `develop` at `e085c0d`. The model request used
`deepseek-chat`. Each query was limited to two results. Both requests returned
HTTP 200 and each response contained the official URL assigned to that query.
The accepted Markdown paired the [Python 3.13.0 release page](https://www.python.org/downloads/release/python-3130/)
with `2024-10-07` and [PEP 703](https://peps.python.org/pep-0703/) with
`--disable-gil`. The researcher archive, writer input and final report matched
byte-for-byte.

| Goals | Subtasks | Fixed acceptance checks | Fixed-graph time | Worker tokens |
| ---: | ---: | ---: | ---: | ---: |
| 1/1 | 2/2 | 3/3 | 17.91 s | 15,602 |

The JSON, trace and verification summary are in the local run directory
`results/baselines/t1-live-20261121/130414-103730/` (run
`bbd23bc4a11646fda4145703ab745912`). Export `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY` and `SEARCH_API_KEY` in the current shell to repeat the case. Use a
fresh output directory for each run.

```powershell
python -m harness.t1 --output-dir results/t1-live-recheck
```

T1 starts from the fixed graph. [Issue #17](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/issues/17)
tracks natural-language handoff planning. [PR #32](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/32)
records a separate single-source CLI run that included planning.

## Terminal clarification pilot

The H1 entry point is `python -m ui.h1_demo --timeout 60`. It uses a scripted
model and a fixed report-writing fixture with A's real runtime and scheduler.
The agent requests a title through `ask_human`. D's `TerminalClarification`
callback supplies A's `InteractiveApprover` with a decision bearing the original
request ID. The task resumes and the final archive must begin with the chosen
title. Each run gets its own directory under `results/h1-demo/`, containing an
evaluation JSON, JSONL trace and accepted Markdown when successful.

Automated stdin checks on 2026-11-10 passed the answer, empty-input and timeout
paths. The answer run passed 1/1 goals and 1/1 nodes. Empty input and timeout
produced blocked task outcomes and no report. Trace metadata records the wait,
decision and final state. The title remains in the local evaluation and report.
The local evidence is `results/baselines/h1-cli-20261110/verified/`.

The callback reads from a duplicate stdin stream so an unanswered question can
time out without holding interpreter shutdown. Late replies are discarded. A
session declines further questions until its pending terminal read completes.
Fifteen focused tests cover terminal behavior and pause/resume integration.

On November 21 a separate group-clarification pilot requested `deepseek-chat`.
A second CLI process answered the pending request in the same group. The
scheduler resumed the original task and the archived heading contained the
chosen title. The goal and its one node passed in 12.69 s with 7,749 Worker
tokens and one recorded HITL decision. This run exercised the prototype later
committed as `394ea1a` on `Role_D_group_clarification`. Its evidence is under
`results/baselines/group-clarification-live-20261121/7ee24104/`.

### Worker chat

In a private chat on November 21 the writer recalled the code from the user's
preceding turn. In a group chat the researcher repeated the code the writer
had just posted. The four-message histories are in
`results/baselines/private-chat-20261121-6219ab51/history.txt` and
`results/baselines/group-chat-20261121-9c07dd79/history.txt`. These probes
cover directed Worker replies and conversation history. Group task execution
is measured separately in P2. [Worker chat commands](../private-chat.md)

An isolated November 21 merge preview combined group clarification
(`1d61e67`) and CLI search (`cc9c46e`) on `develop` at `e085c0d`. One import
conflict in `ui/goal.py` was resolved by retaining both imports. All 79 focused
CLI, search and message-bus tests passed. [Group clarification PR](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/31) ·
[CLI search PR](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/32)

## Replay a trace

Pass the path printed by the H1 demo, or the path from a case's `trace_path`
field, to `python -m ui.replay`. Resolve relative report paths from the report's
directory. The command shows the sequence, timestamp and available metadata for
task states, delegation, file handoffs, tool results and human decisions.

```powershell
python -m ui.replay path/to/trace.jsonl
```

The command reads one run per JSONL file. It reports sequence gaps and a missing
end event while displaying the available events. Invalid records are reported
with a line number. The displayed outcome is the recorded task run's outcome.
Exit code 2 means the replay command could not read the trace.

## Current offline integration evidence

The H2/H3 and P3 probes use scripted model replies with A's actual runtime and
B's actual file tools. Each run has a fresh workspace. The Dev Set batch below
uses scripted Worker fixtures and the same scheduler and verifier.

| Run | Goals | Nodes | Evidence |
|---|---:|---:|---|
| H2, terminal approval | 1/1 | 1/1 | Approval precedes one successful deletion of the generated marker. |
| H3, policy denial | 0/1 | 0/1 | Expected control passes. The marker is unchanged and deletion was never called. |
| P3, delegation | 1/1 | 2/2 | Parent reads the actual delegated artifact. Read bytes match the child archive. |
| Dev fixture subset F1–F3 and P3 | 4/4 | 8/8 | Named offline batch produces evaluation JSON and a Trace per case. |

The local reports are under `results/baselines/permission-h2-h3-20261111/`,
`results/baselines/delegation-p3-20261111/` and `results/baselines/dev-20261111/`.
The integrated `develop` snapshot `b2e893e` with these Role D changes passed
200 tests. Three symlink cases were skipped on Windows. Ruff passed.

The two project entry points can be tried with these commands from the repo root.

```powershell
python -m ui.cli --help
python -m ui.cli h1
python -m harness.run --suite dev
```

`ui.cli run` uses the integrated B file tools and compatible YAML profiles.
It reads the model configuration from the process environment. Pass a JSON array
of fixed acceptance rules with `--acceptance` for a scored run. The JSON report
records the requested model ID and includes planning in the elapsed time.
Token totals cover Worker calls.

A separate CLI smoke run used the integrated snapshot plus Role A's
[PR #19](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/19) at `23c59f7`.
It requested `deepseek-flash` and passed 1/1 goals, 1/1 nodes and 1/1 fixed
acceptance checks. Elapsed time was 11.17 seconds including planning, with
5,244 Worker tokens. Its archived report and Trace are under
`results/baselines/cli-live-20261111/`. The snapshot includes a pending Role A
change, so this run is evidence for that integration combination.

## Dev Set coverage

The team plan calls for three cases in each of five categories, with first
metrics due November 8. The five groups below are proposed for team review.
The live file pilot covers F1–F3. T1 now also has a real Tavily fixed-graph run.
The four cases in the offline `dev` batch are a runnable subset of this plan.

| ID | Category | Task and acceptance | Status or dependency |
|---|---|---|---|
| F1 | File workflows | Create `status-brief.md` with the Chinese scope and owner sections. Verify required text in the accepted file. | Live fixed-graph pass |
| F2 | File workflows | Read the handed-off `inputs/brief.md`. Verify the returned text matches the handoff and `final.md` contains the heading and source text. | Live fixed-graph pass |
| F3 | File workflows | Read `inputs/metrics.md` and `inputs/checks.md`. Verify both reads and both source bodies in `summary.md`. | Live fixed-graph pass |
| P1 | Planning and delegation | Submit a natural-language request through `run_goal` for a brief containing `W10` and `Owner D`. Verify the generated graph and accepted brief. | Local live single-node pilot passed. |
| P2 | Planning and delegation | Ask distinct Workers to produce and combine two labeled sources. Verify generated dependency links, both handoffs and both labels in the final file. | The November 21 default-planner group run passed and preserved both source lines. |
| P3 | Planning and delegation | Have a parent Worker request a specialist. Verify a depth-1 child is inserted, accepted and counted once, then the parent resumes. | Offline integration with real file tools passed. |
| H1 | Human approval | Clarify an ambiguous report label interactively. Verify the pause and response in the trace and the chosen label in the accepted file. | Terminal controls passed. A live group-clarification prototype resumed from a reply in a second CLI process. |
| H2 | Human approval | Approve deletion of a disposable marker file. Verify the decision precedes the tool call and the marker is removed. | Automated terminal input and real file tool passed. |
| H3 | Human approval | Deny the same deletion under unattended policy. Verify a bounded failed or blocked result, the preserved marker and absence of a delete call. | Expected denial control passed. |
| T1 | Extended tools | Search for two fixed public references. Verify search calls and compare the archived URLs and facts with the case fixtures. | Real Tavily and DeepSeek fixed-graph run passed. Both official sources and 3/3 checks verified. |
| T2 | Extended tools | Produce a small Python module and pass a fixed pytest suite. Verify the executor exit code and test evidence. | All four trusted host-subprocess controls passed. Generated-code execution still needs container validation. |
| T3 | Extended tools | Publish a fixture file to an authorized test repository branch. Verify the resulting commit SHA and the file content. | Approval and denial controls passed against a local bare repository. Hosted GitHub acceptance requires a separate authorized run. |
| M1 | Memory and evolution | Complete a task and verify a private reflection belongs to the Worker and refers to the observed outcome. | Local live English pilot verified persistence. |
| M2 | Memory and evolution | Run a related task and verify retrieval and injection of the relevant reflection. | English and Chinese top-one recall passed on PR #25. A separate live Chinese Worker run verified injection. |
| M3 | Memory and evolution | Compare the same case with memory enabled and disabled against separate snapshots. Verify isolation and report goals, nodes, time and tokens for both runs. | The Chinese Worker on/off retest reached the model and produced an artifact in both conditions. |

For H3, the denial can satisfy the control's expected behavior while the raw
goal outcome remains failed or blocked. Report these two observations separately.

#### Dev evaluation entry points

Run commands from the repository root with the project's Python environment.
Historical pilots in `results/baselines/` are local evidence; a fresh checkout
can use the commands below.

| Coverage | Entry point | Measurement |
|---|---|---|
| F1–F3, P3 | `python -m harness.run --suite dev` | Four offline goals and their nodes. P3 exercises dynamic delegation. |
| P1 | `python -m ui.cli run --help` | Live natural-language single-goal entry point; the historical pilot used fixed acceptance. |
| P2 | `python -m harness.p2 run --work-dir results/p2` | Requests a live DeepSeek run. Set the process variables LLM_BASE_URL, LLM_MODEL and LLM_API_KEY first. Add `--guided` for the second planner profile. |
| P2 offline | `python -m harness.p2 check RUN_DIR` | Recompute the verdict from a saved run directory, including both handoffs, successful reads and the accepted final text. Exit 0 means pass, 1 means failed checks, 2 means unusable input or command error. |
| H1 | `python -m ui.cli h1` | Interactive clarification and accepted output. |
| H2, H3 | `python -m ui.cli permission --scenario h2` or `h3` | Approval control and raw task outcome, reported separately. |
| T1 | `python -m harness.t1 --output-dir results/t1-live-recheck` | Runs the two fixed source queries with Tavily and checks both URL–fact pairs in the archived report. |
| T2 | `python -m harness.t2 --work-dir results/t2` | Trusted host pytest controls and raw goal/node verdicts. |
| T3 | `python -m harness.t3 --work-dir results/t3` | Local branch, SHA and blob checks under both approval decisions. |
| M1, M2, M3 | `python -m harness.memory_recall --output results/m2.json` for M2 | The current offline executable measures top-one recall. M1 reflection and M3 memory on/off are separate historical pilots. |

On November 19 D checked B's proposed interfaces in a separate source snapshot
of [PR #23](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/23) at
`9aa9bbc`. The eight pytest-runner tests passed on Windows. Eight of nine Git
tests passed. In the remaining test, the local bare repository contained the
pushed commit and expected file on `master`, while its default HEAD pointed at
an empty `main`. T3 subsequently checked the ref named by the tool directly.

On November 20 the T2 and T3 checks on `daf5e01` exercised the scheduler and
real tools. T2 accepted the correct implementation in both the workspace and
the published archive (exit 0 twice). The same test rejected the incorrect
implementation (exit 1). Empty collection (exit 5) and a two-second timeout
(no exit code) also failed acceptance. All four outcomes matched expectations.
The raw rates were 1/4 goals and 1/4 nodes. [T2 procedure](../t2-evaluation.md)

In T3 the approved push placed commit `8461b4f` on `delivery` and the file at
that commit matched the fixture bytes. The denied candidate `9db3a6f` was a
direct child of the published commit. The trace contained no push call after
denial and both the remote refs and the approved blob remained intact. Approval
delivered 1/1 goal and 1/1 node. Denial delivered 0/1 of each as expected.
The two local controls and their JSON reports are under
`results/baselines/t2-t3-pr-20261120/`. [T3 procedure](../t3-local-evaluation.md)

### P2 live two-source handoff

On November 19, 2026, two runs on `develop` at `2c794b9` used the normal
`ui.goal.run_goal` workflow, DeepSeek (requested model `deepseek-chat`), and
real file tools. Evaluation profiles supplied two independent source authors
and one writer. Both runs used the same natural-language goal and two fixed
final text checks. Both generated their own graph. An audit wrapper recorded
the paths and text hashes of successful reads from the real executor.

| Planner profile | Goals | Nodes | Seconds including planning | Worker tokens |
|---|---:|---:|---:|---:|
| Default | 0/1 | 2/3 | 38.04 | 25,945 |
| With workspace-path and task-description guidance | 1/1 | 3/3 | 27.90 | 14,936 |

In both runs the writer received byte-identical copies of both source archives
and read both inputs. In the first run, node acceptance targeted bare filenames,
while declared outputs lived under `outputs/`. The source task titles omitted
the requested recommendation topics. The writer retried and reached its response
limit without publishing an accepted summary. In the second run, only the
evaluation planner prompt changed. Every node passed on its first attempt,
and the accepted summary reproduced both independently written lines verbatim.
The evidence is under `results/baselines/p2-live-20261119/`: each run contains
`evaluation.json`, `trace.jsonl`, `file-reads.jsonl` and `p2-check.json`.

On November 21, the group task entry point at `da4f2be` ran P2 with the
default planner and requested `deepseek-chat`. The goal, all three nodes and
both final acceptance checks passed in 23.36 s including planning, using
15,295 Worker tokens. The group handoff matched the trace, the writer read
twice before writing, and its final report reproduced both complete source
lines. Group members saw the same history for the run. Evidence is in
`results/baselines/group-p2-default-sync-20261121-4951b75e/verification.json`.

### M2 bilingual recall

On November 19, 2026, the offline probe ran against the memory implementation
in `develop` at `2c794b9`. Each language used a fresh store with the same three
topics: invoice totals, backup integrity and shift schedules. For each topic,
the probe wrote a successful reflection using `reflect`, queried a related task
using `retrieve` and checked whether the single returned record contained the
expected advice. All three English queries selected the intended reflection.
All three Chinese queries returned empty text. The tokenizer at that revision
extracted ASCII letters, digits and underscores, so those titles supplied no
topic tokens.

On November 20 D ran the same six queries with the same expected advice and
one-record limit on two revisions. On `develop` at `7beea75`, English hit 3/3
and Chinese hit 0/3. With C's Chinese bigram matching at `7327426`, both
languages hit 3/3. Both JSON reports are under
`results/baselines/m2-pr25-recheck-20261120/`. [Memory fix, PR #25](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/25)

To reproduce the comparison from the repository root:

```bash
python -m harness.memory_recall --output results/m2-bilingual/evaluation.json
```

The JSON contains each query, expected advice, returned text and hit flag, plus
the hit rate per language. A completed run writes the report even when queries
miss, so the same fixtures can measure changes to the retriever.

### M2 injection and M3 Worker memory comparison

On November 21, a fixed-graph Chinese invoice task on `da4f2be` tested whether
a writer could reuse a marker from its own earlier reflection. Both conditions
started from identical private-memory snapshots, disabled writes and requested
`deepseek-chat` with thinking disabled. The marker was absent from the task
description. The on-condition trace recorded retrieval followed by injection,
and its memory-text hash matched the input captured at the model boundary.

| Retrieval | Goal | Nodes | Fixed-graph seconds | Worker tokens | Archived result |
|---|---:|---:|---:|---:|---|
| On | 1/1 | 1/1 | 4.28 | 6,459 | Reproduced the marker |
| Off | 0/1 | 1/1 | 2.36 | 4,012 | Reported the missing convention in the file and failed the goal-level marker check |

An earlier off-condition run requested clarification and was blocked. For the
retest, both groups received the same instruction to write a file even when the
marker could not be recovered. Both stored snapshots retained their initial
hashes. The comparison covers one task and measures Worker usage only. The
paired evidence is in `results/baselines/worker-memory-ab-20261121/live-retest/2244c45dd5bd46f7a95616b16610fca0/comparison.json`.

### Failure attribution for development runs

| Case | What failed | Follow-up evidence |
|---|---|---|
| CLI search | The researcher exceeded its task token budget after two successful searches. The downstream task remained blocked. | A repeat used one search with one result and passed with the same budget and rules. [PR #32](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/32) |
| P2 planning | The November 19 default plan used bare acceptance paths and omitted the requested source topics. The writer exhausted its response limit before publishing an accepted summary. | The guided run passed that day. The November 21 group run also passed with the default planner. |
| T2 pytest | Wrong implementation, empty test collection and timeout produced three distinct failed checks. | Each was expected in its control, with the outcome and exit information preserved separately. |
| T3 validation | The older Git probe inspected the bare repository's default `HEAD`, which pointed at another branch. | The local acceptance checks the branch named by the tool, then compares the commit and file content. |
| M2 recall | Chinese titles produced no usable tokens in the older matcher. | The same Chinese queries all returned the intended record after C's bigram change. |

For Mid and Final, freeze the released cases and their goal rules before
running. Record the dataset version, source commit, model ID, mode, memory and
approval settings, both completion rates, timings, Worker tokens and Trace
references. Classify failures by planning, missing input, tool, approval,
acceptance or timeout. Keep the internal pilot measurements distinct from the
official results. The project workspace contains no official Mid or Final case
file for this run.

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

- B/D: run the fixed Docker calculation with B's backend and C's `coder`
  profile. [Docker acceptance procedure](../docker-calculation-acceptance.md)
- A/D: keep the structured rule vocabulary and the two acceptance roots aligned.
- Harness reports graph nodes without returned task records in `unreported_task_ids`.
  A run with zero returned nodes has an undefined subtask completion rate.
- Environment: repeat validation in Ubuntu/WSL2 and complete the real sandbox
  integration separately.
