from pathlib import Path

from .evidence_models import StageReport
from .models import FinishArgs
from .state import RunState, dumps


def finish_analysis(state: RunState, args: FinishArgs):
    if state.workflow:
        validation = state.workflow.validate(final=True)
        if not validation["valid"]:
            raise ValueError(" ".join(validation["errors"]))
        comparisons = {cid: state.comparisons[cid] for cid in state.workflow.data.comparison_ids}
    else:
        comparisons = state.comparisons
    if not state.workflow and (not state.comparisons or not state.pcas):
        raise ValueError("Complete at least one group comparison and PCA before finishing.")
    kinds = {a["kind"] for a in state.artifacts.values()}
    if not state.workflow and not {"volcano", "pca", "distributions"} <= kinds:
        raise ValueError("Generate volcano, PCA, and distribution plots before finishing.")
    if not state.workflow and not any(a["kind"] == "distributions" and a.get("source_id") in state.datasets
                                      for a in state.artifacts.values()):
        raise ValueError("Generate a distributions plot with a prepared dataset ID (d...) "
                         "to compare before and after preprocessing; raw-only plots are insufficient.")
    if not state.workflow:
        latest_plan = next(reversed(state.plans))
        if not any(c["plan_id"] == latest_plan for c in state.comparisons.values()):
            raise ValueError("The latest analysis plan has not been tested. Complete it before finishing.")
    known = (state.workflow.evidence_ids() if state.workflow else
             set(state.artifacts) | set(state.plans) | set(state.comparisons) | set(state.pcas))
    unknown = {aid for finding in args.findings for aid in finding.evidence_ids if aid not in known}
    if unknown:
        raise ValueError(f"Unknown evidence IDs: {sorted(unknown)}")
    cfg = state.config
    audited_figures = bool(state.workflow and state.run_info.get("visualization_version"))
    scope = state.workflow_plans[state.workflow.data.plan_id] if audited_figures else None
    objective = scope.objective if scope else cfg.objective
    lines = ["# Metabolomics analysis report", "", objective, "",
             f"Comparison: **{cfg.treatment_group} relative to {cfg.reference_group}**. "
             f"Study design: {cfg.design}. Input scale: {cfg.input_scale}.", "",
             f"Normalization metadata: {cfg.normalization_status}", "",
             f"Selected samples: {len(state.raw)}. Input features: {len(state.raw.columns)}.", "",
             "## Computed results", "",
             "These summaries are generated directly from saved numerical outputs.", ""]
    if audited_figures:
        lines += ["## Objective coverage", "",
                  f"Registered scope: **{scope.analysis_kind}**. Required figures: "
                  f"{', '.join(scope.required_figures) or 'none'}.", ""]
        if scope.analysis_kind == "descriptive":
            lines += ["Univariate testing and comparison-derived volcano plots were omitted by the registered descriptive objective.", ""]
        if state.workflow.data.pca_ids:
            lines += ["Exploratory PCA was performed. No batch correction or QC-based correction was performed.", ""]
        else:
            lines += ["No multivariate analysis, batch correction, or QC-based correction was performed.", ""]
        for stage, path in state.workflow.data.completed_reports.items():
            stage_report = StageReport.model_validate_json(state.evidence.path(path).read_text())
            lines += [f"- {stage}: **{stage_report.status}**; [stage report]({path.replace('.json', '.md')})."]
        if not scope.required_figures:
            lines += ["", "Visualization was omitted by the registered figure requirements.", ""]
    if state.excluded_groups:
        lines += [f"Groups outside the configured comparison were excluded: {state.excluded_groups}.", ""]
    if audited_figures and not comparisons:
        lines += ["No group-comparison inference was performed. Descriptive results follow below.", ""]
    for cid, result in comparisons.items():
        artifact = state.artifacts[result["artifact_id"]]
        lines += [f"### {cid}: {result['purpose']}", "",
                  f"Plan: {result['plan_id']}; dataset: {result['dataset_id']}. "
                  f"Test: {result['test']}; correction: {result['correction']}; alpha: {result['alpha']}.", "",
                  f"Tested {result['features_tested']} features; skipped {result['features_skipped']}; "
                  f"{result['significant_features']} have adjusted p < {result['alpha']}.", "",
                  f"[Full numerical results ({result['artifact_id']})]({artifact['path']})", "",
                  "| Feature | log2 fold change | p | Adjusted p |",
                  "|---|---:|---:|---:|"]
        for row in result["top_features"]:
            name = row["feature"].replace("|", "\\|").replace("\n", " ")
            fc = "undefined" if row["log2_fold_change"] is None else f"{row['log2_fold_change']:.4g}"
            p_value = "undefined" if row["p_value"] is None else f"{row['p_value']:.4g}"
            adjusted = "undefined" if row["adjusted_p_value"] is None else f"{row['adjusted_p_value']:.4g}"
            lines.append(f"| {name} | {fc} | {p_value} | {adjusted} |")
        if audited_figures:
            lines += ["", "Effects and uncertainty on the registered test scale:", "",
                      "| Feature | Mean difference | Hedges' g | Rank-biserial | CI low | CI high |",
                      "|---|---:|---:|---:|---:|---:|"]
            for row in result["top_features"]:
                name = row["feature"].replace("|", "\\|").replace("\n", " ")
                fields = [row.get(key) for key in ["mean_difference_test_scale", "hedges_g", "rank_biserial", "ci_low_test_scale", "ci_high_test_scale"]]
                values = ["undefined" if value is None else f"{value:.4g}" for value in fields]
                lines.append(f"| {name} | " + " | ".join(values) + " |")
            lines += ["", "Undefined/unsupported effects and intervals are explained in the univariate issue tables.", ""]
        lines += ["", "Feature-wise confidence intervals are unadjusted. Statistical significance "
                  "does not by itself establish biological relevance.", ""]
    if state.workflow and state.workflow.data.summary_ids:
        lines += ["## Descriptive statistics", "",
                  "Full tables identify group, scale, observed and imputed counts. Undefined "
                  "statistics have missing values and explicit reasons in a separate table. "
                  "CV is SD/mean (a ratio, not a percentage) and is not reported on log scales.", ""]
        for sid in state.workflow.data.summary_ids:
            summary = state.descriptive_summaries[sid]
            table = state.artifacts[summary["artifacts"]["statistics"]]["path"]
            reasons = state.artifacts[summary["artifacts"]["undefined_reasons"]]["path"]
            lines += [f"- **{sid}**: {summary['dataset_id']}, {summary['scale']}, "
                      f"{summary['grouping']}, {summary['value_policy']}. "
                      f"[Statistics]({table}); [undefined reasons]({reasons}); "
                      f"[methods and metadata]({sid}_summary.json)."]
    if state.workflow and state.workflow.data.univariate_attempt_ids:
        lines += ["", "## Univariate audit", "",
                  "Audit tables distinguish observed measurements, imputed values, used samples, "
                  "and complete pairs. The issue tables explain skipped tests and undefined effects "
                  "or intervals. Uncorrected tables are checkpoints, not discovery results.", ""]
        for uid in state.workflow.data.univariate_attempt_ids:
            attempt = state.univariate_attempts[uid]
            audit = state.artifacts[attempt["artifacts"]["audit"]]["path"]
            issues = state.artifacts[attempt["artifacts"]["issues"]]["path"]
            raw = state.artifacts[attempt["artifacts"]["raw_tests"]]["path"]
            lines += [f"- **{uid} ({attempt['status']})**: [audit]({audit}), "
                      f"[issues]({issues}), [uncorrected checkpoint]({raw}), "
                      f"[methods and correction family]({uid}_univariate.json)."]
    if state.workflow and state.run_info.get("pca_version"):
        lines += ["", "## Exploratory PCA", ""]
        requirement = state.workflow.pca_requirement()
        lines += [f"Registered PCA requirement: {requirement}.", ""]
        if state.workflow.data.pca_skip:
            skip = state.workflow.data.pca_skip
            artifact = state.evidence.artifacts[skip["artifact_id"]]
            lines += [f"PCA skipped: {skip['rationale']} [Recorded omission]({artifact.path}).", ""]
        for result in state.workflow.pca_descriptors():
            lines += [f"### {result['pca_id']}: {result['status']}", "",
                      f"Dataset {result['dataset_id']}; plan {result['plan_id']}; "
                      f"source scale {result['scale']}; source transform {result['transform']}; "
                      f"mean-centered; scaling {result['scaling']} with SD ddof={result['scaling_ddof']}. "
                      f"Samples: {result['samples']}; features: {result['feature_count']}; "
                      f"imputed input cells: {result['imputed_cells']}.", ""]
            if result["status"] == "completed":
                ratios = ", ".join(f"PC{i+1}: {value:.2%}" for i, value in enumerate(result["explained_variance_ratio"]))
                lines += [f"Explained variance: {ratios}. Unrepresented remainder: {result['remaining_variance_ratio']:.2%}.", "",
                          "Full SVD; whitening disabled. Loadings are unit eigenvector weights, not automatically variable-component correlations.", ""]
            for key, aid in result["artifacts"].items():
                lines += [f"- [{key}: {aid}]({state.evidence.artifacts[aid].path})"]
            lines += ["", *result["notes"], ""]
            if result.get("failure_reason"):
                lines += [f"Failure: {result['failure_reason']}.", ""]
        if state.workflow.data.pca_plan_id:
            pid = state.workflow.data.pca_plan_id
            lines += [f"[Complete PCA plan]({pid}_plan.json).", ""]
    if state.workflow and state.workflow.data.plot_result_ids:
        lines += ["## Heatmap selection and page coverage", ""]
        for gid in state.workflow.data.plot_result_ids:
            plot = state.plot_results[gid]
            lines += [f"- **{gid} ({plot['status']})**: {plot['selected_count']} variables selected; "
                      f"{len(plot['figure_ids'])}/{plot['pages_expected']} pages generated."]
            for key, aid in plot["artifacts"].items():
                lines += [f"  [{key}]({state.evidence.artifacts[aid].path})"]
            if plot["figure_spec"]["selection_mode"] == "significant":
                lines += ["  Selection uses valid adjusted p-values below the comparison's registered alpha. "
                          "The original correction family is preserved. Separation is descriptive and not independent validation."]
    lines += ["## Analysis decisions", ""]
    plans = state.preprocessing_plans if state.workflow else state.plans
    if audited_figures:
        pid = state.workflow.data.preprocessing_plan_id
        plans = {pid: state.preprocessing_plans[pid]}
        dataset = state.datasets[state.workflow.data.dataset_id]
        lines += [f"Current prepared dataset: {state.workflow.data.dataset_id}. "
                  "Concentration values include selected imputation/normalization; test values "
                  "include the registered transformation. Display-only heatmap scaling does not change either representation.", ""]
        for key, aid in dataset.artifact_ids.items():
            lines += [f"- [{key}: {aid}]({state.artifacts[aid]['path']})"]
    for pid, plan in plans.items():
        purpose = getattr(plan, "purpose", "preparation")
        lines += [f"- **{pid} ({purpose})**: {plan.rationale}",
                  f"  [Complete parameters]({pid}_plan.json)"]
    if state.workflow:
        for pid, plan in state.univariate_plans.items():
            if audited_figures and pid != state.workflow.data.univariate_plan_id:
                continue
            lines += [f"- **{pid} ({plan.purpose})**: {plan.rationale}",
                      f"  [Complete parameters]({pid}_plan.json)"]
    lines += ["", "## Agent interpretation", "",
              "Evidence IDs are checked for existence. Interpretations require scientific review; "
              "the harness does not verify every natural-language claim.", ""]
    for finding in args.findings:
        lines.append(f"- {finding.text} (evidence: {', '.join(finding.evidence_ids)})")
    lines += ["", "## Limitations", ""]
    limitations = [*args.limitations,
                  "Analyses added after an initial test are exploratory. Multiplicity correction "
                  "is applied within each comparison, not across adaptively selected reruns.",
                  "No covariate adjustment, batch correction, pathway analysis, or causal inference "
                  "is implemented in this prototype."]
    if any(p.imputation != "none" for p in plans.values()):
        limitations.append("Imputation uncertainty is not propagated into p-values or confidence intervals.")
    if state.workflow:
        limitations.extend(state.workflow.limitations())
        limitations.extend(issue.message for issue in state.workflow.issues()
                           if issue.resolution == "open")
        limitations = list(dict.fromkeys(limitations))
    lines += [f"- {item}" for item in limitations]
    if audited_figures:
        lines += ["", "## Stage issues and unchecked assumptions", "",
                  "Open inherited issues remain limitations; a completed run does not establish that all scientific assumptions were verified.", ""]
        for issue in state.workflow.issues():
            refs = []
            for aid in issue.evidence_ids:
                artifact = state.evidence.artifacts.get(aid)
                refs.append(f"[{aid}]({artifact.path})" if artifact else aid)
            lines += [f"- **{issue.code}** ({issue.severity}, {issue.resolution}): {issue.message} "
                      f"{' '.join(refs)}"]
        reports = state.workflow._reports()
        if state.evidence.active:
            reports.append(state.evidence.active)
        assumptions = list(dict.fromkeys(item for report in reports for item in report.unchecked_assumptions))
        lines += [f"- Unchecked assumption: {item}" for item in assumptions]
        if not state.workflow.issues() and not assumptions:
            lines += ["No additional issues or unchecked assumptions were recorded in the stage reports."]
        if state.workflow.data.revisions:
            lines += ["", "Historical attempts are preserved locally. Current result and figure selections exclude superseded attempts.", ""]
            current_paths = set(state.workflow.data.completed_reports.values()) | {state.workflow.data.current_attempt}
            for path in state.evidence.attempts:
                if path.startswith("stages/0") and path not in current_paths:
                    historical = StageReport.model_validate_json(state.evidence.path(path).read_text())
                    lines += [f"- Historical [{historical.stage_id}: {historical.attempt_id}]({path.replace('.json', '.md')}): {historical.status}."]
    if not state.workflow:
        lines += ["", "## PCA", ""]
    for pid, result in (state.pcas.items() if not state.workflow else []):
        percentages = ", ".join(f"PC{i+1}: {v:.1%}" for i, v in enumerate(result["explained_variance_ratio"]))
        lines += [f"- {pid}, dataset {result['dataset_id']}: {percentages}. "]
    lines += ["", "## Figures", ""]
    for aid, artifact in state.artifacts.items():
        if state.workflow:
            selected = [*state.workflow.data.figure_ids]
            if audited_figures:
                selected.extend(state.workflow.data.diagnostic_figure_ids)
            if aid not in selected:
                continue
        if artifact["path"].endswith(".png"):
            lines += [f"### {aid}: {artifact['kind']} ({artifact['source_id']})", "",
                      f"![{artifact['kind']}]({artifact['path']})", ""]
            if audited_figures:
                if artifact.get("page_count", 1) > 1:
                    lines += [f"Page {artifact['page']} of {artifact['page_count']}.", ""]
                lines += [artifact["caption"], "",
                          f"Scale: {artifact['scale']}; centering: {artifact['centering']}; "
                          f"scaling: {artifact['scaling']}; sample order: {artifact['sample_order']}. "
                          f"Omitted values/points: {artifact['omitted_values']}; "
                          f"zero adjusted p-values clipped for display: {artifact['clipped_zero_p']}; "
                          f"complete pair lines: {artifact['paired_lines']}.", ""]
                for key, ref in artifact["plot_artifact_ids"].items():
                    saved = state.evidence.artifacts[ref]
                    lines += [f"- [{key}: {ref}]({saved.path})"]
                lines += [f"- {note}" for note in artifact["display_notes"]]
                lines += [""]
    lines += ["## Prompt and inputs received", "", "### Initial user request", "",
              "```text", state.run_info.get("request", cfg.objective), "```", "",
              "### Study configuration supplied to the model", "", "```json",
              dumps(cfg.model_dump(exclude={"data_path", "metadata_path"})), "```", "",
              "### Local input data", "",
              "The harness reads these input tables locally. The model receives the request, "
              "study configuration, system instructions, tool definitions, and tool-returned "
              "aggregate diagnostics/results. Individual sample tables are not sent to the model.", ""]
    for artifact in state.artifacts.values():
        if artifact["kind"] in {"input_values", "input_metadata"}:
            columns = state.raw.columns if artifact["kind"] == "input_values" else state.metadata.columns
            description = (f"{len(columns)} abundance features" if artifact["kind"] == "input_values"
                           else "columns: " + ", ".join([cfg.sample_column, *columns]))
            lines += [f"- [{artifact['kind']}]({artifact['path']}): "
                      f"{artifact['rows']} samples; {description}."]
    lines += ["", "Original input file hashes (SHA-256):", ""]
    for source in state.run_info.get("inputs", []):
        lines += [f"- {Path(source['path']).name}: `{source['sha256']}`"]
    if (state.output_dir / "model_input.json").exists():
        lines += ["", "[Exact initial messages and tool definitions](model_input.json)."]
    if state.workflow:
        lines += ["", "## Stage reports", ""]
        for stage, path in state.workflow.data.completed_reports.items():
            lines += [f"- [{stage}]({path.replace('.json', '.md')})"]
        current = state.workflow.data.current_attempt
        lines += [f"- [05_reporting]({current.replace('.json', '.md')})", "",
                  "[Workflow state](workflow_state.json). Per-request contexts are saved under requests/."]
    lines += [""]
    lines += ["## Reproducibility", "",
              "See [run metadata](run.json), [run state](state.json), "
              "[tool and model events](events.jsonl), and [conversation](conversation.json). "
              "Input hashes, package versions, plans, and numerical input snapshots are saved.", "",
              "Methods source: [Idkowiak et al., Nature Communications (2025)]"
              "(https://doi.org/10.1038/s41467-025-63751-1). "
              "This run is not an exact GitBook reproduction unless explicitly configured as one.", ""]
    state.evidence.atomic_write("report.md", lambda path: path.write_text("\n".join(lines)))
    state.write_json("findings.json", args.model_dump())
    if not state.workflow:
        state.finished = True
        state.status = "complete"
    incomplete = state.output_dir / "incomplete.md"
    if incomplete.exists():
        incomplete.unlink()
    return {"status": "complete", "report": "report.md", "comparisons": list(comparisons),
            "artifacts": len(state.artifacts)}


