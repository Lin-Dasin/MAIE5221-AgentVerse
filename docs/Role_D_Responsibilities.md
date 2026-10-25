# Role D — Responsibilities and Development Plan

## 1. Role Overview

**Role:** D — Evaluation, Message Bus & User Interface

**Branch:** `Role_D`

**Responsible Directories:**
- `harness/` — Evaluation framework and metrics
- `bus/` — Message bus and event persistence
- `ui/` — Command-line interface
- `docs/` — Documentation, reports and experimental results

### Main Objective

Develop the evaluation, communication, and user interaction components of the Multi-Agent Collaboration System.

The main goal is to provide automated evaluation, reproducible execution traces, inter-agent communication, and a user-friendly interface.

## 2. Core Responsibilities

### 2.1 Evaluation Harness

Implement an automated evaluation framework to execute test tasks and measure system performance.

**Main Tasks:**
- Load test cases from predefined test suites.
- Trigger execution of tasks through the orchestrator.
- Measure execution time and record task status.
- Automatically evaluate outputs against acceptance criteria.
- Calculate goal completion rate and subtask completion rate.
- Support batch evaluation and export structured JSON results.
- Record failure reasons for further analysis.

**Expected Output:**
- Runnable evaluation harness.
- Automated metric calculations.
- JSON evaluation reports.

### 2.2 Message Bus and Event Management

Implement the communication infrastructure for interactions between agents and users.

**Main Tasks:**
- Define message and event formats with the team.
- Support message publishing and delivery.
- Support private communication between a user and an individual agent.
- Support group communication among users and multiple agents.
- Broadcast negotiation summaries, task assignments and delegation events.
- Implement event persistence using SQLite and JSONL.
- Support event history retrieval and replay.
- Keep internal tool payloads and private agent messages separate from public group messages.

**Expected Output:**
- Functional message bus.
- Persistent event records.
- Replayable communication history.

### 2.3 Command-Line Interface (CLI)

Develop a command-line interface for users to interact with the multi-agent system.

**Main Tasks:**
- Allow users to submit high-level goals.
- Support individual agent conversations and group conversations.
- Display agent responses and collaboration updates.
- Display task execution status.
- Show task results and evaluation summaries.
- Present errors and execution logs in a readable format.

**Expected Output:**
- Working CLI application.
- Demonstrable user-agent interaction workflow.

**Note:** CLI is the preferred implementation; the final UI choice requires team confirmation.

### 2.4 Execution Tracing and Reporting

Implement system observability and support experimental reporting.

**Main Tasks:**
- Record task execution events and status changes.
- Track execution time and token consumption.
- Log tool calls, HITL events and failures.
- Generate reproducible execution traces.
- Summarize goal and subtask completion rates.
- Analyze failure modes in mid and final test sets.
- Prepare performance tables, charts and screenshots.
- Contribute to Section 5 of the final report and the message bus subsection of Section 2.

**Expected Output:**
- Structured JSONL traces.
- Performance summaries and charts.
- Evaluation and failure analysis documentation.

## 3. Development Schedule

| Week | Main Tasks |
|---|---|
| W8 | Build harness skeleton, define trace format, implement initial event bus and completion metrics |
| W9 | Add event persistence and replay, batch execution and JSON result export |
| W10 | Prepare development test set, integrate modules and produce initial evaluation results |
| W11 | Execute mid test set, calculate metrics and classify failures |
| W12 | Analyze failure modes, prepare charts and validate improvements |
| W13 | Execute final test set, support demonstration, merge documentation and collect screenshots |
| W14 | Consolidate and finalize the evaluation report |

## 4. Integration with Other Roles

### Role A — Orchestrator
- Coordinate task execution entry points.
- Agree on task, message and event schemas.
- Receive execution status and acceptance results.

### Role B — Tools and Sandbox
- Collect tool execution events and errors.
- Record execution duration and failure information.
- Integrate tool-level tracing.

### Role C — Memory and Evolution
- Support evaluation of memory-enabled and memory-disabled runs.
- Record experimental metrics.
- Help compare completion rates and execution performance.

All cross-module interfaces should follow the shared models defined in `protocol/`.

## 5. Deliverables

- [ ] Automated evaluation harness
- [ ] Goal and subtask completion metrics
- [ ] Message bus with persistent event history
- [ ] Event replay functionality
- [ ] Working CLI interface
- [ ] Structured execution tracing
- [ ] Batch evaluation JSON reports
- [ ] Development, mid and final test results
- [ ] Performance and failure analysis
- [ ] Documentation and commit evidence

## 6. Development and Collaboration Rules

- Develop all assigned features on branch `Role_D`.
- Submit changes to `develop` through Pull Requests.
- Require at least one reviewer before merging.
- Use meaningful Conventional Commit messages.
- Preserve commit history using merge commits.
- Push progress regularly and integrate with `develop` at least every three days.
- Run relevant tests before submitting Pull Requests.
- Coordinate cross-module changes with the corresponding owner.

## 7. Success Criteria

Role D is considered complete when:

1. The evaluation harness can automatically run test cases and produce valid results.
2. Both goal-level and subtask-level completion rates are calculated correctly.
3. Execution traces and event logs can be saved and inspected.
4. The message bus supports the required communication modes and event replay.
5. Users can interact with the system through the agreed interface.
6. Test results and failure analysis can be reproduced and included in the final report.
