"""PCA references, stage routing, automated selection, pagination and recovery."""

import json

import numpy as np
import pandas as pd
import pytest
from sklearn.decomposition import PCA

from conftest import call
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry
from metabolomics_agent.workflow import STAGES
from metabolomics_agent.workflow_models import FigureSpecV2, WorkflowPlanV2
from test_workflow import assess, prepare_staged, compare_staged


@pytest.fixture
def extension(independent_study):
    config, root, original = independent_study
    made = []
    def make(*, pca="disabled", descriptive=False, required=None, frame=None, **preparation):
        directory = root / f"extended-{len(made)}"
        directory.mkdir()
        (original if frame is None else frame).to_csv(directory / "values.csv", index=False)
        tools = ToolRegistry(RunState(config, directory, directory / "run"))
        made.append(tools)
        call(tools, "set_workflow_plan", {
            "objective": "Explore the prepared feature space and describe registered group differences.",
            "rationale": "Use independent exploratory PCA and auditable results-based heatmap selection when requested.",
            "analysis_kind": "descriptive" if descriptive else "two_group", "pca_requirement": pca,
            "required_figures": required if required is not None else ["heatmap"],
        })
        assess(tools)
        prepare_staged(tools, **preparation)
        return tools
    yield make
    for tools in made:
        tools.state.workflow.interrupt("End of extension verification.")


def pca_plan(tools, **options):
    source = {"dataset_id": "d001", "scale": options.pop("scale", "testing"),
              "features": options.pop("features", [])}
    diagnostic = call(tools, "inspect_pca_inputs", source)
    plan = call(tools, "set_pca_plan", {**source, "diagnostic_id": diagnostic["diagnostic_id"],
        "rationale": "Explore the declared numeric source independently of group labels and inferential p-values.", **options})
    return diagnostic, plan


def pca_run(tools, **options):
    diagnostic, plan = pca_plan(tools, **options)
    result = call(tools, "run_pca", {"dataset_id": "d001", "plan_id": plan["plan_id"]})
    return diagnostic, plan, result


def figure_plan(tools, figures):
    return call(tools, "set_figure_plan", {"rationale": "Display the frozen exploratory or inferential evidence with exact selection and sources.", "figures": figures})


def finish(tools):
    return call(tools, "finish_analysis", {"findings": [{"text": "Registered numerical evidence and its provenance are available.",
        "evidence_ids": ["d001"]}], "limitations": ["Exploratory figures do not independently confirm group differences."]})


def test_contracts_prevent_ambiguous_selection_and_scope():
    spec = FigureSpecV2(kind="heatmap", source_id="d001", selection_mode="significant", comparison_id="c001")
    assert spec.features == []
    with pytest.raises(ValueError):
        FigureSpecV2(**{**spec.model_dump(), "features": ["a"]})
    with pytest.raises(ValueError):
        FigureSpecV2(kind="boxplot", source_id="d001", selection_mode="significant", comparison_id="c001")
    with pytest.raises(ValueError):
        WorkflowPlanV2(objective="Explore PCA", rationale="An explicitly registered exploratory objective.", required_figures=["pca_scores"])


@pytest.mark.parametrize("scaling,ddof", [("none", 0), ("auto", 0), ("auto", 1), ("pareto", 0), ("pareto", 1)])
def test_pca_matches_sklearn_and_preserves_sources(extension, scaling, ddof):
    tools = extension(pca="required", descriptive=True, required=["pca_scores"], transform="log2")
    state = tools.state
    before = state.datasets["d001"].testing.copy(deep=True)
    assert state.workflow.in_pca_block()
    assert not tools.execute("inspect_univariate", {"dataset_id": "d001"})["ok"]
    diagnostic, plan, result = pca_run(tools, scaling=scaling, scaling_ddof=ddof)
    expected = before - before.mean()
    if scaling != "none":
        sd = before.std(ddof=ddof)
        expected /= np.sqrt(sd) if scaling == "pareto" else sd
    model = PCA(n_components=2, svd_solver="full", whiten=False)
    expected_scores = model.fit_transform(expected.to_numpy())
    matrix = state.read_artifact(result["artifacts"]["matrix"]).set_index(state.config.sample_column)
    np.testing.assert_allclose(matrix, expected, atol=1e-12)
    scores = state.read_artifact(result["artifacts"]["scores"])
    np.testing.assert_allclose(scores[["PC1", "PC2"]], expected_scores, atol=1e-12)
    weights = state.read_artifact(result["artifacts"]["loadings"])
    np.testing.assert_allclose(weights[["PC1", "PC2"]], model.components_.T, atol=1e-12)
    np.testing.assert_allclose(result["explained_variance_ratio"], model.explained_variance_ratio_)
    assert result["imputed_cells"] == 0 and result["feature_count"] == 2
    pd.testing.assert_frame_equal(state.datasets["d001"].testing, before)
    assert state.datasets["d001"].pca_values is None
    assert not tools.execute("run_pca", {"dataset_id": "d001", "plan_id": plan["plan_id"]})["ok"]
    for key in ["scores", "input", "matrix"]:
        assert not tools.execute("read_result", {"artifact_id": result["artifacts"][key]})["ok"]
    assert call(tools, "read_result", {"artifact_id": result["artifacts"]["variance"]})["matching_rows"] == 2
    context = json.dumps(call(tools, "get_stage_context"))
    assert not any(sid in context for sid in state.raw.index)
    assess(tools)
    assert state.workflow.stage == STAGES[3]
    assert not state.comparisons
    state.evidence.validate()


