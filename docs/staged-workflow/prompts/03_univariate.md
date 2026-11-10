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

1. Call inspect_univariate on the current prepared dataset before planning. Read
   relevant rows from its diagnostic table and the prepared descriptive tables.
   Inspect input observed counts, available test-scale values, imputed values
   actually used, and complete observed/source pairs. A large filled matrix does
   not establish additional observed evidence. The diagnostic ID is required
   when registering the statistical plan.
2. Specify the target comparison and treatment-relative-to-reference direction.
   Distinguish a mean comparison from a rank/distribution comparison.
3. Register the diagnostic_id, test, alpha, correction, family of hypotheses, dataset/scale, and
   rationale before testing. Use the supported exact tutorial parameters when
   reproduction is the objective; alternatives require a separately labeled analysis.
   This implementation supports alternative="two-sided",
   hypothesis_family="valid_features_in_comparison", and
   value_policy="including_imputed". Other alternatives or families are rejected.
4. For independent mean comparisons, consider Welch when justified. Pooled Student
   testing requires its equal-variance assumption. Consider sample size, shape,
   outliers, and the objective together; never choose solely from Shapiro p-values.
5. Mann–Whitney compares distributions/ranks and is not generally a median test.
   Paired designs require paired methods and ID-based alignment; never substitute
   an independent test or create pairs from row order.
   Paired t assumptions concern paired differences, not the two marginal group
   distributions. Prepared and observed-origin normality diagnostics are labeled
   separately; neither proves normality or establishes an automatic test rule.
6. The current toolset supports Student, Welch, Mann–Whitney, paired t, and paired
   Wilcoxon. Do not simulate ANOVA, Kruskal–Wallis, or post hoc tests if unavailable.
7. Execute the comparison. Check tested/skipped counts, nonfinite results, effect
   definitions, observed sample eligibility, and returned warnings. Read beyond a
   top-results summary when necessary to evaluate unexpected omissions.
8. Use the registered adjusted p-values for discovery statements. Identify the
   correction family and do not imply correction across adaptive reruns.

Imputed cells do not establish additional observed evidence. Preserve the runtime's
minimum observed-data checks. Fold changes use prepared unlogged concentrations;
tests may use transformed values. Confidence intervals may be unadjusted and are
on the reported test scale. Describe effect metrics only where the tool provides them.

## Audit and failure interpretation

The saved audit distinguishes input observations, source availability, observed
and imputed values actually used, and complete paired measurements. At least
three original observed measurements per group and, for paired designs, three
complete observed pairs are required. Normalization and imputation do not relax
these minimums. The imputation mask is authoritative; do not reconstruct origin
by comparing transformed numerical values.

A valid inferential result requires a finite test statistic and a p-value in
[0,1]. A degenerate t statistic is not accepted as p=0 evidence. Such rows remain
in the saved tables with undefined_test status. Undefined intervals and effects
remain missing and are explained in the issue table; unavailable rank confidence
intervals or unsupported effect metrics must not be described as computed.
Available t degrees of freedom and unadjusted confidence levels are saved in the audit.

SciPy rank methods use the recorded library version's auto behavior. Ties and
zero differences are identified. Wilcoxon uses zero_method="wilcox"; the existing
all-zero-difference convention returns statistic=0 and p=1. This convention and
very few nonzero differences require explicit interpretation, not proof of equality.

Fold changes retain the existing definition: log2 ratio of arithmetic means of
all available unlogged prepared group values. With incomplete paired measurements,
that population can differ from the complete pairs used for inference; the audit
flags this distinction. Do not describe it as a paired fold-change estimate.

Uncorrected results are persisted before multiplicity correction. They are
checkpoints, not discovery tables. If correction fails or no feature is testable,
the attempt retains raw results, counts, issues, and methods, but does not publish
a completed comparison. Request a reviewed revision rather than rerunning the
same statistical plan. Choices informed by raw tests remain exploratory even
when a completed corrected comparison was never produced.

## Tools and permitted use

- inspect_univariate: inspect all current test-scale features, observed/imputed
  eligibility, complete pairs, variances and normality; does not test group differences.
- set_univariate_plan: reference the current diagnostic ID and record test choices,
  correction family, imputation-use policy and purpose before execution.
- compare_groups: execute the registered two-group analysis and persist its complete table.
- read_result: inspect bounded comparison rows, skipped features, and available summaries.
- get_stage_context, read_stage_report: inspect current preparation and inherited issues.
- request_revision: request a scientifically justified change; do not modify preparation here.
- submit_stage_assessment: register findings, concerns, limitations, and pending decisions.

Do not rerun the same dataset to seek a smaller p-value. A sensitivity analysis
added after viewing results is exploratory and does not independently confirm findings.
If earlier PCA exploration informed the comparison hypothesis or feature choices,
state that adaptation and use an exploratory purpose; PCA does not independently
confirm the univariate results.

## Issues, outputs, and completion

Record skipped tests, undefined effects, low observed counts, substantial imputation,
unadjusted intervals, and the limited multiplicity scope where relevant. Distinguish
zero discoveries from a stage with no valid tests: the former can be complete;
the latter cannot satisfy a required comparison objective.

Completion requires the current required comparison plan to be executed or its
failure explicitly reported. Preserve the full results table and all plan links.
Pass the comparison IDs, raw/audit/issue artifact references, direction, scales,
correction family, effect definitions, skipped features, warnings, and exploratory
status to visualization and final reporting. A stage with zero significant
features can complete; a stage with no valid comparison cannot.
