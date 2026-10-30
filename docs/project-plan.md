# Multi-Agent Collaboration System: Project Specification and Collaboration Guidelines

Course Final Project. This document aligns the team. It should be maintained in the repository's `docs/` directory and linked from `README.md`.

Version: v3.4 local alignment draft (2026-10-29), revised from v3.3 (2026-10-24), pending team PR review.

This document revises the initial project specification and collaboration guidelines. The original attachment is retained as historical material. Use this document as the baseline for responsibilities and internal milestones; use `protocol/models.py` and `protocol/ports.py` for currently callable models and interfaces. See [overview.md](overview.md) for interface semantics, [usage-and-integration.md](usage-and-integration.md) for usage and integration, and [changelog.md](changelog.md) for changes. Implementation gaps do not change course requirements. The capability tables and workflows below describe the target system; consult the integration guide for the implemented scope.

## Key Dates

| Milestone | Dates | Notes |
| --- | --- | --- |
| W8 | Oct 19–25 | Contract phase: draft interfaces and responsibilities |
| W9 | Oct 26–Nov 1 | Parallel development of the four modules |
| W10 | Nov 2–8 | **Hard deadline:** the dev set must produce metrics |
| W11 | Nov 9–15 | Mid-test preparation milestone; continue dev regression if the set has not been released |
| W12 | Nov 16–22 | Hardening and architecture stabilization |
| W13 | Nov 23–29 | Demonstration week; official test release schedule remains unconfirmed |
| End of regular classes | Nov 30, Monday | — |
| Report submission | W14 or later | Subject to the teaching assistant's announcement |

## 0. At a Glance

| Item | Description |
| --- | --- |
| What to build | A multi-agent collaboration platform: human goal → autonomous decomposition, assignment, execution and verification → deliverables |
| Deliverables | Code repository and commit-history screenshots, architecture document, test and performance report, Week 13 demonstration |
| Evaluation metrics | Task completion rate and execution time |
| Main risks | HITL blocking unattended runs; uncontrolled open-ended negotiation; commit history concentrated under one contributor |
| Technical boundaries | DeepSeek or HKGAI APIs only; human-readable document outputs restricted to md/txt; CLI interface |
| Team | Four members, divided by module, each owning a workflow that can be verified independently |

## 1. Project Scope

### 1.1 System Positioning

The system autonomously collaborates through planning, execution and verification roles. It uses structured task graphs to complete user goals, much like a small organization:

- The user provides a high-level goal.
- The Planner decomposes the goal into a task graph with dependencies and assigns work to different roles.
- Workers call tools in their own sandboxes to execute subtasks.
- The Critic checks acceptance criteria and returns unsuccessful work for revision.
- Humans confirm critical steps through HITL. Policy-based approval is limited to preauthorized unattended scenarios and does not replace a demonstration of pausing for and resuming after a real human response.
- Each agent writes its experience to private memory for retrieval and reuse on similar future tasks: Evolution.

### 1.2 Required Capabilities (Specification Sections 2.1–2.3)

| # | Capability | Acceptance criteria |
| --- | --- | --- |
| 1 | Private one-to-one conversations between a human and any agent; group conversations involving multiple agents and humans | Any agent responds when a human starts a conversation with it; group messages are visible to all participants |
| 2 | Autonomous delegation: task decomposition, role negotiation and assignment, including Worker-to-Worker sub-delegation | A high-level goal produces a task graph autonomously; Workers can request subtasks and reassignment, with a maximum delegation depth of 1 |
| 3 | HITL: proactively pause for clarification during execution or approval before critical actions | Two demonstrable scenarios; unattended execution does not block indefinitely |
| 4 | Memory and evolution: independent memory for each agent, learning from successes and failures | Compare memory-enabled and memory-disabled runs with fixed tasks and configuration; report actual effects and failure causes without assuming improvement |
| 5 | Sandbox isolation: a separate workspace and execution environment for each agent | Agent A cannot directly read Agent B's workspace; cross-agent dependencies receive copies through the orchestrator only |
| 6 | Tools: search, file CRUD, code execution and Git | All four tool families can run during the demonstration |

Visibility boundaries:

