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
current history. Chat mode uses an empty tool list. Goal execution continues
through `python -m ui.cli run`.
