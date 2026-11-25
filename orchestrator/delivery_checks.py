"""Read-only artifact evidence and fail-closed composition of injected verdicts."""

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from protocol.delivery import (
    ArtifactEvidence,
    DeliveryPlan,
    DeliveryRequirement,
    DeliveryReview,
    DeliveryReviewer,
    RequirementAssessment,
)
from protocol.models import ChatMessage, CriticResult, DiscussionConfig
from protocol.ports import Verifier

MAX_ARTIFACT_BYTES = 20 * 1024 * 1024


def delivery_fingerprints(
    plan: DeliveryPlan, workspaces: Mapping[str, Path]
) -> dict[str, tuple[tuple[str, str | None], ...]]:
    """Track each declared path, including missing files, without inferring quality.

    Unsafe/unreadable/oversized paths exclude the requirement from comparison;
    they must not be misreported as unchanged or prevent normal acceptance.
    """
    snapshots = {}
    for requirement in plan.requirements:
        paths = []
        try:
            for path in requirement.artifacts:
                single = requirement.model_copy(update={"artifacts": [path]})
                try:
                    evidence = artifact_evidence(workspaces[requirement.owner], single)
                    paths.append((path, evidence[0].sha256))
                except ValueError as exc:
                    if not str(exc).startswith("missing_artifact:"):
                        raise
                    paths.append((path, None))
        except (OSError, ValueError):
            continue
        snapshots[requirement.requirement_id] = tuple(paths)
    return snapshots


def _near_miss(root: Path, target: Path) -> str | None:
    """Name a same-stem sibling with a different suffix for an actionable defect."""
    try:
        parent = target.parent.resolve()
        if not parent.is_relative_to(root.resolve()) or not parent.is_dir():
            return None
        candidates = sorted(
            path
            for path in parent.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.stem == target.stem
            and path.suffix != target.suffix
        )
    except OSError:
        return None
    if not candidates:
        return None
    try:
        name = candidates[0].relative_to(root).as_posix()
    except ValueError:
        name = candidates[0].name
    return f"Found near-miss file {name}; write the exact required path"


def artifact_evidence(root: Path, requirement: DeliveryRequirement) -> list[ArtifactEvidence]:
    """Fingerprint ordinary files; deny links in any relative path component."""
    evidence = []
    for relative in requirement.artifacts:
        target = root
        for component in Path(relative).parts:
            target = target / component
            if target.is_symlink():
                raise ValueError(f"unsafe_artifact: {relative} contains a symlink")
        if not target.resolve().is_relative_to(root) or not target.is_file():
            detail = _near_miss(root, target)
            suffix = f"; {detail}" if detail else ""
            raise ValueError(
                f"missing_artifact: {relative} must be an ordinary workspace file{suffix}"
            )
        digest = hashlib.sha256()
        size = 0
        with target.open("rb") as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES:
                    raise ValueError(f"artifact_too_large: {relative} exceeds 20 MiB")
                digest.update(chunk)
        evidence.append(ArtifactEvidence(path=relative, sha256=digest.hexdigest(), size_bytes=size))
    return evidence


async def assess_delivery(
    *,
    plan: DeliveryPlan,
    config: DiscussionConfig,
    workspaces: Mapping[str, Path],
    verifier: Verifier,
    reviewer: DeliveryReviewer | None,
    history: Sequence[ChatMessage],
    timeout_sec: float,
) -> list[RequirementAssessment]:
    """Recheck every requirement after each attempt; never carry forward stale passes."""
    assessments = []
    for requirement in plan.requirements:
        assessment = RequirementAssessment(
            requirement_id=requirement.requirement_id, owner=requirement.owner, status="unverified"
        )
        assessments.append(assessment)
        root = workspaces[requirement.owner]
        try:
            async with asyncio.timeout(timeout_sec):
                try:
                    before = await asyncio.to_thread(artifact_evidence, root, requirement)
                except ValueError as exc:
                    assessment.status = "failed"
                    assessment.defects = [str(exc)]
                    continue
                assessment.artifacts = before
                if requirement.rules:
                    verdict = await verifier.check(
                        [rule.model_copy(deep=True) for rule in requirement.rules], root
                    )
                    verdict = CriticResult.model_validate(verdict.model_dump())
                    if verdict.checks_total != len(requirement.rules) or (
                        verdict.passed and verdict.defects
                    ):
                        raise ValueError("inconsistent verifier result")
                    if not verdict.passed:
                        assessment.status = "failed"
                        assessment.defects = verdict.defects or ["machine acceptance failed"]
                        continue
                if requirement.review_required:
                    if reviewer is None:
                        assessment.defects = [
                            "review_unavailable: semantic acceptance is unverified"
                        ]
                        continue
                    review = await reviewer.review(
                        config=config.model_copy(deep=True),
                        requirement=requirement.model_copy(deep=True),
                        root=root,
                        history=[message.model_copy(deep=True) for message in history],
                    )
                    review = DeliveryReview.model_validate(review.model_dump())
                    assessment.status = review.status
                    assessment.defects = review.defects
                    assessment.review_evidence = review.evidence
                else:
                    assessment.status = "passed"
                after = await asyncio.to_thread(artifact_evidence, root, requirement)
                if after != before:
                    assessment.status = "unverified"
                    assessment.defects = ["artifact_changed_during_review: rerun acceptance"]
                    assessment.artifacts = []
        except Exception as exc:
            assessment.status = "unverified"
            assessment.defects = [f"verification_error: {type(exc).__name__}"]
    return assessments
