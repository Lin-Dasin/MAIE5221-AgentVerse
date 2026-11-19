# Section 3 — Sandbox Implementation and Threat Model

This chapter describes how the system executes untrusted, model-generated actions,
what prevents an action from escaping its agent's workspace, and which threats remain
open. The code it describes is `sandbox/` and `tools/`.

## Status and scope

| Area | State | Where |
|---|---|---|
| Workspace derivation | Implemented | `sandbox/executor.py` |
| Path validation | Implemented | `sandbox/paths.py` |
| Tool error mapping | Implemented | `sandbox/executor.py` |
| File tools (read, write, list, delete) | Implemented | `tools/fs.py` |
| Code execution | Implemented, subprocess backend | `tools/code_exec.py` |
| Web search | Implemented, Tavily adapter | `tools/web_search.py` |
| Git tools (commit, push) | Implemented | `tools/git.py` |
| Verifier-side pytest runner | Implemented, not yet wired | `sandbox/test_runner.py` |
| Container isolation | **Not implemented** | planned, `docker/` |
| Memory cap for generated code | **Not implemented** | see limits |
| Process-tree cleanup | **Not implemented** | see limits |
| Crash recovery and concurrency | **Not implemented** | planned W12 |

Two statements in this chapter would be wrong to read as guarantees: code runs in a
subprocess on the host, not in a container, and generated code is not memory-capped.
Everything else described here is covered by tests.

## Code map

| File | Lines | Responsibility |
|---|---|---|
| `sandbox/paths.py` | 35 | `resolve_within()`, the single containment check |
| `sandbox/executor.py` | 110 | `SandboxToolExecutor`: dispatch, error mapping, never raises |
| `tools/fs.py` | ~145 | `file_read`, `file_write`, `file_list`, `file_delete` |
| `tools/code_exec.py` | 114 | Python execution with a wall-clock limit |
| `tools/web_search.py` | 154 | Tavily search, provider isolated in one function |
| `tools/git.py` | 214 | `git_commit`, `git_push`, nested-repository guard |
| `tools/registry.py` | 74 | Binds definitions to handlers |

Eight tools are registered in total: four file tools, one execution tool, one search
tool and two git tools.

## Isolation model

### Workspaces are derived, never supplied

A tool handler receives `(arguments, workspace, context)`. The workspace path is
computed by the executor from `root / "workspaces" / context.agent_id`; no tool accepts
a workspace or a root as an argument. An agent therefore cannot address another agent's
directory, because it has no parameter through which to name one.

`root` is supplied once, by the application, when `build_service()` is called. Under the
current entry points it is `results/live-smoke` or the harness work directory.

### One containment check for every path

Every filesystem tool routes its path through `sandbox/paths.py:17`:

```python
base = Path(root).resolve()
target = (raw if raw.is_absolute() else base / raw).resolve()
if target != base and base not in target.parents:
    raise PathViolation(...)
```

Three properties follow, and each has a test:

- **`..` traversal is rejected** — the candidate is resolved before the comparison, so
  `outputs/../../escape.md` cannot pass.
- **Absolute paths outside the root are rejected** — `/etc/passwd` resolves outside
  `base` and fails the parent check.
- **Symlinks are resolved first** — a link inside the workspace pointing outside is
  rejected exactly like a traversal, because `resolve()` follows it before the check.

The check is deliberately the only one. A second, differently worded check in a tool
would eventually disagree with this one; tools call `resolve_within` and do not
re-implement it.

### The workspace is a repository, not the project repository

`tools/git.py` creates a repository *inside* the workspace when one is missing. Before
any `git add`, it verifies that `git rev-parse --show-toplevel` equals the workspace
itself. Workspaces live under the project checkout, so without this check `git add`
would stage files in the project repository.

The guard is tested by committing inside a workspace nested in an outer repository and
then asserting that the outer `HEAD` and index are unchanged.

## Execution limits

| Limit | Value | Enforced by | Failure reported as |
|---|---|---|---|
| Tool wall-clock | 30 s ceiling, 10 s default for `code_exec` | `subprocess.run(timeout=...)` | `execution exceeded Ns` |
| Task wall-clock | 240 s (smoke config) | `asyncio.timeout` in the scheduler path | `timeout` |
| Planning calls | 8 per run | planner loop | `planning budget exhausted` |
| Task tokens | 60 000 (smoke config; default 16 000) | runtime counters | `budget_exceeded` |
| Read payload | 200 000 characters | `tools/fs.py` | truncated, `truncated: true` |
| Command output | 8 000 characters | `tools/code_exec.py` | truncated in `stdout`/`stderr` |
| Search extract | 1 200 characters per result | `tools/web_search.py` | truncated |

The read cap was added after a live run showed the risk: `file_read` returned whole
files with no bound, so one large file could exhaust the model context and fail a task
for reasons unrelated to the task. The cap is a character count, not a byte count, and
the response carries `truncated: true` so a caller can tell a short file from a clipped
one.

