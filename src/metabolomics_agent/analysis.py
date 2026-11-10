"""Numerical analysis; the LLM never implements calculations itself."""

import warnings

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.decomposition import PCA
from sklearn.impute import KNNImputer
from statsmodels.stats.multitest import multipletests

from .models import AnalysisPlan
from .state import Dataset, RunState, clean_json


def inspect_dataset(state: RunState):
    x, meta, cfg = state.raw, state.metadata, state.config
    records = []
    for feature in x:
        row = {"feature": feature, "missing_fraction": x[feature].isna().mean(),
               "zero_fraction": (x[feature] == 0).mean(),
               "unique_observed": x[feature].nunique()}
        for label in [cfg.reference_group, cfg.treatment_group]:
            v = x.loc[meta[cfg.group_column] == label, feature]
            observed = v.dropna()
            q1, q3 = observed.quantile([0.25, 0.75])
            iqr = q3 - q1
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                normality_p = (stats.shapiro(observed).pvalue
                               if 3 <= len(observed) <= 5000 and observed.nunique() > 1
                               else np.nan)
            row.update({f"n_{label}": len(observed), f"missing_fraction_{label}": v.isna().mean(),
                        f"mean_{label}": observed.mean(), f"median_{label}": observed.median(),
                        f"sd_{label}": observed.std(), f"skew_{label}": observed.skew(),
                        f"shapiro_p_{label}": normality_p,
                        f"outlier_fraction_{label}": (
                            ((observed < q1 - 1.5 * iqr) | (observed > q3 + 1.5 * iqr)).mean()
                        )})
        records.append(row)
    diagnostics = pd.DataFrame(records)
    aid = state.table(diagnostics, "diagnostics", {"dataset_id": "raw"})
    flags = [
        "Missingness mechanisms cannot be established from the observed matrix alone.",
        "Normality tests and boxplot fences are diagnostics, not automatic test/removal rules.",
    ]
    if state.excluded_groups:
        flags.append(f"Only the configured comparison is selected; excluded groups: {state.excluded_groups}")
    if cfg.batch_column:
        cross = pd.crosstab(meta[cfg.batch_column].fillna("(missing)"), meta[cfg.group_column])
        batch_table = state.table(cross.reset_index(), "batch_by_group", {"dataset_id": "raw"})
        flags.append("Batch metadata is present. No batch correction or covariate adjustment is implemented.")
        if (cross == 0).any().any():
            flags.append("Some batch/group combinations are empty; assess confounding before interpretation.")
    else:
        batch_table = None
    state.inspected = True
    summary = {
        "dataset_id": "raw", "samples": len(x), "features": len(x.columns),
        "group_counts": meta[cfg.group_column].value_counts().to_dict(),
        "missing_cells": int(x.isna().sum().sum()), "zero_cells": int((x == 0).sum().sum()),
        "constant_features": x.columns[x.nunique() <= 1].tolist(),
        "features_above_35_percent_missing": x.columns[x.isna().mean() > 0.35].tolist(),
        "diagnostics_artifact": aid, "batch_artifact": batch_table,
        "normalization_status": cfg.normalization_status, "design": cfg.design,
        "excluded_groups": state.excluded_groups,
        "flags": flags,
    }
    state.write_json("inspection.json", summary)
    return summary


def set_plan(state: RunState, plan: AnalysisPlan):
    if not state.inspected:
        raise ValueError("Call inspect_dataset before setting an analysis plan.")
    paired = plan.test in {"paired_t", "wilcoxon"}
    if paired != (state.config.design == "paired"):
        raise ValueError("The statistical test must match the study's declared pairing design.")
    if state.comparisons:
        plan = plan.model_copy(update={"purpose": "exploratory"})
    pid = f"p{len(state.plans) + 1:03d}"
    state.plans[pid] = plan
    state.write_json(f"{pid}_plan.json", plan.model_dump())
    state.evidence.register_file(
        f"{pid}_plan.json", "analysis_plan", plan_id=pid, parameters=plan.model_dump()
    )
    return {"plan_id": pid, "plan": plan.model_dump(),
            "note": "Subsequent plans after testing are exploratory; earlier evidence remains saved."}


