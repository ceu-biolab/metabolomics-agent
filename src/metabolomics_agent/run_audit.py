"""Read-only checks of saved runs. Passing checks do not grade LLM reasoning."""

import hashlib
import json
import re
import warnings
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.decomposition import PCA
from sklearn.impute import KNNImputer
from statsmodels.stats.multitest import multipletests

from .evidence import EvidenceStore
from .evidence_models import StageReport
from .serialization import clean_json
from .workflow import COMMON_TOOLS, PCA_TOOLS, STAGES, STAGE_TOOLS
from .workflow_models import StageHandoff


class Checks:
    def __init__(self):
        self.items = []
        self.numeric_values = 0

    def add(self, name, passed, expected=None, observed=None):
        self.items.append(clean_json({"id": name, "passed": bool(passed),
                                      "expected": expected, "observed": observed}))

    def close(self, name, actual, expected):
        actual, expected = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        same = actual.shape == expected.shape
        passed = same and np.allclose(actual, expected, rtol=1e-9, atol=1e-12, equal_nan=True)
        errors = np.abs(actual[np.isfinite(actual)]-expected[np.isfinite(actual)]) if same else []
        self.numeric_values += expected.size
        self.add(name, passed, {"shape": list(expected.shape)}, {
            "shape": list(actual.shape), "max_absolute_error": float(np.max(errors)) if len(errors) else None})


def _json(path):
    return json.loads(path.read_text())


def _table(store, aid, index=None):
    result = store.read_table(aid)
    return result.set_index(index) if index else result


def _inputs(store, state, config):
    def read(kind):
        aid = next(aid for aid, value in state["artifacts"].items() if value["kind"] == kind)
        return _table(store, aid, config["sample_column"])
    return read("input_values"), read("input_metadata")


