# Usage and B/C/D Integration Guide

Updated: 2026-10-29. Scope: the current local Role A implementation and the next steps for team integration.

This guide distinguishes existing code, implementations required from teammates, and follow-up development involving multiple roles. Passing local tests does not establish that real APIs, containers, tools or official evaluation are complete. The current alignment still requires normal PR review.

## 1. What Is Available Now

The current implementation provides the orchestration core: planning tasks, assigning roles, executing dependencies, pausing for approval, inserting subtasks and handing off files after acceptance. Test doubles can verify these workflows. The B/C/D modules for real search, file operations, code execution, GitHub, persistent memory and the chat interface have not yet been integrated.

| Module | Existing functionality | Boundaries and follow-up work |
| --- | --- | --- |
| `protocol/models.py` | Defines Task, TaskGraph, input references, acceptance rules, tool calls/results, agent profiles, execution results, HITL and budgets | Everyone reuses these models; A coordinates new fields |
| `protocol/ports.py` | Defines async interfaces for tools, memory, verification, approval, events and model calls | Call contracts, not B/C/D implementations |
| `orchestrator/planner.py` | Generates task graphs, collects Worker bids and performs bounded repair of invalid JSON | Planner memory is currently supplied externally; real-model behavior remains unverified |
| `orchestrator/scheduler.py` | Serial dependency scheduling, bounded retries, failure propagation, sub-delegation and resumption | Only one run can use a shared root at a time; shared-workspace concurrency is not supported |
| `orchestrator/approvals.py` | Human callback approval and predefined policy approval | D provides the UI callback; policy mode does not replace a human demonstration |
| `orchestrator/artifacts.py` | Distributes accepted inputs, publishes artifacts and saves/restores suspended parent inputs/outputs | Host-side file handoffs; B implements container and mount isolation |
| `orchestrator/bootstrap.py` | `build_service()` assembles explicitly supplied adapters into a service | Does not substitute for missing modules, start containers or load `.env` automatically |
| `orchestrator/service.py` | `run_goal()` accepts natural-language goals; `run_graph()` accepts predefined task graphs | The former exercises autonomous planning; the latter helps diagnose deterministic integration first |
| `runtime/agent.py` | Worker model/tool loop, memory retrieval, sub-delegation and human-response handling | Currently executes only `role="worker"`; not a general chat entry point for every role |
| `runtime/client.py` | Requests to the official DeepSeek endpoint, tool-protocol translation and bounded retries | Requires a real key; an HKGAI adapter is not implemented |
| `runtime/profiles.py` | Loads and validates `profiles/*.yaml` | C supplies YAML contents; the directory currently contains only a placeholder |
| `tests/` | Offline checks using simulated models, tools, memory and verifiers | Test doubles are for testing only, not formal performance results |

`Verifier.check()` is currently a deterministic acceptance component. The planned independent Critic Agent, its conversations and its memory have not been implemented. The presence of `role="critic"` in a model does not establish that this functionality exists.

## 2. Running the Current Implementation

