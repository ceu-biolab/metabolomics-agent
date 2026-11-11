"""Persisted drawing inputs, display transformations, objective coverage and privacy."""

import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from conftest import call
from metabolomics_agent.evidence_models import StageIssue
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry
from metabolomics_agent.workflow import STAGES
from test_workflow import assess, coordinate, prepare_staged, compare_staged


@pytest.fixture
def plots(independent_study):
    config, root, original = independent_study
    created = []

    def make(*, paired=False, missing=False, descriptive=True, required=None, old=False):
        directory = root / f"plot-{len(created)}"
        directory.mkdir()
        frame = original.copy()
        if missing:
            frame.loc[0, "lipid_a"] = np.nan
        if paired:
            frame["subject"] = [f"P{i:03d}" for i in range(12)] * 2
            cfg = config.model_copy(update={"design": "paired", "pair_column": "subject"})
        else:
            cfg = config
        frame.to_csv(directory / "values.csv", index=False)
        tools = ToolRegistry(RunState(cfg, directory, directory / "run"))
        if old:
            tools.state.run_info.pop("visualization_version")
            tools.state.write_json("run.json", tools.state.run_info)
        created.append(tools)
        coordinate(tools, descriptive=descriptive, figures=required or ["boxplot"])
        prepare_staged(tools, transform="log2")
        if not descriptive:
            compare_staged(tools, test="paired_t" if paired else "welch_t")
            assess(tools)
        return tools

    yield make
    for tools in created:
        tools.state.workflow.interrupt("End of figure verification.")


def plan(tools, figures):
    return call(tools, "set_figure_plan", {
        "rationale": "Show declared source features with persisted display data and no adaptive feature selection.",
        "figures": figures,
    })


def finish(tools):
    return call(tools, "finish_analysis", {
        "findings": [{"text": "The registered figure sources and display data are persisted.",
                      "evidence_ids": [tools.state.workflow.data.dataset_id]}],
        "limitations": ["The displayed sample does not establish population representativeness."],
    })


def plotted(tools, spec):
    result = call(tools, "make_plot", spec)
    refs = result["plot_artifact_ids"]
    state = tools.state
    config = json.loads(state.evidence.path(state.evidence.artifacts[refs["configuration"]].path).read_text())
    return result, state.read_artifact(refs["source"]), state.read_artifact(refs["display"]), config


def test_histogram_bins_and_exact_inputs_are_persisted_and_private(plots):
    tools = plots(required=["distributions"])
    spec = {"kind": "distributions", "source_id": "d001", "features": ["lipid_b"], "scale": "testing"}
    plan(tools, [spec])
    result, source, display, config = plotted(tools, spec)
    for representation, expected in [("original", tools.state.raw.lipid_b),
                                     ("testing", tools.state.datasets["d001"].testing.lipid_b)]:
        part = source[source.representation == representation]
        np.testing.assert_allclose(part.value, expected)
        counts, edges = np.histogram(expected, bins=18)
        bins = display[display.representation == representation]
        np.testing.assert_array_equal(bins["count"], counts)
        np.testing.assert_allclose(bins.bin_left, edges[:-1])
        np.testing.assert_allclose(bins.bin_right, edges[1:])
    assert result["labels"] == config["labels"]
    assert result["group_order"] == config["group_order"]
    assert config["methods"]["transform"] == "log2"
    assert config["selected_features"] == ["lipid_b"]
    for aid in result["plot_artifact_ids"].values():
        assert tools.state.evidence.artifacts[aid].individual_samples
        assert not tools.execute("read_result", {"artifact_id": aid})["ok"]
    context = json.dumps(call(tools, "get_stage_context"))
    assert not any(sid in context for sid in tools.state.raw.index)
    assert result["plot_artifact_ids"]["configuration"] in tools.state.workflow.current_artifacts()
    assert config["sample_order_ids"] == list(tools.state.metadata.index)
    assert set(tools.state.evidence.artifacts[result["artifact_id"]].parent_ids) == set(result["plot_artifact_ids"].values())
    tools.state.evidence.validate()


@pytest.mark.parametrize("centering,scaling", [("none", "none"), ("feature_mean", "none"),
                                               ("none", "feature_sd"), ("feature_mean", "feature_sd")])
