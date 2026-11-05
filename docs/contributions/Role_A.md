# Individual Responsibilities and Work Plan

## Basic Information

- Name: Wang
- GitHub Username: Luhkel-04
- Project Role: A — Task Orchestration and Agent Runtime Lead. Responsible for core interface contracts, task scheduling, the HITL gate, and the Agent execution loop; leads early interface alignment and the first integration; serves as the lead author of the architecture overview and report Section 2, “Routing and Collaboration Logic”; and prepares the W13 demonstration script.
- Personal Development Branch: `Role_A`.


## Responsibilities

- Modules Owned: `protocol/` (data models and interface contracts), `orchestrator/` (planning, scheduling, HITL, and artifact handoffs), and `runtime/` (the Agent execution loop and centralized model calls). Also maintains the corresponding tests, interface documentation, and assigned report sections.
- Features to Implement: An orchestration workflow covering user goal submission, task decomposition, role negotiation, dependency scheduling, tool calls, Critic acceptance checks, deliverable aggregation, and memory updates, as detailed below.

| Feature | Implementation Responsibilities for A | Expected Behavior When Complete |
| --- | --- | --- |
| Task graph and cross-module contracts | Use Pydantic to define task nodes, task graphs, execution results, acceptance results, sub-delegation requests, and HITL requests; specify required fields, states, and error information | Validate task IDs, dependencies, cycles, assigned roles, and required fields; use shared models for cross-module calls and relative paths to declare input artifacts |
| Planning and role negotiation | Have the Planner generate a task graph from the goal and retrieved memories; limit planning negotiation rounds and produce validated structured results with machine-checkable acceptance criteria | Complete task decomposition and role assignment within the configured budget; publish negotiation summaries and task-claim information to the group chat through D’s message bus |
| Deterministic scheduling | Dispatch tasks according to dependencies and manage execution, waiting, acceptance, retry, success, and failure states | Start downstream tasks only after upstream acceptance checks pass; provide clear failure reasons and terminal states without unlimited retries or indefinite waiting |
| Worker sub-delegation | Receive subtask requests, validate and insert them into the task graph through the scheduler, and establish parent-child relationships, waiting behavior, and result return paths | Limit sub-delegation depth to 1 and allow at most 2 subtasks per parent task; route every subtask through normal execution and acceptance checks and include it in the metrics |
| Agent execution loop | Connect incoming messages, memory retrieval, model decisions, tool requests, structured reporting, Critic acceptance checks, and post-task reflection; centralize model calls | Run Planner, Worker, and Critic agents according to their respective configurations; call only DeepSeek or HKGAI endpoints and support call budgets, timeouts, retries, and error reporting |
| HITL gate | Define interchangeable interactive and policy-based approvers; handle clarification, permission, and correction requests, and record decisions and assumptions | Allow interactive execution to pause and resume; in unattended mode, approve, reject, or apply permitted conservative defaults according to predefined rules without waiting indefinitely for human input |
| Acceptance checks and artifact handoffs | Integrate pluggable acceptance strategies; distribute inputs before task execution, archive outputs after acceptance, and return defect lists to Workers for bounded rework when acceptance fails | Have the orchestrator transfer copies between `artifacts/<run_id>/<task_id>/` and the assigned Agent’s workspace; do not make unaccepted outputs available to downstream tasks |
| Message routing and observable events | Align private, group, and internal message routing with D; emit events for task assignment, sub-delegation, HITL, acceptance checks, and artifact handoffs | Keep private chats visible only to the relevant human and Agent; show role claims and delegation summaries in group chats; do not broadcast raw internal tool messages to group chats |

- Scope of This Work: Focus on A’s three code directories, core contract definition, the first end-to-end integration in W10, architectural fixes, and demonstration workflow design. In W8, coordinate repository setup, basic CI, and runtime environment alignment. After the W10, W12, and W13 milestones are met, open PRs from `develop` to `main`. From W11 onward, B shares integration troubleshooting and D owns metric regression checks, while A focuses on orchestration defects, interface decisions, and architecture decisions.
- Responsibility Boundaries: B implements tools and container isolation; C implements memory, reflection, retrieval, and Agent profiles; D implements the message bus, CLI, harness, trace persistence, and metric aggregation. A provides integration interfaces, entry points, and events while the respective owners remain responsible for those modules. Changes to another member’s directories must be communicated and coordinated with the team beforehand.
- Technical Boundaries: Human-readable document deliverables must use `.md` or `.txt`; the UI must remain simple; actual model services must be DeepSeek or HKGAI only. Implement serial scheduling first, then collaborate with B to support limited concurrency for independent tasks when justified by measurements.

