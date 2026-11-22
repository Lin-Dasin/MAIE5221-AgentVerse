# Role D live development baselines

These checks used internal development cases on 2026-11-21. Official Mid and
Final dataset results are outside this record. Each row covers one goal and
uses the requested model ID `deepseek-chat`. Token totals cover Worker calls.
The table and checks summarize the local evaluation, verification and trace
records.

| Case | Run ID | Goals | Nodes | Duration and scope | Worker tokens |
| --- | --- | ---: | ---: | --- | ---: |
| T1 fixed two-source search | `bbd23bc4a11646fda4145703ab745912` | 1/1 | 2/2 | 17.91 s, fixed graph | 15,602 |
| #32 bounded CLI search and handoff | `afc7cbb104f5478384b70c5a14d8c1f9` | 1/1 | 2/2 | 22.74 s, including planning | 13,963 |
| #31 group clarification and resume | `782acd28dbec490fa9ec9fd95f9c86b6` | 1/1 | 1/1 | 12.69 s, including planning | 7,749 |
| M3 retrieval on | `53e24fed68fc45d1a842e594a606a77a` | 1/1 | 1/1 | 4.28 s, fixed graph | 6,459 |
| M3 retrieval off | `15f6498fa8a04e619b24a50aec9e5518` | 0/1 | 1/1 | 2.36 s, fixed graph | 4,012 |

## Verified checks and reproduction

- **T1 fixed search.** Both real Tavily requests returned HTTP 200. The
  approved report paired `2024-10-07` with the official Python 3.13.0 release
  page and `--disable-gil` with PEP 703. The researcher archive, copied writer
  input and published report matched byte-for-byte. The goal passed all three
  checks. The suite digest was
  `0162b5464034dcdccdb1e08f73995d3d45c5f8374ce986b5f23b7abb980324b7`.
  Rerun the fixed case with `python -m harness.t1 --output-dir results/t1-recheck`
  from a checkout with the T1 runner and configured model and search credentials.
  [T1 implementation and run notes](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/33) ·
  [Python release page](https://www.python.org/downloads/release/python-3130/) ·
  [PEP 703](https://peps.python.org/pep-0703/)
- **#32 CLI search.** The successful two-node run used one real Tavily request,
  which returned HTTP 200. The trace showed a researcher search and write,
  one artifact handoff, then a writer read and write. The copied input matched
  the archive, and the report retained the official asyncio documentation URL.
  The goal passed all three checks. Use the ordinary `ui.cli run` entry point
  with model and search credentials. Use a bounded goal with one search and
  explicit acceptance for the published source URL.
  [CLI search and run notes](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/32) ·
  [Python asyncio documentation](https://docs.python.org/3/library/asyncio.html)
- **#31 group clarification.** The trace showed `waiting_human`, a human
  `hitl_decided` event approving the clarification, and the same task resuming
  under the original run ID. The verification record confirmed that a separate
  CLI process posted the reply in the same group. The goal passed its one
  fixed check. Use `messages run` and `messages reply` in separate processes,
  with a fresh message database and a synthetic clarification for a rerun.
  [Group clarification implementation and run notes](https://github.com/Lin-Dasin/MAIE5221-AgentVerse/pull/31)
- **M3 Worker memory comparison.** Comparison ID
  `2244c45dd5bd46f7a95616b16610fca0`. Both conditions reached the model,
  wrote a file, and preserved identical private-memory snapshot hashes with
  writes disabled. The on trace recorded retrieval and injection, and only that
  output recovered the expected marker. The off node passed its file rule, but
  its goal failed the marker check. The repository's Worker memory demo offers
  a fresh retrieval-on/off check of the same behavior.
  [Worker memory lifecycle reproduction](../usage-and-integration.md#worker-memory-lifecycle-check)
