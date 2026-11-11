"""Audited inference against SciPy/statsmodels and failure/provenance scenarios."""

import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.multitest import multipletests

from conftest import call
from metabolomics_agent.models import StudyConfig
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry
from test_workflow import coordinate, prepare_staged, assess


@pytest.fixture
def make_tools(tmp_path):
    made = []

    def make(frame=None, *, paired=False, figures=None, **preparation):
        root = tmp_path / str(len(made))
        root.mkdir()
        if frame is None:
            frame = pd.DataFrame({
                "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
                "a": [1, 2, 5, 4, 7, 8, 5, 3, 15, 4, 8, 9],
                "b": [2, 4, 3, 7, 6, 10, 4, 7, 9, 2, 8, 11],
                "subject": ["a", "b", "c", "d", "e", "f", "c", "a", "f", "b", "e", "d"],
            })
            if not paired:
                frame = frame.drop(columns="subject")
        frame.to_csv(root / "values.csv", index=False)
        config = StudyConfig(data_path="values.csv", reference_group="N", treatment_group="T",
                             design="paired" if paired else "independent",
                             pair_column="subject" if paired else None)
        tools = ToolRegistry(RunState(config, root, root / "run"))
        made.append(tools)
        coordinate(tools, figures=figures)
        prepare_staged(tools, **preparation)
        return tools

    yield make
    for tools in made:
        tools.state.workflow.interrupt("End of univariate verification.")


def execute(tools, *, test="welch_t", correction="fdr_bh", alpha=.05):
    did = tools.state.workflow.data.dataset_id
    diagnostic = call(tools, "inspect_univariate", {"dataset_id": did})
    plan = call(tools, "set_univariate_plan", {
        "dataset_id": did, "diagnostic_id": diagnostic["diagnostic_id"],
        "rationale": "Use the recorded design and observed-data eligibility for a prespecified comparison.",
        "test": test, "correction": correction, "alpha": alpha,
    })
    result = tools.execute("compare_groups", {"dataset_id": did, "plan_id": plan["plan_id"]})
    return diagnostic, plan, result


@pytest.mark.parametrize("test", ["welch_t", "student_t", "mannwhitney", "paired_t", "wilcoxon"])
def test_all_supported_tests_effects_intervals_and_counts_match_references(make_tools, test):
    paired = test in {"paired_t", "wilcoxon"}
    tools = make_tools(paired=paired)
    diagnostic, plan, result = execute(tools, test=test, alpha=.1)
    assert result["ok"], result
    comparison = result["result"]
    table = tools.state.read_artifact(comparison["artifact_id"]).set_index("feature")
    audit = tools.state.read_artifact(comparison["audit_artifacts"]["audit"]).set_index("feature")
    dataset = tools.state.datasets["d001"]
    expected_ps = []
    for feature in dataset.testing:
        meta = tools.state.metadata
        if paired:
            joined = meta.copy()
            joined["value"] = dataset.testing[feature]
            wide = joined.pivot(index="subject", columns="group", values="value")
            ref, treat = wide["N"], wide["T"]
        else:
            ref, treat = dataset.testing.loc[meta.group == "N", feature], dataset.testing.loc[meta.group == "T", feature]
        if test in {"welch_t", "student_t"}:
            expected = stats.ttest_ind(treat, ref, equal_var=test == "student_t")
            df = len(treat) + len(ref) - 2
            pooled = np.sqrt(((len(treat) - 1) * treat.var(ddof=1) + (len(ref) - 1) * ref.var(ddof=1)) / df)
            g = (1 - 3 / (4 * df - 1)) * (treat.mean() - ref.mean()) / pooled
            assert table.loc[feature, "hedges_g"] == pytest.approx(g)
        elif test == "mannwhitney":
            expected = stats.mannwhitneyu(treat, ref, alternative="two-sided", method="auto", use_continuity=True)
            assert table.loc[feature, "rank_biserial"] == pytest.approx(2 * expected.statistic / (len(ref) * len(treat)) - 1)
        elif test == "paired_t":
            expected = stats.ttest_rel(treat, ref)
        else:
            expected = stats.wilcoxon(treat, ref, alternative="two-sided", zero_method="wilcox", correction=False, method="auto")
        assert table.loc[feature, "p_value"] == pytest.approx(expected.pvalue)
        assert table.loc[feature, "statistic"] == pytest.approx(expected.statistic)
        if test in {"welch_t", "student_t", "paired_t"}:
            interval = expected.confidence_interval(confidence_level=.9)
            assert table.loc[feature, "ci_low_test_scale"] == pytest.approx(interval.low)
            assert table.loc[feature, "ci_high_test_scale"] == pytest.approx(interval.high)
            assert audit.loc[feature, "df"] == pytest.approx(expected.df)
        assert audit.loc[feature, "n_imputed_used_reference"] == 0
        assert audit.loc[feature, "n_observed_used_reference"] == 6
        if paired:
            assert audit.loc[feature, "n_observed_pairs"] == audit.loc[feature, "n_pairs_used"] == 6
        expected_ps.append(expected.pvalue)
    adjusted = multipletests(expected_ps, alpha=.1, method="fdr_bh")[1]
    for feature, expected in zip(dataset.testing, adjusted):
        assert table.loc[feature, "adjusted_p_value"] == pytest.approx(expected)
    assert plan["plan"]["alternative"] == "two-sided"
    assert diagnostic["features_eligible"] == 2


