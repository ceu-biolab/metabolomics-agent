"""Audited two-group inference; legacy runs retain their original implementation."""

import hashlib
import warnings
from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from .provenance import prepared_source
from .serialization import clean_json


def _source(state, dataset_id):
    values, observed, mask, provenance = prepared_source(state, dataset_id, "testing")
    concentrations = state.dataset(dataset_id).concentrations
    if (not values.index.equals(state.metadata.index) or
            not concentrations.index.equals(values.index) or
            not concentrations.columns.equals(values.columns)):
        raise ValueError("Univariate inputs and metadata must be aligned.")
    for frame in [values, concentrations]:
        if not np.isfinite(frame.to_numpy()[frame.notna().to_numpy()]).all():
            raise ValueError("Univariate inputs must contain finite nonmissing values.")
    provenance["input_artifact_ids"].append(state.datasets[dataset_id].artifact_ids["concentrations"])
    return values, observed, mask, provenance


def _fingerprint(state, dataset_id, values, observed, mask):
    digest = hashlib.sha256()
    columns = [state.config.group_column]
    if state.config.pair_column:
        columns.append(state.config.pair_column)
    for frame in [values, state.datasets[dataset_id].concentrations, observed, mask, state.metadata[columns],
                  state.raw.loc[values.index, values.columns]]:
        digest.update(str(list(frame.columns)).encode())
        digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    return digest.hexdigest()


def _feature_data(state, values, observed, mask, feature):
    cfg, meta = state.config, state.metadata
    ref_mask = meta[cfg.group_column] == cfg.reference_group
    treat_mask = meta[cfg.group_column] == cfg.treatment_group
    ref = values.loc[ref_mask, feature].dropna()
    treat = values.loc[treat_mask, feature].dropna()
    counts = {"feature": feature}
    for name, selected in [("reference", ref_mask), ("treatment", treat_mask)]:
        available = values.loc[selected, feature].notna()
        counts.update({
            f"n_total_{name}": int(selected.sum()),
            f"n_available_{name}": int(available.sum()),
            f"n_observed_{name}": int(observed.loc[selected, feature].sum()),
            f"n_imputed_available_{name}": int(mask.loc[selected, feature].sum()),
            f"n_missing_{name}": int((~available).sum()),
        })
    if cfg.design == "paired":
        def wide(series):
            table = meta[[cfg.pair_column, cfg.group_column]].copy()
            table["value"] = series
            return table.pivot(index=cfg.pair_column, columns=cfg.group_column, values="value")

        source = wide(values[feature])
        obs = wide(observed[feature])
        imp = wide(mask[feature])
        complete = source[[cfg.reference_group, cfg.treatment_group]].notna().all(axis=1)
        both_observed = obs[[cfg.reference_group, cfg.treatment_group]].all(axis=1)
        ref, treat = source.loc[complete, cfg.reference_group], source.loc[complete, cfg.treatment_group]
        counts.update(n_pairs_total=len(source), n_pairs_used=int(complete.sum()),
                      n_observed_pairs=int(both_observed.sum()),
                      n_observed_pairs_used=int((both_observed & complete).sum()),
                      n_imputed_pairs_used=int((complete & imp.any(axis=1)).sum()))
        for name, group in [("reference", cfg.reference_group), ("treatment", cfg.treatment_group)]:
            counts[f"n_observed_used_{name}"] = int(obs.loc[complete, group].sum())
            counts[f"n_imputed_used_{name}"] = int(imp.loc[complete, group].sum())
        differences = treat - ref
        observed_differences = differences.loc[both_observed.loc[differences.index]]
        counts["n_zero_differences"] = int((differences == 0).sum())
        counts["n_nonzero_differences"] = int((differences != 0).sum())
    else:
        for name, series in [("reference", ref), ("treatment", treat)]:
            counts[f"n_observed_used_{name}"] = int(observed.loc[series.index, feature].sum())
            counts[f"n_imputed_used_{name}"] = int(mask.loc[series.index, feature].sum())
        differences = observed_differences = pd.Series(dtype=float)
    counts["n_reference"], counts["n_treatment"] = len(ref), len(treat)
    pooled = pd.concat([ref, treat], ignore_index=True)
    counts["n_tied_used_values"] = len(pooled) - pooled.nunique()
    if cfg.design == "paired":
        nonzero = differences.loc[differences != 0].abs()
        counts["n_tied_absolute_nonzero_differences"] = len(nonzero) - nonzero.nunique()
    if min(counts["n_observed_reference"], counts["n_observed_treatment"]) < 3:
        counts["eligibility"] = "insufficient_observed_data"
    elif cfg.design == "paired" and counts["n_observed_pairs"] < 3:
        counts["eligibility"] = "insufficient_observed_pairs"
    elif min(len(ref), len(treat)) < 3:
        counts["eligibility"] = "insufficient_observations"
    else:
        counts["eligibility"] = "eligible"
    return ref, treat, counts, differences, observed_differences


