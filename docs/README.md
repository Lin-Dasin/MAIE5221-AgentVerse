# docs

Where documentation goes, and what to name it. This is the only place that defines
the layout; other documents point here instead of repeating it.

| Path | Contents | Who writes |
|---|---|---|
| `overview.md` | Cross-module contracts and architecture reference | shared, changes via PR |
| `contributions/` | One work plan per member, named `Role_<X>.md` | each member, own file only |
| `report/` | Architecture document and report sections, named `section-<n>.md` | chapter owner; D merges |

Naming: use the pattern above rather than a descriptive variant, so the directory
stays sortable and the owner is obvious from the filename.

Adding a file: put it in the matching directory. Do not create new top-level
directories under `docs/` without agreement in the group.

Weekly progress is deliberately not kept as files here. `git log` and the PR
descriptions already record who did what and when; a hand-written weekly note
duplicates that and drifts from it. If a written log is ever needed, update a
tracking issue instead of adding a directory.
