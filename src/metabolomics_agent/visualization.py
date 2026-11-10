"""Auditable display-only transformations for the staged workflow (version 1)."""

from matplotlib.cbook import boxplot_stats
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .evidence_models import StageIssue
from .serialization import clean_json


def _selected(columns, requested, *, all_features=False):
    features = requested or list(columns if all_features else columns[:6])
    if not features or len(features) != len(set(features)) or set(features) - set(columns):
        raise ValueError("Plot features must be unique and present in the source.")
    return features


def _long(values, state, features, representation):
    # Build long form without reserving any metabolite name for melt/index fields.
    result = pd.DataFrame({"sample_id": np.tile(values.index.to_numpy(), len(features)),
                           "feature": np.repeat(features, len(values)),
                           "value": values[features].to_numpy().T.reshape(-1)})
    result["group"] = result.sample_id.map(state.metadata[state.config.group_column])
    if state.config.pair_column:
        result["pair_id"] = result.sample_id.map(state.metadata[state.config.pair_column])
    result["representation"] = representation
    return result


def _sources(state, args, resolved_features=None):
    if args.kind.startswith("pca_"):
        from .pca import plot_source
        return plot_source(state, args)
    dataset = None if args.source_id == "raw" else state.datasets.get(args.source_id)
    inputs = {artifact["kind"]: aid for aid, artifact in state.artifacts.items()
              if artifact["kind"] in {"input_values", "input_metadata"}}
    parents = list(inputs.values())
    methods = {}
    if args.kind == "volcano":
        result = state.comparisons[args.source_id]
        table = state.read_artifact(result["artifact_id"])
        features = _selected(table.feature.tolist(), args.features, all_features=True)
        table = table.set_index("feature").loc[features].reset_index()
        parents = [result["artifact_id"]]
        methods = {key: result[key] for key in ["test", "correction", "alpha", "purpose", "direction"]}
        labels = {"x": f"log2 fold change ({result['direction']})",
                  "y": f"-log10 adjusted p ({result['correction']})"}
    else:
        if dataset is None and args.source_id != "raw":
            raise ValueError("A current dataset is required for this figure.")
        if dataset is None and args.scale != "concentrations":
            raise ValueError("Raw plots use the original input scale; no test representation exists yet.")
        values = state.raw if dataset is None else getattr(dataset, args.scale)
        features = _selected(list(values), resolved_features if resolved_features is not None else args.features)
        if dataset:
            key = "testing" if args.scale == "testing" else "concentrations"
            parents.append(dataset.artifact_ids[key])
            if dataset.artifact_ids.get("imputation_mask"):
                parents.append(dataset.artifact_ids["imputation_mask"])
            plan = state.preprocessing_plans[dataset.plan_id]
            methods = {"preprocessing_plan_id": dataset.plan_id, **plan.model_dump()}
        table = _long(values, state, features, "original" if dataset is None else args.scale)
        if args.kind == "distributions" and dataset:
            table = pd.concat([_long(state.raw, state, features, "original"), table], ignore_index=True)
        if dataset is not None and dataset.imputation_mask is not None:
            table["imputed"] = [bool(dataset.imputation_mask.loc[row.sample_id, row.feature])
                                if row.representation != "original" else False for row in table.itertuples()]
        transform = methods.get("transform", "none") if args.scale == "testing" else "none"
        scale_label = state.config.input_scale
        if dataset is not None and methods.get("normalization") == "median":
            scale_label = f"Median-normalized {scale_label}"
        if transform != "none":
            scale_label = f"{transform}({scale_label})"
        labels = {"x": "Samples" if args.kind == "heatmap" else "Group",
                  "y": scale_label}
    return table, features, parents, methods, labels


