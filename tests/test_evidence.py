"""Persistence tests exercise real files, failures, and process interruption."""

import json
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import call
from metabolomics_agent.evidence import EvidenceStore
from metabolomics_agent.state import RunState


def report_for(state, tool):
    reports = [json.loads((state.output_dir / path).read_text())
               for path in state.evidence.attempts]
    return next(report for report in reversed(reports) if report["tool"] == tool)


def test_typed_round_trip_preserves_ids_missing_empty_strings_and_numbers(tmp_path):
    store = EvidenceStore(tmp_path)
    original = pd.DataFrame({
        "sample_id": ["001", "NA", "003", "004"],
        "label": ["", "NA", None, "null"],
        "value": [np.nextafter(1.0, 2.0), np.nan, 0.0, 1e-200],
        "count": pd.Series([2**60 + 1, 2**60 + 2, 0, 1], dtype="int64"),
        "imputed": [True, False, False, True],
    })
    with store.attempt("preparation", "round_trip"):
        artifact = store.table(original, "checkpoint", individual_samples=True)
    restored = EvidenceStore(tmp_path).read_table(artifact.id)
    pd.testing.assert_frame_equal(original.fillna({"label": pd.NA}), restored, check_exact=True)
    assert artifact.schema_sha256 and artifact.sha256
    assert artifact.individual_samples


def test_failed_log_preserves_imputation_checkpoint_and_mask(tools):
    tools.state.raw.iloc[0, 0] = 0
    tools.state.raw.iloc[1, 0] = np.nan
    call(tools, "inspect_dataset")
    plan = call(tools, "set_plan", {
        "rationale": "Inspect imputation before a deliberately invalid logarithm.",
        "imputation": "median", "transform": "log2",
    })
    result = tools.execute("preprocess", {"plan_id": plan["plan_id"]})
    assert not result["ok"]
    assert not tools.state.datasets
    report = report_for(tools.state, "preprocess")
    assert report["status"] == "failed" and report["no_issues_detected"] is False
    operations = {operation["name"]: operation for operation in report["operations"]}
    assert operations["impute"]["status"] == "completed"
    assert operations["transform"]["status"] == "failed"
    store = EvidenceStore(tools.state.output_dir, recover=True)
    outputs = [store.artifacts[aid] for aid in report["output_artifact_ids"]]
    imputed = next(artifact for artifact in outputs if artifact.kind == "imputed")
    mask = next(artifact for artifact in outputs if artifact.kind == "imputation_mask")
    values = store.read_table(imputed.id)
    assert values.lipid_a.isna().sum() == 0
    assert values.lipid_a.iloc[0] == 0
    assert store.read_table(mask.id).lipid_a.sum() == 1
    assert not any(artifact.kind == "transformed" for artifact in outputs)
    assert store.path(f"stages/{report['stage_id']}/{report['attempt_id']}/stage_report.md").is_file()
    restored = RunState.restore(tools.state.output_dir)
    assert restored.evidence.read_table(imputed.id).equals(values)
    assert not restored.datasets


def test_atomic_write_failure_does_not_register_partial_table(tmp_path, monkeypatch):
    store = EvidenceStore(tmp_path)
    original = pd.DataFrame({"value": [1.0]})

    def broken_csv(self, path, **kwargs):
        Path(path).write_text("partial")
        raise OSError("simulated full disk")

    monkeypatch.setattr(pd.DataFrame, "to_csv", broken_csv)
    with pytest.raises(OSError, match="simulated full disk"):
        with store.attempt("preparation", "write"):
            store.table(original, "partial")
    assert not store.artifacts
    assert not list(tmp_path.rglob("*.tmp"))
    report = json.loads((tmp_path / store.attempts[0]).read_text())
    assert report["status"] == "failed"
    assert not report["output_artifact_ids"]


def test_abrupt_process_exit_recovers_last_committed_checkpoint(tmp_path):
    script = """
import os
import sys
from pathlib import Path
import pandas as pd
from metabolomics_agent.evidence import EvidenceStore
store = EvidenceStore(Path(sys.argv[1]))
with store.attempt('preparation', 'crash'):
    with store.operation('impute'):
        store.table(pd.DataFrame({'sample_id': ['001'], 'value': [2.0]}), 'imputed')
    with store.operation('transform'):
        os._exit(23)
"""
    process = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                             capture_output=True, text=True)
    assert process.returncode == 23, process.stderr
    store = EvidenceStore(tmp_path, recover=True)
    report = json.loads((tmp_path / store.attempts[0]).read_text())
    assert report["status"] == "interrupted"
    assert [operation["status"] for operation in report["operations"]] == ["completed", "interrupted"]
    artifact = store.artifacts[report["output_artifact_ids"][0]]
    assert store.read_table(artifact.id).sample_id.tolist() == ["001"]
    first_report = (tmp_path / store.attempts[0]).read_bytes()
    with store.attempt("preparation", "retry") as new:
        assert new.attempt_id == "attempt_002"
    assert (tmp_path / store.attempts[0]).read_bytes() == first_report