def preprocess(state: RunState, plan_id: str):
    if plan_id not in state.plans:
        raise ValueError("Unknown plan ID; call set_plan first.")
    if any(d.plan_id == plan_id for d in state.datasets.values()):
        raise ValueError("This plan already has a prepared dataset. Reuse that dataset ID "
                         "or record a new plan for a revision.")
    plan = state.plans[plan_id]
    x = state.raw.copy()
    notes = []
    evidence = state.evidence
    last = None

    def checkpoint(values, kind, scale="unlogged", *, advance=True):
        nonlocal last
        if evidence.active is None:
            return None
        artifact = evidence.table(
            values.reset_index(), kind,
            parent_ids=[last] if last else evidence.active.input_artifact_ids,
            plan_id=plan_id, scale=scale, parameters=plan.model_dump(),
            individual_samples=True,
        )
        if advance:
            last = artifact.id
        return artifact.id

    with evidence.operation("selected_input", parameters={"plan_id": plan_id}):
        checkpoint(x, "selected_input")
    if plan.zero_as_missing:
        with evidence.operation("zero_handling", inputs=[last] if last else []):
            x = x.mask(x == 0)
            checkpoint(x, "zeros_as_missing")
            notes.append("Zeros were explicitly treated as missing before filtering.")
    else:
        evidence.skip("zero_handling", "Zeros retained according to the plan.")
    with evidence.operation("filter", parameters={"max_missing_fraction": plan.max_missing_fraction},
                            inputs=[last] if last else []):
        missing = x.isna().mean()
        keep = (missing <= plan.max_missing_fraction) & (x.nunique() > 1)
        removed = [{"feature": f, "missing_fraction": missing[f],
                    "reason": "missingness" if missing[f] > plan.max_missing_fraction else "constant/all-missing"}
                   for f in x.columns[~keep]]
        x = x.loc[:, keep]
        checkpoint(x, "filtered")
        if evidence.active is not None:
            evidence.table(pd.DataFrame(removed, columns=["feature", "reason", "missing_fraction"]),
                           "removed_features", parent_ids=[last], plan_id=plan_id)
        if x.empty:
            raise ValueError("No features remain after missingness and constant-feature filtering.")
    missing_count = int(x.isna().sum().sum())
    if missing_count and plan.imputation != "none":
        with evidence.operation("impute", parameters={"method": plan.imputation},
                                inputs=[last] if last else []):
            missing_before = x.isna()
            if plan.imputation == "median":
                x = x.fillna(x.median())
            elif plan.imputation == "half_min":
                minimum = x.where(x > 0).min() / 2
                if minimum.isna().any():
                    raise ValueError("Half-minimum imputation requires positive observations per feature.")
                x = x.fillna(minimum)
            elif plan.imputation == "knn":
                x[:] = KNNImputer(n_neighbors=min(5, len(x) - 1)).fit_transform(x)
            checkpoint(missing_before & x.notna(), "imputation_mask", "boolean", advance=False)
            checkpoint(x, "imputed")
            notes.append("Imputed values are included in tests; inference may depend on imputation. "
                         "Consider an exploratory sensitivity analysis.")
            evidence.check("IMPUTATION_USED", 0, missing_count, passed=False,
                           message="Imputation uncertainty is not propagated into inference.")
    else:
        evidence.skip("impute", "No missing values, or the plan explicitly disables imputation.")
    if plan.normalization == "median":
        with evidence.operation("normalize", parameters={"method": plan.normalization},
                                inputs=[last] if last else []):
            medians = x.median(axis=1)
            if medians.isna().any() or (medians <= 0).any():
                raise ValueError("Median normalization requires positive sample medians.")
            x = x.div(medians, axis=0) * medians.median()
            checkpoint(x, "normalized")
            notes.append("Median normalization assumes broadly comparable sample-wide abundance.")
    else:
        evidence.skip("normalize", "Normalization disabled in the plan.")
    testing = x.copy()
    if plan.transform != "none":
        with evidence.operation("transform", parameters={"method": plan.transform},
                                inputs=[last] if last else []):
            positive = not (testing <= 0).any().any()
            evidence.check("LOG_INPUT_POSITIVE", True, bool(positive), passed=positive,
                           message="Logarithms require positive inputs.", severity="error")
            if not positive:
                raise ValueError("Log transformation requires strictly positive values. "
                                 "Revise zero handling explicitly; no pseudocount is inserted.")
            testing = np.log2(testing) if plan.transform == "log2" else np.log10(testing)
            checkpoint(testing, "transformed", plan.transform)
    else:
        evidence.skip("transform", "Transformation disabled in the plan.")
    variance = testing.var(ddof=1)
    variable = (variance > 0) & variance.notna()
    if not variable.all():
        removed.extend({"feature": f, "reason": "constant_after_preprocessing"}
                       for f in testing.columns[~variable])
        x, testing = x.loc[:, variable], testing.loc[:, variable]
    if testing.empty:
        raise ValueError("No variable features remain after preprocessing.")
    pca_values = testing - testing.mean()
    if plan.pca_scaling == "auto":
        pca_values = pca_values / testing.std(ddof=1)
    elif plan.pca_scaling == "pareto":
        pca_values = pca_values / np.sqrt(testing.std(ddof=1))
    did = f"d{len(state.datasets) + 1:03d}"
    provenance = {"dataset_id": did, "plan_id": plan_id,
                  "parent_ids": [last] if last else []}
    artifacts = {
        "concentrations": state.table(x.reset_index(), "concentrations", provenance),
        "testing": state.table(testing.reset_index(), "testing_values", provenance),
        "pca": state.table(pca_values.reset_index(), "pca_values", provenance),
        "filtered_features": state.table(pd.DataFrame(removed, columns=["feature", "reason", "missing_fraction"]),
                                         "filtered_features", provenance),
    }
    # Publish an executable dataset only after all its tables have been committed.
    state.datasets[did] = Dataset(x, testing, pca_values, plan_id, artifacts)
    evidence.check("PREPARED_DATASET", True, True, passed=True,
                   message="All representations of the prepared dataset must be saved.")
    return {"dataset_id": did, "plan_id": plan_id, "samples": len(x),
            "features_retained": len(x.columns), "features_removed": len(removed),
            "missing_before_imputation": missing_count,
            "missing_after_imputation": int(testing.isna().sum().sum()),
            "artifacts": artifacts, "notes": notes,
            "representations": "Fold changes use unlogged, unscaled concentrations after selected "
                               "imputation/normalization. Tests use transformed, unscaled values. "
                               "PCA uses separately centered/scaled values."}


