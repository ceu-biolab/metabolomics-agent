import hashlib
import importlib.metadata
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import load_study
from .models import AnalysisPlan, StudyConfig


def clean_json(value: Any):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, np.generic):
        return clean_json(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def dumps(value: Any) -> str:
    return json.dumps(clean_json(value), indent=2, ensure_ascii=False, allow_nan=False)


@dataclass
class Dataset:
    concentrations: pd.DataFrame
    testing: pd.DataFrame
    pca_values: pd.DataFrame
    plan_id: str


class RunState:
    @classmethod
    def restore(cls, output_dir: Path):
        """Restore only frozen local snapshots; never reload possibly changed inputs."""
        self = cls.__new__(cls)
        self.output_dir = output_dir.resolve()
        saved = json.loads((self.output_dir / "state.json").read_text())
        self.run_info = json.loads((self.output_dir / "run.json").read_text())
        if saved["finished"]:
            raise ValueError("A complete run cannot be resumed.")
        self.config = StudyConfig.model_validate(self.run_info["study"])
        self.seed = self.run_info["seed"]
        self.plans = {pid: AnalysisPlan.model_validate(p) for pid, p in saved["plans"].items()}
        self.artifacts = saved["artifacts"]
        for artifact in self.artifacts.values():
            path = (self.output_dir / artifact["path"]).resolve()
            if path.parent != self.output_dir or not path.is_file():
                raise ValueError("Run artifacts must exist inside the run directory.")
        self.comparisons, self.pcas = saved["comparisons"], saved["pcas"]
        self.inspected, self.finished, self.status = saved["inspected"], False, saved["status"]
        events = self.output_dir / "events.jsonl"
        self.events = max((json.loads(line)["sequence"] for line in events.read_text().splitlines()), default=0)
        sid = self.config.sample_column

        def load(kind, dataset_id=None):
            matches = [a for a in self.artifacts.values() if a["kind"] == kind
                       and (dataset_id is None or a.get("dataset_id") == dataset_id)]
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one saved {kind} table for {dataset_id}.")
            columns = [sid, self.config.group_column, self.config.pair_column, self.config.batch_column]
            return pd.read_csv(self.output_dir / matches[0]["path"],
                               dtype={c: str for c in columns if c}).set_index(sid)

        self.raw, self.metadata = load("input_values"), load("input_metadata")
        inspection = self.output_dir / "inspection.json"
        self.excluded_groups = (json.loads(inspection.read_text()).get("excluded_groups", {})
                                if inspection.exists() else {})
        self.datasets = {did: Dataset(load("concentrations", did), load("testing_values", did),
                                     load("pca_values", did), d["plan_id"])
                         for did, d in saved["datasets"].items()}
        return self

    def __init__(self, config: StudyConfig, base_dir: Path, output_dir: Path, seed: int = 42):
        self.config = config
        self.seed = seed
        self.raw, self.metadata, self.excluded_groups, paths = load_study(config, base_dir)
        self.output_dir = output_dir.resolve()
        # A run directory is immutable across runs. Never overwrite prior evidence.
        self.output_dir.mkdir(parents=True, exist_ok=False)
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
            "created_at": datetime.now(timezone.utc).isoformat(),
            "study": config.model_dump(), "seed": seed, "python": platform.python_version(),
            "inputs": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in paths],
            "packages": {p: importlib.metadata.version(p) for p in [
                "numpy", "pandas", "scipy", "statsmodels", "scikit-learn", "matplotlib",
                "seaborn", "pydantic", "openai", "metabolomics-agent",
            ]},
        }
        self.table(self.raw.reset_index(), "input_values", {})
        self.table(self.metadata.reset_index(), "input_metadata", {})
        self.write_json("run.json", self.run_info)
        self.snapshot()

    def write_json(self, filename: str, value):
        target = self.output_dir / filename
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(dumps(value) + "\n")
        temporary.replace(target)

    def event(self, kind: str, **values):
        self.events += 1
        record = {"sequence": self.events, "time": datetime.now(timezone.utc).isoformat(),
                  "kind": kind, **values}
        with (self.output_dir / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(clean_json(record), ensure_ascii=False, allow_nan=False) + "\n")

    def table(self, table: pd.DataFrame, kind: str, provenance: dict) -> str:
        aid = f"a{len(self.artifacts) + 1:03d}"
        filename = f"{aid}_{kind}.csv"
        table.to_csv(self.output_dir / filename, index=False, float_format="%.17g")
        self.artifacts[aid] = {"id": aid, "kind": kind, "path": filename,
                               "rows": len(table), **provenance}
        return aid

    def figure(self, figure, kind: str, provenance: dict) -> str:
        aid = f"a{len(self.artifacts) + 1:03d}"
        filename = f"{aid}_{kind}.png"
        figure.savefig(self.output_dir / filename, dpi=160, bbox_inches="tight")
        self.artifacts[aid] = {"id": aid, "kind": kind, "path": filename, **provenance}
        return aid

    def read_artifact(self, aid: str) -> pd.DataFrame:
        if aid not in self.artifacts or not self.artifacts[aid]["path"].endswith(".csv"):
            raise ValueError("Unknown table artifact ID.")
        return pd.read_csv(self.output_dir / self.artifacts[aid]["path"])

    def dataset(self, did: str) -> Dataset:
        if did not in self.datasets:
            raise ValueError(f"Unknown dataset ID {did!r}; call preprocess first.")
        return self.datasets[did]

    def snapshot(self):
        self.write_json("state.json", {
            "status": self.status, "finished": self.finished, "inspected": self.inspected,
            "plans": {k: v.model_dump() for k, v in self.plans.items()},
            "datasets": {k: {"plan_id": d.plan_id, "samples": len(d.testing),
                              "features": len(d.testing.columns)} for k, d in self.datasets.items()},
            "comparisons": self.comparisons, "pcas": self.pcas, "artifacts": self.artifacts,
        })
