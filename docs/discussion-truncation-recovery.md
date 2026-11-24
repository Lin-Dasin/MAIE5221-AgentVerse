# Recovering interrupted delivery handoffs (Role A)

In the managed Mid Test, all five `ModelOutputLimitError` events came from
tool-free requests capped at 512 output tokens, even though the configured
per-call cap was 8,192. One other report printed DSML tool markup as text.
The report reused the production prompt and latest tool exchange, so the
model still had instructions to deliver substantive work and examples of tool
calls. The old final step converted truncation directly into a fatal
`model_error`. Invalid report text likewise ended the discussion. The delivery
runner then stopped before scheduling later members or attempting another
repair. Both newsroom tasks stopped before the final editor ran.

Managed delivery actions now run before a separate, tool-free closing call.
The closing prompt asks for at most three short bullets about saved files,
recorded checks and what is still unverified. Its cap is the minimum of
1,024, the configured output limit and what the member can afford. A
`finish_reason=length` response is discarded; the system may retry once,
asking for a single line. The shared budget charges both calls. No tool call
from either report, or from a truncated work response, can be dispatched.

If the report cannot be recovered, the engine emits a `DeliveryHandoffTurn`
with a machine-readable `handoff_issue`:

| Issue | Meaning |
| --- | --- |
| `report_truncated` | The report was cut off, and a retry was unsuccessful or unaffordable. |
| `invalid_report` | The report was empty, contained a tool call, or tried to print DSML markup. |
| `work_truncated` | Two work responses were cut off after allowing one smaller-work retry. |
| `contribution_budget` | The member could not fit a report or exceeded its share of the attempt. |

The automatic handoff carries the member, round, spent tokens, and only
previously recorded tool calls/results. The adapter can use the existing
`emit` contract to share candidate files. The scheduler polls for a human
correction before continuing to the next member. The delivery runner still
checks actual files and can repair failed requirements within its existing
repair and token limits. No report is a completion or acceptance verdict.
Stop reasons remain strict for true budget exhaustion, human corrections,
provider errors and timeouts. If no member share can fit even a report and
the round uses no tokens, it ends rather than spinning.

This recovery applies to managed delivery with production tools under
`DeliveryDiscussionRunner`; the standalone discussion contract and production
CLI entry point do not opt into it automatically. Role D can record
`handoff_issue` separately from completed turns when integrating the runner.
Tool permissions, workspace isolation and acceptance rules are unchanged.

The regression suite injects consecutive truncations and invalid reports into
the real delivery runner with the file executor and verifier. It checks that
the next owner receives prior write evidence, missing/wrong files do not
pass, rework still runs, and human correction or a spent task budget stops new
calls. See `tests/test_delivery_handoff.py`.

## Validation on 2026-11-23

All 481 regression tests passed with Docker, with zero skips. The focused
discussion/delivery suite passed 93 tests. Ruff and whitespace checks passed.
Thirteen new cases cover last-step retry, separate work/report retries,
invalid/empty/tool-markup reports, receipts and candidate handoff, repair,
member/global budgets, no-spend termination, human correction, and a genuine
provider failure that must remain terminal.

A bounded live probe reran both original newsroom tasks using real
`deepseek-flash`, Tavily and offline Docker. It used one initial group round
and at most one repair per task, a 240,000-token task cap, and an 8,192
configured output cap. Ten separate closing reports used the new 1,024 cap.
All ten completed: 75 model calls overall, zero output-limit errors. All four
members ran on both tasks; the final editor wrote both delivery files and
changed each during repair. The fallback itself was exercised by deterministic
fault injection, since the live run did not trigger it.

This probe checks scheduling, handoff and rework. It is not a new six-task
Mid Test score or a production CLI validation. Its smaller round limit
prevents a direct completion-rate comparison with the earlier
four-round/two-repair run.

Raw calls, tools, reviews, artifacts, metrics and regression XML are preserved
in `results/midtest-truncation-fix-2026-11-23/` (Git-ignored).
The source manifest distinguishes the live probe from the final standalone
compatibility guard, which did not change managed behavior.
