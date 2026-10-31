# Section 2 — Routing and Collaboration Logic

Owner: Role A (Wang / Luhkel-04). Implementation draft, updated 2026-10-29; team review pending.

## Status and scope

The Role A core is implemented locally in `protocol/`, `orchestrator/` and
`runtime/`. Its tests use isolated, explicitly synthetic fixtures and scripted
adapters. This is not a claim that the whole platform has been integrated or that
the dev/mid/final datasets have been evaluated. Code changes still require normal
team review and PR integration.

Implemented:

- Shared Pydantic models with graph, dependency, result and configuration validation.
- Bounded planning, specialist bids, one final negotiation step and schema repair.
- Serial dependency scheduling, finite task retries and explicit terminal states.
- Worker sub-delegation through the scheduler, with depth 1 and at most two children.
- Interactive and policy-driven HITL; decisions are stored in task results.
- A resumable Agent loop with role/tool configuration, memory hooks and model budgets.
- Host-side artifact handoffs, per-run archives and suspended-parent workspace restoration.
- A DeepSeek HTTP client with bounded transient retries and no provider fallback.
- Explicit adapter assembly through `orchestrator.bootstrap.build_service()`.
- Typed integration ports, editable package installation, CI dependency installation,
  and offline tests including a synthetic goal-to-artifact workflow.

Production adapters still required: B's tools and container lifecycle, C's profiles
and memory implementation, D's verdicts, bus/event mapping, CLI and harness. Live
DeepSeek calls have not been verified without a team key. The client does not
implement HKGAI's unconfirmed API. Container isolation remains B's responsibility;
the host-side handoff code is not a replacement for containers.

## Code map

| File | Responsibility |
| --- | --- |
| `protocol/models.py` | Task graphs, structured acceptance, results, profiles, budgets, HITL and model messages |
| `protocol/ports.py` | B/C/D adapter signatures; no tool, memory, evaluator or event implementation |
| `protocol/example_graph.json` | Two-task contract example; not an evaluation result |
| `orchestrator/planner.py` | Planner output validation, specialist bids and bounded plan revision |
| `orchestrator/scheduler.py` | Dependency ordering, task retries, delegation, approvals and finalization |
| `orchestrator/artifacts.py` | Accepted-output publication, input copies and suspended-parent snapshots |
| `orchestrator/approvals.py` | Configured unattended decisions and bounded interactive callbacks |
| `orchestrator/service.py` | Goal/graph entry points for D; structured planning failure results |
| `orchestrator/bootstrap.py` | Shared assembly function for D's CLI and harness, using supplied B/C/D adapters |
| `runtime/agent.py` | Agent context, tool calls, memory retrieval and resumable control requests |
| `runtime/client.py` | DeepSeek request/response translation and transient request retries |
| `runtime/profiles.py` | YAML loading using the shared `AgentProfile` model |

## Shared contracts and review decisions

Import models from `protocol.models` and adapter protocols from `protocol.ports`.
Models reject undeclared fields. Python identifiers and JSON aliases differ only
where necessary: construct `InputRef(destination="inputs/brief.md")`, but serialize
with `model_dump(by_alias=True)` to emit the agreed `as` key.

The following details are implementation proposals for B/C/D review:

1. `TaskGraph` includes `tasks`, `final_task_id`, `goal_acceptance` and `summary`.
   Planner-created nodes have depth 0. Dynamic child nodes are added by A only.
2. `AcceptanceRule` uses `type`, `path` and optional `text`. D implements
   `file_exists`, `text_contains` and `pytest_passes` in its single evaluator.
   Meaningful content requirements need corresponding D-owned checks; a file
   presence check is not a substitute for them.
   For benchmarks, D passes the dataset's fixed criteria through
   `run_goal(..., goal_acceptance=rules)`. These replace any model-proposed goal
   criteria throughout planning and negotiation. Model-proposed defaults are
   available for exploratory development, not as a replacement for test-set rules.
3. `max_retries=2` means one initial execution plus at most two task retries.
   Resuming after delegation or HITL is part of the same attempt. B's tool retries
   remain separate. Worker model-token counts accumulate across task attempts.
   A retry receives only the remaining task token budget.
