import json
import warnings
import hashlib
from importlib.resources import files

from pydantic import ValidationError

from . import analysis
from .models import (AnalysisPlan, DatasetArgs, FinishArgs, InspectArgs, PCAArgs,
                     PlotArgs, PrepareArgs, ReadResultArgs)
from .plots import make_plot
from .report import finish_analysis
from .evidence_models import StageIssue
from .state import RunState, clean_json


TOOL_DEFINITIONS = {
    "inspect_dataset": (InspectArgs, "Inspect selected groups, missingness, distributions, outliers, "
                        "and batch metadata. Must be called before planning. Returns diagnostics artifact."),
    "set_plan": (AnalysisPlan, "Record or revise an analysis plan with a scientific rationale. "
                 "Revisions after any statistical test are automatically exploratory. "
                 "Changing methods solely to achieve significance is invalid."),
    "preprocess": (PrepareArgs, "Prepare a NEW dataset from the original input under the specified plan. "
                   "Produces separate concentration, testing, and PCA representations. "
                   "Does not silently delete samples or add pseudocounts."),
    "compare_groups": (DatasetArgs, "Execute the dataset's planned two-sided test across retained features, "
                       "calculate effects, and adjust p-values across valid features. Returns top five "
                       "rows plus a complete table artifact. No test parameters can be silently changed."),
    "run_pca": (PCAArgs, "Run unsupervised PCA on the prepared dataset's separate PCA representation. "
                "Requires complete data; returns explained variance, scores, and loadings artifacts."),
    "make_plot": (PlotArgs, "Create a figure. source_id: raw/dataset ID for distributions, boxplot, "
                  "heatmap; comparison ID for volcano; PCA result ID for pca. Volcano uses adjusted p. "
                  "Default feature selection is original column order, not significance."),
    "read_result": (ReadResultArgs, "Read up to 20 rows from a saved TABLE artifact. Optional exact feature "
                    "filter. Use artifact IDs rather than filesystem paths. Large matrices stay local."),
    "finish_analysis": (FinishArgs, "Write a report with evidence-linked findings and limitations. Requires "
                        "a comparison, PCA, distribution/volcano/PCA figures, and the latest plan tested. "
                        "Call last; success ends the run."),
}