@pytest.mark.parametrize("correction", ["fdr_bh", "holm-sidak", "holm", "bonferroni"])
def test_correction_family_excludes_ineligible_imputed_feature(make_tools, correction):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "good": [1, 2, 3, 5, 4, 8, 2, 4, 5, 6, 8, 10],
        "other": [2, 3, 4, 6, 5, 8, 3, 2, 5, 4, 8, 9],
        "sparse": [1, 2, np.nan, np.nan, np.nan, np.nan, 4, 5, 6, 7, 8, 9],
    })
    tools = make_tools(frame, imputation="median", max_missing_fraction=.5)
    diagnostic, _, result = execute(tools, correction=correction)
    assert result["ok"], result
    comparison = result["result"]
    table = tools.state.read_artifact(comparison["artifact_id"]).set_index("feature")
    assert table.loc["sparse", "n_reference"] == 6 and table.loc["sparse", "n_observed_reference"] == 2
    assert table.loc["sparse", "status"] == "insufficient_observed_data"
    assert pd.isna(table.loc["sparse", "p_value"])
    valid = table.loc[["good", "other"]]
    np.testing.assert_allclose(valid.adjusted_p_value, multipletests(valid.p_value, method=correction)[1])
    record = tools.state.univariate_attempts[comparison["univariate_attempt_id"]]
    assert set(record["family_members"]) == {"good", "other"}
    assert record["family_excluded"] == ["sparse"]
    assert diagnostic["features_ineligible"] == 1


def test_imputed_pairs_do_not_manufacture_observed_complete_pairs(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "subject": list("abcdef") * 2,
        "good": [1, 2, 3, 5, 4, 7, 3, 4, 3, 8, 5, 12],
        "incomplete": [np.nan, np.nan, 3, 4, 5, 6, 2, 3, np.nan, np.nan, 8, 10],
    })
    tools = make_tools(frame, paired=True, imputation="median", max_missing_fraction=.5)
    _, _, result = execute(tools, test="paired_t")
    assert result["ok"], result
    table = tools.state.read_artifact(result["result"]["artifact_id"]).set_index("feature")
    audit = tools.state.read_artifact(result["result"]["audit_artifacts"]["audit"]).set_index("feature")
    row = table.loc["incomplete"]
    assert row.n_observed_reference == row.n_observed_treatment == 4
    assert row.n_reference == row.n_treatment == 6
    assert row.n_observed_pairs == 2 and row.status == "insufficient_observed_pairs"
    assert pd.isna(row.p_value)
    assert audit.loc["incomplete", "n_imputed_pairs_used"] == 4
    assert audit.loc["incomplete", "n_observed_pairs_used"] == 2
    assert not audit.loc["incomplete", "test_executed"]
    assert audit.loc["incomplete", "n_test_reference"] == 0


def test_incomplete_pair_inference_and_fold_change_populations_are_explicit(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "subject": list("abcdef") * 2,
        "lipid": [np.nan, 2, 4, 6, 5, 8, 3, 4, 7, 6, 9, 10],
    })
    tools = make_tools(frame, paired=True)
    _, _, result = execute(tools, test="paired_t")
    assert result["ok"], result
    row = tools.state.read_artifact(result["result"]["artifact_id"]).iloc[0]
    expected = stats.ttest_rel([4, 7, 6, 9, 10], [2, 4, 6, 5, 8])
    assert row.n_reference == row.n_treatment == 5
    assert row.p_value == pytest.approx(expected.pvalue)
    assert row.log2_fold_change == pytest.approx(np.log2(np.mean([3, 4, 7, 6, 9, 10]) / np.mean([2, 4, 6, 5, 8])))
    issues = tools.state.read_artifact(result["result"]["audit_artifacts"]["issues"])
    assert "FOLD_CHANGE_POPULATION" in set(issues.code)


