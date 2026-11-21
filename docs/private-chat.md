# Worker private chat

The local CLI can ask a configured Worker for a DeepSeek text reply in a
two-member private conversation. Run the commands from the repository root in
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

The CLI trusts the local operator to supply their identity. The conversation
must be private and contain both the sender and the selected Worker. Each
successful turn saves the prompt and reply together. If the provider fails or
another command changes that conversation while the reply is being generated,
repeat the chat command using the current history. Chat mode sends text and
uses an empty tool list. Goal execution continues through `python -m ui.cli run`.
