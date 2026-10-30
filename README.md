# MAIE5221-AgentVerse

A multi-agent collaboration platform. A user states a goal in natural language; the system decomposes it into tasks, dispatches them to agents with different roles, executes and verifies the work, and produces deliverables. All models are served through the DeepSeek API.

## Target capabilities

These describe the intended platform. The local implementation currently contains
Role A's offline core; production B/C/D adapters and full integration remain pending.

- Decomposes a single high-level goal into a task graph with explicit dependencies, where every subtask carries machine-checkable acceptance criteria
- Distributes work across heterogeneous agents: a Planner plans and delegates, Workers execute with tools inside their own sandboxes, and a Critic verifies results against the criteria, sending failing work back with a defect list
- Lets a Worker request a subtask when it lacks a needed capability; the scheduler reassigns that subtask to another agent
- Pauses to ask the human when it hits ambiguity or a risky operation; when no human is available, a predefined policy decides instead and every decision is logged
- Gives each agent its own working directory and private memory. Reflections are reused on similar tasks; their effect is measured in controlled experiments
- Records both goal-level and subtask-level completion rate plus execution time automatically, and supports batch runs over a test set

## Pipeline

```
User goal
  └→ Plan: Planner retrieves past experience, produces a task graph (with acceptance criteria)
       └→ Dispatch: scheduler assigns tasks to agents in dependency order
            └→ Execute: each agent calls tools inside its own sandbox
                 │  search · file read/write · code execution · git
                 ├ Missing capability → request a subtask → scheduler inserts a child node
                 │                    → verified as usual → result returned to requester
                 └→ Structured report (artifact paths · changes · self-assessment · blockers)
                      └→ Verify: Critic checks against the acceptance criteria
                           ├ Fail → returned with a defect list, up to a retry limit
                           └ Pass → artifacts collected
                                └→ Reflect: each agent writes a reflection to its private memory
                                     └→ Feeds the next task
```

Human-readable document deliverables use Markdown and plain text. Code,
configuration and machine-readable evaluation data keep their normal formats.

Project structure and cross-module contracts: [docs/overview.md](docs/overview.md).

- [使用说明与 B/C/D 接入指南](docs/usage-and-integration.md)
- [项目规划修订版](docs/project-plan.md)
- [修改日志](docs/changelog.md)

## Role A core: local development

The task contracts, planner, serial scheduler, Agent runtime, HITL gates and
artifact handoffs now have an offline implementation. B/C/D production adapters
are still required for the complete platform. No live evaluation results are
claimed by the offline tests.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check .
```

Use Python 3.11 or later. Tests use temporary workspaces and scripted model,
tool, memory and verdict fixtures; they require neither API keys nor Docker.

The entry point for D's CLI/harness is
`orchestrator.service.CollaborationService.run_goal(goal, config)`.
Use `orchestrator.bootstrap.build_service()` to assemble it with explicit adapters.
See [Section 2: implementation and adapter guide](docs/report/section-2.md) for
composition, contract changes and remaining integration work. The current real
provider adapter targets DeepSeek only; HKGAI integration requires its confirmed
API specification and a separate adapter.