def _phase_requests(paths, conversation, info, state, checks):
    """Reconstruct saved model windows from the complete local conversation."""
    strategy = info.get("context_strategy", "full_history")
    checks.add("trace.context_strategy", strategy in {"full_history", "phase_window_v1"},
               ["full_history", "phase_window_v1"], strategy)
    if strategy != "phase_window_v1":
        checks.add("trace.legacy_context_records", all("context_window" not in _json(path) for path in paths),
                   "No phase-window records in a full-history run")
        return {"context_strategy": strategy}

    ledger = info.get("user_requests")
    valid_ledger = (isinstance(ledger, list) and bool(ledger)
                    and len(conversation) >= 2 and conversation[1].get("role") == "user")
    if valid_ledger:
        for position, entry in enumerate(ledger):
            if not isinstance(entry, dict):
                valid_ledger = False
                break
            index = entry.get("message_index")
            if (not isinstance(index, int) or isinstance(index, bool)
                    or not 1 <= index < len(conversation)
                    or (position == 0 and (index != 1 or entry.get("kind") != "initial"))
                    or (position > 0 and (entry.get("kind") != "clarification"
                                          or index <= ledger[position-1]["message_index"]))
                    or conversation[index].get("role") != "user"
                    or (position > 0 and conversation[index].get("content") != entry.get("text"))):
                valid_ledger = False
                break
        if valid_ledger:
            try:
                initial = json.loads(conversation[1]["content"])
                valid_ledger = initial["request"] == ledger[0].get("text") == info.get("request")
            except (ValueError, TypeError, KeyError):
                valid_ledger = False
    checks.add("trace.user_requests", valid_ledger, "Ordered requests matching conversation", len(ledger) if isinstance(ledger, list) else None)

    reply_positions = [index for index, message in enumerate(conversation)
                       if message.get("role") == "assistant"]
    checks.add("trace.conversation_response_count", len(reply_positions) == len(paths),
               len(paths), len(reply_positions))
    previous = None
    previous_end = None
    sizes = []
    for number, path in enumerate(paths, 1):
        record = _json(path)
        messages = record.get("messages", [])
        window = record.get("context_window")
        valid_window = (isinstance(window, dict)
                        and isinstance(window.get("start_index"), int)
                        and not isinstance(window.get("start_index"), bool)
                        and 2 <= window["start_index"] <= len(conversation)
                        and isinstance(window.get("epoch"), int)
                        and not isinstance(window.get("epoch"), bool)
                        and window["epoch"] > 0
                        and isinstance(window.get("phase"), str))
        checks.add(f"request.{number}.context_window", valid_window, "Saved valid phase cursor", window)
        if not valid_window:
            continue
        start = window["start_index"]
        context = json.loads(messages[-1]["content"])["stage_context"] if messages else {}
        phase = f"{context.get('revision')}:{record.get('stage')}:{context.get('analysis_block')}"
        checks.add(f"request.{number}.context_phase",
                   window["phase"] == phase and context.get("stage") == record.get("stage"), phase, window["phase"])
        if previous is None:
            checks.add(f"request.{number}.context_initial_cursor", start == 2, 2, start)
        else:
            changed = window["phase"] != previous["phase"]
            transition = (window["epoch"] > previous["epoch"] and start > previous_end
                          if changed else window == previous)
            checks.add(f"request.{number}.context_transition", transition,
                       "New phase advances epoch and cursor; same phase retains both", window)
        previous = window
        pending = set()
        for message in conversation[:start]:
            if message.get("role") == "assistant":
                pending.update(call["id"] for call in message.get("tool_calls", []))
            elif message.get("role") == "tool":
                pending.discard(message.get("tool_call_id"))
        checks.add(f"request.{number}.context_batch_boundary", not pending, [], sorted(pending))
        seen_calls = set()
        orphaned = []
        for message in messages[1:-1]:
            if message.get("role") == "assistant":
                seen_calls.update(call["id"] for call in message.get("tool_calls", []))
            elif message.get("role") == "tool" and message.get("tool_call_id") not in seen_calls:
                orphaned.append(message.get("tool_call_id"))
        checks.add(f"request.{number}.context_tool_links", not orphaned, [], orphaned)
        if valid_ledger:
            earlier = [conversation[entry["message_index"]] for entry in ledger[1:]
                       if entry["message_index"] < start]
            tail_length = len(messages) - 3 - len(earlier)
            reply_index = reply_positions[number-1] if number <= len(reply_positions) else None
            expected = ([{"role": "system", "content": messages[0].get("content")},
                         conversation[1], *earlier,
                         *conversation[start:reply_index], messages[-1]]
                        if messages and reply_index is not None and start <= reply_index else [])
            window_matches = (reply_index is not None and tail_length == reply_index - start
                              and messages == expected)
            checks.add(f"request.{number}.context_messages", window_matches,
                       "Exact current-phase conversation plus original request and prior clarifications",
                       len(messages))
        previous_end = reply_positions[number-1] if number <= len(reply_positions) else start
        sizes.append(len(json.dumps({"messages": messages, "tools": record.get("tools", [])},
                                    ensure_ascii=False).encode()))
    saved_window = state.get("context_window")
    valid_saved = (isinstance(saved_window, dict)
                   and isinstance(saved_window.get("epoch"), int)
                   and not isinstance(saved_window.get("epoch"), bool)
                   and isinstance(saved_window.get("phase"), str)
                   and isinstance(saved_window.get("start_index"), int)
                   and not isinstance(saved_window.get("start_index"), bool)
                   and 2 <= saved_window["start_index"] <= len(conversation)
                   and (previous is None or
                        (saved_window["epoch"] >= previous["epoch"]
                         and saved_window["start_index"] >= previous["start_index"])))
    checks.add("trace.saved_context_window", valid_saved, "Cursor present and no earlier than last request", saved_window)
    return {"context_strategy": strategy, "request_payload_bytes": sizes,
            "maximum_request_payload_bytes": max(sizes, default=0)}


