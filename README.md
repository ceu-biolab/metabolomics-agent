# Metabolomics agent prototype

A single LLM chooses analysis tools, examines their outputs, and can revise its
analysis plan. Python executes the calculations and records every decision.
The tools use the libraries discussed in [Idkowiak et al. (2025)](https://doi.org/10.1038/s41467-025-63751-1)
and its [companion GitBook](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python).

The current scope is **two-group analysis of already quantified concentration or
intensity tables**. It includes independent and paired designs, missing-value
handling, univariate tests, PCA, plots, and a Markdown report. It does not process
raw MS files. No LLM API request is made by installation, tests, or the offline demo.

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
observed missingness, performs a log-scale Welch analysis, generates figures, and
adds an explicitly exploratory rank-based sensitivity analysis.

The demo verifies the machinery. It does not evaluate a real model's reasoning.
The synthetic ground truth is not provided to the agent.

Local evidence persistence now records tool attempts under `stages/`, with JSON
and Markdown reports, operation logs, typed intermediate preprocessing tables,
imputation masks, and SHA-256 manifests. Failed operations preserve their last
committed checkpoints; reopening evidence marks unfinished attempts as interrupted.
This is the persistence layer for the planned staged workflow; stage routing and
the new prompts are not active yet. See [delivery B](docs/staged-workflow/delivery-B.md)
for scope, recovery behavior, and verification results.

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

This model has been tested with GPT-6 Luna, but it should theoretically work with other models. 

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
| `inspect_dataset` | Missingness, zeros, distributions, normality diagnostics, outliers, batch/group counts |
| `set_plan` | Versioned method selection and rationale; later revisions become exploratory |
| `preprocess` | Missingness/constant filtering; none/median/half-minimum/kNN imputation; optional median normalization; optional log2/log10 transformation |
| `compare_groups` | Welch, pooled t, Mann–Whitney U, paired t, or Wilcoxon; BH, Holm–Šidák, Holm, or Bonferroni correction |
| `run_pca` | PCA with centering and optional auto/Pareto scaling; scores, loadings, explained variance |
| `make_plot` | Before/after distributions, boxplots, adjusted-p volcano, PCA, heatmap |
| `read_result` | Bounded result-table inspection by artifact ID, with optional feature filtering |
| `finish_analysis` | Completeness checks, evidence ID checks, computed summaries, interpretation, figures, limitations |

```bash
python -m metabolomics_agent tool-schemas
```

The system prompt is in `src/metabolomics_agent/prompts/system.md`. The agent is
free to select valid steps and revise plans when the objective or diagnostics
justify it. The harness rejects mismatched pairing, unsafe log inputs, incomplete
PCA inputs, unknown tools/arguments, and duplicate testing of the same dataset.

Plans and outputs are immutable. Each plan prepares one dataset directly from
the original input. A revision creates a new plan and dataset; no cumulative
double transformation occurs. Once a test has run, further analyses require an
exploratory plan. Trying methods until significance appears is explicitly
prohibited by the prompt. The harness records choices; it cannot prove the model's
rationale is scientifically sound.

Three data representations are saved:

1. Unlogged, unscaled concentrations **after selected imputation/normalization**,
   for group summaries and log2 ratios of arithmetic means.
2. Optionally transformed, unscaled values for statistical tests.
3. Separately centered/scaled values for PCA and heatmaps.

Tests are two-sided. Welch/pooled t analyses include signed Hedges' g and
feature-wise confidence intervals for mean differences on the test scale.
Mann–Whitney includes signed rank-biserial correlation; it does not generally
test equality of medians. Paired t includes confidence intervals for paired mean
differences. Undefined fold changes/effects remain missing, not zero.

Multiplicity correction covers valid tested features within each comparison.
Confidence intervals are not multiplicity-adjusted. Correction across adaptive
reruns, imputation uncertainty propagation, covariate models, batch correction,
supervised prediction, biological annotation, and pathway analysis are not
implemented. Batch metadata is inspected and confounding is flagged, not corrected.

Completion requires a tested current plan, at least one PCA, and distribution,
volcano, and PCA figures. Reports include every comparison and plan. Numerical
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

Resume restores numerical snapshots and the original conversation without
repeating completed calls. It requires the same model, endpoint, and API format. The aggregate
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
  analysis.py       preprocessing, tests, effects, and PCA
  plots.py          figures
  tools.py          tool schemas and validated dispatch
  report.py         computed report and evidence-linked interpretation
  cli.py            command-line entry point
  benchmark.py      selected exact GitBook reference checks
  prompts/system.md scientific behavior and tool-use guidance
```