def _display(state, args, source, features, metadata):
    if args.kind.startswith("pca_"):
        if args.kind == "pca_scree":
            metadata["display_notes"].append("Variance covers calculated components only; the unrepresented remainder is saved in PCA metadata.")
        metadata["display_notes"].extend(metadata["methods"]["notes"])
        return source.copy()
    if args.kind == "volcano":
        valid = (np.isfinite(source.log2_fold_change) & np.isfinite(source.adjusted_p_value)
                 & source.adjusted_p_value.between(0, 1))
        display = source.loc[valid].copy()
        metadata["omitted_values"] = int((~valid).sum())
        metadata["clipped_zero_p"] = int((display.adjusted_p_value == 0).sum())
        metadata["display_p_floor"] = float(np.finfo(float).tiny)
        display["display_adjusted_p"] = display.adjusted_p_value.clip(lower=np.finfo(float).tiny)
        display["minus_log10_adjusted_p"] = -np.log10(display.display_adjusted_p)
        display["display_significant"] = display.adjusted_p_value < metadata["methods"]["alpha"]
        metadata["display_notes"].append("Zero adjusted p-values are clipped only for display; saved inference is unchanged.")
        return display
    finite = np.isfinite(source.value)
    if (source.value.notna() & ~finite).any():
        raise ValueError("Plot inputs must contain finite nonmissing values.")
    metadata["omitted_values"] = int((~finite).sum())
    if args.kind == "distributions":
        rows = []
        for (feature, representation), part in source.groupby(["feature", "representation"], sort=False):
            values = part.loc[np.isfinite(part.value), "value"].to_numpy()
            if not len(values):
                metadata["display_notes"].append(f"No available values for {feature} ({representation}).")
                continue
            counts, edges = np.histogram(values, bins=18)
            rows.extend({"feature": feature, "representation": representation, "bin_left": edges[i],
                         "bin_right": edges[i+1], "count": int(count)} for i, count in enumerate(counts))
        metadata["histogram_bins"] = 18
        metadata["display_notes"].append("Separate bin edges per feature and representation; counts, not densities.")
        return pd.DataFrame(rows, columns=["feature", "representation", "bin_left", "bin_right", "count"])
    display = source.loc[finite].copy()
    if args.kind == "boxplot":
        lines = 0
        if state.config.design == "paired":
            for _, part in display.groupby("feature", sort=False):
                pairs = part.pivot(index="pair_id", columns="group", values="value")
                lines += int(pairs.reindex(columns=metadata["group_order"]).notna().all(axis=1).sum())
            metadata["display_notes"].append("Lines connect available complete pairs; unpaired available points remain visible.")
        metadata["paired_lines"] = lines
        metadata["display_notes"].append("Boxplots show quartiles and 1.5-IQR whiskers; all available individual points are overlaid without jitter.")
        return display
    # Heatmaps use their own persisted display representation, never a PCA matrix.
    matrix = source.pivot(index="sample_id", columns="feature", values="value").reindex(columns=features)
    if matrix.isna().any().any():
        raise ValueError("Heatmaps require complete selected features; revise the selection or preparation.")
    centers = matrix.mean() if args.centering == "feature_mean" else pd.Series(0.0, index=features)
    divisors = matrix.std(ddof=1) if args.scaling == "feature_sd" else pd.Series(1.0, index=features)
    if not np.isfinite(divisors).all() or (divisors <= 0).any():
        raise ValueError("Heatmap feature SD must be finite and positive; no constant-feature substitution is allowed.")
    matrix = (matrix - centers) / divisors
    if not np.isfinite(matrix.to_numpy()).all():
        raise ValueError("Heatmap display values must be finite.")
    metadata["display_centers"] = centers.to_dict()
    metadata["display_divisors"] = divisors.to_dict()
    metadata["display_notes"].append("No clustering. Group order is declared study order; ordering does not discover clusters. Scaling uses sample SD (ddof=1).")
    display = _long(matrix, state, features, "display_only").rename(columns={"value": "display_value"})
    return display


