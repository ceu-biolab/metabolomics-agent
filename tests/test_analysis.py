import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.multitest import multipletests

from conftest import call, prepare
from metabolomics_agent.models import StudyConfig
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry


@pytest.mark.parametrize("test", ["welch_t", "student_t", "mannwhitney"])
def test_numerical_results_match_library_reference(tools, test):
    did = prepare(tools, test=test, correction="holm-sidak")
    result = call(tools, "compare_groups", {"dataset_id": did})
    actual = tools.state.read_artifact(result["artifact_id"]).set_index("feature")
    expected_ps = []
    for feature in tools.state.datasets[did].testing:
        raw = tools.state.raw[feature]
        groups = tools.state.metadata.group
        treat, ref = raw[groups == "T"], raw[groups == "N"]
        expected = (stats.mannwhitneyu(treat, ref) if test == "mannwhitney"
                    else stats.ttest_ind(treat, ref, equal_var=test == "student_t"))
        assert actual.loc[feature, "statistic"] == pytest.approx(expected.statistic)
        assert actual.loc[feature, "p_value"] == pytest.approx(expected.pvalue)
        assert actual.loc[feature, "log2_fold_change"] == pytest.approx(np.log2(treat.mean() / ref.mean()))
        expected_ps.append(expected.pvalue)
    expected_adjusted = multipletests(expected_ps, method="holm-sidak")[1]
    for feature, expected in zip(tools.state.datasets[did].testing, expected_adjusted):
        assert actual.loc[feature, "adjusted_p_value"] == pytest.approx(expected)
    assert "constant" not in actual.index


def test_separate_testing_and_pca_scales(tools):
    did = prepare(tools, transform="log2", pca_scaling="auto")
    dataset = tools.state.datasets[did]
    np.testing.assert_allclose(dataset.testing, np.log2(dataset.concentrations))
    np.testing.assert_allclose(dataset.pca_values.mean(), 0, atol=1e-14)
    np.testing.assert_allclose(dataset.pca_values.std(ddof=1), 1)
    result = call(tools, "compare_groups", {"dataset_id": did})
    table = tools.state.read_artifact(result["artifact_id"]).set_index("feature")
    raw = tools.state.raw.lipid_a
    groups = tools.state.metadata.group
    assert table.loc["lipid_a", "log2_fold_change"] == pytest.approx(np.log2(raw[groups == "T"].mean() / raw[groups == "N"].mean()))
    pca = call(tools, "run_pca", {"dataset_id": did})
    assert sum(pca["explained_variance_ratio"]) == pytest.approx(1)
    scores = tools.state.read_artifact(pca["scores_artifact"])
    loadings = tools.state.read_artifact(pca["loadings_artifact"])
    reconstructed = scores[["PC1", "PC2"]].to_numpy() @ loadings[["PC1", "PC2"]].to_numpy().T
    np.testing.assert_allclose(reconstructed, dataset.pca_values, atol=1e-12)


def test_revisions_are_exploratory_and_preserve_original(tools):
    first = prepare(tools)
    primary = call(tools, "compare_groups", {"dataset_id": first})
    original = tools.state.read_artifact(primary["artifact_id"])
    second = prepare(tools, test="mannwhitney", purpose="primary")
    exploratory = call(tools, "compare_groups", {"dataset_id": second})
    assert primary["purpose"] == "primary"
    assert exploratory["purpose"] == "exploratory"
    pd.testing.assert_frame_equal(original, tools.state.read_artifact(primary["artifact_id"]))
    assert not tools.execute("compare_groups", {"dataset_id": first})["ok"]


def test_preparing_plans_early_cannot_bypass_exploratory_rule(tools):
    first = prepare(tools)
    second = prepare(tools, test="mannwhitney")
    call(tools, "compare_groups", {"dataset_id": first})
    assert not tools.execute("compare_groups", {"dataset_id": second})["ok"]
    assert not tools.execute("preprocess", {"plan_id": "p001"})["ok"]


def test_knn_imputation_and_median_normalization_preserve_raw_inputs(tools):
    from sklearn.impute import KNNImputer

    tools.state.raw.iloc[0, 0] = np.nan
    raw_before = tools.state.raw.copy()
    did = prepare(tools, imputation="knn", normalization="median", pca_scaling="pareto")
    dataset = tools.state.datasets[did]
    expected = pd.DataFrame(KNNImputer(n_neighbors=5).fit_transform(raw_before[["lipid_a", "lipid_b"]]),
                            index=raw_before.index, columns=["lipid_a", "lipid_b"])
    medians = expected.median(axis=1)
    expected = expected.div(medians, axis=0) * medians.median()
    np.testing.assert_allclose(dataset.concentrations, expected)
    np.testing.assert_allclose(dataset.pca_values, (expected - expected.mean()) / np.sqrt(expected.std(ddof=1)))
    pd.testing.assert_frame_equal(tools.state.raw, raw_before)


