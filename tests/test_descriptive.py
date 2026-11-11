"""Numerical references, provenance, undefined values, and workflow integration."""

import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from conftest import call
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry
from test_workflow import assess, coordinate


@pytest.fixture
def make_tools(independent_study):
    config, root, original = independent_study
    created = []

    def make(frame=None):
        directory = root / f"case-{len(created)}"
        directory.mkdir()
        frame = original if frame is None else frame
        frame.to_csv(directory / "values.csv", index=False)
        tools = ToolRegistry(RunState(config, directory, directory / "run"))
        created.append(tools)
        coordinate(tools)
        call(tools, "inspect_dataset")
        return tools

    yield make
    for tools in created:
        tools.state.workflow.interrupt("End of descriptive test.")


def summary(tools, **options):
    result = call(tools, "summarize_dataset", options)
    return result, tools.state.read_artifact(result["artifacts"]["statistics"]), tools.state.read_artifact(
        result["artifacts"]["undefined_reasons"])


def prepared(tools, **options):
    summary(tools)
    plan = call(tools, "set_preprocessing_plan", {
        "rationale": "Prepare the quantified observations with an explicit provenance policy.", **options,
    })
    return call(tools, "preprocess", {"plan_id": plan["plan_id"]})


def test_descriptives_match_hand_calculated_and_library_references(make_tools):
    tools = make_tools()
    result, frame, reasons = summary(tools)
    row = frame.set_index(["group", "feature"]).loc[("N", "lipid_a")]
    expected = {"mean": 6.5, "median": 6.5, "variance": 13.0, "sd": np.sqrt(13),
                "min": 1, "max": 12, "range": 11, "q1": 3.75, "q3": 9.25, "iqr": 5.5}
    for name, value in expected.items():
        assert row[name] == pytest.approx(value)
    assert row["cv"] == pytest.approx(np.sqrt(13) / 6.5)
    assert row.n_total == row.n_observed_input == row.n_used == 12
    assert row.n_imputed_used == row.n_imputed_source == row.n_missing_source == 0
    values = tools.state.raw.loc[tools.state.metadata.group == "N", "lipid_b"].to_numpy()
    other = frame.set_index(["group", "feature"]).loc[("N", "lipid_b")]
    assert other.skewness == pytest.approx(stats.skew(values, bias=False))
    assert other["kurtosis"] == pytest.approx(stats.kurtosis(values, fisher=True, bias=False))
    assert other.variance == pytest.approx(np.var(values, ddof=1))
    for percent in range(10, 100, 10):
        assert row[f"p{percent}"] == pytest.approx(np.quantile(np.arange(1, 13), percent / 100, method="linear"))
    assert result["rows"] == 6 and result["all_features"]
    assert not ((reasons.feature == "lipid_a") & (reasons.group == "N")).any()
    assert result["conventions"]["variance_ddof"] == 1
    metadata = json.loads((tools.state.output_dir / f"{result['summary_id']}_summary.json").read_text())
    assert metadata["conventions"] == result["conventions"]


def test_overall_grouping_and_feature_subset_are_explicit(make_tools):
    tools = make_tools()
    result, frame, _ = summary(tools, grouping="overall", features=["lipid_a"])
    assert not result["all_features"] and result["rows"] == 1
    assert frame.iloc[0]["group"] == "all_selected_samples"
    assert frame.iloc[0].n_total == 24
    assert frame.iloc[0]["mean"] == pytest.approx(9.0)


def test_undefined_values_have_reasons_instead_of_fabricated_zeros(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "empty": [np.nan] * 6 + [1] * 6,
        "single": [np.nan] * 5 + [2] + list(range(1, 7)),
        "two": [np.nan] * 4 + [1, 2] + list(range(1, 7)),
        "three": [np.nan] * 3 + [1, 2, 4] + list(range(1, 7)),
        "zeros": [0] * 12, "constant": [7] * 12,
    })
    tools = make_tools(frame)
    result, table, reasons = summary(tools)
    rows = table.set_index(["group", "feature"])
    empty = rows.loc[("N", "empty")]
    assert empty.n_used == 0 and empty.n_missing_input == 6
    assert pd.isna(empty["mean"]) and pd.isna(empty["sd"])
    assert set(reasons.loc[(reasons.group == "N") & (reasons.feature == "empty"), "reason"]) == {"no_observations"}
    single = rows.loc[("N", "single")]
    assert single["mean"] == 2 and pd.isna(single.variance) and pd.isna(single["cv"])
    assert rows.loc[("N", "two"), "variance"] == pytest.approx(.5)
    assert pd.isna(rows.loc[("N", "two"), "skewness"])
    assert pd.isna(rows.loc[("N", "three"), "kurtosis"])
    zeros = rows.loc[("N", "zeros")]
    assert zeros["mean"] == zeros["sd"] == zeros["variance"] == 0
    assert zeros.n_zero_input == zeros.n_observed_input == 6
    assert pd.isna(zeros["cv"])
    assert ((reasons.feature == "zeros") & (reasons.statistic == "cv") & (reasons.reason == "zero_mean")).any()
    assert rows.loc[("N", "constant"), "cv"] == 0
    assert pd.isna(rows.loc[("N", "constant"), "skewness"])
    view = call(tools, "read_result", {"artifact_id": result["artifacts"]["statistics"], "features": ["empty"]})
    assert view["rows"][0]["mean"] is None
    assert result["undefined_counts"]["no_observations"] > 0