def _render(state, args, display, features, metadata):
    if display.empty:
        raise ValueError("No drawable values remain; an empty figure cannot meet a figure requirement.")
    order = metadata["group_order"]
    if args.kind.startswith("pca_"):
        fig, ax = plt.subplots(figsize=(8, 6))
        if args.kind == "pca_scores":
            first, second = metadata["methods"]["score_columns"]
            sns.scatterplot(data=display, x=first, y=second, hue=state.config.group_column,
                            hue_order=order, s=55, ax=ax)
            ax.set(title="Exploratory PCA scores")
        elif args.kind == "pca_scree":
            positions = np.arange(len(display))
            ax.bar(positions, 100*display.explained_variance_ratio, color="#647c91", label="Per component")
            ax.plot(positions, 100*display.cumulative_variance_ratio, marker="o", color="#bf3e36", label="Cumulative")
            ax.set_xticks(positions, labels=display.component)
            ax.set(title="PCA variance of calculated components", ylim=(0, 100))
            ax.legend()
        else:
            ax.barh(display.feature.iloc[::-1], display.weight.iloc[::-1], color="#647c91")
            ax.axvline(0, color="gray", linewidth=1)
            ax.set(title=f"PCA loadings: {metadata['methods']['loading_component']}")
        ax.set(xlabel=metadata["labels"]["x"], ylabel=metadata["labels"]["y"])
        return fig
    if args.kind == "volcano":
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.scatter(display.log2_fold_change, display.minus_log10_adjusted_p,
                   c=np.where(display.display_significant, "#bf3e36", "#647c91"), alpha=.8, s=25)
        ax.axhline(-np.log10(metadata["methods"]["alpha"]), color="gray", linestyle="--", linewidth=1)
        ax.axvline(0, color="gray", linewidth=1)
        ax.set(xlabel=metadata["labels"]["x"], ylabel=metadata["labels"]["y"],
               title=f"{metadata['methods']['test']} | {metadata['methods']['purpose']}")
        # Labels use declared/source order, never a hidden significance ranking.
        for row in display.head(5).itertuples():
            ax.annotate(row.feature, (row.log2_fold_change, row.minus_log10_adjusted_p),
                        xytext=(4, 4), textcoords="offset points", fontsize=8)
        return fig
    if args.kind == "distributions":
        representations = list(dict.fromkeys(display.representation))
        fig, axes = plt.subplots(len(features), len(representations), squeeze=False,
                                 figsize=(6 * len(representations), 2.5 * len(features)))
        for i, feature in enumerate(features):
            for j, representation in enumerate(representations):
                part = display[(display.feature == feature) & (display.representation == representation)]
                axes[i, j].bar(part.bin_left, part["count"], width=part.bin_right-part.bin_left,
                               align="edge", color="#647c91" if representation == "original" else "#bf3e36")
                axes[i, j].set(title=f"{feature}: {representation}", ylabel="Count",
                               xlabel=state.config.input_scale if representation == "original" else metadata["labels"]["y"])
        return fig
    if args.kind == "boxplot":
        fig, axes = plt.subplots(1, len(features), figsize=(max(6, 3 * len(features)), 5), squeeze=False)
        for feature, ax in zip(features, axes[0]):
            part = display[display.feature == feature]
            sns.boxplot(data=part, x="group", y="value", order=order, color="#cad5df",
                        showfliers=False, ax=ax)
            if state.config.design == "paired":
                pairs = part.pivot(index="pair_id", columns="group", values="value").reindex(columns=order).dropna()
                for pair in pairs.to_numpy():
                    ax.plot([0, 1], pair, color="#9da9af", alpha=.5, linewidth=.7)
            sns.stripplot(data=part, x="group", y="value", order=order, jitter=False,
                          color="#34495e", size=3, alpha=.6, ax=ax)
            ax.set(title=feature, xlabel="", ylabel=metadata["labels"]["y"])
        return fig
    matrix = display.pivot(index="sample_id", columns="feature", values="display_value")
    samples = state.metadata.index
    if args.sample_order == "group":
        groups = state.metadata[state.config.group_column]
        samples = [sid for group in order for sid in samples if groups.loc[sid] == group]
    matrix = matrix.reindex(index=samples, columns=features)
    fig, ax = plt.subplots(figsize=(10, max(4, len(features)*.45)))
    centered = args.centering == "feature_mean"
    color_label = metadata["labels"]["colorbar"]
    sns.heatmap(matrix.T, cmap="vlag" if centered else "viridis", center=0 if centered else None,
                xticklabels=False, ax=ax, cbar_kws={"label": color_label})
    if args.sample_order == "group":
        first = int((state.metadata[state.config.group_column] == order[0]).sum())
        ax.axvline(first, color="black", linewidth=1)
        ax.set_xticks([first/2, first+(len(samples)-first)/2], labels=order)
    ax.set(title=f"Heatmap without clustering; samples in {args.sample_order} order", xlabel="Samples", ylabel="Features")
    return fig