def test_pca_block_then_univariate_and_required_pca_cannot_be_skipped(extension):
    tools = extension(pca="required")
    assert not tools.execute("skip_pca", {"rationale": "Try to omit a required exploratory analysis."})["ok"]
    assert not tools.execute("submit_stage_assessment", {"summary": "No PCA fit exists yet."})["ok"]
    pca_run(tools)
    result = assess(tools)
    assert result["next_block"] == "univariate" and tools.state.workflow.stage == STAGES[2]
    assert "run_pca" not in tools.state.workflow.allowed_tools()
    compare_staged(tools)
    assess(tools)
    assert tools.state.workflow.stage == STAGES[3]


def test_optional_pca_skip_is_saved_and_descriptive_scope_advances(extension):
    tools = extension(pca="optional", descriptive=True, required=[])
    result = call(tools, "skip_pca", {"rationale": "PCA is optional and not needed for the requested descriptive summaries."})
    assert result["status"] == "skipped"
    assess(tools)
    assert tools.state.workflow.stage == STAGES[4]
    finish(tools)
    report = (tools.state.output_dir / "report.md").read_text()
    assert "PCA skipped" in report and not tools.state.pcas and not tools.state.comparisons


def test_pca_input_drift_and_rank_failure_preserve_checkpoints(extension):
    tools = extension(pca="required", descriptive=True)
    diagnostic, plan = pca_plan(tools)
    dataset = tools.state.datasets["d001"]
    dataset.testing.iloc[0, 0] += .5
    assert "changed" in tools.execute("run_pca", {"dataset_id": "d001", "plan_id": plan["plan_id"]})["error"]
    dataset.testing.iloc[0, 0] -= .5
    # Another run has two varying but collinear features: no artificial second component.
    frame = tools.state.raw.reset_index().join(tools.state.metadata.group.reset_index(drop=True))
    frame["lipid_b"] = 2 * frame.lipid_a
    other = extension(pca="required", descriptive=True, frame=frame)
    diagnostic, plan = pca_plan(other)
    result = other.execute("run_pca", {"dataset_id": "d001", "plan_id": plan["plan_id"]})
    assert not result["ok"] and "rank" in result["error"]
    attempt = other.state.pca_attempts["pc001"]
    assert attempt["status"] == "failed" and "matrix" in attempt["artifacts"]
    assert not other.state.pcas and not other.state.workflow.validate()["valid"]
    other.state.evidence.validate()


def test_significant_heatmap_selection_exact_and_numerics_unchanged(extension):
    tools = extension(transform="log2")
    _, comparison = compare_staged(tools)
    assess(tools)
    spec = {"kind": "heatmap", "source_id": "d001", "selection_mode": "significant", "comparison_id": "c001",
            "scale": "testing", "centering": "feature_mean", "scaling": "feature_sd"}
    figure_plan(tools, [spec])
    state = tools.state
    source_path = state.output_dir / state.artifacts[comparison["artifact_id"]]["path"]
    before = source_path.read_bytes()
    result = call(tools, "make_plot", spec)
    selection = state.read_artifact(result["artifacts"]["selection"])
    source = state.read_artifact(comparison["artifact_id"])
    expected = source.loc[(source.status == "ok") & (source.adjusted_p_value < comparison["alpha"]), "feature"].tolist()
    assert selection.loc[selection.included, "feature"].tolist() == expected
    assert result["selected_count"] == len(expected) and result["status"] == "completed"
    assert source_path.read_bytes() == before
    assert "same study data" in state.artifacts[result["artifact_id"]]["caption"]
    assess(tools)
    finish(tools)
    assert "not independent validation" in (state.output_dir / "report.md").read_text()


