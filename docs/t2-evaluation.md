# T2 pytest acceptance

Run `python -m harness.t2 --work-dir results/t2` from a checkout that includes
B's verifier-side `sandbox.test_runner` from PR #23. This offline entry point
uses a fixed task graph, four repository-controlled Python fixtures, the real
scheduler and a fresh run directory for every invocation. It requires pytest
in the active Python environment.

| Fixture | Expected pytest result | Goal and node verdict |
|---|---|---|
| Correct addition | Exit 0 in both the worker workspace and the final archive | Both pass |
| Incorrect addition, same test | Exit 1 | Both fail |
| No tests collected | Exit 5 | Both fail |
| Slow test | Two-second timeout, no exit code | Both fail |

`evaluation.json` reports the raw batch rates, 1/4 goals and 1/4 nodes in a
successful control run. `control.json` reports whether all four outcomes matched
expectations and records each invocation's target, output, exit code and timing.
The goal-level pytest check runs after the accepted node publishes its files.

The T2 factory explicitly supplies the runner to D's verifier and advertises
its supported rules to A's plan validation. Ordinary file verification keeps its
file-only defaults. B's runner executes on the host by default and can run the same
command inside the sandbox container (`backend="docker"`, or `PYTEST_BACKEND=docker`);
the fixtures above exercise acceptance semantics, and model-generated Python should use
the container backend.
