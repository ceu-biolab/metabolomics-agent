import json
import warnings
import hashlib
from importlib.resources import files

from pydantic import ValidationError

from . import analysis
from .descriptive import summarize_dataset
from .models import (AnalysisPlan, DatasetArgs, FinishArgs, InspectArgs, PCAArgs,
                     PlotArgs, PrepareArgs, ReadResultArgs)
from .plots import make_plot
from .report import finish_analysis
from .evidence_models import StageIssue
from .state import RunState, clean_json
from .workflow import COMMON_TOOLS, STAGES, STAGE_TOOLS
from .workflow_models import (CompareArgs, FigurePlan, FigureSpec, PreprocessingPlan,
                              ReadStageArgs, RevisionArgs, StageAssessment,
                              SummaryArgs, UnivariatePlan, WorkflowPlan)


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

STAGED_DEFINITIONS = {
    "get_workflow_state": (InspectArgs, "Read current stage, plans, aggregate diagnostics, and requirements."),
    "get_stage_context": (InspectArgs, "Read the validated aggregate context and incoming handoff."),
    "read_stage_report": (ReadStageArgs, "Read a bounded stage report summary; sample tables remain local."),
    "set_workflow_plan": (WorkflowPlan, "Register the objective, descriptive/two-group scope, and required figures."),
    "set_preprocessing_plan": (PreprocessingPlan, "After inspection, record filtering, imputation, normalization, and transformation choices."),
    "preprocess": (PrepareArgs, "Execute the current preparation plan, saving local checkpoints and separate concentration/test scales. No PCA representation is generated."),
    "set_univariate_plan": (UnivariatePlan, "Record a test and correction for the current dataset before testing. Revisions after testing are exploratory."),
    "compare_groups": (CompareArgs, "Execute the current univariate plan and save the full comparison table."),
    "set_figure_plan": (FigurePlan, "Register current sources and the figures required to answer the objective."),
    "make_plot": (FigureSpec, "Create a supported figure. Preparation allows diagnostic distributions/boxplots; visualization requires a registered figure plan."),
    "submit_stage_assessment": (StageAssessment, "Record an evidence-linked assessment and request validated stage completion. In reporting this records assessment without finishing; use finish_analysis last."),
    "request_revision": (RevisionArgs, "Create a justified new attempt of the current or an earlier stage. Preserve historical evidence and invalidate affected downstream results."),
    "validate_workflow": (InspectArgs, "Check objective-specific completion and current dependencies before final reporting."),
    "finish_analysis": (FinishArgs, "Validate and render the final report, close the reporting stage, and finish. Requires the preceding stages, with justified scope-dependent omissions. No PCA is required."),
    "inspect_dataset": TOOL_DEFINITIONS["inspect_dataset"],
    "summarize_dataset": (SummaryArgs, "Save group-wise or overall descriptive statistics and undefined-reason tables for raw/original or current prepared concentrations/testing. Choose observed_only or including_imputed explicitly; no sample tables are returned."),
    "read_result": TOOL_DEFINITIONS["read_result"],
}