def test_no_significant_heatmap_is_justified_omission(extension):
    tools = extension()
    _, comparison = compare_staged(tools, alpha=1e-15)
    assert comparison["significant_features"] == 0
    assess(tools)
    spec = {"kind": "heatmap", "source_id": "d001", "selection_mode": "significant", "comparison_id": "c001"}
    figure_plan(tools, [spec])
    result = call(tools, "make_plot", spec)
    assert result["status"] == "skipped_no_significant" and not result["artifact_ids"]
    assert tools.state.workflow.validate()["valid"]
    assess(tools)
    finish(tools)
    report = (tools.state.output_dir / "report.md").read_text()
    assert "skipped_no_significant" in report


def test_pagination_all_selected_features_and_failed_page_cannot_complete(extension, monkeypatch):
    from metabolomics_agent import visualization
    frame = pd.DataFrame({"sample_id": [f"S{i:03d}" for i in range(24)], "group": ["N"]*12+["T"]*12,
                          **{f"v{i:02d}": np.r_[np.arange(1,13), np.arange(11,23)]+i for i in range(14)}})
    tools = extension(frame=frame)
    compare_staged(tools)
    assess(tools)
    spec = {"kind": "heatmap", "source_id": "d001", "selection_mode": "significant", "comparison_id": "c001", "rows_per_page": 5}
    figure_plan(tools, [spec])
    original = visualization._render
    def failed_page(state, args, display, features, metadata):
        if metadata["page"] == 2:
            raise ValueError("Controlled failure on heatmap page 2")
        return original(state, args, display, features, metadata)
    monkeypatch.setattr(visualization, "_render", failed_page)
    assert not tools.execute("make_plot", spec)["ok"]
    partial = tools.state.plot_results["g001"]
    assert partial["status"] == "failed" and len(partial["figure_ids"]) == 1
    assert not tools.state.workflow.validate()["valid"]
    monkeypatch.setattr(visualization, "_render", original)
    result = call(tools, "make_plot", spec)
    assert result["selected_count"] == 14 and result["pages_expected"] == 3
    assert len(result["artifact_ids"]) == 3 and tools.state.workflow.validate()["valid"]
    selected = [feature for aid in result["artifact_ids"] for feature in tools.state.artifacts[aid]["selected_features"]]
    assert selected == list(frame.columns[2:]) or set(selected) == set(frame.columns[2:])
    assert len(set(selected)) == 14
    tools.state.evidence.validate()


def test_pca_figures_restoration_and_descriptive_report(extension):
    tools = extension(pca="required", descriptive=True, required=["pca_scores", "pca_scree", "pca_loadings"], transform="log2")
    diagnostic, plan, result = pca_run(tools)
    state = tools.state
    state.workflow.interrupt("Check recovery after completed PCA and before its assessment.")
    restored = RunState.restore(state.output_dir)
    resumed = ToolRegistry(restored)
    assert restored.workflow.in_pca_block() and restored.workflow.data.pca_ids == ["pc001"]
    assert "03_pca" in restored.workflow.bundle
    assert not resumed.execute("run_pca", {"dataset_id": "d001", "plan_id": plan["plan_id"]})["ok"]
    assess(resumed)
    assert restored.workflow.stage == STAGES[3]
    specs = [{"kind": kind, "source_id": "pc001"} for kind in ["pca_scores", "pca_scree", "pca_loadings"]]
    figure_plan(resumed, specs)
    for spec in specs:
        figure = call(resumed, "make_plot", spec)
        data = restored.read_artifact(figure["plot_artifact_ids"]["display"])
        if spec["kind"] == "pca_scores":
            scores = restored.read_artifact(result["artifacts"]["scores"])
            np.testing.assert_allclose(data[["PC1", "PC2"]], scores[["PC1", "PC2"]])
        elif spec["kind"] == "pca_scree":
            np.testing.assert_allclose(data.cumulative_variance_ratio, np.cumsum(result["explained_variance_ratio"]))
        else:
            assert set(data.feature) == {"lipid_a", "lipid_b"}
        assert not resumed.execute("read_result", {"artifact_id": figure["plot_artifact_ids"]["source"]})["ok"]
    assess(resumed)
    serialized = json.dumps(call(resumed, "get_stage_context"))
    assert not any(sid in serialized for sid in restored.raw.index)
    finish(resumed)
    report = (restored.output_dir / "report.md").read_text()
    assert "PCA was performed" in report and "ddof=0" in report
    assert "Univariate testing and comparison-derived volcano plots were omitted" in report
    assert "Unrepresented remainder" in report and "not automatically variable-component correlations" in report
    restored.evidence.validate()


