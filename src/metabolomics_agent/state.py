import importlib.metadata
import json
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data import load_study
from .evidence import EvidenceStore, digest
from .models import AnalysisPlan, StudyConfig
from .serialization import clean_json as clean_json, dumps as dumps
from .workflow import Workflow
from .workflow_models import (FigurePlan, FigurePlanV1, PreprocessingPlan, UnivariatePlan,
                              UnivariatePlanV1, WorkflowPlan, WorkflowPlanV1, WorkflowPlanV2, FigurePlanV2, PCAPlan)


@dataclass
class Dataset:
    concentrations: pd.DataFrame
    testing: pd.DataFrame
    pca_values: pd.DataFrame | None
    plan_id: str
    artifact_ids: dict[str, str] = field(default_factory=dict)
    imputation_mask: pd.DataFrame | None = None


class RunState:
    @classmethod
    def restore(cls, output_dir: Path):
        """Restore only frozen local snapshots; never reload possibly changed inputs."""
        self = cls.__new__(cls)
        self.output_dir = output_dir.resolve()
        saved = json.loads((self.output_dir / "state.json").read_text())
        if saved.get("schema_version", 1) not in {1, 2, 3}:
            raise ValueError("Unsupported run state schema version.")
        if saved.get("schema_version", 1) >= 2 and not (self.output_dir / "evidence_index.json").is_file():
            raise ValueError("Missing evidence index for this run state version.")
        self.run_info = json.loads((self.output_dir / "run.json").read_text())
        strategy = self.run_info.get("context_strategy", "full_history")
        if strategy not in {"full_history", "phase_window_v1"}:
            raise ValueError("Unsupported saved context strategy.")
        self.context_window = saved.get("context_window")
        if strategy == "phase_window_v1" and not isinstance(self.context_window, dict):
            raise ValueError("A phase-window run is missing its saved context position.")
        if saved["finished"]:
            raise ValueError("A complete run cannot be resumed.")
        self.evidence = EvidenceStore(self.output_dir, recover=True)
        self.config = StudyConfig.model_validate(self.run_info["study"])
        self.seed = self.run_info["seed"]
        self.protocol = self.run_info.get("protocol", "legacy")
        self.descriptive_summaries = saved.get("descriptive_summaries", {})
        self.univariate_diagnostics = saved.get("univariate_diagnostics", {})
        self.univariate_attempts = saved.get("univariate_attempts", {})
        self.pca_diagnostics = saved.get("pca_diagnostics", {})
        self.pca_attempts = saved.get("pca_attempts", {})
        self.pca_plans = {key: PCAPlan.model_validate(value) for key, value in saved.get("pca_plans", {}).items()}
        self.plot_results = saved.get("plot_results", {})
        if self.run_info.get("descriptive_version", 0) not in {0, 1}:
            raise ValueError("Unsupported descriptive workflow version.")
        if self.run_info.get("univariate_version", 0) not in {0, 1}:
            raise ValueError("Unsupported univariate workflow version.")
        if self.run_info.get("visualization_version", 0) not in {0, 1, 2}:
            raise ValueError("Unsupported visualization workflow version.")
        if self.run_info.get("pca_version", 0) not in {0, 1}:
            raise ValueError("Unsupported PCA workflow version.")
        if self.run_info.get("univariate_version") and not self.run_info.get("descriptive_version"):
            raise ValueError("Audited inference requires saved observed/imputed provenance.")
        if self.protocol not in {"legacy", "staged_v1"}:
            raise ValueError("Unsupported workflow protocol; cannot migrate a saved run implicitly.")
        if self.protocol == "staged_v1" and not saved.get("workflow"):
            raise ValueError("Missing staged workflow state.")
        self.plans = {pid: AnalysisPlan.model_validate(p) for pid, p in saved["plans"].items()}
        for attribute, model in [("workflow_plans", WorkflowPlanV2 if self.run_info.get("visualization_version") == 2 else WorkflowPlanV1 if self.run_info.get("visualization_version") else WorkflowPlan), ("preprocessing_plans", PreprocessingPlan),
                                 ("univariate_plans", UnivariatePlanV1 if self.run_info.get("univariate_version") else UnivariatePlan),
                                 ("figure_plans", FigurePlanV2 if self.run_info.get("visualization_version") == 2 else FigurePlanV1 if self.run_info.get("visualization_version") else FigurePlan)]:
            setattr(self, attribute, {pid: model.model_validate(plan)
                                     for pid, plan in saved.get(attribute, {}).items()})
        self.artifacts = saved["artifacts"]
        for artifact in self.artifacts.values():
            path = (self.output_dir / artifact["path"]).resolve()
            if not path.is_relative_to(self.output_dir) or not path.is_file():
                raise ValueError("Run artifacts must exist inside the run directory.")
            if artifact.get("sha256") and digest(path) != artifact["sha256"]:
                raise ValueError(f"Modified run artifact: {artifact['id']}")
            if artifact["id"] not in self.evidence.artifacts:
                self.evidence.register_file(artifact["path"], artifact["kind"], aid=artifact["id"])
        self.comparisons, self.pcas = saved["comparisons"], saved["pcas"]
        self.inspected, self.finished, self.status = saved["inspected"], False, saved["status"]
        events = self.output_dir / "events.jsonl"
        self.events = max((json.loads(line)["sequence"] for line in
                           (events.read_text().splitlines() if events.exists() else [])), default=0)
        sid = self.config.sample_column

        def load(kind, dataset_id=None, artifact_id=None):
            matches = [a for a in self.artifacts.values() if a["kind"] == kind
                       and (dataset_id is None or a.get("dataset_id") == dataset_id)
                       and (artifact_id is None or a["id"] == artifact_id)]
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one saved {kind} table for {dataset_id}.")
            if self.evidence.artifacts[matches[0]["id"]].schema_path:
                return self.evidence.read_table(matches[0]["id"]).set_index(sid)
            columns = [sid, self.config.group_column, self.config.pair_column, self.config.batch_column]
            return pd.read_csv(self.output_dir / matches[0]["path"],
                               dtype={c: str for c in columns if c}).set_index(sid)

        self.raw, self.metadata = load("input_values"), load("input_metadata")
        inspection = self.output_dir / "inspection.json"
        self.excluded_groups = (json.loads(inspection.read_text()).get("excluded_groups", {})
                                if inspection.exists() else {})
        self.datasets = {did: Dataset(
            load("concentrations", did, d.get("artifact_ids", {}).get("concentrations")),
            load("testing_values", did, d.get("artifact_ids", {}).get("testing")),
            (load("pca_values", did, d.get("artifact_ids", {}).get("pca"))
             if self.protocol == "legacy" else None),
            d["plan_id"], d.get("artifact_ids", {}),
            (load("imputation_mask", did, d["artifact_ids"]["imputation_mask"])
             if d.get("artifact_ids", {}).get("imputation_mask") else None))
                         for did, d in saved["datasets"].items()}
        if self.run_info.get("descriptive_version") == 1 and any(
            dataset.imputation_mask is None for dataset in self.datasets.values()
        ):
            raise ValueError("A descriptive-enabled dataset is missing its saved imputation mask.")
        self.workflow = Workflow(self, saved["workflow"]) if self.protocol == "staged_v1" else None
        return self

    def __init__(self, config: StudyConfig, base_dir: Path, output_dir: Path, seed: int = 42,
                 *, protocol="staged_v1"):
        if protocol not in {"legacy", "staged_v1"}:
            raise ValueError("Unsupported workflow protocol.")
        self.config = config
        self.seed = seed
        self.protocol = protocol
        self.workflow = None
        self.context_window = None
        self.workflow_plans = {}
        self.preprocessing_plans = {}
        self.univariate_plans = {}
        self.figure_plans = {}
        self.descriptive_summaries = {}
        self.univariate_diagnostics = {}
        self.univariate_attempts = {}
        self.pca_diagnostics = {}
        self.pca_attempts = {}
        self.pca_plans = {}
        self.plot_results = {}
        self.output_dir = output_dir.resolve()
        # A run directory is immutable across runs. Never overwrite prior evidence.
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.evidence = EvidenceStore(self.output_dir)
        self.plans: dict[str, AnalysisPlan] = {}
        self.datasets: dict[str, Dataset] = {}
        self.artifacts: dict[str, dict] = {}
        self.comparisons: dict[str, dict] = {}
        self.pcas: dict[str, dict] = {}
        self.inspected = False
        self.finished = False
        self.status = "created"
        self.events = 0
        self.run_info = {
            "protocol": protocol,
            "context_strategy": "phase_window_v1" if protocol == "staged_v1" else "full_history",
            "descriptive_version": 1 if protocol == "staged_v1" else 0,
            "univariate_version": 1 if protocol == "staged_v1" else 0,
            "visualization_version": 2 if protocol == "staged_v1" else 0,
            "pca_version": 1 if protocol == "staged_v1" else 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "study": config.model_dump(), "seed": seed, "python": platform.python_version(),
            "inputs": [],
            "packages": {p: importlib.metadata.version(p) for p in [
                "numpy", "pandas", "scipy", "statsmodels", "scikit-learn", "matplotlib",
                "seaborn", "pydantic", "openai", "metabolomics-agent",
            ]},
        }
        self.write_json("run.json", self.run_info)
        with self.evidence.attempt("initialization", "load_study",
                                   context={"study": config.model_dump()}):
            with self.evidence.operation("load_and_validate_inputs"):
                self.raw, self.metadata, self.excluded_groups, paths = load_study(config, base_dir)
                self.run_info["inputs"] = [{"path": str(p), "sha256": digest(p)} for p in paths]
                self.table(self.raw.reset_index(), "input_values", {})
                self.table(self.metadata.reset_index(), "input_metadata", {})
                self.evidence.check("INPUT_ALIGNMENT", True,
                                    self.raw.index.equals(self.metadata.index),
                                    passed=self.raw.index.equals(self.metadata.index),
                                    message="Input and metadata sample IDs must align.", severity="error")
            self.write_json("run.json", self.run_info)
        if protocol == "staged_v1":
            self.workflow = Workflow(self)
            self.context_window = {"phase": self.workflow.context_phase(),
                                   "start_index": 2, "epoch": 1}
            self.write_json("run.json", self.run_info)
        self.snapshot()

    def write_json(self, filename: str, value):
        self.evidence.write_json(filename, value)

    def event(self, kind: str, **values):
        self.events += 1
        record = {"sequence": self.events, "time": datetime.now(timezone.utc).isoformat(),
                  "kind": kind, **values}
        with (self.output_dir / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(clean_json(record), ensure_ascii=False, allow_nan=False) + "\n")

    def table(self, table: pd.DataFrame, kind: str, provenance: dict) -> str:
        aid = self.next_artifact_id()
        filename = f"{aid}_{kind}.csv"
        parents = provenance.get("parent_ids", self.evidence.active.input_artifact_ids
                                 if self.evidence.active else [])
        individual = kind in {"input_values", "input_metadata", "concentrations", "testing_values",
                              "pca_values", "pca_scores", "imputation_mask", "plot_source", "plot_data", "pca_input", "pca_matrix"}
        artifact = self.evidence.table(
            table, kind, aid=aid, relative=filename, parent_ids=parents,
            plan_id=provenance.get("plan_id"), dataset_id=provenance.get("dataset_id"),
            scale=provenance.get("scale"), individual_samples=individual,
            parameters=provenance.get("parameters", {}),
        )
        self.artifacts[aid] = {**artifact.model_dump(), **provenance}
        return aid

    def figure(self, figure, kind: str, provenance: dict) -> str:
        aid = self.next_artifact_id()
        filename = f"{aid}_{kind}.png"
        self.evidence.atomic_write(filename, lambda path: figure.savefig(
            path, format="png", dpi=160, bbox_inches="tight"
        ))
        parents = provenance.get("parent_ids", self.evidence.active.input_artifact_ids
                                 if self.evidence.active else [])
        artifact = self.evidence.register_file(filename, kind, aid=aid, parent_ids=parents,
                                              parameters=provenance)
        self.artifacts[aid] = {**artifact.model_dump(), **provenance}
        return aid

    def read_artifact(self, aid: str) -> pd.DataFrame:
        if aid not in self.artifacts or not self.artifacts[aid]["path"].endswith(".csv"):
            raise ValueError("Unknown table artifact ID.")
        if aid in self.evidence.artifacts and self.evidence.artifacts[aid].schema_path:
            return self.evidence.read_table(aid)
        return pd.read_csv(self.output_dir / self.artifacts[aid]["path"])

    def next_artifact_id(self):
        # Include committed outputs not yet reflected in a legacy state snapshot.
        numbers = [int(aid[1:]) for aid in self.evidence.artifacts
                   if aid.startswith("a") and aid[1:].isdigit()]
        numbers += [int(aid[1:]) for aid in self.artifacts
                    if aid.startswith("a") and aid[1:].isdigit()]
        numbers += [int(path.name.split("_")[0][1:])
                    for path in self.output_dir.glob("a[0-9]*_*")
                    if path.name.split("_")[0][1:].isdigit()]
        return f"a{max(numbers, default=0) + 1:03d}"

    def dataset(self, did: str) -> Dataset:
        if did not in self.datasets:
            raise ValueError(f"Unknown dataset ID {did!r}; call preprocess first.")
        return self.datasets[did]

    def snapshot(self):
        workflow = self.workflow.data.model_dump() if self.workflow else None
        if workflow:
            self.write_json("workflow_state.json", workflow)
        self.write_json("state.json", {
            "schema_version": 3 if workflow else 2,
            "workflow": workflow,
            "context_window": self.context_window,
            "workflow_plans": {key: value.model_dump() for key, value in self.workflow_plans.items()},
            "preprocessing_plans": {key: value.model_dump() for key, value in self.preprocessing_plans.items()},
            "univariate_plans": {key: value.model_dump() for key, value in self.univariate_plans.items()},
            "figure_plans": {key: value.model_dump() for key, value in self.figure_plans.items()},
            "descriptive_summaries": self.descriptive_summaries,
            "univariate_diagnostics": self.univariate_diagnostics,
            "univariate_attempts": self.univariate_attempts,
            "pca_diagnostics": self.pca_diagnostics, "pca_attempts": self.pca_attempts,
            "pca_plans": {key: value.model_dump() for key, value in self.pca_plans.items()},
            "plot_results": self.plot_results,
            "status": self.status, "finished": self.finished, "inspected": self.inspected,
            "plans": {k: v.model_dump() for k, v in self.plans.items()},
            "datasets": {k: {"plan_id": d.plan_id, "artifact_ids": d.artifact_ids,
                              "samples": len(d.testing),
                              "features": len(d.testing.columns)} for k, d in self.datasets.items()},
            "comparisons": self.comparisons, "pcas": self.pcas, "artifacts": self.artifacts,
        })