def _normality(values):
    if not 3 <= len(values) <= 5000:
        return np.nan, "outside_3_to_5000_observations"
    if values.nunique() <= 1:
        return np.nan, "constant_values"
    result = stats.shapiro(values).pvalue
    return (float(result), "ok") if np.isfinite(result) else (np.nan, "non_finite_result")


def _next_id(state, prefix, collection, parameter):
    numbers = [int(key[len(prefix):]) for key in collection]
    for artifact in state.evidence.artifacts.values():
        value = artifact.parameters.get(parameter, "")
        if value.startswith(prefix) and value[len(prefix):].isdigit():
            numbers.append(int(value[len(prefix):]))
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def inspect_univariate(state, dataset_id, normality_method="shapiro"):
    if state.config.analysis_scope != "two_group":
        raise ValueError("Univariate comparisons require an explicit two-group loading scope.")
    if normality_method not in {"shapiro", "lilliefors_norm"}:
        raise ValueError("Unsupported normality diagnostic.")
    values, observed, mask, provenance = _source(state, dataset_id)
    uid = _next_id(state, "uv", state.univariate_diagnostics, "diagnostic_id")
    rows = []
    with state.evidence.operation("inspect_univariate", inputs=provenance["input_artifact_ids"]):
        for feature in values:
            ref, treat, counts, differences, observed_differences = _feature_data(
                state, values, observed, mask, feature)
            row = dict(counts)
            for name, series in [("reference", ref), ("treatment", treat)]:
                row[f"variance_{name}"] = series.var(ddof=1)
                row[f"variance_status_{name}"] = ("insufficient_observations" if len(series) < 2 else
                                                   "non_finite_result" if not np.isfinite(row[f"variance_{name}"]) else "ok")
                if not np.isfinite(row[f"variance_{name}"]):
                    row[f"variance_{name}"] = np.nan
                row[f"shapiro_p_{name}"], row[f"shapiro_status_{name}"] = _normality(series)
                if normality_method == "lilliefors_norm":
                    from statsmodels.stats.diagnostic import kstest_normal
                    populations = [(name, series)]
                    if state.config.design == "independent":
                        populations.append((f"observed_{name}", series.loc[observed.loc[series.index, feature]]))
                    for suffix, selected in populations:
                        usable = len(selected) >= 4 and selected.nunique() > 1
                        statistic, pvalue = kstest_normal(selected, "norm") if usable else (np.nan, np.nan)
                        row[f"lilliefors_statistic_{suffix}"] = statistic
                        row[f"lilliefors_p_{suffix}"] = pvalue
                        row[f"lilliefors_status_{suffix}"] = "ok" if usable and np.isfinite(pvalue) else "insufficient_or_constant_values"
                only_observed = series.loc[observed.loc[series.index, feature]] if state.config.design == "independent" else pd.Series(dtype=float)
                if state.config.design == "independent":
                    row[f"shapiro_observed_p_{name}"], row[f"shapiro_observed_status_{name}"] = _normality(only_observed)
            if state.config.design == "paired":
                row["shapiro_p_differences"], row["shapiro_status_differences"] = _normality(differences)
                row["shapiro_observed_p_differences"], row["shapiro_observed_status_differences"] = _normality(observed_differences)
                if normality_method == "lilliefors_norm":
                    for suffix, selected in [("differences", differences), ("observed_differences", observed_differences)]:
                        usable = len(selected) >= 4 and selected.nunique() > 1
                        statistic, pvalue = kstest_normal(selected, "norm") if usable else (np.nan, np.nan)
                        row[f"lilliefors_statistic_{suffix}"] = statistic
                        row[f"lilliefors_p_{suffix}"] = pvalue
                        row[f"lilliefors_status_{suffix}"] = "ok" if usable and np.isfinite(pvalue) else "insufficient_or_constant_values"
            rows.append(row)
        frame = pd.DataFrame(rows)
        aid = state.table(frame, "univariate_diagnostics", {
            "dataset_id": dataset_id, "plan_id": provenance["plan_id"], "scale": "testing",
            "parent_ids": provenance["input_artifact_ids"], "parameters": {"diagnostic_id": uid},
        })
        result = {
            "normality_method": normality_method,
            "diagnostic_id": uid, "dataset_id": dataset_id,
            "preprocessing_plan_id": provenance["plan_id"], "transform": provenance["transform"],
            "artifacts": {"diagnostics": aid},
            "features": len(frame), "features_eligible": int((frame.eligibility == "eligible").sum()),
            "features_ineligible": int((frame.eligibility != "eligible").sum()),
            "features_with_imputed_values": int(((frame.n_imputed_used_reference + frame.n_imputed_used_treatment) > 0).sum()),
            "fingerprint": _fingerprint(state, dataset_id, values, observed, mask),
            "notes": ["Eligibility uses at least three original observed values per group, and three observed complete pairs for paired designs.",
                      "Normality diagnostics do not prove normality or automatically choose a test. Paired t assumptions concern differences.",
                      "Prepared-value normality diagnostics may be affected by imputation; observed-origin diagnostics are identified separately."],
        }
        filename = f"{uid}_diagnostics.json"
        state.write_json(filename, result)
        document = state.evidence.register_file(filename, "univariate_diagnostic_metadata", parent_ids=[aid],
                                                 parameters={"diagnostic_id": uid})
        result["metadata_artifact_id"] = document.id
        state.univariate_diagnostics[uid] = clean_json(result)
    return clean_json(result)


