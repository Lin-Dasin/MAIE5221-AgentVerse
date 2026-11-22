# Managed Delivery Execution Policy (Role A, Local)

This change addresses the allocation and unsaved-repair failures documented in
`results/midtest-acceptance-retest-2026-11-23/MIDTEST_REPORT.md`. Historical
results and snapshots are preserved. This policy is included in the cumulative
Role A discussion/delivery PR; publishing the code does not change its measured
validation limits.

## One attempt reserve, then bounded contributions

`DeliveryDiscussionRunner` still reserves 40% of the task cap for repairs,
allocating 60% to the initial attempt. It now selects an isolated
`RoundRobinDiscussionEngine.for_delivery()` policy. The managed engine does
not apply the standalone engine's additional 40% round reserve.

At 240,000 total tokens and four speakers, the first contribution therefore
has a 36,000-token ceiling (`240000 * .6 / 4`), instead of the previous
14,400-token soft work share (`240000 * .6 * .4 / 4`). Each subsequent speaker
receives the remaining attempt allowance divided by speakers still to act in
that round. Later rounds use the unspent allowance; they are not guaranteed.
For profiles with production tools, each contribution is bounded by that
share. A small or exhausted allowance can still legitimately cause early
reporting or a budget stop.

The wrapper remains the only repair-reserve owner and meters all attempts
through `DiscussionBudget`, including failed calls and output-limit recovery.
No token cap, default round count or permission boundary is raised. With
repairs disabled, the initial attempt may use the full task allowance.
Standalone `engine.run()` callers keep the previous policy. The cloned policy
does not mutate an engine that another caller may reuse.

## Inspect, produce, verify

For managed profiles with permitted `file_write` or `code_exec`,
`runtime/discussion_delivery_policy.py` selects tools and output limits:

| Phase | Opportunities | Limits |
| --- | --- | --- |
| Inspect | Read exact targets and supplied candidates; research relevant evidence; small early writes allowed | At most two model steps initially, one during repair; inspection output capped at 2,048 or the configured smaller limit |
| Targeted source check | During repair, use one additional step for missing source evidence or write immediately | Only available when search quota and sufficient budget remain; no directory listings or local reads; normal production output cap |
| Produce | Persist a draft or defect fix using `file_write` / `code_exec` | No list/read/search calls; retain the configured output cap (8,192 in the previous Mid Test), reduced if needed to reserve closing tokens |
| Verify | Read the saved result, run short checks, or write another fix | Opens after a successful write to a declared target (or an outputs/ file for supporting roles without targets); no listings/search; acceptance is separate |
| Report | State actual paths, unresolved defects and blockers | No tools, output capped at 512 or the configured smaller limit |

A contribution has at most two directory-list dispatches, four inspection
reads and six reads in total. The two additional reads remain available for
checking saved files. Search retains the existing two-call quota, including
batched calls. Tools outside the active phase and over-quota batched requests
are rejected **before execution**, even if the model ignores the offered list.
Direct `file_write` must target `outputs/`; input paths, traversal and absolute
paths are rejected. Helper-file writes do not unlock verification for an owner
with declared targets. The runner passes those targets explicitly, so an
unrelated placeholder cannot stand in for the required repair. This is a
workflow gate, not a replacement for B's path/symlink isolation or semantic
acceptance: a meaningless write to the correct target can still occur and must
fail review. Protected tools never become available. `code_exec` stays behind B's existing
sandbox; A does not parse arbitrary Python to infer whether it wrote a file.

The policy reserves a bounded closing response and recounts the prompt after
adding phase instructions. A long early write may hit the smaller inspection
output cap; the existing one-time output-limit retry enters production with
the configured larger limit. Its spent tokens are still charged. File writes
replace file contents, so prompts recommend small complete files, not appending
fragments by repeated replacement.

Plain-text DSML tool markup is never executed. It receives at most one
structured-tool corrective opportunity; repeated markup fails explicitly.
Successful current-contribution write receipts also survive final prompt
compaction, so later reads do not erase the evidence supplied to the model.
Receipts describe executed calls, not final quality or continued file existence.
None of these controls guarantees that a model will produce correct work.
They provide bounded writing opportunities and stop directory discovery from
using the production phase. When evidence is insufficient, an honest blocker
is preferable to an invented source or an unverified success claim.

## Actual repair evidence and handoff

All participants now receive the same final-owner/path map as well as their
own requirements. Rework receives the recorded successful writes and execution
checks from prior completed turns. Candidate-copy paths are explicitly an
adapter convention; A neither copies workspaces nor claims a copy exists.

Before and immediately after each repair, A fingerprints **each declared
artifact**, including partial deliveries where another required file is
missing. `DeliveryAttempt` adds two optional, empty-by-default fields:

- `changed_requirements`: at least one declared artifact changed/appeared/disappeared.
- `unchanged_requirements`: all declared artifact states are identical.

These fields cover comparable requirements, including those owned by other
participants, so previously passing files can also be tracked. They are empty
for the initial attempt. Unsafe, unreadable or oversized artifacts are excluded
from the comparison and remain subject to fail-closed acceptance.

A failed requirement owned by a current repair participant whose files are
unchanged gets a `no_artifact_progress` defect for the next bounded repair.
Rewriting identical bytes does not count as progress. Changed hashes do not
establish correctness: all requirements are still rechecked with B/D adapters.
The original repair-count and total-budget limits remain in force. In
particular, a model that keeps refusing to write will still fail truthfully.
Fingerprints cover declared entry artifacts, not every possible dependency;
functional/semantic checks and final-package validation remain necessary.

## Scope and validation

Only A's protocol extension, orchestrator, runtime, corresponding tests and
docs are changed. Original discussion wire contracts and `run()` signatures
are unchanged. B's executor, C's memory/personas and D's production CLI/harness
are not edited. D can persist the new progress fields and adopt the existing
runner; B/D continue to own workspace/candidate/package consistency.

Run with the repository virtual environment:

```bash
python -m pytest -q tests/test_discussion.py tests/test_discussion_delivery.py tests/test_delivery_lifecycle.py tests/test_delivery_execution_policy.py
python -m pytest -q
python -m ruff check .
git diff --check
```

The new controls reproduce the recorded 4,511-token prompt cost with four
speakers, a 240,000-token task cap, 8,192 output cap and four initial rounds.
They verify real file inspection/writing/checking, out-of-phase dispatch
rejection, batched quotas, reserved verification reads, one targeted source
opportunity, truncated-output recovery, protected tools, DSML rejection,
unchanged repairs, partial artifacts, and changed-but-still-failed work.
These are deterministic orchestration controls, not model-quality scores.

Local evidence, including regression XML and bounded real-model repair probes,
is under `results/delivery-execution-fix-2026-11-23/` (Git-ignored). See its
`IMPROVEMENT_REPORT.md` for measured results and limitations. A full new
six-task Mid Test has not been run as part of this change; the historical
0/6 result is not replaced by these controls.

Final local validation on 2026-11-23: **80 focused tests passed; 468 full-suite
tests passed with Docker and zero skips; Ruff and whitespace checks passed.**
Twenty new regression cases cover this change. Four bounded live repair probes
were retained, including an unsuccessful placeholder-write attempt that led to
the declared-target gate. The final probe changed the intended article and
consumed 33,407 tokens, but source verification and content quality remain
unverified. It is not a new six-case completion rate.