def test_no_valid_tests_preserve_evidence_and_cannot_complete_stage(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "sparse": [1, 2, np.nan, np.nan, np.nan, np.nan, 4, 5, 6, 7, 8, 9],
    })
    tools = make_tools(frame, imputation="median", max_missing_fraction=.5)
    _, _, result = execute(tools)
    assert not result["ok"] and not tools.state.comparisons
    record = next(iter(tools.state.univariate_attempts.values()))
    assert record["status"] == "failed" and record["failure_reason"] == "no_valid_tests"
    assert record["tests_executed"] == 0
    assert record["artifacts"]["raw_tests"] in tools.state.artifacts
    assert "comparison" not in record["artifacts"]
    assert not tools.execute("submit_stage_assessment", {"summary": "Cannot complete without a valid comparison."})["ok"]


def test_correction_failure_keeps_raw_tests_and_forces_exploratory_revision(make_tools, monkeypatch):
    from metabolomics_agent import univariate

    tools = make_tools()
    actual = univariate.multipletests

    def broken(*args, **kwargs):
        raise ValueError("Deliberately failed correction")

    monkeypatch.setattr(univariate, "multipletests", broken)
    _, plan, result = execute(tools)
    assert not result["ok"] and not tools.state.comparisons
    record = next(iter(tools.state.univariate_attempts.values()))
    raw = tools.state.read_artifact(record["artifacts"]["raw_tests"])
    assert raw.p_value.notna().all() and raw.adjusted_p_value.isna().all()
    assert record["tests_executed"] == 2 and not record["correction_completed"]
    assert not tools.execute("compare_groups", {"dataset_id": "d001", "plan_id": plan["plan_id"]})["ok"]
    call(tools, "request_revision", {"target": "03_univariate", "rationale": "Review a failed correction with preserved raw test evidence."})
    monkeypatch.setattr(univariate, "multipletests", actual)
    _, newer, retried = execute(tools)
    assert retried["ok"] and newer["plan"]["purpose"] == "exploratory"
    assert retried["result"]["purpose"] == "exploratory"


def test_degenerate_t_is_undefined_and_wilcoxon_zero_convention_is_audited(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "subject": list("abcdef") * 2,
        "zero_difference": [1, 2, 4, 3, 7, 8] * 2,
    })
    t_tools = make_tools(frame, paired=True)
    _, _, failed = execute(t_tools, test="paired_t")
    assert not failed["ok"] and not t_tools.state.comparisons
    record = next(iter(t_tools.state.univariate_attempts.values()))
    raw = t_tools.state.read_artifact(record["artifacts"]["raw_tests"])
    assert raw.status.tolist() == ["undefined_test"] and raw.p_value.isna().all()
    rank_tools = make_tools(frame, paired=True)
    _, _, result = execute(rank_tools, test="wilcoxon")
    assert result["ok"], result
    row = rank_tools.state.read_artifact(result["result"]["artifact_id"]).iloc[0]
    assert row.statistic == 0 and row.p_value == row.adjusted_p_value == 1
    issues = rank_tools.state.read_artifact(result["result"]["audit_artifacts"]["issues"])
    assert {"ZERO_PAIRED_DIFFERENCES", "LOW_NONZERO_PAIR_COUNT"} <= set(issues.code)


def test_diagnostics_required_and_input_drift_is_rejected(make_tools):
    tools = make_tools()
    assert not tools.execute("set_univariate_plan", {"dataset_id": "d001", "rationale": "No diagnostic ID was provided for this plan."})["ok"]
    diagnostic = call(tools, "inspect_univariate", {"dataset_id": "d001"})
    plan = call(tools, "set_univariate_plan", {
        "dataset_id": "d001", "diagnostic_id": diagnostic["diagnostic_id"],
        "rationale": "Use an inspected immutable dataset for the statistical comparison.",
    })
    tools.state.datasets["d001"].testing.iloc[0, 0] += 1
    assert not tools.execute("compare_groups", {"dataset_id": "d001", "plan_id": plan["plan_id"]})["ok"]
    assert not tools.state.comparisons and not tools.state.univariate_attempts


