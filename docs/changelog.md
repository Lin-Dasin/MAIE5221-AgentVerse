# Changelog

This file records actual changes to documentation, contracts and the integration entry point. Day-to-day progress and code contributions are evidenced by genuine Git commits, PRs and tracking issues. This log does not imply that changes have already been committed, merged or approved by the team.

## 2026-11-23 — Managed Delivery Allocation and Persisted Repair

- Removed the second round reserve inside `DeliveryDiscussionRunner`'s managed
  engine policy. The first four-person contribution at 240k now has a 36k
  ceiling instead of a 14.4k work share; the total cap and repair reserve remain.
- Added bounded inspect/produce/verify/report tool phases, adaptive output
  limits, dispatch-enforced inspection quotas and a targeted source-check
  opportunity during repair. Protected tools remain unavailable.
- Added per-repair declared-file fingerprint comparisons, explicit no-progress
  feedback, team-wide final-owner/path mapping and recorded candidate handoffs.
  Changed bytes never replace acceptance. Current write receipts remain in
  final prompts; plain-text DSML is never executed.
- Added deterministic behavioral regressions and bounded real DeepSeek/Tavily
  repair diagnostics using the previously unchanged fact-check article.
  These probes are not a new full Mid Test score. See
  [managed execution policy](discussion-execution-policy.md) for scope and
  [cumulative validation summary](report/role-a-discussion-validation.md) for
  the published results and retained local evidence locations.
  Original discussion contracts and B/C/D production code are unchanged.
- Final validation: 80 focused and 468 full-suite tests passed with Docker and
  zero skips; Ruff and whitespace checks passed. The final bounded live probe
  persisted the intended article, but does not establish semantic acceptance.


## 2026-11-23 — Discussion Acceptance and Bounded Rework (Local)

- Added an opt-in `DeliveryDiscussionRunner`, separate delivery contracts and
  reviewer port without changing the frozen discussion API. Caller-approved
  requirements bind the original goal/instructions, owner and actual paths.
- Check file existence/hash and supplied machine/semantic verdicts, route failed
  requirements only to their owners, and recheck all requirements after repair.
  Missing review remains unverified; retries default to two rounds.
- Share one budget across attempts, including incomplete/error calls, reserve
  repair allowance, preserve human corrections and block protected tools.
  Reuse repeated directory listings until a possibly mutating call occurs.
- Added lifecycle regressions with real B file tools and D file verification.
  These controls do not establish a new live Mid Test completion rate.
  See [integration details and boundaries](discussion-acceptance-loop.md).
- Validation: 47 focused tests and 448 full-suite tests passed, including
  Docker, with zero skips; Ruff and diff whitespace checks passed.

## 2026-11-23 — Discussion Delivery and Output-Limit Recovery (Local)

- Added general build/complete/review/deliver guidance, a write/check evidence
  ledger, bounded research, one action reminder, and soft per-round allowances.
  Budget pressure preserves a bounded write/check opportunity before final text.
  Source-file views retain more content for editing; original tool evidence is
  unchanged. No task-specific solutions were embedded in the runtime.
- Added `ModelOutputLimitError` with usage metadata and one budget/step-bounded
  retry for a shorter response. Truncated tool arguments are never executed.
  The existing task-graph loop and frozen discussion signatures remain unchanged.
- Tested explicit 120,000/4,096 and 240,000/4,096 task/output caps, then used
  240,000/8,192 for the current six-task retest. Shared defaults remain
  60,000/2,048. A local adapter records candidate copies across private
  workspaces; no B/C/D production modules were edited.
- Validation: 53 focused tests, 414 full-suite tests with Docker, Ruff and
  whitespace checks passed. Independent Docker evaluation of the generated
  booking CLI passed nine controls and its ten agent-written tests. Dashboard
  evaluation passed seven of ten controls and exposed missing browser
  interactivity and an incorrect per-day threshold. The six live cases all
  reached four rounds, consuming 1,259,307 returned tokens in 792.359 seconds;
  local core-goal acceptance was 1/6, not six successes. See
  [delivery validation](discussion-delivery-validation.md) for limits and
  responsibility boundaries; artifacts alone do not establish task completion.

## 2026-11-23 — Discussion Budget and Context Fix (Local)

- Kept the 60,000-token default and the frozen discussion contracts. Replaced
  the byte-per-token reservation with an injectable prompt counter and a
  language-aware fallback estimate with a margin. Returned provider usage
  remains the accounting source; missing usage charges the full reservation.
