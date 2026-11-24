# Role A Discussion and Delivery: Cumulative Validation

Date: 2026-11-23. Base: `develop` at `5f6bace`.

This document accompanies the cumulative Role A discussion/delivery change.
It summarizes the work developed locally after the earlier memory PRs and
separates runnable regression coverage from observed live task quality.

## Relationship to existing PRs

At preparation time, #38 (`feat(memory): trace which records were retrieved`)
remains open and #39 (`feat(planner): retrieve and reflect on previous goals`)
is a separate open PR. Neither is the base of this change. This change targets
`develop` directly and does not duplicate their memory implementations, nor
those of the separate #35–#37 workstreams. Discussion memory is still disabled.
Changes touching the shared protocol/docs/client may need reconciliation when
those PRs merge; the current base and tests do not establish that future merge.

## Included implementation

1. Standalone discussion contracts and engine: ordered Worker personas,
   rotating moderator, public conversation, awaited completed-turn callbacks,
   human-input polling, and private per-persona tool execution contexts.
2. Context and budget handling: injectable prompt counter, bounded labeled
   assistant/tool excerpts, verbatim human instructions, intact tool pairing,
   retained source/evidence fields, usage-preserving output-limit exceptions,
   and one bounded shorter-response recovery without executing partial calls.
3. Delivery guidance: build/complete/review/deliver phases, candidate-write and
   execution-check ledger, bounded search, action reminders, and cached repeated
   listings invalidated by potentially mutating calls.
4. Opt-in delivery lifecycle: fixed goal/instruction/owner/artifact plans,
   ordinary-file and SHA-256 checks, injected machine and semantic verification,
   owner-targeted repairs, all-requirement rechecks, and explicit
   passed/partial/failed/unverified results. New human instructions invalidate
   the stale plan; missing or faulty review cannot produce acceptance.
5. Shared task spending across initial/rework attempts, including failed or
   interrupted calls. The wrapper reserves repair allowance; managed execution
   removes the extra round reserve. At the evaluated 240k/four-person setting,
   the first contribution gets 36k instead of a 14.4k soft work share.
6. Managed inspect/source-check/produce/verify/report tool phases, dynamic
   output caps, batched dispatch quotas, reserved verification reads, declared
   target write gates, outputs-only direct writes, current write receipts,
   plain-text DSML rejection, and per-repair changed/unchanged artifact evidence.

Defaults remain 60,000 task tokens and 2,048 output tokens. Evaluations explicitly
used larger limits. The task-graph entry point is unchanged; this is an opt-in
backend API, not a completed production chat/CLI integration. B/C/D production
modules are not changed, and no approval requirement is bypassed.

## Reproducible regression validation

```bash
python -m pytest -q tests/test_discussion.py tests/test_discussion_delivery.py tests/test_delivery_lifecycle.py tests/test_delivery_execution_policy.py
python -m pytest -q
python -m ruff check .
git diff --check
```

The final development validation passed **80 focused tests** and **468 full-suite
tests**, with Docker available and **zero skips**. Ruff and whitespace checks
passed. These include real file adapters, real file verification, container
checks, and deterministic model controls. Passing controls show orchestration
behavior; they are not a live task completion score.

The new execution-policy tests use the recorded 4,511-token prompt cost with
four speakers, a 240k task cap, 8,192 output cap and four rounds. They check that
owners can inspect, write and read back; that extra reads cannot displace the
production phase; that an unrelated placeholder cannot unlock verification;
and that a changed-but-incorrect or unchanged delivery still fails acceptance.

## Live evaluation history

These are local observations, not the course's official score. Conditions and
adapters changed between stages, and model/search outputs vary, so they are not
an isolated causal A/B comparison.

| Stage | Observed result | Interpretation |
| --- | --- | --- |
| First budget/context fix, real S2 at 60k | Completed contributions increased from 4/0 to 12/11; both tasks still reached the budget cap and produced no artifact | More discussion alone did not complete the work |
| Delivery hardening, six tasks at 240k/8,192 | 1,259,307 returned tokens, 323 calls, 792.359 seconds; all tasks reached four rounds; local core-goal result 1/6 | Booking core checks passed; other deliverables remained partial or off-goal |
| Acceptance/rework wrapper, six tasks at 240k/8,192 | 0/6 complete deliveries; all stopped at `rework_limit`; 1,102,912 tokens and 306 calls; no model errors or task-budget stops | The gate caught failures, but stacked allowances and nonpersisted repairs prevented convergence |
| Final managed policy, bounded single-owner repair diagnostic | 33,407 tokens, eight calls; the intended fact-check article changed and was read back | Evidence of actual repair execution, not semantic acceptance or a new six-task score |

The acceptance/rework run took 755.409 seconds, including 163.282 seconds of
external review waiting (592.127 seconds excluding that wait). Of 367 tool
dispatches, 249 were directory listings and 11 were writes. The fact-check
article remained byte-identical across all three reviews. These observations
motivated the final managed execution policy.

Four real DeepSeek/Tavily repair diagnostics were retained during that policy's
development. Each copied the same historical failed article and review defects
into an isolated workspace and used one editor contribution, a 60k cap and at
most eight calls. They are not four complete Mid Test runs:

| Diagnostic | Tokens | Model calls | Intended article changed | Finding |
| --- | ---: | ---: | --- | --- |
| Initial phase policy | 28,924 | 7 | Yes | Wrote corrections, but closed search too early |
| Added targeted source window | 32,534 | 8 | Yes | Added URLs, but final text incorrectly denied making changes |
| Added closing write receipts | 33,884 | 8 | No | Wrote an unrelated input placeholder; this exposed the need for a declared-target gate |
| Final target gate | 33,407 | 8 | Yes | Wrote the correct target; final text acknowledged delivery |

The four diagnostics consumed 128,749 tokens and 31 model calls in total.
Their mixed results are reported rather than selecting only successful writes.
The final article distinguishes false positives from false negatives, withdraws
an unsupported Turnitin attribution, adds URLs and scopes its conclusion.
However, primary-source checks remain incomplete, source retrieval claims need
qualification, some generalizations lack direct support, and the final textual
summary still incorrectly denied a recorded read-back. The semantic status
remains **unverified**. Neither a file hash change nor an agent's publication
claim is an acceptance verdict.

## Evidence and limits

The repository includes the implementation, deterministic tests, integration
documentation and this results summary. Raw model/tool traces, full workspaces,
generated articles/code and local source snapshots remain in ignored `results/`
directories; they are not included in the PR or required for offline tests:

- `results/midtest-budget-fix-2026-11-23/`
- `results/midtest-delivery-retest-2026-11-23/`
- `results/midtest-acceptance-retest-2026-11-23/`
- `results/delivery-execution-fix-2026-11-23/`

The last directory contains the final regression XML, all four repair probes,
`IMPROVEMENT_REPORT.md`, and `target-gate-source-manifest.json`. Local paths in
the detailed development docs refer to retained operator evidence, not files
available in a fresh clone. No credentials are included in this summary.

A full six-task Mid Test has not been repeated on the final target-gate policy.
Production CLI/chat adoption, candidate copying/final packaging, trusted review
coverage, full-page/paged reads and discussion-memory integration remain joint
integration work. Independent local progress does not establish that the whole
course project is complete or assign overall failures to a particular member.
