"""Versioned local evidence contracts; no orchestration or LLM decisions."""

from typing import Any, Literal

from pydantic import Field

from .models import Model


class StageIssue(Model):
    code: str
    severity: Literal["info", "warning", "error"]
    source: Literal["computed_check", "tool_error", "agent_assessment", "inherited"]
    message: str
    evidence_ids: list[str] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    blocking: bool = False
    resolution: Literal["open", "resolved"] = "open"
    suggested_action: str | None = None


class EvidenceArtifact(Model):
    schema_version: int = 1
    id: str
    kind: str
    path: str
    sha256: str
    bytes: int
    stage_id: str | None = None
    attempt_id: str | None = None
    operation_id: str | None = None
    parent_ids: list[str] = Field(default_factory=list)
    plan_id: str | None = None
    dataset_id: str | None = None
    scale: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    rows: int | None = None
    columns: int | None = None
    schema_path: str | None = None
    schema_sha256: str | None = None
    individual_samples: bool = False


class OperationRecord(Model):
    id: str
    name: str
    status: Literal["running", "completed", "skipped", "failed", "interrupted"]
    started_at: str
    finished_at: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class StageReport(Model):
    schema_version: int = 1
    stage_id: str
    attempt_id: str
    tool: str
    status: Literal[
        "running", "completed", "completed_with_warnings", "blocked", "failed",
        "interrupted", "skipped",
    ] = "running"
    started_at: str
    finished_at: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    plan_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    operations: list[OperationRecord] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[StageIssue] = Field(default_factory=list)
    no_issues_detected: bool | None = None
    limitations: list[str] = Field(default_factory=list)
    unchecked_assumptions: list[str] = Field(default_factory=list)
    agent_assessment: dict[str, Any] = Field(default_factory=dict)
    continuation: dict[str, Any] = Field(default_factory=dict)
