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

## Evidence for one run

Build the sandbox image and set `CODE_EXEC_BACKEND=docker`.
[Docker setup instructions](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/blob/25f50f0365c79898fc3804a222e31d2c76c75b38/docker/README.md)

Use a fixed-graph runner that registers `code_exec`. Start with a fresh run
directory so the file has no prior version. Keep the report, trace and
verification output under the ignored
`results/baselines/docker-calculation-<date>/` directory.

- Match the run ID and task ID in the trace and evaluation report. For
  `compute`, require exactly one tool completion, a successful `code_exec`.
- Capture that same call's result at the executor boundary. Require
  `ToolResult.ok=true`, `data.backend="docker"`, integer `data.exit_code=0`,
  and `data.stdout.strip() == "55"`. The trace records the tool and success
  flag. The tool result supplies the backend and program exit code.
- Check that `workspaces/coder/outputs/calculation.md` and
  `artifacts/<run_id>/compute/calculation.md` both match the expected bytes.
- Require one successful goal and one successful task in `evaluation.json`,
  with goal and subtask completion rates both equal to `1.0`.