def _requests(root, state, info, store, checks):
    paths = sorted((root/"requests").glob("request_*.json"))
    events = [json.loads(line) for line in (root/"events.jsonl").read_text().splitlines()]
    replies = [event for event in events if event["kind"] == "model_response"]
    checks.add("trace.request_response_count", len(paths) == len(replies), len(paths), len(replies))
    conversation = _json(root/"conversation.json")
    context_trace = _phase_requests(paths, conversation, info, state, checks)
    results = {message["tool_call_id"]: json.loads(message["content"]) for message in conversation if message["role"] == "tool"}
    raw, metadata = _inputs(store, state, info["study"])
    # Public labels may legitimately coincide with an ID; very short strings are ambiguous.
    public = set(map(str, raw.columns)) | set(map(str, metadata[info["study"]["group_column"]]))
    identifiers = list(map(str, raw.index))
    pair = info["study"].get("pair_column")
    if pair:
        identifiers.extend(map(str, metadata[pair].unique()))
    protected = sorted({value for value in identifiers if len(value) >= 4 and value not in public})
    short = len(set(identifiers) - set(protected))
    pattern = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(value) for value in protected) + r")(?![\w])") if protected else None
    hashes = {}
    rejected = 0
    pending = []
    successful = []
    for index, path in enumerate(paths):
        record = _json(path)
        context = json.loads(record["messages"][-1]["content"])["stage_context"]
        stage = record["stage"]
        keys = ["descriptive_version", "univariate_version", "visualization_version", "pca_version"]
        checks.add(f"request.{index+1}.capability_profile", all(context.get(key, 0) == info.get(key, 0) for key in keys))
        block = context.get("analysis_block", "univariate")
        active = "03_pca" if stage == STAGES[2] and block == "pca" else stage
        system = record["messages"][0]["content"]
        expected = info["workflow_prompts"]["common"] + "\n\n" + info["workflow_prompts"][active]
        checks.add(f"request.{index+1}.frozen_prompt", system.startswith(expected + "\n\n"), active, stage)
        digest = hashlib.sha256(system.encode()).hexdigest()
        checks.add(f"request.{index+1}.prompt_consistency", active not in hashes or hashes[active] == digest)
        hashes[active] = digest
        allowed = COMMON_TOOLS | (PCA_TOOLS if active == "03_pca" else STAGE_TOOLS[stage])
        if stage == STAGES[2] and active != "03_pca" and context.get("workflow_plan", {}).get("analysis_kind") == "descriptive":
            allowed = COMMON_TOOLS.copy()
        if not info.get("descriptive_version"):
            allowed -= {"summarize_dataset"}
        if not info.get("univariate_version"):
            allowed -= {"inspect_univariate"}
        names = {tool["function"]["name"] for tool in record["tools"]}
        if info.get("visualization_version") == 2 and info.get("pca_version") == 1:
            from .tools import ToolRegistry
            profile = SimpleNamespace(run_info=info, workflow=SimpleNamespace(stage=stage, allowed_tools=lambda: allowed))
            definitions = {tool["function"]["name"]: tool["function"]["parameters"] for tool in ToolRegistry(profile).schemas()}
            for tool in record["tools"]:
                name = tool["function"]["name"]
                actual = tool["function"]["parameters"]
                expected_parameters = definitions.get(name)
                fingerprint = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                expected_hash, actual_hash = fingerprint(expected_parameters), fingerprint(actual)
                checks.add(f"request.{index+1}.{name}.tool_parameters", actual_hash == expected_hash,
                           expected_hash, actual_hash)
        checks.add(f"request.{index+1}.tool_scope", names == allowed, sorted(allowed), sorted(names))
        payload = json.dumps(record, ensure_ascii=False)
        checks.add(f"request.{index+1}.identifier_screen", not pattern or not pattern.search(payload),
                   "No unambiguous individual identifiers", "screened")
        if index < len(replies):
            for call in replies[index]["message"].get("tool_calls", []):
                result = results.get(call["id"])
                if result is None:
                    pending.append(call["id"])
                    honest_stop = not state["finished"] and state["status"] in {
                        "tool_limit", "token_limit", "time_limit", "model_output_incomplete", "failed", "interrupted"}
                    checks.add(f"request.{index+1}.tool_response", honest_stop,
                               "Saved response or explicitly interrupted unexecuted batch", call["id"])
                    continue
                name = call["function"]["name"]
                if not result.get("ok"):
                    rejected += 1
                    continue
                successful.append(name)
                checks.add(f"request.{index+1}.successful_tool_offered", name in names, "Offered tool", name)
                if name == "read_result":
                    args = json.loads(call["function"]["arguments"])
                    artifact = store.artifacts.get(args["artifact_id"])
                    checks.add(f"request.{index+1}.private_read_blocked", bool(artifact) and not artifact.individual_samples and artifact.path.endswith(".csv"))
    inventory = {key: {"sha256": hashlib.sha256(value.encode()).hexdigest(), "characters": len(value)}
                 for key, value in info["workflow_prompts"].items()}
    return {"requests": len(paths), **context_trace, "rejected_calls": rejected, "successful_tools": successful,
            "ambiguous_or_public_identifiers_exempted": short, "prompt_inventory": inventory,
            "observed_system_hashes": hashes, "pending_tool_responses": pending, "resume_batch_safe": not pending}


