# Docker calculation acceptance

This prepared case checks that a Worker runs a calculation through B's Docker
backend and that the scheduler publishes the file created by that call.
Live acceptance requires the Docker backend from
[PR #29](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/29) and the
`coder` Worker profile tracked in
[#30](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/issues/30).

## Fixed case

Use a fixed graph with one task, `compute`, assigned to `coder`. Set
`max_retries=0`, declare `outputs/calculation.md` as the only output, and
make `compute` the final task. Use a `text_contains` rule for
`sum_of_squares_1_to_5 = 55` at both levels. The task rule checks
`outputs/calculation.md` in the workspace, and the goal rule checks
`calculation.md` in the final task's archive.

Use this instruction as the task title and append the fixed Python source.

> Call `code_exec` once. Run the supplied Python program to calculate the sum
> of the squares of the integers 1 through 5, print the decimal result, and
> write `outputs/calculation.md`. Finish after the call. The Python program
> creates the file.

Send this fixed source through `code_exec`.

```python
from pathlib import Path

total = sum(number * number for number in range(1, 6))
output = Path("outputs/calculation.md")
output.parent.mkdir(exist_ok=True)
output.write_text(
    f"# Docker calculation\n\nsum_of_squares_1_to_5 = {total}\n",
    encoding="utf-8",
    newline="\n",
)
print(total)
```

Expected stdout, ignoring its trailing newline, is `55`. The expected file
is UTF-8 and ends with one LF.

```markdown
# Docker calculation

sum_of_squares_1_to_5 = 55
```

## Run the fixed graph

Build the sandbox image and set `CODE_EXEC_BACKEND=docker`.
[Docker setup instructions](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/blob/25f50f0365c79898fc3804a222e31d2c76c75b38/docker/README.md)

Export `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` for the DeepSeek Worker.
From the repository root, run the dedicated entry point in a checkout with the
Docker backend and `coder` profile. The runner creates one fresh batch directory.

```powershell
$env:CODE_EXEC_BACKEND = 'docker'
python -m harness.docker_calculation --work-dir results/docker-calculation
```

`--profiles-dir` can point to a profile directory containing `planner` and
`coder`. The fixed task exposes only `code_exec` to the Worker and forwards the
fixture program. A different program or a repeated call fails the control.
Exit `2` indicates a missing prerequisite. Exit `1` indicates a failed run.

## Evidence for one run

The batch directory contains `evaluation.json`, a case `trace.jsonl`, and
`control.json` with per-call facts. Verify the following together.

- Match the run ID and task ID in the trace and evaluation report. For
  `compute`, require exactly one tool completion, a successful `code_exec`.
- Match the call in `control.json` to the case. Require `executed=true`,
  `ok=true`, `backend="docker"`, `exit_code=0` and `stdout_matches=true`.
  The harness derives these fields from the same `ToolResult` and compares
  its stripped stdout with `55`.
- Check that `workspaces/coder/outputs/calculation.md` and
  `artifacts/<run_id>/compute/calculation.md` both match the expected bytes.
- Require one successful goal and one successful task in `evaluation.json`,
  with goal and subtask completion rates both equal to `1.0`.

The offline test uses model and tool substitutes to check that an incorrect
backend, a nonzero exit, extra artifact bytes and a changed program all fail.

```powershell
python -m pytest -q tests/harness/test_docker_calculation.py
```
