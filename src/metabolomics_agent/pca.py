"""Exploratory PCA with frozen sources, independent scaling and durable checkpoints."""

import hashlib

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from .provenance import prepared_source
from .serialization import clean_json


def _next(state, prefix, collection, parameter):
    used = [int(key[len(prefix):]) for key in collection]
    for artifact in state.evidence.artifacts.values():
        value = artifact.parameters.get(parameter, "")
        if value.startswith(prefix) and value[len(prefix):].isdigit():
            used.append(int(value[len(prefix):]))
    return f"{prefix}{max(used, default=0)+1:03d}"


def _source(state, args):
    values, observed, mask, provenance = prepared_source(state, args.dataset_id, args.scale)
    features = args.features or list(values.columns)
    if not features or set(features) - set(values.columns):
        raise ValueError("PCA features must exist in the current prepared source.")
    if not values.index.equals(state.metadata.index):
        raise ValueError("PCA source and metadata must be aligned.")
    return values[features].copy(), observed[features], mask[features], provenance


def _fingerprint(values, mask, provenance):
    digest = hashlib.sha256()
    for frame in [values, mask]:
        digest.update(str(list(frame.columns)).encode())
        digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    digest.update(str((provenance['plan_id'], provenance['transform'], provenance['normalization'])).encode())
    return digest.hexdigest()


def inspect_pca_inputs(state, args):
    values, observed, mask, provenance = _source(state, args)
    uid = _next(state, "pv", state.pca_diagnostics, "pca_diagnostic_id")
    finite = np.isfinite(values.to_numpy()).all()
    variance = values.var(ddof=0)
    complete = not values.isna().any().any()
    viable = bool(finite and complete and (variance > 0).all() and np.isfinite(variance).all()
                  and min(len(values)-1, len(values.columns)) >= 2)
    with state.evidence.operation("inspect_pca_inputs", inputs=provenance["input_artifact_ids"]):
        table = pd.DataFrame({"feature": values.columns, "n_available": values.notna().sum().to_numpy(),
                              "n_observed": observed.sum().to_numpy(), "n_imputed": mask.sum().to_numpy(),
                              "n_missing": values.isna().sum().to_numpy(), "variance_ddof0": variance.to_numpy()})
        aid = state.table(table, "pca_diagnostics", {"parent_ids": provenance["input_artifact_ids"],
                          "dataset_id": args.dataset_id, "scale": args.scale,
                          "parameters": {"pca_diagnostic_id": uid}})
        result = {"diagnostic_id": uid, "dataset_id": args.dataset_id, "scale": args.scale,
                  "requested_features": args.features, "all_features": not args.features,
                  "feature_count": len(values.columns), "samples": len(values),
                  "missing_cells": int(values.isna().sum().sum()), "imputed_cells": int(mask.sum().sum()),
                  "constant_features": int((variance == 0).sum()), "finite": bool(finite), "viable": viable,
                  "max_components": min(len(values)-1, len(values.columns)),
                  "fingerprint": _fingerprint(values, mask, provenance), "artifacts": {"diagnostics": aid},
                  "note": "PCA requires complete finite data, nonconstant selected features and sufficient numerical rank. No group labels or p-values enter fitting."}
        filename = f"{uid}_pca_diagnostic.json"
        state.write_json(filename, result)
        document = state.evidence.register_file(filename, "pca_diagnostic_metadata", parent_ids=[aid])
        result["metadata_artifact_id"] = document.id
        state.pca_diagnostics[uid] = clean_json(result)
    return clean_json(result)


