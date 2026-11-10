from pathlib import Path

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
    lines = ["# Metabolomics analysis report", "", cfg.objective, "",
             f"Comparison: **{cfg.treatment_group} relative to {cfg.reference_group}**. "
             f"Study design: {cfg.design}. Input scale: {cfg.input_scale}.", "",
             f"Normalization metadata: {cfg.normalization_status}", "",
             f"Selected samples: {len(state.raw)}. Input features: {len(state.raw.columns)}.", "",
             "## Computed results", "",
             "These summaries are generated directly from saved numerical outputs.", ""]
    if state.excluded_groups:
        lines += [f"Groups outside the configured comparison were excluded: {state.excluded_groups}.", ""]
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
    lines += ["## Analysis decisions", ""]
    plans = state.preprocessing_plans if state.workflow else state.plans
    for pid, plan in plans.items():
        purpose = getattr(plan, "purpose", "preparation")
        lines += [f"- **{pid} ({purpose})**: {plan.rationale}",
                  f"  [Complete parameters]({pid}_plan.json)"]
    if state.workflow:
        for pid, plan in state.univariate_plans.items():
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
    if not state.workflow:
        lines += ["", "## PCA", ""]
    for pid, result in state.pcas.items():
        percentages = ", ".join(f"PC{i+1}: {v:.1%}" for i, v in enumerate(result["explained_variance_ratio"]))
        lines += [f"- {pid}, dataset {result['dataset_id']}: {percentages}. "]
    lines += ["", "## Figures", ""]
    for aid, artifact in state.artifacts.items():
        if state.workflow and aid not in state.workflow.data.figure_ids:
            continue
        if artifact["path"].endswith(".png"):
            lines += [f"### {aid}: {artifact['kind']} ({artifact['source_id']})", "",
                      f"![{artifact['kind']}]({artifact['path']})", ""]
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