| Channel | Visibility | Rationale |
| --- | --- | --- |
| Private conversation between a human and one agent | That human and that agent only | Explicitly required by specification Section 2.1 |
| Group conversation with multiple agents and humans | All participants | Explicitly required by specification Section 2.1 |
| Negotiation summaries, role assignments and task-handoff notices within a group | Broadcast to the human and all agents in that group | Negotiation and delegation need observable evidence to establish that they occurred |
| Raw tool payloads and private messages between agents | Internal to the system; not shown to the human | Not required by the specification; exposing them would pollute conversation context |

The group conversation must show who claimed which task and who delegated which part to whom. Without this evidence, autonomous negotiation cannot be demonstrated during evaluation. Raw tool payloads do not enter the group conversation.

### 1.3 Execution Workflow

```text
User goal
  └→ Planner reads memory → produces a structured task graph with acceptance criteria
       └→ Scheduler dispatches tasks in dependency order
            └→ Worker calls tools in its sandbox and produces files
                 ├ Missing expertise → requests subtasks → scheduler validates and inserts child nodes
                 │           (depth + 1, maximum 1; at most 2 children per parent)
                 │           → normal dispatch and verification → results returned to requester
                 └→ Structured report: artifact paths, changes, self-assessment and blockers
                      └→ Critic checks acceptance criteria
                           ├ Fail → return a defect list for bounded rework
                           └ Pass → copy artifacts to artifacts/<run_id>/<task_id>/ for downstream use
                                └→ Collect deliverables
                                     └→ Each agent writes a reflection to private memory
                                          └→ Future tasks benefit
```

Key design: open-ended negotiation occurs only during planning, with a round limit, and produces a task graph conforming to a fixed schema. Execution uses deterministic scheduling. Otherwise, the system becomes slow, expensive and difficult to reproduce.

For sub-delegation, a Worker **submits a request and the scheduler inserts nodes**, rather than directly invoking another agent. Going around the scheduler would skip verification and exclude subtasks from completion-rate statistics.

### 1.4 Core Contract: Task Graph Schema

This is the central contract for the project. Establish it before implementation.

```json
{
  "task_id": "t3",
  "title": "Collect competitor pricing",
  "assignee": "researcher",
  "parent_task": null,
  "depth": 0,
  "depends_on": ["t1"],
  "inputs": [
    {"from_task": "t1", "path": "brief.md", "as": "inputs/brief.md"}
  ],
  "acceptance": [
    {"type": "file_exists", "path": "outputs/competitor_pricing.md"},
    {"type": "text_contains", "path": "outputs/competitor_pricing.md", "text": "Sources"}
  ],
  "outputs": ["outputs/competitor_pricing.md"],
  "max_retries": 2
}
```

`acceptance` contains `AcceptanceRule` objects expressing machine-checkable assertions. The example checks only for a file and a section; it does not establish pricing accuracy, company count or source quality. D must implement corresponding evaluators for such requirements, with A updating the model. For formal evaluation, D supplies fixed dataset criteria through `run_goal(..., goal_acceptance=rules)`; the model must not weaken them.

`inputs` does not contain absolute paths. It declares which artifact to take from which task, and the orchestrator copies it into the receiving agent's workspace before execution. A `depth` of 0 identifies tasks created directly by the Planner; Worker-requested subtasks have depth 1, with no deeper nesting. `parent_task` records the requesting task.

### 1.5 Artifact Handoff

Isolation and dependencies impose different requirements: agents cannot read other agents' workspaces, but downstream tasks need upstream outputs. The orchestrator mediates by copying artifacts without broadening mount permissions.

| Directory | Owner | Access |
| --- | --- | --- |
| `workspaces/<agent>/` | Private to that agent | Only that agent's container can read and write it |
| `artifacts/<run_id>/<task_id>/` | Orchestrator | Writable by the host process; invisible to agents |

The workflow has four steps:

1. Before a task starts, the orchestrator follows its `inputs` declarations to copy files from `artifacts/<run_id>/<from_task>/` to `workspaces/<assignee>/inputs/`.
2. The Worker reads its own workspace's `inputs/` and writes artifacts to `outputs/`.
3. Once the Critic accepts the task, the orchestrator copies `outputs/` to `artifacts/<run_id>/<task_id>/`, making the artifacts available downstream.
4. Rejected artifacts never enter `artifacts/`, so downstream tasks cannot consume unverified outputs.

This preserves container isolation while satisfying file dependencies. Each handoff is recorded, allowing the trace to reconstruct which artifact was transferred to whom and when.