def test_heatmap_display_scale_matches_reference_without_mutating_test_data(plots, centering, scaling):
    tools = plots(required=["heatmap"])
    spec = {"kind": "heatmap", "source_id": "d001", "features": ["lipid_b", "lipid_a"],
            "scale": "testing", "centering": centering, "scaling": scaling,
            "sample_order": "input", "selection_rationale": "Display both declared variables in the requested order without clustering."}
    dataset = tools.state.datasets["d001"]
    before = dataset.testing.copy(deep=True)
    plan(tools, [spec])
    result, source, display, config = plotted(tools, spec)
    matrix = display.pivot(index="sample_id", columns="feature", values="display_value").reindex(index=before.index, columns=spec["features"])
    expected = before[spec["features"]]
    if centering == "feature_mean":
        expected = expected - before[spec["features"]].mean()
    if scaling == "feature_sd":
        expected = expected / before[spec["features"]].std(ddof=1)
    np.testing.assert_allclose(matrix, expected, atol=1e-14)
    pd.testing.assert_frame_equal(dataset.testing, before)
    assert dataset.pca_values is None
    assert not any("pca" in artifact.kind for artifact in tools.state.evidence.artifacts.values())
    assert config["selected_features"] == ["lipid_b", "lipid_a"]
    assert "No clustering" in " ".join(result["display_notes"])
    assess(tools)
    assert tools.state.workflow.stage == STAGES[4]
    finish(tools)
    report = (tools.state.output_dir / "report.md").read_text()
    assert "Registered scope: **descriptive**" in report
    assert "omitted by the registered descriptive objective" in report
    assert "## PCA" not in report and "## Stage issues" in report
    assert tools.state.artifacts[result["artifact_id"]]["path"] in report


def test_paired_boxplots_keep_pair_ids_local_and_connect_complete_pairs(plots):
    tools = plots(paired=True, missing=True)
    spec = {"kind": "boxplot", "source_id": "d001", "features": ["lipid_a"], "scale": "testing"}
    plan(tools, [spec])
    result, source, display, config = plotted(tools, spec)
    assert result["paired_lines"] == 11 and result["omitted_values"] == 1
    assert source.pair_id.nunique() == 12 and len(display) == 23
    assert config["group_order"] == ["N", "T"]
    assert any(issue.code == "FIGURE_OMISSIONS" for issue in tools.state.workflow.issues())
    serialized = json.dumps(call(tools, "get_stage_context"))
    assert not any(pair in serialized for pair in tools.state.metadata.subject)
    np.testing.assert_allclose(display.value, source.loc[source.value.notna(), "value"])


def test_volcano_persists_zero_clipping_and_omitted_points_without_changing_results(plots):
    tools = plots(descriptive=False, required=["volcano"])
    state = tools.state
    comparison = state.comparisons["c001"]
    original = state.read_artifact(comparison["artifact_id"])
    custom = original.copy()
    custom.loc[0, "adjusted_p_value"] = 0.0
    custom.loc[1, "log2_fold_change"] = np.nan
    comparison["artifact_id"] = state.table(custom, "comparison", {"parent_ids": [comparison["artifact_id"]]})
    path = state.output_dir / state.artifacts[comparison["artifact_id"]]["path"]
    before = path.read_bytes()
    spec = {"kind": "volcano", "source_id": "c001"}
    plan(tools, [spec])
    result, source, display, config = plotted(tools, spec)
    assert result["clipped_zero_p"] == result["omitted_values"] == 1
    assert len(source) == 2 and len(display) == 1
    assert display.adjusted_p_value.iloc[0] == 0
    assert display.display_adjusted_p.iloc[0] == np.finfo(float).tiny
    assert display.minus_log10_adjusted_p.iloc[0] == pytest.approx(-np.log10(np.finfo(float).tiny))
    assert config["methods"]["correction"] == "fdr_bh"
    assert path.read_bytes() == before
    assess(tools)
    context = call(tools, "get_stage_context")
    assert context["incoming_handoff"]["figures"][0]["clipped_zero_p"] == 1
    assert any(issue["code"] == "VOLCANO_ZERO_P_CLIPPED" for issue in context["issues"])


