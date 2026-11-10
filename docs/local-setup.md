# Local setup and credentials

How to get a working environment, and how secrets are handled. Read this before
running anything that calls a real model.

## 1. Environment

Python 3.11 or newer, then:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
.venv/bin/python -m pip install -e ".[dev]"             # Linux / macOS
```

Use the editable install locally because CI runs the same command
(`pip install -e '.[dev]'`). If local and CI install differently, "green locally,
red on CI" becomes a regular event.

## 2. One key per member

Copy `.env.example` to `.env` and fill in **your own** keys:

- `LLM_API_KEY` — your DeepSeek key
- `SEARCH_API_KEY` — your Tavily key (only needed when using `web_search`)

Why per member rather than one shared key:

- **Quota is attributable.** When a dev-set run costs a lot, we can tell whose
  run it was instead of guessing.
- **One person cannot exhaust the budget for everyone.**
- **Revoking is cheap.** If a key leaks, one person rotates theirs.

Rules that apply to every key:

- `.env` is git-ignored. Never commit it, and never add a second copy.
- Never paste a key into a PR description, an issue, a chat message, or a screenshot.
- Never write a key into `meta`, a trace record, or a log line — traces are exported
  to JSON and attached to reports.

## 2.1 Which key to use, by activity

| Activity | Key | Why |
|---|---|---|
| Development, debugging, trying things out | your own | Quota is attributable and one person cannot exhaust it for everyone |
| **Formal evaluation** (dev/mid/final set runs whose numbers go into the report) | **one fixed evaluation key** | Per-account rate limits affect retries and duration, so mixing keys makes the timing figures not comparable |

The model output does not depend on which key is used; retry and duration figures do.
Record the model name and configuration with the report, never the key itself.

> Open question for the application owner: confirm that the live entry point loads
> `.env` (or requires exported variables) and that formal runs use the fixed
> evaluation key. Until that is confirmed, this section is a proposal, not a rule.

## 3. How variables actually reach the process

**Nothing loads `.env` automatically today.** `DeepSeekClient.from_environment()`
reads `os.environ` only, so copying the file is not enough on its own.

Option A — export for the current shell (works today):

```bash
export LLM_BASE_URL=https://api.deepseek.com      # no /v1: the client appends the path
export LLM_API_KEY=sk-...
export LLM_MODEL=deepseek-flash
export LLM_THINKING=disabled                     # shared client default
export SEARCH_API_KEY=tvly-...
```

Where the values come from:

| Variable | Where to get it | Format |
|---|---|---|
| `LLM_BASE_URL` | fixed value, not user-specific | `https://api.deepseek.com` |
| `LLM_API_KEY` | platform.deepseek.com → API keys → create (shown once, top up first) | `sk-...` |
| `LLM_MODEL` | `deepseek-flash` (default) or `deepseek-v4-pro` for heavy reasoning. `deepseek-chat` and `deepseek-reasoner` are retired | plain model id |
| `LLM_THINKING` | optional client setting; defaults to `disabled` | `disabled` or `enabled` |
| `SEARCH_API_KEY` | tavily.com → dashboard → API keys | `tvly-...` |

Two DeepSeek details that cost real debugging time:

- **The model name matters more than the URL.** `deepseek-chat` and
  `deepseek-reasoner` are no longer in the official model table; use
  `deepseek-flash` (or `deepseek-v4-pro`). A stale name fails at request time,
  not at import time.
- **`/v1` is legacy but still served.** Verified by probing with a deliberately
  invalid key: both `https://api.deepseek.com/chat/completions` and
  `https://api.deepseek.com/v1/chat/completions` answer 401, so neither path is
  missing. Use the bare host because that is the documented form, and keep it
  consistent across machines.
- **The shared client explicitly defaults to thinking disabled**, independently
  of provider defaults. Set `LLM_THINKING=enabled` to request thinking; the Runtime
  preserves `reasoning_content` for assistant-history replay on later tool turns.
  Thinking tokens consume the output budget, so enabled runs may need a larger
  `--max-output-tokens`. The smoke script now uses the same client for its probe,
  Planner and Workers; the old temporary shim has been removed. Both modes have
  offline HTTP regression coverage, not a new live-provider validation claim.

```bat
set LLM_API_KEY=sk-...                             :: cmd
```

```powershell
$env:LLM_API_KEY = "sk-..."                        # PowerShell
```

Option B — the application loader reads `.env` at startup. That belongs to the
application assembly, which owns environment loading, trace wrapping and client
shutdown. Until it exists, use Option A.

## 4. What is required, and when

| Variable | Needed | If missing |
|---|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Before constructing the model client | `ValueError: LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set` |
| `LLM_THINKING` | Optional | Defaults to `disabled`; invalid values raise `ValueError` |
| `SEARCH_API_KEY` | Only when `web_search` is called | The tool returns a failed result; the service still starts |
| `GITHUB_TOKEN`, `GITHUB_SANDBOX_REPO` | Only when the git tool exists | No effect yet |

Note the distinction: the **tool name** must be registered for a profile to load,
but its **key** is only needed when the tool is actually called.

## 5. CI

CI has no keys and no outbound network. Therefore:

- Unit tests must mock the provider; never call a real API from a test.
- Any test that needs a live endpoint must be guarded with
  `pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="no key")`.

## 6. Check your setup

```bash
python -c "import os; print({k: bool(os.environ.get(k)) for k in ('LLM_API_KEY', 'SEARCH_API_KEY')})"
python -m pytest -q
```

The first command prints booleans, never the key values.

For an actual model/file workflow after exporting credentials, run from the
repository root:

```bash
python -m scripts.live_smoke --goal \
  "Do not search. Write outputs/notes.md with heading '# Local smoke test'." \
  --root results/live-file-smoke
```

The script verifies archive-relative `notes.md`, while Workers write
`outputs/notes.md`. See [the integration guide](usage-and-integration.md) for the
search variant, supported acceptance rules and the limits of this smoke check.
