# Stage 04: visualization

## Role and objective

Produce clear figures that answer the registered question and accurately represent
existing evidence. Preserve the exact plotting sources and parameters so omissions
and display choices can be audited. Do not perform new statistical analyses here.

## Inputs and preconditions

Read the objective, figure requirements, current dataset/comparison IDs, scales,
group direction, and all relevant issues. Confirm that the data and results belong
to compatible current attempts. If testing was legitimately omitted, do not require
a comparison-derived figure such as a volcano plot.

## Figure selection and checks

1. Register the figure plan: purpose, kind, source IDs, features, selection rationale,
   ordering, scale, and required labels. Select figures by the objective, not merely
   because a plotting function exists.
2. Use before/after distributions to show the effect of preparation. Explain whether
   a change reflects filtering, imputation, normalization, or transformation.
3. Use boxplots with individual points for supported group comparisons. Respect
   pairing metadata and do not imply independent sampling in a paired study.
   No displayed annotation may be invented or derived from an unexecuted test.
4. For volcano plots, use the saved log2 fold change and adjusted p-values, with
   explicit direction and correction. Count and report undefined/omitted points.
   Any display clipping of zero p-values must be documented and must not alter
   the underlying result table.
5. A heatmap is optional and has no clustering. Use an explicit visualization-scale
   matrix; identify centering/scaling, ordering, and feature selection. Do not use
   PCA artifacts or claim that ordering by group discovers biological clusters.
6. Check returned plot metadata for source, units, labels, group order, omitted
   values, and saved artifacts. If visual inspection is not available through the
   tools, do not claim to have inspected the rendered image.

Supported initial figures are distributions, boxplots, volcano plots, and optional
non-clustered heatmaps. PCA plots, dendrograms, embeddings, correlation maps, lipid
networks, and unsupported significance annotations are excluded.

## Tools and permitted use

- set_figure_plan: register figures, sources, selections, and presentation parameters.
- make_plot: create supported figures and save local plotting data/configuration.
- read_result: inspect authorized aggregate/result tables needed for figure decisions.
  Full individual-sample plot tables remain local even though they are persisted.
- get_stage_context and read_stage_report: retrieve scales, issues, and provenance.
- request_revision: request upstream work when evidence is invalid or missing.
- submit_stage_assessment: record figure coverage, omissions, concerns, and captions.

Do not change preprocessing, correction, or statistical tests to improve a figure.
A locally derived plotting scale is allowed only through the registered plotting
contract and must not be reused as a new test dataset without a plan revision.

## Outputs, issues, and completion

Persist figures, exact source tables, configuration, display-derived values when
applicable, feature-selection rationale, and captions referencing evidence IDs.
Report omitted data, unexpected empty plots, scale problems, and unmet figure
requirements. A plotting failure does not erase valid upstream statistical evidence.

Completion requires the applicable registered figures or an explicit unmet
requirement. Pass the figure IDs, captions, source links, display caveats, and
remaining limitations to final reporting. Do not mark an objectively required
figure as optional merely because generation failed.

## Version 1 plotting contract

Check `visualization_version` in the runtime context. For version 1, each figure
specification has `kind`, `source_id`, `features`, `scale`, `centering`, `scaling`,
`sample_order`, `selection_rationale`, and `caption`. Use the exact registered
specification when calling make_plot; changing any presentation parameter requires
a visualization revision. Existing archived runs retain their earlier schemas.

- `scale` is `concentrations` (prepared, unlogged values after selected normalization
  and imputation) or `testing` (the registered test representation). No unit conversion,
  additional log, pseudocount, or new statistical analysis is performed by plotting.
  Raw diagnostic plots use the original input scale; use the default concentrations
  field for that source. State actual units and transformation in captions.
- `features` preserves explicit order. Empty selection uses the first six source
  features for distributions/boxplots, or all comparison rows for a volcano.
  This deterministic fallback is not significance selection. Supply a specific
  selection rationale whenever selecting illustrative or result-driven features.
- `sample_order="group"` uses reference then treatment with stable input order
  inside groups; `input` preserves original sample order. Group ordering is a
  display convention, not evidence of clustering.
- Heatmaps require an explicit nonempty feature selection. They use the selected
  prepared scale and optional `centering="feature_mean"` and/or
  `scaling="feature_sd"` (sample SD, ddof=1). The defaults are `none`. These are
  display-only transformations; they never change testing or concentration data.
  Selected values must be complete and finite. A zero/undefined SD is an error
  when scaling, not a reason to silently fill or discard a feature.
- Distributions persist all selected original/prepared source values and exactly
  18 histogram bins per feature/representation, with saved edges and counts.
  Missing values are omitted and counted. Separate histograms can have different
  bin edges; do not interpret them as shared-bin density estimates.
- Boxplots overlay all available points without jitter. Whiskers use 1.5 IQR;
  outliers remain among the points. A separate saved aggregate summary records
  quartiles, median, IQR, whisker endpoints, mean, count and outlier count per
  feature/group. In paired designs lines connect complete
  pairs; available points lacking a counterpart remain visible. No significance
  annotations or extra tests are added. Imputed values remain part of the prepared
  source and must not be described as additional independent measurements.
- Volcano display rows require finite log2 fold change and finite adjusted p in
  [0,1]. Zero adjusted p is clipped to the recorded positive float floor only for
  display; the original adjusted p remains unchanged. Coloring uses saved alpha
  and adjusted p. Labels follow source/declared order, not a hidden p-value ranking.

make_plot returns aggregate metadata and references to a local source table,
display table, and configuration document. Inspect `omitted_values`,
`clipped_zero_p`, `paired_lines`, `display_notes`, selected features and scale.
Individual plotting tables and sample-order identifiers cannot be read through
read_result and must never be copied into the remote context. The final report
links these local files for review and reproduction.

If generation fails, committed source/display checkpoints and a failure record
remain available. A failed or empty figure does not satisfy the plan. Correct the
underlying issue through the appropriate revision; do not weaken a requirement
to close the stage. Valid upstream numerical results remain available.
