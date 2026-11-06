import json
from pathlib import Path

import numpy as np
import pandas as pd


def generate_example(directory: Path, seed: int = 42) -> Path:
    """Small synthetic study with known effects, missingness, and a constant feature."""
    directory.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    count = 30
    logs = rng.normal(3, 0.45, size=(count * 2, 12))
    logs[count:, :3] += [1.0, -0.9, 0.6]
    values = np.exp(logs)
    values[4, 5] *= 15  # An outlier to inspect, not silently delete.
    values[rng.random(values.shape) < 0.025] = np.nan
    table = pd.DataFrame(values, columns=[f"lipid_{i + 1:02d}" for i in range(12)])
    table["constant_feature"] = 5.0
    table["mostly_missing"] = np.where(rng.random(count * 2) < 0.8, np.nan, 1.0)
    table.insert(0, "sample_id", [f"S{i + 1:03d}" for i in range(count * 2)])
    table.to_csv(directory / "abundances.csv", index=False, float_format="%.17g")
    pd.DataFrame({"sample_id": table.sample_id,
                  "group": ["control"] * count + ["condition"] * count}).to_csv(
        directory / "metadata.csv", index=False)
    study = {
        "data_path": "abundances.csv", "metadata_path": "metadata.csv",
        "sample_column": "sample_id", "group_column": "group",
        "reference_group": "control", "treatment_group": "condition",
        "design": "independent", "normalization_status": "Synthetic positive concentrations; no normalization applied.",
        "objective": "Compare synthetic condition versus control. Assess data quality, "
                     "perform multiplicity-corrected tests and PCA, and report limitations.",
    }
    (directory / "study.json").write_text(json.dumps(study, indent=2) + "\n")
    (directory / "truth.json").write_text(json.dumps({
        "seed": seed, "samples_per_group": count, "shifted_log_features": {
            "lipid_01": 1.0, "lipid_02": -0.9, "lipid_03": 0.6,
        }, "note": "Synthetic ground truth; never supplied to the agent.",
    }, indent=2) + "\n")
    return directory / "study.json"
