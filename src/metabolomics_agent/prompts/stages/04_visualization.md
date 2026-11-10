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