def _stages(root, state, info, store, checks):
    workflow = state["workflow"]
    completed = workflow["completed_reports"]
    if state["finished"]:
        checks.add("stages.completion", set(completed) == set(STAGES), list(STAGES), list(completed))
    outcomes = {}
    inherited = set()
    for index, stage in enumerate(STAGES):
        if stage not in completed:
            continue
        path = completed[stage]
        report = StageReport.model_validate(_json(root/path))
        directory = (root/path).parent
        checks.add(f"stage.{stage}.outcome", report.status in {"completed", "completed_with_warnings", "skipped"},
                   "Completed or justified skipped", report.status)
        checks.add(f"stage.{stage}.records", all((directory/name).is_file() for name in [
            "stage_report.md", "context.json", "prompt_snapshot.json", "artifact_manifest.json", "handoff.json"]))
        handoff = StageHandoff.model_validate(_json(directory/"handoff.json"))
        expected_next = STAGES[index+1] if index < 4 else None
        checks.add(f"stage.{stage}.handoff", handoff.stage_id == stage and handoff.next_stage == expected_next
                   and handoff.plan_ids["workflow"] == workflow["plan_id"])
        checks.add(f"stage.{stage}.artifacts", all(aid in store.artifacts for aid in handoff.artifact_ids))
        codes = {issue.code for issue in report.issues}
        checks.add(f"stage.{stage}.inherited_issues", inherited <= codes, sorted(inherited), sorted(codes))
        inherited.update(codes)
        checks.add(f"stage.{stage}.blocking", not any(issue.blocking and issue.resolution == "open" for issue in report.issues))
        outcomes[stage] = report.status
    if state["finished"]:
        checks.add("report.final_exists", (root/"report.md").is_file())
        text = (root/"report.md").read_text()
        if info.get("visualization_version"):
            checks.add("report.issue_codes", all(code in text for code in inherited), sorted(inherited), "Final report checked")
        missing = []
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if target.startswith(("https:", "http:", "#")):
                continue
            target = target.split("#", 1)[0]
            candidate = (root/target).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                missing.append(target)
        checks.add("report.local_links", not missing, [], missing)
    else:
        checks.add("report.honest_partial", not (root/"report.md").exists() and (root/"incomplete.md").is_file())
    return outcomes


def _preparation_reference(raw, plan):
    values = raw.mask(raw == 0) if plan["zero_as_missing"] else raw.copy()
    retained = (values.isna().mean() <= plan["max_missing_fraction"]) & (values.nunique() > 1)
    values = values.loc[:, retained]
    missing = values.isna()
    data = values.to_numpy(copy=True)
    if plan["imputation"] == "median":
        replacement = np.nanmedian(data, axis=0)
        data = np.where(np.isnan(data), replacement, data)
    elif plan["imputation"] == "half_min":
        replacement = np.nanmin(np.where(data > 0, data, np.nan), axis=0)/2
        data = np.where(np.isnan(data), replacement, data)
    elif plan["imputation"] == "knn" and missing.any().any():
        data = KNNImputer(n_neighbors=min(5, len(data)-1)).fit_transform(data)
    mask = missing.to_numpy() & ~np.isnan(data)
    if plan["normalization"] == "median":
        medians = np.nanmedian(data, axis=1)
        data = data/medians[:, None]*np.median(medians)
    testing = data if plan["transform"] == "none" else np.log2(data) if plan["transform"] == "log2" else np.log10(data)
    retained = np.nanvar(testing, axis=0, ddof=1) > 0
    names = values.columns[retained]
    return (pd.DataFrame(data[:, retained], index=values.index, columns=names),
            pd.DataFrame(testing[:, retained], index=values.index, columns=names),
            pd.DataFrame(mask[:, retained], index=values.index, columns=names))


def _preparation(state, info, store, checks):
    raw, meta = _inputs(store, state, info["study"])
    sid = info["study"]["sample_column"]
    for did, dataset in state["datasets"].items():
        if dataset["plan_id"] not in state.get("preprocessing_plans", {}):
            continue  # Archived profiles remain available without imposing new provenance.
        plan = state["preprocessing_plans"][dataset["plan_id"]]
        refs = dataset["artifact_ids"]
        concentrations = _table(store, refs["concentrations"], sid)
        testing = _table(store, refs["testing"], sid)
        checks.add(f"preparation.{did}.alignment", concentrations.index.equals(meta.index)
                   and testing.index.equals(meta.index) and concentrations.columns.equals(testing.columns))
        expected_concentrations, expected_testing, expected_mask = _preparation_reference(raw, plan)
        checks.add(f"preparation.{did}.features", list(concentrations.columns) == list(expected_concentrations.columns))
        checks.close(f"preparation.{did}.concentrations", concentrations, expected_concentrations)
        checks.close(f"preparation.{did}.test_scale", testing, expected_testing)
        if "imputation_mask" in refs:
            mask = _table(store, refs["imputation_mask"], sid)
            checks.close(f"preparation.{did}.mask_reference", mask, expected_mask)
            original = raw.loc[concentrations.index, concentrations.columns]
            observed = original.notna() & ((original != 0) if plan["zero_as_missing"] else True)
            checks.add(f"preparation.{did}.mask", mask.index.equals(concentrations.index)
                       and mask.columns.equals(concentrations.columns) and all(dtype == bool for dtype in mask.dtypes)
                       and not ((mask & observed) | (mask & concentrations.isna()) |
                                (concentrations.notna() & ~observed & ~mask)).any().any())