def test_pca_refuses_missing_and_agent_can_revise(tools):
    tools.state.raw.iloc[0, 0] = np.nan
    did = prepare(tools, imputation="none")
    failed = tools.execute("run_pca", {"dataset_id": did})
    assert not failed["ok"] and "complete data" in failed["error"]
    revised = prepare(tools, imputation="median")
    call(tools, "run_pca", {"dataset_id": revised})
    assert tools.state.raw.iloc[0, 0] is np.nan or pd.isna(tools.state.raw.iloc[0, 0])


def test_log_zeros_are_not_silently_fixed(tools):
    tools.state.raw.iloc[0, 0] = 0
    call(tools, "inspect_dataset")
    plan = call(tools, "set_plan", {"rationale": "Log preprocessing to examine multiplicative differences.", "transform": "log2"})
    result = tools.execute("preprocess", {"plan_id": plan["plan_id"]})
    assert not result["ok"] and "strictly positive" in result["error"]
    did = prepare(tools, transform="log2", zero_as_missing=True, imputation="half_min")
    assert np.isfinite(tools.state.datasets[did].testing.to_numpy()).all()


def test_imputation_cannot_create_observed_evidence(tools):
    tools.state.raw.loc[tools.state.metadata.group == "T", "lipid_a"] = np.nan
    did = prepare(tools, imputation="median", max_missing_fraction=0.6)
    result = call(tools, "compare_groups", {"dataset_id": did})
    table = tools.state.read_artifact(result["artifact_id"]).set_index("feature")
    assert table.loc["lipid_a", "status"] == "insufficient_observed_data"
    assert pd.isna(table.loc["lipid_a", "p_value"])


@pytest.mark.parametrize("test", ["paired_t", "wilcoxon"])
def test_paired_analysis_aligns_subjects_not_row_order(tmp_path, test):
    table = pd.DataFrame({
        "sample_id": [f"S{i}" for i in range(10)], "group": ["N"] * 5 + ["T"] * 5,
        "subject": ["a", "b", "c", "d", "e", "c", "a", "e", "b", "d"],
        "lipid": [1, 5, 9, 12, 7, 11, 4, 10, 8, 17],
    })
    table.to_csv(tmp_path / "data.csv", index=False)
    cfg = StudyConfig(data_path="data.csv", reference_group="N", treatment_group="T",
                      design="paired", pair_column="subject")
    state = RunState(cfg, tmp_path, tmp_path / "run", protocol="legacy")
    registry = ToolRegistry(state)
    did = prepare(registry, test=test)
    result = call(registry, "compare_groups", {"dataset_id": did})
    actual = state.read_artifact(result["artifact_id"]).iloc[0]
    ref = np.array([1, 5, 9, 12, 7])
    treat = np.array([4, 8, 11, 17, 10])
    expected = stats.ttest_rel(treat, ref) if test == "paired_t" else stats.wilcoxon(treat, ref)
    assert actual.p_value == pytest.approx(expected.pvalue)
    assert actual.n_observed_pairs == 5
    assert not registry.execute("set_plan", {"rationale": "This test is inconsistent with declared pairing.",
                                             "test": "welch_t"})["ok"]


def test_tool_validation_and_paths(tools):
    assert not tools.execute("set_plan", {"rationale": "Plan without inspection is invalid."})["ok"]
    assert not tools.execute("inspect_dataset", {"unexpected": True})["ok"]
    assert not tools.execute("preprocess", "not-json")["ok"]
    assert not tools.execute("run_shell", {"command": "anything"})["ok"]
    assert not tools.execute("read_result", {"artifact_id": "../../.env"})["ok"]
    assert not tools.execute("read_result", {"artifact_id": "a001"})["ok"]
    call(tools, "inspect_dataset")
    records = [json.loads(line) for line in (tools.state.output_dir / "events.jsonl").read_text().splitlines()]
    assert any(r["kind"] == "tool_error" for r in records)


def test_finish_checks_completeness_and_evidence(tools):
    assert not tools.execute("finish_analysis", {
        "findings": [{"text": "Unsupported conclusion", "evidence_ids": ["fake"]}],
        "limitations": ["Incomplete"],
    })["ok"]


def test_raw_only_distribution_plot_does_not_satisfy_completion(tools):
    did = prepare(tools)
    comparison = call(tools, "compare_groups", {"dataset_id": did})
    pca = call(tools, "run_pca", {"dataset_id": did})
    for kind, source in [("distributions", "raw"), ("volcano", comparison["comparison_id"]),
                         ("pca", pca["pca_id"])]:
        call(tools, "make_plot", {"kind": kind, "source_id": source})
    result = tools.execute("finish_analysis", {
        "findings": [{"text": "Comparison results are saved.", "evidence_ids": ["c001"]}],
        "limitations": ["Synthetic study."],
    })
    assert not result["ok"]
    assert "before and after" in result["error"]
