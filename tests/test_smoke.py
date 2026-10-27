"""Smoke test for the skeleton.

Empty directories are not tracked by git, so a fresh clone silently loses every
module directory unless a placeholder file exists. This test fails loudly when
that happens. Delete it once the modules have real tests.
"""

from pathlib import Path

MODULES = [
    "protocol",
    "orchestrator",
    "runtime",
    "profiles",
    "tools",
    "sandbox",
    "memory",
    "harness",
    "ui",
]


def test_module_dirs_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    missing = [name for name in MODULES if not (root / name).is_dir()]
    assert not missing, f"missing module directories: {missing}"


def test_env_example_present() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / ".env.example").is_file(), ".env.example is missing"