def _descriptives(state, info, store, checks):
    raw, metadata = _inputs(store, state, info["study"])
    sid = info["study"]["sample_column"]
    for summary_id, summary in state.get("descriptive_summaries", {}).items():
        original = summary["dataset_id"] == "raw"
        if original:
            values = raw
            mask = pd.DataFrame(False, index=raw.index, columns=raw.columns)
            observed = raw.notna()
        else:
            dataset = state["datasets"][summary["dataset_id"]]
            plan = state["preprocessing_plans"][dataset["plan_id"]]
            values = _table(store, dataset["artifact_ids"]["testing" if summary["scale"] == "testing" else "concentrations"], sid)
            mask = _table(store, dataset["artifact_ids"]["imputation_mask"], sid)
            observed = raw[values.columns].notna() & ((raw[values.columns] != 0) if plan["zero_as_missing"] else True)
        rows = _table(store, summary["artifacts"]["statistics"])
        for index, row in rows.iterrows():
            selected = metadata[info["study"]["group_column"]] == row.group if summary["grouping"] == "group" else pd.Series(True, index=values.index)
            source = values.loc[selected, row.feature]
            imputed = mask.loc[selected, row.feature]
            origin = observed.loc[selected, row.feature]
            keep = source.notna() & (~imputed if summary["value_policy"] == "observed_only" else True)
            data = source[keep].to_numpy()
            n = len(data)
            prefix = f"descriptive.{summary_id}.{index}"
            checks.close(prefix+".counts", [row.n_total, row.n_observed_input, row.n_missing_input,
                row.n_available_source, row.n_imputed_source, row.n_used, row.n_observed_used, row.n_imputed_used],
                [len(source), origin.sum(), (~origin).sum(), source.notna().sum(), imputed.sum(), n,
                 (keep & origin).sum(), (keep & imputed).sum()])
            names = ["mean", "median", "sd", "variance", "min", "max", "range", "q1", "q3", "iqr",
                     *[f"p{p}" for p in range(10,100,10)], "skewness", "kurtosis", "cv"]
            expected = dict.fromkeys(names, np.nan)
            if n:
                q1, q3 = np.quantile(data, [.25, .75], method="linear")
                expected.update(mean=np.mean(data), median=np.median(data), min=np.min(data), max=np.max(data),
                                range=np.ptp(data), q1=q1, q3=q3, iqr=q3-q1)
                expected.update({f"p{p}": np.quantile(data, p/100, method="linear") for p in range(10,100,10)})
                if n >= 2:
                    expected.update(sd=np.std(data, ddof=1), variance=np.var(data, ddof=1))
                varying = len(np.unique(data)) > 1
                if varying and n >= 3:
                    expected["skewness"] = stats.skew(data, bias=False)
                if varying and n >= 4:
                    expected["kurtosis"] = stats.kurtosis(data, fisher=True, bias=False)
                if row["transform"] == "none" and n >= 2 and np.mean(data) > 0:
                    expected["cv"] = np.std(data, ddof=1)/np.mean(data)
            checks.close(prefix+".metrics", [row[name] for name in names], [expected[name] for name in names])
            if "mode" in rows.columns:
                unique, counts = np.unique(data, return_counts=True)
                maximum = int(counts.max()) if n else 0
                ties = int((counts == maximum).sum()) if n else 0
                mode = unique[np.argmax(counts)] if maximum > 1 and ties == 1 else np.nan
                checks.close(prefix+".mode", [row["mode"], row.mode_frequency, row.mode_n_ties], [mode, maximum, ties])


