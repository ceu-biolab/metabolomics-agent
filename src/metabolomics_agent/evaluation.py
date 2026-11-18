"""Repeatable offline evaluation and preparation of a separate live-review protocol."""

import hashlib
import importlib.metadata
import json
from pathlib import Path

from .agent import Agent
from .evaluation_cases import CASES, ScriptedProvider, case_manifest, create_inputs, script
from .evidence import EvidenceStore
from .models import LLMConfig
from .run_audit import Checks, audit_run
from .state import RunState


RUBRIC = [
    {"id": "objective", "question": "Does the analysis address the declared objective and unsupported requests honestly?"},
    {"id": "design", "question": "Are groups, effect direction, pairing and sample identities interpreted correctly?"},
    {"id": "preparation", "question": "Are missingness, zeros, normalization and transformations scientifically justified without inventing information?"},
    {"id": "methods", "question": "Are method choices and assumptions justified beyond normality p-values and visual separation?"},
    {"id": "evidence", "question": "Do numerical claims match saved outputs, including uncertainty, nonsignificant and undefined results?"},
    {"id": "selection", "question": "Are multiplicity, result-selected heatmaps and PCA independence explained accurately?"},
    {"id": "adaptation", "question": "Are data-informed revisions treated as exploratory with preserved history?"},
    {"id": "limitations", "question": "Are inherited warnings, unchecked assumptions, imputation and paired-PCA limitations retained?"},
]


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render(report, title):
    lines = [f"# {title}", "", f"Status: **{report['status']}**.", "",
             "This is offline execution/record validation. Live LLM reasoning and scientific interpretation quality were not evaluated.", ""]
    if "analysis_status" in report:
        lines += [f"Recorded analysis status: {report['analysis_status']}; finished: {report['analysis_finished']}.", ""]
    if "cases" in report:
        lines += ["| Case | Outcome | Checks failed | Audit | Analysis |", "|---|---|---:|---|---|"]
        for case in report["cases"]:
            name = case["name"]
            lines.append(f"| {name} | {case['status']} | {case['checks_failed']} | [Audit](cases/{name}/audit.md) | {case['analysis_status']} ([record](cases/{name}/run/{'report.md' if case.get('analysis_finished') else 'incomplete.md'})) |")
    else:
        lines += [f"Checks passed: {report.get('checks_passed', 0)}; failed: {report.get('checks_failed', 0)}. "
                  f"Numerical values compared: {report.get('numeric_values_checked', 0)}.", ""]
    lines += ["", "## Failed checks", ""]
    failed = [check for check in report.get("checks", []) if not check["passed"]]
    lines += [f"- {check['id']}: expected {check.get('expected')}; observed {check.get('observed')}" for check in failed]
    if not failed:
        lines += ["No failed checks recorded at this report level. Review per-case reports for details."]
    lines += ["", "The machine-readable JSON report contains individual checks and prompt fingerprints. "
              "A passed audit does not prove that a scientific rationale or a natural-language claim is valid.", ""]
    return "\n".join(lines)


def save_audit(run_directory: Path, output: Path):
    if output.resolve().is_relative_to(run_directory.resolve()):
        raise ValueError("Audit output must be outside the inspected run directory.")
    output.mkdir(parents=True, exist_ok=False)
    report = audit_run(run_directory)
    store = EvidenceStore(output)
    store.write_json("audit.json", report)
    store.atomic_write("audit.md", lambda path: path.write_text(_render(report, "Saved run audit")))
    store.write_json("manifest.json", {"schema_version": 1, "files": {
        name: _hash(output/name) for name in ["audit.json", "audit.md"]}})
    return report


