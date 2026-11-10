import numpy as np
import pandas as pd
import pytest

from metabolomics_agent.models import StudyConfig
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry


@pytest.fixture
def independent_study(tmp_path):
    rng = np.random.default_rng(12)
    table = pd.DataFrame({
        "sample_id": [f"S{i:03d}" for i in range(24)],
        "group": ["N"] * 12 + ["T"] * 12,
        "lipid_a": np.r_[np.arange(1, 13), np.arange(6, 18)],
        "lipid_b": np.exp(rng.normal(2, 0.4, 24)),
        "constant": 7.0,
    })
    table.to_csv(tmp_path / "values.csv", index=False)
    config = StudyConfig(data_path="values.csv", reference_group="N", treatment_group="T")
    return config, tmp_path, table


@pytest.fixture
def state(independent_study):
    cfg, root, _ = independent_study
    return RunState(cfg, root, root / "run", protocol="legacy")


@pytest.fixture
def tools(state):
    return ToolRegistry(state)


def call(tools, name, args=None):
    result = tools.execute(name, args or {})
    assert result["ok"], result
    return result["result"]


def prepare(tools, **options):
    if not tools.state.inspected:
        call(tools, "inspect_dataset")
    plan = call(tools, "set_plan", {
        "rationale": "Prespecified independent-group comparison with no preprocessing required.",
        **options,
    })
    return call(tools, "preprocess", {"plan_id": plan["plan_id"]})["dataset_id"]
