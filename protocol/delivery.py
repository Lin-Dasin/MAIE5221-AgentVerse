"""Opt-in delivery contracts; the original discussion wire models stay unchanged."""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from protocol.models import (
    AcceptanceRule,
    ChatMessage,
    Contract,
    DiscussionConfig,
    DiscussionTurn,
    Identifier,
    RelativePath,
)


class DeliveryRequirement(Contract):
    requirement_id: Identifier
    owner: Identifier
    description: str = Field(min_length=1, max_length=4000)
    artifacts: list[RelativePath] = Field(min_length=1, max_length=16)
    rules: list[AcceptanceRule] = Field(default_factory=list, max_length=32)
    # File existence or a literal marker alone does not establish semantic quality.
    review_required: bool = True

    @model_validator(mode="after")
    def valid_artifacts(self) -> Self:
        if not self.description.strip():
            raise ValueError("description must contain text")
        if len(set(self.artifacts)) != len(self.artifacts) or any(
            not path.startswith("outputs/") for path in self.artifacts
        ):
            raise ValueError("artifacts must be unique files under outputs/")
        return self


class DeliveryPlan(Contract):
    """Caller-approved requirements, fixed before execution, never model-edited."""

    goal: str = Field(min_length=1)
    human_instructions: list[str] = Field(default_factory=list)
    requirements: list[DeliveryRequirement] = Field(min_length=1, max_length=32)
    max_rework_rounds: int = Field(default=2, ge=0, le=3)

    @model_validator(mode="after")
    def unique_requirements(self) -> Self:
        ids = [item.requirement_id for item in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("requirement IDs must be unique")
        return self


class DeliveryReview(Contract):
    status: Literal["passed", "failed", "unverified"]
    defects: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidenced_verdict(self) -> Self:
        if self.status == "passed" and (self.defects or not any(self.evidence)):
            raise ValueError("passing review requires evidence and no defects")
        if self.status != "passed" and not any(self.defects):
            raise ValueError("failed/unverified review requires a reason")
        return self


class ArtifactEvidence(Contract):
    path: RelativePath
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class RequirementAssessment(Contract):
    requirement_id: Identifier
    owner: Identifier
    status: Literal["passed", "failed", "unverified"]
    defects: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactEvidence] = Field(default_factory=list)
    review_evidence: list[str] = Field(default_factory=list)


HandoffIssue = Literal[
    "report_truncated", "invalid_report", "work_truncated", "contribution_budget"
]


class DeliveryHandoffTurn(DiscussionTurn):
    """Orchestrator-generated handoff; the member did not finish its turn normally."""

    handoff_issue: HandoffIssue


class DeliveryAttempt(Contract):
    attempt: int = Field(ge=0)
    turns: list[DeliveryHandoffTurn | DiscussionTurn]
    stopped_reason: str
    assessments: list[RequirementAssessment]
    tokens: int = Field(ge=0)
    changed_requirements: list[Identifier] = Field(default_factory=list)
    unchanged_requirements: list[Identifier] = Field(default_factory=list)


class DeliveryResult(Contract):
    run_id: Identifier
    status: Literal["passed", "partial", "failed", "unverified"]
    stop_reason: str
    plan: DeliveryPlan
    attempts: list[DeliveryAttempt] = Field(default_factory=list)
    assessments: list[RequirementAssessment]
    tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    pending_human_messages: list[ChatMessage] = Field(default_factory=list)


class DeliveryReviewer(Protocol):
    """D supplies trusted review of actual artifacts against the original request.

    Read-only, bounded and evidence-based. Missing evidence is unverified.
    Never execute submitted code on the host. No independent model spending:
    a future model reviewer must share the run's budget before it is supported.
    """

    async def review(
        self,
        *,
        config: DiscussionConfig,
        requirement: DeliveryRequirement,
        root: Path,
        history: Sequence[ChatMessage],
    ) -> DeliveryReview: ...