def compare_groups(state: RunState, dataset_id: str):
    dataset = state.dataset(dataset_id)
    plan, cfg, meta = state.plans[dataset.plan_id], state.config, state.metadata
    if any(c["dataset_id"] == dataset_id for c in state.comparisons.values()):
        raise ValueError("This dataset has already been tested. Read its result, or set a new exploratory plan.")
    if state.comparisons and plan.purpose == "primary":
        raise ValueError("A statistical analysis has already been performed. Record a new plan; "
                         "the next analysis must be exploratory even if this plan was prepared earlier.")
    records = []
    for feature in dataset.testing:
        c = dataset.concentrations[feature]
        raw = state.raw[feature].mask(state.raw[feature] == 0) if plan.zero_as_missing else state.raw[feature]
        ref_mask = meta[cfg.group_column] == cfg.reference_group
        treat_mask = meta[cfg.group_column] == cfg.treatment_group
        ref, treat = dataset.testing.loc[ref_mask, feature], dataset.testing.loc[treat_mask, feature]
        if cfg.design == "paired":
            paired_table = meta[[cfg.pair_column, cfg.group_column]].copy()
            paired_table["value"] = dataset.testing[feature]
            wide = paired_table.pivot(index=cfg.pair_column, columns=cfg.group_column, values="value").dropna()
            ref, treat = wide[cfg.reference_group], wide[cfg.treatment_group]
        else:
            ref, treat = ref.dropna(), treat.dropna()
        ref_mean, treat_mean = c[ref_mask].mean(), c[treat_mask].mean()
        row = {
            "feature": feature, "test": plan.test, "correction": plan.correction,
            "purpose": plan.purpose, "n_reference": len(ref), "n_treatment": len(treat),
            "n_observed_reference": raw[ref_mask].notna().sum(),
            "n_observed_treatment": raw[treat_mask].notna().sum(),
            "mean_reference": ref_mean, "mean_treatment": treat_mean,
            "median_reference": c[ref_mask].median(), "median_treatment": c[treat_mask].median(),
            "log2_fold_change": (np.log2(treat_mean / ref_mean)
                                 if ref_mean > 0 and treat_mean > 0 else np.nan),
            "mean_difference_test_scale": treat.mean() - ref.mean(),
            "statistic": np.nan, "p_value": np.nan, "adjusted_p_value": np.nan,
            "hedges_g": np.nan, "rank_biserial": np.nan,
            "ci_low_test_scale": np.nan, "ci_high_test_scale": np.nan,
            "status": "ok",
        }
        if cfg.design == "paired":
            observed = meta[[cfg.pair_column, cfg.group_column]].copy()
            observed["value"] = raw
            observed_wide = observed.pivot(index=cfg.pair_column, columns=cfg.group_column, values="value")
            row["n_observed_pairs"] = len(observed_wide.dropna())
        if min(row["n_observed_reference"], row["n_observed_treatment"]) < 3:
            row["status"] = "insufficient_observed_data"
        elif cfg.design == "paired" and row["n_observed_pairs"] < 3:
            row["status"] = "insufficient_observed_pairs"
        elif min(len(ref), len(treat)) < 3:
            row["status"] = "insufficient_observations"
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                if plan.test in {"welch_t", "student_t"}:
                    result = stats.ttest_ind(treat, ref, equal_var=plan.test == "student_t")
                    interval = result.confidence_interval(confidence_level=1 - plan.alpha)
                    row.update(ci_low_test_scale=interval.low, ci_high_test_scale=interval.high)
                    df = len(treat) + len(ref) - 2
                    pooled_sd = np.sqrt(((len(treat) - 1) * treat.var(ddof=1) +
                                         (len(ref) - 1) * ref.var(ddof=1)) / df)
                    if pooled_sd > 0:
                        row["hedges_g"] = (1 - 3 / (4 * df - 1)) * (treat.mean() - ref.mean()) / pooled_sd
                elif plan.test == "mannwhitney":
                    result = stats.mannwhitneyu(treat, ref, alternative="two-sided", method="auto")
                    row["rank_biserial"] = 2 * result.statistic / (len(treat) * len(ref)) - 1
                elif plan.test == "paired_t":
                    result = stats.ttest_rel(treat, ref)
                    interval = result.confidence_interval(confidence_level=1 - plan.alpha)
                    row.update(ci_low_test_scale=interval.low, ci_high_test_scale=interval.high)
                else:
                    if np.allclose(treat.to_numpy() - ref.to_numpy(), 0, atol=0, rtol=0):
                        result = (0.0, 1.0)
                    else:
                        result = stats.wilcoxon(treat, ref, alternative="two-sided", zero_method="wilcox")
                row["statistic"], row["p_value"] = result[:2]
                if not np.isfinite(row["p_value"]):
                    row["p_value"] = np.nan
                    row["status"] = "undefined_test"
        records.append(row)
    table = pd.DataFrame(records)
    valid = table.p_value.notna()
    if not valid.any():
        raise ValueError("No features could be tested. Reconsider missingness, variability, and sample sizes.")
    table.loc[valid, "adjusted_p_value"] = multipletests(
        table.loc[valid, "p_value"], alpha=plan.alpha, method=plan.correction
    )[1]
    table["significant"] = table.adjusted_p_value < plan.alpha
    table = table.sort_values("adjusted_p_value", na_position="last")
    cid = f"c{len(state.comparisons) + 1:03d}"
    provenance = {"dataset_id": dataset_id, "plan_id": dataset.plan_id, "comparison_id": cid}
    aid = state.table(table, "comparison", provenance)
    summary = {"comparison_id": cid, **provenance, "artifact_id": aid,
               "test": plan.test, "correction": plan.correction, "alpha": plan.alpha,
               "purpose": plan.purpose, "features_tested": int(valid.sum()),
               "features_skipped": int((~valid).sum()), "significant_features": int(table.significant.sum()),
               "direction": f"{cfg.treatment_group} relative to {cfg.reference_group}",
               "top_features": clean_json(table.head(5).to_dict(orient="records")),
               "notes": ["Confidence intervals are feature-wise, unadjusted intervals on the test scale.",
                         "Correction covers valid features in this comparison only; exploratory reruns "
                         "are not independently confirmed discoveries."]}
    state.comparisons[cid] = summary
    return summary


