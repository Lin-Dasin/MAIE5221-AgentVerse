# Project Overview

This document is the alignment reference for the team. If code and this document
disagree, the code wins and this document gets updated within two days.

## 1. What we are building

A multi-agent collaboration platform. A user states a goal in natural language;
the system decomposes it into a task graph, dispatches tasks to agents with
different roles, executes and verifies the work, and produces deliverables.

It is not a single chatbot with a long prompt. It is an orchestration layer: one
planner, several workers with different tools, one critic, plus per-agent memory
that persists across tasks.

Models are served through the DeepSeek or HKGAI API only. OpenAI and Anthropic
APIs are prohibited by the course specification.

## 2. Required capabilities

| # | Capability | How we satisfy it |
|---|---|---|
| 1 | One-to-one chat between a human and any agent; group chat with multiple agents and humans | Unified message bus; every agent is addressable |
| 2 | Autonomous delegation: decomposition, role negotiation, subtask dispatch, including worker-to-worker requests | Planner produces a task graph; a worker may request a subtask that the scheduler inserts |
| 3 | Human-in-the-loop: pause for clarification or approval before risky actions | Approver interface with two modes: interactive and policy-driven |
| 4 | Per-agent memory and evolution across tasks | Private memory file per agent; reflections written after a task and retrieved before similar tasks |
| 5 | Sandbox isolation: separate working directory and execution environment per agent | One container per agent, its own workspace mounted, path validation as a second line of defence |
| 6 | Tools: web search, file CRUD, code execution, git commit and push | Four tool families in `tools/`, executed either in the container or on the host |

## 3. Execution pipeline

```
User goal
  └→ Plan: Planner retrieves past experience, produces a task graph
       └→ Dispatch: scheduler assigns tasks to agents in dependency order
            └→ Execute: each agent calls tools inside its own sandbox
                 ├ Missing capability → request a subtask → scheduler inserts a child node
                 │                    → verified as usual → result returned to requester
                 └→ Structured report (artifact paths · changes · self-assessment · blockers)
                      └→ Verify: Critic checks acceptance criteria
                           ├ Fail → returned with a defect list, up to a retry limit
                           └ Pass → artifacts collected, available downstream
                                └→ Reflect: each agent writes a reflection to its private memory
                                     └→ Feeds the next task
```

Free-form negotiation happens only during planning, with a round limit. Execution
follows a deterministic schedule. Without that boundary the system becomes slow,
expensive and impossible to reproduce.

## 4. Core contract: the task graph

