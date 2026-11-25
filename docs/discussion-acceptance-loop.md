# Discussion Acceptance and Bounded Rework (Role A, Local)

This adds an opt-in lifecycle around the existing discussion engine. It does
not replace the production CLI, change the original discussion wire contracts,
or establish that the six live Mid Test goals now pass. The measurements below
record local development before publication of the cumulative discussion/delivery PR.

## Why this belongs to A, and what the evidence does not establish

`docs/contributions/Role_A.md` assigns A the contracts, scheduler, execution
loop, acceptance integration, defect feedback and bounded retries. The existing
`orchestrator/scheduler.py` already checks task acceptance, returns defects to
Workers and rechecks retried tasks. Its final goal verdict is also explicit.
The standalone `orchestrator/discussion.py` originally stopped by round/token/
time/step limits without a goal acceptance gate or verdict-directed rework.

The earlier six-case report found missing claimed final files, a news explainer
that contradicted the requested event report, and dashboard defects that
remained at delivery. Those findings support adding an orchestration gate.
They do **not** isolate causes or assign a percentage of failures to any member.
The previous phrase that A bore the main responsibility for overall completion
was too broad: A owns this missing lifecycle; model quality, tools, evidence,
review coverage and production integration all affect end-to-end success.

## What is implemented

1. `protocol/delivery.py` defines separate delivery contracts. A plan binds the
   exact original goal and initial human instructions. Each requirement has
   a stable ID, owner, description, exact `outputs/` artifact paths, machine
   rules and a semantic-review flag. The caller supplies an approved plan;
   this change does not automatically derive complete criteria from free text.
   Profiles and plans are copied, and agents cannot edit the acceptance plan.
2. `orchestrator/delivery.py::DeliveryDiscussionRunner` runs an initial group
   attempt and invokes acceptance. Failed requirements go only to their named
   owners for one contribution each per repair round. Exact paths and defects
   are injected along with the original request. Already passing requirements
   are rechecked after every repair, including work by other owners.
3. `orchestrator/delivery_checks.py` verifies actual ordinary files, records
   SHA-256/size evidence, rejects symlink components and files over 20 MiB, and
   calls the supplied `Verifier`. A claim in chat is never file evidence.
   Hashes before/after review detect changes during verification. Acceptance
   is of the current workspace snapshot; final archive consistency still
   requires D's packaging checks. Use fresh per-task isolated workspaces and
   keep them stable while checks run.
4. Semantic review is **required by default**. `DeliveryReviewer` receives the
   unchanged goal, requirement, public history and actual workspace. It must
   return an evidenced verdict; no adapter means `unverified`, not success.
   Verifier/reviewer errors, unsupported checks and uncertain evidence cannot
   create a pass. Infrastructure failures do not trigger pointless model rework.
5. `runtime/discussion_budget.py` accounts across all attempts, including
   incomplete turns, discarded output and failed/timed-out calls. Known usage
   is charged; otherwise prompt plus output reservation is charged. The initial
   attempt receives 60% of the total allowance when repairs are enabled. The
   remainder, including unused initial allowance, is shared across at most two
   repair rounds by default (configurable 0–3). No attempt resets the total cap.
   Provider/tokenizer uncertainty can still produce reported overspend; tools
   from such a response are not dispatched. This is not exact billing for
   provider-side retries hidden inside a client adapter.
6. The managed engine now removes the second round-level reserve and uses
   bounded inspection, production and verification phases. Per-repair artifact
   fingerprints expose unchanged files and feed targeted no-progress defects
   into the next attempt. All participants receive final-owner/path mappings.
   See [managed execution policy](discussion-execution-policy.md) for exact
   quotas, source-check opportunities, added progress fields and validation.
7. Identical `file_list` requests within one contribution reuse the result,
   with `meta.reused_listing=true`. Any potentially mutating call, even a failed
   one, clears the cache. Cache state never crosses agent contributions.
