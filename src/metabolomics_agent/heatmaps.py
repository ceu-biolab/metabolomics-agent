"""Saved feature selection and complete, paginated heatmap jobs."""

import math

import numpy as np
import pandas as pd

from .evidence_models import StageIssue
from .serialization import clean_json


def complete_plot(state, plot, specification, current_figures):
    if plot["figure_spec"] != specification.model_dump() or not plot.get("metadata_artifact_id"):
        return False
    if plot["status"] == "skipped_no_significant":
        return specification.selection_mode == "significant" and plot["selected_count"] == 0 and not plot["figure_ids"]
    if plot["status"] != "completed" or len(plot["figure_ids"]) != plot["pages_expected"]:
        return False
    figures = [state.artifacts.get(aid, {}) for aid in plot["figure_ids"]]
    return (all(aid in current_figures for aid in plot["figure_ids"])
            and {figure.get("page") for figure in figures} == set(range(1, plot["pages_expected"]+1))
            and all(figure.get("page_count") == plot["pages_expected"]
                    and figure.get("figure_spec") == specification.model_dump() for figure in figures))


def make_heatmap(state, args):
    from .visualization import make_audited_plot

    numbers = [int(key[1:]) for key in state.plot_results]
    for artifact in state.evidence.artifacts.values():
        value = artifact.parameters.get("plot_id", "")
        if value.startswith("g") and value[1:].isdigit():
            numbers.append(int(value[1:]))
    gid = f"g{max(numbers, default=0)+1:03d}"
    plot = {"plot_id": gid, "kind": "heatmap", "source_id": args.source_id,
            "figure_spec": args.model_dump(), "status": "running", "selected_count": 0,
            "pages_expected": 0, "figure_ids": [], "artifacts": {}}
    state.plot_results[gid] = plot
    state.workflow.data.plot_result_ids.append(gid)
    parents = [state.datasets[args.source_id].artifact_ids["testing" if args.scale == "testing" else "concentrations"]]
    first_result = {}
    try:
        with state.evidence.operation("select_heatmap_features", inputs=parents, parameters=args.model_dump()):
            if args.selection_mode == "significant":
                comparison = state.comparisons[args.comparison_id]
                if comparison["dataset_id"] != args.source_id or args.comparison_id not in state.workflow.data.comparison_ids:
                    raise ValueError("Heatmap comparison must be current and use the same prepared dataset.")
                source = state.read_artifact(comparison["artifact_id"])
                p = source.adjusted_p_value
                valid = (source.status == "ok") & np.isfinite(p) & p.between(0, 1)
                included = valid & (p < comparison["alpha"])
                selection = source[["feature", "adjusted_p_value", "log2_fold_change", "status"]].copy()
                selection["included"] = included
                selection["reason"] = np.where(included, "adjusted_p_below_registered_alpha",
                                               np.where(valid, "not_below_registered_alpha", "invalid_or_untested"))
                parents.append(comparison["artifact_id"])
                criteria = {"mode": "significant", "comparison_id": args.comparison_id,
                            "comparison_artifact_id": comparison["artifact_id"], "alpha": comparison["alpha"],
                            "correction": comparison["correction"], "operator": "<", "order": "comparison_table_order",
                            "caveat": "Selection used the same group-comparison data; separation is descriptive, not independent validation."}
            else:
                values = state.datasets[args.source_id].concentrations
                if set(args.features) - set(values):
                    raise ValueError("Explicit heatmap features must exist in the prepared source.")
                selection = pd.DataFrame({"feature": args.features, "included": True, "reason": "explicit_requested"})
                criteria = {"mode": "explicit", "order": "requested_order"}
            features = selection.loc[selection.included, "feature"].tolist()
            available = state.datasets[args.source_id].testing.columns
            if set(features) - set(available):
                raise ValueError("Selected comparison features are absent from the prepared source.")
            order = {feature: index+1 for index, feature in enumerate(features)}
            selection["display_order"] = pd.array([order.get(feature) for feature in selection.feature], dtype="Int64")
            aid = state.table(selection, "heatmap_selection", {"parent_ids": parents,
                "dataset_id": args.source_id, "scale": args.scale, "parameters": {"plot_id": gid}})
            plot["artifacts"]["selection"] = aid
            plot["selected_count"] = len(features)
            plot["pages_expected"] = math.ceil(len(features) / args.rows_per_page)
            criteria.update(selected_count=len(features), candidates=len(selection), selection_artifact_id=aid,
                            selection_rationale=args.selection_rationale)
            filename = f"{gid}_selection.json"
            state.write_json(filename, {**criteria, "selected_features": features, "figure_spec": args.model_dump()})
            metadata = state.evidence.register_file(filename, "heatmap_selection_metadata", parent_ids=[aid],
                                                      parameters={"plot_id": gid})
            plot["artifacts"]["selection_metadata"] = metadata.id
        state.snapshot()
        if not features:
            plot["status"] = "skipped_no_significant"
            state.evidence.skip("render_heatmap", "No valid features have adjusted p below the registered alpha.")
            if state.evidence.active:
                state.evidence.active.issues.append(StageIssue(
                    code="HEATMAP_NO_SIGNIFICANT_FEATURES", severity="info", source="computed_check",
                    message="Heatmap omitted: no valid features met the registered adjusted-p criterion.",
                    evidence_ids=[aid]))
        else:
            for page in range(plot["pages_expected"]):
                subset = features[page*args.rows_per_page:(page+1)*args.rows_per_page]
                result = make_audited_plot(state, args, resolved_features=subset, selection=criteria,
                                          selection_artifacts=plot["artifacts"], page=page+1,
                                          page_count=plot["pages_expected"])
                if not first_result:
                    first_result = result
                plot["figure_ids"].append(result["artifact_id"])
                state.snapshot()
            plot["status"] = "completed"
    except BaseException as error:
        plot.update(status="failed" if isinstance(error, Exception) else "interrupted", failure_reason=str(error))
        raise
    finally:
        filename = f"{gid}_heatmap.json"
        state.write_json(filename, plot)
        document = state.evidence.register_file(filename, "heatmap_result",
            parent_ids=[*plot["artifacts"].values(), *plot["figure_ids"]], parameters={"plot_id": gid})
        plot["metadata_artifact_id"] = document.id
        state.plot_results[gid] = clean_json(plot)
    return {**first_result, "plot_id": gid, "status": plot["status"], "kind": "heatmap", "source_id": args.source_id,
            "artifact_ids": plot["figure_ids"], "selected_count": plot["selected_count"],
            "pages_expected": plot["pages_expected"], "artifacts": plot["artifacts"],
            "metadata_artifact_id": plot["metadata_artifact_id"],
            **({"artifact_id": plot["figure_ids"][0]} if plot["figure_ids"] else {})}
