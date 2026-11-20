# T3 local Git publication

Run `python -m harness.t3 --work-dir results/t3` with Git installed and the
Role B tools from PR #23. Each run creates a fresh directory and a local bare
repository. A fixed script requests the actual file and Git tools through the
scheduler. The permission decision is produced by a configured policy. This
entry point uses file-only Git transport and requires no model or GitHub token.

| Scenario | Expected tool sequence | Independent Git checks |
|---|---|---|
| Approve | Write, commit, grant permission, push | Resolve the returned short commit ID in the workspace. The reported branch in the bare repository must point at that full ID and the blob at that commit must match the expected bytes. |
| Deny | Write, commit, deny permission | The candidate is a direct child of the published commit. The scheduler must record a blocked task and zero push calls. All remote refs and the previously published blob must remain unchanged. |

The bare repository's default HEAD deliberately differs from the delivery
branch. The checker reads the explicitly reported ref. A successful run exits
with code 0 and prints the path to `control.json`. That file records both
verdicts, commit IDs and ref snapshots. Each scenario also writes its own
`evaluation.json` and `trace.jsonl`. The raw goal and node rates are 1/1 for
approval and 0/1 for denial. Both outcomes are required for this control to pass.

This local test covers permission handling and Git delivery semantics. A hosted
sandbox repository needs a separate acceptance run for authentication and
remote repository permissions.
