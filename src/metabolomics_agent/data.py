from pathlib import Path

import numpy as np
import pandas as pd

from .models import StudyConfig


def read_table(path: Path, string_columns=()) -> pd.DataFrame:
    dtypes = {c: str for c in string_columns if c}
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path, dtype=dtypes)
    if path.suffix.lower() not in {".csv", ".tsv"}:
        raise ValueError("Use CSV, TSV, or XLSX input files.")
    return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",", dtype=dtypes)


def load_study(config: StudyConfig, base_dir: Path):
    path = (base_dir / config.data_path).resolve()
    sid, group = config.sample_column, config.group_column
    string_columns = [sid, group, config.pair_column, config.batch_column]
    table = read_table(path, string_columns)
    if sid not in table:
        raise ValueError(f"Data must contain sample ID column {sid!r}.")
    if table[sid].isna().any() or table[sid].duplicated().any():
        raise ValueError("Data sample IDs must be present and unique.")
    table[sid] = table[sid].astype(str)
    if table[sid].duplicated().any():
        raise ValueError("Sample IDs collide after conversion to strings.")
    input_paths = [path]
    if config.metadata_path:
        meta_path = (base_dir / config.metadata_path).resolve()
        meta = read_table(meta_path, string_columns)
        input_paths.append(meta_path)
        if sid not in meta or meta[sid].isna().any() or meta[sid].duplicated().any():
            raise ValueError("Metadata must have present, unique sample IDs.")
        meta[sid] = meta[sid].astype(str)
        if meta[sid].duplicated().any() or set(table[sid]) != set(meta[sid]):
            raise ValueError("Data and metadata must have exactly matching sample IDs.")
        overlap = (set(table.columns) & set(meta.columns)) - {sid}
        if overlap:
            raise ValueError(f"Ambiguous columns present in data and metadata: {sorted(overlap)}")
        table = table.merge(meta, on=sid, validate="one_to_one", sort=False)
        metadata_columns = set(meta.columns)
    else:
        metadata_columns = set()
    metadata_columns |= {sid, group, *config.metadata_columns}
    metadata_columns.update(c for c in [config.pair_column, config.batch_column] if c)
    absent = metadata_columns - set(table.columns)
    if absent:
        raise ValueError(f"Missing metadata columns: {sorted(absent)}")
    if table[group].isna().any():
        raise ValueError("Group labels cannot be missing.")
    table[group] = table[group].astype(str)
    features = config.feature_columns or [c for c in table if c not in metadata_columns]
    if len(features) != len(set(features)):
        raise ValueError("Feature columns must be unique.")
    if not features or set(features) - set(table.columns) or set(features) & metadata_columns:
        raise ValueError("Feature columns must exist and exclude metadata columns.")
    if config.feature_columns is None:
        nonnumeric = [c for c in features if not pd.api.types.is_numeric_dtype(table[c])]
        if nonnumeric:
            raise ValueError(f"Declare non-feature columns as metadata_columns: {nonnumeric}")
    numeric = table[features].apply(pd.to_numeric, errors="raise").astype(float)
    if np.isinf(numeric.to_numpy()).any():
        raise ValueError("Infinite feature values are not supported; correct the input.")
    if (numeric < 0).any().any():
        raise ValueError("Inputs must be nonnegative concentrations/intensities, before log/scaling.")
    selected = table[group].isin([config.reference_group, config.treatment_group])
    excluded_groups = table.loc[~selected, group].value_counts().to_dict()
    metadata = table.loc[selected, sorted(metadata_columns)].set_index(sid)
    values = numeric.loc[selected].copy()
    values.index = metadata.index
    for label in [config.reference_group, config.treatment_group]:
        if (metadata[group] == label).sum() < 3:
            raise ValueError(f"Group {label!r} needs at least three samples for this prototype.")
    if config.design == "paired":
        if metadata[config.pair_column].isna().any():
            raise ValueError("Pair IDs cannot be missing.")
        counts = metadata.groupby([config.pair_column, group]).size().unstack(fill_value=0)
        if counts.shape[1] != 2 or not (counts == 1).all().all():
            raise ValueError("Each pair must have exactly one observation in each compared group.")
    return values, metadata, excluded_groups, input_paths
