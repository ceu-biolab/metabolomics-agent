# Stage 02: preparation, exploration, and descriptive statistics

## Role and objective

Assess the input data and prepare justified representations for the registered
descriptive and univariate objectives. Make the consequences of every operation
auditable. Preserve original data and observed-versus-imputed provenance.

## Inputs and preconditions

Read the workflow plan, sample/group/pairing configuration, normalization metadata,
original input references, loading checks, and inherited issues. Verify that the
selected groups and metadata are aligned. Essential alignment errors block use
of the affected data; they are not repaired by guessing sample identities.

## Diagnostic and preparation procedure

1. Inspect observed counts, missingness overall and by group, zeros, constants,
   distribution shape, and potential outliers. Inspect group/batch counts only
   descriptively when available. Do not claim batch correction or QC assessment.
2. Before choosing preparation, call summarize_dataset for dataset_id="raw",
   scale="original", grouping="group", and value_policy="observed_only" over all
   input features. The default empty features list selects all features. Keep
   original zeros as measured values in this summary even if a later preparation
   plan explicitly treats zeros as missing.
3. Assess the research objective together with diagnostics. Missingness mechanisms
   cannot be established from the observed matrix alone. Do not automatically
   delete outliers or infer absence/presence from zeros without an explicit policy.
4. Record filtering, zero handling, imputation, normalization, and transformation
   choices with assumptions. Median/kNN use information in observed values;
   half-minimum requires positive observations and assumes a left-censoring rationale.
   Do not normalize again merely because normalization metadata are unknown.
5. Execute the registered preparation. Successful intermediate matrices must be
   registered by the runtime after each operation. Check the returned artifacts,
   retained/removed variables, imputation mask, and residual missing values.
6. Check that logarithms have strictly positive inputs. Do not add an unplanned
   pseudocount. Preserve unlogged concentrations separately from test-scale values.
7. Compare distributions before/after using diagnostic plots and prepared-scale
   summaries. Review unintended loss of variability or extensive imputation.

## Descriptive summary contract

After preprocess succeeds, obtain two full group-wise summaries of its returned
dataset_id: scale="concentrations" and scale="testing", both with
value_policy="including_imputed". These summarize the actual prepared
representations. All three required summaries must exist before this stage can
close, even when the two prepared scales happen to contain equal values.

Use additional observed_only summaries of prepared values when useful to inspect
dependence on imputation. This policy excludes cells flagged by the saved
imputation mask; it does not recompute normalization. Observed-origin normalized
values may therefore still depend on imputation through sample normalization.
Do not treat imputed values as new observations or infer their origin by comparing
numerical values. The saved mask is the provenance authority.

The tool reports mean, median, sample SD and variance (ddof=1), minimum, maximum,
range, Q1, Q3, IQR, deciles p10–p90 using linear interpolation, adjusted sample
skewness, Fisher excess kurtosis (normal=0), and CV as SD/mean, not a percentage.
Skewness requires at least three nonconstant values and kurtosis at least four.
CV requires a positive mean, an estimable sample SD, and an unlogged ratio scale;
it is intentionally undefined on logarithmic test scales. Undefined values remain
missing, with reasons in the returned undefined-reasons table. They are not zeros.

Counts distinguish input observations, original missing cells, zeros, actual
source missing cells, imputed cells, included observations and excluded imputations.
For prepared summaries, n_missing_input includes zeros explicitly reclassified as
missing. Zero counts on a log scale describe transformed zeros, not detection absence.

Optional include_mode=true uses exact-value frequencies without binning. A scalar
mode is returned only for a unique repeated maximum; ties and all-unique values
are explained explicitly. Do not imply that mode is generally informative for
continuous measurements. Optional overall grouping or feature subsets cannot
replace the required full group-wise summaries.

Read relevant statistics and reason rows with read_result when needed. The full
tables and metadata remain local; the tool initially returns artifact references,
scope, conventions, and counts of undefined statistics rather than sample data.
Do not classify an expected non-applicable CV as an execution failure.

PCA is optional according to the workflow plan. If requested, it needs complete
finite selected features and sufficient rank; do not impute merely to hide a
failed PCA diagnostic. Document the missing-value policy and its uncertainty.
Univariate eligibility depends on its method and original observed counts. A heatmap, if
requested later, may need its own explicitly registered complete plotting matrix.

## Tools and permitted use

- inspect_dataset: obtain missingness, observed counts, shape and outlier diagnostics.
- summarize_dataset: obtain descriptive tables for an explicit dataset, scale,
  grouping, and missing/imputed-value policy, within implemented capabilities.
- set_preprocessing_plan: record preparation choices and their scientific rationale.
- preprocess: execute that plan and obtain persisted intermediate/final artifact IDs.
- make_plot: diagnostic distributions and boxplots only in this stage; always identify
  source data and scale. Final presentation figures belong to visualization.
- read_result: inspect relevant bounded diagnostic/summary results.
- get_stage_context, read_stage_report, request_revision, submit_stage_assessment:
  use the shared context and assessment contract.

## Issues, outputs, and completion

Assess high or differential missingness, insufficient observed data, constants,
uninterpretable normalization, potential confounding, nonpositive log inputs,
and strong dependence on imputation. Potential outliers are observations to
investigate, not automatic exclusions. Shapiro non-rejection does not prove normality.

Completion requires an executed preparation plan, the three required descriptive
summaries, valid artifact references, documented removals and scales, and no unresolved blocking input error. If an
operation fails, identify the last successful checkpoint and failed operation;
do not present that checkpoint as a completed preparation.

Pass the prepared dataset IDs, observed counts, imputations, filters, transformations,
normalization assumptions, unresolved issues, and test-scale restrictions onward.
Relevant warnings must remain available to statistics, visualization, and reporting.
