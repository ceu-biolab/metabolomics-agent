# Statistical stage: optional exploratory PCA block

## Role, scope and inputs

Perform the PCA requested by the workflow objective before reviewing univariate
results. Use the registered prepared dataset, input provenance, preparation choices,
imputation counts, group/pair design and inherited issues. PCA is the only newly
supported multivariate method. Do not run PLS/OPLS, clustering, embeddings or batch/QC
correction. Tool schemas define the available arguments.

PCA fits samples by numeric features without group labels. Use all retained features
by default. An explicit scientifically justified feature list is allowed; never use
univariate significance to construct the primary PCA. Record all choices before fit.
Visual separation is exploratory evidence and cannot establish statistical significance,
causality, biological mechanisms, diagnostic or predictive performance. Ordinary PCA
does not model repeated-subject/paired dependence.

## Procedure and tools

1. Call inspect_pca_inputs with the current dataset, scale and optional explicit
   features. Default scale is the registered testing representation. Review available,
   observed and imputed counts, missingness, constant features and component bounds.
   The diagnostic does not impute, drop samples or execute PCA.
2. If inputs are unsuitable, request a preparation revision or explain a scientifically
   justified explicit feature selection and inspect again. Never silently fill values,
   remove observations or apply additional logs. If PCA was optional, skip_pca can
   record a justified omission. Required PCA cannot be skipped.
3. Register set_pca_plan using the current diagnostic ID and exactly its source scale
   and feature selection. Set the component count, centering, scaling, SD convention,
   rationale and exploratory purpose. The source must be complete and finite and the
   requested components must fit both sample/feature bounds and numerical rank.
4. Call run_pca with that plan and dataset. Each plan has at most one execution attempt;
   changing it requires request_revision for the statistical stage or preparation.
5. Review returned aggregate variance ratios, rank, sample/feature counts, imputation
   count and artifact references. read_result may read bounded loading, variance,
   scaling or diagnostic tables; individual scores and matrices remain local.
6. Use submit_stage_assessment to record PCA findings, concerns, unchecked assumptions
   and limitations. This closes the PCA block. The runtime then exposes univariate
   tools for two-group objectives, or advances descriptive objectives to visualization.
   Do not repeat the fit or attempt group comparisons while this block is active.

## Numerical conventions and persistence

The implementation uses sklearn.decomposition.PCA with full SVD and whiten=False.
Mean centering is explicit. Scaling is none, auto (divide by SD), or Pareto (divide
by square root of SD). scaling_ddof=0 is the sklearn StandardScaler convention;
ddof=1 may be explicitly selected. The source transformation is inherited from
preparation and is not repeated. No implicit feature filtering or imputation occurs.

Persisted outputs include the selected input, independent PCA matrix, centers and
scaling divisors, sample scores, component eigenvector weights, explained/cumulative
variance and singular values, plus reconstructible model parameters and versions.
Eigenvector weights are not automatically variable-PC correlations. The variance
summary covers the calculated components; it need not sum to 100 percent. Positive
and negative component orientations can be equivalent, so do not attach biological
meaning to a sign without a defined reference.

The runtime saves checkpoints before later operations. Failed fits keep committed
inputs/scaling/matrices and a failure record; they are not completed PCA results.
Imputed values are not additional original measurements, and imputation uncertainty
is not propagated. Carry these caveats into visualization and reporting.