8. New human messages pause the wrapper with `plan_review_required` before
   further model work at the next poll boundary. The original message is
   returned in `pending_human_messages`; callers must revise/confirm the plan.
   Polls occur before attempts, after a recoverable handoff before dispatching
   the next member, between initial discussion rounds, and around acceptance.
   This is not asynchronous interruption during a tool call.
   Protected tools remain blocked; rework does not grant additional permissions.

The runner records per-attempt turns, stop reasons, assessments and usage.
Overall `passed` requires all declared requirements to pass and an `accepted`
stop reason. `partial`, `failed` and `unverified` preserve remaining defects.
Completed turns or rounds are never converted to task completion metrics.
The evidence proves only the coverage of the supplied plan and evaluators.

## Assembly contract for D

The following is an integration example using existing application objects,
not a new CLI command. Import the new modules directly:

```python
from harness.verdict import FileVerifier
from orchestrator.delivery import DeliveryDiscussionRunner
from orchestrator.discussion import RoundRobinDiscussionEngine
from protocol.delivery import DeliveryPlan

# approved_plan_data contains goal, human_instructions, and requirements.
# workspaces maps every config.speakers entry to an existing, non-overlapping
# task-specific workspace. It must match the roots used by executor.
plan = DeliveryPlan.model_validate(approved_plan_data)
verifier = FileVerifier()
runner = DeliveryDiscussionRunner(
    RoundRobinDiscussionEngine(executor.tool_definitions()),
    verifier,
    reviewer=trusted_review_adapter,  # None leaves semantic checks unverified.
    supported_acceptance_types=verifier.supported_acceptance_types,
)
result = await runner.run(
    config, plan=plan, workspaces=workspaces, profiles=profiles,
    history=public_history, model=model, executor=executor,
    human=human_input, emit=publish_turn,
)
```

All original plain user messages must appear verbatim and in order in
`plan.human_instructions`. Do not silently lower requirements to obtain a pass.
Only explicitly deterministic scopes may set `review_required=False`.
For example, checking that a fixed marker exists is not an evaluation of the
truth, timeliness or completeness of a news article.

`FileVerifier()` supports file and text rules. To enable `pytest_passes`, pass
its supported-type set and a runner using B's container isolation and trusted
tests; do not execute generated code on the host or accept a worker's own
"tests passed" message. `DeliveryReviewer` is currently for trusted deterministic
or externally supplied review; independent model calls within the adapter are
outside the supported budget contract. A model Critic needs shared metering
before being added.

If using a provider-specific token counter, pass the same counter to the engine
and the delivery runner. Defaults remain 60,000 total tokens / 2,048 output
tokens; the prior 240,000 / 8,192 setting remains an explicit test override.

`emit` is awaited per completed turn or marked automatic handoff, including
rework, with monotonically advancing round numbers. A `DeliveryHandoffTurn`
has a `handoff_issue` and contains only recorded operations; tally it separately
from completed model turns. Store only public content in chat; keep tool
results in traces. Persist `DeliveryResult` separately. Adapter exceptions
are reported without exposing raw exception messages. D must still trace
tools independently for terminal failures without an emitted turn.
Candidate sharing and final packaging remain caller responsibilities. Copies
under `inputs/shared/` remain unaccepted candidates until the relevant checks
pass; this runner neither copies files nor automatically publishes an archive.

## Independent work and integration boundaries

| Work | A can implement independently | Integration needed |
| --- | --- | --- |
| Requirement/owner contracts, scheduling failures back to owners, bounded rechecks, shared budget, result reporting | Implemented here with adapter controls | D supplies approved evaluation criteria, persists results and adopts the entry point |
| Actual file validation | Implemented with supplied workspace roots and existing verifier | B/D keep roots isolated, inputs stable, and final packages consistent with hashes |
| Semantic and functional acceptance | Interface and failure propagation implemented; no automatic universal quality judge | D supplies trusted cases/rubrics/review adapters; A integrates any future budgeted Critic |
| Full web-text retrieval and paged reads | A can consume a confirmed schema and preserve prompt/evidence limits | B implements tools, pagination/truncation metadata and limits; D adds coverage |
| Discussion memory/reflection | A can later connect verdict-driven lifecycle hooks | C supplies task/agent memory mapping; A/C agree how discussion requirements map to task results; D persists trace and tests outcome-based recall |
| Production discussion CLI, human routing, handoffs, exports and metrics | A provides this callable API and its report | D owns CLI/bus/harness adoption, B supports sandbox/tool assembly, A joins integration testing |

