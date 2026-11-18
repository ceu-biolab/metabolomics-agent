"""Contracts, failure sensitivity and real offline execution of generic cases."""

import json

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from metabolomics_agent.case_evaluation import evaluate_case, load_case, safe_path
from metabolomics_agent.evaluation_examples import make_evaluation_example
from metabolomics_agent.evaluation_metrics import aggregate, numeric_comparison, result, set_comparison, table_comparison
from metabolomics_agent.evaluation_models import MetricSpec, Observation, StageExpectation, Tolerance


def metric(**kwargs):
    return MetricSpec(id="test.metric", validator="exact", observation=Observation(),
                      provenance="Independent test expectation.", **kwargs)


def test_null_is_distinct_from_missing_expectation():
    missing, null = metric(), metric(expected=None)
    assert not MetricSpec.model_validate(missing.model_dump()).expected_is_set
    assert MetricSpec.model_validate(null.model_dump()).expected_is_set


@pytest.mark.parametrize("actual,expected", [([0.], [1e-19]), ([1., np.nan], [1., 0.]), ([1.], [1., 2.])])
def test_numeric_rejects_small_p_masks_and_shape(actual, expected):
    assert not numeric_comparison(actual, expected, Tolerance())[0]


def test_numeric_masks_and_zero_denominators():
    passed, details, count = numeric_comparison([0, np.nan], [0, np.nan], Tolerance())
    assert passed and count == 2 and details["max_relative_error"] is None


def test_table_aligns_by_identity_and_detects_wrong_labels():
    reference = pd.DataFrame({"feature": ["a", "b"], "p": [1e-19, .5]})
    assert table_comparison(reference.iloc[::-1], reference, ["feature"], [], Tolerance())[0]
    corrupted = reference.copy()
    corrupted.loc[0, "p"] = 0
    assert not table_comparison(corrupted, reference, ["feature"], [], Tolerance())[0]
    corrupted = reference.copy()
    corrupted["feature"] = ["b", "a"]
    assert not table_comparison(corrupted, reference, ["feature"], [], Tolerance())[0]


def test_empty_sets_have_no_fake_precision():
    passed, details, _ = set_comparison([], [])
    assert passed and details["precision"] is None and details["jaccard"] is None


def test_critical_failure_cannot_be_compensated():
    failed = result(metric(critical=True, required=False), "failed", explanation="Wrong pairing.")
    successes = [result(metric(), "passed", explanation="Valid.") for _ in range(100)]
    assert aggregate([failed, *successes])[0] == "failed"


def test_pending_and_non_applicable_coverage():
    passed = result(metric(), "passed", explanation="Valid.")
    missing = result(metric(), "not_assessed", explanation="Reference missing.")
    skipped = result(metric(), "not_applicable", explanation="Not requested.")
    status, summary = aggregate([passed, missing, skipped])
    assert status == "not_assessed" and summary["coverage"] == .5
    assert summary["pass_rate"] == 1
    assert aggregate([skipped])[1]["pass_rate"] is None
    pending_critical = result(metric(required=False, critical=True), "not_assessed", explanation="Reference missing.")
    assert aggregate([passed, pending_critical])[0] == "not_assessed"


def test_contract_rejects_inconsistent_conditions_and_paths(tmp_path):
    with pytest.raises(ValidationError):
        StageExpectation(stage_id="03_pca", applicability="conditional", reason="PCA may apply.")
    with pytest.raises(ValueError, match="escapes"):
        safe_path(tmp_path, "../outside.json")
    with pytest.raises(ValidationError):
        MetricSpec(id="a", validator="table", observation=Observation(document="table", artifact_kind="comparison"),
                   provenance="Reference", reference_id="x")


@pytest.mark.parametrize("variant", ["independent", "descriptive"])
def test_generic_cases_end_to_end_and_read_only_recheck(tmp_path, variant):
    case = make_evaluation_example(tmp_path / "case", variant)
    report = evaluate_case(case, tmp_path / "evaluation")
    assert report.status == "passed", report.model_dump()
    assert report.analysis_finished and report.api_calls == 0
    run = tmp_path / "evaluation/scientific_run"
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob('*') if p.is_file()}
    assert evaluate_case(case, tmp_path / "recheck", run=run).status == "passed"
    assert before == {p.relative_to(run): p.read_bytes() for p in run.rglob('*') if p.is_file()}
    assert report.scientific_review == "pending"
    with pytest.raises(ValueError, match="outside"):
        evaluate_case(case, run / "nested", run=run)


def test_missing_reference_remains_unassessed_after_json_roundtrip(tmp_path):
    path = make_evaluation_example(tmp_path / "case", "descriptive")
    data = json.loads(path.read_text())
    data["stages"][-1]["metrics"].append(metric().model_dump())
    path.write_text(json.dumps(data))
    report = evaluate_case(path, tmp_path / "evaluation")
    assert report.status == "not_assessed"
    assert report.stages[-1].metrics[-1].status == "not_assessed"


def test_case_contract_rejects_cycles_and_nonfinite_answers(tmp_path):
    path = make_evaluation_example(tmp_path / "case")
    data = json.loads(path.read_text())
    data["stages"][0]["depends_on"] = ["02_preparation"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        load_case(path)
    data["stages"][0]["depends_on"] = []
    data["stages"][0]["metrics"][0]["expected"] = float('nan')
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Non-finite"):
        load_case(path)


def test_isolated_preparation_from_frozen_reference_package(tmp_path):
    from metabolomics_agent.agent import Agent
    from metabolomics_agent.case_evaluation import fingerprint
    from metabolomics_agent.evaluation_cases import ScriptedProvider
    from metabolomics_agent.models import LLMConfig
    from metabolomics_agent.state import RunState
    path = make_evaluation_example(tmp_path / "case", "descriptive")
    case = load_case(path)
    package = path.parent / "stage-input"
    state = RunState(case.study, path.parent / "input", package)
    Agent(state, ScriptedProvider([step.model_dump() for step in case.script]),
          LLMConfig(model="offline-package", max_turns=2, max_total_tokens=2000000)).run(
              case.request, progress=lambda _: None)
    assert state.workflow.stage == "02_preparation" and state.status == "turn_limit"
    data = case.model_dump()
    data["stage_packages"] = [{"stage_id": "02_preparation", "root": "stage-input",
        "provenance": "Frozen reference fixture after validated coordination.",
        "files": [{"path": p.relative_to(package).as_posix(), "sha256": fingerprint(p)}
                  for p in package.rglob('*') if p.is_file()]}]
    path.write_text(json.dumps(data))
    report = evaluate_case(path, tmp_path / "isolated", stage_only="02_preparation")
    assert report.status == "passed", report.model_dump()
    assert report.execution_mode == "stage_isolated" and not report.analysis_finished
    assert len(report.stages) == 1
    frozen = package / "run.json"
    frozen.write_text(frozen.read_text() + '\n')
    with pytest.raises(ValueError, match="checksum"):
        evaluate_case(path, tmp_path / "bad-package", stage_only="02_preparation")