def run_pca(state, dataset_id, plan_id):
    plan = state.pca_plans[plan_id]
    if dataset_id != plan.dataset_id:
        raise ValueError("PCA plan and dataset must match.")
    if any(attempt["plan_id"] == plan_id for attempt in state.pca_attempts.values()):
        raise ValueError("This PCA plan has already been attempted; request a revision.")
    values, observed, mask, provenance = _source(state, plan)
    diagnostic = state.pca_diagnostics[plan.diagnostic_id]
    if _fingerprint(values, mask, provenance) != diagnostic["fingerprint"]:
        raise ValueError("PCA source changed after inspection; request a revision.")
    if not diagnostic["viable"] or plan.components > diagnostic["max_components"]:
        raise ValueError("PCA requires viable inputs and enough samples/features for the requested components.")
    pid = _next(state, "pc", state.pca_attempts, "pca_id")
    artifacts = {}
    result = {"pca_id": pid, "dataset_id": dataset_id, "plan_id": plan_id, "status": "running",
              "scale": plan.scale, "scaling": plan.scaling, "scaling_ddof": plan.scaling_ddof,
              "samples": len(values), "feature_count": len(values.columns), "components": plan.components,
              "imputed_cells": int(mask.sum().sum()), "artifacts": artifacts,
              "purpose": "exploratory", "transform": provenance["transform"],
              "notes": ["PCA is exploratory; visual separation is not a significance test or independent validation.",
                        "Group labels are added after fitting. Ordinary PCA does not model paired dependence."]}
    if result["imputed_cells"]:
        result["notes"].append("PCA depends on prepared imputed cells; imputation uncertainty is not propagated.")
    state.pca_attempts[pid] = result
    try:
        with state.evidence.operation("save_pca_input", inputs=provenance["input_artifact_ids"]):
            artifacts["input"] = state.table(values.reset_index(), "pca_input", {
                "parent_ids": provenance["input_artifact_ids"], "dataset_id": dataset_id,
                "plan_id": plan_id, "scale": plan.scale, "parameters": {"pca_id": pid}})
        state.evidence.check("PCA_IMPUTATION_USED", 0, result["imputed_cells"],
            passed=result["imputed_cells"] == 0,
            message="PCA includes prepared imputed cells; these are not additional original measurements and their uncertainty is not propagated.")
        with state.evidence.operation("prepare_pca_matrix", inputs=[artifacts["input"]]):
            scaler = StandardScaler(with_mean=True, with_std=plan.scaling == "auto" and plan.scaling_ddof == 0)
            scaler.fit(values.to_numpy())
            centers = scaler.mean_
            if plan.scaling == "none":
                divisors = np.ones(len(values.columns))
            elif plan.scaling == "auto" and plan.scaling_ddof == 0:
                divisors = scaler.scale_
            else:
                divisors = values.std(ddof=plan.scaling_ddof).to_numpy()
                if plan.scaling == "pareto":
                    divisors = np.sqrt(divisors)
            if not np.isfinite(divisors).all() or (divisors <= 0).any():
                raise ValueError("PCA scaling divisors must be finite and positive.")
            matrix = (values - centers) / divisors
            if not np.isfinite(matrix.to_numpy()).all():
                raise ValueError("PCA matrix contains nonfinite values.")
            artifacts["scaling"] = state.table(pd.DataFrame({"feature": values.columns,
                "center": centers, "divisor": divisors}), "pca_scaling", {
                    "parent_ids": [artifacts["input"]], "plan_id": plan_id, "dataset_id": dataset_id})
            artifacts["matrix"] = state.table(matrix.reset_index(), "pca_matrix", {
                "parent_ids": [artifacts["input"], artifacts["scaling"]], "plan_id": plan_id,
                "dataset_id": dataset_id, "scale": "pca_only"})
            rank = int(np.linalg.matrix_rank(matrix.to_numpy()))
            result["numerical_rank"] = rank
            if plan.components > rank:
                raise ValueError(f"PCA requested {plan.components} components but numerical rank is {rank}.")
        with state.evidence.operation("fit_pca", inputs=[artifacts["matrix"]]):
            model = PCA(n_components=plan.components, svd_solver=plan.svd_solver,
                        whiten=plan.whiten, random_state=state.seed)
            coordinates = model.fit_transform(matrix.to_numpy())
            if not (np.isfinite(coordinates).all() and np.isfinite(model.explained_variance_ratio_).all()):
                raise ValueError("PCA returned nonfinite scores or explained variance.")
            names = [f"PC{i+1}" for i in range(plan.components)]
            scores = pd.DataFrame(coordinates, index=values.index, columns=names)
            scores = scores.join(state.metadata)
            weights = pd.DataFrame(model.components_.T, columns=names)
            weights.insert(0, "feature", list(values.columns))
            explained = pd.DataFrame({"component": names, "explained_variance": model.explained_variance_,
                "explained_variance_ratio": model.explained_variance_ratio_,
                "cumulative_variance_ratio": np.cumsum(model.explained_variance_ratio_),
                "singular_value": model.singular_values_})
            common = {"parent_ids": [artifacts["matrix"]], "plan_id": plan_id, "dataset_id": dataset_id}
            artifacts["scores"] = state.table(scores.reset_index(), "pca_scores", common)
            artifacts["loadings"] = state.table(weights, "pca_loadings", common)
            artifacts["variance"] = state.table(explained, "pca_variance", common)
            filename = f"{pid}_model.json"
            state.write_json(filename, {"plan": plan.model_dump(), "features": list(values.columns),
                "centers": centers.tolist(), "divisors": divisors.tolist(),
                "pca_mean": model.mean_.tolist(), "components": model.components_.tolist(),
                "explained_variance": model.explained_variance_.tolist(),
                "explained_variance_ratio": model.explained_variance_ratio_.tolist(),
                "singular_values": model.singular_values_.tolist(), "noise_variance": model.noise_variance_,
                "loadings_definition": "unit eigenvector weights (sklearn components_.T), not variable-PC correlations",
                "packages": state.run_info["packages"]})
            document = state.evidence.register_file(filename, "pca_model", parent_ids=list(artifacts.values()))
            artifacts["model"] = document.id
            result.update(status="completed", explained_variance_ratio=model.explained_variance_ratio_.tolist(),
                          remaining_variance_ratio=max(0.0, float(1-model.explained_variance_ratio_.sum())))
    except BaseException as error:
        result.update(status="failed" if isinstance(error, Exception) else "interrupted", failure_reason=str(error))
        state.evidence.check("PCA_INCOMPLETE", True, False, passed=False,
                             message=f"{pid}: PCA failed; committed checkpoints remain available.")
        raise
    finally:
        filename = f"{pid}_pca.json"
        state.write_json(filename, result)
        metadata = state.evidence.register_file(filename, "pca_metadata", parent_ids=list(artifacts.values()),
                                                parameters={"pca_id": pid})
        result["metadata_artifact_id"] = metadata.id
        state.pca_attempts[pid] = clean_json(result)
    state.pcas[pid] = clean_json(result)
    return descriptor(result)