class ToolRegistry:
    def __init__(self, state: RunState):
        self.state = state

    def schemas(self, stage=None):
        if self.state is None or self.state.workflow:
            stage = self.state.workflow.stage if self.state else stage or STAGES[0]
            allowed = self.state.workflow.allowed_tools() if self.state else COMMON_TOOLS | STAGE_TOOLS[stage]
            schemas = []
            for name, (model, description) in STAGED_DEFINITIONS.items():
                if name not in allowed:
                    continue
                parameters = model.model_json_schema()
                if name == "make_plot" and stage == STAGES[1]:
                    parameters["properties"]["kind"]["enum"] = ["distributions", "boxplot"]
                schemas.append({"type": "function", "function": {
                    "name": name, "description": description, "parameters": parameters,
                }})
            return schemas
        return [{"type": "function", "function": {
            "name": name, "description": description, "parameters": model.model_json_schema(),
        }} for name, (model, description) in TOOL_DEFINITIONS.items()]

    def execute(self, name: str, arguments: str | dict):
        """Execute current-stage tools or the explicit legacy replay protocol.

        Invalid calls are recorded locally rather than losing their evidence.
        """
        if self.state.workflow:
            return self._execute_staged(name, arguments)
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

    def _execute_staged(self, name, arguments):
        state, workflow = self.state, self.state.workflow
        if state.finished:
            return {"ok": False, "error": "The run is already complete."}
        workflow.activate()
        report = state.evidence.active
        try:
            if name not in workflow.allowed_tools():
                raise ValueError(f"Tool {name!r} is not permitted in stage {workflow.stage}.")
            if isinstance(arguments, str):
                if len(arguments) > 32000:
                    raise ValueError("Tool arguments exceed 32000 characters.")
                arguments = json.loads(arguments)
            parsed = STAGED_DEFINITIONS[name][0].model_validate(arguments)
            state.event("tool_call", tool=name, stage=workflow.stage, arguments=parsed.model_dump())
            state.evidence._journal("tool_started", tool=name, arguments=parsed.model_dump())
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                if name in {"get_workflow_state", "get_stage_context"}:
                    result = workflow.context()
                elif name == "read_stage_report":
                    result = workflow.read_report(parsed.stage_id)
                elif name == "set_workflow_plan":
                    result = workflow.set_workflow_plan(parsed)
                elif name == "inspect_dataset":
                    result = analysis.inspect_dataset(state)
                    workflow.data.diagnostic_ids = [result[key] for key in
                                                    ("diagnostics_artifact", "batch_artifact")
                                                    if result.get(key)]
                elif name == "set_preprocessing_plan":
                    result = workflow.set_preprocessing_plan(parsed)
                elif name == "summarize_dataset":
                    if parsed.dataset_id not in {"raw", workflow.data.dataset_id}:
                        raise ValueError("Summaries require raw data or the current prepared dataset.")
                    if not state.inspected or not workflow.data.diagnostic_ids:
                        raise ValueError("Inspect the dataset before requesting descriptive summaries.")
                    result = summarize_dataset(state, parsed)
                    workflow.data.summary_ids.append(result["summary_id"])
                elif name == "preprocess":
                    if parsed.plan_id != workflow.data.preprocessing_plan_id:
                        raise ValueError("Use the current preparation plan.")
                    result = analysis.preprocess(state, parsed.plan_id)
                    workflow.data.dataset_id = result["dataset_id"]
                    workflow.data.preparation_summary = result
                elif name == "set_univariate_plan":
                    result = workflow.set_univariate_plan(parsed)
                elif name == "compare_groups":
                    if (parsed.dataset_id != workflow.data.dataset_id or
                            parsed.plan_id != workflow.data.univariate_plan_id):
                        raise ValueError("Use the current statistical plan and prepared dataset.")
                    result = analysis.compare_groups(state, parsed.dataset_id, parsed.plan_id)
                    workflow.data.comparison_ids.append(result["comparison_id"])
                elif name == "set_figure_plan":
                    result = workflow.set_figure_plan(parsed)
                elif name == "make_plot":
                    workflow.validate_figure(parsed)
                    if workflow.stage == STAGES[3]:
                        plan = state.figure_plans.get(workflow.data.figure_plan_id)
                        if plan is None or parsed not in plan.figures:
                            raise ValueError("Register this figure in the current figure plan first.")
                    result = make_plot(state, parsed)
                    if workflow.stage == STAGES[3]:
                        workflow.data.figure_ids.append(result["artifact_id"])
                elif name == "read_result":
                    artifact = state.artifacts.get(parsed.artifact_id)
                    if not artifact or artifact.get("individual_samples") or artifact["kind"] in {
                        "input_values", "input_metadata", "concentrations", "testing_values", "pca_values", "pca_scores",
                    }:
                        raise ValueError("Only aggregate result tables may be read; sample tables remain local.")
                    if parsed.artifact_id not in workflow.current_artifacts():
                        raise ValueError("The requested result belongs to superseded evidence.")
                    table = state.read_artifact(parsed.artifact_id)
                    if parsed.features:
                        if "feature" not in table or set(parsed.features) - set(table.feature):
                            raise ValueError("Requested features must exist in the result table.")
                        table = table[table.feature.isin(parsed.features)]
                    result = {"artifact_id": parsed.artifact_id, "matching_rows": len(table),
                              "rows": table.head(parsed.limit).to_dict(orient="records")}
                elif name == "submit_stage_assessment":
                    result = workflow.assess(parsed)
                elif name == "request_revision":
                    result = workflow.request_revision(parsed)
                elif name == "validate_workflow":
                    result = workflow.validate(final=True)
                else:
                    validation = workflow.validate(final=True)
                    if not validation["valid"]:
                        raise ValueError(" ".join(validation["errors"]))
                    state.evidence.check("WORKFLOW_REQUIREMENTS", True, True, passed=True,
                                         message="All required stages must be complete.")
                    result = finish_analysis(state, parsed)
                    for path, kind in [("report.md", "final_report"), ("findings.json", "findings")]:
                        # Preserve immutable report versions if finalization is
                        # interrupted and a later attempt renders another report.
                        canonical = f"final_{report.attempt_id}_{path}"
                        content = (state.output_dir / path).read_bytes()
                        state.evidence.atomic_write(canonical, lambda target: target.write_bytes(content))
                        state.evidence.register_file(canonical, kind)
                    report.agent_assessment = parsed.model_dump()
                    workflow.finish()
            # A transition may have closed the report. Only mutate the active one.
            if state.evidence.active is report:
                report.issues.extend(StageIssue(
                    code="PYTHON_WARNING", severity="warning", source="computed_check",
                    message=f"{item.category.__name__}: {item.message}",
                    evidence_ids=list(report.output_artifact_ids),
                ) for item in captured)
                report.limitations.extend(result.get("notes", []))
                report.limitations.extend(result.get("flags", []))
                if name == "compare_groups":
                    state.evidence.check("SKIPPED_FEATURES", 0, result["features_skipped"],
                                         passed=result["features_skipped"] == 0,
                                         message="Some features could not be tested; inspect the full table.")
                state.evidence.check("TOOL_COMPLETED", True, True, passed=True,
                                     message="Tool execution must complete.")
            envelope = {"ok": True, "result": clean_json(result)}
            state.event("tool_result", tool=name, **envelope)
        except (ValueError, TypeError, KeyError, ValidationError) as error:
            envelope = {"ok": False, "error": str(error)}
            state.event("tool_error", tool=name, arguments=arguments, error=str(error))
            if state.evidence.active:
                state.evidence.active.issues.append(StageIssue(
                    code="TOOL_CALL_REJECTED", severity="warning", source="tool_error",
                    message=str(error), blocking=False,
                ))
        if state.evidence.active:
            state.evidence._save_report()
        state.snapshot()
        return envelope

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
