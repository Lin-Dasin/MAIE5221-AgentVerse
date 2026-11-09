from pathlib import Path

import pytest

from harness.verdict import FileVerifier
from protocol.models import AcceptanceRule, CriticResult


def write_report(root: Path, content: str = "# 报告\n## 结论\n结果正常。\n") -> Path:
    report = root / "outputs/report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(content, encoding="utf-8")
    return report


async def test_file_and_chinese_text_pass(tmp_path):
    write_report(tmp_path)
    result = await FileVerifier().check(
        [
            AcceptanceRule(type="file_exists", path="outputs/report.md"),
            AcceptanceRule(type="text_contains", path="outputs/report.md", text="## 结论"),
        ],
        tmp_path,
    )
    assert isinstance(result, CriticResult)
    assert result.passed
    assert (result.checks_passed, result.checks_total) == (2, 2)
    assert result.defects == []


@pytest.mark.parametrize("make_directory", [False, True], ids=["missing", "directory"])
async def test_file_exists_requires_a_regular_file(tmp_path, make_directory):
    if make_directory:
        (tmp_path / "report.md").mkdir()
    result = await FileVerifier().check(
        [AcceptanceRule(type="file_exists", path="report.md")], tmp_path
    )
    assert not result.passed
    assert result.checks_passed == 0
    assert "missing_file" in result.defects[0]


async def test_mixed_rules_collect_all_failures_in_order(tmp_path):
    write_report(tmp_path)
    result = await FileVerifier().check(
        [
            AcceptanceRule(type="text_contains", path="outputs/report.md", text="参考文献"),
            AcceptanceRule(type="file_exists", path="missing.txt"),
            AcceptanceRule(type="text_contains", path="outputs/report.md", text="## 结论"),
        ],
        tmp_path,
    )
    assert not result.passed
    assert (result.checks_passed, result.checks_total) == (1, 3)
    assert len(result.defects) == 2
    assert "rule 1" in result.defects[0] and "text_missing" in result.defects[0]
    assert "rule 2" in result.defects[1] and "missing_file" in result.defects[1]


@pytest.mark.parametrize(
    ("text", "expected"), [("Conclusion", True), ("conclusion", False), ("Con.*", False)]
)
async def test_text_match_is_case_sensitive_and_literal(tmp_path, text, expected):
    write_report(tmp_path, "Conclusion: done")
    result = await FileVerifier().check(
        [AcceptanceRule(type="text_contains", path="outputs/report.md", text=text)], tmp_path
    )
    assert result.passed is expected


async def test_invalid_utf8_is_a_failed_check(tmp_path):
    (tmp_path / "report.txt").write_bytes(b"\xff\xfe")
    result = await FileVerifier().check(
        [AcceptanceRule(type="text_contains", path="report.txt", text="done")], tmp_path
    )
    assert not result.passed
    assert "invalid_encoding" in result.defects[0]


async def test_read_error_does_not_hide_other_results(tmp_path, monkeypatch):
    write_report(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError("synthetic read denial")

    monkeypatch.setattr(Path, "read_text", denied)
    result = await FileVerifier().check(
        [
            AcceptanceRule(type="text_contains", path="outputs/report.md", text="结论"),
            AcceptanceRule(type="file_exists", path="outputs/report.md"),
        ],
        tmp_path,
    )
    assert not result.passed
    assert result.checks_passed == 1
    assert "file_error: PermissionError" in result.defects[0]


async def test_pytest_rule_is_explicitly_unsupported(tmp_path):
    # If mistakenly executed on the host, this test file would create a marker.
    (tmp_path / "test_generated.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('executed.txt').write_text('executed')\n",
        encoding="utf-8",
    )
    result = await FileVerifier().check(
        [AcceptanceRule(type="pytest_passes", path="test_generated.py")], tmp_path
    )
    assert not result.passed
    assert "unsupported_rule" in result.defects[0]
    assert not (tmp_path / "executed.txt").exists()


async def test_empty_rules_are_not_vacuous_success(tmp_path):
    with pytest.raises(ValueError, match="at least one acceptance rule"):
        await FileVerifier().check([], tmp_path)


@pytest.mark.parametrize("root_kind", ["missing", "file"])
async def test_invalid_root_is_a_configuration_error(tmp_path, root_kind):
    root = tmp_path / root_kind
    if root_kind == "file":
        root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="verification root"):
        await FileVerifier().check([AcceptanceRule(type="file_exists", path="report.md")], root)


async def test_parent_traversal_is_rejected_even_if_model_validation_is_bypassed(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (tmp_path / "outside.md").write_text("private", encoding="utf-8")
    # model_copy(update=...) skips validation; still do not inspect outside the root.
    rule = AcceptanceRule(type="file_exists", path="safe.md").model_copy(
        update={"path": "../outside.md"}
    )
    result = await FileVerifier().check([rule], root)
    assert not result.passed
    assert "invalid_path" in result.defects[0]


def create_link(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this environment cannot create symlinks: {exc}")


@pytest.mark.parametrize("directory", [False, True], ids=["file-link", "parent-link"])
async def test_symlink_cannot_escape_root(tmp_path, directory):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("private", encoding="utf-8")
    create_link(root / "link", outside if directory else outside / "secret.md", directory=directory)
    result = await FileVerifier().check(
        [
            AcceptanceRule(
                type="text_contains", path="link/secret.md" if directory else "link", text="private"
            )
        ],
        root,
    )
    assert not result.passed
    assert "outside_root" in result.defects[0]


async def test_in_root_file_symlink_matches_archive_restriction(tmp_path):
    report = write_report(tmp_path)
    create_link(tmp_path / "shortcut.md", report)
    result = await FileVerifier().check(
        [AcceptanceRule(type="file_exists", path="shortcut.md")], tmp_path
    )
    assert not result.passed
    assert "symlink_not_allowed" in result.defects[0]