@pytest.mark.parametrize("tampering", ["contents", "schema", "outside_path", "parent"])
def test_corruption_or_invalid_provenance_is_rejected(tmp_path, tampering):
    store = EvidenceStore(tmp_path)
    with store.attempt("preparation", "save"):
        artifact = store.table(pd.DataFrame({"value": [1.0]}), "checkpoint")
    if tampering == "contents":
        store.path(artifact.path).write_text("value\n9\n")
    elif tampering == "schema":
        store.path(artifact.schema_path).write_text("{}")
    else:
        index = json.loads((tmp_path / "evidence_index.json").read_text())
        if tampering == "outside_path":
            index["artifacts"][artifact.id]["path"] = "../outside.csv"
        else:
            index["artifacts"][artifact.id]["parent_ids"] = ["unknown"]
        (tmp_path / "evidence_index.json").write_text(json.dumps(index))
    with pytest.raises(ValueError):
        EvidenceStore(tmp_path, recover=True)


def test_no_transform_stage_has_report_without_invented_data(tmp_path):
    store = EvidenceStore(tmp_path)
    with store.attempt("coordination", "plan"):
        store.check("SCOPE", True, True, passed=True, message="Scope must be supported.")
    report = json.loads((tmp_path / store.attempts[0]).read_text())
    assert report["status"] == "completed"
    assert report["no_issues_detected"] is True
    assert not report["output_artifact_ids"]
    assert not store.artifacts


def test_runtime_warning_is_local_and_recorded(tools, monkeypatch):
    from metabolomics_agent import analysis

    def warning_inspection(state):
        warnings.warn("deliberate runtime warning", UserWarning)
        return {"samples": len(state.raw)}

    monkeypatch.setattr(analysis, "inspect_dataset", warning_inspection)
    result = tools.execute("inspect_dataset", {})
    assert result == {"ok": True, "result": {"samples": len(tools.state.raw)}}
    report = report_for(tools.state, "inspect_dataset")
    assert report["status"] == "completed_with_warnings"
    assert report["issues"][0]["code"] == "PYTHON_WARNING"
    assert "deliberate runtime warning" in report["issues"][0]["message"]


def test_restore_uses_schemas_and_rejects_missing_evidence_index(state):
    restored = RunState.restore(state.output_dir)
    pd.testing.assert_frame_equal(restored.raw, state.raw, check_exact=True)
    (state.output_dir / "evidence_index.json").unlink()
    with pytest.raises(ValueError, match="Missing evidence index"):
        RunState.restore(state.output_dir)


def test_legacy_state_remains_readable_without_changing_original_tables(state):
    saved = json.loads((state.output_dir / "state.json").read_text())
    saved.pop("schema_version")
    for artifact in saved["artifacts"].values():
        for key in ["sha256", "schema_path", "schema_sha256"]:
            artifact.pop(key, None)
    (state.output_dir / "state.json").write_text(json.dumps(saved))
    (state.output_dir / "evidence_index.json").unlink()
    before = {aid: (state.output_dir / value["path"]).read_bytes()
              for aid, value in state.artifacts.items()}
    restored = RunState.restore(state.output_dir)
    np.testing.assert_allclose(restored.raw, state.raw)
    for aid, value in restored.artifacts.items():
        assert (state.output_dir / value["path"]).read_bytes() == before[aid]


def test_validated_missing_operation_is_not_a_published_dataset(tools):
    call(tools, "inspect_dataset")
    plan = call(tools, "set_plan", {"rationale": "Prespecified preparation without any transformations."})
    result = call(tools, "preprocess", {"plan_id": plan["plan_id"]})
    report = report_for(tools.state, "preprocess")
    assert report["status"] == "completed"
    skipped = {op["name"] for op in report["operations"] if op["status"] == "skipped"}
    assert {"impute", "normalize", "transform", "zero_handling"} <= skipped
    kinds = {tools.state.evidence.artifacts[aid].kind for aid in report["output_artifact_ids"]}
    assert not {"imputed", "normalized", "transformed"} & kinds
    assert tools.state.datasets[result["dataset_id"]].artifact_ids == result["artifacts"]
