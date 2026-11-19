"""Deterministic file acceptance using Role A's shared contracts."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from protocol.models import AcceptanceRule, CriticResult, relative_path

PytestRunner = Callable[[Path, str], dict[str, object]]


class FileVerifier:
    """Verify files and UTF-8 text, with optional verifier-side pytest execution.

    Paths are relative to the caller's root. Node checks use the worker workspace;
    goal checks use the final task archive. No ``outputs/`` prefix is rewritten.
    The caller must keep artifacts stable during verification (serial execution).
    Supply a pytest runner only for code the execution environment can safely run.
    """

    def __init__(self, *, pytest_runner: PytestRunner | None = None):
        self._pytest_runner = pytest_runner

    @property
    def supported_acceptance_types(self) -> frozenset[str]:
        if self._pytest_runner is not None:
            return frozenset({"file_exists", "text_contains", "pytest_passes"})
        return frozenset({"file_exists", "text_contains"})

    async def check(self, rules: list[AcceptanceRule], root: Path) -> CriticResult:
        if not rules:
            raise ValueError("at least one acceptance rule is required")
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("verification root must be an existing directory") from exc
        if not resolved_root.is_dir():
            raise ValueError("verification root must be an existing directory")

        defects = []
        for index, rule in enumerate(rules, start=1):
            # File I/O must not block the application's asyncio event loop.
            reason = await asyncio.to_thread(self._check_rule, rule, resolved_root)
            if reason is not None:
                defects.append(f"rule {index} ({rule.type}, {rule.path}): {reason}")
        return CriticResult(
            passed=not defects,
            checks_passed=len(rules) - len(defects),
            checks_total=len(rules),
            defects=defects,
        )

    def _check_rule(self, rule: AcceptanceRule, root: Path) -> str | None:
        if rule.type not in self.supported_acceptance_types:
            return f"unsupported_rule: {rule.type} is not enabled for this verifier"
        try:
            # Reuse the protocol's path grammar and also check resolved targets.
            candidate = root / relative_path(rule.path)
            target = candidate.resolve()
            if not target.is_relative_to(root):
                return "outside_root: artifact must remain inside the verification root"
            # A's artifact publisher also requires ordinary files, not file links.
            if candidate.is_symlink():
                return "symlink_not_allowed: expected an ordinary artifact file"
            if not target.is_file():
                return "missing_file: expected an existing regular file"
            if rule.type == "text_contains":
                content = target.read_text(encoding="utf-8")
                if not rule.text:
                    return "invalid_rule: text_contains requires non-empty text"
                if rule.text not in content:
                    return f"text_missing: expected literal text {rule.text!r}"
        except UnicodeDecodeError:
            return "invalid_encoding: expected UTF-8 text"
        except (ValueError, RuntimeError):
            return "invalid_path: expected a resolvable relative path inside the root"
        except OSError as exc:
            # Keep diagnostics useful without exposing absolute host paths.
            return f"file_error: {type(exc).__name__} while inspecting the artifact"
        if rule.type == "pytest_passes" and self._pytest_runner is not None:
            try:
                result = self._pytest_runner(root, rule.path)
            except Exception as exc:
                return f"pytest_runner_error: {type(exc).__name__}"
            if not isinstance(result, dict):
                return "pytest_runner_error: expected a result dictionary"
            code = result.get("exit_code")
            if result.get("passed") is not True or type(code) is not int or code != 0:
                summary = result.get("summary") or "missing runner summary"
                return f"pytest_failed: exit_code={code!r}, {summary}"
        return None