def _comparisons(state, info, store, checks):
    config = info["study"]
    sid, group = config["sample_column"], config["group_column"]
    raw, meta = _inputs(store, state, config)
    for cid, comparison in state["comparisons"].items():
        if not info.get("univariate_version"):
            continue
        plan = state["univariate_plans"][comparison["plan_id"]]
        dataset = state["datasets"][comparison["dataset_id"]]
        preparation = state["preprocessing_plans"][dataset["plan_id"]]
        testing = _table(store, dataset["artifact_ids"]["testing"], sid)
        concentrations = _table(store, dataset["artifact_ids"]["concentrations"], sid)
        table = _table(store, comparison["artifact_id"]).set_index("feature")
        expected_p = pd.Series(np.nan, index=table.index)
        for feature, row in table.iterrows():
            original = raw[feature].notna() & ((raw[feature] != 0) if preparation["zero_as_missing"] else True)
            r = meta[group] == config["reference_group"]
            t = meta[group] == config["treatment_group"]
            counts = [int(original[r].sum()), int(original[t].sum())]
            checks.close(f"comparison.{cid}.{feature}.observed", [row.n_observed_reference, row.n_observed_treatment], counts)
            reference, treatment = testing.loc[r, feature].dropna(), testing.loc[t, feature].dropna()
            eligible = min(counts) >= 3
            if config["design"] == "paired":
                pairs = meta[[config["pair_column"], group]].copy()
                pairs["__value__"] = testing[feature]
                pairs = pairs.pivot(index=config["pair_column"], columns=group, values="__value__")
                original_pairs = meta[[config["pair_column"], group]].copy()
                original_pairs["__observed__"] = original
                observed = original_pairs.pivot(index=config["pair_column"], columns=group, values="__observed__").all(axis=1)
                eligible = eligible and int(observed.sum()) >= 3
                pairs = pairs.dropna()
                reference, treatment = pairs[config["reference_group"]], pairs[config["treatment_group"]]
                checks.close(f"comparison.{cid}.{feature}.observed_pairs", row.n_observed_pairs, int(observed.sum()))
            checks.close(f"comparison.{cid}.{feature}.source_counts", [row.n_reference, row.n_treatment], [len(reference), len(treatment)])
            concentration_r, concentration_t = concentrations.loc[r, feature].mean(), concentrations.loc[t, feature].mean()
            fold = np.log2(concentration_t/concentration_r) if concentration_r > 0 and concentration_t > 0 else np.nan
            checks.close(f"comparison.{cid}.{feature}.fold_change", row.log2_fold_change, fold)
            eligible = eligible and min(len(reference), len(treatment)) >= 3
            if not eligible:
                checks.close(f"comparison.{cid}.{feature}.ineligible_p", row.p_value, np.nan)
                continue
            intervals = [np.nan, np.nan]
            effect, rank_effect = np.nan, np.nan
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if plan["test"] in {"welch_t", "student_t"}:
                    result = stats.ttest_ind(treatment, reference, equal_var=plan["test"] == "student_t")
                    interval = result.confidence_interval(confidence_level=1-plan["alpha"])
                    intervals = [interval.low, interval.high]
                    df = len(treatment)+len(reference)-2
                    pooled = np.sqrt(((len(treatment)-1)*treatment.var(ddof=1)+(len(reference)-1)*reference.var(ddof=1))/df)
                    effect = (1-3/(4*df-1))*(treatment.mean()-reference.mean())/pooled if pooled > 0 else np.nan
                elif plan["test"] == "paired_t":
                    result = stats.ttest_rel(treatment, reference)
                    interval = result.confidence_interval(confidence_level=1-plan["alpha"])
                    intervals = [interval.low, interval.high]
                elif plan["test"] == "mannwhitney":
                    result = stats.mannwhitneyu(treatment, reference, alternative="two-sided", method="auto", use_continuity=True)
                    rank_effect = 2*result.statistic/(len(treatment)*len(reference))-1
                else:
                    result = ((0., 1.) if np.array_equal(treatment.to_numpy(), reference.to_numpy()) else
                              stats.wilcoxon(treatment, reference, alternative="two-sided", method="auto", zero_method="wilcox", correction=False))
            statistic, p = result[:2]
            if not np.isfinite(statistic) or not np.isfinite(p) or not 0 <= p <= 1:
                p, intervals = np.nan, [np.nan, np.nan]
            if not np.isfinite(intervals).all():
                intervals = [np.nan, np.nan]
            expected_p.loc[feature] = p
            checks.close(f"comparison.{cid}.{feature}.test", [row.statistic, row.p_value], [statistic, p])
            checks.close(f"comparison.{cid}.{feature}.effects_ci", [row.hedges_g, row.rank_biserial, row.ci_low_test_scale, row.ci_high_test_scale], [effect, rank_effect, *intervals])
        valid = expected_p.notna()
        adjusted = pd.Series(np.nan, index=expected_p.index)
        if valid.any():
            adjusted.loc[valid] = multipletests(expected_p[valid], alpha=plan["alpha"], method=plan["correction"])[1]
        checks.close(f"comparison.{cid}.adjusted_p", table.adjusted_p_value, adjusted)
        checks.add(f"comparison.{cid}.discoveries", int(table.significant.sum()) == int((adjusted < plan["alpha"]).sum())
                   == comparison["significant_features"])
        checks.add(f"comparison.{cid}.tested_counts", comparison["features_tested"] == int(valid.sum())
                   and comparison["features_skipped"] == int((~valid).sum()))