def descriptor(result):
    keys = {"pca_id", "dataset_id", "plan_id", "status", "scale", "scaling", "scaling_ddof",
            "samples", "feature_count", "components", "imputed_cells", "artifacts", "purpose",
            "transform", "notes", "numerical_rank", "failure_reason", "explained_variance_ratio",
            "remaining_variance_ratio", "metadata_artifact_id"}
    return clean_json({key: value for key, value in result.items() if key in keys})


def plot_source(state, args):
    result = state.pcas[args.source_id]
    methods = descriptor(result)
    if args.kind == "pca_loadings_scatter":
        names = [f"PC{i}" for i in args.components]
        if max(args.components) > result["components"]:
            raise ValueError("Requested loading components were not calculated.")
        source = state.read_artifact(result["artifacts"]["loadings"])[["feature", *names]]
        if args.features:
            if set(args.features) - set(source.feature):
                raise ValueError("Loading plot features must exist in the PCA result.")
            source = source.set_index("feature").loc[args.features].reset_index()
        methods.update(score_columns=names, loadings_definition="unit eigenvector weights, not variable-PC correlations")
        return source, source.feature.tolist(), [result["artifacts"]["loadings"]], methods, {
            "x": f"{names[0]} eigenvector weight", "y": f"{names[1]} eigenvector weight"}
    if args.kind == "pca_scores":
        first, second = args.components
        if max(first, second) > result["components"]:
            raise ValueError("Requested PCA score component was not calculated.")
        names = [f"PC{first}", f"PC{second}"]
        source = state.read_artifact(result["artifacts"]["scores"])
        source = source[[state.config.sample_column, *names, state.config.group_column,
                         *([state.config.pair_column] if state.config.pair_column else [])]].copy()
        variance = result["explained_variance_ratio"]
        labels = {"x": f"{names[0]} ({variance[first-1]:.1%})", "y": f"{names[1]} ({variance[second-1]:.1%})"}
        methods["score_columns"] = names
        return source, [], [result["artifacts"]["scores"]], methods, labels
    if args.kind == "pca_scree":
        source = state.read_artifact(result["artifacts"]["variance"])
        labels = {"x": "Calculated components", "y": "Explained variance (%)"}
        return source, [], [result["artifacts"]["variance"]], methods, labels
    name = f"PC{args.component}"
    if args.component > result["components"]:
        raise ValueError("Requested loading component was not calculated.")
    source = state.read_artifact(result["artifacts"]["loadings"])
    source = source[["feature", name]].rename(columns={name: "weight"})
    source["absolute_weight"] = source.weight.abs()
    if args.features:
        if set(args.features) - set(source.feature):
            raise ValueError("Loading plot features must exist in the PCA result.")
        source = source.set_index("feature").loc[args.features].reset_index()
        methods["loading_selection"] = "explicit_requested_order"
    else:
        source = source.sort_values(["absolute_weight", "feature"], ascending=[False, True]).head(args.max_loading_features)
        methods["loading_selection"] = "largest_absolute_weight_then_feature_name"
    methods["loading_component"] = name
    methods["loadings_definition"] = "unit eigenvector weights, not variable-PC correlations"
    return source, source.feature.tolist(), [result["artifacts"]["loadings"]], methods, {
        "x": f"{name} eigenvector weight", "y": "Feature"}
