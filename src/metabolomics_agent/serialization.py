"""Strict JSON serialization shared by run state and evidence persistence."""

import json
from pathlib import Path
from typing import Any

import numpy as np


def clean_json(value: Any):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, np.generic):
        return clean_json(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def dumps(value: Any) -> str:
    return json.dumps(clean_json(value), indent=2, ensure_ascii=False, allow_nan=False)
