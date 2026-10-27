# Role C Work Specification: Memory, Evolution, and Experiments

**Project:** Multi-Agent Collaboration System  
**Role branch:** `Role_C`  
**Code ownership:** `memory/`, `profiles/`  
**Report ownership:** Section 4, Memory Mechanism and Experimental Results  
**Effective date:** 2026-10-25

## 1. Role Objective

Role C is responsible for providing each agent with independent, traceable, reusable task memory, and for using reproducible experiments to demonstrate whether the memory mechanism improves task completion or execution efficiency.

In this project, Evolution means cross-task memory: after a task, the system records outcomes and reusable lessons; when a similar task arrives later, the agent retrieves and applies relevant lessons. It is not model training, fine-tuning, or a vector-database project.

The final system must demonstrate both of the following:

1. Different agents have isolated memories and cannot read or contaminate one another's memory.
2. Given the same task set, model configuration, and acceptance criteria, runs with memory enabled outperform, or at minimum differ explainably from, the baseline with memory disabled.

## 2. Scope of Responsibility

### 2.1 Required Deliverables

- Design and implement read/write interfaces for private agent memory.
- Define a structured reflection record covering at least: task summary, success or failure, failure category, effective strategies, ineffective strategies, reusable advice, and timestamp.
- Provide an independent role profile and tool-allowlist configuration for each agent.
- Before execution, retrieve task-relevant memory and produce concise text that can be injected into the agent context.
- After execution, write a reflection record based on the task result, Critic acceptance result, and trace.
- Design and run an A/B experiment comparing memory-disabled and memory-enabled runs.
- Write report Section 4, explaining the mechanism, experimental setup, metrics, results, failure cases, and limitations.

### 2.2 Explicitly Out of Scope

- Task graph schema, task scheduling, sub-delegation, and HITL decision logic are owned by Role A.
- Docker, container isolation, file tools, code execution, search, and git tools are owned by Role B.
- The primary harness flow, global metric computation, message bus, CLI, and final chart consolidation are owned by Role D.
- Do not introduce a vector database, external database, model fine-tuning, or complex multi-round reflection chains unless the team explicitly approves them.

## 3. Minimum Viable Design

Build a stable, testable minimum version first; optimize only after the full loop works.

### 3.1 Data Storage

- Each agent owns one memory file, for example `memory/data/<agent_id>.jsonl`.
- Each line is one JSON record, allowing appending, diffing, filtering, and replay.
- A memory file may only be read or written by its corresponding agent memory module. Other agents must not read it directly.
- Runtime-generated data must not be committed to Git. Commit only the schema, examples, and test fixtures.

Recommended record format:

```json
{
  "memory_id": "mem-0001",
  "agent_id": "researcher",
  "task_type": "web_research",
  "task_summary": "Collect pricing for three competitors with sources",
  "outcome": "success",
  "failure_category": null,
  "effective_strategy": ["Identify candidate companies first, then verify each official source"],
  "avoid": ["Do not cite aggregator pages without publication dates"],
  "reusable_advice": "Pricing information must retain both the source URL and access date.",
  "evidence_task_id": "dev-03",
  "created_at": "2026-11-05T10:20:00Z"
}
```

### 3.2 Retrieval Strategy

The first version should use keyword overlap and string similarity only. The expected memory volume is small, making this approach more stable and reproducible than adding a vector database.

- Input: current agent ID, task title, task description, and task type.
- Filter: query only the current agent's private memory.
- Ranking: prioritize matching task types, then rank by keyword or string similarity.
- Output: return at most three relevant lessons, each length-limited to avoid consuming excessive model context.
- No match: return an empty result and allow the system to run normally. This must not be treated as an error.

### 3.3 Reflection Generation Strategy

Reflection should be based on structured execution facts, rather than asking the model to improvise. Inputs include the task description, tool results, artifact paths, Critic acceptance result, defect list, and duration.

The first version may generate reflections using a fixed template. If an LLM is used, the prompt must be fixed and versioned for experimental reproducibility. Persist only valuable, reusable conclusions to avoid storing noise and transient context.

## 4. External Interface Contract

Role C implements the following interfaces. Exact fields follow the `protocol/` models finalized by Role A. Modules must not exchange untyped dictionaries.

```python
def retrieve_memory(agent_id: str, task: Task) -> list[MemoryRecord]:
    """Return private memories most relevant to the current task."""

def build_memory_context(records: list[MemoryRecord]) -> str:
    """Compress retrieved records into context that can be injected into an agent prompt."""

def write_reflection(
    agent_id: str,
    task: Task,
    result: TaskResult,
    critique: CriticResult,
) -> MemoryRecord:
    """Generate and persist a reflection from accepted execution facts."""
```

### Handoff with Role A

- Role A calls `retrieve_memory()` and `build_memory_context()` before the agent's main execution loop begins.
- After a task ends, retries are exhausted, or the Critic reaches a final result, Role A supplies structured `TaskResult` and `CriticResult` objects to Role C.
- Role C does not modify scheduling rules. Role A arbitrates interface or field changes and must update the models in `protocol/` first.

### Handoff with Role D

- Role D's harness passes the experiment mode: `memory_enabled=true/false`.
- Role D produces standardized task-level results: task status, duration, acceptance counts, failure reason, token count, and trace path.
- Role C analyzes both groups and provides Section 4 metrics, conclusions, and chart data to Role D. Role D standardizes presentation only and must not rewrite the experimental argument.

### Handoff with Role B

- Memory files and profile configuration must comply with Role B's path-validation and container-isolation rules.
- Role C must not bypass the tool layer to read another agent's workspace or private memory.