## Weekly Deliverables

| Week and Dates | Planned Work | Deliverables | Weekly Acceptance Criteria |
| --- | --- | --- | --- |
| **Week 8: October 19–25** | Lead alignment on task graph, execution result, HITL, sub-delegation, and event contracts; coordinate the repository, branch protection, CI, and runtime environment; build a minimal orchestration prototype | Contract v1; task graph and message examples; core models and validation tests; repository skeleton and basic CI; records of technology stack and runtime environment decisions | Contract v1 is confirmed by all four members and merged into `develop`; every member can develop against the same models; CI can run basic checks. Any unfinished items become prerequisites to address first in W9 |
| **Week 9: October 26–November 1** | Implement dependency scheduling, state transitions, retry limits, sub-delegation node insertion, and the Agent execution loop skeleton; integrate B’s search/file tools, C’s memory interfaces, and D’s message bus | Independently runnable scheduler; runtime skeleton and centralized model client; sub-delegation validation and scheduling tests; a text-task workflow using interface test doubles | Tasks execute serially in dependency order; subtasks can be inserted, accepted, and returned to their parent tasks; invalid task graphs and excessive delegation produce clear errors; organize interface checks across the four modules, with complete code and git tool integration verified in W10 |
| **Week 10: November 2–8** | Lead the first end-to-end integration; complete interactive/policy-based HITL, artifact handoffs, and the full sub-delegation workflow; integrate B’s code execution and git tools, C’s reflection injection, and D’s harness | Runnable text-task and code-task workflows; clarification and permission HITL scenarios; end-to-end tests; first integration records; a stable-release PR | **By November 8**, work with D to produce goal completion rate, subtask completion rate, and execution time on the team’s dev set; policy-based HITL requires no human waiting; all four tool categories work; A opens the stable-release PR |
| **Week 11: November 9–15** | Use the mid test set or dev set regression runs to identify routing, dependency, state-transition, retry, and sub-delegation issues; provide a consistent orchestration entry point for C’s memory A/B experiments; prepare a draft of Section 2 | Orchestration failure analysis; fix PRs and regression cases; draft routing/collaboration section; baseline interfaces and architecture | Each orchestration failure can be linked to a trace, cause, and resolution or disposition; critical defects are covered by regression tests; troubleshooting and metric regression responsibilities are coordinated with B and D |
| **Week 12: November 16–22** | Harden orchestration against confirmed issues and reduce duplicate calls and ineffective retries; integrate necessary controlled concurrency with B; improve exceptional terminal states and interface documentation | Hardened version; regression results and before/after comparisons; finalized interface/state documentation; draft demonstration workflow; a stable-release PR | Major interfaces remain stable; optimizations preserve dependency order, sub-delegation limits, HITL behavior, and acceptance-before-handoff rules; metric changes are explained with evidence; A opens the hardening-release PR |
| **Week 13: November 23–29** | Write and rehearse the live demonstration; support D in formal evaluation and evidence collection; check the architecture document and Section 2; complete pre-submission integration checks | Demonstration script in `.md`; startup and operating instructions; reproducible examples and expected events; a review-ready Section 2; an index of genuine personal commits/PRs; a demonstration-release PR | The demonstration covers private/group chats, task decomposition, sub-delegation, HITL, all four tool categories, and memory calls; formal metrics are traceable to run records; documentation matches the actual version |

Additional arrangements:

- If a test set has not yet been released, continue integration and regression work using the dev set maintained by D. Do not treat estimated release dates as course commitments or label dev set scores as formal test results. Run the official evaluation as required after the relevant test set is released.
- Establish the main architecture baseline by the end of W11. Focus W12 on necessary fixes and hardening, and W13 on reproducible demonstrations and finalization. Avoid major interface changes close to submission.
- Record actual completed work, validation results, and blockers in PR descriptions or a tracking issue, following `docs/README.md`; do not create `docs/weekly/`. Follow team guidelines by planning at least 3 substantive commits per week, promptly pushing completed changes each day, and integrating into `develop` through a PR at least every 3 days. Commit content, authorship, and timestamps must reflect the actual work performed.
- Deliver a review-ready Section 2 in W13. Finalize it in W14 or later according to the official deadline, and support D in assembling the report and commit-history screenshots.

## Deliverables

The paths below indicate planned code and documentation locations. Specific filenames will be finalized through team agreement on the contracts; their inclusion here does not mean the files have already been implemented.