def test_failed_heatmap_keeps_sources_and_blocks_required_coverage(plots):
    tools = plots(missing=True, required=["heatmap"])
    spec = {"kind": "heatmap", "source_id": "d001", "features": ["lipid_a"]}
    plan(tools, [spec])
    result = tools.execute("make_plot", spec)
    assert not result["ok"] and "complete selected features" in result["error"]
    assert not tools.state.workflow.data.figure_ids
    source = [a for a in tools.state.artifacts.values() if a["kind"] == "plot_source"]
    assert len(source) == 1 and tools.state.read_artifact(source[0]["id"]).value.isna().sum() == 1
    failures = [a for a in tools.state.evidence.artifacts.values() if a.kind == "plot_failure"]
    assert len(failures) == 1
    assert json.loads(tools.state.evidence.path(failures[0].path).read_text())["status"] == "failed"
    assert not tools.execute("submit_stage_assessment", {"summary": "The required heatmap failed."})["ok"]
    assert tools.state.workflow.stage == STAGES[3]
    tools.state.evidence.validate()


def test_rendering_failure_keeps_prepared_display_and_upstream_results(plots, monkeypatch):
    from metabolomics_agent import visualization
    tools = plots(descriptive=False, required=["boxplot"])
    comparison = tools.state.comparisons["c001"].copy()
    spec = {"kind": "boxplot", "source_id": "d001", "features": ["lipid_a"]}
    plan(tools, [spec])
    def fail(*args):
        raise ValueError("Controlled rendering failure")
    monkeypatch.setattr(visualization, "_render", fail)
    assert not tools.execute("make_plot", spec)["ok"]
    assert tools.state.comparisons["c001"] == comparison
    assert any(a["kind"] == "plot_data" for a in tools.state.artifacts.values())
    assert any(a.kind == "plot_failure" for a in tools.state.evidence.artifacts.values())
    assert not tools.state.workflow.validate()["valid"]
    assert not plt.get_fignums()


def test_complete_figure_spec_and_current_sources_are_enforced(plots):
    tools = plots(required=["boxplot"])
    spec = {"kind": "boxplot", "source_id": "d001", "features": ["lipid_a"], "scale": "concentrations"}
    plan(tools, [spec])
    assert not tools.execute("make_plot", {**spec, "scale": "testing"})["ok"]
    assert not tools.execute("make_plot", {**spec, "source_id": "raw"})["ok"]
    assert not tools.execute("make_plot", {**spec, "kind": "pca"})["ok"]
    assert not tools.execute("make_plot", {**spec, "features": ["lipid_a", "lipid_a"]})["ok"]
    assert not tools.execute("make_plot", {**spec, "centering": "feature_mean"})["ok"]
    result, *_ = plotted(tools, spec)
    summary = tools.state.read_artifact(result["plot_artifact_ids"]["summary"]).set_index("group").loc["N"]
    assert summary.q1 == pytest.approx(3.75) and summary.med == pytest.approx(6.5)
    assert summary.q3 == pytest.approx(9.25) and summary.whislo == 1 and summary.whishi == 12
    assert tools.state.workflow.validate()["valid"]


def test_restoration_retains_figure_sources_and_versioned_contract(plots):
    tools = plots()
    spec = {"kind": "boxplot", "source_id": "d001", "features": ["lipid_a"], "scale": "testing"}
    plan(tools, [spec])
    result, *_ = plotted(tools, spec)
    state = tools.state
    state.workflow.interrupt("Restore plotted evidence.")
    restored = RunState.restore(state.output_dir)
    assert restored.run_info["visualization_version"] == 3
    assert restored.workflow.figure_descriptors()[0]["plot_artifact_ids"] == result["plot_artifact_ids"]
    assert restored.workflow.data.figure_ids == [result["artifact_id"]]
    assert restored.figure_plans["f001"].figures[0].scale == "testing"
    restored.workflow.activate()
    assess(ToolRegistry(restored))
    finish(ToolRegistry(restored))
    assert restored.finished


def test_pre_F_runs_keep_previous_schema_and_plotting(plots):
    tools = plots(old=True)
    spec = {"kind": "boxplot", "source_id": "d001", "features": ["lipid_a"]}
    plan(tools, [spec])
    result = call(tools, "make_plot", spec)
    assert "plot_artifact_ids" not in result
    tools.state.workflow.interrupt("Restore old plot contract.")
    restored = RunState.restore(tools.state.output_dir)
    schemas = ToolRegistry(restored).schemas()
    schema = next(s["function"]["parameters"] for s in schemas if s["function"]["name"] == "make_plot")
    assert "scale" not in schema["properties"]
    assert "heatmap" not in schema["properties"]["kind"]["enum"]
    assert not restored.run_info.get("visualization_version")


