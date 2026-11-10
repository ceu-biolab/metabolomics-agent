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
2. Request group-wise descriptive summaries on the original scale. Include the
   supported location, dispersion, quantile, and shape measures. Report undefined
   statistics with reasons; a mode may be uninformative for continuous measurements.
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

Do not require complete data merely to satisfy a removed PCA step. Statistical
eligibility depends on the selected method and observed counts. A heatmap, if
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

Completion requires an executed preparation plan, valid artifact references,
documented removals and scales, and no unresolved blocking input error. If an
operation fails, identify the last successful checkpoint and failed operation;
do not present that checkpoint as a completed preparation.

Pass the prepared dataset IDs, observed counts, imputations, filters, transformations,
normalization assumptions, unresolved issues, and test-scale restrictions onward.
Relevant warnings must remain available to statistics, visualization, and reporting.