def _calculate(ref, treat, plan, feature, issues):
    extra = {"df": np.nan, "ci_level": 1 - plan.alpha}
    row = {"statistic": np.nan, "p_value": np.nan, "hedges_g": np.nan, "rank_biserial": np.nan,
           "ci_low_test_scale": np.nan, "ci_high_test_scale": np.nan}
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        if plan.test in {"welch_t", "student_t"}:
            result = stats.ttest_ind(treat, ref, equal_var=plan.test == "student_t")
            interval = result.confidence_interval(confidence_level=1 - plan.alpha)
            row.update(ci_low_test_scale=interval.low, ci_high_test_scale=interval.high)
            extra["df"] = result.df
            df = len(treat) + len(ref) - 2
            pooled_sd = np.sqrt(((len(treat) - 1) * treat.var(ddof=1) +
                                 (len(ref) - 1) * ref.var(ddof=1)) / df)
            if pooled_sd > 0:
                row["hedges_g"] = (1 - 3 / (4 * df - 1)) * (treat.mean() - ref.mean()) / pooled_sd
        elif plan.test == "mannwhitney":
            result = stats.mannwhitneyu(treat, ref, alternative="two-sided", method="auto", use_continuity=True)
            row["rank_biserial"] = 2 * result.statistic / (len(treat) * len(ref)) - 1
        elif plan.test == "paired_t":
            result = stats.ttest_rel(treat, ref)
            interval = result.confidence_interval(confidence_level=1 - plan.alpha)
            row.update(ci_low_test_scale=interval.low, ci_high_test_scale=interval.high)
            extra["df"] = result.df
        else:
            if np.allclose(treat.to_numpy() - ref.to_numpy(), 0, atol=0, rtol=0):
                result = (0.0, 1.0)
            else:
                result = stats.wilcoxon(treat, ref, alternative="two-sided", zero_method="wilcox",
                                        correction=False, method="auto")
        row["statistic"], row["p_value"] = result[:2]
    issues.extend({"feature": feature, "code": "SCIPY_WARNING", "severity": "warning",
                   "metric": "test", "reason": f"{item.category.__name__}: {item.message}"[:500]}
                  for item in captured)
    return row, extra


