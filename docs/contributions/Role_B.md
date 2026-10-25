# Role_B — Tools & Sandbox · Work Plan

**Member**: Lin
**Role**: Role_B — Tools & Sandbox
**Branch**: `Role_B`
**Code directories**: `tools/`, `sandbox/`, `docker/`
**Report chapter**: Architecture Document §3 — Sandbox Implementation and Threat Model

---

## 1. Team Roles

> The source of truth for this table is Section 4.1 of the internal project specification. If anything diverges, that document wins.

| Role | Scope | Code directories | Report chapter |
|---|---|---|---|
| A | Task-graph schema, scheduler, HITL gate, agent main loop | `protocol/` `orchestrator/` `runtime/` | §2 Routing and collaboration logic |
| **B** | **Four tool families, time-limited execution, container and directory isolation, git integration, stability** | **`tools/` `sandbox/`** | **§3 Sandbox implementation and threat model** |
| C | Memory read/write, reflection generation and retrieval, A/B experiments | `memory/` `profiles/` | §4 Memory mechanism and experimental data |
| D | harness, metrics, trace, message bus, CLI | `harness/` `ui/` `bus/` `docs/` | §5 Metrics and failure-mode analysis; §2 message-bus subsection |

---

## 2. Scope

**In scope**

- Implementation of four tool families: web search, file CRUD, code execution, git commit and push
- An isolated execution environment per agent: container isolation, directory isolation, path-traversal validation
- Tool-call timeouts, resource caps, retry policy, and crash recovery
- Trace instrumentation at the tool layer (per-call latency, success/failure, degradation flags)

**Out of scope** (to avoid touching files owned by others)

| Item | Owner |
|---|---|
| Agent main loop, task-graph schema, scheduling logic | A |
| Memory storage policy, per-agent prompts and tool allowlists | C |
| harness verdict logic, metric aggregation, CLI | D |

**Interfaces**

| Counterpart | What I provide | What I need |
|---|---|---|
| A | A uniform Tool interface (JSON-schema input, structured output) | Tool-call schema convention, workspace directory convention |
| C | Workspace path-resolution API (where memory files live) | Memory file naming convention per agent |
| D | Tool-layer trace events (latency, success, `search_degraded` flag) | Field format for trace events |

---

## 3. Weekly Work Plan

| Week | Tasks | Verifiable output | Acceptance |
|---|---|---|---|
| W8 10/19–10/25 | Container baseline: Dockerfile and compose able to start 3 long-running containers; executor prototype; path-traversal validation | `docker/` brings the stack up with one command; `sandbox/executor.py` runs code and captures stdout/stderr/exit code | Unit tests cover three cases: path traversal, execution timeout, non-zero exit |
| W9 10/26–11/1 | Search tool (API first, scraping fallback); file read/write tool; unified Tool interface | `tools/search.py`, `tools/fs.py`; JSON schema per tool | Unit tests pass; each tool is invoked at least once from the real agent loop |
| W10 11/2–11/8 | Code-execution tool (time and memory capped); git tool (host side, pushes to a sandbox repo); fix integration defects | `tools/code_exec.py`, `tools/git.py` | Code-type tasks in the dev set run end to end; push succeeds against the sandbox repo with no change to the main repo |
| W11 11/9–11/15 | Timeout policy (tool level and task level); crash recovery (container restart, stale-artifact cleanup) | `sandbox/health.py` | Killing a container manually leads to recovery or a clean failure with no dirty files left |
| W12 11/16–11/22 | Concurrency and rate limiting (configurable concurrency, backoff on 429); one-command environment startup | Startup script, concurrency config | Three concurrent runs keep separate logs; a fresh clone is runnable within ten minutes |
| W13 11/23–11/29 | Environment fallback, on-site support for the demo | Reproduction guide | The demo script completes on a clean environment |
| W14 onward | Finalise Architecture Document §3 | §3 chapter | Consistent with the code, threat model included |

---

## 4. Deliverables

- **Code**: `tools/` (four tool families), `sandbox/` (container and execution), `docker/` (image and orchestration)
- **Documentation**: Architecture Document §3, covering the isolation model, path-validation rules, threat model, and resource caps
- **Demo**: a tool-chain segment showing search, file write, code execution, and git commit in sequence

---

## 5. Dependencies

**What I need from others**

| Item | From | Needed by | Fallback if late |
|---|---|---|---|
| Tool-call schema (input and output shapes) | A | End of W8 | Implement against a draft JSON schema, adapt the wrapper layer once the contract is final |
| Workspace directory convention (`workspaces/<agent>/`, `artifacts/<task_id>/`) | A | End of W8 | Implement per Section 1.5 of the specification |
| Trace event field format | D | Early W9 | Instrument the minimum fields first: tool name, latency, success |

**What others need from me**

| Item | For | Ready by |
|---|---|---|
| Callable Tool interface | A | Mid W9 |
| Workspace path-resolution API | C | Early W9 |
| Tool-layer trace instrumentation | D | Mid W9 |

---

## 6. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Container path mapping misbehaves on Windows | File tools cannot see artifacts | Run inside WSL2 paths only, never `/mnt/c`; validate mount points at startup |
| Multiple containers exhaust memory | The 16 GB host starts swapping | Cap each container at 512 MB–1 GB; start and stop on demand |
| Git credentials leak | Generated code reads or exfiltrates the token | Run the git tool on the host only; the token never enters a container |
| Search rate limiting or IP blocking | Task timeouts, lower completion rate | API first with scraping fallback; emit a `search_degraded` flag for later statistics |
| Inconsistent tool return shapes | A has to write per-tool special cases | All tools share one envelope: `{ok, data, error, elapsed, meta}` |

---

## 7. Definition of Done

A task is done only when all four hold:

1. Code is merged into `develop` through a reviewed pull request
2. Unit tests exist and CI passes
3. The code has been invoked at least once from the real agent loop, not only in unit tests
4. Failure paths are logged well enough to identify which step broke

---

*This document is updated as the project progresses. Changes are appended below.*

| Date | Change |
|---|---|
| 2026-10-25 | Initial version, translated from the Chinese draft |
