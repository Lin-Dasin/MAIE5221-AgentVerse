# Discussion Budget Policy

The opt-in [delivery acceptance runner](discussion-acceptance-loop.md) adds a
shared draft/rework budget and reports usage from incomplete contributions in
its separate `DeliveryResult`. The plain discussion behavior and limitations
documented below remain applicable when calling `RoundRobinDiscussionEngine`
directly. Unknown provider usage is reserved conservatively, not measured.

This Role A change addresses premature `token_budget` stops in the standalone
Mid Test discussion engine. It keeps `max_task_tokens=60000`,
`max_output_tokens=2048`, and `max_rounds=4`. It does not change Planner,
`run_goal`, the task-graph Worker, B's tools, C's memory, or D's verdicts.

## Why increasing the budget alone was insufficient

The initial implementation reserved one token per UTF-8 byte plus framing.
Every new request also included all public history and all tool exchanges in
that contribution. Long English discussion and repeated search results could
therefore prevent the next request even with substantial unused budget.
Raising the cap would allow more spending but retain both sources of growth.

There are three separate quantities: the cumulative task budget, the output
cap for one model call, and the provider's context window. Changing one does
not change the others. This fix concerns task accounting and prompt growth;
it does not expand the provider's context window.

## Implemented policy

1. **Prompt estimation and accounting.** The fallback counts ASCII words and
   digits in chunks, handles punctuation/whitespace separately, retains a
   conservative UTF-8 byte count for non-ASCII characters, and adds a 20%
   margin and framing allowances. It is a heuristic, not a matched tokenizer.
   Actual nonzero provider usage replaces the request reservation. Missing or
   zero usage charges the estimated prompt plus the requested output cap.
   If reported usage exceeds the remaining budget, the engine stops before
   dispatching tools from that response. A provider can still exceed an
   inaccurate estimate on a request already sent; this is not a strict billing
   guarantee.
2. **Bounded model context, intact evidence.** Human messages and the current
   goal remain verbatim. Up to eight distinct assistant messages are excerpted
   within 4,000 content characters, prioritizing each recently active speaker.
   Exact repeated messages are removed from this view. The latest complete
   tool exchange keeps its call IDs, arguments and private reasoning; its
   result bodies share 16,000 characters, with at most 4,000 per result except
   file reads, which may retain up to 12,000 to preserve small source modules. Search
   views keep up to three results and prioritize exact URLs. Large generic
   responses retain status and exit codes before a bounded preview. Older
   tool exchanges become labeled observation excerpts, without replaying old
   private reasoning or leaving orphan tool messages. JSON/framing overhead,
   human instructions and latest tool arguments/reasoning are outside the
   excerpt limits, so the total prompt is still checked before every call.
3. **End tool exploration with a usable contribution.** Each non-final round
   receives a soft 40% allowance of the remaining budget; the final round can
   use the remaining balance. The unspent round allowance is divided among
   speakers still to speak. Before offering tools, the loop considers the next
   prompt, output cap, and a closing allowance (estimated final prompt,
   up to 512 output tokens, plus 2,000 tokens of headroom). When that sum no
   longer fits the share, research ends. If no file write has succeeded in that
   contribution, one bounded write/check step remains when globally affordable.
   The next step (or the last configured step) offers no tools and asks for the
   deliverable or an honest partial result. The final reply still
   uses the normal output cap when affordable. If the model nevertheless
   returns tool calls, they are not executed. This heuristic reserve cannot
   guarantee a reply after arbitrarily large generated arguments or reasoning.

The prompt asks agents to do concrete work, identify real artifact paths and
checks, avoid repeating plans, and distinguish the current assignment from
earlier tasks. It does not require pretending that an incomplete task is done.

Full original results remain in completed `DiscussionTurn` objects. D should
continue recording model and executor traces: an interrupted contribution is
absent from `DiscussionResult.turns`, so summing completed turn tokens alone
does not measure all incurred costs. The frozen result schema is unchanged.

## Optional provider-matched counter

The constructor accepts a synchronous callable with this shape:

```python
def provider_prompt_tokens(messages, tools) -> int:
    # Count the actual provider wire format, tool schemas and framing with a
    # tokenizer matched to the deployed model. Supply the implementation here.
    ...

engine = RoundRobinDiscussionEngine(
    executor.tool_definitions(),
    prompt_token_counter=provider_prompt_tokens,
)
```