## 2. Requirements and Constraints

### 2.1 Technical Constraints (Specification Sections 2.4–2.6)

| Constraint | Meaning | Implication |
| --- | --- | --- |
| Models | OpenAI and Anthropic APIs are prohibited; use DeepSeek or HKGAI only | Keys will be provided later. DeepSeek supports an OpenAI-compatible interface, so the `openai` library can point to its `base_url`; document the endpoint in the report |
| Document formats | Human-readable document outputs must use md/txt | Do not implement pdf/docx/pptx parsers; code, configuration and data artifacts are unaffected |
| UI | CLI, bot or Gradio are allowed; frontend design earns no credit | CLI is the current baseline; changes require team review |

The md/txt restriction applies to document tasks and outputs, not to every file:

| Category | Examples | Restriction |
| --- | --- | --- |
| Final documents for human readers | Analysis reports, research notes and final documents | md/txt only |
| Code | `.py`, `.sh`, `.sql` | Not restricted |
| Configuration | `.yaml`, `.json`, `.toml` | Not restricted |
| Data and intermediate artifacts | `.csv`, `.sqlite`, `.jsonl` logs | Not restricted |
| Complex proprietary formats | `.pdf`, `.docx`, `.pptx` | Neither parsed nor generated |

The distinction is whether the file is a finished document for humans or an intermediate artifact consumed by software. The former uses md/txt; the latter is not subject to that restriction.

The same applies to inputs. Assume test documents use md/txt. If a PDF is supplied, ask the teaching assistant for clarification instead of implementing a parser.

### 2.2 Deliverables

| Deliverable | Requirement | Owner |
| --- | --- | --- |
| Code repository | Public GitHub link; runnable code | Entire team |
| Commit-history screenshots | Evidence of substantive contributions by every member throughout development; uploading everything at the end risks severe penalties or an academic integrity review | Everyone contributes evidence; D consolidates |
| Architecture document | Multi-agent routing, memory and evolution mechanisms, and sandbox implementation | Each owner writes their section; A leads the overview |
| Test and performance report | Mid/final completion rates, execution time, failure modes and memory-evolution analysis | Each owner writes their section; D handles formatting, charts and screenshots without rewriting others' arguments |
| Week 13 demonstration | An end-to-end workflow that runs live | A prepares the script; D prepares the data |

### 2.3 Evaluation

- Report **two completion-rate measures**, plus execution time:
  - `goal_completion_rate`: successful top-level goals / total goals.
  - `subtask_completion_rate`: the proportion of task-graph nodes accepted by the Critic, including dynamically delegated children.
  - Current integration timing: `finished_at - started_at` returned by `run_goal()`, including planning, waiting, execution, final verification and reflection completed before return. D retains stage timestamps to accommodate any different endpoint specified by the official evaluation.
- The system must include a harness that computes and exports these metrics automatically; see Section 5.6.
- The number of official test sets, release dates and allowed runs are subject to instructor/teaching-assistant announcements. The course text mentions both "final three weeks before the deadline" and "mid/final in the last 3/1 weeks," so the exact release schedule cannot be established from it. W11/W13 in the tables are internal preparation milestones.

Specification Section 3.1 says "the percentage of sub-tasks and overarching goals," covering both levels. Reporting only one measure risks a mismatch with the evaluator's interpretation; reporting both addresses either interpretation.

The number of permitted official evaluation runs remains unconfirmed. To reduce late architectural changes, the team aims to **establish an architecture baseline by the end of W11**, with W12 focused on fixes and hardening.

The test-task format is not yet known. This should not block development: start with the text workflow, then add the required code capabilities.

| Stage | Coverage |
| --- | --- |
| W8–W9 | Complete text workflow: search, file read/write and Markdown generation |
| W10 | Add code execution and Git tools; connect the dev set |
| W11 onward | Shift emphasis to code tasks if they are confirmed in the test set; otherwise maintain the text workflow |

Make acceptance checking pluggable rather than hard-coding it. The harness dispatches acceptance rules to evaluators by type:

| Evaluator | Check |
| --- | --- |
| `file_exists` | Whether an artifact path exists |
| `text_contains` | Whether required text or sections are present |
| `pytest_passes` | Whether the target tests pass |

When test formats change, add an evaluator and update the `AcceptanceRule` contract without rewriting scheduling. This is a low-cost way to handle an unknown task format.