def _expectations(case, state, audit, extra):
    checks = Checks()
    checks.add("case.expected_status", state.status == case.expected_status, case.expected_status, state.status)
    checks.add("case.expected_completion", state.finished == (case.expected_status == "complete"))
    expected_rejections = sum(not step["expect_ok"] for step in script(case))
    checks.add("case.controlled_rejections", audit.get("trace", {}).get("rejected_calls") == expected_rejections,
               expected_rejections, audit.get("trace", {}).get("rejected_calls"))
    successful = audit.get("trace", {}).get("successful_tools", [])
    if case.expected_status == "complete":
        checks.add("case.single_preparation", successful.count("preprocess") == 1, 1, successful.count("preprocess"))
    if case.analysis_kind == "descriptive":
        checks.add("case.no_univariate", not state.comparisons and "compare_groups" not in successful)
    if case.pca == "required":
        checks.add("case.completed_pca", len(state.pcas) == 1 and state.workflow.data.pca_reviewed)
    elif case.pca == "optional":
        checks.add("case.explicit_pca_skip", bool(state.workflow.data.pca_skip) and not state.pcas)
    else:
        checks.add("case.no_pca", not state.pcas and "run_pca" not in successful)
    if case.variant == "null":
        checks.add("case.no_discoveries", all(value["significant_features"] == 0 for value in state.comparisons.values()))
        checks.add("case.no_significant_heatmap", any(value["status"] == "skipped_no_significant" for value in state.plot_results.values()))
    if case.variant == "unsafe_log":
        kinds = {artifact.kind for artifact in state.evidence.artifacts.values()}
        checks.add("case.partial_checkpoints", {"imputed", "imputation_mask"} <= kinds and not state.datasets)
        checks.add("case.no_fake_comparison", not state.comparisons)
    if case.resume_after:
        checks.add("case.restore_without_originals", extra.get("restored_without_sources", False))
        checks.add("case.no_repeated_tests", successful.count("compare_groups") == 1, 1, successful.count("compare_groups"))
    audit["checks"].extend(checks.items)
    audit["checks_failed"] += sum(not value["passed"] for value in checks.items)
    audit["checks_passed"] += sum(value["passed"] for value in checks.items)
    audit["status"] = "failed" if audit["checks_failed"] else "passed" if state.finished else "incomplete"
    return audit


