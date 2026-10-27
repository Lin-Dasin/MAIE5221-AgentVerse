# MAIE5221-AgentVerse

A multi-agent collaboration platform. A user states a goal in natural language; the system decomposes it into tasks, dispatches them to agents with different roles, executes and verifies the work, and produces deliverables. All models are served through the DeepSeek API.

## What it does

- Decomposes a single high-level goal into a task graph with explicit dependencies, where every subtask carries machine-checkable acceptance criteria
- Distributes work across heterogeneous agents: a Planner plans and delegates, Workers execute with tools inside their own sandboxes, and a Critic verifies results against the criteria, sending failing work back with a defect list
- Lets a Worker request a subtask when it lacks a needed capability; the scheduler reassigns that subtask to another agent
- Pauses to ask the human when it hits ambiguity or a risky operation; when no human is available, a predefined policy decides instead and every decision is logged
- Gives each agent its own working directory and private memory. After a task, the agent writes a reflection; before a similar task, it retrieves and reuses that reflection, so it improves over repeated runs
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

Deliverables are limited to Markdown and plain text.

Project structure and cross-module contracts: [docs/overview.md](docs/overview.md).
