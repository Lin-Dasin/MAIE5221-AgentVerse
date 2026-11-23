# Mid Test scenario runner

Use the teacher's scenario package as the input. Each group has four scenario
Agents and the instructor. It receives the two original tasks in order, with the
same SQLite conversation throughout. The orchestration event sender is
separate from the four Agent profiles.

## Set up and operate a scenario

Use the local integration branch with the D scenario driver, the Role A
discussion engine from PR #40 and the Role C scenario policy YAMLs. Use that
checkout while the engine and official role policies are pending integration
into `develop`. From the repository root, use the project Python environment
and the model credentials already configured for the process.
[Role A engine](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/40) ·
[Local configuration](local-setup.md)

For the newsroom (S2), use the existing DeepSeek and Tavily configuration.
That workflow uses file and search tools and can run with Docker stopped.
For software or fund scenarios (S1 or S3) that use `code_exec`, start Docker
and use the sandbox image before running the task. The HTML and file checks
below apply to the selected scenario.
[Search configuration](local-setup.md) · [Sandbox setup](../docker/README.md)

Choose a fresh session directory. The `init` command records the original
role prompts and tasks in `scenario.json` and prints the group ID and
message database path.

```powershell
$packet = 'C:/path/to/teacher-scenario-folder'
$session = 'results/midtest/newsroom-01'
python -m ui.cli mid-test init $packet --run-dir $session
```

The scenario runner combines the four original role prompts with the matching
role and tool policy YAMLs from `--profiles-dir`. The live engine runs an
open discussion with bounded rounds and polls for new instructor messages
between rounds. Keep the running terminal open and use a second terminal to
respond to the actual discussion and to inspect the conversation.

```powershell
python -m ui.cli mid-test run $session 1 --profiles-dir profiles/scenarios --max-rounds 4 --max-task-tokens 60000 --max-output-tokens 2048
```

The three limit flags are optional. The values shown are the defaults. In the
second terminal, use the same session path.

```powershell
$session = 'results/midtest/newsroom-01'
python -m ui.cli mid-test say $session --text '<response to the actual discussion>'
$group = (Get-Content -Raw "$session/scenario.json" | ConvertFrom-Json).conversation_id
python -m ui.cli messages --db "$session/messages.sqlite3" history $group --as-id instructor
```

## Review and accept each task

When the discussion completes its scheduled rounds and produces candidate
files, the CLI prints the attempt ID and the reviewable paths. Inspect the
files in `tasks/task-1/attempts/<run_id>/outputs/` under the session directory
and apply the scenario checks below. Use `task-2` for the second task. The
attempt's `discussion-summary.json` records its stop reason. Live `run`
returns status 1 until a task has accepted files. Use the stop reason and
candidate list to decide whether to review or rerun the discussion.
Run `accept` only after reviewing the content, with one `--artifact` flag for
each selected output. Use the attempt ID and actual candidate paths from
the run in place of the examples.

```powershell
python -m ui.cli mid-test accept $session 1 --attempt '<task-1-run-id>' --artifact 'outputs/editor/news-report.md'
python -m ui.cli mid-test run $session 2 --profiles-dir profiles/scenarios
python -m ui.cli mid-test accept $session 2 --attempt '<task-2-run-id>' --artifact 'outputs/editor/fact-check.md'
python -m ui.cli mid-test export $session --output-dir results/midtest/newsroom-submission
```

The acceptance command verifies the saved hash of each selected candidate
before publishing it. If a candidate changed after the run or needs revision,
produce a new candidate in a fresh attempt and review that version. Accept
Task 1 before starting Task 2 in the same session, then accept Task 2 before
exporting. The operator's review is the acceptance decision.

During a discussion, other agents with file access can read candidate
material staged under `inputs/shared/<owner>/...`. Task 2 receives only the
formally accepted Task 1 files under `inputs/<owner>/...` and uses the same
public conversation history.

The export includes `transcript.md`, `manifest.json` and portable copies under
`artifacts/task-1/` and `artifacts/task-2/`. Share the public transcript,
accepted artifacts and whitelisted coordination metadata. Keep raw tool
arguments, results, private conversations and diagnosis in internal records.

## Run Task 1 with automated rework

The newsroom and fund scenarios support `--managed` for Task 1 in a checkout
containing Role A's delivery runner and Role C's scenario policies. The other
tasks use the open discussion commands above. Managed mode defaults to one
initial round and up to two rework rounds. The limit flags still apply. A new
instructor message pauses managed execution for review of the changed plan.

```powershell
python -m ui.cli mid-test run $session 1 --profiles-dir profiles/scenarios --managed
```

- **Newsroom:** the reporter writes `event-evidence.json` for an event from the last 120 days using
  an official HKUST source excerpt from the reporter's own Tavily results in this
  run. The editor produces `hkust-news.md` using that evidence. Missing or
  contradictory evidence goes back to the reporter. If the article depends on
  invalid evidence, the editor also revises it after receiving the new evidence.
- **Fund:** the fund manager writes `allocation.json` and
  `investment-proposal.md`. The checker recalculates position weights, HKD
  amounts, class subtotals and the stock-only stress scenario, and compares them
  with the proposal's required summary table.

The run records per-requirement defects, artifact hashes and rework attempts in
`discussion-summary.json`. Once the automated checks pass, review the complete
candidate set for factual and editorial quality. Accept both files from the same
attempt using repeated `--artifact` options. For example, in the newsroom run:

```powershell
python -m ui.cli mid-test accept $session 1 --attempt '<task-1-run-id>' --artifact 'outputs/reporter/event-evidence.json' --artifact 'outputs/editor/hkust-news.md'
```

Automated checks cover the recorded source excerpt, required article elements,
and the allocation arithmetic. Full-source verification and unstructured prose
still need final review. After acceptance, run Task 2 in the same session using
the open mode.

## Check the deliverables

| Scenario | What to open or check |
|---|---|
| Software Task 1 | Open the proposed `app.html` or the delivered application. Check availability, create a booking, cancel one, and follow `usage.md`. Record the specific data created during the check. |
| Software Task 2 | Open the dashboard in the extended application. Select a date range and reconcile the frequency, duration, peak times and cancellation figures with the Task 1 booking data. Use a shared browser origin or a data export/import when the browser generates the records. |
| Newsroom | Open the report and the fact-check article. Verify the linked evidence, intended audience and uncertainty documented for each task. |
| Simulated fund | Open the simulated proposal, then load the HTML slides in the demonstration browser and navigate through them. Check that the audience can see the research, implications and risks. |

The software `usage.md` should list launch steps, test data, the booking and
cancellation actions, and the steps to open and verify the dashboard. Include
the data export and import steps if the two versions use different browser
origins.

## Offline integration check

```powershell
python -m pytest -q tests/harness/test_scenario.py tests/harness/test_scenario_export.py tests/ui/test_mid_scenario_driver.py tests/ui/test_mid_scenario_engine.py
python -m pytest -q tests/harness/test_scenario_newsroom.py tests/harness/test_scenario_portfolio.py tests/ui/test_mid_scenario_delivery.py tests/ui/test_mid_portfolio_delivery.py
```

The driver tests use a scripted engine to check task order, messages and scoring
export. With PR #40 present, the adapter tests drive the discussion engine with a
scripted model and real file tools to check cross-role copies and task handoff.
The managed adapter also has offline tests for evidence-based rework and final
acceptance. Use the live run and explicit acceptance steps above to prepare a
bundle.