- Code: Cross-module data models and validation in `protocol/`; the planner, dependency scheduler, sub-delegation handling, HITL gate, acceptance workflow, and artifact handoffs in `orchestrator/`; the Agent execution loop and centralized model client in `runtime/`; and basic CI configuration and interface examples within A’s scope. Submit code through `Role_A` and integrate it through reviewed PRs.
- Tests: Tests under `tests/` owned by A for model validation, dependency ordering, state transitions, retry limits, sub-delegation, HITL, and artifact handoffs; integration tests completed jointly with B/C/D for text tasks, code tasks, and unattended batch execution. Use model-interface test doubles to support stable unit tests, and retain records of integration validation against actual permitted endpoints.
- Documentation: The architecture overview and Section 2, “Routing and Collaboration Logic”; task graph, state-transition, HITL, and cross-module interface specifications; orchestration failure analysis and fix records; the W13 demonstration script; progress in PRs/tracking issues; and an index of personal commits/PRs. Record contract and documentation changes in `docs/changelog.md`. Align the message-bus subsection of Section 2 with D. All written documentation must use Markdown or plain text.

## Collaboration and Dependencies

- Inputs Needed from Other Members:

| Member | Required Inputs | Purpose and Timing for A |
| --- | --- | --- |
| **B: Tools and Sandboxes** | A shared tool execution entry point, tool parameter and result models, container workspace mappings, execution time limits and error returns; search/file/code/git capabilities; interfaces needed for artifact reads, writes, and handoffs | Align contracts in W8, integrate prototypes in W9, and connect all four tool categories in W10; use these capabilities to execute tasks and transfer artifacts; share integration troubleshooting from W11 onward |
| **C: Memory and Profiles** | Interfaces for memory retrieval, writing, and reflection; Agent identities and tool allowlist configurations; a memory enable/disable switch and A/B experiment execution conventions | Align data structures in W8, integrate memory reads/writes in W9, and complete the reflection loop in W10; allow the execution loop to retrieve experience before execution and store experience afterward |
| **D: Message Bus, CLI, and Evaluation** | Private/group message models and publish/subscribe entry points; HITL question-and-answer interaction; trace event fields; the harness entry point and dev set; acceptance-checking and metric interfaces | Finalize event contracts in W8, integrate the message bus in W9, and complete batch evaluation in W10; provide subsequent regression results and demonstration data |

- Outputs to Provide to Other Members:

| Recipient | Outputs to Be Provided by A |
| --- | --- |
| **B** | Validated task, tool-call, and result contracts; execution context, timeout/budget conventions, workspace and artifact-handoff requests; orchestration examples that allow tool integration to be checked independently |
| **C** | Agent/task identifiers, retrieval timing, execution summaries, Critic defect reports, and final states; reflection write timing; an integration point for enabling/disabling memory while keeping other conditions consistent |
| **D** | A stable system execution entry point and structured return values; relationships among top-level goals, task nodes, and dynamically inserted child nodes; stage timing, HITL, acceptance, and artifact events; Section 2 text and demonstration steps |
| **Entire Team** | Contract v1, task graph examples, interface change records, integration checklists, milestone releases, and architecture decision records |

- Interfaces Requiring Joint Confirmation:

| Interface or Convention | Items to Confirm | Main Participants |
| --- | --- | --- |
| Task graph | Types, constraints, and defaults for `task_id`, `assignee`, `parent_task`, `depth`, `depends_on`, `inputs`, `acceptance`, `outputs`, and `max_retries`; how acceptance criteria are represented as machine-checkable rules | A/B/C/D |
| States and results | Worker outputs, Critic decisions, failure reasons, and the meaning of retry counts; how downstream tasks terminate after a dependency fails; the success criteria for a top-level goal | A/B/D |
| Sub-delegation | Request fields, depth and count limits, parent-task waiting/resumption conditions, subtask failure propagation, and the counting rules for dynamic nodes | A/B/D |
| Tools and sandboxes | Tool names, parameters, results, execution timeouts, permission denials, and error categories; file and code tools execute inside containers, while search and git tools execute through the host-side tool layer | A/B, with C providing tool allowlists |
| Artifact handoffs | Input sources, destination relative paths, accepted status, archiving timing, artifact version handling after retries of the same task, and handoff events | A/B/D |
| Memory and reflection | Memory retrieval inputs/outputs, Agent isolation identifiers, records written after task completion, reflection triggers, and behavior when memory is disabled | A/C |
| HITL | Request IDs, session ownership, request types, context, available decisions, timeout/rejection rules, pause/resume behavior, and records of policy-based decisions | A/D, with B aligning critical tool operations |
| Message bus and traces | `run_id`, session IDs, task IDs, Agent IDs, event types, timestamps, and duration fields; private/group visibility; dynamic subtask and artifact-handoff records | A/D |
| Acceptance checks and harness | Call/return models and implementation ownership for strategies such as `file_exists`, `text_contains`, and `pytest_passes`; execution entry points, terminal states, goal/subtask denominators, whether retries count more than once, and timing start/end points | A/D, with B providing code execution capabilities |
| Model and runtime configuration | DeepSeek/HKGAI endpoints, model configuration, call budgets, retries, and structured-output capabilities; CI and local runtime environments | A/B/C/D |