4. `AgentProfile` contains `agent_id`, `role`, `system_prompt`, `capabilities` and
   `tools`. C owns the YAML contents. B supplies `ToolDefinition` objects; the
   runtime exposes only tools listed by the profile. Tool implementations retain
   their own argument validation and execution limits.
5. The shared failure vocabulary is `FailureCode`; C/D should import it rather
   than introducing alternate failure strings.
6. `memory_enabled=false` suppresses memory retrieval and prompt injection.
   `write_memory=false` suppresses terminal reflection writes. C/D must prepare
   and record initial memory snapshots for comparable experiments.
7. A goal verdict is independent of the fraction of successful graph nodes.
   D checks the final published artifact; failed non-critical nodes remain visible.
8. HITL task-result decisions are authoritative. D receives copies through the
   observer adapter. Private/group visibility and event persistence belong to D.

## Execution flow

`CollaborationService.run_goal()` calls the Planner and then the Scheduler. A
planning failure returns a `RunResult` with `graph=null`, no executed tasks, a
failure code, and timestamps. D must include that run in the goal denominator.

The Planner receives worker profiles and the graph schema, generates a draft, and
requests specialist bids within `max_planning_calls`. It reserves calls for final
planning and a possible repair. The final summary includes the received bids for
D to display in the group chat. No tools run during planning. A caller may supply
C's planner-specific memory through `memory_context`; this text is ignored when
memory is disabled. Automatic planner-memory retrieval remains an integration
point for C; worker-memory retrieval is implemented in the runtime.

The scheduler uses this state flow:

```text
pending -> running -> reviewing -> success
              |          |
              |          +-> pending (bounded retry) / failed
              +-> waiting_children -> running / failed
              +-> waiting_human -> running / failed
dependency failure -> blocked
```

Each task attempt has a wall-clock deadline, including delegation and approval
waiting. Runtime model steps and tokens are bounded independently. Approval
callbacks also have their own timeout. Unhandled adapter failures become a task
failure or a structured run-initialization failure; observer and reflection
failures are retained as warnings rather than changing an accepted task verdict.
Process cancellation still propagates to the caller.

## Sub-delegation and workspace lifecycle

The runtime returns an `AgentOutcome(kind="delegate")`; it never invokes another
Agent directly. The scheduler validates the requested IDs, depth, total node
budget, dependencies and cumulative child count. Child dependencies may refer to
accepted tasks or sibling subtasks. Parent wait relationships participate in cycle
validation. A failed delegated child fails its waiting parent.

Because a child or another task may reuse the parent's Agent container, the
orchestrator snapshots the parent's `inputs/` and `outputs/` before suspension.
After children finish, it restores those directories and copies accepted child
artifacts under `inputs/delegated/<child_id>/`. Agent conversation state remains
in the runtime until terminal completion; resumed model tool calls receive the
child results without restarting the parent attempt.

```text
workspaces/<agent>/inputs/                         rebuilt per task attempt
workspaces/<agent>/outputs/                        rebuilt per task attempt
workspaces/<agent>/memory/<agent_id>.jsonl          persistent, owned by B/C
artifacts/<run_id>/<task_id>/<file>                declared, accepted outputs only
.agentverse-state/<run_id>/<task_id>/              suspended input/output snapshot
```

An output named `outputs/brief.md` is archived as `brief.md` under the task's
archive. Its downstream `InputRef.path` is `brief.md`. Task acceptance paths are
workspace-relative; goal acceptance paths are relative to the final task archive.
Archive publication happens only after D's task verdict passes and all declared
files are present. New runs require unique IDs; accepted artifacts are not reused
from an earlier run with matching task IDs.

Only one run may use a given workspace root at a time. The scheduler uses a
filesystem lease under `.agentverse-state/` and releases it on normal completion
or cancellation. After a process is forcibly terminated, the operator must first
confirm it has stopped before removing the stale empty `.workspace-lease`
directory. Parallel evaluation should use separate roots until B/A implement
coordinated workspace leasing for concurrency.

