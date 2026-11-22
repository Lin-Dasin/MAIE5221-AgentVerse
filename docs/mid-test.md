# Mid Test scenario runner

Use the teacher's scenario package as the input. Each group has four scenario
Agents and the instructor. It receives the two original tasks in order, with the
same SQLite conversation throughout. The orchestration event sender is
separate from the four Agent profiles.

## Set up and operate a scenario

Run from the repository root in the project Python environment. Choose a fresh
session directory. The command records the teacher's original role prompts and
tasks in `scenario.json` and prints the group ID and message database path.

```powershell
$packet = 'C:/path/to/teacher-scenario-folder'
$session = 'results/midtest/software-rehearsal'
python -m ui.cli mid-test init $packet --run-dir $session
```

The scenario runner combines the four original role prompts with the matching
role and tool policy YAMLs, using the `--profiles-dir` argument. The live `run`
command requires the Role A discussion-engine adapter. Once the adapter is
connected, launch Task 1 and Task 2 in the same session. Use another terminal
to post messages from the instructor while a task is running and to inspect
the conversation.

```powershell
python -m ui.cli mid-test run $session 1 --profiles-dir profiles/scenarios
python -m ui.cli mid-test say $session --text '<response to the actual discussion>'
$group = (Get-Content -Raw "$session/scenario.json" | ConvertFrom-Json).conversation_id
python -m ui.cli messages --db "$session/messages.sqlite3" history $group --as-id instructor
python -m ui.cli mid-test run $session 2 --profiles-dir profiles/scenarios
python -m ui.cli mid-test export $session --output-dir results/midtest/software-submission
```

The engine adapter receives the public history, the task and the four profiles.
It can poll for new instructor messages and publish public turns. The driver
passes each instructor message from the active task to the polling callback
once. Task 2 receives the accepted Task 1 artifacts under `inputs/`, with a
relative file list supplied to the adapter. The adapter should expose those
inputs to the Agents using the established workspace isolation rules.

The export includes `transcript.md`, `manifest.json` and portable copies under
`artifacts/task-1/` and `artifacts/task-2/`. Share the public transcript,
accepted artifacts and whitelisted coordination metadata. Keep raw tool
arguments, results, private conversations and diagnosis in internal records.

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
python -m pytest -q tests/harness/test_scenario.py tests/harness/test_scenario_export.py tests/ui/test_mid_scenario_driver.py
```

The integration test uses a scripted engine to exercise task order, human
messages, the real artifact copy and the scoring export. Use the Role A
adapter and the configured Role C profiles to produce the three official
scenario bundles with the live model.

The live adapter must report a verified list of accepted outputs. Completing
a discussion round alone does not publish a task's artifacts or unlock Task 2.
