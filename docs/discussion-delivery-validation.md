# Discussion Delivery Hardening and Local Validation

The measurements below are the **earlier** six-case live baseline. A subsequent
local [acceptance/rework lifecycle](discussion-acceptance-loop.md) is now
available through an opt-in runner. Its subsequent six-case run is recorded
separately in [acceptance/rework validation](discussion-acceptance-loop.md);
the figures below must not be presented as that later implementation's results.

This Role A validation records the local development stage following the first
budget fix, before publication of the cumulative discussion/delivery PR. The original Mid Test persona and task texts remain intact.

## Changes and boundaries

- A general build/complete/review/deliver policy now asks each persona to
  produce its own contribution, inspect actual candidate files, and distinguish
  verified work from plans, forecasts and blockers. A compact ledger records
  successful file writes and code exit codes. It never treats exit zero as
  proof that all requirements passed.
- Non-final rounds receive a soft 40% share of the remaining task budget;
  unused budget rolls forward. On pressure, research stops but a bounded
  write/check opportunity remains if no file was written. A final text slot
  still has no tools. At most two searches are dispatched per contribution;
  surplus calls in a batch receive a readable rejection.
- A tentative text reply without a successful file write receives at most one
  action reminder when write access and steps remain. Human answers and
  permission decisions are never invented.
- Small source files can remain intact in the prompt, up to 12,000 result
  characters within the shared tool-result window. Search excerpts retain more
  content than the first budget fix. Large-file pagination and full web-page
  retrieval still need tool support; clipping cannot replace those features.
- `ModelOutputLimitError` preserves provider usage for discarded truncated
  responses. The discussion loop charges that usage (or its reservation when
  usage is absent) and permits one smaller-response retry. Partial tool-call
  arguments are never parsed or executed. Further truncation, exhausted steps
  or exhausted budget terminates the contribution normally through its stop
  reason. The existing task-graph runtime's error behavior is unchanged.
- The frozen discussion models and `run()` signature remain unchanged. The
  optional constructor setting `max_search_calls_per_turn` defaults to two.
  No B/C/D production directories were edited by this change.

The local evaluation adapter awaits `emit`, copies ordinary candidate files
between isolated workspaces, and records producer/consumer/hash provenance.
Copies live under `inputs/shared/<producer>/`; prior-task snapshots live under
`inputs/task1/<producer>/`. They are **unaccepted candidates**, not accepted
artifacts. D owns adopting this adapter behavior in the production group flow.
A does not clear workspaces or directly copy files in the discussion engine.

## Budget experiments

The agreed defaults remain 60,000 task tokens, 2,048 output tokens and four
rounds. Increased limits are explicit evaluation settings, not hidden default
changes or evidence that a task succeeded.

1. A 120,000 / 4,096 software pilot produced source code and real Docker
   execution, but early turns consumed most of the budget and neither goal
   reached complete delivery.
2. A revised 240,000 / 4,096 run exposed a distinct failure: generated code
   exceeded the per-call output cap, and the client stopped the task despite
   substantial unused total budget. That finding motivated output-limit
   recovery and a larger per-call cap.
3. The final retest uses **240,000 task tokens / 8,192 output tokens**, four
   rounds, eight model steps per contribution, 120-second contribution timeout,
   and two searches per contribution. It uses DeepSeek and Tavily plus the
   Docker execution backend. No generated code runs on the host.

