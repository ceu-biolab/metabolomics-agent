"""Deterministic offline cases; these scripted decisions never evaluate an LLM."""

from dataclasses import asdict, dataclass
from pathlib import Path
import json

import numpy as np
import pandas as pd

from .llm import Reply
from .models import StudyConfig


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    design: str = "independent"
    analysis_kind: str = "two_group"
    pca: str = "disabled"
    variant: str = "effects"
    resume_after: int | None = None
    guards: bool = False
    expected_status: str = "complete"


CASES = (
    Case("independent", "Independent groups, missingness, BH and significant-feature heatmap."),
    Case("paired", "ID-aligned paired t-test, individual pair lines and significant heatmap.", design="paired"),
    Case("descriptive", "Descriptive objective with justified statistical and figure omissions.", analysis_kind="descriptive"),
    Case("pca_combined", "Exploratory PCA followed by independent group testing.", pca="required"),
    Case("pca_only", "Descriptive/PCA objective without univariate tests.", analysis_kind="descriptive", pca="required"),
    Case("optional_pca", "Explicit omission of an optional PCA.", analysis_kind="descriptive", pca="optional"),
    Case("no_discoveries", "Valid tests with no discoveries and a justified heatmap omission.", variant="null"),
    Case("resume", "Resume after testing, with original files temporarily unavailable.", resume_after=12),
    Case("guardrails", "Reject out-of-stage execution and reading individual tables.", guards=True),
    Case("unsafe_log", "Preserve imputed checkpoints when logarithms encounter zero.", variant="unsafe_log", expected_status="needs_input"),
)


def create_inputs(case: Case, directory: Path, seed=42):
    directory.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    count = 8
    base = np.exp(rng.normal(3, .25, (count, 4)))
    if case.variant == "null":
        values = np.vstack([base, base])
    elif case.design == "paired":
        treatment = base * np.exp(rng.normal([.7, -.5, 0., 0.], .12, (count, 4)))
        permutation = np.array([4, 1, 7, 0, 5, 3, 6, 2])
        values = np.vstack([base, treatment[permutation]])
    else:
        values = np.exp(rng.normal(3, .25, (2*count, 4)))
        values[count:, :2] *= np.exp([.9, -.7])
    table = pd.DataFrame(values, columns=[f"feature_{i+1:02d}" for i in range(4)])
    if case.variant != "null":
        table.loc[0, "feature_04"] = np.nan
    if case.variant == "unsafe_log":
        table.loc[0, "feature_01"] = 0.
    table["constant_feature"] = 5.
    table.insert(0, "sample_id", [f"eval_sample_{i+1:04d}" for i in range(2*count)])
    metadata = pd.DataFrame({"sample_id": table.sample_id,
                             "group": ["reference"]*count + ["treatment"]*count})
    if case.design == "paired":
        subjects = [f"eval_subject_{i+1:04d}" for i in range(count)]
        metadata["subject"] = subjects + [subjects[i] for i in permutation]
    table.to_csv(directory / "data.csv", index=False, float_format="%.17g")
    metadata.to_csv(directory / "metadata.csv", index=False)
    request = ("Describe the measured features and their preparation, with explicit limitations."
               if case.analysis_kind == "descriptive" else
               "Compare treatment with reference using the declared design, account for missing values and multiple testing, and explain the evidence and limitations.")
    if case.pca == "required":
        request += " Include exploratory PCA independently of univariate significance."
    elif case.pca == "optional":
        request += " PCA is optional and may be omitted with an explicit reason."
    config = StudyConfig(data_path="data.csv", metadata_path="metadata.csv",
        sample_column="sample_id", group_column="group", reference_group="reference",
        treatment_group="treatment", design=case.design,
        pair_column="subject" if case.design == "paired" else None,
        objective=request, normalization_status="Synthetic positive concentrations; no prior normalization.")
    (directory / "study.json").write_text(config.model_dump_json(indent=2) + "\n")
    return config, request