def run_pca(state: RunState, dataset_id: str, components: int = 2):
    dataset = state.dataset(dataset_id)
    x = dataset.pca_values
    if x.isna().any().any():
        raise ValueError("PCA requires complete data. Revise the imputation/filtering plan explicitly.")
    if not np.isfinite(x.to_numpy()).all():
        raise ValueError("PCA input contains nonfinite values.")
    if components > min(x.shape):
        raise ValueError(f"components must be <= {min(x.shape)} for this dataset.")
    model = PCA(n_components=components, svd_solver="full", random_state=state.seed)
    names = [f"PC{i + 1}" for i in range(components)]
    scores = pd.DataFrame(model.fit_transform(x), index=x.index, columns=names)
    scores = scores.join(state.metadata)
    loadings = pd.DataFrame(model.components_.T, index=x.columns, columns=names)
    loadings.index.name = "feature"
    pid = f"pc{len(state.pcas) + 1:03d}"
    provenance = {"dataset_id": dataset_id, "plan_id": dataset.plan_id, "pca_id": pid}
    summary = {"pca_id": pid, **provenance,
               "scores_artifact": state.table(scores.reset_index(), "pca_scores", provenance),
               "loadings_artifact": state.table(loadings.reset_index(), "pca_loadings", provenance),
               "explained_variance_ratio": model.explained_variance_ratio_.tolist(),
               "note": "PCA is exploratory; visual separation alone does not establish significance."}
    state.pcas[pid] = summary
    return summary
