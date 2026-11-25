# Mid Test scenario runner

Use the teacher's scenario package as the input. Each group has four scenario
Agents and the instructor. It receives the two original tasks in order, with the
same SQLite conversation throughout. The orchestration event sender is
separate from the four Agent profiles.

## Set up and operate a scenario

Use a checkout that includes this scenario runner and the Role C scenario
policy YAMLs in `profiles/scenarios`. The Role A discussion and delivery
engine is available on `develop` through PR #40. From the repository root,
use the project Python environment and the model credentials already
configured for the process.
[Role A engine](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/40) ·
[Scenario policies](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/41) ·
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

## Run with automated rework

Managed mode supports both tasks in all three scenarios in a checkout
containing Role A's delivery runner and Role C's scenario policies. It defaults
to one initial round and up to two rework rounds. It sends failed checks back
to the responsible agents and checks their revised files again. A new
instructor message pauses execution for review of the changed goal.

```powershell
python -m ui.cli mid-test run $session 1 --profiles-dir profiles/scenarios --managed --max-task-tokens 240000 --max-output-tokens 8192
```

- **Software:** the developer writes the booking application and guide in Task
  1, then the dashboard and guide in Task 2. Start Docker and make the sandbox
  image available before running either task. The verifier seeds real bookings
  and cancellations through the booking HTTP API. For Task 2 it uses the
  accepted Task 1 application and checks the same SQLite data, including minutes
  of occupancy across two-hour bands. The developer receives failures for both
  program behavior and usage instructions. It retains a verified program
  during guide-only repairs and checks that the documented servers use
  distinct ports.
- **Newsroom:** in Task 1, the reporter writes `event-evidence.json` using the
  reference ID and exact URL of their own official HKUST search result. The
  verifier checks that the event actually occurred within the date range
  shown in the delivery instructions. The editor receives the checked event
  details, including the full occurrence range, for `hkust-news.md`. In Task
  2, the system starts a scholarly search for the fact-checker and records
  source IDs. The fact-checker writes `detector-evidence.json`, and the editor
  writes `ai-detector-fact-check.md` with a supported verdict, both kinds of
  errors and remaining uncertainty. The system formats the verified claims
  and citations when the editor saves the article. A percentage must retain
  the metric and study population in its recorded passage, and the article
  must keep it with the complete checked claim. A source failure goes to the
  evidence owner first.
- **Fund:** the researcher writes `research-evidence.json` in each task using
  direct URLs and reference IDs from their own Tavily results. The runner uses
  the original result excerpts to check the dated claims and cited figures. The
  fund manager uses the checked research in the proposal. When the proposal uses
  the allocation-summary placeholder, the system renders the weights, amounts,
  and stress figures from the validated allocation ledger. In Task 2, Marketing
  uses the research in
  `semiconductor-outlook.html` and uses a current outlook with a verifiable
  publication date or named release edition. Missing or inconsistent evidence
  goes back to Research first, and the corresponding author can then update
  the citations.

Researchers can reread the current task's results in
`inputs/recorded-search-results.json`. When the submitted URL exactly matches
one recorded in the task, the runner can recover the correct source ID from
that record. The claim still has to pass the task's evidence checks.

The run records per-requirement defects, artifact hashes and rework attempts in
`discussion-summary.json`. The operator's role is final acceptance. Once the
automated checks pass, review the complete candidate set and accept its files
from the same attempt using repeated `--artifact` options.

```powershell
python -m ui.cli mid-test accept $session 1 --attempt '<task-1-run-id>' --artifact 'outputs/reporter/event-evidence.json' --artifact 'outputs/editor/hkust-news.md'
```

The final review covers browser behavior, full-source verification and prose.
If a candidate needs correction, have the system generate a new reviewable
attempt and preserve its hashes for acceptance. After accepting Task 1, run
Task 2 in the same session. Use `--managed` again for automatic rework.

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