## 5. Timeline and Exit Criteria

### W8: Oct 19-25, Contract and Prototype

Objective: complete the data design and minimal loop.

- Confirm minimum `Task`, `TaskResult`, and `CriticResult` fields with Role A.
- Define the `MemoryRecord` Pydantic model and JSONL examples.
- Define a failure-category taxonomy, for example: `tool_error`, `timeout`, `invalid_output`, `missing_input`, `hitl_blocked`, and `model_reasoning_error`.
- Use mock data to test the write -> retrieve -> context-injection loop.

Exit criteria: interface signatures, sample data, tests, and profile configuration examples are merged into `develop`.

### W9: Oct 26-Nov 1, Memory Module Implementation

Objective: `memory/` and `profiles/` run independently.

- Implement JSONL append, read, validation, and error handling.
- Implement agent-isolated retrieval and ranking.
- Implement context compression so empty memory or corrupt records never terminate a task.
- Write unit tests for isolation, filtering, ranking, length limits, and no-match fallback.

Exit criteria: unit tests pass, and a script demonstrates that different agents retrieve different memories.

### W10: Nov 2-8, End-to-End Integration

Objective: a real task writes memory on its first run, and a later similar task retrieves and uses it.

- Integrate with Role A's agent main loop.
- Generate reflections from the Critic's final acceptance result.
- Integrate Role D's `memory_enabled` switch and task-result collection.
- Prepare at least three repeatable groups of similar tasks as A/B experiment candidates.

Exit criteria: a dev-set trace demonstrates a full loop: a first run records experience after success or failure, and a second run reads that experience.

### W11: Nov 9-15, A/B Experiment

Objective: obtain the first report-ready dataset.

- Freeze the task set, model, prompt version, token budget, acceptance rules, and runtime environment.
- Run memory-disabled and memory-enabled groups; repeat runs where needed to reduce randomness.
- Record overarching-goal completion rate, subtask completion rate, average duration, failure categories, and token consumption.
- Separate real memory benefits from model randomness or task differences.

Exit criteria: raw result files, an experiment description, data for at least one comparison chart, and failure examples are available.

### W12: Nov 16-22, Hardening and Report Draft

Objective: improve the mechanism based on mid-test results and freeze experimental conclusions.

- Remove low-quality or duplicate memories and correct retrieval-ranking issues.
- Re-run affected experiments so results before and after changes remain explainable.
- Complete the first draft of report Section 4: motivation, implementation, experimental setup, results, and limitations.

Exit criteria: A/B data is complete, conclusions are reviewable, and Section 4 can be merged into the final report.

### W13: Nov 23-29, Final Support

Objective: support final testing and the demo, and finalize Section 4.

- Help identify memory-related failures during final testing.
- Provide explainable memory examples for the demo.
- Update Section 4 using final data without high-risk architecture refactors.

Exit criteria: the final trace shows when memory was written, when it was retrieved, and how it affected task execution.

## 6. A/B Experiment Rules

The experiment must be reproducible and fair:

1. Both groups use the same task set, acceptance criteria, model configuration, tool permissions, and token budget.
2. The only variable is whether historical memory is injected. The memory-disabled group does not read previous records; the memory-enabled group reads only the current agent's prior records.
3. Record both completion-rate definitions, average duration, token consumption, and failure-reason distribution. Do not select only cases favorable to memory.
4. If there is no meaningful improvement, report that honestly and analyze likely causes, such as irrelevant retrieval, tasks lacking recurring patterns, or model variation.
5. Do not claim that memory improves performance without raw run records, frozen configuration, and corresponding traces.

## 7. Branch and Commit Guidance

- Use `Role_C` as the long-lived personal branch. Use a temporary branch such as `Role_C/exp-memory-ab` only for isolated experiments, then delete it after merging.
- Push daily and never leave local work unpushed for more than two days. Merge to `develop` through a PR at least every three days.
- Before opening a PR, merge `develop` into the personal branch and resolve conflicts. Do not use squash or rebase merges.
- Every PR description must state: what changed, how it was verified, and its impact on Role A/D interfaces.

Suggested initial commits:

```text
docs(memory): add Role C memory mechanism work specification
feat(memory): define MemoryRecord and JSONL storage interface
feat(profiles): add example agent tool allowlist configuration
test(memory): cover private isolation and similar-task retrieval
feat(memory): integrate task reflection and context construction
test(memory): add fixtures for memory-toggle comparison experiments
docs(report): add memory A/B experiment design
```

## 8. Risks and Escalation Conditions

- If Role A has not frozen the protocol by the end of W8: implement storage and retrieval independently of unstable fields first. Do not integrate until protocol changes are confirmed in the group.
- If Role D has not provided the switch or result format by W10: collect experimental data with an independent script, but do not modify the primary harness flow unilaterally.
- If memory injection expands context or harms results: limit output to three high-relevance records and allow the system to run with memory disabled.
- If the same issue blocks work for more than half a day, requires edits outside this ownership boundary, or has an unclear interface meaning: report it to the group first; do not modify another owner's module directly.

## 9. Definition of Personal Completion

Role C is complete only when all of the following are true:

- `memory/` and `profiles/` contain independent, testable, maintainable implementations.
- Every agent's memory is demonstrably independent and does not prevent memory-disabled execution.
- An end-to-end trace shows the time, subject, and content summary of memory writes and retrievals.
- The A/B experiment has reproducible configuration, raw results, and an honest conclusion.
- Report Section 4, personal commits, and PR history together demonstrate sustained, substantive contribution.
