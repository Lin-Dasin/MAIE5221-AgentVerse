# Worker chat

The local CLI can ask a configured Worker for a DeepSeek text reply in a
private or group conversation. Run the commands from the repository root in
the project Python environment. Export `LLM_BASE_URL`, `LLM_API_KEY` and
`LLM_MODEL` as described in [local setup](local-setup.md).

```powershell
$db = 'results/private-chat.sqlite3'
python -m ui.cli messages --db $db create writer-chat --kind private --member user --member writer
python -m ui.cli messages --db $db chat writer-chat --sender user --agent writer --text 'Reply with one sentence about how to structure my report.'
python -m ui.cli messages --db $db history writer-chat --as-id user
```

Run `create` once per conversation. A later `chat` call uses the same
conversation ID and database and includes its previous text messages in the
request. `--profiles-dir` can select a directory of AgentVerse YAML profiles.

## Ask a Worker in a group

```powershell
$db = 'results/group-chat.sqlite3'
python -m ui.cli messages --db $db create report-team --kind group --member user --member writer --member researcher
python -m ui.cli messages --db $db chat report-team --sender user --agent writer --text 'Suggest one section heading for a project report.'
python -m ui.cli messages --db $db chat report-team --sender user --agent researcher --text 'What heading did writer suggest?'
python -m ui.cli messages --db $db history report-team --as-id writer
```

`--agent` selects the Worker to reply. The group transcript shows the recipient
as an `@agent` prefix. The chosen Worker sees the group's text messages, with
other speakers labeled by identity. All group members can read the saved turn.

The CLI trusts the local operator to supply their identity. Both the sender and
the selected Worker must belong to the conversation. Each successful turn saves
the prompt and reply together. If the provider fails or another command changes
that conversation before the reply is saved, repeat the chat command using the
current history. Chat mode uses an empty tool list.

## Start a group task

Use `messages run` to submit an explicit goal to the shared Planner and
Scheduler. Create a group with the requesting user, a member to post run
notifications (default `orchestrator`), and the eligible Worker IDs. The CLI
limits the file-tool Workers to members of that group.

This example uses the P2 profiles and fixed acceptance rule file:

```powershell
$db = 'results/group-p2.sqlite3'
$goal = (Get-Content -Raw -Encoding UTF8 'harness/suites/p2/goal.txt').Trim()
python -m ui.cli messages --db $db create p2-team --kind group --member user --member orchestrator --member owner_a --member owner_b --member writer
python -m ui.cli messages --db $db run p2-team --sender user --goal $goal --profiles-dir 'harness/suites/p2/profiles-guided' --acceptance 'harness/suites/p2/acceptance.json'
python -m ui.cli messages --db $db history p2-team --as-id writer
```

The history includes the goal, handoff notices and final status with artifact
and report paths. All events for the goal carry its run ID. Each command
executes in a fresh run directory. The Planner receives the goal from
`--goal`. Group history remains available when reading the conversation or
using `messages chat`.

## Answer a group clarification

Keep the terminal running the group task open. If a Worker asks a clarification
question, the CLI prints the request ID and posts the question in the same
group. In another terminal from the same repository, read that group's history
and answer as the user who started the run, using the same database and
conversation ID:

```powershell
$db = 'results/group-p2.sqlite3'
python -m ui.cli messages --db $db history p2-team --as-id user
python -m ui.cli messages --db $db reply p2-team --sender user --request-id '<request_id>' --text '项目进度'
```

Each request accepts a single answer for its original run. The default deadline
is 60 seconds, configurable with `messages run --clarification-timeout SECONDS`.
An expired clarification and the final run status appear in the conversation.
The reply command covers text clarification. Permission decisions continue to
follow the run policy.