def test_optional_mode_handles_unique_values_and_ties(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "unique": list(range(1, 7)) * 2,
        "repeated": [1, 1, 1, 2, 3, 4] * 2,
        "tied": [1, 1, 2, 2, 3, 4] * 2,
    })
    tools = make_tools(frame)
    _, ordinary, _ = summary(tools)
    assert "mode" not in ordinary
    _, table, reasons = summary(tools, include_mode=True)
    rows = table.set_index(["group", "feature"])
    assert rows.loc[("N", "repeated"), "mode"] == 1
    assert rows.loc[("N", "repeated"), "mode_frequency"] == 3
    assert rows.loc[("N", "tied"), "mode_n_ties"] == 2
    assert pd.isna(rows.loc[("N", "tied"), "mode"])
    assert pd.isna(rows.loc[("N", "unique"), "mode"])
    assert set(reasons.reason) == {"multiple_modes", "no_repeated_values"}


def test_observed_and_imputed_counts_survive_normalization_log_and_restore(make_tools, independent_study):
    _, _, frame = independent_study
    frame = frame.copy()
    frame.loc[0, "lipid_a"] = 0
    frame.loc[[1, 12], "lipid_a"] = np.nan
    tools = make_tools(frame)
    original, raw_table, _ = summary(tools)
    raw = raw_table.set_index(["group", "feature"]).loc[("N", "lipid_a")]
    assert raw.n_observed_input == raw.n_used == 11 and raw.n_zero_input == 1
    plan = call(tools, "set_preprocessing_plan", {
        "rationale": "Treat explicit zeros as missing, impute, normalize, and inspect log-scale provenance.",
        "zero_as_missing": True, "imputation": "median", "normalization": "median", "transform": "log2",
    })
    output = call(tools, "preprocess", {"plan_id": plan["plan_id"]})
    did = output["dataset_id"]
    included, complete, _ = summary(tools, dataset_id=did, scale="concentrations", value_policy="including_imputed")
    observed, subset, _ = summary(tools, dataset_id=did, scale="concentrations", value_policy="observed_only")
    testing, log_table, reasons = summary(tools, dataset_id=did, scale="testing", value_policy="including_imputed")
    row = complete.set_index(["group", "feature"]).loc[("N", "lipid_a")]
    partial = subset.set_index(["group", "feature"]).loc[("N", "lipid_a")]
    assert row.n_observed_input == 10 and row.n_missing_input == 2
    assert row.n_original_missing == row.n_zero_input == 1
    assert row.n_imputed_source == row.n_imputed_used == 2
    assert row.n_used == row.n_available_source == 12
    assert row.n_missing_source == 0
    assert partial.n_used == 10 and partial.n_imputed_used == 0 and partial.n_excluded_imputed == 2
    dataset = tools.state.datasets[did]
    selected = tools.state.metadata.group == "N"
    values = dataset.concentrations.loc[selected, "lipid_a"]
    mask = dataset.imputation_mask.loc[selected, "lipid_a"]
    assert row["mean"] == pytest.approx(values.mean())
    assert partial["mean"] == pytest.approx(values.loc[~mask].mean())
    log = log_table.set_index(["group", "feature"]).loc[("N", "lipid_a")]
    assert log["mean"] == pytest.approx(np.log2(values).mean())
    assert pd.isna(log["cv"]) and set(reasons.reason) == {"not_ratio_scale"}
    assert not tools.execute("read_result", {"artifact_id": output["artifacts"]["imputation_mask"]})["ok"]
    tools.state.workflow.interrupt("Save confirmed summaries before restoration.")
    (tools.state.output_dir.parent / "values.csv").unlink()
    restored = RunState.restore(tools.state.output_dir)
    pd.testing.assert_frame_equal(restored.datasets[did].imputation_mask, dataset.imputation_mask)
    assert restored.descriptive_summaries[included["summary_id"]] == tools.state.descriptive_summaries[included["summary_id"]]
    assert {original["summary_id"], included["summary_id"], observed["summary_id"], testing["summary_id"]} <= set(restored.workflow.data.summary_ids)