## 3. Issues to Watch

| # | Issue | Consequence | Mitigation |
| --- | --- | --- | --- |
| 1 | HITL supports interactive mode only | Batch evaluation waits indefinitely, reducing completion rate | Abstract approval behind an interface with interactive and policy-based modes |
| 2 | Agents negotiate freely in group chat | Slow, expensive and difficult to reproduce | Restrict negotiation to planning, limit rounds and require schema-conforming output |
| 3 | No token or step limits | Multi-agent calls exhaust the budget quickly | Set per-task limits, with caching and backoff retries |
| 4 | Agents push directly to the main project repository | Development history is polluted, compromising contribution evidence | Point Git tools only at a separate sandbox repository |
| 5 | Completion is assessed by visual inspection | Metrics cannot be reproduced or substantiated | Use machine-checkable acceptance; an LLM judge is supplementary and uses a fixed prompt |
| 6 | Commits are concentrated in the final week | Academic integrity concerns | Create the repository in the first week; each member makes at least three meaningful commits per week |
| 7 | Time spent on PDF/DOCX/PPT | Violates explicit format constraints | Support md/txt documents only; explicitly exclude other document formats |
| 8 | Concurrency is introduced immediately | Interleaved logs make failures difficult to attribute | Start serially; later allow only 2–3 concurrent independent subtasks |
| 9 | Architectural problems are discovered only after test release | The release schedule is unconfirmed; insufficient time may remain | Produce dev-set metrics by Nov 8 using an internal set of three tasks in each of five categories |
| 10 | Sandboxes rely only on directory conventions | Boundary violations and data leakage | Use containers, with path validation as an additional safeguard |
| 11 | Team members use inconsistent environments | Path, encoding and process-limit differences break integration | Agree on a Linux sandbox image and mount conventions in W8; see Section 6.2 |

Terminology:

| Term | Meaning in this project |
| --- | --- |
| Evolutionary | Cross-task memory: write reflections after tasks, then retrieve and inject them into future tasks; no model training or fine-tuning |
| HITL | Human-in-the-Loop: clarification, permission and correction requests |
| Policy-based approver | Preauthorized rules for unattended decisions: approve allowlisted actions, record conservative defaults for ambiguity, and reject dangerous actions; the model does not invent the policy |
| Heterogeneous | Agents differ in system prompts, tool permissions and memory; this also applies to the lead agent |
| Harness | Evaluation infrastructure: load tasks, clean sandboxes, time execution, run the system, judge results and aggregate metrics |

## 4. Responsibilities

### 4.1 Four Roles

| Owner | Responsibilities | Code directories | Report section | Branch |
| --- | --- | --- | --- | --- |
| A | Task-graph schema, scheduler, HITL gate and agent main loop | `protocol/`, `orchestrator/`, `runtime/` | §2 Routing and collaboration logic | `Role_A` |
| B | Four tool families, execution time limits, container/directory isolation, Git integration and stability | `tools/`, `sandbox/` | §3 Sandbox implementation and threat model | `Role_B` |
| C | Memory read/write, reflection generation and retrieval, A/B experiments | `memory/`, `profiles/` | §4 Memory mechanisms and experimental data | `Role_C` |
| D | Harness, metrics, traces, message bus and CLI | `harness/`, `ui/`, `bus/`, `docs/` | §5 Metrics and failure modes; message-bus subsection of §2 | `Role_D` |

Divide work along directory boundaries that support independent commits, rather than by project phase. Each member owns a report section.

Ownership assigns workload, not exclusive authority. Interface work is concentrated in the first three weeks. From W11 onward, B shares integration troubleshooting, D handles metric regression, and A retains interface arbitration and architectural decisions.

### 4.2 Directory Layout (Cross-Module Changes Require PR Review and Updated Documentation)

```text
multi-agent/
├── protocol/        # Data models and schemas (A)
├── orchestrator/    # Planning, scheduling and HITL gate (A)
├── bus/             # Message bus and blackboard (D)
├── runtime/         # Agent main loop (A)
├── profiles/        # Agent prompts and tool allowlists (C)
├── tools/           # Tool implementations (B)
├── sandbox/         # Container and directory isolation (B)
├── memory/          # Memory storage and retrieval (C)
├── harness/         # Evaluation harness (D)
├── ui/              # CLI (D)
├── docker/          # Sandbox images and startup configuration (B)
├── workspaces/      # Generated at runtime; excluded from Git
├── tests/
├── docs/
├── .env.example
└── README.md
```

