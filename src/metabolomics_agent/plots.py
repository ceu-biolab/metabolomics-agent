import os
import tempfile

os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="metabolomics-mpl-"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

from .models import PlotArgs
from .state import RunState


def _features(columns, requested, limit=6):
    selected = requested or list(columns[:limit])
    if not selected or set(selected) - set(columns):
        raise ValueError("Plot features must exist in the source dataset.")
    return selected


def make_plot(state: RunState, args: PlotArgs):
    sns.set_theme(style="whitegrid", context="notebook")
    source, kind, cfg = args.source_id, args.kind, state.config
    fig = None
    try:
        if kind == "volcano":
            if source not in state.comparisons:
                raise ValueError("Volcano source_id must be a comparison ID (c...).")
            result = state.comparisons[source]
            table = state.read_artifact(result["artifact_id"])
            table = table.dropna(subset=["log2_fold_change", "adjusted_p_value"])
            fig, ax = plt.subplots(figsize=(8, 6))
            y = -np.log10(table.adjusted_p_value.clip(lower=np.finfo(float).tiny))
            ax.scatter(table.log2_fold_change, y,
                       c=np.where(table.significant, "#bf3e36", "#647c91"), alpha=0.8, s=25)
            ax.axhline(-np.log10(result["alpha"]), color="gray", linestyle="--", linewidth=1)
            ax.axvline(0, color="gray", linewidth=1)
            ax.set(xlabel=f"log2 fold change ({result['direction']})",
                   ylabel=f"-log10 adjusted p ({result['correction']})",
                   title=f"{result['test']} | {result['purpose']}")
            for _, row in table.head(5).iterrows():
                ax.annotate(row.feature, (row.log2_fold_change,
                            -np.log10(max(row.adjusted_p_value, np.finfo(float).tiny))),
                            xytext=(4, 4), textcoords="offset points", fontsize=8)
        elif kind == "pca":
            if source not in state.pcas:
                raise ValueError("PCA plot source_id must be a PCA result ID (pc...).")
            result = state.pcas[source]
            table = state.read_artifact(result["scores_artifact"])
            variance = result["explained_variance_ratio"]
            fig, ax = plt.subplots(figsize=(8, 6))
            sns.scatterplot(data=table, x="PC1", y="PC2", hue=cfg.group_column,
                            style=cfg.batch_column if cfg.batch_column else None,
                            s=65, ax=ax)
            ax.set(xlabel=f"PC1 ({variance[0]:.1%})", ylabel=f"PC2 ({variance[1]:.1%})",
                   title="PCA: exploratory structure")
        elif kind in {"distributions", "boxplot", "heatmap"}:
            dataset = None if source == "raw" else state.dataset(source)
            values = state.raw if dataset is None else dataset.concentrations
            features = _features(values.columns, args.features)
            if kind == "distributions":
                fig, axes = plt.subplots(len(features), 2 if dataset else 1,
                                         figsize=(10 if dataset else 6, 2.5 * len(features)), squeeze=False)
                for i, feature in enumerate(features):
                    sns.histplot(state.raw[feature].dropna(), ax=axes[i, 0], bins=18, color="#647c91")
                    axes[i, 0].set_title(f"{feature}: input")
                    if dataset:
                        sns.histplot(dataset.testing[feature].dropna(), ax=axes[i, 1], bins=18, color="#bf3e36")
                        transform = (state.preprocessing_plans.get(dataset.plan_id)
                                     or state.plans[dataset.plan_id]).transform
                        axes[i, 1].set_title(f"{feature}: test scale ({transform})")
            elif kind == "boxplot":
                fig, axes = plt.subplots(1, len(features), figsize=(max(6, 3 * len(features)), 5), squeeze=False)
                table = values[features].join(state.metadata[[cfg.group_column]])
                for feature, ax in zip(features, axes[0]):
                    sns.boxplot(data=table, x=cfg.group_column, y=feature,
                                order=[cfg.reference_group, cfg.treatment_group],
                                color="#cad5df", showfliers=False, ax=ax)
                    sns.stripplot(data=table, x=cfg.group_column, y=feature,
                                  order=[cfg.reference_group, cfg.treatment_group],
                                  jitter=False, color="#34495e", size=3, alpha=0.6, ax=ax)
                    ax.set(title=feature, xlabel="", ylabel=cfg.input_scale)
            else:
                if dataset is None:
                    raise ValueError("Heatmaps require a preprocessed dataset ID.")
                heat = dataset.pca_values[features]
                if heat.isna().any().any():
                    raise ValueError("Heatmaps require complete data for the selected features.")
                # A heatmap, without silently adding clustering or selecting by p-value.
                order = state.metadata.sort_values(cfg.group_column).index
                fig, ax = plt.subplots(figsize=(10, max(4, len(features) * 0.45)))
                sns.heatmap(heat.loc[order].T, cmap="vlag", center=0, xticklabels=False, ax=ax)
                ax.set(title="Centered/scaled abundances; samples ordered by group", xlabel="Samples")
        else:
            raise ValueError("Unsupported plot kind.")
        fig.tight_layout()
        aid = state.figure(fig, kind, {"source_id": source, "features": args.features})
        return {"artifact_id": aid, "path": state.artifacts[aid]["path"], "source_id": source}
    finally:
        if fig is not None:
            plt.close(fig)