## Tool contracts and error semantics

A tool handler either returns a JSON-serialisable value or raises. `SandboxToolExecutor`
converts every outcome into `ToolResult` and **never raises**:

| Handler outcome | `ToolResult` |
|---|---|
| Returns a value | `ok=True`, `data=value`, `elapsed` set |
| `ValueError` | `ok=False`, `error="invalid argument: ..."` |
| `ToolFailure` | `ok=False`, `error` verbatim |
| `PathViolation` | `ok=False`, `error="path escapes workspace: ..."` |
| `FileNotFoundError` | `ok=False`, `error="file not found: ..."` |
| `IsADirectoryError` | `ok=False`, `error="expected a file, got a directory: ..."` |
| `PermissionError` | `ok=False`, `error="permission denied: ..."` |
| Any other exception | `ok=False`, `error="<TypeName>: ..."` |

The distinction between `ValueError` and `ToolFailure` is deliberate. A malformed
argument and a provider outage are different problems, and the agent should react
differently: fix the call, or report a blocker. The `invalid argument:` prefix exists so
the trace classifier can tell them apart.

### The eight tools

| Tool | Purpose | Arguments | Returns | Approval |
|---|---|---|---|---|
| `file_read` | Read a UTF-8 file | `path` | `path`, `text`, `truncated` | — |
| `file_write` | Create or overwrite a file | `path`, `text` | `path`, `bytes` | — |
| `file_list` | List a directory | `path` (default `.`) | `path`, `entries` | — |
| `file_delete` | Delete a file | `path` | `path`, `deleted` | **required** |
| `code_exec` | Run Python in the workspace | `code`, `timeout_sec` | `exit_code`, `stdout`, `stderr`, `duration_sec` | — |
| `web_search` | Search the web | `query`, `max_results` | `query`, `results[]`, `count`, `provider` | — |
| `git_commit` | Commit workspace files | `message`, `paths` (default `outputs`) | `committed`, `commit`, `branch`, `files` | — |
| `git_push` | Push to the sandbox remote | — | `pushed`, `branch`, `remote` | **required** |

Return values are plain JSON objects: no tool returns a formatted string, so a caller
never has to parse prose to find a commit id or an exit code. Two tools carry
`requires_approval`, both because the action cannot be undone by the run that performed
it: deleting a file, and pushing to a remote.

### Verifier-side test execution

`pytest_passes` is an acceptance rule, not an agent capability, so its runner lives in
`sandbox/test_runner.py` and is deliberately **not** registered as a tool: handing the
agent a "run these tests" tool would let it observe and tune against the check.

```python
run_pytest(workspace, target=".", *, timeout_sec=60.0) -> {
    "passed": bool, "exit_code": int | None, "summary": str,
    "output": str, "duration_sec": float,
}
```

It keeps the same three properties as the tool layer: the target is validated by
`resolve_within`, the run has a wall-clock limit, and it never raises — a timeout
returns `exit_code: None` with `summary: "timeout after Ns"`, and collecting zero tests
returns exit code 5 with `summary: "no tests collected"` rather than a silent pass.

The verifier still rejects `pytest_passes` today; this runner is the piece it needs to
stop rejecting it.

Two behaviours are worth stating because they are easy to get wrong:

- **A failing program is not a failing tool.** `code_exec` returns `ok=True` with
  `exit_code`, `stdout` and `stderr` whenever the program finished. The agent needs the
  stderr to repair its own code; wrapping that in `ok=False` would hide the output.
  Only a timeout or a failure to start the interpreter is a tool failure.
- **Trace categories are derived from these strings.** `bus/trace.py` classifies a
  failure by prefix (`unknown tool:`, `invalid argument:`, `path escapes workspace`,
  `execution exceeded`). Changing an error message without updating that table silently
  reclassifies failures in the trace; the two files must change together.

## Threat model

Assets: the host filesystem outside workspaces, credentials in the environment, the
project repository, and the integrity of the produced artifacts.

Adversaries, in decreasing order of likelihood: a model that follows an injected
instruction; a model that makes a plausible mistake; generated code that behaves
differently than described; and, least likely, a malicious search result crafted to
steer the agent.

