# Changelog

This file records actual changes to documentation, contracts and the integration entry point. Day-to-day progress and code contributions are evidenced by genuine Git commits, PRs and tracking issues. This log does not imply that changes have already been committed, merged or approved by the team.

## 2026-10-29 — Documentation and Interface Alignment, Integration Guide and Shared Assembly Entry Point

Background: comparing the course specification, the project-plan attachment and the current Role A code revealed inconsistencies in memory interfaces, paths, progress-record locations, some status descriptions and official evaluation arrangements. Cross-role documentation was aligned at the user's request and awaits normal team PR review.

| Area | Before | Change and rationale |
| --- | --- | --- |
| Project-plan source | The project-plan attachment was outside the repository and differed from later code/documentation | Added `docs/project-plan.md` as the v3.4 local alignment draft |
| Contract authority | Different documents gave precedence either to code or to the old plan | Course specifications/official notices define requirements; the plan defines responsibilities; `protocol/models.py` and `protocol/ports.py` define current callable models/signatures; gaps are recorded separately |
| C's public interface | Documentation described synchronous `retrieve_memory()`, `build_memory_context()` and `write_reflection()`, while A called different async methods | Standardized on async `retrieve(agent_id, task) -> str` and `reflect(task, result) -> None`; record-level helpers may remain internal to C, with no second runtime API |
| Memory fields | C's document assumed separate description/type fields, a mandatory CriticResult and complete traces | Aligned with actual Task/TaskResult fields; `result.critique` is optional; additional tags and trace correlation are adapter work |
| Memory location | C's example used `memory/data/<agent_id>.jsonl` | Standardized on `workspaces/<agent_id>/memory/<agent_id>.jsonl`, consistent with isolation and lifecycle conventions |
| Artifact archives | Some documents omitted run_id | Standardized on `artifacts/<run_id>/<task_id>/`; clarified the difference between task-verification and goal-verification roots |
| Progress and change records | The external plan required `docs/weekly/`, while the repository convention excluded it | Progress goes in PRs/tracking issues; documentation and contract changes go in this file |
| Individual plans | D's file was in the docs root; Wang's plan had a duplicate outside the repository | Moved D's maintained content to `docs/contributions/Role_D.md` and retained a redirect; replaced the external Wang copy with a link to the maintained plan to prevent divergence |
| Capability status | Some descriptions could be read as claiming target capabilities were already implemented | Explicitly identified outstanding B/C/D adapters, chat routing, automatic Planner memory, the independent Critic Agent and full traces |
| Evaluation and memory experiments | Test releases/run counts were inferred without confirmation; the second run was expected to improve unconditionally | Official arrangements remain unconfirmed; internal development continues with the dev set; memory experiments retain configuration/raw results and report actual changes |
| Environments and containers | WSL2 was described as mandatory on every platform; long-lived and on-demand lifecycle descriptions conflicted | Standardized the Linux sandbox and mounts, with host-specific Docker/WSL2 choices; containers persist within a session and shared-root runs are currently serial |
| Models and structured output | Stack recommendations did not fully match the current httpx/JSON planning implementation | Documented the existing DeepSeek HTTP client, Worker tool calls and Planner JSON validation; listed HKGAI as future adapter work |
| Service assembly | CLI/harness authors would need to duplicate Runtime, Scheduler and Planner composition | Added `orchestrator/bootstrap.py::build_service()` so D supplies real adapters and shares one model and memory instance |
| Interface explanations | Empty memory, optional verdicts and event-interface scope were easy to misinterpret | Added docstrings to `protocol/ports.py` without changing existing method signatures or model fields |
| Usage guidance | No complete B/C/D integration sequence or live-integration exit criteria | Added `docs/usage-and-integration.md` covering modules, available commands, interfaces, follow-up work, an assembly example and seven integration steps |

Synchronized related descriptions in `README.md`, `docs/README.md`, `docs/overview.md`, the individual role plans, `docs/report/section-2.md` and `.env.example`.

### Validation

- Ran `python -m pytest -q` in the local project environment: **36 passed**.
- Ran `ruff check .`: **passed**.
- Updated the existing `test_goal_to_artifact_with_memory_and_approval` to assemble the service through the new factory, retaining combined coverage of planning, Worker execution, approval, memory calls, actual temporary-file writes and archiving.
- The model, tool adapter, memory and verdicts in that test remain explicit test doubles. These results are not evaluations of real DeepSeek, Docker, search, GitHub or official datasets.
- Statically checked 13 Markdown files, 20 local file links, two Python examples and five JSON examples: no missing links or syntax errors. External-service calls in examples were not executed.
- `git diff --check` passed.

### Outstanding Work

The team still needs production B/C/D adapters, real-model end-to-end integration, private/group chat, memory lifecycles for all agents, per-call traces and a dev-set baseline. Adding the assembly entry point does not establish completion of the first live integration. No Git commits or PRs were created in this change.

## 2026-10-29 — English Translation of the Revised Documents

- Translated the user's current revisions of `docs/project-plan.md`, `docs/usage-and-integration.md` and this changelog into English, preserving the revised scope and omissions.
- Preserved interface names, paths, commands, configuration keys and executable Python examples. Translated natural-language descriptions inside illustrative JSON/YAML examples.
- Updated the Section 2 report's link fragment to match the integration guide's translated assembly heading.
- Validation found no untranslated Chinese in the three documents; table-row and checklist counts were preserved, all 20 local file links and the heading fragment resolved, and all 20 Python source/test files matched their pre-translation hashes. Executable Python examples were unchanged; JSON/YAML examples parsed successfully. `git diff --check` passed.
- This translation changes documentation only; it does not implement additional agent capabilities or create Git commits, pushes or PRs.