def _pca(state, info, store, checks):
    sid = info["study"]["sample_column"]
    for pid, result in state["pcas"].items():
        if not info.get("pca_version"):
            continue
        refs, plan = result["artifacts"], state["pca_plans"][result["plan_id"]]
        values = _table(store, refs["input"], sid)
        factors = _table(store, refs["scaling"], "feature")
        matrix = _table(store, refs["matrix"], sid)
        divisor = np.ones(len(values.columns))
        if plan["scaling"] != "none":
            divisor = values.std(ddof=plan["scaling_ddof"]).to_numpy()
            if plan["scaling"] == "pareto":
                divisor = np.sqrt(divisor)
        source_dataset = state["datasets"][result["dataset_id"]]
        source = _table(store, source_dataset["artifact_ids"]["testing" if plan["scale"] == "testing" else "concentrations"], sid)
        selected = source[plan["features"]] if plan["features"] else source
        checks.add(f"pca.{pid}.source_axes", values.index.equals(selected.index) and values.columns.equals(selected.columns))
        checks.close(f"pca.{pid}.source_values", values, selected)
        expected = (values-values.mean())/divisor
        checks.close(f"pca.{pid}.centers", factors.center, values.mean())
        checks.close(f"pca.{pid}.divisors", factors.divisor, divisor)
        checks.close(f"pca.{pid}.matrix", matrix, expected)
        reference = PCA(n_components=plan["components"], svd_solver="full", whiten=False)
        reference.fit(expected.to_numpy())
        scores = _table(store, refs["scores"])
        weights = _table(store, refs["loadings"])
        names = [f"PC{i+1}" for i in range(plan["components"])]
        # Eigen-equations allow signs and degenerate-eigenspace choices without an n-samples squared matrix.
        axes = weights.set_index("feature").loc[values.columns, names].to_numpy()
        numeric = expected.to_numpy()
        checks.close(f"pca.{pid}.score_projection", scores[names], (numeric-numeric.mean(axis=0)) @ axes)
        checks.close(f"pca.{pid}.eigen_equation", numeric.T @ (numeric @ axes)/(len(numeric)-1),
                     axes*reference.explained_variance_)
        checks.close(f"pca.{pid}.variance", result["explained_variance_ratio"], reference.explained_variance_ratio_)
        checks.close(f"pca.{pid}.weight_orthogonality", weights[names].to_numpy().T @ weights[names].to_numpy(), np.eye(len(names)))
        model = _json(store.path(store.artifacts[refs["model"]].path))
        checks.add(f"pca.{pid}.model_features", model["features"] == list(values.columns))
        reconstructed = ((values.to_numpy()-model["centers"])/model["divisors"]-model["pca_mean"]) @ np.asarray(model["components"]).T
        checks.close(f"pca.{pid}.model_reconstruction", reconstructed, scores[names])
        checks.add(f"pca.{pid}.private_scores", store.artifacts[refs["scores"]].individual_samples)


def _figures(state, store, checks):
    workflow = state["workflow"]
    for gid in workflow.get("plot_result_ids", []):
        plot = state["plot_results"][gid]
        spec = plot["figure_spec"]
        selection = _table(store, plot["artifacts"]["selection"])
        chosen = selection.loc[selection.included, "feature"].tolist()
        if spec["selection_mode"] == "significant":
            comparison = state["comparisons"][spec["comparison_id"]]
            source = _table(store, comparison["artifact_id"])
            expected = source.loc[(source.status == "ok") & np.isfinite(source.adjusted_p_value)
                & source.adjusted_p_value.between(0, 1) & (source.adjusted_p_value < comparison["alpha"]), "feature"].tolist()
            checks.add(f"heatmap.{gid}.selection", chosen == expected)
        checks.add(f"heatmap.{gid}.count", plot["selected_count"] == len(chosen))
        if plot["status"] == "completed":
            actual = [feature for aid in plot["figure_ids"] for feature in state["artifacts"][aid]["selected_features"]]
            checks.add(f"heatmap.{gid}.coverage", chosen == actual and len(plot["figure_ids"]) == plot["pages_expected"])
        elif plot["status"] == "skipped_no_significant":
            checks.add(f"heatmap.{gid}.omission", not chosen and not plot["figure_ids"] and spec["selection_mode"] == "significant")
    for aid in workflow.get("figure_ids", []):
        figure = state["artifacts"][aid]
        refs = figure.get("plot_artifact_ids", {})
        checks.add(f"figure.{aid}.reproduction_sources", {"source", "display", "configuration"} <= set(refs))
        if not refs:
            continue
        source, display = _table(store, refs["source"]), _table(store, refs["display"])
        if figure["kind"] == "heatmap":
            matrix = source.pivot(index="sample_id", columns="feature", values="value")
            expected = matrix-matrix.mean() if figure["centering"] == "feature_mean" else matrix.copy()
            if figure["scaling"] == "feature_sd":
                expected /= matrix.std(ddof=1)
            actual = display.pivot(index="sample_id", columns="feature", values="display_value")
            checks.close(f"figure.{aid}.heatmap_scale", actual, expected)
        elif figure["kind"] == "distributions":
            for (feature, representation), part in source.groupby(["feature", "representation"], sort=False):
                expected_counts, edges = np.histogram(part.value.dropna(), bins=18)
                bins = display[(display.feature == feature) & (display.representation == representation)]
                checks.close(f"figure.{aid}.{feature}.{representation}.counts", bins["count"], expected_counts)
                checks.close(f"figure.{aid}.{feature}.{representation}.edges", bins[["bin_left", "bin_right"]],
                             np.column_stack([edges[:-1], edges[1:]]))
        elif figure["kind"] == "boxplot":
            if "pair_id" in display:
                complete = display.groupby(["feature", "pair_id"])["group"].nunique() == 2
                checks.close(f"figure.{aid}.pair_lines", figure["paired_lines"], complete.sum())
            if "summary" in refs:
                summaries = _table(store, refs["summary"]).set_index(["feature", "group"])
                for (feature, group), part in display.groupby(["feature", "group"]):
                    q1, med, q3 = np.quantile(part.value, [.25, .5, .75], method="linear")
                    inside = part.value[part.value.between(q1-1.5*(q3-q1), q3+1.5*(q3-q1))]
                    row = summaries.loc[(feature, group)]
                    checks.close(f"figure.{aid}.{feature}.{group}.box", row[["q1", "med", "q3", "whislo", "whishi"]],
                                 [q1, med, q3, min(q1, inside.min()), max(q3, inside.max())])
            checks.close(f"figure.{aid}.points", display.value, source.value.dropna())
        elif figure["kind"] == "volcano":
            checks.close(f"figure.{aid}.volcano", display.minus_log10_adjusted_p,
                         -np.log10(display.adjusted_p_value.clip(lower=np.finfo(float).tiny)))
        elif figure["kind"] == "pca_scores":
            pca = state["pcas"][figure["source_id"]]
            scores = _table(store, pca["artifacts"]["scores"])
            names = [f"PC{i}" for i in figure["components"]]
            checks.close(f"figure.{aid}.scores", display[names], scores[names])
        elif figure["kind"] == "pca_scree":
            result = state["pcas"][figure["source_id"]]
            checks.close(f"figure.{aid}.variance", display.explained_variance_ratio, result["explained_variance_ratio"])
            checks.close(f"figure.{aid}.cumulative", display.cumulative_variance_ratio, np.cumsum(result["explained_variance_ratio"]))
        elif figure["kind"] == "pca_loadings":
            result = state["pcas"][figure["source_id"]]
            weights = _table(store, result["artifacts"]["loadings"]).set_index("feature")
            checks.close(f"figure.{aid}.weights", display.weight, weights.loc[display.feature, f"PC{figure['component']}"])