## HITL behavior

`InteractiveApprover` wraps D's asynchronous UI callback and validates the returned
request ID. `PolicyApprover` accepts an application-configured set of permitted
actions and a mapping of clarification answers. Unmatched requests are denied;
the model cannot supply its own approval policy. A denied or timed-out request
terminates the task with `hitl_blocked` and is not automatically retried.

B marks tools that require approval through `ToolDefinition.requires_approval`.
The runtime pauses before calling such a tool. On approval, it resumes that exact
pending call; other queued calls remain pending. Every approved protected call
therefore has its own decision record. D chooses the private/group channel for
questions and may broadcast appropriate summaries, not internal payloads.

## Adapter checklist

| Owner | Adapter | Required implementation |
| --- | --- | --- |
| B | `ToolExecutor.execute(call, context)` | Dispatch into the declared tool's host/container executor; return `ToolResult` |
| B | `ToolDefinition` registry | Supply tool descriptions, input schemas and approval requirements |
| C | `Memory.retrieve(agent_id, task)` | Return bounded private memory context, or an empty string |
| C | `Memory.reflect(task, result)` | Persist a terminal reflection; `result.critique` contains the final verdict when available |
| C | `profiles/*.yaml` | Supply profiles accepted by `load_profiles()` |
| D | `Verifier.check(rules, root)` | Call the one D-owned acceptance implementation and return `CriticResult` |
| D | `Observer` | Map plan, delegation, task, handoff and HITL callbacks to `bus/events.py`; retain run/task/Agent associations |
| D | Interactive approval callback | Present the question and return the matching `HITLDecision` |
| D | CLI and harness | Invoke the service, preserve failed runs, compute metrics from returned facts, persist traces |

`TaskResult` carries task status, attempts, worker tokens, timestamps, outputs,
failure information, critique, HITL decisions and warnings. `RunResult` carries
the final graph including dynamic children, all task records, the goal verdict,
and overall timestamps. `run_goal()` timestamps include planning. D alone computes
completion rates and elapsed-duration aggregates. These outputs do not yet expose
per-call planner token usage; D's model-call tracing adapter must account for it
before reporting total system token consumption.

## Composition

Use `orchestrator.bootstrap.build_service()` with a shared root, model client,
profile dictionary, tool definitions, executor, memory, verifier, approver and
observer. It returns `CollaborationService`; D calls `run_goal()` or `run_graph()`.
The full, typed application example is maintained once in
[the usage and integration guide](../usage-and-integration.md#7-shared-service-assembly).

The helper does not start containers, load `.env`, instantiate missing production
adapters, replace them with test doubles, or close the caller's model client.
D owns application startup/shutdown with B's lifecycle API. B/C adapters must use
the same root. A missing memory adapter requires both RunConfig memory flags to
be false; the default config enables both flags.

The current DeepSeek adapter requires exported or application-loaded
`LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`; HKGAI needs a separate adapter.

## Remaining lifecycle contracts

The current verifier is D's deterministic acceptance component, not a completed
Critic Agent. A/C still own automatic Planner memory and the Critic Agent's
runtime/memory lifecycle. Worker memory hooks do not complete the requirement for
all individual Agents.

D's Observer adapter handles task events. Chat input/session routing and Agent
replies need additional A/D integration; per-model/per-tool traces need D's
instrumented client/executor adapters and A's context plumbing. These missing
pieces are tracked explicitly in the integration guide rather than represented
as already implemented interfaces.

## Validation

Run `python -m pytest -q` and `ruff check .` in the installed project environment.
Tests cover graph validation, dependency ordering, retry exhaustion, final-goal
independence, child insertion and restoration, HITL decisions and timeout, task
timeouts, memory toggles, planning repair, model wire format, and a synthetic
end-to-end integration. Scripted verdicts in `tests/helpers.py` verify A's use of
D's interface; they are not implementations of D's production evaluator.

Remaining joint validation includes real tools and containers, profiles and
persistent memory, private/group routing, D's acceptance rules and trace mapping,
live DeepSeek behavior, and the official dev/mid/final evaluation datasets.
