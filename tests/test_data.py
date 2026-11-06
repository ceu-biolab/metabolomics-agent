import pandas as pd
import pytest

from metabolomics_agent.data import load_study
from metabolomics_agent.models import StudyConfig
from metabolomics_agent.state import RunState


def test_metadata_alignment_and_numeric_metadata_exclusion(tmp_path):
    pd.DataFrame({"sample_id": ["001", "002", "003", "004", "005", "006"],
                  "lipid": [2, 3, 4, 5, 6, 7]}).to_csv(tmp_path / "data.csv", index=False)
    pd.DataFrame({"sample_id": ["006", "001", "004", "002", "005", "003"],
                  "group": ["T", "N", "T", "N", "T", "N"],
                  "age": [30, 31, 32, 33, 34, 35]}).to_csv(tmp_path / "meta.csv", index=False)
    cfg = StudyConfig(data_path="data.csv", metadata_path="meta.csv",
                      reference_group="N", treatment_group="T")
    values, metadata, _, _ = load_study(cfg, tmp_path)
    assert list(values.columns) == ["lipid"]
    assert values.index[0] == "001"
    assert metadata.loc["001", "age"] == 31
    assert metadata.loc["001", "group"] == "N"


def test_bad_inputs_fail_before_creating_run(independent_study):
    cfg, root, table = independent_study
    for mutation, match in [
        (lambda t: t.assign(sample_id="same"), "unique"),
        (lambda t: t.assign(lipid_a=-1), "nonnegative"),
        (lambda t: t.assign(lipid_a=float("inf")), "Infinite"),
        (lambda t: t.assign(unexplained_text="metadata"), "metadata_columns"),
    ]:
        mutation(table).to_csv(root / "values.csv", index=False)
        with pytest.raises(ValueError, match=match):
            RunState(cfg, root, root / "run")
        assert not (root / "run").exists()


def test_existing_run_is_not_overwritten(state, independent_study):
    cfg, root, _ = independent_study
    with pytest.raises(FileExistsError):
        RunState(cfg, root, state.output_dir)


def test_unmatched_metadata_is_rejected(independent_study):
    cfg, root, table = independent_study
    table[["sample_id", "group"]].iloc[:-1].to_csv(root / "meta.csv", index=False)
    table.drop(columns="group").to_csv(root / "values.csv", index=False)
    cfg = cfg.model_copy(update={"metadata_path": "meta.csv"})
    with pytest.raises(ValueError, match="matching sample IDs"):
        load_study(cfg, root)
