"""Exact selected GitBook checks, without an LLM or changed default methods."""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.weightstats import ttest_ind


DATASET_URL = "https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/introduction/example-data-sets"
TUTORIAL_URL = "https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/metabolites-and-lipids-univariate-statistical-analysis-in-python/two-sample-comparisons-in-python"


def reference_check(path: Path, output: Path):
    df = pd.read_excel(path).set_index("Sample Name")
    features = [c for c in df if c != "Label"]
    if len(features) != 127 or df.Label.value_counts().to_dict() != {"T": 109, "N": 97, "PAN": 21}:
        raise ValueError("This is not the expected unmodified GitBook dataset no. 1.")
    rows = []
    for feature in features:
        treat, ref = df.loc[df.Label == "T", feature], df.loc[df.Label == "N", feature]
        t, p, dof = ttest_ind(treat, ref, alternative="two-sided", usevar="pooled")
        u, up = mannwhitneyu(treat, ref, alternative="two-sided")
        rows.append({"feature": feature, "t_statistic": t, "t_p_value": p, "df": dof,
                     "u_statistic": u, "u_p_value": up})
    results = pd.DataFrame(rows).set_index("feature")
    results["t_adjusted_p"] = multipletests(results.t_p_value, method="holm-sidak")[1]
    results["u_adjusted_p"] = multipletests(results.u_p_value, method="holm-sidak")[1]
    checks = {
        "PC 34:1 pooled t": np.isclose(results.loc["PC 34:1", "t_statistic"], 2.0056017821278194, rtol=1e-9),
        "PC 34:1 pooled p": np.isclose(results.loc["PC 34:1", "t_p_value"], 0.04621995094509444, rtol=1e-9),
        "PC 34:1 df": results.loc["PC 34:1", "df"] == 204,
        "PC 36:5 Mann-Whitney U": results.loc["PC 36:5", "u_statistic"] == 2467,
        "PC 36:5 Mann-Whitney p": np.isclose(results.loc["PC 36:5", "u_p_value"],
                                            4.0874221736427684e-11, rtol=1e-7, atol=0),
        "SM 41:1;O2 corrected t p": np.isclose(results.loc["SM 41:1;O2", "t_adjusted_p"],
                                              1.080575e-19, rtol=1e-5, atol=0),
    }
    output.mkdir(parents=True, exist_ok=False)
    results.to_csv(output / "reference_results.csv", float_format="%.17g")
    from .state import dumps

    summary = {"passed": bool(all(checks.values())), "checks": checks,
               "dataset_source": DATASET_URL, "tutorial_source": TUTORIAL_URL,
               "methods": "T vs N; raw concentrations; pooled two-sided t-tests and "
                          "two-sided Mann-Whitney U; Holm-Sidak across all 127 lipids."}
    (output / "reference_check.json").write_text(dumps(summary) + "\n")
    return summary
