# docs

Where documentation goes, and what to name it. This is the only place that defines
the layout; other documents point here instead of repeating it.

| Path | Contents | Who writes |
|---|---|---|
| `overview.md` | Cross-module contracts and architecture reference | shared, changes via PR |
| `project-plan.md` | Repository revision of the original team plan; course dates remain subject to official notices | shared, changes via PR |
| `usage-and-integration.md` | Current usage, B/C/D adapter guide and first-integration checklist | A coordinates; B/C/D maintain their adapter details |
| `changelog.md` | Dated changes to documentation, contracts and integration behavior | author of the change |
| `contributions/` | One work plan per member, named `Role_<X>.md` | each member, own file only |
| `report/` | Architecture document and report sections, named `section-<n>.md` | chapter owner; D merges |

Naming: use the pattern above rather than a descriptive variant, so the directory
stays sortable and the owner is obvious from the filename.

`Role_D_Responsibilities.md` is a legacy redirect to `contributions/Role_D.md`.
The original WeChat attachment is historical input; `project-plan.md` is the
repository revision. Local alignment changes still require normal team review.

Authority is separated by purpose: the course specification and official notices
define requirements; `project-plan.md` defines the team plan; `protocol/models.py`
and `protocol/ports.py` define currently callable data and interfaces; `overview.md`
explains their meaning; this file defines documentation layout. An implementation
gap does not override a course requirement. Record it in the integration checklist.

Adding a file: put it in the matching directory. Do not create new top-level
directories under `docs/` without agreement in the group.

Weekly progress is deliberately not kept as files here. `git log` and the PR
descriptions already record who did what and when; a hand-written weekly note
duplicates that and drifts from it. If a written log is ever needed, update a
tracking issue instead of adding a directory.
