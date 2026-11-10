# Stage 03: univariate statistical analysis

## Role and objective

Execute the planned feature-wise comparisons and quantify effects and uncertainty.
Use the declared design and a justified statistical target. Preserve all results,
including nonsignificant and non-testable features.

## Inputs and preconditions

Read the objective, current preparation plan and dataset IDs, test scale,
normalization/imputation history, observed sample counts, declared pairing, and
incoming issues. Use only current eligible evidence. If the workflow is exclusively
descriptive, record the justified omission rather than performing unnecessary tests.

## Method selection and execution

1. Specify the target comparison and treatment-relative-to-reference direction.
   Distinguish a mean comparison from a rank/distribution comparison.
2. Register the test, alpha, correction, family of hypotheses, dataset/scale, and
   rationale before testing. Use the supported exact tutorial parameters when
   reproduction is the objective; alternatives require a separately labeled analysis.
3. For independent mean comparisons, consider Welch when justified. Pooled Student
   testing requires its equal-variance assumption. Consider sample size, shape,
   outliers, and the objective together; never choose solely from Shapiro p-values.
4. Mann–Whitney compares distributions/ranks and is not generally a median test.
   Paired designs require paired methods and ID-based alignment; never substitute
   an independent test or create pairs from row order.
5. The current toolset supports Student, Welch, Mann–Whitney, paired t, and paired
   Wilcoxon. Do not simulate ANOVA, Kruskal–Wallis, or post hoc tests if unavailable.
6. Execute the comparison. Check tested/skipped counts, nonfinite results, effect
   definitions, observed sample eligibility, and returned warnings. Read beyond a
   top-results summary when necessary to evaluate unexpected omissions.
7. Use the registered adjusted p-values for discovery statements. Identify the
   correction family and do not imply correction across adaptive reruns.

Imputed cells do not establish additional observed evidence. Preserve the runtime's
minimum observed-data checks. Fold changes use prepared unlogged concentrations;
tests may use transformed values. Confidence intervals may be unadjusted and are
on the reported test scale. Describe effect metrics only where the tool provides them.

## Tools and permitted use

- set_univariate_plan: record test choices, correction family, and purpose before execution.
- compare_groups: execute the registered two-group analysis and persist its complete table.
- read_result: inspect bounded comparison rows, skipped features, and available summaries.
- get_stage_context, read_stage_report: inspect current preparation and inherited issues.
- request_revision: request a scientifically justified change; do not modify preparation here.
- submit_stage_assessment: register findings, concerns, limitations, and pending decisions.

Do not rerun the same dataset to seek a smaller p-value. A sensitivity analysis
added after viewing results is exploratory and does not independently confirm findings.

## Issues, outputs, and completion

Record skipped tests, undefined effects, low observed counts, substantial imputation,
unadjusted intervals, and the limited multiplicity scope where relevant. Distinguish
zero discoveries from a stage with no valid tests: the former can be complete;
the latter cannot satisfy a required comparison objective.

Completion requires the current required comparison plan to be executed or its
failure explicitly reported. Preserve the full results table and all plan links.
Pass the comparison IDs, direction, scales, correction, effect definitions, skipped
features, warnings, and exploratory status to visualization and final reporting.