def write_partial_report(state):
    """Keep a linked, explicitly incomplete account without declaring completion."""
    from uuid import uuid4

    workflow = state.workflow
    lines = ["# Analysis did not finish", "", f"Status: **{state.status}**.", "",
             "Required objectives may remain unmet. Saved partial outputs are evidence of work performed, not completed results.", ""]
    scope = state.workflow_plans.get(workflow.data.plan_id)
    if scope:
        lines += [scope.objective, "", f"Scope: {scope.analysis_kind}. Required figures: {', '.join(scope.required_figures) or 'none'}.", ""]
    lines += [f"Active stage: {workflow.stage}.", "", "## Stage outcomes", ""]
    for path in state.evidence.attempts:
        if path.startswith("stages/0"):
            report = StageReport.model_validate_json(state.evidence.path(path).read_text())
            lines += [f"- [{report.stage_id}: {report.attempt_id}]({path.replace('.json', '.md')}): {report.status}."]
    artifacts = workflow.current_artifacts()
    latest = next((path for path in reversed(state.evidence.attempts)
                   if path.startswith(f"stages/{workflow.stage}/")), None)
    latest_report = StageReport.model_validate_json(state.evidence.path(latest).read_text()) if latest else None
    if latest_report:
        artifacts = list(dict.fromkeys([*artifacts, *latest_report.output_artifact_ids]))
    lines += ["", "## Saved current evidence", ""]
    for aid in artifacts:
        artifact = state.evidence.artifacts[aid]
        lines += [f"- [{aid}: {artifact.kind}]({artifact.path})"]
    lines += ["", "## Recorded concerns and limitations", ""]
    issues = workflow.issues() + (latest_report.issues if latest_report else [])
    for issue in issues:
        lines += [f"- {issue.code} ({issue.severity}, {issue.resolution}): {issue.message}"]
    limits = workflow.limitations() + (latest_report.limitations if latest_report else [])
    lines += [f"- {item}" for item in dict.fromkeys(limits)]
    reports = workflow._reports() + ([latest_report] if latest_report else [])
    assumptions = dict.fromkeys(item for report in reports for item in report.unchecked_assumptions)
    lines += [f"- Unchecked assumption: {item}" for item in assumptions]
    lines += ["", "Inspect [workflow state](workflow_state.json), [run state](state.json), and [events](events.jsonl) before resuming.", ""]
    content = "\n".join(lines)
    canonical = f"partial_{uuid4().hex}.md"
    state.evidence.atomic_write(canonical, lambda path: path.write_text(content))
    state.evidence.register_file(canonical, "partial_report", parent_ids=artifacts)
    state.evidence.atomic_write("incomplete.md", lambda path: path.write_text(content))
