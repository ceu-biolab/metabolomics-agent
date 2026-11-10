# Metabolomics agent prototype

A single LLM chooses analysis tools, examines their outputs, and can revise its
analysis plan. Python executes the calculations and records every decision.
The tools use the libraries discussed in [Idkowiak et al. (2025)](https://doi.org/10.1038/s41467-025-63751-1)
and its [companion GitBook](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python).

The current scope is **two-group analysis of already quantified concentration or
intensity tables**. It includes independent and paired designs, missing-value
handling, group-wise descriptive statistics, univariate tests, plots, and a Markdown report.
New runs follow five validated stages: coordination, preparation, univariate
analysis, visualization, and reporting. Exploratory PCA is available as an optional
block of the statistical stage; other multivariate methods and batch/QC correction
are excluded. It does not process raw MS files. No LLM API request is
made by installation, tests, or the offline demo.

## Install

Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The workspace already has a `.venv` with the prototype installed. Use
`.venv/bin/python` directly if you prefer not to activate it. `requirements-tested.txt`
records direct dependency versions used for local verification; each run also saves
the actual package versions. It is a tested version snapshot, not a complete lockfile.

## Run without a key

```bash
python -m metabolomics_agent demo
```

This generates a synthetic study with 60 samples and 14 input features, including
missing values, a constant feature, and an outlier. A **scripted test driver** runs
through the same tool-calling loop as a live model. It selects imputation based on
observed missingness, performs a log-scale Welch analysis, and generates figures
through the five-stage workflow, including a heatmap selected from valid significant
comparison results. The default demo does not run PCA. Scientifically justified
sensitivity analyses can be requested through versioned stage revisions.

To include exploratory PCA and scores/variance/loading figures:

```bash
python -m metabolomics_agent demo --pca
python -m metabolomics_agent demo --pca-only
```

The second command runs a descriptive/PCA objective without univariate tests.
PCA uses scikit-learn and its own persisted centered/scaled matrix. Plotting uses
Seaborn and Matplotlib, as described in the article.

The demo verifies the machinery. It does not evaluate a real model's reasoning.
The synthetic ground truth is not provided to the agent.

Local evidence persistence records stage attempts under `stages/`, with JSON
and Markdown reports, operation logs, typed intermediate preprocessing tables,
imputation masks, and SHA-256 manifests. Failed operations preserve their last
committed checkpoints; reopening evidence marks unfinished attempts as interrupted.
Each completed stage also saves a validated `handoff.json` with aggregate context,
current evidence IDs, decisions, and relevant issues for the next stage. English
stage prompts and tool permissions are active; full individual sample tables stay
local. See [the PCA/heatmap extension](docs/staged-workflow/pca-heatmap-extension.md)
for automatic selection, PCA contracts and verification; [delivery F](docs/staged-workflow/delivery-F.md) for persisted plotting
sources and objective-specific reports, [delivery E](docs/staged-workflow/delivery-E.md)
for univariate auditing, and [delivery D](docs/staged-workflow/delivery-D.md) for
descriptive statistics.

The command prints its output directory. Open `report.md` there; it links to the
numerical tables and embeds PNG figures. An existing run directory is never
overwritten. To choose a path, use `demo --output runs/my-demo`.

## Connect an LLM

```bash
cp .env.example .env
```

Edit `.env` locally:

```dotenv
LLM_API_KEY=your-key
LLM_MODEL=your-chosen-tool-calling-model
LLM_BASE_URL=https://api.openai.com/v1
```

There is no default paid model. Choose the model and provider explicitly. The
default adapter uses the OpenAI-compatible **Chat Completions function-tool**
contract; an optional **Responses** adapter is also available. The configured
provider and model must support the selected API. The adapter
uses the OpenAI SDK, isolated in `llm.py`. Other API formats can implement the
small `Provider` protocol. The implementation follows the official
[function-calling guide](https://developers.openai.com/api/docs/guides/function-calling).

The legacy prototype was tested with GPT-6 Luna. The staged workflow is currently
verified with offline and mocked providers; live model evaluation is pending.

For GPT-6 Luna/Sol (including provider-prefixed names such as `openai/gpt-6-luna`),
Chat Completions function tools use `reasoning_effort=none`. To enable reasoning
with tools, configure the Responses adapter:

```dotenv
LLM_API=responses
LLM_REASONING_EFFORT=low
```

The repository includes persistent synthetic inputs. Start a real run with:

```bash
python -m metabolomics_agent run --study examples/synthetic/study.json
```

To generate another synthetic study in a new directory, use
`python -m metabolomics_agent make-example --output examples/another-study`.

For your own objective:

```bash
python -m metabolomics_agent run \
  --study examples/synthetic/study.json \
  --request "Compare condition versus control, inspect missingness and outliers, and assess whether the results depend on the test choice."
```

The `run` command loads `.env` from the current working directory by default;
`--env-file` selects another file. Existing environment variables take precedence.
Both `.env` and run outputs are ignored by Git. Keys are passed only to the API
client and are not stored in run metadata or traces.

Configuration options are documented in `.env.example`. In particular:

- Set `LLM_TOKEN_PARAMETER=max_tokens` if a compatible provider does not accept
  `max_completion_tokens`.
- `LLM_EXTRA_JSON` accepts optional provider parameters such as `temperature` or
  `reasoning_effort`; request limits and tool definitions cannot be overridden.
- Turn, tool-call, completion-token, aggregate-token, request-timeout, and run-time
  limits stop runaway loops. Run time is checked between requests/tools; a running
  numerical operation is not forcibly interrupted.
- Aggregate tokens include input and output. A conservative UTF-8 byte estimate
  checks the first request against the remaining allowance. Later estimates anchor
  to the provider's actual prompt count and add a conservative byte allowance for
  new messages. Provider usage is used afterward; providers without usage reports
  are charged the conservative estimate.
  This may stop a run early. These are token controls, not an exact currency budget.
- Requests have no automatic SDK retries. A failed request preserves the partial
  run, and the CLI returns nonzero. An incomplete analysis never produces a report
  marked complete. Inspect `incomplete.md` for the stopping status.

Individual sample tables remain local. The API receives the user's objective,
study metadata, feature names, aggregate diagnostics, selected result rows, and
the conversation. Local traces contain these messages and all numerical snapshots.

## Input contract

Use CSV, TSV, or XLSX. Rows are samples and feature columns are quantified
metabolites/lipids. Inputs must be nonnegative, before logarithms and scaling;
missing cells are allowed. Use unique, nonmissing sample IDs.

Supply either one combined table or a concentration table plus a separate metadata
table joined by sample ID. Separate tables must have exactly matching IDs;
metadata row order does not matter.

Example `study.json` for separate tables:

```json
{
  "data_path": "abundances.csv",
  "metadata_path": "metadata.csv",
  "sample_column": "sample_id",
  "group_column": "group",
  "reference_group": "control",
  "treatment_group": "condition",
  "design": "independent",
  "input_scale": "concentration",
  "normalization_status": "Already normalized to internal standards",
  "objective": "Compare condition with control; assess data quality and report limitations."
}
```

Data paths resolve relative to `study.json`. For a combined table, omit
`metadata_path`. List additional metadata columns, including numeric age or BMI,
in `metadata_columns`. Set `batch_column` when present. Prefer explicit
`feature_columns` for unfamiliar tables. Otherwise all remaining columns are
treated as features; unrecognized text columns cause an error. Every column of a
separate metadata table is automatically excluded from features.

For paired measurements use `"design": "paired"` and `"pair_column": "subject_id"`.
Every subject must have exactly one observation in each configured group. Pairs
are aligned by ID, not row order. Each group requires at least three samples;
each test requires at least three observed values per group (or complete observed
pairs), even when imputation has filled missing cells.

Only the configured reference/treatment groups are selected. Any other groups
are counted and recorded as excluded. The direction of all signed effects is
**treatment relative to reference**.

## Tools and adaptation

| Tool | Capability |
|---|---|
| `set_workflow_plan` | Objective, descriptive/two-group scope, and figure requirements |
| `inspect_dataset` | Missingness, zeros, distributions, normality diagnostics, outliers, batch/group counts |
| `summarize_dataset` | Group-wise or overall statistics on original/concentration/test scales, observed/imputed counts, quantiles, shape, CV, optional mode, and explicit undefined reasons |
| `set_preprocessing_plan` | Preparation choices after inspection, independent of the statistical plan |
| `preprocess` | Missingness/constant filtering; none/median/half-minimum/kNN imputation; optional median normalization; optional log2/log10 transformation |
| `inspect_univariate` | Prepared-data eligibility, observed/imputed counts, paired differences, normality diagnostics, and ties before selecting a test |
| `set_univariate_plan` | Diagnostic ID, statistical test, correction, hypothesis family, value policy, and purpose for the current dataset |
| `compare_groups` | Welch, pooled t, Mann–Whitney U, paired t, or Wilcoxon; BH, Holm–Šidák, Holm, or Bonferroni correction |
| `inspect_pca_inputs`, `set_pca_plan`, `run_pca`, `skip_pca` | Optional PCA block: diagnostics, frozen plan, durable fit outputs, or justified optional omission |
| `set_figure_plan` | Figures with current sources, feature-selection rationale, scale, display centering/scaling, ordering, and captions |
| `make_plot` | Before/after distributions, boxplots with points and pair lines, adjusted-p volcano, paginated non-clustered heatmaps, and current PCA scores/variance/loadings; exact local sources and configurations |
| `read_result` | Bounded result-table inspection by artifact ID, with optional feature filtering |
| `get_stage_context`, `get_workflow_state`, `read_stage_report` | Aggregate context, current plans, and bounded stage reports |
| `submit_stage_assessment` | Evidence-linked assessment and validated stage completion |
| `request_revision` | New attempt with downstream invalidation and preserved historical evidence |
| `validate_workflow` | Objective-specific completeness checks |
| `finish_analysis` | Completeness checks, evidence ID checks, computed summaries, interpretation, figures, limitations |

```bash
python -m metabolomics_agent tool-schemas
python -m metabolomics_agent tool-schemas --stage 03_univariate
python -m metabolomics_agent tool-schemas --stage 03_univariate --analysis-block pca
```

The current prompts are in `src/metabolomics_agent/prompts/stages/`. Each request
uses the shared English instructions plus the active stage's prompt and tools.
The bundle is frozen in run metadata; exact request messages and tools are saved
under `requests/`. Runtime checks reject out-of-stage tools, mismatched pairing,
unsafe log inputs, superseded sources, unregistered figures, and duplicate execution
of a statistical plan. PCA tools are offered only in the requested PCA block;
univariate tools become available after its assessment. Other multivariate tools
remain unavailable.

Plans and outputs are immutable. Each preparation plan prepares one dataset directly from
the original input. A preparation revision creates a new plan and dataset; no cumulative
double transformation occurs. Once a test has run, further analyses require an
exploratory plan. Trying methods until significance appears is explicitly
prohibited by the prompt. The harness records choices; it cannot prove the model's
rationale is scientifically sound.

Two analysis representations are saved, in addition to operation checkpoints:

1. Unlogged, unscaled concentrations **after selected imputation/normalization**,
   for group summaries and log2 ratios of arithmetic means.
2. Optionally transformed, unscaled values for statistical tests.

An aligned boolean imputation mask is also saved for each newly prepared dataset.
PCA representations are computed only when requested, under a separate PCA plan.
Heatmaps use an independent display-only representation derived from a registered
concentration or test scale.

Preparation requires full group-wise summaries of raw/original observations and
both prepared scales (including imputed cells). Additional `observed_only` summaries
can exclude imputed cells without recomputing normalization. Each summary saves
statistics, a separate undefined-reasons table, and `sNNN_summary.json` with methods.
Sample variance/SD use ddof=1; quantiles use linear interpolation; kurtosis is
bias-corrected Fisher excess. CV is SD/mean, not a percentage, and is not reported
on logarithmic scales. Optional exact-value mode reports ambiguity rather than
arbitrarily choosing one of tied modes.

Counts distinguish input observations, original missing values, zeros, source
missingness, imputed values, used observations, and excluded imputations. For
prepared data, zeros explicitly reclassified as missing count as missing inputs;
the original summary preserves them as measured values. Full sample tables and
masks cannot be read by the model through `read_result`.

New runs require `inspect_univariate` before the statistical plan. Eligibility
requires at least three original observations per group and, for paired designs,
three originally observed complete pairs; imputations cannot supply these minima.
The diagnostic records prepared-value and observed-origin normality separately
and does not automatically select a test.

Each attempt saves uncorrected results before correction, a provenance/sample-count
audit, per-feature issues, and `uaNNN_univariate.json` with methods and correction
family. Skipped features remain in the tables. Audit `n_test_*` counts are zero
when no test is executed; historical result counts describe prepared candidates.
A finite statistic and a finite p-value in [0,1] are required for correction.
No valid tests or a failed correction block completion while preserving evidence.
Any executed tests require an exploratory plan for subsequent comparisons, even
if correction failed. Existing runs retain their saved protocol and prompts.

Tests are two-sided. Welch/pooled t analyses include signed Hedges' g and
feature-wise confidence intervals for mean differences on the test scale.
Mann–Whitney includes signed rank-biserial correlation; it does not generally
test equality of medians. Paired t includes confidence intervals for paired mean
differences. Undefined fold changes/effects remain missing, not zero.

Each new figure saves its source table, exact display data, configuration, and
PNG with linked hashes. Histograms save bin edges/counts; boxplots also save
quartiles, medians and whisker endpoints. Paired boxplots connect complete pairs
and retain available points without counterparts. Source tables/configurations
with individual identifiers remain local; aggregate descriptors and caveats pass
to later stages. The final report links each figure's reproduction files.

Heatmaps accept an explicit feature list or `selection_mode="significant"` with a
current `comparison_id` and no explicit features. The latter selects valid rows
with adjusted p below the comparison's saved alpha, preserving its correction
family and table order. Candidate inclusion/exclusion reasons are saved. All
selected features are displayed in pages (default 40 rows), with no implicit
12-feature cap. Zero selected features is a documented omission; a failed page
cannot satisfy completion. Selection on the same data is descriptive and does
not provide independent confirmation of group separation.

Heatmaps require complete finite selected values. Optional per-feature
mean centering and/or sample-SD scaling apply only to the display matrix. Samples
follow input order or reference/treatment order; no clustering is performed.
Volcano plots record omitted points and clip zero adjusted p-values only for the
display coordinate, preserving numerical inference. Empty or failed figures do
not satisfy a plan; committed plotting checkpoints remain available.

PCA has a separate `disabled`/`optional`/`required` workflow requirement. In its
block, inspect sources before registering the PCA plan and execute it once.
Inputs may be `concentrations` or `testing`; the default is the existing testing
scale. Mean centering is explicit; scaling is none, auto, or Pareto. The default
SD convention is ddof=0 (scikit-learn StandardScaler); ddof=1 can be selected.
PCA uses full SVD, no whitening, and two components by default. Missing values,
constant selected features and insufficient numerical rank require a recorded
correction or optional omission; no implicit imputation or sample removal occurs.
The main PCA uses retained/preselected features independently of univariate p-values.

Input and PCA matrices, scaling factors, scores, unit eigenvector weights,
explained variance, singular values and reconstructible model metadata are saved.
Individual matrices/scores remain local. Groups color saved scores after fitting.
PCA is exploratory; apparent separation does not establish significance or predictive
performance. Ordinary PCA does not model paired dependence. An old run retains
its frozen capabilities and prompts; it is not implicitly upgraded.

Multiplicity correction covers valid tested features within each comparison.
Confidence intervals are not multiplicity-adjusted. Correction across adaptive
reruns, imputation uncertainty propagation, covariate models, batch correction,
supervised prediction, biological annotation, and pathway analysis are not
implemented. Batch metadata is inspected and confounding is flagged, not corrected.

Completion depends on the registered objective and its required figures.
Descriptive-only objectives can omit univariate testing and volcano plots with
saved skip reports. Reports state the registered scope and stage outcomes, carry
forward open issue codes and unchecked assumptions, and distinguish current
evidence from historical attempts. A stopped run preserves linked partial reports
without declaring completion. Current comparisons, plans, and figures must agree; historical
results remain available locally after revisions. Numerical
summaries come from saved calculations; agent interpretations cite existing
evidence IDs but are not semantically verified. Reports also record the initial
user request, model-visible study configuration, and local input snapshots/hashes.
`model_input.json` saves the exact initial messages and tool definitions.
If the model requests essential
missing information, the run stops with a saved incomplete record; update the
study/request as needed, or continue the frozen run with a clarification:

```bash
python -m metabolomics_agent resume --run runs/your-run \
  --request "Continue from the saved outputs and finish the report."
```

Resume restores numerical snapshots, stage position, frozen prompts, and the
original conversation without repeating completed calls. It requires the same
model, endpoint, and API format. Old runs retain their legacy protocol and are
not silently migrated. The legacy library protocol (`RunState(..., protocol="legacy")`)
is retained for historical verification; new CLI and default library runs use `staged_v1`.
The aggregate
token allowance remains cumulative across sessions; turn/tool/time limits apply
to the new invocation. Increasing limits is an explicit configuration change.
Runs with unresolved/truncated tool-call batches cannot be resumed automatically.

## Published example benchmark

The synthetic demo is distinct from exact reproduction of the GitBook. To check
selected published values, download **dataset no. 1, `Lipidomics_dataset.xlsx`**
from the [authors' dataset page](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/introduction/example-data-sets)
and put it under `data/`:

```bash
python -m metabolomics_agent reference-check --dataset data/Lipidomics_dataset.xlsx
```

This computes the original T-versus-N pooled two-sided t-tests and Mann–Whitney
tests, with Holm–Šidák correction across 127 features. It checks the published
PC 34:1 t statistic/p-value/degrees of freedom, PC 36:5 U statistic/p-value, and
one published corrected p-value. It writes the complete results and pass/fail
checks without involving an LLM. Sources and exact parameters are included.

For a live analysis of that dataset, use `examples/gitbook-study.json` after
downloading it. The agent's objective specifies exact reproduction; any suggested
alternative should be a separately labeled exploratory analysis.

## Verification

```bash
python -m pytest -q
```

Tests cover numerical agreement with SciPy/statsmodels, effect direction,
correction families, separate scales, paired alignment, missing-data restrictions,
adaptive revisions, invalid tool calls, completeness checks, spending/turn limits,
an end-to-end offline run, and the actual SDK protocol through a mocked HTTP
transport. No test makes a paid API call.

## Source layout

```text
src/metabolomics_agent/
  agent.py          tool-calling loop and budgets
  llm.py            provider protocol, API adapter, scripted demo driver
  models.py         study, plan, tool arguments, and LLM configuration
  data.py           input validation and metadata alignment
  state.py          immutable artifacts, provenance, and event log
  analysis.py       preprocessing, tests, effects, and legacy PCA
  pca.py            audited optional PCA and plot sources
  heatmaps.py       saved significance selection and page coverage
  visualization.py  durable drawing data, configurations and images
  plots.py          figures
  tools.py          tool schemas and validated dispatch
  report.py         computed report and evidence-linked interpretation
  cli.py            command-line entry point
  benchmark.py      selected exact GitBook reference checks
  workflow.py       stage contracts, transitions, revisions, and handoffs
  workflow_models.py independent workflow/preparation/statistical/figure plans
  descriptive.py    statistics by group/scale with observed/imputed provenance
  prompts/stages/   shared and stage-specific English instructions
  prompts/system.md legacy scientific behavior and tool-use guidance
```