def test_report_includes_inherited_issues_assumptions_and_current_figure_links(plots):
    tools = plots(required=["boxplot"])
    spec = {"kind": "boxplot", "source_id": "d001", "features": ["lipid_b"]}
    plan(tools, [spec])
    result, *_ = plotted(tools, spec)
    tools.state.evidence.active.issues.append(StageIssue(code="DISPLAY_CAVEAT", severity="warning",
        source="computed_check", message="Illustrative feature selection is not independent confirmation.",
        evidence_ids=[result["artifact_id"]]))
    call(tools, "submit_stage_assessment", {"summary": "The displayed values are saved with their provenance.",
        "unchecked_assumptions": ["Population representativeness was not assessed."],
        "limitations": ["Only two groups were selected."]})
    finish(tools)
    report = (tools.state.output_dir / "report.md").read_text()
    assert "DISPLAY_CAVEAT" in report and "Population representativeness was not assessed." in report
    assert "Only two groups were selected." in report
    for aid in result["plot_artifact_ids"].values():
        assert tools.state.evidence.artifacts[aid].path in report
    assert "## PCA" not in report


def test_preparation_diagnostic_figures_are_inherited_without_sample_rows(independent_study):
    config, root, _ = independent_study
    tools = ToolRegistry(RunState(config, root, root / "diagnostic"))
    coordinate(tools, descriptive=True, figures=[])
    call(tools, "inspect_dataset")
    result = call(tools, "make_plot", {"kind": "boxplot", "source_id": "raw", "features": ["lipid_a"]})
    assert tools.state.workflow.data.diagnostic_figure_ids == [result["artifact_id"]]
    assert not tools.execute("make_plot", {"kind": "heatmap", "source_id": "raw", "features": ["lipid_a"]})["ok"]
    prepare_staged(tools)
    context = call(tools, "get_stage_context")
    assert context["figures"][0]["artifact_id"] == result["artifact_id"]
    assert not any(sid in json.dumps(context) for sid in tools.state.raw.index)
    finish(tools)
    report = (tools.state.output_dir / "report.md").read_text()
    assert tools.state.artifacts[result["artifact_id"]]["path"] in report
    assert "Visualization was omitted" in report


def test_partial_report_links_evidence_without_claiming_completion(plots):
    from metabolomics_agent.report import write_partial_report
    tools = plots(missing=True, required=["heatmap"])
    spec = {"kind": "heatmap", "source_id": "d001", "features": ["lipid_a"]}
    plan(tools, [spec])
    assert not tools.execute("make_plot", spec)["ok"]
    tools.state.status = "needs_input"
    tools.state.workflow.interrupt("A required plot cannot be completed.", status="blocked")
    write_partial_report(tools.state)
    report = (tools.state.output_dir / "incomplete.md").read_text()
    assert "Analysis did not finish" in report and "heatmap" in report
    assert "blocked" in report and "FIGURE_FAILED" in report
    assert "plot_source" in report
    assert not tools.state.finished and not (tools.state.output_dir / "report.md").exists()
    first = {a.path for a in tools.state.evidence.artifacts.values() if a.kind == "partial_report"}
    write_partial_report(tools.state)
    second = {a.path for a in tools.state.evidence.artifacts.values() if a.kind == "partial_report"}
    assert len(first) == 1 and len(second) == 2 and first < second
    tools.state.evidence.validate()


def test_visualization_revision_preserves_old_files_but_requires_new_spec(plots):
    tools = plots()
    spec = {"kind": "boxplot", "source_id": "d001", "features": ["lipid_a"]}
    plan(tools, [spec])
    first, *_ = plotted(tools, spec)
    state = tools.state
    image = state.output_dir / state.artifacts[first["artifact_id"]]["path"]
    saved = image.read_bytes()
    assess(tools)
    call(tools, "request_revision", {"target": STAGES[3],
        "rationale": "Present the registered test scale and preserve the earlier concentration figure as history."})
    newer_spec = {**spec, "scale": "testing"}
    plan(tools, [newer_spec])
    assert not state.workflow.validate()["valid"]
    assert first["artifact_id"] not in state.workflow.current_artifacts()
    second, *_ = plotted(tools, newer_spec)
    assert state.workflow.validate()["valid"]
    assert image.read_bytes() == saved and second["artifact_id"] != first["artifact_id"]
    assess(tools)
    finish(tools)
    report = (state.output_dir / "report.md").read_text()
    assert image.name not in report
    assert state.artifacts[second["artifact_id"]]["path"] in report