### 4.3 Project Phases

| Phase | Week | Goal | Exit criteria and reviewer |
| --- | --- | --- | --- |
| P0: Contracts | W8, Oct 19–25 | Agree on contracts, repository and CI, stack, environment and technical spikes | Contract v1 merged into develop; A coordinates |
| P1: Parallel development | W9, Oct 26–Nov 1 | Four independently runnable modules | Module unit tests pass; search/file interfaces are ready to connect; A verifies |
| P2: First integration | W10, Nov 2–8 | End-to-end execution and dev-set metrics | First metrics available by Nov 8; D verifies |
| P3: Evaluation and diagnosis | W11, Nov 9–15 | Run an official set if released; otherwise continue dev regression | Metrics and failure categories for the actual dataset; D verifies |
| P4: Hardening | W12, Nov 16–22 | Fix architecture based on failure categories; finalize memory A/B work | Improved completion rate and complete §4 data; C verifies |
| P5: Finalization | W13, Nov 23–29 | Demonstration and documentation; official evaluation according to actual releases | Reproducible demonstration and complete deliverables; A verifies |
| P6: Submission | W14 onward | Finalize the report and commit screenshots | Submit on time |

### 4.4 Weekly Tasks by Member

| Week | A | B | C | D |
| --- | --- | --- | --- | --- |
| W8 | Contract v1, repository and CI, runtime environment | Executor prototype, containers and path validation | Memory structures, failure categories and a synthetic memory-loop prototype | Harness skeleton, trace format, event-flow skeleton and both completion measures |
| W9 | Scheduler, including delegated-node insertion | Search and file tools | Memory read/write and similarity retrieval | Persistent events and replay, batch runs and result JSON |
| W10 | First end-to-end integration, HITL gate and sub-delegation workflow | Code execution, host-side Git tools and tool fixes | Reflection generation and injection | Dev set and first metrics |
| W11 | Diagnose issues using released official sets or dev results | Timeouts and crash recovery | Memory A/B experiments | Run available datasets, label their source and report metrics |
| W12 | Architectural fixes and routing optimization | Concurrency, rate limiting and one-command startup | Analyze evolution effects | Failure attribution and charts |
| W13 | Demonstration script and integration support | Environment fallback | Finalize §4 | Official evaluation according to actual releases; consolidate reports and screenshots |
| W14 | Finalize §2 | Finalize §3 | Proofreading | Assemble the final report |

The effective window for four-way parallel work is W9–W11. W8 depends on agreeing on the central contracts; from W12 onward, two people focus on system fixes and two on documentation.

## 5. Development Rules

The commit frequency, branch names, merge methods and container design below are team conventions. The course requires sustained, genuine collaboration, a runnable system and effective isolation; it does not prescribe these specific practices.

### 5.1 Branch Model

```text
main          ← Protected; demonstrable stable versions only
  ↑ PR (Create a merge commit; one reviewer required)
develop       ← Protected daily integration branch
  ↑ PR (Create a merge commit; one reviewer required)
Role_A / Role_B / Role_C / Role_D   ← Long-lived personal branches
```

Mandatory team rules:

1. Protect both `main` and `develop`. No direct pushes: use PRs with at least one reviewer.
2. Name branches by person/role, not feature. Responsibilities remain fixed, so the branch identifies the contributor and supports contribution evidence. For two independent workstreams, a temporary branch such as `Role_A/exp-xxx` may be used and deleted after merging.
3. Push to your own remote branch at least once per day; do not leave local work unpushed for more than two days. This protects work and preserves contribution evidence.
4. Integrate personal branches into `develop` through PRs at least every three days. Before merging, merge `develop` into the personal branch and resolve conflicts; do not use rebase to integrate.
5. Use **Create a merge commit** for PRs. Do not use **Squash and merge**, which collapses the individual commits, or **Rebase and merge**.
6. Merge `develop` into `main` only at three milestones: the first end-to-end workflow in W10, completed hardening in W12, and before the W13 demonstration. A opens these PRs.
7. Do not force-push to `develop` or `main`, or use `--force` to overwrite someone else's branch.
8. Every PR description must explain what changed, how it was verified and which modules are affected. Review against these three points.