Run these commands from the `MAIE5221-AgentVerse/` repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check .
```

If `.venv` already exists, activate it directly. The project requires Python 3.11+. Current CI uses Python 3.11; local execution uses the installed interpreter version.

To inspect the existing synthetic workflow test:

```bash
python -m pytest -q tests/test_runtime.py::test_goal_to_artifact_with_memory_and_approval
```

It assembles A's modules through the actual `build_service()` function, but model responses, tools, memory and verification remain explicitly labeled test doubles. This verifies interface connections and control flow, not network integration or goal completion by real agents.

**The following two application entry points are not yet implemented and cannot currently be used as startup commands:**

```text
python -m ui.cli
python -m harness.run --suite dev
```

D will enable them after implementing the corresponding modules. Both entry points should use `build_service()`.

Before real calls, D's application must load a local `.env` or read exported `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` variables. Copying `.env.example` alone does not load them. B's adapters read search and Git configuration. Close the model client and flush logs when the application exits.

## 3. Shared Interfaces and Paths

### 3.1 Authoritative Interface Sources

- Data models: `protocol/models.py`.
- Callable signatures: `protocol/ports.py`.
- Interface semantics: `docs/overview.md`.
- Responsibilities and milestones: `docs/project-plan.md`; official test dates and permitted run counts follow instructor announcements.
- Documentation layout: `docs/README.md`; individual plans reside in `docs/contributions/Role_<X>.md`.
- Record actual progress in PRs/tracking issues, and interface/documentation changes in `docs/changelog.md`.

The record-retrieval and context-building functions previously described in C's document may remain internal to C. The public interface to A is standardized below. There is no released legacy implementation requiring compatibility, so this change does not add a second runtime API.

| Provider | Current signature | Return value or meaning |
| --- | --- | --- |
| B | `async execute(call: ToolCall, context: ExecutionContext) -> ToolResult` | Execute a concrete tool; return an explicit error on failure |
| B | Supply `list[ToolDefinition]` | Tool names, descriptions, parameter JSON schemas and approval requirements |
| C | `async retrieve(agent_id: str, task: Task) -> str` | Prepared private memory ready for prompt injection; empty string when no records exist |
| C | `async reflect(task: Task, result: TaskResult) -> None` | Persist experience for `result.agent_id`; the optional verdict is in `result.critique` |
| D | `async check(rules: list[AcceptanceRule], root: Path) -> CriticResult` | Perform real acceptance checks; counts must be consistent with the verdict |
| D | Implement all five async `Observer` callbacks | Plan completion, task status, delegation, file handoffs and approval decisions |
| D | `async ask(request: HITLRequest) -> HITLDecision` | Interact with the user and retain the original `request_id` |

Consult `protocol.ports.Observer` for its full signatures. It is not a chat-input interface and does not automatically capture individual model/tool calls.

### 3.2 Paths and Lifecycles

All adapters and A's service use the same absolute `root`. The following paths are relative to it:

```text
workspaces/<agent_id>/inputs/                  Rebuilt for each task attempt
workspaces/<agent_id>/outputs/                 Rebuilt for each task attempt
workspaces/<agent_id>/memory/<agent_id>.jsonl   Preserved across tasks
artifacts/<run_id>/<task_id>/                  Accepted files archived by A
.agentverse-state/<run_id>/<task_id>/          Parent inputs/outputs suspended by A
```

B mounts each agent's workspace into its container and resolves tool-relative paths against that workspace. C/D must not introduce parallel path conventions. The repository's `memory/` directory contains implementation code, not actual agent memory.

For example, an upstream task declares `outputs/brief.md`, archived as `artifacts/<run_id>/research/brief.md`. The downstream reference is:

```json
{"from_task": "research", "path": "brief.md", "as": "inputs/brief.md"}
```

The downstream task's `depends_on` must include `research`. Task acceptance paths are workspace-relative; top-level goal acceptance paths are relative to the final task's archive, so they normally omit `outputs/`.

Use a new `run_id` for every run. Identifier permits only letters, digits, underscores and hyphens; do not use a timestamp containing colons directly. The default `RunConfig()` generates a valid ID.

Preserve memory during ordinary batch runs. When resetting it for A/B experiments, C/D should use separate copies and record their starting state. Shared-root execution is currently serial; parallel evaluation requires separate roots and corresponding container, memory and log mappings.

## 4. B Integration: Tools and Sandboxes

### Initial Deliverables

1. A `ToolExecutor` instance that dispatches by `call.name` and locates the workspace using `context.agent_id`.
2. Tool definitions with names matching both the implementations and C's tool allowlists exactly.
3. Container startup/shutdown, workspace mounts, execution limits and error handling, with lifecycle instructions callable by D.
4. Connect search and file read/write first; complete file CRUD, Python execution/testing and Git commit/push in W10.

B/C may agree on tool names such as `file_write` and `web_search`; these are not tools already implemented by A. `delegate` and `ask_human` are reserved Runtime names and cannot be registered again.

Use the shared `ToolResult` model. Success requires `ok=True` and no `error`; failure requires `ok=False` and an `error`. Record `elapsed` in seconds; `meta` may hold agreed fields such as degradation flags. `data` must be JSON-serializable.

Mark operations requiring confirmation with `ToolDefinition.requires_approval=True`. A pauses before execution and records the decision; B remains responsible for argument validation and execution limits. File operations and code run in containers, while search and Git credentials stay in the host-side tool layer. Git targets only the separate test repository agreed by the team.

### Integration Points with A/D

- A's file copies and B's container filesystem view must refer to the same workspace.
- A cleans `inputs/` and `outputs/` before each retry. B must define where project code, tests and Git working directories live and how retries affect them, avoiding dependence on files already rebuilt.
- Sub-delegation currently saves/restores only a parent's `inputs/` and `outputs/`. If tools maintain state elsewhere, B/A must define its lifecycle separately rather than assume it will be restored.
- D's `pytest_passes` must use B's isolated execution capability. If the verification root is an archive, B/D stage its snapshot in a controlled execution workspace. Do not execute model-generated code directly on the host.
- B provides tool-call timings and results; D persists traces. Avoid implementing a competing tool-log format inside A's modules.

## 5. C Integration: Memory and Agent Profiles

### Initial Deliverables

1. Implement an object conforming to `Memory`, using the same root as A/B.
2. `retrieve()` performs reading, ranking, truncation and context assembly internally. Return a string, not a list of records.
3. `reflect()` receives terminal results. A task may fail before verification or be blocked by an upstream failure, so `result.critique` may be `None` and `started_at` may also be absent.
4. Supply `profiles/*.yaml` for the existing `load_profiles()` function.

The following illustrates the configuration format only; tool names must match B's actual registry:

```yaml
agent_id: writer
role: worker
system_prompt: "Complete assigned text tasks; use ask_human when clarification is needed."
capabilities:
  - markdown_writing
tools:
  - file_write
```

Also supply a planning profile with `agent_id: planner` and `role: planner`. Start with a minimal single-Worker workflow, then add at least two Workers with different capabilities to validate collaboration and cross-agent sub-delegation. A role enum does not establish that all execution behavior for that role exists.

### Memory Flags and Experiments

| Configuration | Effect |
| --- | --- |
| `memory_enabled=False` | Disables retrieval and injection of historical memory, including the Planner's `memory_context` |
| `write_memory=False` | Disables terminal reflection writes |
| Both False | Allows other modules to be integrated before a Memory adapter is available |

Both flags default to True in `RunConfig()`. Without Memory, explicitly disable both. To validate actual memory behavior, connect a real adapter and enable the relevant flags.

Control the model, tasks, prompts, acceptance rules and budgets, and record both flags and the starting memory snapshot. Experiments may compare frozen histories or task-by-task evolution in separate memory stores. Report actual effects without presuming improvement.

### Follow-Up Joint Development

- A/C: automatic Planner retrieval/reflection and Critic Agent execution/memory. Workers currently have memory call sites; the Planner has only an external `memory_context` parameter.
- A/C: Workers need the actual IDs and capabilities of available delegates. The Runtime does not yet inject the full peer directory into Worker context; agree on how to supply it.
- C/D: `TaskResult` currently lacks raw tool traces, a complete retry history and `run_id`. When needed, associate these through runtime context and trace adapters rather than reading nonexistent fields.
- Reflection writes currently wait at most `min(5, task_timeout_sec)` seconds. Start with lightweight persistence/template-based reflection. If reflection calls a model, A/C should review the timeout budget and failure handling.

## 6. D Integration: Verification, Messaging, CLI and Evaluation

### Real Verifier

Implement the single rule interpreter in `harness/verdict.py`, conforming to `Verifier.check()`:

- `file_exists`: check the specified artifact.
- `text_contains`: check for the specified nonempty text.
- `pytest_passes`: run the relevant tests through B's isolated executor and inspect the outcome.

Do not use the default-passing `StubVerifier` from `tests/helpers.py` as the real verifier. File existence or a Sources section does not establish factual accuracy. For richer official criteria, D/A must extend the rule model and evaluator implementation.

### Events, Human Responses and Chat

- Map A's five Observer callbacks into D's event model, preserving run/task/agent associations.
- Connect the CLI through `InteractiveApprover(ask_callback)`. Return a matching `HITLDecision`; the system waits for the human response and records timeout as failure.
- Batch mode may use explicit preauthorization rules in `PolicyApprover`. Unmatched requests are denied, which may lower completion rate and must be recorded as the actual result.
- A/D still need to implement private/group message reception, conversation history, recipients and visibility controls. `run_goal()` and Observer do not replace a normal chat interface.
- Wrap `ToolExecutor.execute()` for tool traces and `ModelClient.complete()` for model traces. The model interface currently lacks task/agent IDs; accurate per-agent correlation still needs A/D runtime-context plumbing.
- Do not sum Worker `TaskResult.tokens` and label it total system usage: Planner and other calls are not included.

### Evaluation Aggregation

D computes metrics from `RunResult`; modules must not independently calculate inconsistent completion rates:

```text
Goal completion rate = runs with no run-level failure and goal_verdict.passed == True / all submitted goals
Subtask completion rate = task records with status == success / all task records
Execution time = finished_at - started_at (run_goal timestamps include planning)
```

Failed, blocked and dynamically inserted subtasks all count in the subtask denominator. Retries of the same task do not add extra nodes. Planning failures still count in the goal denominator, but generate no subtasks and must not be treated as subtask successes. If the total subtask count is zero, report an explicit undefined value and reason, not 100%.

Retain the final graph, task records, real verdicts, timestamps, memory flags, model/prompt versions and trace references. Supply fixed formal criteria through `goal_acceptance=dataset_rules`; do not replace them with weaker criteria proposed by the model.

## 7. Shared Service Assembly

`build_service()` is implemented in `orchestrator/bootstrap.py`. The application function below uses existing public interfaces and can be called by D's entry point. Its executor, memory, verifier, observer and ask_user arguments must be real objects supplied by B/C/D. It is not a complete CLI that can run immediately.

```python
from collections.abc import Awaitable, Callable
from pathlib import Path

from orchestrator.approvals import InteractiveApprover
from orchestrator.bootstrap import build_service
from protocol.models import (
    AcceptanceRule, HITLDecision, HITLRequest, RunConfig, RunResult, ToolDefinition,
)
from protocol.ports import Memory, Observer, ToolExecutor, Verifier
from runtime.client import DeepSeekClient
from runtime.profiles import load_profiles


async def run_connected_goal(
    goal: str,
    *,
    root: Path,
    profile_directory: Path,
    executor: ToolExecutor,
    tool_definitions: list[ToolDefinition],
    memory: Memory,
    verifier: Verifier,
    observer: Observer,
    ask_user: Callable[[HITLRequest], Awaitable[HITLDecision]],
    goal_rules: list[AcceptanceRule],
) -> RunResult:
    profiles = load_profiles(profile_directory)
    client = DeepSeekClient.from_environment()
    try:
        service = build_service(
            root=root,
            model=client,
            profiles=profiles,
            tool_definitions=tool_definitions,
            executor=executor,
            memory=memory,
            verifier=verifier,
            observer=observer,
            approver=InteractiveApprover(ask_user),
        )
        return await service.run_goal(
            goal, RunConfig(), goal_acceptance=goal_rules,
        )
    finally:
        await client.aclose()
```

Before calling it, D has B establish containers and mappings, and supplies the same root to C's adapter. After return, save `result.model_dump_json(indent=2)`, flush traces and close session resources. Keep containers running between tasks in a batch and clean up at the end of the session. The production application must also perform resource cleanup on errors and cancellation.

To validate scheduling separately, pass a loaded `TaskGraph` to `service.run_graph(graph, config)`. It does not invoke the Planner, so its results do not establish autonomous decomposition with a real model.

## 8. How A Performs the First Integration

The first integration connects the real model and all four members' modules through one entry point, completes tasks and retains verifiable results. Merging code must be followed by the integration steps below.

| Step | A's work | Inputs from teammates | Exit evidence |
| --- | --- | --- | --- |
| 1. Establish contracts | Confirm models, tool names, root directory, role IDs, acceptance paths and versions | B/C/D commitments to implement the interfaces | Interface checklist and blockers in the same PR/issue |
| 2. Minimal real workflow | Connect DeepSeek, file tools and a real verifier through `build_service()`; diagnose paths with `run_graph()`, then use `run_goal()` | B's file tools/containers; C's profiles; D's verifier | Actual model-generated Markdown written, accepted and archived, with logs |
| 3. Cross-agent dependencies | Use distinct Workers such as researcher/writer for upstream/downstream handoffs and a sub-delegation | B's workspaces; C's differentiated profiles; D's event records | Trace includes distinct agent IDs, child nodes, verification and parent resumption |
| 4. Human interaction and conversations | Connect a real clarification and an operation-permission request; jointly complete private/group entry points | D's CLI/message bus/approval callback | Human responses affect execution; conversation routing respects visibility boundaries |
| 5. Memory loop | Run two related tasks through the same interface; verify the first writes memory and the second retrieves it | C's persistence/retrieval; D's traces | Actual memory records and retrieval events, without presuming a performance gain |
| 6. Four tool families | Connect search, file CRUD, Python writing/execution/testing and Git commit/push to the same service | B's complete tool suite and separate GitHub test repository | Actual artifacts, test results and corresponding commit/push outcomes |
| 7. Dev-set baseline | Diagnose failures with D, verify counting/timing and fix a reproducible version | D's dev set/harness; C's fixed memory conditions | Goal/subtask completion rates, execution time, raw results and traces |

For interface diagnosis in Step 2, both memory flags may temporarily be explicitly disabled. Step 5 must repeat validation with real memory connected. Collaboration in Step 3 requires at least two different Workers; a single-Worker offline test is insufficient.

### Suggested First Integration Task

Have a researcher collect and organize information about two public software tools, then have a writer produce a Markdown comparison with sources. First use a fixed two-node graph to validate artifact handoff, then have the Planner generate the graph from a natural-language goal.

Next, use separate scenarios for sub-delegation, human clarification, code testing and Git push so failures can be attributed to a module. Define acceptance rules in advance for each scenario. These are internal dev scenarios, not official test-set results.

### Ownership of Fixes

- Interface, scheduling, state and resumption issues: A fixes them.
- Tool execution, path mapping, containers or Git: B fixes them; A verifies the call results.
- Memory contents, retrieval, persistence or profiles: C fixes them; A verifies injection points.
- Messages, CLI, rule evaluation, traces or metrics: D fixes them; A provides raw execution facts.

### Checklist Before W10 Integration Acceptance

- [ ] The team agrees on interfaces and uses the same configuration and actual merged version.
- [ ] Real B/C/D adapters can be imported and assembled; production flows do not use test doubles.
- [ ] The natural-language goal-to-artifact workflow is repeatable with real DeepSeek calls.
- [ ] Dependency handoffs and sub-delegation work across at least two Workers.
- [ ] Real human clarification/permission and policy-based unattended execution are validated separately.
- [ ] Private and group conversations actually receive, process and reply to messages.
- [ ] Real Worker memory read/write is complete; remaining Planner/Critic lifecycle work has explicit implementation plans and owners.
- [ ] Search, file CRUD, code execution/testing and pushes to the separate GitHub repository actually work.
- [ ] D's dev set reports both completion measures, execution time and corresponding raw logs, including failures.
- [ ] Unfinished course capabilities remain listed as gaps; a successful smoke workflow does not establish completion of the entire project.

The team's internal target is to produce the first dev-set metrics by November 8. Official test release dates and permitted run counts follow course announcements.
