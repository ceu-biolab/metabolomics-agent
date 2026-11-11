"""Pure comparison and aggregation functions; no production statistical calls."""

from collections import Counter

import numpy as np
import pandas as pd

from .evaluation_models import MetricResult, Tolerance
from .serialization import clean_json


def numeric_comparison(actual, expected, tolerance: Tolerance):
    left, right = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
    if left.shape != right.shape:
        return False, {"actual_shape": list(left.shape), "expected_shape": list(right.shape)}, right.size
    finite = np.isfinite(left) & np.isfinite(right)
    masks_match = (np.array_equal(np.isnan(left), np.isnan(right))
                   and np.array_equal(np.isposinf(left), np.isposinf(right))
                   and np.array_equal(np.isneginf(left), np.isneginf(right)))
    errors = np.abs(left[finite] - right[finite])
    accepted = np.isclose(left[finite], right[finite], rtol=tolerance.rtol, atol=tolerance.atol)
    nonzero = right[finite] != 0
    relative = errors[nonzero] / np.abs(right[finite][nonzero])
    details = {"shape": list(right.shape), "undefined_mask_match": masks_match,
               "finite_values_compared": int(finite.sum()),
               "max_absolute_error": float(errors.max()) if errors.size else None,
               "max_relative_error": float(relative.max()) if relative.size else None,
               "fraction_within_tolerance": float(accepted.mean()) if accepted.size else None}
    return bool(masks_match and accepted.all()), details, right.size


def table_comparison(actual, expected, keys, columns, tolerance):
    if not keys or any(key not in frame for frame in (actual, expected) for key in keys):
        return False, {"reason": "Missing explicit identity columns."}, len(expected)
    for frame in (actual, expected):
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            return False, {"reason": "Identity columns must be present and unique."}, len(expected)
    left, right = actual.set_index(keys), expected.set_index(keys)
    if set(left.index) != set(right.index):
        return False, {"reason": "Row identity sets differ.", "actual_rows": len(left),
                       "expected_rows": len(right)}, len(right)
    if not columns and set(left.columns) != set(right.columns):
        return False, {"reason": "Column identity sets differ."}, len(right)
    selected = columns or list(right.columns)
    if any(column not in frame for frame in (left, right) for column in selected):
        return False, {"reason": "A registered comparison column is missing."}, len(right)
    left = left.reindex(right.index)
    checks, denominator = {}, 0
    passed = True
    for column in selected:
        a, b = left[column], right[column]
        if pd.api.types.is_numeric_dtype(b) and pd.api.types.is_numeric_dtype(a):
            ok, details, count = numeric_comparison(a, b, tolerance)
        else:
            ok = bool(((a == b) | (a.isna() & b.isna())).all())
            details, count = {"exact_match": ok}, len(b)
        checks[column] = details
        denominator += count
        passed &= ok
    return bool(passed), {"rows": len(right), "columns": checks}, denominator


def set_comparison(actual, expected):
    if not isinstance(actual, (list, tuple, set, np.ndarray)) or not isinstance(expected, (list, tuple, set, np.ndarray)):
        raise TypeError("Set comparisons require explicit collections.")
    a, b = set(actual), set(expected)
    unique = len(a) == len(actual) and len(b) == len(expected)
    common = len(a & b)
    return a == b and unique, {"actual_count": len(a), "expected_count": len(b), "unique": unique,
                    "precision": common / len(a) if a else None,
                    "sensitivity": common / len(b) if b else None,
                    "jaccard": common / len(a | b) if a | b else None,
                    "exact_match": a == b}, len(b)


def pca_weights_comparison(actual, expected, keys, columns, blocks, tolerance):
    left, right = actual.set_index(keys), expected.set_index(keys)
    if not left.index.is_unique or not right.index.is_unique or set(left.index) != set(right.index):
        return False, {"reason": "PCA feature identities differ or are duplicated."}, len(right)
    left = left.reindex(right.index)
    diagnostics = {}
    passed = True
    for block in blocks or [[column] for column in columns]:
        a, b = left[block].to_numpy(float), right[block].to_numpy(float)
        # Projectors admit coherent sign flips and rotations within declared degenerate blocks.
        ok, details, _ = numeric_comparison(a @ a.T, b @ b.T, tolerance)
        orthogonal_a = np.allclose(a.T @ a, np.eye(len(block)), rtol=0, atol=max(tolerance.atol, tolerance.rtol))
        orthogonal_b = np.allclose(b.T @ b, np.eye(len(block)), rtol=0, atol=max(tolerance.atol, tolerance.rtol))
        diagnostics[",".join(block)] = {**details, "orthonormal": bool(orthogonal_a and orthogonal_b)}
        passed &= ok and orthogonal_a and orthogonal_b
    return bool(passed), diagnostics, len(right) * len(columns)


def aggregate(results: list[MetricResult]):
    counts = Counter(result.status for result in results)
    evaluated = counts["passed"] + counts["failed"]
    applicable = len(results) - counts["not_applicable"]
    pending_required = sum((result.required or result.critical) and result.status in {"not_assessed", "blocked_by_dependency"}
                           for result in results)
    failed_required = sum(result.status == "failed" and (result.required or result.critical)
                          for result in results)
    if failed_required:
        status = "failed"
    elif pending_required:
        status = "blocked_by_dependency" if any((result.required or result.critical) and result.status == "blocked_by_dependency"
                                                for result in results) else "not_assessed"
    elif not applicable:
        status = "not_applicable"
    elif not evaluated:
        status = "blocked_by_dependency" if counts["blocked_by_dependency"] else "not_assessed"
    else:
        status = "passed"
    return status, {"criteria": len(results), "applicable": applicable, "evaluated": evaluated,
                    "counts": dict(counts), "required_pending": pending_required,
                    "critical_failures": sum(result.critical and result.status == "failed" for result in results),
                    "pass_rate": counts["passed"] / evaluated if evaluated else None,
                    "coverage": evaluated / applicable if applicable else None}


def result(spec, status, *, expected=None, observed=None, details=None, denominator=None, evidence=None, explanation):
    return MetricResult(id=spec.id, status=status, required=spec.required, critical=spec.critical,
                        expected=clean_json(expected), observed=clean_json(observed),
                        tolerance=spec.tolerance if spec.validator in {"numeric", "table", "pca_weights"} else None,
                        diagnostics=clean_json(details or {}), denominator=denominator,
                        evidence_ids=evidence or [], explanation=explanation)
