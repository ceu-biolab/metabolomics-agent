"""Small reusable cases with independent audit oracles, not LLM answer templates."""

from pathlib import Path

import pandas as pd

from .case_evaluation import fingerprint
from .evaluation_cases import CASES, create_inputs, script
from .evaluation_models import EvaluationCase
from .serialization import dumps


def make_evaluation_example(output: Path, variant="independent", seed=42):
    case = next((item for item in CASES if item.name == variant), None)
    if case is None or case.guards or case.resume_after:
        raise ValueError("Choose a non-guard, non-resume example.")
    output.mkdir(parents=True, exist_ok=False)
    study, request = create_inputs(case, output / "input", seed)
    table = pd.read_csv(output / "input/data.csv")
    def exact(mid, document, path, expected, critical=True):
        return {"id": mid, "validator": "exact", "observation": {"document": document, "path": path},
                "expected": expected, "critical": critical, "provenance": "Declared synthetic task contract."}
    def audit(mid, prefixes):
        return {"id": mid, "validator": "audit", "audit_prefixes": prefixes,
                "critical": True, "provenance": "Independent reconstruction from frozen input; shared scientific libraries."}
    stages = [
        {"stage_id": "01_coordination", "reason": "Declared independent/paired synthetic design.",
         "metrics": [exact("coordination.design_match", "run", ["study", "design"], case.design),
                     {"id": "coordination.cohort_match", "validator": "set", "critical": True,
                      "observation": {"document": "table", "artifact_kind": "input_values", "column": "sample_id"},
                      "expected": table.sample_id.tolist(), "provenance": "Frozen synthetic sample roster."}]},
        {"stage_id": "02_preparation", "reason": "Preparation and descriptive computations.",
         "depends_on": ["01_coordination"], "metrics": [audit("preparation.computation", ["preparation.", "descriptive."])]},
    ]
    if case.expected_status == "complete":
        stages.append({"stage_id": "03_univariate", "reason": "Compare groups or verify descriptive omission.",
            "depends_on": ["02_preparation"], "metrics": [audit("univariate.computation", ["comparison."])]
            if case.analysis_kind == "two_group" else [exact("univariate.omission", "state", ["comparisons"], {})]})
        stages.append({"stage_id": "03_pca", "reason": "Requested PCA or independently verified omission.",
            "depends_on": ["02_preparation"], "metrics": [audit("pca.computation", ["pca."])]
            if case.pca == "required" else [exact("pca.omission", "state", ["pcas"], {})]})
        stages.append({"stage_id": "04_visualization", "reason": "Persisted figure sources and declared omissions.",
            "metrics": [audit("visualization.computation", ["figure.", "heatmap."])]
            if case.analysis_kind == "two_group" or case.pca == "required" else [exact("visualization.omission", "state", ["workflow", "figure_ids"], [])]})
        stages.append({"stage_id": "05_reporting", "reason": "Final report and explicit scientific-review limit.",
            "metrics": [audit("reporting.records", ["report."]),
                        {"id": "reporting.scientific_review", "validator": "manual", "required": False,
                         "provenance": "Interpretation requires a human reviewer."}]})
    else:
        # A controlled preparation failure must not be confused with successful inference.
        stages[1]["metrics"] = [exact("preparation.expected_stop", "state", ["status"], case.expected_status),
                               exact("preparation.no_fake_dataset", "state", ["datasets"], {})]
        stages.append({"stage_id": "03_univariate", "reason": "Expected upstream failure blocks inference.",
            "requires_artifact_kinds": ["testing_values"],
            "metrics": [{"id": "univariate.not_executed", "validator": "manual", "required": False,
                         "provenance": "No scientific comparison is available after the expected preparation stop."}]})
    document = EvaluationCase.model_validate({
        "case_id": f"synthetic.{seed}.{variant}", "mode": "reproduction", "seed": seed,
        "dataset": {"dataset_id": f"synthetic-{seed}", "root": "input", "source": "Seeded synthetic example, not a biological validation cohort.",
                    "units": "Arbitrary synthetic concentration units.", "normalization_status": study.normalization_status,
                    "files": [{"path": name, "sha256": fingerprint(output / "input" / name)} for name in ["data.csv", "metadata.csv"]]},
        "study": study.model_dump(), "request": request, "stages": stages,
        "script": script(case), "expected_execution_status": case.expected_status})
    path = output / "case.json"
    path.write_text(dumps(document.model_dump()) + "\n")
    return path