def test_infinite_statistic_is_not_published_as_zero_p_evidence(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "good": [1, 2, 4, 3, 5, 7, 2, 4, 5, 3, 8, 9],
        "constant_groups": [1] * 6 + [2] * 6,
    })
    tools = make_tools(frame)
    _, _, result = execute(tools)
    assert result["ok"], result
    table = tools.state.read_artifact(result["result"]["artifact_id"]).set_index("feature")
    assert table.loc["constant_groups", "status"] == "undefined_test"
    assert pd.isna(table.loc["constant_groups", "statistic"])
    assert pd.isna(table.loc["constant_groups", "p_value"])
    issues = tools.state.read_artifact(result["result"]["audit_artifacts"]["issues"])
    assert {"INVALID_TEST_RESULT", "SCIPY_WARNING"} <= set(issues.code)
    assert result["result"]["features_tested"] == 1


def test_pre_e_state_keeps_old_plan_schema_and_restores_counts(make_tools):
    tools = make_tools()
    state = tools.state
    state.run_info.pop("univariate_version")
    state.run_info["workflow_prompts"]["03_univariate"] = "Frozen earlier univariate instructions."
    state.write_json("run.json", state.run_info)
    assert "inspect_univariate" not in {schema["function"]["name"] for schema in tools.schemas()}
    plan = call(tools, "set_univariate_plan", {"dataset_id": "d001", "rationale": "Preserve the earlier statistical contract without a new preflight."})
    result = call(tools, "compare_groups", {"dataset_id": "d001", "plan_id": plan["plan_id"]})
    assert result["features_tested"] == 2
    assess(tools)
    restored = RunState.restore(state.output_dir)
    assert not restored.run_info.get("univariate_version")
    assert restored.workflow.bundle["03_univariate"] == "Frozen earlier univariate instructions."
    assert restored.comparisons["c001"]["features_tested"] == 2
    assert not restored.univariate_attempts


def test_completed_audit_restores_and_handoff_is_aggregate_only(make_tools):
    tools = make_tools()
    diagnostic, _, result = execute(tools)
    assert result["ok"], result
    assess(tools)
    (tools.state.output_dir.parent / "values.csv").unlink()
    restored = RunState.restore(tools.state.output_dir)
    assert restored.univariate_diagnostics[diagnostic["diagnostic_id"]] == tools.state.univariate_diagnostics[diagnostic["diagnostic_id"]]
    assert restored.univariate_attempts == tools.state.univariate_attempts
    context = call(ToolRegistry(restored), "get_stage_context")
    assert context["stage"] == "04_visualization"
    assert context["incoming_handoff"]["univariate_results"][0]["correction_completed"]
    assert not any(sample in json.dumps(context) for sample in restored.raw.index)
    restored.workflow.interrupt("End of restored audit test.")


def test_zero_discoveries_is_a_completed_comparison(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "unchanged": [1, 2, 3, 4, 6, 8] * 2,
    })
    tools = make_tools(frame)
    _, _, result = execute(tools)
    assert result["ok"] and result["result"]["significant_features"] == 0
    assert result["result"]["features_tested"] == 1
    assess(tools)
    assert tools.state.workflow.stage == "04_visualization"


def test_report_renders_skipped_features_and_links_the_audit(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "good": [1, 2, 4, 3, 6, 9, 2, 3, 5, 7, 6, 11],
        "sparse": [1, 2, np.nan, np.nan, np.nan, np.nan, 4, 5, 6, 7, 8, 9],
    })
    tools = make_tools(frame, figures=[], imputation="median", max_missing_fraction=.5)
    _, _, result = execute(tools)
    assert result["ok"], result
    assess(tools)
    assert tools.state.workflow.stage == "05_reporting"
    call(tools, "finish_analysis", {
        "findings": [{"text": "A valid comparison exists and an ineligible feature remains documented.", "evidence_ids": ["c001"]}],
        "limitations": ["Imputed values do not replace original observed evidence."],
    })
    report = (tools.state.output_dir / "report.md").read_text()
    assert "undefined | undefined" in report
    assert "## Univariate audit" in report
    assert "ua001_univariate.json" in report