Collaboration follows the `Role_A → develop → main` PR workflow, requires at least one reviewer, and uses merge commits to preserve the actual development history. PRs must describe the changes, validation methods, and affected modules. Synchronize with `develop` and resolve conflicts before merging. Notify the team promptly when blocked for more than half a day, when a merge conflict cannot be resolved confidently, or when another member’s files need modification.

## Timeline

- Phase 1: **Contracts and Independent Development (W8–W9, October 19–November 1)**. Confirm data models, interfaces, and module boundaries; establish the repository and CI; and complete the scheduler, execution loop, and sub-delegation skeleton. Prioritize unfinished W8 items in W9. Exit criteria: stable Contract v1, passing unit tests for A’s modules, and a complete scheduling workflow that can run using interface test doubles.
- Phase 2: **First Integration and Evaluation Baseline (W10, November 2–8)**. Integrate all four tool categories, memory, the message bus, CLI, and harness; complete HITL and artifact handoffs after acceptance. **The first set of dev set metrics must be produced by November 8.** D verifies the metric outputs, while A coordinates orchestration fixes and opens the stable-release PR.
- Phase 3: **Issue Resolution, Hardening, and Delivery (W11–W13, November 9–29)**. Fix architectural issues based on the test sets actually released and dev set regression results; support memory A/B experiments; complete W12 hardening and the W13 demonstration. From W11 onward, B shares integration troubleshooting and D handles metric regression checks. A remains responsible for core interfaces and architecture, the demonstration script, and the Section 2 manuscript. From W14 onward, finalize the report and support submission according to course announcements.

## Acceptance Criteria

- [ ] **Core features completed**: `protocol/`, `orchestrator/`, and `runtime/` are implemented and runnable through a shared entry point; high-level goals can be converted into task graphs, with working dependency scheduling, role assignment, and structured reporting.
- [ ] **Complete sub-delegation workflow**: Subtasks are inserted by the scheduler, with a maximum depth of 1 and no more than 2 subtasks per parent; parent tasks can wait for and receive results; subtasks undergo acceptance checks and are included in the metrics.
- [ ] **HITL supports demonstrations and batch execution**: Demonstrate at least clarification and permission scenarios; interactive execution can pause and resume; policy-based execution follows predefined rules and records decisions without permanently blocking evaluation.
- [ ] **Correct artifact handoffs**: Inputs are distributed according to task declarations; only artifacts accepted by the Critic are archived and made available to downstream tasks; handoffs generate traceable events.
- [ ] **Relevant tests pass**: Tests for task graph validation, dependency ordering, retry limits, sub-delegation, HITL, failure propagation, and artifact handoffs pass; CI formatting checks and agreed test suites also pass.
- [ ] **Documentation complete**: Core contracts, state transitions, the architecture overview, Section 2, the demonstration script, and personal progress records are complete and consistent with the actual code.
- [ ] **Integration with other modules complete**: End-to-end validation passes for B’s tools/sandboxes, C’s memory/profiles, and D’s message bus/CLI/harness; private/group chats and visibility rules meet the agreed requirements.
- [ ] **Evaluation data can be verified**: Establish the dev set baseline by November 8; retain complete records for goal completion rate, subtask completion rate including dynamic child nodes, and execution time; retries do not increase the count of logical tasks, and omitted task states do not inflate results.
- [ ] **Course and team constraints satisfied**: Runtime model calls use only DeepSeek or HKGAI services; written documentation uses Markdown/plain text only; git tools operate on a separate sandbox repository as agreed by the team; Agents execute in the isolated environments provided by B.
- [ ] **Collaboration and delivery evidence complete**: Personal contributions can be verified against genuine commits, PRs, test records, and progress issues; complete the W13 demonstration and support report finalization and screenshot collection in W14 and beyond.

## Current Integration Reference

Use [the integration guide](../usage-and-integration.md) and the exact signatures
in `protocol/ports.py`. A provides `orchestrator.bootstrap.build_service()` for D
to assemble the service with B/C/D adapters. The first live integration remains
pending. A must still coordinate chat entry points, Planner memory and the Critic
agent lifecycle; current Worker hooks and deterministic verdicts do not complete
those requirements. Weekly progress evidence lives in PRs/tracking issues, not a
separate weekly-report directory.
