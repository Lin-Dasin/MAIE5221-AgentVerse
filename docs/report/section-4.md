# Section 4: Private Memory and Evolution

## 4.1 Scope

The system treats evolution as cross-task memory, not model training or
fine-tuning. A Worker records a compact reflection after a terminal task result;
before a later task, the same Worker retrieves relevant records and receives a
bounded memory context. The mechanism is intentionally lightweight so it remains
auditable, deterministic, and usable in the scheduler's short reflection timeout.

This section covers the current Worker-memory implementation. Automatic Planner
memory and a separate Critic-Agent memory lifecycle remain joint A/C follow-up
work and are not claimed by this section.

## 4.2 Storage, Isolation, and Reflection

`JsonlMemory` stores each agent's records at:

```text
workspaces/<agent_id>/memory/<agent_id>.jsonl
```

The repository-level `memory/` directory contains implementation code only.
Each JSONL line is a validated `MemoryRecord` with the agent identifier, task
summary, terminal outcome, execution summary, optional failure code, strategy or
avoidance notes, reusable advice, evidence task identifier, and timestamp.

`reflect(task, result)` is called by the scheduler for terminal task states when
`RunConfig.write_memory` is enabled. It uses structured `TaskResult` fields and a
fixed template rather than an additional model call. This keeps write latency
small, records failed and blocked outcomes even when `result.critique` is absent,
and avoids presenting generated reflection text as ground truth.

Records are private by path and by validation: retrieval opens only the requesting
agent's JSONL file, and any malformed or mismatched-agent line is skipped with a
diagnostic. Runtime files are not committed to Git.

## 4.3 Retrieval and Prompt Context

`retrieve(agent_id, task)` reads the requesting agent's history, ranks records,
and returns prompt-ready text plus the opaque IDs of the records included.
No matching record returns empty text and an empty ID list, so
memory absence does not block task execution. The adapter returns at most three
records and limits the total context length.

The ranker uses deterministic token-overlap scoring:

- ASCII text is normalized into lowercase words of at least two characters.
- Each contiguous CJK run is represented by overlapping two-character tokens.
  For example, `核对发票总额` yields `核对`, `对发`, `发票`, `票总`, and `总额`.
- A one-character CJK run remains a token so short valid labels are preserved.
- Ties prefer the most recently created record.

CJK bigrams are dependency-free and intentionally conservative. They support
related Chinese task titles without requiring a language-specific tokenizer, but
they do not provide semantic similarity across different wording. More advanced
retrieval should be evaluated only if the fixed baseline proves insufficient.

## 4.4 M2 Bilingual Recall Probe

The offline M2 probe is implemented in `harness/memory_recall.py`. It uses three
paired topics in English and Chinese: invoice totals, backup integrity, and shift
schedules. For each language, the probe writes one successful reflection per
topic into a fresh private store, then queries a related title.

The metric is **top-one marker recall**:

1. Set `max_records=1`, so the retriever returns at most one ranked reflection.
2. A case is a hit only when the returned text contains that case's unique,
   expected advice marker.
3. Per-language hit rate is `hits / 3`. This tests retrieval selection only; it
   does not claim that prompt injection improves goal completion.

Reproduce from the repository root:

```bash
python -m pytest -q tests/test_memory.py tests/test_memory_recall_benchmark.py
python -m harness.memory_recall --output results/m2-bilingual/evaluation.json
```

The JSON output records each seed title, query, expected marker, returned text,
per-case hit flag, language totals, and the policy strings used for this version.
The preceding M2 baseline documented English 3/3 and Chinese 0/3 because the
ASCII-only tokenizer discarded Chinese titles. The CJK-bigram change must be
re-run through this command and its resulting JSON retained as the corrected
measurement; results must not be inferred from unit-test status alone.

## 4.5 M3 Comparison Protocol

Memory-enabled and memory-disabled runs must use the same task suite, model,
prompt version, tool permissions, acceptance rules, token limits, approval mode,
and runtime environment. Record both `memory_enabled` and `write_memory`, as well
as the starting JSONL snapshot.

For a frozen-history comparison, prepare two identical memory snapshots, use
`write_memory=false` in both groups, and vary only `memory_enabled`. For an
evolving-memory comparison, use separate seeded stores and preserve the ordered
task sequence. Report goal completion rate, subtask completion rate, elapsed
time, Worker tokens, failure categories, trace references, and the raw result
files. A retrieval hit alone is not evidence of performance improvement.

## 4.6 Current Limitations

- The Worker path calls the adapter, but full Planner and Critic memory lifecycles
  remain unimplemented.
- `TaskResult` does not contain complete tool traces or every retry detail; C/D
  must associate those through the trace layer when experimental analysis needs
  them.
- JSONL suits the small expected corpus but needs a documented compaction policy
  if histories become large.
- Character-bigram matching cannot infer synonyms or conceptual similarity.