def script(case: Case):
    """Explicit test stimuli, isolated from the user-facing prompts and live tasks."""
    assessment = {"summary": "The scripted case has reviewed the saved current-stage evidence."}
    stats = case.analysis_kind == "two_group"
    figures = []
    if stats:
        figures = [{"kind": "distributions", "source_id": "d001", "scale": "testing",
                    "features": ["feature_01", "feature_04"]},
                   {"kind": "volcano", "source_id": "c001"},
                   {"kind": "boxplot", "source_id": "d001", "features": ["feature_01", "feature_02"]},
                   {"kind": "heatmap", "source_id": "d001", "scale": "testing",
                    "selection_mode": "significant", "comparison_id": "c001",
                    "centering": "feature_mean", "scaling": "feature_sd", "rows_per_page": 2,
                    "selection_rationale": "Describe all valid features below the registered adjusted-p threshold without independent-validation claims."}]
    if case.pca == "required":
        figures.extend({"kind": kind, "source_id": "pc001",
                        "selection_rationale": "Describe PCA fitted to all prepared features independently of significance selection."}
                       for kind in ["pca_scores", "pca_scree", "pca_loadings"])
    steps = []
    def add(name, arguments=None, ok=True):
        steps.append({"tool": name, "arguments": arguments or {}, "expect_ok": ok})
    if case.guards:
        add("preprocess", {"plan_id": "pp001"}, False)
    add("set_workflow_plan", {"objective": "Describe prepared features and any requested group comparison or exploratory PCA.",
        "rationale": "The offline case declares scope independently of numerical outputs and preserves all scientific limitations.",
        "analysis_kind": case.analysis_kind, "pca_requirement": case.pca,
        "required_figures": list(dict.fromkeys(figure["kind"] for figure in figures))})
    add("submit_stage_assessment", assessment)
    add("inspect_dataset")
    add("summarize_dataset", {"dataset_id": "raw", "scale": "original"})
    add("set_preprocessing_plan", {"rationale": "Use declared log2 preparation and median imputation when data contain missing cells; never add a pseudocount.",
        "imputation": "none" if case.variant == "null" else "median", "transform": "log2"})
    add("preprocess", {"plan_id": "pp001"}, case.variant != "unsafe_log")
    if case.variant == "unsafe_log":
        return steps
    for scale in ["concentrations", "testing"]:
        add("summarize_dataset", {"dataset_id": "d001", "scale": scale, "value_policy": "including_imputed"})
    if case.guards:
        add("read_result", {"artifact_id": "@current_testing_artifact"}, False)
    add("submit_stage_assessment", {**assessment,
        "limitations": ["Missingness mechanisms are not established from this synthetic table."],
        "unchecked_assumptions": ["Population representativeness was not assessed."]})
    if case.pca == "required":
        add("inspect_pca_inputs", {"dataset_id": "d001", "scale": "testing"})
        add("set_pca_plan", {"dataset_id": "d001", "diagnostic_id": "pv001",
            "rationale": "Explore all retained numeric features without labels or significance-based filtering.",
            "scale": "testing", "components": 2, "scaling": "auto", "scaling_ddof": 0})
        add("run_pca", {"dataset_id": "d001", "plan_id": "ppc001"})
        add("submit_stage_assessment", {**assessment,
            "limitations": ["PCA is exploratory and ordinary PCA does not model paired dependence."]})
    elif case.pca == "optional":
        add("skip_pca", {"rationale": "PCA is optional and the requested descriptive summaries already cover this case objective."})
        add("submit_stage_assessment", assessment)
    if stats:
        add("inspect_univariate", {"dataset_id": "d001"})
        add("set_univariate_plan", {"dataset_id": "d001", "diagnostic_id": "uv001",
            "rationale": "Use the declared design for prespecified feature-wise mean comparisons with BH correction.",
            "test": "paired_t" if case.design == "paired" else "welch_t", "correction": "fdr_bh"})
        add("compare_groups", {"dataset_id": "d001", "plan_id": "u001"})
        add("submit_stage_assessment", assessment)
    if figures:
        add("set_figure_plan", {"rationale": "Show registered current results with exact source tables, caveats and objective-specific coverage.", "figures": figures})
        for figure in figures:
            add("make_plot", figure)
        add("submit_stage_assessment", assessment)
    add("validate_workflow")
    add("finish_analysis", {"findings": [{"text": "The declared numerical outputs and their local evidence have been saved.",
        "evidence_ids": ["c001" if stats else "pc001" if case.pca == "required" else "d001"]}],
        "limitations": ["Decisions and interpretation were scripted; no live LLM reasoning was evaluated."]})
    return steps


class ScriptedProvider:
    """Fresh instances can resume from the saved tool count and current phase."""
    def __init__(self, steps):
        self.steps = steps
        self.calls = 0

    def complete(self, messages, tools, **kwargs):
        context = json.loads(messages[-1]["content"])["stage_context"]
        completed = context["tool_responses_seen"]
        results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
        for index, result in enumerate(results, start=completed - len(results)):
            if index >= len(self.steps) or result.get("ok") != self.steps[index]["expect_ok"]:
                raise ValueError(f"Offline script diverged at tool result {index+1}.")
        self.calls += 1
        if completed == len(self.steps):
            return Reply({"role": "assistant", "content": "Explicit zero handling must be clarified before a logarithmic transformation can proceed."},
                         {"total_tokens": 0}, "stop")
        step = self.steps[completed]
        arguments = dict(step["arguments"])
        if arguments.get("artifact_id") == "@current_testing_artifact":
            arguments["artifact_id"] = context["current_dataset_artifacts"]["testing"]
        return Reply({"role": "assistant", "content": None, "tool_calls": [{
            "id": f"offline_call_{completed+1:04d}", "type": "function",
            "function": {"name": step["tool"], "arguments": json.dumps(arguments)},
        }]}, {"total_tokens": 0}, "tool_calls")


def case_manifest(case):
    return {**asdict(case), "scripted_steps": script(case),
            "provider": "deterministic_script", "live_llm_decisions": "not_assessed"}