def _undefined(table, audits, plan, issues, correction_done):
    indexed = {audit["feature"]: audit for audit in audits}
    for row in table.to_dict(orient="records"):
        audit = indexed[row["feature"]]
        for metric in ["log2_fold_change", "statistic", "p_value", "adjusted_p_value",
                       "hedges_g", "rank_biserial", "ci_low_test_scale", "ci_high_test_scale"]:
            if np.isfinite(row[metric]):
                continue
            if metric == "log2_fold_change":
                reason = ("nonpositive_group_mean" if row["mean_reference"] <= 0 or row["mean_treatment"] <= 0
                          else "undefined_group_mean" if not (np.isfinite(row["mean_reference"]) and np.isfinite(row["mean_treatment"]))
                          else "non_finite_result")
            elif row["status"] != "ok":
                reason = row["status"]
            elif metric == "adjusted_p_value" and not correction_done:
                reason = "correction_not_completed"
            elif metric == "hedges_g" and plan.test not in {"welch_t", "student_t"}:
                reason = "not_implemented_for_this_test"
            elif metric == "rank_biserial" and plan.test != "mannwhitney":
                reason = "not_implemented_for_this_test"
            elif metric.startswith("ci_") and plan.test not in {"welch_t", "student_t", "paired_t"}:
                reason = "not_implemented_for_this_test"
            elif metric == "hedges_g":
                reason = "zero_or_nonfinite_pooled_sd"
            else:
                reason = "non_finite_result"
            severity = "info" if reason == "not_implemented_for_this_test" else "warning"
            issues.append({"feature": row["feature"], "code": "UNDEFINED_METRIC", "severity": severity,
                           "metric": metric, "reason": reason})
        audit["status"] = row["status"]


