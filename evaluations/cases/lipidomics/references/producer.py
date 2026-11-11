"""Independent companion-tutorial calculations. No production analysis imports."""

import importlib.metadata
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from statsmodels.stats.diagnostic import kstest_normal
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.weightstats import ttest_ind

from .serialization import dumps


DATASET_SOURCE = "https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/introduction/example-data-sets"
TEST_SOURCE = "https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/metabolites-and-lipids-univariate-statistical-analysis-in-python/two-sample-comparisons-in-python"
PCA_SOURCE = "https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/metabolites-and-lipids-multivariate-statistical-analysis-in-python/principal-component-analysis"


def build_references(dataset: Path, output: Path):
    frame = pd.read_excel(dataset, decimal=",")
    if not {"Sample Name", "Label"} <= set(frame):
        raise ValueError("Expected the unmodified tutorial sample and label columns.")
    features = [column for column in frame if column not in {"Sample Name", "Label"}]
    if len(features) != 127 or frame.Label.value_counts().to_dict() != {"T": 109, "N": 97, "PAN": 21}:
        raise ValueError("The file is not dataset no. 1 with the expected full cohort.")
    if frame['Sample Name'].isna().any() or frame['Sample Name'].duplicated().any():
        raise ValueError("Sample identities are missing or duplicated.")
    values = frame[features].to_numpy(float)
    if not np.isfinite(values).all() or (values <= 0).any() or (frame[features].nunique() <= 1).any():
        raise ValueError("Exact reproduction requires the expected complete positive nonconstant matrix.")
    output.mkdir(parents=True, exist_ok=False)
    selected = frame[frame.Label.isin(["T", "N"])]
    testing_rows, rank_rows, normality = [], [], []
    for feature in features:
        treatment = selected.loc[selected.Label == "T", feature].to_numpy(float)
        control = selected.loc[selected.Label == "N", feature].to_numpy(float)
        statistic, p, dof = ttest_ind(treatment, control, alternative="two-sided", usevar="pooled")
        u, up = mannwhitneyu(treatment, control, alternative="two-sided", method="auto")
        fold = np.log2(treatment.mean() / control.mean())
        testing_rows.append({"feature": feature, "statistic": statistic, "p_value": p, "df": dof, "log2_fold_change": fold})
        rank_rows.append({"feature": feature, "statistic": u, "p_value": up, "log2_fold_change": fold})
        row = {"feature": feature}
        for label, data in [("reference", control), ("treatment", treatment)]:
            stat, pvalue = kstest_normal(data, "norm")
            row.update({f"lilliefors_statistic_{label}": stat, f"lilliefors_p_{label}": pvalue})
        normality.append(row)
    tables = {}
    for name, rows in [("student_t", testing_rows), ("mannwhitney", rank_rows)]:
        table = pd.DataFrame(rows)
        # p=1 legitimately gives log1p(-1)=-inf; expm1 then returns the correct adjusted value 1.
        with np.errstate(divide="ignore"):
            table["adjusted_p_value"] = multipletests(table.p_value, method="holm-sidak", alpha=.05)[1]
        table["significant"] = table.adjusted_p_value < .05
        tables[name] = table
    tables["normality"] = pd.DataFrame(normality)
    tables["concentrations_TN"] = selected[["Sample Name", *features]]
    tables["concentrations_all"] = frame[["Sample Name", *features]]
    tables["cohort_TN"] = selected[['Sample Name','Label']]
    tables["cohort_all"] = frame[['Sample Name','Label']]
    scaled = StandardScaler().fit_transform(frame[features])
    pca = PCA(n_components=10)
    scores = pca.fit_transform(scaled)
    components = [f"PC{i+1}" for i in range(10)]
    tables["pca_matrix"] = pd.DataFrame(scaled, columns=features).assign(**{'Sample Name': frame['Sample Name']})
    tables["pca_weights"] = pd.DataFrame(pca.components_.T, columns=components).assign(feature=features)
    tables["pca_scores"] = pd.DataFrame(scores, columns=components).assign(**{'Sample Name': frame['Sample Name']})
    tables["pca_variance"] = pd.DataFrame({"component": components, "explained_variance": pca.explained_variance_,
        "explained_variance_ratio": pca.explained_variance_ratio_, "cumulative_variance_ratio": np.cumsum(pca.explained_variance_ratio_)})
    t = tables["student_t"].set_index("feature")
    u = tables["mannwhitney"].set_index("feature")
    normal = tables["normality"].set_index("feature")
    checks = {
        "PC34_1_t": bool(np.isclose(t.loc['PC 34:1', 'statistic'], 2.0056017821278194, rtol=1e-9, atol=0)),
        "PC34_1_p": bool(np.isclose(t.loc['PC 34:1', 'p_value'], .04621995094509444, rtol=1e-9, atol=0)),
        "PC34_1_df": bool(t.loc['PC 34:1', 'df'] == 204),
        "PC36_5_U": bool(u.loc['PC 36:5', 'statistic'] == 2467),
        "PC36_5_p": bool(np.isclose(u.loc['PC 36:5', 'p_value'], 4.0874221736427684e-11, rtol=1e-7, atol=0)),
        "SM41_1_adjusted": bool(np.isclose(t.loc['SM 41:1;O2', 'adjusted_p_value'], 1.080575e-19, rtol=1e-5, atol=0)),
        "PC34_1_lilliefors_T": bool(np.isclose(normal.loc['PC 34:1', 'lilliefors_p_treatment'], .08119506735189941, rtol=1e-7, atol=0)),
        "PC34_1_lilliefors_N": bool(np.isclose(normal.loc['PC 34:1', 'lilliefors_p_reference'], .078397863558115, rtol=1e-7, atol=0)),
    }
    for name, table in tables.items():
        table.to_csv(output / f"{name}.csv", index=False, float_format="%.17g")
    metadata = {"published_checks": checks, "published_checks_passed": all(checks.values()),
        "source": DATASET_SOURCE, "test_source": TEST_SOURCE, "pca_source": PCA_SOURCE,
        "pca_solver_requested": "auto", "pca_solver_resolved": pca._fit_svd_solver,
        "pca_components": 10, "scaling_ddof": 0, "normalization": "Previously normalized to NIST SRM 1950 per authors; no additional normalization.",
        "packages": {name: importlib.metadata.version(name) for name in ['numpy','pandas','scipy','statsmodels','scikit-learn']},
        "reference_scope": "Selected published values plus full tables recalculated from author code. Arithmetic-mean fold changes are explicit supplementary calculations."}
    (output / "reference_metadata.json").write_text(dumps(metadata) + '\n')
    if not all(checks.values()):
        raise ValueError("Reference does not reproduce published values; inspect preserved reference_metadata.json.")
    return frame, features, tables, metadata