@pytest.mark.parametrize("arguments", [
    {"scale": "testing"}, {"dataset_id": "d999", "scale": "concentrations"},
    {"features": ["age"]}, {"features": ["lipid_a", "lipid_a"]},
    {"grouping": "batch"}, {"value_policy": "guess_missingness"},
])
def test_invalid_sources_features_and_policies_are_rejected(make_tools, arguments):
    tools = make_tools()
    assert not tools.execute("summarize_dataset", arguments)["ok"]
    assert not tools.state.descriptive_summaries


def test_preparation_requires_full_summaries_with_correct_scope(make_tools):
    tools = make_tools()
    plan = {"rationale": "Use explicit original and prepared summaries before advancing."}
    assert not tools.execute("set_preprocessing_plan", plan)["ok"]
    summary(tools, features=["lipid_a"])
    summary(tools, grouping="overall")
    assert not tools.execute("set_preprocessing_plan", plan)["ok"]
    summary(tools)
    pid = call(tools, "set_preprocessing_plan", plan)["plan_id"]
    output = call(tools, "preprocess", {"plan_id": pid})
    assert not tools.execute("submit_stage_assessment", {"summary": "Missing prepared summaries."})["ok"]
    summary(tools, dataset_id=output["dataset_id"], scale="concentrations", value_policy="observed_only")
    assert not tools.execute("submit_stage_assessment", {"summary": "The inclusion policy is still incomplete."})["ok"]
    for scale in ["concentrations", "testing"]:
        summary(tools, dataset_id=output["dataset_id"], scale=scale, value_policy="including_imputed")
    assess(tools)
    context = call(tools, "get_stage_context")
    assert context["stage"] == "03_univariate"
    assert context["incoming_handoff"]["descriptive_summaries"]
    serialized = json.dumps(context)
    assert not any(sample in serialized for sample in tools.state.raw.index)
    table = next(iter(tools.state.descriptive_summaries.values()))["artifacts"]["statistics"]
    call(tools, "request_revision", {"target": "02_preparation", "rationale": "Review the preparation assumptions without overwriting historical summaries."})
    assert tools.state.workflow.data.summary_ids == []
    assert tools.state.descriptive_summaries
    assert not tools.execute("read_result", {"artifact_id": table})["ok"]


def test_mask_tracks_only_final_features_after_normalization(make_tools):
    frame = pd.DataFrame({
        "sample_id": [f"{i:03d}" for i in range(12)], "group": ["N"] * 6 + ["T"] * 6,
        "a": np.arange(1, 13, dtype=float), "b": np.arange(1, 13, dtype=float) * 2,
        "c": np.arange(1, 13, dtype=float) * 3 + np.array([1, 0, 2, 1, 3, 2, 1, 4, 2, 0, 3, 1]),
    })
    tools = make_tools(frame)
    output = prepared(tools, normalization="median")
    dataset = tools.state.datasets[output["dataset_id"]]
    assert dataset.testing.columns.tolist() == ["c"]
    assert dataset.imputation_mask.columns.equals(dataset.testing.columns)
    assert not dataset.imputation_mask.any().any()
    assert dataset.imputation_mask.shape == dataset.testing.shape
    assert tools.state.read_artifact(output["artifacts"]["imputation_mask"]).columns.tolist() == ["sample_id", *dataset.testing.columns]


def test_pre_d_runs_keep_their_capabilities_and_frozen_prompts(make_tools):
    tools = make_tools()
    state = tools.state
    state.run_info.pop("descriptive_version")
    state.run_info.pop("univariate_version")
    state.run_info["workflow_prompts"]["02_preparation"] = "Frozen preparation instructions from an earlier run."
    state.write_json("run.json", state.run_info)
    assert "summarize_dataset" not in {schema["function"]["name"] for schema in tools.schemas()}
    assert not tools.execute("summarize_dataset", {})["ok"]
    pid = call(tools, "set_preprocessing_plan", {"rationale": "Preserve the earlier preparation contract without new requirements."})["plan_id"]
    output = call(tools, "preprocess", {"plan_id": pid})
    assert "imputation_mask" not in output["artifacts"]
    assess(tools)
    restored = RunState.restore(state.output_dir)
    assert restored.workflow.bundle["02_preparation"] == "Frozen preparation instructions from an earlier run."
    assert not restored.run_info.get("descriptive_version")
    assert restored.datasets[output["dataset_id"]].imputation_mask is None
    assert restored.workflow.stage == "03_univariate"