def audit_run(directory: Path):
    """Inspect a complete or partial staged run without restoring or writing to it."""
    root = directory.resolve()
    checks = Checks()
    report = {"schema_version": 1, "evaluation_type": "offline_record_audit", "run_directory": str(root),
              "audit_network_calls": 0, "live_llm_decisions": "not_assessed",
              "interpretation": "Checks assess recorded execution and numerical consistency, not scientific rationale or LLM quality."}
    try:
        info, state = _json(root/"run.json"), _json(root/"state.json")
        report.update(analysis_status=state["status"], analysis_finished=state["finished"],
            capability_profile={key: info.get(key, 0) for key in ["descriptive_version", "univariate_version", "visualization_version", "pca_version"]})
        if info.get("protocol") != "staged_v1":
            raise ValueError("This audit supports staged runs; legacy replay retains its separate tests.")
        store = EvidenceStore(root)
        checks.add("evidence.integrity", True)
        checks.add("evidence.state_index", all(aid in store.artifacts and value["path"] == store.artifacts[aid].path
                   and value["sha256"] == store.artifacts[aid].sha256 for aid, value in state["artifacts"].items()))
        report["registered_artifacts"] = len(store.artifacts)
        for name, function in [("trace", _requests), ("stages", _stages), ("preparation", _preparation),
                               ("descriptives", _descriptives), ("comparisons", _comparisons), ("pca", _pca), ("figures", _figures)]:
            try:
                if name in {"trace", "stages"}:
                    report[name] = function(root, state, info, store, checks)
                elif name == "figures":
                    if info.get("visualization_version"):
                        function(state, store, checks)
                    else:
                        report["figure_source_coverage"] = "not_applicable_to_pre_F_profile"
                else:
                    function(state, info, store, checks)
            except (ValueError, TypeError, KeyError, OSError, IndexError, StopIteration) as error:
                checks.add(f"{name}.readable", False, "Consistent saved records", str(error))
    except (ValueError, TypeError, KeyError, OSError) as error:
        checks.add("evidence.readable", False, "Complete readable metadata and intact evidence", str(error))
    failed = sum(not check["passed"] for check in checks.items)
    report.update(status="failed" if failed else "passed" if report.get("analysis_finished") else "incomplete",
                  checks=checks.items, checks_passed=len(checks.items)-failed, checks_failed=failed,
                  numeric_values_checked=checks.numeric_values)
    return clean_json(report)
