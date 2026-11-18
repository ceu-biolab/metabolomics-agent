"""Shared validation of prepared scales and observed/imputed cell origins."""


def prepared_source(state, dataset_id, scale):
    if scale not in {"concentrations", "testing"}:
        raise ValueError("Prepared sources require concentration or testing scale.")
    dataset = state.dataset(dataset_id)
    if dataset.imputation_mask is None or "imputation_mask" not in dataset.artifact_ids:
        raise ValueError("This dataset has no saved imputation mask; use a provenance-enabled run.")
    plan = state.preprocessing_plans[dataset.plan_id]
    values = dataset.concentrations if scale == "concentrations" else dataset.testing
    raw = state.raw.loc[values.index, values.columns]
    observed = raw.notna()
    if plan.zero_as_missing:
        observed &= raw != 0
    mask = dataset.imputation_mask
    if (not mask.index.equals(values.index) or not mask.columns.equals(values.columns)
            or mask.isna().any().any() or not all(dtype == bool for dtype in mask.dtypes)):
        raise ValueError("Imputation mask must be boolean and aligned with the prepared data.")
    if ((mask & observed) | (mask & values.isna()) |
            (values.notna() & ~observed & ~mask)).any().any():
        raise ValueError("Prepared values disagree with their saved observed/imputed provenance.")
    inputs = [aid for aid, artifact in state.artifacts.items()
              if artifact["kind"] in {"input_values", "input_metadata"}]
    key = "concentrations" if scale == "concentrations" else "testing"
    inputs += [dataset.artifact_ids[key], dataset.artifact_ids["imputation_mask"]]
    transform = plan.transform if scale == "testing" else "none"
    return values, observed, mask, {
        "plan_id": dataset.plan_id, "transform": transform, "normalization": plan.normalization,
        "zero_as_missing": plan.zero_as_missing, "ratio_scale": transform == "none",
        "input_artifact_ids": inputs,
    }