def evaluate_offline(output: Path, *, cases=None, seed=42, progress=print):
    """Run only local scripted providers. Never load environment credentials or instantiate a live provider."""
    requested = [case.name for case in CASES] if cases is None else list(cases)
    known = {case.name for case in CASES}
    if not requested or set(requested)-known or len(requested) != len(set(requested)):
        raise ValueError("Choose nonempty, unique supported case names.")
    selected = set(requested)
    if "resume" in selected:
        selected.add("independent")
    output.mkdir(parents=True, exist_ok=False)
    root = EvidenceStore(output)
    result = {"schema_version": 1, "evaluation_type": "offline_scripted_suite", "status": "running",
              "seed": seed, "requested_cases": requested, "executed_cases": [], "cases": [],
              "api_calls": 0, "live_model_evaluation": "not_run", "llm_reasoning_quality": "not_assessed",
              "checks": [], "numeric_values_checked": 0,
              "packages": {name: importlib.metadata.version(name) for name in [
                  "metabolomics-agent", "numpy", "pandas", "scipy", "scikit-learn", "statsmodels"]}}
    live_cases = []
    for case in CASES:
        if case.name not in selected:
            continue
        progress(f"Offline case: {case.name}")
        directory = output/"cases"/case.name
        directory.mkdir(parents=True)
        root.write_json(f"cases/{case.name}/case.json", case_manifest(case))
        config, request = create_inputs(case, directory/"input", seed)
        if not case.guards and not case.resume_after:
            live_cases.append({"id": case.name, "study": f"cases/{case.name}/input/study.json", "request": request,
                "review_criteria": [criterion["id"] for criterion in RUBRIC],
                "note": "Judge justified supported choices; the offline script is not a required live tool sequence."})
        state = RunState(config, directory/"input", directory/"run", seed)
        cfg = LLMConfig(model="offline-evaluation-script", max_turns=case.resume_after or 64,
                        max_tool_calls=128, max_total_tokens=500000)
        extra = {}
        try:
            Agent(state, ScriptedProvider(script(case)), cfg).run(request, progress=lambda _: None)
            if case.resume_after:
                if state.status != "turn_limit":
                    raise ValueError("Resume case did not stop at its declared stable boundary.")
                files = [directory/"input"/name for name in ["data.csv", "metadata.csv"]]
                backups = {path: path.read_bytes() for path in files}
                try:
                    for path in files:
                        path.unlink()
                    state = RunState.restore(directory/"run")
                    extra["restored_without_sources"] = not any(path.exists() for path in files)
                finally:
                    for path, content in backups.items():
                        path.write_bytes(content)
                Agent(state, ScriptedProvider(script(case)), cfg.model_copy(update={"max_turns": 64})).run(
                    "Continue from frozen outputs without repeating confirmed calculations.", resume=True, progress=lambda _: None)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
            extra["execution_error"] = str(error)
        audit = _expectations(case, state, audit_run(directory/"run"), extra)
        if "execution_error" in extra:
            audit["checks"].append({"id": "case.execution", "passed": False, "observed": extra["execution_error"]})
            audit["checks_failed"] += 1
            audit["status"] = "failed"
        audit["scenario"] = case.name
        audit["expected_analysis_status"] = case.expected_status
        audit["recovery"] = extra
        root.write_json(f"cases/{case.name}/audit.json", audit)
        root.atomic_write(f"cases/{case.name}/audit.md", lambda path, data=audit: path.write_text(_render(data, "Offline case audit")))
        result["executed_cases"].append(case.name)
        result["cases"].append({"name": case.name, "status": "passed" if not audit["checks_failed"] else "failed",
            "analysis_status": state.status, "analysis_finished": state.finished, "audit_status": audit["status"],
            "checks_failed": audit["checks_failed"], "checks_passed": audit["checks_passed"],
            "expected_incomplete": case.expected_status != "complete",
            "requests": audit.get("trace", {}).get("requests", 0),
            "rejected_calls": audit.get("trace", {}).get("rejected_calls", 0)})
        result["numeric_values_checked"] += audit["numeric_values_checked"]
        root.write_json("evaluation.json", result)
    # Pausing must not change confirmed numerical outputs, even with fresh provider objects.
    if "resume" in selected:
        left = json.loads((output/"cases/independent/run/state.json").read_text())
        right = json.loads((output/"cases/resume/run/state.json").read_text())
        same = {}
        for kind in ["concentrations", "testing_values", "comparison"]:
            def find(state):
                return next(value["path"] for value in state["artifacts"].values() if value["kind"] == kind)
            try:
                same[kind] = (output/"cases/independent/run"/find(left)).read_bytes() == (output/"cases/resume/run"/find(right)).read_bytes()
            except (StopIteration, OSError, KeyError):
                same[kind] = False
        result["checks"].append({"id": "resume.same_numerical_outputs", "passed": all(same.values()), "observed": same})
    result["status"] = "passed" if all(case["status"] == "passed" for case in result["cases"]) and all(check["passed"] for check in result["checks"]) else "failed"
    result["cases_passed"] = sum(case["status"] == "passed" for case in result["cases"])
    root.write_json("evaluation.json", result)
    root.atomic_write("evaluation.md", lambda path: path.write_text(_render(result, "Offline evaluation suite")))
    root.write_json("live_evaluation_manifest.json", {"schema_version": 1, "execution_status": "not_run",
        "model": None, "provider": None, "credential_environment_variable": "LLM_API_KEY",
        "max_total_tokens_per_case": None, "maximum_spend": None, "cases": live_cases,
        "rubric": [{**item, "status": "not_assessed", "human_review_required": True} for item in RUBRIC],
        "notice": "This is preparation only. No live provider was constructed or evaluated; budgets and explicit execution are still required."})
    root.write_json("manifest.json", {"schema_version": 1, "seed": seed, "files": {
        str(path.relative_to(output)): _hash(path) for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "manifest.json"}})
    return result
