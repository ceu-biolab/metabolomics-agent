"""Versioned evaluation contracts. Expected answers belong to the evaluator only."""

from typing import Any, Literal

from pydantic import Field, model_validator

from .models import Model, StudyConfig
from .workflow_models import StageID


Verdict = Literal["passed", "failed", "not_applicable", "not_assessed", "blocked_by_dependency"]
BlockID = Literal["01_coordination", "02_preparation", "03_univariate", "03_pca",
                  "04_visualization", "05_reporting"]


class FileReference(Model):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DatasetManifest(Model):
    schema_version: Literal[1] = 1
    dataset_id: str = Field(min_length=1)
    root: str = "."
    files: list[FileReference] = Field(min_length=1)
    source: str
    units: str = "unknown"
    normalization_status: str = "unknown"
    sample_column: str = "sample_id"
    feature_columns: list[str] | None = None
    group_counts: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_files(self):
        if len({entry.path for entry in self.files}) != len(self.files):
            raise ValueError("Dataset file paths must be unique.")
        if any(count < 0 for count in self.group_counts.values()):
            raise ValueError("Group counts must be nonnegative.")
        return self


class ReferenceEntry(FileReference):
    id: str
    origin: Literal["published", "author_script", "collaborator", "mathematical", "synthetic"]
    source: str
    producer: str
    review_status: Literal["pending", "reviewed"] = "pending"
    independence: str


class ReferenceManifest(Model):
    schema_version: Literal[1] = 1
    entries: list[ReferenceEntry] = Field(default_factory=list)
    support_files: list[FileReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Reference IDs must be unique.")
        return self


class Observation(Model):
    document: Literal["state", "run", "audit", "findings", "stage_report", "table"] = "state"
    path: list[str] = Field(default_factory=list)
    artifact_id: str | None = None
    artifact_kind: str | None = None
    column: str | None = None

    @model_validator(mode="after")
    def table_source(self):
        if self.document == "table" and bool(self.artifact_id) == bool(self.artifact_kind):
            raise ValueError("A table requires exactly one artifact_id or artifact_kind.")
        if self.document != "table" and (self.artifact_id or self.artifact_kind or self.column):
            raise ValueError("Artifact/column selectors require a table observation.")
        return self


class Condition(Model):
    observation: Observation
    equals: Any
    negate: bool = False


class Tolerance(Model):
    rtol: float = Field(default=1e-9, ge=0)
    atol: float = Field(default=0, ge=0)


class MetricSpec(Model):
    id: str = Field(min_length=1)
    validator: Literal["exact", "one_of", "numeric", "set", "table", "pca_weights", "audit", "manual"]
    observation: Observation | None = None
    expected: Any = None
    expected_is_set: bool = False
    reference_id: str | None = None
    keys: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    component_blocks: list[list[str]] = Field(default_factory=list)
    tolerance: Tolerance = Field(default_factory=Tolerance)
    required: bool = True
    critical: bool = False
    provenance: str = Field(min_length=1)
    audit_prefixes: list[str] = Field(default_factory=list)
    when: Condition | None = None
    review_scope: Literal["scientific", "visual", "narrative"] = "scientific"

    @model_validator(mode="after")
    def coherent(self):
        if "expected" in self.model_fields_set and "expected_is_set" not in self.model_fields_set:
            self.expected_is_set = True
        if self.validator not in {"manual", "audit"} and self.observation is None:
            raise ValueError("An automatic metric requires an observation.")
        if self.validator in {"table", "pca_weights"} and (not self.reference_id or not self.keys):
            raise ValueError("Table comparisons require a reference and explicit identity keys.")
        if self.component_blocks:
            members = [name for block in self.component_blocks for name in block]
            if len(members) != len(set(members)) or set(members) != set(self.columns):
                raise ValueError("PCA blocks must partition the declared components exactly.")
        if self.validator == "audit" and not self.audit_prefixes:
            raise ValueError("Audit metrics require explicit check prefixes.")
        if self.validator == "one_of" and self.expected_is_set and (not isinstance(self.expected, list) or not self.expected):
            raise ValueError("Alternative accepted answers require a nonempty list.")
        if self.reference_id is not None and self.expected_is_set:
            raise ValueError("Use a reference or an inline expected answer, not both.")
        return self


class StageExpectation(Model):
    stage_id: BlockID
    applicability: Literal["required", "optional", "conditional", "out_of_scope"] = "required"
    reason: str = Field(min_length=1)
    condition: Condition | None = None
    depends_on: list[BlockID] = Field(default_factory=list)
    requires_artifact_kinds: list[str] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self):
        if (self.applicability == "conditional") != (self.condition is not None):
            raise ValueError("Only conditional stages require a condition.")
        if self.applicability == "required" and not self.metrics:
            raise ValueError("Required stages need explicit metrics.")
        if self.stage_id in self.depends_on:
            raise ValueError("A stage cannot depend on itself.")
        return self


