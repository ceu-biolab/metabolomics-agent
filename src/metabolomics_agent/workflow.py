"""Validated stage routing and aggregate-only handoffs for new CLI runs."""

import hashlib
from importlib.resources import files

from .evidence import digest
from .evidence_models import StageIssue, StageReport
from .workflow_models import StageHandoff, WorkflowState

STAGES = ("01_coordination", "02_preparation", "03_univariate",
          "04_visualization", "05_reporting")
PCA_TOOLS = {"inspect_pca_inputs", "set_pca_plan", "run_pca", "skip_pca"}
COMMON_TOOLS = {"get_workflow_state", "get_stage_context", "read_stage_report", "read_result",
                "submit_stage_assessment", "request_revision"}
STAGE_TOOLS = {
    STAGES[0]: {"set_workflow_plan"},
    STAGES[1]: {"inspect_dataset", "summarize_dataset", "set_preprocessing_plan", "preprocess", "make_plot"},
    STAGES[2]: {"inspect_univariate", "set_univariate_plan", "compare_groups"},
    STAGES[3]: {"set_figure_plan", "make_plot"},
    STAGES[4]: {"validate_workflow", "finish_analysis"},
}


class Workflow:
    def __init__(self, state, saved=None):
        self.state = state
        self.data = WorkflowState.model_validate(saved) if saved else WorkflowState()
        self._attempt = None
        if saved:
            if "workflow_prompts" not in state.run_info:
                raise ValueError("Cannot resume a staged run without its frozen prompt bundle.")
        else:
            directory = files("metabolomics_agent").joinpath("prompts/stages")
            state.run_info["workflow_prompts"] = {
                name: directory.joinpath(f"{name}.md").read_text()
                for name in ("common", *STAGES, *(["03_pca"] if state.run_info.get("pca_version") else []))
            }
        self.bundle = state.run_info["workflow_prompts"]
        expected = {"common", *STAGES, *(["03_pca"] if state.run_info.get("pca_version") else [])}
        if set(self.bundle) != expected:
            raise ValueError("Unsupported staged prompt bundle.")

    @property
    def stage(self):
        return self.data.active_stage

    def context_phase(self):
        """A revision or PCA/univariate block change starts a new model window."""
        block = "pca" if self.in_pca_block() else "univariate"
        return f"{self.data.revision}:{self.stage}:{block}"

    def pca_requirement(self):
        scope = self.state.workflow_plans.get(self.data.plan_id)
        return getattr(scope, "pca_requirement", "disabled") if self.state.run_info.get("pca_version") else "disabled"

    def in_pca_block(self):
        return self.stage == STAGES[2] and self.pca_requirement() != "disabled" and not self.data.pca_reviewed

    def pca_descriptors(self):
        from .pca import descriptor
        return [descriptor(result) for result in self.state.pca_attempts.values()
                if result["plan_id"] == self.data.pca_plan_id] if self.data.pca_plan_id else []

    def plot_descriptors(self):
        return [{key: value for key, value in self.state.plot_results[pid].items()
                 if key in {"plot_id", "status", "kind", "source_id", "selected_count", "pages_expected",
                            "figure_ids", "artifacts", "metadata_artifact_id", "failure_reason"}}
                for pid in self.data.plot_result_ids]

    def allowed_tools(self):
        allowed = COMMON_TOOLS | STAGE_TOOLS[self.stage]
        if not self.state.run_info.get("descriptive_version"):
            allowed = allowed - {"summarize_dataset"}
        if not self.state.run_info.get("univariate_version"):
            allowed = allowed - {"inspect_univariate"}
        if self.in_pca_block():
            allowed = COMMON_TOOLS | PCA_TOOLS
        elif self.stage == STAGES[2] and self.state.workflow_plans.get(self.data.plan_id) and self.state.workflow_plans[self.data.plan_id].analysis_kind == "descriptive":
            allowed = COMMON_TOOLS
        return allowed

    def prompt(self):
        return self.bundle["common"] + "\n\n" + self.bundle["03_pca" if self.in_pca_block() else self.stage] + "\n\n" + (
            "Stage protocol: use submit_stage_assessment to request completion of stages 01–04. "
            "The runtime validates required evidence before advancing. In stage 05, "
            "finish_analysis validates, saves the final report, and closes the workflow. "
            "Only the supplied tool schemas are available; methods mentioned as future "
            "capabilities must not be simulated.\n"
        )

    def current_artifacts(self):
        result = [aid for aid, artifact in self.state.artifacts.items()
                  if artifact["kind"] in {"input_values", "input_metadata"}]
        result.extend(self.data.diagnostic_ids)
        if self.data.pca_diagnostic_id:
            diagnostic = self.state.pca_diagnostics[self.data.pca_diagnostic_id]
            result.extend(diagnostic["artifacts"].values())
            result.append(diagnostic["metadata_artifact_id"])
        for attempt in self.pca_descriptors():
            result.extend(attempt["artifacts"].values())
            result.append(attempt["metadata_artifact_id"])
        if self.data.pca_skip:
            result.append(self.data.pca_skip["artifact_id"])
        for plot in self.plot_descriptors():
            result.extend(plot["artifacts"].values())
            if plot.get("metadata_artifact_id"):
                result.append(plot["metadata_artifact_id"])
            for aid in plot["figure_ids"]:
                result.append(aid)
                result.extend(self.state.artifacts[aid].get("plot_artifact_ids", {}).values())
        for sid in self.data.summary_ids:
            summary = self.state.descriptive_summaries[sid]
            result.extend(summary["artifacts"].values())
            result.append(summary["metadata_artifact_id"])
        if self.data.univariate_diagnostic_id:
            diagnostic = self.state.univariate_diagnostics[self.data.univariate_diagnostic_id]
            result.extend(diagnostic["artifacts"].values())
            result.append(diagnostic["metadata_artifact_id"])
        for uid in self.data.univariate_attempt_ids:
            attempt = self.state.univariate_attempts[uid]
            result.extend(attempt["artifacts"].values())
            result.append(attempt["metadata_artifact_id"])
        if self.data.dataset_id:
            result.extend(self.state.datasets[self.data.dataset_id].artifact_ids.values())
        result.extend(self.state.comparisons[cid]["artifact_id"] for cid in self.data.comparison_ids)
        result.extend(self.data.diagnostic_figure_ids)
        result.extend(self.data.figure_ids)
        for aid in [*self.data.diagnostic_figure_ids, *self.data.figure_ids]:
            figure = self.state.artifacts[aid]
            result.extend(figure.get("plot_artifact_ids", {}).values())
        return list(dict.fromkeys(result))

    def evidence_ids(self):
        ids = set(self.current_artifacts()) | set(self.data.comparison_ids)
        ids.update(self.data.summary_ids)
        ids.update(self.data.pca_ids)
        ids.update(self.data.plot_result_ids)
        if self.data.pca_diagnostic_id:
            ids.add(self.data.pca_diagnostic_id)
        ids.update(self.data.univariate_attempt_ids)
        if self.data.univariate_diagnostic_id:
            ids.add(self.data.univariate_diagnostic_id)
        ids |= {value for value in self.plan_refs().values() if value}
        if self.data.dataset_id:
            ids.add(self.data.dataset_id)
        if self.state.evidence.active:
            ids.update(self.state.evidence.active.output_artifact_ids)
        return ids

    def plan_refs(self):
        return {"workflow": self.data.plan_id, "preparation": self.data.preprocessing_plan_id,
                "univariate": self.data.univariate_plan_id, "figures": self.data.figure_plan_id,
                "pca": self.data.pca_plan_id}

    def summary_descriptors(self):
        fields = {"summary_id", "dataset_id", "scale", "grouping", "value_policy", "include_mode",
                  "all_features", "feature_count", "rows", "preprocessing_plan_id", "transform",
                  "zero_as_missing", "artifacts", "undefined_counts", "metadata_artifact_id"}
        return [{key: value for key, value in self.state.descriptive_summaries[sid].items()
                 if key in fields} for sid in self.data.summary_ids]

    def has_summary(self, dataset_id, scale, policy):
        return any(summary["dataset_id"] == dataset_id and summary["scale"] == scale
                   and summary["value_policy"] == policy and summary["grouping"] == "group"
                   and summary["all_features"] for summary in self.summary_descriptors())

    def univariate_descriptors(self):
        keys = {"attempt_id", "dataset_id", "plan_id", "diagnostic_id", "status", "failure_reason",
                "artifacts", "features_tested", "features_skipped", "tests_executed", "features_in_attempt",
                "features_with_raw_p", "correction_completed", "issue_counts", "metadata_artifact_id"}
        return [{key: value for key, value in self.state.univariate_attempts[uid].items() if key in keys}
                for uid in self.data.univariate_attempt_ids]

    def figure_descriptors(self):
        keys = {"kind", "source_id", "features", "scale", "centering", "scaling",
                "sample_order", "selection_rationale", "caption", "plot_artifact_ids",
                "omitted_values", "clipped_zero_p", "paired_lines", "display_notes",
                "selected_features", "imputed_values", "labels", "group_order", "page", "page_count", "selection"}
        return [{"artifact_id": aid, **{key: value for key, value in self.state.artifacts[aid].items()
                                       if key in keys}} for aid in [*self.data.diagnostic_figure_ids, *self.data.figure_ids]]

    def _reports(self):
        return [StageReport.model_validate_json(self.state.evidence.path(path).read_text())
                for path in self.data.completed_reports.values()]

    def issues(self, include_active=True):
        reports = self._reports()
        if include_active and self.state.evidence.active:
            reports.append(self.state.evidence.active)
        result, seen = [], set()
        for report in reports:
            for issue in report.issues:
                key = (issue.code, issue.message, tuple(issue.evidence_ids))
                if key not in seen:
                    seen.add(key)
                    # Do not propagate sample identifiers, local paths, or arbitrary
                    # nested scope payloads into model-visible stage context.
                    result.append(issue.model_copy(update={
                        "source": "inherited", "scope": {"origin_stage": report.stage_id},
                    }))
        return result

    def limitations(self):
        reports = self._reports()
        if self.state.evidence.active:
            reports.append(self.state.evidence.active)
        return list(dict.fromkeys(item for report in reports for item in report.limitations))

    def aggregates(self):
        values = self.state.raw
        if self.data.dataset_id:
            values = self.state.datasets[self.data.dataset_id].testing
        return {"samples": len(values), "features": len(values.columns),
                "missing_cells": int(values.isna().sum().sum()),
                "group_counts": self.state.metadata[self.state.config.group_column].value_counts().to_dict(),
                **{key: value for key, value in self.data.preparation_summary.items()
                   if key in {"features_retained", "features_removed", "missing_before_imputation",
                              "missing_after_imputation"}}}

    def _incoming_handoff(self):
        if not self.data.handoff_ids:
            return None
        aid = list(self.data.handoff_ids.values())[-1]
        artifact = self.state.evidence.artifacts[aid]
        path = self.state.evidence.path(artifact.path)
        if digest(path) != artifact.sha256:
            raise ValueError("Modified stage handoff.")
        handoff = StageHandoff.model_validate_json(path.read_text())
        if handoff.workflow_revision > self.data.revision:
            raise ValueError("Stage handoff has an invalid workflow revision.")
        if handoff.next_stage != self.stage and not (self.state.finished and handoff.next_stage is None):
            raise ValueError("Stage handoff is addressed to another stage.")
        if any(aid not in self.state.evidence.artifacts for aid in handoff.artifact_ids):
            raise ValueError("Stage handoff references unknown artifacts.")
        if handoff.plan_ids["workflow"] != self.data.plan_id:
            raise ValueError("Stage handoff belongs to another workflow scope.")
        result = handoff.model_dump()
        result["issue_count"] = len(result["issues"])
        result["issues"] = result["issues"][:20]
        result["limitations"] = result["limitations"][:20]
        result["descriptive_summaries"] = result["descriptive_summaries"][:20]
        result["univariate_results"] = result["univariate_results"][:20]
        result["figures"] = result["figures"][:20]
        result["pca_results"] = result["pca_results"][:10]
        result["plot_results"] = result["plot_results"][:12]
        return result

    def context(self, *, include_handoff=True):
        preparation = (self.state.preprocessing_plans[self.data.preprocessing_plan_id].model_dump()
                       if self.data.preprocessing_plan_id else {})
        univariate = (self.state.univariate_plans[self.data.univariate_plan_id].model_dump()
                      if self.data.univariate_plan_id else {})
        return {"protocol": "staged_v1", "stage": self.stage, "revision": self.data.revision,
                "descriptive_version": self.state.run_info.get("descriptive_version", 0),
                "univariate_version": self.state.run_info.get("univariate_version", 0),
                "visualization_version": self.state.run_info.get("visualization_version", 0),
                "pca_version": self.state.run_info.get("pca_version", 0),
                "diagnostic_version": self.state.run_info.get("diagnostic_version", 0),
                "reporting_version": self.state.run_info.get("reporting_version", 0),
                "analysis_block": "pca" if self.in_pca_block() else "univariate",
                "pca_requirement": self.pca_requirement(), "pca_reviewed": self.data.pca_reviewed,
                "pca_diagnostic_id": self.data.pca_diagnostic_id, "pca_results": self.pca_descriptors()[:10],
                "pca_skip": self.data.pca_skip, "plot_results": self.plot_descriptors()[:12],
                "figures": self.figure_descriptors()[:20],
                "figure_count": len(self.data.figure_ids) + len(self.data.diagnostic_figure_ids),
                "univariate_diagnostic_id": self.data.univariate_diagnostic_id,
                "univariate_results": self.univariate_descriptors()[:20],
                "descriptive_summaries": self.summary_descriptors()[:20],
                "descriptive_summary_count": len(self.data.summary_ids),
                "study": self.state.config.model_dump(exclude={"data_path", "metadata_path"}),
                "objective": self.state.run_info.get("request", self.state.config.objective),
                "workflow_plan": (self.state.workflow_plans[self.data.plan_id].model_dump()
                                  if self.data.plan_id else {}),
                "figure_plan": (self.state.figure_plans[self.data.figure_plan_id].model_dump()
                                if self.data.figure_plan_id else {}),
                "plan_ids": self.plan_refs(), "dataset_id": self.data.dataset_id,
                "comparison_ids": self.data.comparison_ids, "figure_ids": self.data.figure_ids,
                "completed_stages": list(self.data.completed_reports), "aggregates": self.aggregates(),
                "preparation": preparation, "univariate": univariate,
                "issues": [issue.model_dump() for issue in self.issues()][:20],
                "issue_count": len(self.issues()), "limitations": self.limitations()[:20],
                "incoming_handoff": self._incoming_handoff() if include_handoff else None,
                "allowed_tools": sorted(self.allowed_tools())}

    def model_context(self):
        """Current evidence for each request; full handoffs remain available by tool."""
        context = self.context(include_handoff=False)
        for duplicate in ("incoming_handoff", "study", "objective", "allowed_tools",
                          "completed_stages"):
            context.pop(duplicate)
        issues = self.issues()
        blocking = [issue for issue in issues if issue.blocking and issue.resolution == "open"]
        other_issues = [issue for issue in issues if not (issue.blocking and issue.resolution == "open")]
        context["issues"] = [issue.model_dump() for issue in
                             (blocking + other_issues)[:max(20, len(blocking))]]
        context["current_comparisons"] = [
            {key: self.state.comparisons[cid][key]
             for key in ("comparison_id", "features_tested", "features_skipped",
                         "significant_features", "test", "correction", "alpha", "purpose")}
            for cid in self.data.comparison_ids
        ]
        if self.data.dataset_id:
            context["current_dataset_artifacts"] = self.state.datasets[
                self.data.dataset_id].artifact_ids
        return context

    def activate(self):
        if self._attempt is not None or self.state.finished:
            return
        self.state.evidence.validate()
        context = self.context()
        prompt = self.prompt()
        self._attempt = self.state.evidence.attempt(
            self.stage, "workflow_stage", inputs=self.current_artifacts(),
            parameters={"protocol": "staged_v1", "revision": self.data.revision}, context=context,
            prompt_snapshot={"protocol": "staged_v1", "system": prompt,
                             "sha256": hashlib.sha256(prompt.encode()).hexdigest()},
        )
        report = self._attempt.__enter__()
        report.issues.extend(self.issues(include_active=False))
        report.limitations.extend(self.limitations())
        self.data.current_attempt = f"stages/{self.stage}/{report.attempt_id}/stage_report.json"
        self.state.snapshot()

    def _register_plan(self, prefix, plan, collection):
        pid = f"{prefix}{len(collection) + 1:03d}"
        self.state.write_json(f"{pid}_plan.json", plan.model_dump())
        self.state.evidence.register_file(f"{pid}_plan.json", f"{prefix}_plan", plan_id=pid)
        collection[pid] = plan
        return pid

    def set_workflow_plan(self, plan):
        if self.state.config.analysis_scope == "descriptive" and plan.analysis_kind != "descriptive":
            raise ValueError("A descriptive loading scope cannot perform group-comparison inference.")
        if self.data.plan_id:
            raise ValueError("The workflow scope is already registered; start a new run to change scope.")
        self.data.plan_id = self._register_plan("w", plan, self.state.workflow_plans)
        return {"plan_id": self.data.plan_id, "plan": plan.model_dump()}

    def set_preprocessing_plan(self, plan):
        if not self.state.inspected or not self.data.diagnostic_ids:
            raise ValueError("Inspect the dataset before selecting preparation.")
        if self.state.run_info.get("descriptive_version") and not self.has_summary(
            "raw", "original", "observed_only"
        ):
            raise ValueError("Summarize all original features by group with observed_only before planning preparation.")
        if self.data.preprocessing_plan_id:
            raise ValueError("Request a preparation revision before replacing its plan.")
        self.data.preprocessing_plan_id = self._register_plan("pp", plan, self.state.preprocessing_plans)
        return {"plan_id": self.data.preprocessing_plan_id, "plan": plan.model_dump()}

    def set_univariate_plan(self, plan):
        if plan.dataset_id != self.data.dataset_id:
            raise ValueError("The statistical plan must use the current prepared dataset.")
        if self.data.univariate_plan_id:
            raise ValueError("Request a univariate revision before replacing its plan.")
        if self.state.run_info.get("univariate_version"):
            if not self.data.univariate_diagnostic_id or plan.diagnostic_id != self.data.univariate_diagnostic_id:
                raise ValueError("Inspect the current univariate inputs and reference that diagnostic ID before planning.")
            diagnostic = self.state.univariate_diagnostics[plan.diagnostic_id]
            if diagnostic["dataset_id"] != plan.dataset_id:
                raise ValueError("The univariate diagnostic belongs to another prepared dataset.")
        paired = plan.test in {"paired_t", "wilcoxon"}
        if paired != (self.state.config.design == "paired"):
            raise ValueError("The statistical test must match the declared pairing design.")
        if self.state.comparisons or any(item.get("tests_executed", 0) for item in self.state.univariate_attempts.values()):
            plan = plan.model_copy(update={"purpose": "exploratory"})
        self.data.univariate_plan_id = self._register_plan("u", plan, self.state.univariate_plans)
        return {"plan_id": self.data.univariate_plan_id, "plan": plan.model_dump()}

    def set_pca_plan(self, plan):
        if self.data.pca_plan_id:
            raise ValueError("Request a statistical-stage revision before replacing the PCA plan.")
        if plan.dataset_id != self.data.dataset_id or plan.diagnostic_id != self.data.pca_diagnostic_id:
            raise ValueError("Use the current PCA diagnostic and prepared dataset.")
        diagnostic = self.state.pca_diagnostics[plan.diagnostic_id]
        if (plan.scale != diagnostic["scale"] or plan.features != diagnostic["requested_features"]
                or not diagnostic["viable"] or plan.components > diagnostic["max_components"]):
            raise ValueError("PCA plan must match viable inspected inputs and component limits.")
        self.data.pca_plan_id = self._register_plan("ppc", plan, self.state.pca_plans)
        return {"plan_id": self.data.pca_plan_id, "plan": plan.model_dump()}

    def skip_pca(self, rationale):
        if self.pca_requirement() != "optional" or self.data.pca_ids or self.data.pca_skip:
            raise ValueError("Only an unresolved optional PCA can be skipped explicitly.")
        filename = f"pca_skip_r{self.data.revision}.json"
        self.state.write_json(filename, {"status": "skipped", "rationale": rationale})
        artifact = self.state.evidence.register_file(filename, "pca_skip")
        self.data.pca_skip = {"status": "skipped", "rationale": rationale, "artifact_id": artifact.id}
        return self.data.pca_skip

    def set_figure_plan(self, plan):
        if self.data.figure_plan_id:
            raise ValueError("Request a visualization revision before replacing its plan.")
        required = self.state.workflow_plans[self.data.plan_id].required_figures
        if not set(required) <= {figure.kind for figure in plan.figures}:
            raise ValueError("The figure plan does not cover the workflow requirements.")
        for figure in plan.figures:
            self.validate_figure(figure)
        self.data.figure_plan_id = self._register_plan("f", plan, self.state.figure_plans)
        return {"plan_id": self.data.figure_plan_id, "plan": plan.model_dump()}

    def validate_figure(self, args):
        kinds = {"distributions", "boxplot", "volcano"}
        if self.state.run_info.get("visualization_version"):
            kinds.add("heatmap")
        if self.state.run_info.get("pca_version"):
            kinds.update({"pca_scores", "pca_scree", "pca_loadings"})
            if self.state.run_info.get("visualization_version", 0) >= 3:
                kinds.add("pca_loadings_scatter")
        if args.kind not in kinds:
            raise ValueError("Unsupported figure for this workflow version.")
        if self.stage == STAGES[1]:
            if args.kind not in {"distributions", "boxplot"} or args.source_id not in {"raw", self.data.dataset_id}:
                raise ValueError("Preparation permits only diagnostic distributions and boxplots.")
        elif args.kind.startswith("pca_"):
            if args.source_id not in self.data.pca_ids or self.state.pcas[args.source_id]["status"] != "completed":
                raise ValueError("PCA figures require a current completed PCA result.")
        elif args.kind == "volcano":
            if args.source_id not in self.data.comparison_ids:
                raise ValueError("Volcano plots require a current comparison.")
        elif args.source_id != self.data.dataset_id:
            raise ValueError("Presentation figures require the current prepared dataset.")
        if getattr(args, "selection_mode", "explicit") == "significant":
            comparison = self.state.comparisons.get(args.comparison_id)
            if (args.comparison_id not in self.data.comparison_ids or not comparison
                    or comparison["dataset_id"] != args.source_id):
                raise ValueError("Significance selection requires a current comparison from the same dataset.")

    def validate(self, *, final=False):
        self.state.evidence.validate()
        errors = []
        data = self.data
        if any(issue.blocking and issue.resolution == "open" for issue in self.issues()):
            errors.append("An unresolved blocking issue requires a new reviewed attempt.")
        stage = STAGES[4] if final else self.stage
        if final and self.stage != STAGES[4]:
            errors.append("Finalization is permitted only in the reporting stage.")
        if stage == STAGES[0]:
            if not data.plan_id:
                errors.append("Register the workflow scope first.")
        elif stage == STAGES[1]:
            if not data.diagnostic_ids or not data.preprocessing_plan_id or not data.dataset_id:
                errors.append("Inspection and an executed current preparation plan are required.")
            elif self.state.datasets[data.dataset_id].plan_id != data.preprocessing_plan_id:
                errors.append("The prepared dataset belongs to a superseded plan.")
            if self.state.run_info.get("descriptive_version"):
                for dataset_id, scale, policy in [
                    ("raw", "original", "observed_only"),
                    (data.dataset_id, "concentrations", "including_imputed"),
                    (data.dataset_id, "testing", "including_imputed"),
                ]:
                    if not self.has_summary(dataset_id, scale, policy):
                        errors.append(f"Missing full group-wise summary: {dataset_id}/{scale}/{policy}.")
        elif stage == STAGES[2]:
            if self.in_pca_block():
                if not data.pca_skip and not any(result["status"] == "completed" for result in self.pca_descriptors()):
                    errors.append("Complete the current PCA plan or explicitly skip an optional PCA.")
            elif self.state.workflow_plans[data.plan_id].analysis_kind == "two_group":
                if not data.univariate_plan_id or not data.comparison_ids:
                    errors.append("Execute the current univariate plan before advancing.")
                elif not any(self.state.comparisons[cid].get("univariate_plan_id") == data.univariate_plan_id
                             for cid in data.comparison_ids):
                    errors.append("The current statistical plan has not been tested.")
                if self.state.run_info.get("univariate_version") and not any(
                    record["status"] == "completed" and record["plan_id"] == data.univariate_plan_id
                    and record["correction_completed"] for record in self.univariate_descriptors()
                ):
                    errors.append("The current plan requires a complete univariate audit and multiplicity correction.")
        elif stage == STAGES[3]:
            if not data.figure_plan_id:
                errors.append("Register a figure plan first.")
            else:
                plan = self.state.figure_plans[data.figure_plan_id]
                for figure in plan.figures:
                    if self.state.run_info.get("visualization_version", 0) >= 2 and figure.kind == "heatmap":
                        from .heatmaps import complete_plot
                        if not any(complete_plot(self.state, self.state.plot_results[pid], figure, data.figure_ids)
                                   for pid in data.plot_result_ids):
                            errors.append(f"Missing complete heatmap selection/pages: {figure.source_id}.")
                        continue
                    if not any(self.state.artifacts[aid]["kind"] == figure.kind
                               and self.state.artifacts[aid].get("source_id") == figure.source_id
                               and self.state.artifacts[aid].get("features", []) == figure.features
                               and (not self.state.run_info.get("visualization_version")
                                    or self.state.artifacts[aid].get("figure_spec") == figure.model_dump())
                               for aid in data.figure_ids):
                        errors.append(f"Missing planned figure: {figure.kind} / {figure.source_id}.")
        else:
            if set(data.completed_reports) != set(STAGES[:4]):
                errors.append("All preceding stages must be completed or validly skipped.")
        return {"valid": not errors, "stage": stage, "errors": errors}

    def read_report(self, stage_id):
        paths = [path for path in self.state.evidence.attempts
                 if path.startswith(f"stages/{stage_id}/")]
        if not paths:
            raise ValueError("No report exists for that stage.")
        report = StageReport.model_validate_json(self.state.evidence.path(paths[-1]).read_text())
        return {"stage_id": stage_id, "attempt_id": report.attempt_id, "status": report.status,
                "input_artifact_ids": report.input_artifact_ids,
                "output_artifact_ids": report.output_artifact_ids,
                "checks": report.checks[:20],
                "issues": [issue.model_copy(update={"scope": {}}).model_dump()
                           for issue in report.issues[:20]],
                "issue_count": len(report.issues), "limitations": report.limitations[:20]}

    def _close(self, *, status=None, advance=True):
        report = self.state.evidence.active
        if status:
            report.status = status
        index = STAGES.index(self.stage)
        next_stage = STAGES[index + 1] if index < 4 and advance else None
        preparation = (self.state.preprocessing_plans[self.data.preprocessing_plan_id].model_dump()
                       if self.data.preprocessing_plan_id else {})
        handoff = StageHandoff(
            workflow_revision=self.data.revision, stage_id=self.stage, attempt_id=report.attempt_id,
            next_stage=next_stage,
            study=self.state.config.model_dump(exclude={"data_path", "metadata_path"}),
            workflow_scope=(self.state.workflow_plans[self.data.plan_id].model_dump()
                            if self.data.plan_id else {}),
            plan_ids=self.plan_refs(), dataset_id=self.data.dataset_id,
            comparison_ids=self.data.comparison_ids, figure_ids=self.data.figure_ids,
            artifact_ids=self.current_artifacts(), aggregates=self.aggregates(), preparation=preparation,
            issues=self.issues(), limitations=self.limitations(),
            descriptive_summaries=self.summary_descriptors(),
            univariate_results=self.univariate_descriptors(),
            figures=self.figure_descriptors(), pca_results=self.pca_descriptors(),
            plot_results=self.plot_descriptors(),
        )
        relative = f"stages/{self.stage}/{report.attempt_id}/handoff.json"
        self.state.write_json(relative, handoff.model_dump())
        artifact = self.state.evidence.register_file(relative, "stage_handoff",
                                                      parent_ids=self.current_artifacts())
        path = self.data.current_attempt
        closed_stage = self.stage
        self._attempt.__exit__(None, None, None)
        self._attempt = None
        self.data.current_attempt = None
        if advance:
            self.data.completed_reports[closed_stage] = path
            self.data.handoff_ids[closed_stage] = artifact.id
            if next_stage:
                self.data.active_stage = next_stage
        self.state.snapshot()

    def assess(self, assessment):
        for finding in assessment.findings:
            if not set(finding.evidence_ids) <= self.evidence_ids():
                raise ValueError("Assessment references unknown or superseded evidence.")
        report = self.state.evidence.active
        report.agent_assessment = assessment.model_dump()
        report.limitations.extend(assessment.limitations)
        report.unchecked_assumptions.extend(assessment.unchecked_assumptions)
        report.issues.extend(StageIssue(code="AGENT_CONCERN", severity="warning",
                                         source="agent_assessment", message=message)
                             for message in assessment.concerns)
        if assessment.needs_input:
            self._close(status="blocked", advance=False)
            self.state.status = "needs_input"
            return {"status": "needs_input", "summary": assessment.summary}
        validation = self.validate()
        if self.in_pca_block() and validation["valid"]:
            filename = f"stages/{self.stage}/{report.attempt_id}/pca_assessment.json"
            self.state.write_json(filename, assessment.model_dump())
            self.state.evidence.register_file(filename, "pca_assessment", parent_ids=self.current_artifacts())
            self.data.pca_reviewed = True
            if self.state.workflow_plans[self.data.plan_id].analysis_kind == "two_group":
                return {"completed_block": "pca", "next_block": "univariate", "stage": self.stage}
            report.limitations.append("Univariate testing omitted by the registered descriptive objective.")
        if not validation["valid"]:
            raise ValueError(" ".join(validation["errors"]))
        if self.stage == STAGES[4]:
            return {"assessment_recorded": True, "next_action": "finish_analysis"}
        self._close()
        while ((self.stage == STAGES[2] and
                self.state.workflow_plans[self.data.plan_id].analysis_kind == "descriptive"
                and self.pca_requirement() == "disabled") or
               (self.stage == STAGES[3] and
                not self.state.workflow_plans[self.data.plan_id].required_figures)):
            self.activate()
            self.state.evidence.active.limitations.append("Stage omitted by the registered workflow scope.")
            self._close(status="skipped")
        return {"completed": True, "next_stage": self.stage}

    def request_revision(self, args):
        if STAGES.index(args.target) > STAGES.index(self.stage):
            raise ValueError("A revision cannot skip forward to a future stage.")
        if not self.data.plan_id:
            raise ValueError("Register the workflow scope before requesting a revision.")
        if args.target == STAGES[2] and not self.data.dataset_id:
            raise ValueError("Univariate revision requires a prepared dataset.")
        if args.target == STAGES[3] and not self.data.dataset_id:
            raise ValueError("Visualization revision requires a prepared dataset.")
        old = self.data.current_attempt
        self.state.evidence.active.limitations.append(f"Superseded by revision: {args.rationale}")
        self._close(status="superseded", advance=False)
        self.data.revisions.append({"from_attempt": old, "target": args.target,
                                    "rationale": args.rationale})
        self.data.revision += 1
        index = STAGES.index(args.target)
        for stage in STAGES[index:]:
            self.data.completed_reports.pop(stage, None)
            self.data.handoff_ids.pop(stage, None)
        if index <= 1:
            self.data.preprocessing_plan_id = None
            self.data.diagnostic_ids = []
            self.data.diagnostic_figure_ids = []
            self.data.summary_ids = []
            self.data.dataset_id = None
            self.data.preparation_summary = {}
        if index <= 2:
            self.data.pca_plan_id = None
            self.data.pca_diagnostic_id = None
            self.data.pca_ids = []
            self.data.pca_reviewed = False
            self.data.pca_skip = {}
            self.data.univariate_plan_id = None
            self.data.univariate_diagnostic_id = None
            self.data.univariate_attempt_ids = []
            self.data.comparison_ids = []
        self.data.figure_plan_id = None
        self.data.figure_ids = []
        self.data.plot_result_ids = []
        self.data.active_stage = args.target
        return {"revision": self.data.revision, "stage": self.stage,
                "purpose": "exploratory" if self.state.comparisons else "not_yet_tested"}

    def interrupt(self, reason, status="interrupted"):
        if self._attempt is not None:
            report = self.state.evidence.active
            report.issues.append(StageIssue(code="WORKFLOW_STOPPED", severity="error",
                                             source="tool_error", message=reason, blocking=True))
            self._close(status=status, advance=False)

    def finish(self):
        self.state.finished = True
        self.state.status = "complete"
        try:
            # The closing snapshot commits stage completion and run completion
            # together, rather than leaving an unresumable intermediate state.
            self._close()
        except BaseException:
            self.state.finished = False
            self.state.status = "failed"
            raise
