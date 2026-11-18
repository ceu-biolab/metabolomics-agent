from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StudyConfig(Model):
    data_path: str
    metadata_path: str | None = None
    sample_column: str = "sample_id"
    group_column: str = "group"
    reference_group: str | None = None
    treatment_group: str | None = None
    analysis_scope: Literal["two_group", "descriptive"] = "two_group"
    selected_groups: list[str] = Field(default_factory=list)
    design: Literal["independent", "paired"] = "independent"
    pair_column: str | None = None
    batch_column: str | None = None
    metadata_columns: list[str] = Field(default_factory=list)
    feature_columns: list[str] | None = None
    input_scale: Literal["concentration", "intensity"] = "concentration"
    normalization_status: str = "Unknown; do not assume prior normalization."
    objective: str = "Compare the specified groups and assess robustness and data quality."

    @model_validator(mode="after")
    def check_design(self):
        if self.analysis_scope == "descriptive":
            if not self.selected_groups or len(set(self.selected_groups)) != len(self.selected_groups):
                raise ValueError("Descriptive loading requires explicit unique selected_groups.")
            if self.design != "independent" or self.reference_group is not None or self.treatment_group is not None:
                raise ValueError("Descriptive loading has no comparison groups or paired inference.")
        elif not self.reference_group or not self.treatment_group or self.reference_group == self.treatment_group:
            raise ValueError("Reference and treatment groups must differ.")
        elif self.selected_groups and self.selected_groups != [self.reference_group, self.treatment_group]:
            raise ValueError("Two-group scope selects only its declared comparison groups.")
        if self.design == "paired" and not self.pair_column:
            raise ValueError("Paired studies require pair_column.")
        if self.feature_columns is not None and not self.feature_columns:
            raise ValueError("feature_columns cannot be empty.")
        return self

    @property
    def group_order(self):
        return self.selected_groups if self.analysis_scope == "descriptive" else [self.reference_group, self.treatment_group]


TestName = Literal["welch_t", "student_t", "mannwhitney", "paired_t", "wilcoxon"]


class AnalysisPlan(Model):
    rationale: str = Field(min_length=15, max_length=4000)
    test: TestName = "welch_t"
    correction: Literal["fdr_bh", "holm-sidak", "holm", "bonferroni"] = "fdr_bh"
    alpha: float = Field(default=0.05, gt=0, lt=1)
    max_missing_fraction: float = Field(default=0.35, ge=0, lt=1)
    imputation: Literal["none", "median", "half_min", "knn"] = "none"
    zero_as_missing: bool = False
    normalization: Literal["none", "median"] = "none"
    transform: Literal["none", "log2", "log10"] = "none"
    pca_scaling: Literal["none", "auto", "pareto"] = "auto"
    purpose: Literal["primary", "exploratory"] = "primary"


class InspectArgs(Model):
    pass


class PrepareArgs(Model):
    plan_id: str


class DatasetArgs(Model):
    dataset_id: str


class PCAArgs(DatasetArgs):
    components: int = Field(default=2, ge=2, le=5)


class PlotArgs(Model):
    kind: Literal["distributions", "boxplot", "volcano", "pca", "heatmap"]
    source_id: str
    features: list[str] = Field(default_factory=list, max_length=12)


class ReadResultArgs(Model):
    artifact_id: str
    features: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=20)


class Finding(Model):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class FinishArgs(Model):
    findings: list[Finding] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=20)


class QuantitativeClaim(Model):
    comparison_id: str
    feature: str | None = None
    metric: Literal["significant_features", "features_tested", "p_value", "adjusted_p_value", "log2_fold_change", "statistic"]
    value: float


class VerifiedFinding(Finding):
    claims: list[QuantitativeClaim] = Field(default_factory=list, max_length=20)


class FinishArgsV2(FinishArgs):
    findings: list[VerifiedFinding] = Field(min_length=1, max_length=20)


class LLMConfig(Model):
    model: str = Field(min_length=1)
    base_url: str = "https://api.openai.com/v1"
    api: Literal["chat_completions", "responses"] = "chat_completions"
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    max_turns: int = Field(default=24, ge=1, le=200)
    max_tool_calls: int = Field(default=48, ge=1, le=500)
    max_completion_tokens: int = Field(default=2500, ge=128)
    max_total_tokens: int = Field(default=60000, ge=1000)
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    max_run_seconds: float = Field(default=600, gt=0)
    token_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    extra: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_extra(self):
        from urllib.parse import urlparse

        url = urlparse(self.base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.query or url.fragment):
            raise ValueError("LLM_BASE_URL must be an HTTP(S) URL without credentials/query/fragment.")
        reserved = {"model", "messages", "tools", "tool_choice", "stream", "n",
                    "max_tokens", "max_completion_tokens", "timeout", "api_key",
                    "extra_headers", "extra_body", "extra_query", "input", "max_output_tokens",
                    "store", "include", "previous_response_id", "reasoning"}
        if reserved.intersection(self.extra):
            raise ValueError(f"LLM_EXTRA_JSON cannot override {sorted(reserved)}")
        legacy_effort = self.extra.get("reasoning_effort")
        if self.reasoning_effort is not None and legacy_effort is not None and self.reasoning_effort != legacy_effort:
            raise ValueError("LLM_REASONING_EFFORT conflicts with reasoning_effort in LLM_EXTRA_JSON.")
        effort = self.reasoning_effort if self.reasoning_effort is not None else legacy_effort
        if effort is not None and effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Unsupported reasoning effort.")
        if self.model.rsplit("/", 1)[-1] in {"gpt-6-luna", "gpt-6-sol"}:
            if effort == "minimal":
                raise ValueError("This model does not support minimal effort; use none, low, medium, high, xhigh, or max.")
            if self.api == "chat_completions" and effort not in {None, "none"}:
                raise ValueError("This model requires reasoning_effort=none for Chat Completions "
                                 "function tools. Set LLM_API=responses for reasoning with tools.")
            if self.api == "chat_completions":
                effort = "none"
        self.reasoning_effort = effort
        self.extra = {k: v for k, v in self.extra.items() if k != "reasoning_effort"}
        if self.api == "chat_completions" and effort is not None:
            self.extra = {**self.extra, "reasoning_effort": effort}
        return self