class ScriptStep(Model):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expect_ok: bool = True


class StageInputPackage(Model):
    stage_id: BlockID
    root: str
    files: list[FileReference] = Field(min_length=1)
    provenance: str = Field(min_length=1)


class EvaluationCase(Model):
    schema_version: Literal[1] = 1
    case_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$")
    mode: Literal["reproduction", "open_analysis"]
    dataset: DatasetManifest
    study: StudyConfig
    request: str = Field(min_length=1)
    references: ReferenceManifest = Field(default_factory=ReferenceManifest)
    stages: list[StageExpectation] = Field(min_length=1)
    script: list[ScriptStep] = Field(default_factory=list)
    expected_execution_status: str = "complete"
    continuation_policy: Literal["observe", "stop_on_critical"] = "observe"
    seed: int = 42
    stage_packages: list[StageInputPackage] = Field(default_factory=list)

    @model_validator(mode="after")
    def graph(self):
        import json
        json.dumps(self.model_dump(), allow_nan=False)
        stages = [stage.stage_id for stage in self.stages]
        metrics = [metric.id for stage in self.stages for metric in stage.metrics]
        if len(set(stages)) != len(stages) or len(set(metrics)) != len(metrics):
            raise ValueError("Stage and metric IDs must be unique within a case.")
        seen = set()
        references = {entry.id for entry in self.references.entries}
        packages = [package.stage_id for package in self.stage_packages]
        if len(packages) != len(set(packages)) or set(packages) - set(stages):
            raise ValueError("Stage packages must have unique registered stages.")
        for stage in self.stages:
            if set(stage.depends_on) - seen:
                raise ValueError("Dependencies must refer to earlier registered stages.")
            seen.add(stage.stage_id)
            for metric in stage.metrics:
                if metric.reference_id is not None and metric.reference_id not in references:
                    raise ValueError(f"Unknown reference: {metric.reference_id}")
        return self


class MetricResult(Model):
    id: str
    status: Verdict
    required: bool
    critical: bool
    expected: Any = None
    observed: Any = None
    tolerance: Tolerance | None = None
    denominator: int | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class StageEvaluation(Model):
    stage_id: BlockID
    production_stage_id: StageID
    attempt_id: str | None = None
    execution_status: str
    status: Verdict
    metrics: list[MetricResult]
    summary: dict[str, Any]
    reason: str


class CaseEvaluation(Model):
    schema_version: Literal[1] = 1
    case_id: str
    mode: Literal["reproduction", "open_analysis"]
    execution_mode: Literal["end_to_end", "stage_isolated"] = "end_to_end"
    analysis_status: str
    analysis_finished: bool
    expected_outcome_match: bool
    status: Verdict
    stages: list[StageEvaluation]
    summary: dict[str, Any]
    api_calls: int = 0
    scientific_review: Literal["pending", "partial", "reviewed"] = "pending"
    narrative_scope: str = "Only explicitly registered claims/checks are evaluated."
    scientific_run: str | None = None
    evidence_index_sha256: str | None = None


class ManualReview(Model):
    case_id: str
    metric_id: str
    reviewer: str = Field(min_length=1)
    reviewed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    evidence_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["passed", "failed"]
    explanation: str = Field(min_length=1)
