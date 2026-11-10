"""Contracts for the five-stage, univariate workflow."""

from typing import Any, Literal

from pydantic import Field, model_validator

from .evidence_models import StageIssue
from .models import Finding, Model, TestName

StageID = Literal[
    "01_coordination", "02_preparation", "03_univariate", "04_visualization", "05_reporting",
]
FigureKind = Literal["distributions", "boxplot", "volcano"]


class WorkflowPlan(Model):
    objective: str = Field(min_length=5, max_length=4000)
    rationale: str = Field(min_length=15, max_length=4000)
    analysis_kind: Literal["descriptive", "two_group"] = "two_group"
    required_figures: list[FigureKind] = Field(default_factory=lambda: ["distributions", "volcano"])

    @model_validator(mode="after")
    def check_figures(self):
        if self.analysis_kind == "descriptive" and "volcano" in self.required_figures:
            raise ValueError("Descriptive workflows cannot require a volcano plot.")
        if len(set(self.required_figures)) != len(self.required_figures):
            raise ValueError("Required figure kinds must be unique.")
        return self


class PreprocessingPlan(Model):
    rationale: str = Field(min_length=15, max_length=4000)
    max_missing_fraction: float = Field(default=0.35, ge=0, lt=1)
    imputation: Literal["none", "median", "half_min", "knn"] = "none"
    zero_as_missing: bool = False
    normalization: Literal["none", "median"] = "none"
    transform: Literal["none", "log2", "log10"] = "none"


class UnivariatePlan(Model):
    dataset_id: str
    rationale: str = Field(min_length=15, max_length=4000)
    test: TestName = "welch_t"
    correction: Literal["fdr_bh", "holm-sidak", "holm", "bonferroni"] = "fdr_bh"
    alpha: float = Field(default=0.05, gt=0, lt=1)
    purpose: Literal["primary", "exploratory"] = "primary"


class UnivariatePlanV1(UnivariatePlan):
    diagnostic_id: str
    alternative: Literal["two-sided"] = "two-sided"
    hypothesis_family: Literal["valid_features_in_comparison"] = "valid_features_in_comparison"
    value_policy: Literal["including_imputed"] = "including_imputed"


class FigureSpec(Model):
    kind: FigureKind
    source_id: str
    features: list[str] = Field(default_factory=list, max_length=12)


class FigurePlan(Model):
    rationale: str = Field(min_length=15, max_length=4000)
    figures: list[FigureSpec] = Field(default_factory=list, max_length=12)


class CompareArgs(Model):
    dataset_id: str
    plan_id: str


class SummaryArgs(Model):
    dataset_id: str = "raw"
    scale: Literal["original", "concentrations", "testing"] = "original"
    grouping: Literal["group", "overall"] = "group"
    value_policy: Literal["observed_only", "including_imputed"] = "observed_only"
    features: list[str] = Field(default_factory=list, max_length=20)
    include_mode: bool = False


class StageAssessment(Model):
    summary: str = Field(min_length=5, max_length=4000)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    unchecked_assumptions: list[str] = Field(default_factory=list, max_length=20)
    concerns: list[str] = Field(default_factory=list, max_length=20)
    needs_input: bool = False


class RevisionArgs(Model):
    target: Literal["02_preparation", "03_univariate", "04_visualization"]
    rationale: str = Field(min_length=15, max_length=4000)


class ReadStageArgs(Model):
    stage_id: StageID


class StageHandoff(Model):
    schema_version: int = 1
    workflow_revision: int
    stage_id: StageID
    attempt_id: str
    next_stage: StageID | None
    study: dict[str, Any]
    workflow_scope: dict[str, Any]
    plan_ids: dict[str, str | None]
    dataset_id: str | None
    comparison_ids: list[str]
    figure_ids: list[str]
    artifact_ids: list[str]
    aggregates: dict[str, Any]
    preparation: dict[str, Any]
    issues: list[StageIssue]
    limitations: list[str]
    descriptive_summaries: list[dict[str, Any]] = Field(default_factory=list)
    univariate_results: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowState(Model):
    schema_version: int = 1
    protocol: Literal["staged_v1"] = "staged_v1"
    revision: int = 1
    active_stage: StageID = "01_coordination"
    plan_id: str | None = None
    preprocessing_plan_id: str | None = None
    diagnostic_ids: list[str] = Field(default_factory=list)
    summary_ids: list[str] = Field(default_factory=list)
    dataset_id: str | None = None
    univariate_plan_id: str | None = None
    univariate_diagnostic_id: str | None = None
    univariate_attempt_ids: list[str] = Field(default_factory=list)
    comparison_ids: list[str] = Field(default_factory=list)
    figure_plan_id: str | None = None
    figure_ids: list[str] = Field(default_factory=list)
    completed_reports: dict[str, str] = Field(default_factory=dict)
    handoff_ids: dict[str, str] = Field(default_factory=dict)
    current_attempt: str | None = None
    preparation_summary: dict[str, Any] = Field(default_factory=dict)
    revisions: list[dict[str, Any]] = Field(default_factory=list)