| # | Threat | Consequence | State |
|---|---|---|---|
| 1 | Path traversal (`../`, absolute path) | Read or write outside the workspace | **Blocked** — `resolve_within()` |
| 2 | Symlink escape | Same, via a link created earlier | **Blocked** — resolution precedes the check |
| 3 | Cross-agent access | One agent reads another's files | **Blocked** — workspace derived from `agent_id`, no parameter |
| 4 | Committing to the project repository | Polluted project history | **Blocked** — top-level check plus its test |
| 5 | Credential leakage through errors | Token in a trace or report | **Mitigated** — token scrubbed from git errors; trace records metadata only |
| 6 | Credential leakage through artifacts | Token written into an output file | **Open** — the agent can write any string it has seen; nothing inspects outputs |
| 7 | Prompt injection from search content | Agent executes an instruction from a web page | **Partly mitigated** — `file_delete` and `git_push` require approval; content is not filtered |
| 8 | Resource exhaustion (loops, memory) | Host slowed or a task hangs | **Partly mitigated** — timeouts enforced, **memory is not capped** |
| 9 | Orphaned child processes | Processes survive after a timeout | **Open** — `subprocess.run` kills the direct child only |
| 10 | Context flooding | A single read exhausts the model context | **Mitigated** — 200k character cap, flagged |
| 11 | Binary or non-UTF-8 read | Decode error surfacing as a crash | **Mitigated** — converted to `invalid argument: file is not UTF-8 text` |
| 12 | Unbounded network use by generated code | Data exfiltration or cost | **Open** — the subprocess has the host network |

Threats 6, 9 and 12 are the honest ceiling of a host-subprocess sandbox. They are the
reason the container backend is on the roadmap rather than optional: a container gives
filesystem and network policy, a memory cap, and a process tree that dies with its
parent.

### Why approval gates rather than content filtering

Filtering what the model reads is the intuitive answer to threat 7 and a poor one: the
injected instruction can be phrased any number of ways, arrives inside legitimate
content, and has to be filtered by the same component that is being attacked. The
design bounds the blast radius instead.

Two things limit what a successfully injected instruction can achieve. First, the
workspace: any filesystem action it triggers stays inside `workspaces/<agent>/`, so the
worst case is corrupted artifacts, not corrupted host state. Second, the approval gate:
the two irreversible actions, file deletion and a remote push, stop and wait for a
human decision recorded as a `HITLDecision`, which means an injected instruction cannot
complete them within the run that was hijacked.

That is a containment argument, not a prevention argument, and it should be read as
such: a hijacked agent can still write a wrong answer into its own outputs. Detecting
that is the verifier's job, not the sandbox's.

## Credentials

- **One key per member.** `LLM_API_KEY` and `SEARCH_API_KEY` are personal; the
  evaluation key is separate and fixed, because per-account rate limits change retry
  counts and therefore timing figures.
- **Not loaded from `.env` automatically.** `DeepSeekClient.from_environment()` reads
  `os.environ` only. Copying `.env.example` is necessary but not sufficient; this has
  already caused one false "key missing" report.
- **Git credentials are injected per call.** `git_push` builds
  `https://x-access-token:<token>@host/...` at call time, never writes it to disk, and
  replaces the token with `***` in any error text before returning it to the model. A
  timeout rebuilds the message from the subcommand alone, because
  `str(subprocess.TimeoutExpired)` embeds the full argv — which is where the token
  would otherwise appear. Git is additionally run with `GIT_TERMINAL_PROMPT=0` and an
  empty `GIT_ASKPASS` so a credential prompt cannot block a task.
- **Trace is metadata only.** Tool name, durations, agent id, failure category. No
  arguments, no file contents, no keys.

## Validation

At the time of writing:

- `pytest -q` → **258 passed, 3 skipped**. The three skips are symlink cases that need
  Windows Developer Mode; they run on CI.
- `ruff check .` → clean.
- Live two-task run (`research` → `write`) against the real API: both tasks succeeded,
  goal verdict `PASS`, 13.4 s, 13 728 and 8 612 tokens.
- That run's trace, filtered by `run_id`, contains 14 events: `plan_ready` 1,
  `task_changed` 6, `tool_finished` 6, `artifacts_handed_off` 1. The six tool calls were
  `web_search` twice, `file_write` twice and `file_read` twice, all `ok`.
- The artifact archive contains `research/research.md` and `write/notes.md`; the
  `outputs/` prefix is stripped on publish, and the downstream task's
  `inputs/research.md` exists, which is the handoff working end to end.

Two things this validation does not cover: concurrent runs, and anything that depends
on the container backend, because neither exists yet.

## Known limits and remaining work

| Limit | Impact | Planned |
|---|---|---|
| No container | Generated code shares the host network and filesystem namespace | `docker/`, W12 |
| No memory cap | A single allocation can exhaust host memory | container, or a job object on Windows |
| No process-tree kill | Grandchildren survive a timeout | container |
| No crash recovery | A killed container leaves stale artifacts | `sandbox/health.py`, W12 |
| No concurrency | Runs are serial; two runs against one root would collide | W12, coordinated with A |
| `trace.jsonl` appends across runs | Counts must be filtered by `run_id` | one file per run |

The first three are properties of the subprocess backend rather than defects in the
tool code, which is why the tool contracts were written to be backend-agnostic: swapping
`subprocess` for a container call changes `tools/code_exec.py` only, and the eight tool
definitions, the error semantics and the tests stay as they are.
