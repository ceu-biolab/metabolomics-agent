"""Group-wise descriptive statistics with explicit scale and cell provenance."""

from collections import Counter

import numpy as np
import pandas as pd

from .serialization import clean_json


CONVENTIONS = {
    "variance_ddof": 1,
    "sd_ddof": 1,
    "quantile_interpolation": "linear",
    "skewness": "adjusted Fisher-Pearson sample skewness; n >= 3; nonconstant",
    "kurtosis": "bias-corrected Fisher excess kurtosis (normal = 0); n >= 4; nonconstant",
    "cv": "sample SD / arithmetic mean; ratio, not percentage; positive mean; unlogged ratio scale only",
    "mode": "optional exact-value frequency; scalar only for a unique repeated maximum; no binning",
    "missing_values": "excluded from numerical summaries, never replaced with zero",
    "observed_only": "exclude saved imputation-mask cells; normalization can still depend on imputed cells",
}


def _source(state, args):
    raw = state.raw
    inputs = [aid for aid, artifact in state.artifacts.items()
              if artifact["kind"] in {"input_values", "input_metadata"}]
    if args.dataset_id == "raw":
        if args.scale != "original":
            raise ValueError("The raw dataset supports only the original scale.")
        return raw, raw.notna(), pd.DataFrame(False, index=raw.index, columns=raw.columns), {
            "plan_id": None, "transform": "none", "normalization": "none",
            "zero_as_missing": False, "ratio_scale": True, "input_artifact_ids": inputs,
        }
    if args.scale == "original":
        raise ValueError("Original-scale summaries require dataset_id='raw'.")
    dataset = state.dataset(args.dataset_id)
    if dataset.imputation_mask is None:
        raise ValueError("This dataset has no saved imputation mask; use a descriptive-enabled run.")
    plan = state.preprocessing_plans[dataset.plan_id]
    values = dataset.concentrations if args.scale == "concentrations" else dataset.testing
    observed = raw.loc[values.index, values.columns].notna()
    if plan.zero_as_missing:
        observed &= raw.loc[values.index, values.columns] != 0
    mask = dataset.imputation_mask
    if (not mask.index.equals(values.index) or not mask.columns.equals(values.columns)
            or mask.isna().any().any() or not all(dtype == bool for dtype in mask.dtypes)):
        raise ValueError("Imputation mask must be boolean and aligned with the prepared data.")
    if ((mask & observed) | (mask & values.isna()) |
            (values.notna() & ~observed & ~mask)).any().any():
        raise ValueError("Prepared values disagree with their saved observed/imputed provenance.")
    key = "concentrations" if args.scale == "concentrations" else "testing"
    inputs += [dataset.artifact_ids[key], dataset.artifact_ids["imputation_mask"]]
    transform = plan.transform if args.scale == "testing" else "none"
    return values, observed, mask, {
        "plan_id": dataset.plan_id, "transform": transform, "normalization": plan.normalization,
        "zero_as_missing": plan.zero_as_missing, "ratio_scale": transform == "none",
        "input_artifact_ids": inputs,
    }


def _metrics(values, *, ratio_scale, include_mode):
    """Return values plus a reason for every undefined requested statistic."""
    names = ["mean", "median", "sd", "variance", "min", "max", "range", "q1", "q3", "iqr",
             *[f"p{percent}" for percent in range(10, 100, 10)], "skewness", "kurtosis", "cv"]
    if include_mode:
        names.append("mode")
    result = {name: np.nan for name in names}
    reasons = {}
    n = len(values)
    if not n:
        reasons = {name: "no_observations" for name in names}
    else:
        quantiles = values.quantile([percent / 100 for percent in range(10, 100, 10)] + [.25, .75],
                                    interpolation="linear")
        result.update(mean=values.mean(), median=values.median(), min=values.min(), max=values.max(),
                      q1=quantiles.loc[.25], q3=quantiles.loc[.75])
        result["range"] = result["max"] - result["min"]
        result["iqr"] = result["q3"] - result["q1"]
        result.update({f"p{percent}": quantiles.loc[percent / 100] for percent in range(10, 100, 10)})
        if n >= 2:
            result.update(sd=values.std(ddof=1), variance=values.var(ddof=1))
        else:
            reasons.update(sd="insufficient_observations", variance="insufficient_observations")
        for name, minimum, function in [("skewness", 3, values.skew), ("kurtosis", 4, values.kurt)]:
            if n < minimum:
                reasons[name] = "insufficient_observations"
            elif values.nunique() <= 1:
                reasons[name] = "constant_values"
            else:
                result[name] = function()
        if not ratio_scale:
            reasons["cv"] = "not_ratio_scale"
        elif n < 2:
            reasons["cv"] = "insufficient_observations"
        elif result["mean"] == 0:
            reasons["cv"] = "zero_mean"
        elif result["mean"] < 0:
            reasons["cv"] = "nonpositive_mean"
        else:
            result["cv"] = result["sd"] / result["mean"]
        if include_mode:
            frequencies = values.value_counts()
            frequency = int(frequencies.iloc[0])
            modes = frequencies[frequencies == frequency]
            if frequency <= 1:
                reasons["mode"] = "no_repeated_values"
            elif len(modes) > 1:
                reasons["mode"] = "multiple_modes"
            else:
                result["mode"] = modes.index[0]
    if include_mode:
        frequencies = values.value_counts()
        result["mode_frequency"] = int(frequencies.iloc[0]) if n else 0
        result["mode_n_ties"] = int((frequencies == frequencies.iloc[0]).sum()) if n else 0
    for name in names:
        if name not in reasons and not np.isfinite(result[name]):
            result[name] = np.nan
            reasons[name] = "non_finite_result"
    return result, reasons