The return value must be a non-negative integer; booleans are rejected.
This is a constructor-only extension. `DiscussionEngine.run()` and
`DiscussionConfig`, `DiscussionTurn`, and `DiscussionResult` keep their agreed
signatures. No tokenizer package or additional provider API was introduced.

## Historical validation of the first budget fix

The measurements below describe the earlier 60,000-token candidate, before
delivery hardening and output-limit recovery. Current results and test counts
are recorded in [Delivery validation](discussion-delivery-validation.md).

The offline regression suite verifies actual local file writes after long
history/search input, exact human instructions, intact evidence, valid bounded
JSON, source URLs and exit codes, complete tool-call pairing, final-step tool
suppression, shared accounting, missing usage, reported overspend, and refusal
of oversized protected human input. A scripted model proves orchestration
behavior, not real-model task quality.

```bash
python -m pytest -q tests/test_discussion.py tests/test_discussion_context.py
python -m pytest -q
python -m ruff check .
git diff --check
```

On 2026-11-23, the focused suite passed 29 tests and the full suite passed 400
tests with Docker available, without skips. Ruff passed.

Exact replay using preserved pre-fix modules reproduced all six original
stops. Applying the new prompt view and counter to each blocked context gave:

| Original case | Remaining budget | Old reservation | New reservation |
| --- | ---: | ---: | ---: |
| S1 task 1 | 18,931 | 26,366 | 6,578 |
| S1 task 2 | 26,932 | 37,525 | 6,924 |
| S2 task 1 | 36,276 | 56,755 | 10,690 |
| S2 task 2 | 48,738 | 52,407 | 11,100 |
| S3 task 1 | 25,236 | 30,898 | 6,312 |
| S3 task 2 | 40,864 | 59,879 | 11,417 |

All six revised requests fit with the full 2,048-token output allowance.
This is a prompt-only comparison at the old failure points; it neither
generates new responses nor demonstrates six completed tasks. Local evidence
is under `results/midtest-budget-fix-2026-11-23/`; original evidence remains
under `results/midtest-2026-11-23/`. These directories are Git-ignored.

The live DeepSeek + Tavily S2 regression used the same default task limits:

| Case | Baseline completed contributions | Revised completed contributions | Baseline returned tokens | Revised returned tokens |
| --- | ---: | ---: | ---: | ---: |
| S2 task 1 | 4 | 12 | 23,724 | 55,983 |
| S2 task 2 | 0 | 11 | 11,262 | 56,180 |

Both revised tasks still stopped on `token_budget`, after using roughly 93%
of the cap. There were no model errors, and returned tokens matched completed
turn accounting in these two runs. Neither produced an artifact file, so this
is **not an end-to-end success**. There were 42 model calls, 40 executed tool
calls (34 successful), and 176.736 seconds of task execution in total. Longer
discussion and higher spending are not themselves completion improvements.

This is an observational regression, not a controlled model-quality A/B:
search/model outputs can vary, and the revised S2 history includes the user's
actual reply, “按原任务：选择近期具体事件，面向校内网站及社交媒体”. It was
provided before the run; no new human answers were synthesized. The model still
spent turns on source verification, proposed work and blockers. Full-source
retrieval, persona behavior and editorial acceptance remain separate from
correcting the premature budget reservation.

## Remaining limits and integration

- A legitimate budget stop is still possible. The allocation does not promise
  all rounds, all speakers or all deliverables will finish. Preserve partial
  results and evaluate the actual artifacts separately from the stop reason.
- Excerpts can omit relevant evidence. They are explicitly marked; omitted
  URLs/paths are never replaced with shortened identifiers. The model must
  not treat an excerpt as a full-source review. A robust full-page retrieval
  capability is a separate B-owned integration topic.
- Human messages are not silently truncated. An oversized goal, human history,
  or latest call/reasoning may still make the next request unaffordable.
- D owns live chat, cross-task workspace handoff, model/tool trace persistence,
  and task-quality scoring. C owns personas and memory. This fix preserves
  those boundaries and the existing permission checks.
- If measured, useful work still needs a larger task budget, select a documented
  cap for that workload after this fix and record it in D's comparison. Do not
  disable the cap or silently change benchmark settings to claim success.

## Managed delivery allocation update

The opt-in delivery runner now uses a separate managed execution policy to
avoid multiplying its 60% initial allowance by the standalone engine's 40%
round share. Inspection/production/reporting use different output caps and
retain bounded writing/checking opportunities. Standalone discussion behavior
and task defaults are unchanged. See [managed execution policy](discussion-execution-policy.md)
for formulas, hard caps, source-check limits and the current validation scope.