- Added bounded assistant-history and tool-result views while preserving human
  instructions, the current goal and full evidence. Latest tool calls retain
  their IDs, arguments and reasoning; older complete exchanges become labeled
  observations. Status, exit codes and exact source links take priority.
- Added a soft per-speaker exploration share and a reserved final text phase.
  The last model step offers no tools, and any tool calls returned in this
  phase are rejected before dispatch. Prompts now ask for concrete deliverables
  and honest partial results instead of repeated planning.
- Validation: 29 discussion tests and 400 full-suite tests passed, including
  Docker controls; Ruff passed. All six original blocked prompts fit their
  remaining budgets after the context/counter changes in an offline comparison.
  This is not a six-task success claim. Live S2 completed contributions increased
  from 4/0 to 12/11, but both tasks still stopped near 56,000 returned tokens and
  produced no artifact files. End-to-end completion is not established. See the
  budget policy for the comparison caveats. No B/C/D production modules were
  changed by this fix.

## 2026-11-23 — Mid Test Discussion Engine

- Added Role A's shared discussion models and ports plus a standalone runner
  with rotating speakers, public history, round-boundary human input, and
  turn callbacks. The existing task graph runtime and Planner entry points
  are unchanged.
- Added a per-persona tool loop with shared discussion token limits and a
  per-turn step and time limit. Tool details stay out of public history, and
  tools requiring approval are not executed on this path.
- Added offline discussion coverage and documented the D adapter boundary.
  Formal scenario runs and cross-task workspace handoff remain integration work.

## 2026-11-21 — Worker Memory Injection and Trace Evidence

- Added an optional `MemoryObserver` port and metadata-only `MemoryEvent` to A's
  runtime and scheduler. Retrieve, model-client injection and reflection report
  outcomes without recording private context or prompts. Existing Memory and
  Observer adapters remain compatible; callback failures become warnings.
- Added `scripts.worker_memory_demo` with a shared C memory store, two distinct
  Chinese tasks, real file writes and acceptance, and checks correlating context
  digests, trace events and reflected task IDs. The recall task has no file-read
  access or marker in its instruction. Its goal passes only with the correct
  marker. The disabled-recall control requires a missing-marker verdict, rather
  than treating a model or tool crash as expected failure.
- Four lifecycle regressions passed, including mocked HTTP verification of the
  enabled and disabled requests, model failure attribution and observer failure
  isolation. Both offline demo controls passed. No new live-provider result is
  claimed; `--live` and default-profile P2 require exported DeepSeek credentials.
- M2 on the merged C revision reproduced English 3/3 and Chinese 3/3. This is an
  offline retrieval-only check. Section 5 and adoption of the optional trace port
  in D's main adapter remain D-owned follow-ups.
- Full verification: 303 tests passed, Ruff and Git whitespace checks passed,
  T2/T3 controls passed, and the offline Dev fixture subset passed 4/4 goals and
  8/8 nodes. The CLI regression now expects an invalid plan to exhaust three model
  calls without running tools.

## 2026-11-21 — T2/T3 Role A Integration Regressions

- Verified the public `build_service()` factory with an explicitly advertised
  pytest verifier: real trusted tests fail, send defects into retry, recover without
  extra logical nodes, block dependents, and fail the goal independently of node success.
- Added approval-boundary regressions for refusal, timeout, wrong request IDs and
  multiple protected calls in a single model response. Retry budgets cannot bypass
  denial; queues and sessions are released and every push needs its own decision.
- Validation: 11 tests passed across the new A suites and D's T2/T3 suites. The
  production decision/approval flow needed no change. B's runner, Git tools and
  D's verdict/metric implementations remain owned by their respective authors.
- Updated integration/architecture guidance for opt-in pytest support, failure
  semantics and approval order. T2/T3 results cover trusted host fixtures and local
  Git transport only.

## 2026-11-21 — Planner Repair and P2 Task Instructions