Example assembly (with the caller's IDs, goal, personas and tools):

```python
config = DiscussionConfig(
    conversation_id=conversation_id, run_id=run_id, task_id=task_id,
    goal=goal, speakers=speakers, moderator=speakers[0],
    max_task_tokens=240000, max_output_tokens=8192, max_rounds=4,
)
engine = RoundRobinDiscussionEngine(
    executor.tool_definitions(), max_search_calls_per_turn=2,
)
```

The experiments also change workflow and candidate-copy behavior, and provider
outputs vary. They do not isolate budget as the sole cause of any improvement.
The S2 runs include the user's actual earlier request to choose a recent event
for the campus website and social media; other human answers are not scripted.

## Verification and reproduction

```bash
python -m pytest -q tests/test_discussion.py tests/test_discussion_context.py tests/test_discussion_delivery.py tests/test_client.py
python -m pytest -q
python -m ruff check .
git diff --check
python -m harness.memory_recall --output results/m2-check.json
```

The current focused suite passed 53 tests. The complete suite passed 414 tests
with Docker available and no skips. Ruff passed. The unchanged C memory probe
returned English 3/3 and Chinese 3/3 at top-1; this does not establish that the
discussion path uses memory.

All six live retest cases reached `max_rounds`, with 16 completed contributions
each and no model errors. Total returned usage was 1,259,307 tokens across 323
model calls; task execution took 792.359 seconds. There were 24 successful file
writes, 18 code executions and 81 successful reads from handed-off inputs.
These are execution metrics, not evidence that six goals passed.

| Task | Returned tokens | Local requirement review |
| --- | ---: | --- |
| Booking system | 214,905 | Core functionality passed nine independent Docker controls and ten agent-written tests. QA documentation still contains inaccurate commands. |
| Usage dashboard | 208,896 | Partial: seven of ten independent checks passed; browser interactivity is absent and a daily threshold is applied to period totals. |
| Recent campus AI news | 203,626 | Goal failed: the delivered explainer contradicts the user's explicit request for a specific recent event. |
| AI-detector fact-check | 209,024 | Partial: a structured article exists, but primary-source checks and citations remain unresolved. |
| Simulated investment proposal | 209,456 | Partial: allocation and introduction exist, but equity classification conflicts and claimed final files were not actually written. |
| Semiconductor HTML slides | 213,400 | Partial: 12 slides with navigation source exist; research attribution is unfinished. Browser URL policy blocked local-file opening, so actual browser navigation was not verified. |

The local core-goal completion estimate is **1/6**, not an official score.
The detailed Chinese report is saved at
`results/midtest-delivery-retest-2026-11-23/DELIVERY_TEST_REPORT.md`, with
machine-readable reviewed metrics and independent checks alongside it.
After the six-case process started, one reminder wording was corrected so a
missing `file_write` does not falsely deny an earlier `code_exec`. That change
does not alter control flow; the final 414-test suite includes its regression.
The six live cases were not repeated for this wording-only change; their source
snapshot and the exact difference are retained in the local report.

Local run evidence is saved separately:

- `results/midtest-delivery-2026-11-23/`: 120,000-token pilot.
- `results/midtest-delivery-final-2026-11-23/`: intermediate 240,000 / 4,096 run,
  before the output-limit repair; despite its directory name, not the final candidate.
- `results/midtest-delivery-retest-2026-11-23/`: current retest, source snapshot,
  candidate hashes, model/tool traces, candidate handoffs and per-task artifacts.

Results directories are Git-ignored. Use a new sibling directory when copying
the local driver for another run. Its output directory must not already contain
the scenario. Credentials are loaded from the existing local environment file
and are never included in the saved configuration or report. A completed round
or a written file alone is not a passed task; inspect requirements and artifacts.

## Project completeness and responsibility

The repository has a substantial backend, but its end-to-end integration is
not complete. A local test adapter must not be presented as a finished
production CLI feature.

| Owner | Implemented foundation | Remaining work supported by current code/evidence |
| --- | --- | --- |
| A | Task-graph scheduling, approval gates, runtime, discussion loop, context/budget guards | The original discussion path lacked verdict-directed rework. A later opt-in delivery runner adds that lifecycle and a separate usage report; production entry-point adoption and trusted review coverage still need integration. This is ownership of a structural gap, not proof that A caused most task failures. |
| B | File tools, Tavily search, Docker code execution, local Git controls | Search returns clipped snippets, with no full-page retrieval tool; file reads lack offset/range parameters. These limit evidence review and large-code editing. Wrong model paths and failed generated-code assertions are not automatically B failures. |
| C | Isolated JSONL memory, bilingual retrieval, Worker reflection and recall evidence | Discussion memory is not connected; persona collaboration and learning need joint A/C/D integration and outcome-based tests. The test personas are supplied by the Mid Test packet, so their wording cannot be blamed on C's repository profiles. |
| D | Message bus, CLI goal flow, traces, verdict/metric tools and development harnesses | `ui/group_run.py` invokes the task-graph `run_goal`, not the discussion engine. Default `ui/goal.py` disables memory and offers file/search tools, not the complete discussion tool set. Live discussion routing, candidate handoffs, complete usage and requirement-level acceptance need production integration. |

The discussion loop disables memory by design and refuses protected tools
without an approval channel. Those choices preserve its current boundary but
do not satisfy the entire final-project specification by themselves. Likewise,
existing task-graph HITL and memory tests cannot prove those features operate
in the new discussion flow. No percentage of overall completion is inferred
from unit-test counts.

A's prompt clipping also contributes to incomplete source-file review; B's
current file tool can return far more text than A sends to the model. Paging
therefore requires joint A/B work, not a claim that B failed to read the file.