class ToolRegistry:
    def __init__(self, state: RunState):
        self.state = state

    def schemas(self):
        return [{"type": "function", "function": {
            "name": name, "description": description, "parameters": model.model_json_schema(),
        }} for name, (model, description) in TOOL_DEFINITIONS.items()]

    def execute(self, name: str, arguments: str | dict):
        """Record legacy tool attempts without changing routing or model context.

        Delivery C will group these persistence primitives into workflow stages.
        Invalid calls also get a failed report, rather than losing their evidence.
        """
        stages = {
            "inspect_dataset": "02_preparation", "set_plan": "01_coordination",
            "preprocess": "02_preparation", "compare_groups": "03_univariate",
            "make_plot": "04_visualization", "finish_analysis": "05_reporting",
            "run_pca": "legacy_multivariate", "read_result": "evidence_read",
        }
        parameters = arguments if isinstance(arguments, dict) else {"raw_arguments": arguments}
        inputs = [aid for aid, artifact in self.state.artifacts.items()
                  if artifact["kind"] in {"input_values", "input_metadata"}]
        try:
            decoded = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (ValueError, TypeError):
            decoded = {}
        if isinstance(decoded, dict):
            dataset_id = decoded.get("dataset_id", decoded.get("source_id"))
            if not isinstance(dataset_id, str):
                dataset_id = None
            if dataset_id in self.state.datasets:
                inputs.extend(self.state.datasets[dataset_id].artifact_ids.values())
            if dataset_id in self.state.comparisons:
                inputs.append(self.state.comparisons[dataset_id]["artifact_id"])
            if dataset_id in self.state.pcas:
                inputs.extend(self.state.pcas[dataset_id][key]
                              for key in ("scores_artifact", "loadings_artifact"))
            if isinstance(decoded.get("artifact_id"), str) and decoded["artifact_id"] in self.state.artifacts:
                inputs.append(decoded["artifact_id"])
        inputs = list(dict.fromkeys(inputs))
        system = self.state.run_info.get("system_prompt") or files("metabolomics_agent").joinpath(
            "prompts/system.md").read_text()
        # Parameters are already local trace data; full sample tables are never
        # included in a model-visible result by this persistence layer.
        with self.state.evidence.attempt(
            stages.get(name, "tool_validation"), name, inputs=inputs,
            parameters=parameters,
            context={"study": self.state.config.model_dump(), "tool": name,
                     "request": self.state.run_info.get("request", self.state.config.objective)},
            prompt_snapshot={"applicable": True, "protocol": "legacy_single_prompt",
                             "system": system, "sha256": hashlib.sha256(system.encode()).hexdigest()},
        ) as report:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                result = self._execute(name, arguments)
            report.issues.extend(StageIssue(
                code="PYTHON_WARNING", severity="warning", source="computed_check",
                message=f"{item.category.__name__}: {item.message}",
                evidence_ids=list(report.output_artifact_ids),
                scope={"file": item.filename, "line": item.lineno},
            ) for item in captured)
            if not result["ok"]:
                report.status = "failed"
                report.issues.append(StageIssue(
                    code="TOOL_CALL_FAILED", severity="error", source="tool_error",
                    message=result["error"], blocking=True,
                    evidence_ids=list(report.output_artifact_ids),
                ))
            else:
                summary = result["result"]
                report.limitations.extend(summary.get("notes", []))
                report.limitations.extend(summary.get("flags", []))
                if name == "compare_groups":
                    self.state.evidence.check(
                        "SKIPPED_FEATURES", 0, summary["features_skipped"],
                        passed=summary["features_skipped"] == 0,
                        message="Some features could not be tested; inspect the complete comparison table.",
                    )
                self.state.evidence.check(
                    "TOOL_COMPLETED", True, True, passed=True,
                    message="Tool execution must complete.",
                )
                if name == "finish_analysis":
                    for path, kind in [("report.md", "final_report"), ("findings.json", "findings")]:
                        self.state.evidence.register_file(path, kind)
            return result

    def _execute(self, name: str, arguments: str | dict):
        state = self.state
        try:
            if state.finished:
                raise ValueError("The run is already complete.")
            if name not in TOOL_DEFINITIONS:
                raise ValueError(f"Unknown tool {name!r}.")
            if isinstance(arguments, str):
                if len(arguments) > 32000:
                    raise ValueError("Tool arguments exceed 32000 characters.")
                arguments = json.loads(arguments)
            parsed = TOOL_DEFINITIONS[name][0].model_validate(arguments)
            state.event("tool_call", tool=name, arguments=parsed.model_dump())
            if name == "inspect_dataset":
                result = analysis.inspect_dataset(state)
            elif name == "set_plan":
                result = analysis.set_plan(state, parsed)
            elif name == "preprocess":
                result = analysis.preprocess(state, **parsed.model_dump())
            elif name == "compare_groups":
                result = analysis.compare_groups(state, **parsed.model_dump())
            elif name == "run_pca":
                result = analysis.run_pca(state, **parsed.model_dump())
            elif name == "make_plot":
                result = make_plot(state, parsed)
            elif name == "read_result":
                artifact = state.artifacts.get(parsed.artifact_id)
                if not artifact:
                    raise ValueError("Unknown artifact ID.")
                if artifact["kind"] in {"input_values", "input_metadata", "pca_scores",
                                         "concentrations", "testing_values", "pca_values"}:
                    raise ValueError("Individual sample tables remain local. Read diagnostics, "
                                     "comparison results, or loadings instead.")
                table = state.read_artifact(parsed.artifact_id)
                if parsed.features:
                    if "feature" not in table:
                        raise ValueError("This table does not have a feature column.")
                    unknown = set(parsed.features) - set(table.feature)
                    if unknown:
                        raise ValueError(f"Unknown features: {sorted(unknown)}")
                    table = table[table.feature.isin(parsed.features)]
                result = {"artifact_id": parsed.artifact_id, "matching_rows": len(table),
                          "rows": table.head(parsed.limit).to_dict(orient="records")}
            else:
                result = finish_analysis(state, parsed)
            envelope = {"ok": True, "result": clean_json(result)}
            state.event("tool_result", tool=name, **envelope)
        except (ValueError, TypeError, KeyError, ValidationError) as error:
            envelope = {"ok": False, "error": str(error)}
            state.event("tool_error", tool=name, arguments=arguments, error=str(error))
        state.snapshot()
        return envelope