- Based on develop `68aee5f` (including C's #25 and D's #26).
- Added positive/negative examples for workspace outputs, archived inputs, input
  destinations and goal paths. Worker task titles must retain the original topic,
  exact markers, format and read/copy requirements.
- Raised bounded generation from two to three attempts and reserved all three
  final-generation calls before requesting more specialist bids. Global call,
  token and timeout budgets are unchanged.
- Reject known node acceptance archive/workspace path mix-ups before execution,
  without rewriting dataset criteria or blocking declared input checks.
- Validation: 37 Planner/runtime/preflight/P2 tests passed, including third-attempt
  repair through `run_goal()` with real file verification and handoff, exhausted
  budgets, negotiation reservations and legal workspace paths. No live API call
  was made; default-profile P2 still needs credentialed remeasurement.

## 2026-11-11 — Role A: Shared Model Client and Executable Plan Preflight

Two integration defects were prioritized: the live smoke script duplicated the
model client and bypassed its retry policy, and inconsistent artifact references
were discovered only after paid execution. In particular, the script checked
`outputs/notes.md` against an archive containing `notes.md`.

### Changes

- `DeepSeekClient` owns `thinking="disabled"|"enabled"`, also read from
  `LLM_THINKING` with default `disabled`. Requests explicitly send this mode.
  Optional `reasoning_content` fields in `ChatMessage`/`ModelResponse` preserve
  provider assistant history through tool/approval/delegation turns and Planner
  repair. Enabled requests replay the field; disabled requests omit it. Existing
  adapter method signatures and bounded transient retries remain intact.
- The smoke probe and workflow use the production client. Removed
  `NoThinkingClient`, raw error-body output and duplicate HTTP serialization.
  The script now calls `run_goal()`, retains graph/trace output and always closes
  its client. Final file acceptance uses archive-relative `notes.md`.
- Added shared `PlanPreflight` to planning, fixed-graph execution and child
  insertion. It checks graph/budget contracts, registered Workers when configured,
  input references against upstream published outputs, goal file checks against
  final outputs, and supported acceptance-rule types. Publication and preflight
  share `archive_path()` to preserve nested paths and remove one `outputs/` prefix.
- Both draft and negotiated plans can repair contract errors within existing
  call/token limits. Unsupported dataset rules fail before any model call;
  supplied goal criteria are not weakened or rewritten. Fixed invalid graphs fail
  before workspace/Worker side effects; invalid children do not enter the graph.
  Planning failure records retain the diagnostic needed to identify the bad path.
- `build_service(supported_acceptance_types=...)` explicitly declares the supplied
  verifier's implementation. Defaults match D's `FileVerifier`: `file_exists`
  and `text_contains`. `pytest_passes` still needs B/D's isolated implementation.
  This change adds no tool, memory-store or verdict implementation in A's modules.
- Updated the integration/setup guides, architecture overview, Section 2,
  Role A's integration reference, README and environment example. Corrected stale
  claims that all B/C/D adapters were missing. Team review and integration remain
  separate from publishing the Role A branch.

### Validation

Reproduce from the repository root after installing `.[dev]`:

```bash
python -m pytest -q
ruff check .
python -m scripts.live_smoke --help
python -m harness.run --suite offline-smoke --mock \
  --work-dir results/role-a-regression/work --output results/role-a-regression/report.json
git diff --check
```

- Full local suite: **156 passed** (131 existing plus 25 new cases).
- Ruff and whitespace checks passed; the smoke entry point imports and shows help.
- New coverage includes thinking configuration, reasoning replay after approval,
  bounded HTTP retries, malformed envelopes, draft/negotiated plan repair,
  authoritative dataset rules, rejection before execution, and invalid child
  insertion. The smoke-script regression uses mocked HTTP with B's actual file
  tool and D's actual verifier/JSONL trace, checking the final archived file.
- Offline harness: **3/6 goals, 6/10 subtasks**, unchanged from the deliberately
  mixed success/failure fixture baseline. These are not real-model completion rates.
- No live model/search API calls or formal dataset evaluations were performed.
  Provider compatibility and performance still require credentialed live runs.

## 2026-10-29 — Documentation and Interface Alignment, Integration Guide and Shared Assembly Entry Point

Background: comparing the course specification, the project-plan attachment and the current Role A code revealed inconsistencies in memory interfaces, paths, progress-record locations, some status descriptions and official evaluation arrangements. Cross-role documentation was aligned at the user's request and awaits normal team PR review.

| Area | Before | Change and rationale |
| --- | --- | --- |
| Project-plan source | The project-plan attachment was outside the repository and differed from later code/documentation | Added `docs/project-plan.md` as the v3.4 local alignment draft |
| Contract authority | Different documents gave precedence either to code or to the old plan | Course specifications/official notices define requirements; the plan defines responsibilities; `protocol/models.py` and `protocol/ports.py` define current callable models/signatures; gaps are recorded separately |
| C's public interface | Documentation described synchronous `retrieve_memory()`, `build_memory_context()` and `write_reflection()`, while A called different async methods | Standardized on async `retrieve(agent_id, task) -> str` and `reflect(task, result) -> None`; record-level helpers may remain internal to C, with no second runtime API |
| Memory fields | C's document assumed separate description/type fields, a mandatory CriticResult and complete traces | Aligned with actual Task/TaskResult fields; `result.critique` is optional; additional tags and trace correlation are adapter work |
| Memory location | C's example used `memory/data/<agent_id>.jsonl` | Standardized on `workspaces/<agent_id>/memory/<agent_id>.jsonl`, consistent with isolation and lifecycle conventions |
| Artifact archives | Some documents omitted run_id | Standardized on `artifacts/<run_id>/<task_id>/`; clarified the difference between task-verification and goal-verification roots |
| Progress and change records | The external plan required `docs/weekly/`, while the repository convention excluded it | Progress goes in PRs/tracking issues; documentation and contract changes go in this file |
| Individual plans | D's file was in the docs root; Wang's plan had a duplicate outside the repository | Moved D's maintained content to `docs/contributions/Role_D.md` and retained a redirect; replaced the external Wang copy with a link to the maintained plan to prevent divergence |
| Capability status | Some descriptions could be read as claiming target capabilities were already implemented | Explicitly identified outstanding B/C/D adapters, chat routing, automatic Planner memory, the independent Critic Agent and full traces |
| Evaluation and memory experiments | Test releases/run counts were inferred without confirmation; the second run was expected to improve unconditionally | Official arrangements remain unconfirmed; internal development continues with the dev set; memory experiments retain configuration/raw results and report actual changes |
| Environments and containers | WSL2 was described as mandatory on every platform; long-lived and on-demand lifecycle descriptions conflicted | Standardized the Linux sandbox and mounts, with host-specific Docker/WSL2 choices; containers persist within a session and shared-root runs are currently serial |
| Models and structured output | Stack recommendations did not fully match the current httpx/JSON planning implementation | Documented the existing DeepSeek HTTP client, Worker tool calls and Planner JSON validation; listed HKGAI as future adapter work |
| Service assembly | CLI/harness authors would need to duplicate Runtime, Scheduler and Planner composition | Added `orchestrator/bootstrap.py::build_service()` so D supplies real adapters and shares one model and memory instance |
| Interface explanations | Empty memory, optional verdicts and event-interface scope were easy to misinterpret | Added docstrings to `protocol/ports.py` without changing existing method signatures or model fields |
| Usage guidance | No complete B/C/D integration sequence or live-integration exit criteria | Added `docs/usage-and-integration.md` covering modules, available commands, interfaces, follow-up work, an assembly example and seven integration steps |

Synchronized related descriptions in `README.md`, `docs/README.md`, `docs/overview.md`, the individual role plans, `docs/report/section-2.md` and `.env.example`.

### Validation

- Ran `python -m pytest -q` in the local project environment: **36 passed**.
- Ran `ruff check .`: **passed**.
- Updated the existing `test_goal_to_artifact_with_memory_and_approval` to assemble the service through the new factory, retaining combined coverage of planning, Worker execution, approval, memory calls, actual temporary-file writes and archiving.
- The model, tool adapter, memory and verdicts in that test remain explicit test doubles. These results are not evaluations of real DeepSeek, Docker, search, GitHub or official datasets.
- Statically checked 13 Markdown files, 20 local file links, two Python examples and five JSON examples: no missing links or syntax errors. External-service calls in examples were not executed.
- `git diff --check` passed.

### Outstanding Work

The team still needs production B/C/D adapters, real-model end-to-end integration, private/group chat, memory lifecycles for all agents, per-call traces and a dev-set baseline. Adding the assembly entry point does not establish completion of the first live integration. No Git commits or PRs were created in this change.

## 2026-10-29 — English Translation of the Revised Documents

- Translated the user's current revisions of `docs/project-plan.md`, `docs/usage-and-integration.md` and this changelog into English, preserving the revised scope and omissions.
- Preserved interface names, paths, commands, configuration keys and executable Python examples. Translated natural-language descriptions inside illustrative JSON/YAML examples.
- Updated the Section 2 report's link fragment to match the integration guide's translated assembly heading.
- Validation found no untranslated Chinese in the three documents; table-row and checklist counts were preserved, all 20 local file links and the heading fragment resolved, and all 20 Python source/test files matched their pre-translation hashes. Executable Python examples were unchanged; JSON/YAML examples parsed successfully. `git diff --check` passed.
- This translation changes documentation only; it does not implement additional agent capabilities or create Git commits, pushes or PRs.