Every module reads and writes this shape. It is defined in `protocol/` as a
pydantic model and must not be redefined elsewhere.

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
    "outputs/competitor_pricing.md exists",
    "covers at least 3 vendors",
    "each price has a source link"
  ],
  "outputs": ["outputs/competitor_pricing.md"],
  "max_retries": 2
}
```

Rules:

- `acceptance` must be machine-checkable. "High quality" cannot be counted.
- `inputs` never contains an absolute path or another agent's workspace path.
- `depth` is 0 for planner-created tasks and 1 for worker-requested subtasks.
  Deeper nesting is not supported.
- A worker never calls another agent directly. It requests a subtask and the
  scheduler inserts it, so the new node still goes through verification and still
  counts towards the metrics.

## 5. Artifact handoff

Isolation and dependency pull in opposite directions: agents must not read each
other's workspace, but downstream tasks need upstream output.

| Directory | Owner | Access |
|---|---|---|
| `workspaces/<agent>/` | that agent | only its own container, read and write |
| `workspaces/<agent>/memory/` | that agent | private memory, survives task cleanup and container rebuild |
| `artifacts/<task_id>/` | orchestrator | written by the host process, invisible to agents |

Subdirectories inside a workspace do not share the same lifetime:

| Subdirectory | Lifetime |
|---|---|
| `inputs/` | rebuilt for every task |
| `outputs/` | rebuilt for every task; copied to `artifacts/<task_id>/` after acceptance |
| `memory/` | persists across tasks and across container rebuilds |

Memory lives inside the workspace rather than in a central directory so that
isolation is verifiable from the filesystem, not merely asserted by code: agent A
cannot read agent B's memory because A's container never mounts it.

1. Before a task starts, the orchestrator copies the artifacts named in `inputs`
   from `artifacts/<from_task>/` into `workspaces/<assignee>/inputs/`.
2. The worker reads only its own `inputs/` and writes to `outputs/`.
3. After the critic passes the task, the orchestrator copies `outputs/` into
   `artifacts/<task_id>/`, making it available downstream.
4. A failed task never reaches `artifacts/`, so downstream work cannot consume
   unverified output.

## 6. Module map

| Directory | Owner | Responsibility |
|---|---|---|
| `protocol/` | A | Data models and schema |
| `orchestrator/` | A | Planning, scheduling, HITL gate |
| `runtime/` | A | Agent main loop |
| `profiles/` | C | Per-agent prompts and tool allowlists |
| `tools/` | B | Four tool families |
| `sandbox/` | B | Container and directory isolation |
| `memory/` | C | Memory storage and retrieval |
| `harness/` | D | Evaluation harness |
| `ui/` | D | CLI |
| `bus/` | D | Message bus and trace |
| `docker/` | B | Container image and compose |
| `docs/` | shared | Layout per `docs/README.md`; D merges the report but does not rewrite other members' chapters |

## 7. Out of scope

- Parsing or generating PDF, DOCX or PPTX. Document deliverables are Markdown and
  plain text only. Code, configuration and data files are unaffected.
- A polished front end. A CLI is sufficient; the grade depends on the backend.
- Vector databases. Memory holds a few hundred entries; keyword and character
  similarity is enough.

## 8. Environment

Six variables, defined in `.env.example`:

| Variable | Purpose | If missing |
|---|---|---|
| `LLM_BASE_URL` | DeepSeek or HKGAI endpoint | startup fails |
| `LLM_API_KEY` | model key | startup fails |
| `LLM_MODEL` | model name | startup fails |
| `SEARCH_API_KEY` | search API | falls back to key-less scraping, trace marks `search_degraded` |
| `GITHUB_TOKEN` | push to the sandbox repository | git tool reports unavailable |
| `GITHUB_SANDBOX_REPO` | dedicated sandbox repository | same as above |

Credentials never enter an agent container. File operations and code execution run
inside the container; web search and git run on the host.

## 9. Metrics

Two completion figures are reported, because the course specification mentions
sub-tasks and overarching goals:

- `goal_completion_rate`: goals passed / total goals, where a goal passes when its
  final artifact exists and that artifact meets its acceptance criteria (contract 10)
- `subtask_completion_rate`: passed nodes / all nodes in the task graph, including
  worker-requested subtasks

Execution time is measured from task dispatch to artifact generation. All three are
produced by `harness/`, not estimated by hand (see contract 2 below).

## 10. Cross-module contracts

These are binding. Each row names exactly one owner; everyone else conforms rather
than re-implements. Changing a contract is the owner's call, announced in the group,
and this document is updated in the same week.

| # | Contract | Owner | Rule |
|---|---|---|---|
| 1 | Verdict logic (`file_exists`, `text_contains`, `pytest_passes`) | D | Implemented once in `harness/verdict.py`. The orchestrator calls it and must not implement its own checks. |
| 2 | Metric computation | D | Only `harness/` computes `goal_completion_rate`, `subtask_completion_rate` and duration. Other modules expose raw facts, never aggregates. |
| 3 | Event schema | D | Defined in `bus/events.py` and frozen. A and B emit events with those fields only; a new field requires a schema change first. |
| 4 | `ToolResult` | A | One pydantic model in `protocol/`, fields `ok` `data` `error` `elapsed` `meta`. Tools return this model instead of defining their own envelope. |
| 5 | Memory location and workspace lifecycle | B, C | Memory at `workspaces/<agent>/memory/<agent_id>.jsonl`. `inputs/` and `outputs/` are rebuilt per task; `memory/` survives cleanup and container rebuild. |
| 6 | `AgentProfile` | A model, C data | A defines the model in `protocol/`; C fills `profiles/*.yaml`. The runtime loads declared fields only. |
| 7 | Failure vocabulary | A | Enum in `protocol/`. C tags reflections with it and D aggregates by it. No module invents new strings. |
| 8 | Retry levels | A task, B tool | Task-level `max_retries` lives in the task graph; tool-level `tool_retries` lives in tool config. Metrics count task-level retries only. |
| 9 | `memory_enabled` | D, A, C | The harness passes it in `RunConfig`; the runtime forwards it to memory retrieval. Memory must run normally when false. |
| 10 | Goal verdict | D | `goal_passed` means the final artifact exists and its acceptance passes. It does not require every node to pass. |
| 11 | `docs/` layout | shared | Write access is shared; layout and naming are defined in `docs/README.md`. |
| 12 | HITL decision record | A | A writes the decision and assumptions into the task result; D's trace is a copy. On conflict, the task result wins. |

Contract 10 is why two completion figures are reported: a goal can be delivered
while a non-critical node failed, and the subtask figure keeps that visible instead
of hiding it.