Rules 4 and 5 govern integration into `develop`. They do not cover reorganizing commits within a personal branch; see Section 5.2.

### 5.2 Commit Conventions

Use a simplified Conventional Commits format:

```text
feat(orchestrator): dispatch tasks in dependency order
fix(sandbox): reap child processes after execution timeout
docs(protocol): document the acceptance field
refactor(memory): extract the retrieval interface
test(harness): add a timeout scenario
```

Split commits by logical change rather than directory:

1. Each commit must be a self-contained change that can be reverted without breaking the code. An interface change and its callers belong in the same commit.
2. Describe the change in one sentence without joining unrelated changes with "and" or "while here."
3. Keep pure formatting, renaming and file moves in separate commits. These do not change behavior and can be reverted independently.
4. Commit documentation changes separately.

Do not commit `.env`, `workspaces/`, `__pycache__/`, `results/`, large files or model weights. Keep sensitive configuration in `.env`; the repository contains only `.env.example`.

### 5.3 Code Conventions

- Cross-module calls use Pydantic models from `protocol/`, not untyped dictionaries.
- Centralize all LLM calls in one client wrapper for retries, backoff, token accounting and logging. Do not make scattered direct calls.
- Declare each agent's tool allowlist explicitly in `profiles/*.yaml`; deny by default.
- Validate sandbox paths against the workspace root and reject `..` traversal.
- Write a JSONL trace for each task run, recording tool calls, elapsed time and token usage at each step.

### 5.4 Collaboration Cadence

| When | Activity | Record |
| --- | --- | --- |
| Every Monday | Review last week's progress, this week's goals and blockers | Progress in PRs/tracking issues; interface changes in `docs/changelog.md` |
| Every three days | Merge personal work into `develop` through PRs | Runnable develop branch |

Discuss the following in the group before making unilateral changes:

1. The same issue has blocked progress for more than half a day.
2. A merge conflict cannot be resolved confidently.
3. Work requires modifying another member's files.

### 5.5 Environment and Execution

`.env` contains six variables:

| Variable | Purpose | When missing |
| --- | --- | --- |
| `LLM_BASE_URL` | DeepSeek or HKGAI endpoint | Fail at startup |
| `LLM_API_KEY` | Model API key | Fail at startup |
| `LLM_MODEL` | Model name | Fail at startup |
| `SEARCH_API_KEY` | Search API, using the free tier of Tavily or Serper | Fall back to keyless retrieval and mark `search_degraded` in the trace |
| `GITHUB_TOKEN` | Push to the sandbox repository | Mark Git tools unavailable without blocking other tools |
| `GITHUB_SANDBOX_REPO` | Dedicated sandbox repository URL | Same as above |

Tool execution locations must be explicit so credentials are not exposed to agent-generated code:

| Tools | Execution location | Reason |
| --- | --- | --- |
| File operations and code execution | Inside containers | Require actual isolation and execution limits |
| Search and Git push | Host-side tool layer | Keep credentials outside containers and inaccessible to generated code |

Other execution conventions:

- Two entry points: `python -m harness.run --suite dev` and `python -m ui.cli`.
- No external database dependency. The message bus uses SQLite and JSONL, initialized at startup.
- Agree on the runtime environment in W8; see Section 6.2.

### 5.6 Harness Output Format

The following is a synthetic format example, not actual evaluation data. `run_id` follows `Identifier` and does not contain characters such as colons. D aggregates `RunResult`; A does not compute a separate completion rate.

```json
{
  "run_id": "mid-20261110T140300",
  "suite": "mid",
  "tasks": [
    {
      "task_id": "mid-03",
      "status": "success",
      "duration_sec": 214.7,
      "tokens": 38210,
      "artifacts": ["outputs/competitor_pricing.md"],
      "acceptance_passed": 3,
      "acceptance_total": 3,
      "subtasks_total": 4,
      "subtasks_passed": 4,
      "failure_reason": null,
      "hitl_events": 2,
      "assumptions": ["Assume 2026 data is used"]
    }
  ],
  "summary": {
    "goals_total": 15,
    "goals_passed": 11,
    "goal_completion_rate": 0.733,
    "subtasks_total": 47,
    "subtasks_passed": 39,
    "subtask_completion_rate": 0.830,
    "avg_duration_sec": 241.3
  }
}
```