def test_missing_pca_inputs_not_imputed_inside_pca(extension, independent_study):
    _, _, frame = independent_study
    frame = frame.copy()
    frame.loc[0, "lipid_a"] = np.nan
    tools = extension(pca="optional", descriptive=True, frame=frame, required=[])
    diagnostic = call(tools, "inspect_pca_inputs", {"dataset_id": "d001"})
    assert not diagnostic["viable"] and diagnostic["missing_cells"] == 1
    assert not tools.execute("set_pca_plan", {"dataset_id": "d001", "diagnostic_id": diagnostic["diagnostic_id"],
        "rationale": "Attempt fitting a matrix with an explicitly unfilled missing cell."})["ok"]
    assert tools.state.datasets["d001"].testing.isna().sum().sum() == 1
    call(tools, "skip_pca", {"rationale": "The optional PCA is omitted because the prepared source is intentionally incomplete."})
    assess(tools)
    finish(tools)


def test_old_F_restores_without_new_tools_or_selector(extension):
    # Simulate an F archive with its own frozen bundle and V1 contracts.
    tools = extension(required=[])
    state = tools.state
    state.workflow.interrupt("Freeze an older capability profile.")
    state.run_info["visualization_version"] = 1
    state.run_info.pop("pca_version")
    state.run_info["workflow_prompts"].pop("03_pca")
    for plan in state.workflow_plans.values():
        raw = plan.model_dump()
        raw.pop("pca_requirement")
        from metabolomics_agent.workflow_models import WorkflowPlanV1
        state.workflow_plans[state.workflow.data.plan_id] = WorkflowPlanV1.model_validate(raw)
    state.write_json("run.json", state.run_info)
    state.snapshot()
    restored = RunState.restore(state.output_dir)
    assert "03_pca" not in restored.workflow.bundle
    assert "run_pca" not in restored.workflow.allowed_tools()
    assert restored.run_info["visualization_version"] == 1
    schemas = ToolRegistry(restored).schemas(stage=STAGES[3])
    # Runtime always uses its current stage; the archived visualization model is restored independently.
    assert not hasattr(restored.workflow_plans[restored.workflow.data.plan_id], "pca_requirement")
    assert schemas


def test_superseded_comparison_cannot_select_heatmap(extension):
    tools = extension()
    compare_staged(tools)
    assess(tools)
    call(tools, "request_revision", {"target": STAGES[2], "rationale": "Revisit the comparison while keeping the same prepared dataset and preserving historical results."})
    compare_staged(tools)
    assess(tools)
    spec = {"kind": "heatmap", "source_id": "d001", "selection_mode": "significant", "comparison_id": "c001"}
    assert not tools.execute("set_figure_plan", {"rationale": "Reject selection from a superseded comparison of this dataset.", "figures": [spec]})["ok"]
    spec["comparison_id"] = "c002"
    figure_plan(tools, [spec])
    assert call(tools, "make_plot", spec)["status"] == "completed"


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_or_interrupted_pca_fit_keeps_prepared_matrix(extension, monkeypatch, interrupted):
    from metabolomics_agent import pca as implementation
    tools = extension(pca="required", descriptive=True)
    _, plan = pca_plan(tools)
    def fail(*args, **kwargs):
        if interrupted:
            raise KeyboardInterrupt("Controlled PCA interruption")
        raise ValueError("Controlled PCA fit failure")
    monkeypatch.setattr(implementation.PCA, "fit_transform", fail)
    arguments = {"dataset_id": "d001", "plan_id": plan["plan_id"]}
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            tools.execute("run_pca", arguments)
    else:
        assert not tools.execute("run_pca", arguments)["ok"]
    attempt = tools.state.pca_attempts["pc001"]
    assert attempt["status"] == ("interrupted" if interrupted else "failed")
    assert set(attempt["artifacts"]) == {"input", "scaling", "matrix"}
    tools.state.workflow.interrupt("Preserve partial PCA evidence.")
    restored = RunState.restore(tools.state.output_dir)
    assert restored.pca_attempts["pc001"]["status"] == attempt["status"]
    assert not restored.pcas
    restored.evidence.validate()