def make_audited_plot(state, args, *, resolved_features=None, selection=None, selection_artifacts=None, page=1, page_count=1):
    sns.set_theme(style="whitegrid", context="notebook")
    source, features, parents, methods, labels = _sources(state, args, resolved_features)
    if selection_artifacts:
        parents.extend(selection_artifacts.values())
    provenance = {"parent_ids": parents, "scale": args.scale,
                  "plan_id": state.workflow.data.figure_plan_id,
                  "dataset_id": args.source_id if args.source_id in state.datasets else None}
    metadata = {"schema_version": 1, "status": "preparing", "figure_spec": args.model_dump(),
                "selected_features": features, "methods": methods, "labels": labels,
                "group_order": [state.config.reference_group, state.config.treatment_group],
                "omitted_values": 0, "clipped_zero_p": 0, "paired_lines": 0, "display_notes": [],
                "imputed_values": (methods.get("imputed_cells", 0) if args.kind.startswith("pca_")
                                   else int(source["imputed"].sum()) if "imputed" in source else 0),
                "caption": args.caption or f"{args.kind} from {args.source_id}; {args.selection_rationale}",
                "render_parameters": {"theme": "whitegrid", "context": "notebook", "dpi": 160,
                    "histogram_bins": 18, "boxplot_whis": 1.5, "point_jitter": False,
                    "clustering": False, "heatmap_cmap": "vlag" if args.centering == "feature_mean" else "viridis",
                    "volcano_colors": {"significant": "#bf3e36", "other": "#647c91"},
                    "volcano_annotation_limit": 5, "annotation_order": "declared_or_source",
                    "package_versions": {key: value for key, value in state.run_info["packages"].items()
                                         if key in {"numpy", "pandas", "matplotlib", "seaborn"}}},
                "artifacts": {}}
    if selection is not None and selection.get("mode") == "significant" and not args.caption:
        metadata["caption"] = (f"Heatmap of {selection['selected_count']} selected variables: adjusted p < "
            f"{selection['alpha']} ({selection['correction']}) from {selection['comparison_id']}; "
            f"page {page}/{page_count}. Selection and display use the same study data.")
    if selection is not None:
        metadata.update(selection=selection, page=page, page_count=page_count)
        if selection.get("caveat"):
            metadata["display_notes"].append(selection["caveat"])
    if metadata["imputed_values"]:
        metadata["display_notes"].append(f"Prepared source includes {metadata['imputed_values']} imputed values; these are not additional original measurements.")
    with state.evidence.operation("save_plot_source", inputs=parents, parameters=args.model_dump()):
        source_id = state.table(source, "plot_source", provenance)
        metadata["artifacts"]["source"] = source_id
    filename = f"{source_id}_plot.json"
    fig = None
    figures_before = set(plt.get_fignums())
    try:
        with state.evidence.operation("prepare_plot_display", inputs=[source_id]):
            display = _display(state, args, source, features, metadata)
            display_id = state.table(display, "plot_data", {**provenance, "parent_ids": [source_id]})
            metadata["artifacts"]["display"] = display_id
            if args.kind == "boxplot":
                summaries = []
                for (feature, group), part in display.groupby(["feature", "group"], sort=False):
                    summary = boxplot_stats(part.value.to_numpy(), whis=1.5)[0]
                    summaries.append({"feature": feature, "group": group, "n": len(part),
                        **{key: summary[key] for key in ["q1", "med", "q3", "iqr", "whislo", "whishi", "mean"]},
                        "outlier_points": len(summary["fliers"])})
                summary_id = state.table(pd.DataFrame(summaries, columns=["feature", "group", "n", "q1", "med", "q3", "iqr", "whislo", "whishi", "mean", "outlier_points"]), "plot_summary",
                                         {**provenance, "parent_ids": [display_id]})
                metadata["artifacts"]["summary"] = summary_id
        with state.evidence.operation("render_plot", inputs=[source_id, display_id]):
            # Exact sample order stays in the local rendering configuration.
            samples = list(state.metadata.index)
            if args.sample_order == "group":
                samples = [sid for group in metadata["group_order"] for sid in samples
                           if state.metadata.loc[sid, state.config.group_column] == group]
            metadata["sample_order_ids"] = samples
            if args.kind == "heatmap":
                if args.scaling == "feature_sd":
                    metadata["labels"]["colorbar"] = ("Centered values / sample SD" if args.centering == "feature_mean" else "Values / sample SD")
                else:
                    metadata["labels"]["colorbar"] = (f"Mean-centered {labels['y']}" if args.centering == "feature_mean" else labels['y'])
            metadata["status"] = "display_prepared"
            state.write_json(filename, metadata)
            config = state.evidence.register_file(filename, "plot_configuration", parent_ids=list(metadata["artifacts"].values()),
                                                   individual_samples=True)
            fig = _render(state, args, display, features, metadata)
            fig.tight_layout()
            artifact_ids = {**metadata["artifacts"], **(selection_artifacts or {}), "configuration": config.id}
            aggregate = {**args.model_dump(), "caption": metadata["caption"],
                         "labels": metadata["labels"], "group_order": metadata["group_order"],
                         "selected_features": features, "imputed_values": metadata["imputed_values"],
                         "plot_artifact_ids": artifact_ids, "omitted_values": metadata["omitted_values"],
                         "clipped_zero_p": metadata["clipped_zero_p"], "paired_lines": metadata["paired_lines"],
                         "display_notes": metadata["display_notes"], "figure_spec": args.model_dump(),
                         "parent_ids": list(artifact_ids.values())}
            if selection is not None:
                aggregate.update(selection=selection, page=page, page_count=page_count)
            aid = state.figure(fig, args.kind, aggregate)
    except Exception as error:
        # A failed image never satisfies coverage; retain the committed source and any display table.
        failure_path = filename
        if any(a.path == filename for a in state.evidence.artifacts.values()):
            failure_path = f"{source_id}_plot_failure.json"
        metadata.update(status="failed", failure=str(error))
        state.write_json(failure_path, metadata)
        state.evidence.register_file(failure_path, "plot_failure", parent_ids=list(metadata["artifacts"].values()),
                                     individual_samples=True)
        if state.evidence.active:
            state.evidence.active.issues.append(StageIssue(
                code="FIGURE_FAILED", severity="warning", source="computed_check",
                message=f"{args.kind} figure failed; saved source {source_id} remains available.",
                evidence_ids=list(metadata["artifacts"].values())))
        raise
    finally:
        if fig is not None:
            plt.close(fig)
        for number in set(plt.get_fignums()) - figures_before:
            plt.close(number)
    for code, count, message in [
        ("FIGURE_OMISSIONS", metadata["omitted_values"], "Unavailable or invalid values were omitted from the figure; inspect its saved source/configuration."),
        ("VOLCANO_ZERO_P_CLIPPED", metadata["clipped_zero_p"], "Zero adjusted p-values were clipped for display only; inference was unchanged."),
    ]:
        state.evidence.check(code, 0, count, passed=count == 0, message=message)
    return clean_json({"artifact_id": aid, "path": state.artifacts[aid]["path"], "source_id": args.source_id,
                       "selected_features": features, **{key: value for key, value in aggregate.items()
                           if key not in {"parent_ids", "figure_spec", "features"}}})