## 6. Recommended Technology Stack (Pending Team Confirmation)

### 6.1 Recommended Combination

| Layer | Recommendation | Notes |
| --- | --- | --- |
| Language | Python 3.11+ | Broad LLM ecosystem; DeepSeek examples use Python |
| Package management | uv | Fast installation, clean lockfiles and shorter CI setup |
| LLM calls | Current `DeepSeekClient` uses httpx and restricts requests to the official DeepSeek endpoint | HKGAI requires a separate adapter; the SDK choice does not change provider restrictions |
| Data models | Pydantic v2 | Validation and direct JSON schema export |
| Structured output | Workers currently use native tool calls; the Planner uses JSON, Pydantic validation and bounded repair | See `runtime/agent.py` and `orchestrator/planner.py` |
| Storage | SQLite for events/tasks; JSONL for memory/logs | Minimal operations overhead, inspectable diffs and replay |
| Retrieval | Keywords and character similarity | With only hundreds of memories, a vector database would be unnecessary complexity |
| CLI | Typer and Rich | Fast development and readable output |
| Async execution | Single-process asyncio | Sufficient and easy to debug; no Redis or Celery |
| Testing | pytest and pytest-asyncio | Standard Python testing tools |
| Linting | Ruff | Replaces flake8, black and isort with one tool |
| CI | GitHub Actions running Ruff and pytest | Automatic checks on PRs |

The project's difficulty lies in orchestration and reliability, not the stack. Prefer common tools the team knows well rather than choosing technologies for novelty.

### 6.2 Runtime Environment and Sandbox Baseline

Use containers with a common Linux sandbox image and workspace mapping. Windows developers use WSL2; macOS/Linux developers use the appropriate Docker environment. WSL2 is a platform-specific development choice; the course requires effective isolation.

| Item | Approach | Rationale |
| --- | --- | --- |
| Development environment | Python 3.11+; agree on Linux sandbox image and mount paths | Windows can use WSL2; macOS/Linux use compatible Docker environments |
| Sandbox | One long-lived container per agent, executing through `docker exec` | Isolation is evaluated; containers provide a separate filesystem for each agent |
| CI | Current GitHub Actions uses Ubuntu and Python 3.11 for offline checks; sandbox tests will use the shared image once B adds them | Current CI does not establish that real container integration works |

Three execution details: start containers at system startup and retain them across tasks rather than recreating them for every task, which would add roughly 1–2 seconds per task; bake dependencies into the image rather than installing them online at runtime; disable external networking in containers by default, routing search and Git through the host-side tool layer instead (Section 5.5).

Without external networking, generated code cannot download its own dependencies. Preinstall commonly needed libraries in the image. This also motivates separating code execution from online retrieval.

## 7. Items to Confirm

| # | Question | Participants | Impact |
| --- | --- | --- | --- |
| 1 | CLI is the current choice; review any proposed change | Team | D's workload |
| 2 | Final stack selection | Team, W8 | See Section 6 |
| 3 | Long-lived containers are the baseline; B still needs to supply resource limits and startup interfaces | A/B/D | Execution time and lifecycle |

## 8. Interface Alignment and Outstanding Work as of 2026-10-29

- C's public interface to A is async `retrieve(agent_id, task) -> str` and `reflect(task, result) -> None`. Retrieval and formatting can remain separate internally.
- Memory lives at `workspaces/<agent_id>/memory/<agent_id>.jsonl`; task cleanup rebuilds only `inputs/` and `outputs/`.
- `memory_enabled` controls retrieval/injection; `write_memory` controls terminal writes. Experiments record both flags and initial memory state.
- D's rule-based verification connects through `Verifier.check()`. A complete Critic Agent does not yet exist. A/C must implement automatic Planner memory and Critic execution/memory; Worker tests alone do not establish memory support for every agent.
- A provides `orchestrator.bootstrap.build_service()`. D uses this shared factory for the CLI and harness; real B/C/D adapters still need implementation and integration.
- Observer currently emits task events. Private/group input routing and full per-tool/per-model traces still require A/B/D integration.
- Personal plans reside in `docs/contributions/Role_<X>.md`. D's previous file remains as a redirect. Changes are recorded in `docs/changelog.md`; local alignment does not mean the changes have been merged or approved by the full team.