No B/C/D production directories are edited by this work. No existing frozen
`DiscussionConfig`, `DiscussionTurn`, `DiscussionResult` or `DiscussionEngine.run`
signature changes. Existing callers continue to get plain discussion behavior;
they must explicitly choose the new runner to obtain this lifecycle.

## Local validation

```bash
python -m pytest -q tests/test_delivery_lifecycle.py tests/test_discussion_delivery.py
python -m pytest -q --junitxml=results/role-a-delivery-loop-2026-11-23/final-regression.xml
python -m ruff check .
git diff --check
```

The targeted controls use scripted model responses, B's actual file executor
and D's actual `FileVerifier`. They cover targeted repair, missing claimed files,
rechecking previous passes, missing semantic review, original-goal feedback,
human correction before/during/after execution, budget conservation, truncated
and timed-out calls, protected tools, observer failures and symlink rejection.
The semantic fixture verifies feedback routing only; it is not a real-news
fact-checker. The controls do not establish a new live Mid Test completion rate.

Final local results on 2026-11-23: **47 focused tests passed; 448 full-suite
tests passed with zero skips**, including Docker checks. Ruff and
`git diff --check` passed. The first restricted run skipped five Docker tests
because it could not access the Docker socket; those five passed with the
required local socket access, and the final full run includes them. JUnit
evidence is in `results/role-a-delivery-loop-2026-11-23/final-regression.xml`
(Git-ignored). This change adds 34 regression cases relative to the earlier
414-test version; it does not change the historical six-case live score.

## Subsequent six-case live retest (2026-11-23)

The local candidate was subsequently tested against all six original Mid Test
tasks using real DeepSeek/Tavily and Docker, with an external operator-reviewed
test adapter. All six ended at `rework_limit` after two repairs; **0/6 complete
deliveries passed**, with no model-call errors or task-budget stops. This is
not an official score or a production autonomous-Critic evaluation.

The run consumed 1,102,912 returned tokens in 306 model calls. Total task time
was 755.409 seconds, including 163.282 seconds waiting for external assessment
(592.127 seconds excluding that wait). There were 249 actual directory-list
calls out of 367 tool dispatches, versus only 11 file writes. The source snapshot
remained unchanged throughout the run.

Evidence shows the 60% initial allocation combines with the discussion engine's
40% first-round allowance to leave 14,400 tokens per initial speaker at a
240,000-token cap. Some first calls already entered the last-tool-step policy.
Rework also failed to persist corrections despite write tools being available
in earlier steps. The fact-check article's hash remained identical across all
three reviews. Booking code passed ordinary controls but failed an independent
concurrent-booking control; dashboard and HTML slide implementations were absent.
Thus the gate prevents false acceptance, but delivery convergence is unresolved.

The test also introduced fixed delivery owners/entry paths and cleared old
outputs for task2 while preserving task1 copies/history. It is a bundled
integration comparison, not an isolated causal measurement against the earlier
1/6 core-goal baseline. Plans, raw traces, snapshots, independent checks,
metrics and the detailed Chinese report are saved under
`results/midtest-acceptance-retest-2026-11-23/` (Git-ignored), specifically
`MIDTEST_REPORT.md` and `metrics-reviewed.json`. No production fixes were made
during this retest; the next A priorities are budget-allocation composition and
actual repair persistence before spending more tokens on another full suite.