def compare_groups(state, dataset_id, plan_id):
    if plan_id not in state.univariate_plans:
        raise ValueError("Register a current univariate plan before testing.")
    plan = state.univariate_plans[plan_id]
    if plan.dataset_id != dataset_id:
        raise ValueError("The test plan and dataset do not agree.")
    if (any(item.get("univariate_plan_id") == plan_id for item in state.comparisons.values()) or
            any(item["plan_id"] == plan_id for item in state.univariate_attempts.values())):
        raise ValueError("This statistical plan has already been executed.")
    if (state.comparisons or any(item.get("tests_executed", 0) for item in state.univariate_attempts.values())) and plan.purpose != "exploratory":
        raise ValueError("Analyses after testing must be recorded as exploratory.")
    values, observed, mask, provenance = _source(state, dataset_id)
    diagnostic = state.univariate_diagnostics[plan.diagnostic_id]
    if diagnostic["dataset_id"] != dataset_id or diagnostic["fingerprint"] != _fingerprint(state, dataset_id, values, observed, mask):
        raise ValueError("Data changed after univariate diagnostics; request a reviewed revision.")
    state.evidence.validate()
    uid = _next_id(state, "ua", state.univariate_attempts, "univariate_attempt_id")
    parameters = {"univariate_attempt_id": uid, **plan.model_dump()}
    parents = provenance["input_artifact_ids"] + list(diagnostic["artifacts"].values())
    artifact_provenance = {"dataset_id": dataset_id, "plan_id": plan_id, "parent_ids": parents,
                           "scale": "testing", "parameters": parameters}
    rows, audits, issues, artifacts = [], [], [], {}
    table = pd.DataFrame()
    correction_done = False
    failure = None
    tests_executed = 0
    cid = f"c{len(state.comparisons) + 1:03d}"
    try:
        with state.evidence.operation("univariate_tests", parameters=parameters, inputs=parents):
            for feature in values:
                ref, treat, counts, differences, _ = _feature_data(state, values, observed, mask, feature)
                concentration = state.datasets[dataset_id].concentrations[feature]
                groups = state.metadata[state.config.group_column]
                ref_mean = concentration[groups == state.config.reference_group].mean()
                treat_mean = concentration[groups == state.config.treatment_group].mean()
                row = {
                    "feature": feature, "test": plan.test, "correction": plan.correction,
                    "purpose": plan.purpose, "n_reference": len(ref), "n_treatment": len(treat),
                    "n_observed_reference": counts["n_observed_reference"],
                    "n_observed_treatment": counts["n_observed_treatment"],
                    "mean_reference": ref_mean, "mean_treatment": treat_mean,
                    "median_reference": concentration[groups == state.config.reference_group].median(),
                    "median_treatment": concentration[groups == state.config.treatment_group].median(),
                    "log2_fold_change": np.log2(treat_mean / ref_mean) if ref_mean > 0 and treat_mean > 0 else np.nan,
                    "mean_difference_test_scale": treat.mean() - ref.mean(),
                    "statistic": np.nan, "p_value": np.nan, "adjusted_p_value": np.nan,
                    "hedges_g": np.nan, "rank_biserial": np.nan,
                    "ci_low_test_scale": np.nan, "ci_high_test_scale": np.nan,
                    "status": "ok" if counts["eligibility"] == "eligible" else counts["eligibility"],
                }
                if state.config.design == "paired":
                    row["n_observed_pairs"] = counts["n_observed_pairs"]
                audit = {**counts, "df": np.nan, "ci_level": 1 - plan.alpha,
                         "test_executed": row["status"] == "ok",
                         "n_test_reference": len(ref) if row["status"] == "ok" else 0,
                         "n_test_treatment": len(treat) if row["status"] == "ok" else 0,
                         "n_test_imputed_reference": counts["n_imputed_used_reference"] if row["status"] == "ok" else 0,
                         "n_test_imputed_treatment": counts["n_imputed_used_treatment"] if row["status"] == "ok" else 0,
                         "test_scale_transform": provenance["transform"],
                         "fold_change_population": "all_available_prepared_group_values"}
                if row["status"] == "ok":
                    tests_executed += 1
                    result, extra = _calculate(ref, treat, plan, feature, issues)
                    row.update(result)
                    audit.update(extra)
                    if not (np.isfinite(row["statistic"]) and np.isfinite(row["p_value"])
                            and 0 <= row["p_value"] <= 1):
                        row["status"] = "undefined_test"
                        row["p_value"] = np.nan
                        row["ci_low_test_scale"] = row["ci_high_test_scale"] = np.nan
                        issues.append({"feature": feature, "code": "INVALID_TEST_RESULT", "severity": "warning",
                                       "metric": "test", "reason": "A finite statistic and a p-value in [0,1] are required."})
                    elif plan.test in {"welch_t", "student_t", "paired_t"} and not (
                        np.isfinite(row["ci_low_test_scale"]) and np.isfinite(row["ci_high_test_scale"])
                        and row["ci_low_test_scale"] <= row["ci_high_test_scale"]
                    ):
                        row["ci_low_test_scale"] = row["ci_high_test_scale"] = np.nan
                audit["df_reason"] = ("not_applicable" if plan.test not in {"welch_t", "student_t", "paired_t"}
                                      else "not_tested" if counts["eligibility"] != "eligible"
                                      else "non_finite_result" if not np.isfinite(audit["df"]) else "ok")
                if not np.isfinite(audit["df"]):
                    audit["df"] = np.nan
                if audit["test_executed"] and counts["n_imputed_used_reference"] + counts["n_imputed_used_treatment"]:
                    issues.append({"feature": feature, "code": "IMPUTATION_IN_TEST", "severity": "warning",
                                   "metric": "inference", "reason": "Imputation uncertainty is not propagated into p-values or intervals."})
                if state.config.design == "paired" and counts["n_zero_differences"]:
                    issues.append({"feature": feature, "code": "ZERO_PAIRED_DIFFERENCES", "severity": "info",
                                   "metric": "inference", "reason": "Zero differences are present; Wilcoxon uses zero_method=wilcox and the existing all-zero (0,1) convention."})
                if plan.test in {"mannwhitney", "wilcoxon"} and counts["n_tied_used_values"]:
                    issues.append({"feature": feature, "code": "RANK_TIES", "severity": "info",
                                   "metric": "inference", "reason": "Ties are present; rank methods use the recorded SciPy auto behavior."})
                if plan.test == "wilcoxon" and counts["n_nonzero_differences"] < 3:
                    issues.append({"feature": feature, "code": "LOW_NONZERO_PAIR_COUNT", "severity": "warning",
                                   "metric": "inference", "reason": "Fewer than three nonzero paired differences remain; observed pairs and effective ranks are distinct."})
                if state.config.design == "paired" and max(counts["n_available_reference"], counts["n_available_treatment"]) > len(ref):
                    issues.append({"feature": feature, "code": "FOLD_CHANGE_POPULATION", "severity": "warning",
                                   "metric": "log2_fold_change", "reason": "Fold changes use all available group concentrations; paired inference uses complete prepared pairs."})
                for key, value in row.items():
                    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
                        row[key] = np.nan
                rows.append(row)
                audits.append(audit)
            table = pd.DataFrame(rows)
            artifacts["raw_tests"] = state.table(table, "univariate_raw", artifact_provenance)
        valid = table.p_value.notna()
        if not valid.any():
            failure = "no_valid_tests"
            raise ValueError("No features could be tested. Complete raw results and audit evidence are preserved.")
        with state.evidence.operation("multiple_testing_correction", parameters={
            "method": plan.correction, "alpha": plan.alpha, "family": plan.hypothesis_family,
            "features": table.loc[valid, "feature"].tolist(),
        }, inputs=[artifacts["raw_tests"]]):
            adjusted = multipletests(table.loc[valid, "p_value"], alpha=plan.alpha, method=plan.correction)[1]
            if not np.isfinite(adjusted).all() or ((adjusted < 0) | (adjusted > 1)).any():
                raise ValueError("Multiplicity correction returned invalid adjusted p-values.")
            table.loc[valid, "adjusted_p_value"] = adjusted
            correction_done = True
    except BaseException as error:
        failure = failure or ("interrupted" if not isinstance(error, Exception) else "execution_failed")
        raise
    finally:
        if rows:
            if table.empty:
                table = pd.DataFrame(rows)
            if "raw_tests" not in artifacts:
                artifacts["raw_tests"] = state.table(table, "univariate_partial_raw", artifact_provenance)
            _undefined(table, audits, plan, issues, correction_done)
            artifacts["audit"] = state.table(pd.DataFrame(audits), "univariate_audit", artifact_provenance)
            artifacts["issues"] = state.table(pd.DataFrame(issues, columns=[
                "feature", "code", "severity", "metric", "reason",
            ]), "univariate_issues", artifact_provenance)
            if not failure:
                table["significant"] = table.adjusted_p_value < plan.alpha
                table = table.sort_values("adjusted_p_value", na_position="last")
                artifacts["comparison"] = state.table(table, "comparison", {
                    **artifact_provenance, "comparison_id": cid, "univariate_plan_id": plan_id,
                    "preprocessing_plan_id": provenance["plan_id"],
                    "parent_ids": [artifacts["raw_tests"], artifacts["audit"], artifacts["issues"]],
                })
            attempted = {
                "attempt_id": uid, "dataset_id": dataset_id, "plan_id": plan_id,
                "diagnostic_id": plan.diagnostic_id, "status": "failed" if failure else "completed",
                "failure_reason": failure, "artifacts": artifacts,
                "features_tested": int(table.p_value.notna().sum()) if correction_done else 0,
                "features_skipped": int(table.p_value.isna().sum()),
                "tests_executed": tests_executed,
                "features_in_attempt": len(rows),
                "features_with_raw_p": int(table.p_value.notna().sum()),
                "correction_completed": correction_done,
                "issue_counts": dict(Counter(item["code"] for item in issues)),
                "methods": {**plan.model_dump(), "confidence_intervals": "feature-wise, unadjusted, on test scale",
                            "hedges_g": "pooled sample SD with small-sample factor 1-3/(4*(n_reference+n_treatment-2)-1)",
                            "fold_change": "log2 ratio of arithmetic means of all available unlogged prepared group concentrations",
                            "rank_method": ("SciPy auto; exact/asymptotic selection is delegated to the recorded library version"
                                            if plan.test in {"mannwhitney", "wilcoxon"} else "not_applicable"),
                            "observed_minimum": 3, "scipy_version": state.run_info["packages"]["scipy"]},
                "family_members": table.loc[table.p_value.notna(), "feature"].tolist(),
                "family_excluded": table.loc[table.p_value.isna(), "feature"].tolist(),
            }
            filename = f"{uid}_univariate.json"
            state.write_json(filename, attempted)
            metadata = state.evidence.register_file(filename, "univariate_metadata", parent_ids=list(artifacts.values()),
                                                      parameters={"univariate_attempt_id": uid})
            attempted["metadata_artifact_id"] = metadata.id
            state.univariate_attempts[uid] = clean_json(attempted)
            state.workflow.data.univariate_attempt_ids.append(uid)
            for code, count in attempted["issue_counts"].items():
                selected = [issue for issue in issues if issue["code"] == code]
                severity = "warning" if any(issue["severity"] == "warning" for issue in selected) else "info"
                state.evidence.check(code, 0, count, passed=False,
                                     message=f"{uid}: {count} feature/metric issues ({code}); see {artifacts['issues']}.",
                                     severity=severity)
            if failure:
                state.evidence.check("UNIVARIATE_INCOMPLETE", True, False, passed=False,
                                     message=f"{uid}: no completed inferential result ({failure}); partial evidence is preserved.", severity="error")
    provenance_out = {"dataset_id": dataset_id, "plan_id": plan_id, "comparison_id": cid,
                      "univariate_plan_id": plan_id, "preprocessing_plan_id": provenance["plan_id"],
                      "parent_ids": [artifacts["raw_tests"], artifacts["audit"], artifacts["issues"]]}
    aid = artifacts["comparison"]
    result = {"comparison_id": cid, **provenance_out, "artifact_id": aid,
              "univariate_attempt_id": uid, "audit_artifacts": artifacts,
              "test": plan.test, "correction": plan.correction, "alpha": plan.alpha, "purpose": plan.purpose,
              "features_tested": int(table.p_value.notna().sum()), "features_skipped": int(table.p_value.isna().sum()),
              "significant_features": int(table.significant.sum()),
              "direction": f"{state.config.treatment_group} relative to {state.config.reference_group}",
              "top_features": clean_json(table.head(5).to_dict(orient="records")),
              "notes": ["Confidence intervals are feature-wise and unadjusted on the test scale.",
                        "Multiplicity correction covers valid features in this comparison only, not adaptive reruns.",
                        "Observed counts and imputed/paired counts are distinct; inspect the saved audit."]}
    state.comparisons[cid] = result
    return result
