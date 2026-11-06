import json

from pydantic import ValidationError

from . import analysis
from .models import (AnalysisPlan, DatasetArgs, FinishArgs, InspectArgs, PCAArgs,
                     PlotArgs, PrepareArgs, ReadResultArgs)
from .plots import make_plot
from .report import finish_analysis
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