def summarize_dataset(state, args):
    values, observed, imputed, provenance = _source(state, args)
    features = args.features or list(values.columns)
    if len(set(features)) != len(features) or set(features) - set(values.columns):
        raise ValueError("Summary features must exist in the source and must be unique.")
    all_features = set(features) == set(values.columns)
    values = values.loc[:, features]
    if not np.isfinite(values.to_numpy()[values.notna().to_numpy()]).all():
        raise ValueError("Descriptive summaries require finite nonmissing source values.")
    labels = state.metadata.loc[values.index, state.config.group_column]
    groups = ([state.config.reference_group, state.config.treatment_group]
              if args.grouping == "group" else ["all_selected_samples"])
    numbers = [int(sid[1:]) for sid in state.descriptive_summaries]
    numbers += [int(path.name.split("_")[0][1:]) for path in state.output_dir.glob("s[0-9]*_summary.json")]
    for artifact in state.evidence.artifacts.values():
        previous = artifact.parameters.get("summary_id", "")
        if previous.startswith("s") and previous[1:].isdigit():
            numbers.append(int(previous[1:]))
    sid = f"s{max(numbers, default=0) + 1:03d}"
    parameters = {**args.model_dump(), "summary_id": sid}
    rows, undefined = [], []
    with state.evidence.operation("summarize_dataset", parameters=parameters,
                                  inputs=provenance["input_artifact_ids"]):
        for group in groups:
            selected = labels == group if args.grouping == "group" else pd.Series(True, index=values.index)
            for feature in features:
                source = values.loc[selected, feature]
                origin = observed.loc[selected, feature]
                mask = imputed.loc[selected, feature]
                keep = source.notna()
                if args.value_policy == "observed_only":
                    keep &= ~mask
                used = source.loc[keep]
                metrics, reasons = _metrics(used, ratio_scale=provenance["ratio_scale"],
                                             include_mode=args.include_mode)
                raw = state.raw.loc[source.index, feature]
                row = {
                    "summary_id": sid, "dataset_id": args.dataset_id, "scale": args.scale,
                    "grouping": args.grouping, "group": group, "feature": feature,
                    "value_policy": args.value_policy, "transform": provenance["transform"],
                    "normalization": provenance["normalization"],
                    "zero_as_missing": provenance["zero_as_missing"],
                    "n_total": len(source), "n_observed_input": int(origin.sum()),
                    "n_missing_input": int((~origin).sum()), "n_original_missing": int(raw.isna().sum()),
                    "n_zero_input": int((raw == 0).sum()), "n_available_source": int(source.notna().sum()),
                    "n_missing_source": int(source.isna().sum()), "n_imputed_source": int(mask.sum()),
                    "n_used": len(used), "n_observed_used": int((keep & origin).sum()),
                    "n_imputed_used": int((keep & mask).sum()),
                    "n_excluded_imputed": int((mask & ~keep).sum()),
                    "n_zero_source": int((source == 0).sum()), "n_zero_used": int((used == 0).sum()),
                    **metrics, "status": "no_observations" if not len(used) else "partial" if reasons else "ok",
                }
                rows.append(row)
                undefined.extend({"summary_id": sid, "dataset_id": args.dataset_id, "scale": args.scale,
                                  "group": group, "feature": feature, "statistic": name, "reason": reason}
                                 for name, reason in reasons.items())
        metadata = {"plan_id": provenance["plan_id"], "dataset_id": args.dataset_id,
                    "parent_ids": provenance["input_artifact_ids"], "scale": args.scale,
                    "parameters": parameters}
        table = state.table(pd.DataFrame(rows), "descriptive_statistics", metadata)
        reasons_table = state.table(pd.DataFrame(undefined, columns=[
            "summary_id", "dataset_id", "scale", "group", "feature", "statistic", "reason",
        ]), "descriptive_undefined", metadata)
        counts = dict(Counter(item["reason"] for item in undefined))
        result = {
            "summary_id": sid, "dataset_id": args.dataset_id, "scale": args.scale,
            "grouping": args.grouping, "value_policy": args.value_policy,
            "include_mode": args.include_mode, "all_features": all_features,
            "requested_features": args.features, "feature_count": len(features), "rows": len(rows),
            "preprocessing_plan_id": provenance["plan_id"], "transform": provenance["transform"],
            "zero_as_missing": provenance["zero_as_missing"],
            "artifacts": {"statistics": table, "undefined_reasons": reasons_table},
            "undefined_counts": counts, "conventions": CONVENTIONS,
            "notes": ["Observed-only summaries of prepared values can still depend on imputation through normalization."]
                     if args.dataset_id != "raw" else [],
        }
        if args.include_mode:
            result["notes"].append("Exact-value modes can be uninformative for continuous measurements; ties are not resolved arbitrarily.")
        filename = f"{sid}_summary.json"
        state.write_json(filename, result)
        record = state.evidence.register_file(filename, "descriptive_metadata",
                                               parent_ids=[table, reasons_table],
                                               parameters=parameters, plan_id=provenance["plan_id"])
        result["metadata_artifact_id"] = record.id
        state.descriptive_summaries[sid] = clean_json(result)
        for reason, count in counts.items():
            severity = "warning" if reason in {
                "no_observations", "insufficient_observations", "non_finite_result",
            } else "info"
            state.evidence.check(f"DESCRIPTIVE_{reason.upper()}", 0, count, passed=False,
                                 message=f"{sid}: {count} undefined statistics ({reason}) on {args.dataset_id}/{args.scale}; see {reasons_table}.",
                                 severity=severity)
    return clean_json(result)
