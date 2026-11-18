"""External evaluation of immutable scientific runs using registered case contracts."""

import hashlib
import importlib.metadata
import json
import platform
import shutil
from pathlib import Path
from datetime import datetime
from time import monotonic

import pandas as pd

from .evidence import EvidenceStore
from .evaluation_metrics import aggregate, numeric_comparison, pca_weights_comparison, result, set_comparison, table_comparison
from .evaluation_models import CaseEvaluation, EvaluationCase, ManualReview, StageEvaluation
from .run_audit import audit_run


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes its declared root: {relative}")
    return path


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_case(path):
    def invalid(value):
        raise ValueError(f"Non-finite JSON value: {value}")
    return EvaluationCase.model_validate(json.loads(path.read_text(), parse_constant=invalid))


class RunObservations:
    def __init__(self, root):
        self.root = root.resolve()
        self.state = json.loads((root / "state.json").read_text())
        self.info = json.loads((root / "run.json").read_text())
        self.store = EvidenceStore(root)
        self._audit = None
        self._efficiency = None

    @property
    def audit(self):
        if self._audit is None:
            self._audit = audit_run(self.root)
        return self._audit

    def stage_report(self, stage):
        stage = "03_univariate" if stage == "03_pca" else stage
        paths = sorted((self.root / "stages" / stage).glob("attempt_*/stage_report.json"))
        return json.loads(paths[-1].read_text()) if paths else None

    def efficiency(self, block, report):
        if self._efficiency is None:
            self._efficiency = {}
            requests = sorted((self.root / 'requests').glob('request_*.json'))
            events = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
            responses = [event for event in events if event.get('kind') == 'model_response']
            for index, path in enumerate(requests):
                record = json.loads(path.read_text())
                context = json.loads(record['messages'][-1]['content'])['stage_context']
                stage = '03_pca' if record['stage']=='03_univariate' and context.get('analysis_block')=='pca' else record['stage']
                values = self._efficiency.setdefault(stage, {'model_requests':0,'tool_calls':0,'reported_total_tokens':0,'responses_with_reported_tokens':0})
                values['model_requests'] += 1
                if index < len(responses):
                    response = responses[index]
                    values['tool_calls'] += len(response['message'].get('tool_calls', []))
                    tokens = response.get('usage', {}).get('total_tokens')
                    if isinstance(tokens, int):
                        values['reported_total_tokens'] += tokens
                        values['responses_with_reported_tokens'] += 1
        data = dict(self._efficiency.get(block, {'model_requests':0,'tool_calls':0,'reported_total_tokens':None,'responses_with_reported_tokens':0}))
        if not data['responses_with_reported_tokens']:
            data['reported_total_tokens'] = None
        data['production_stage_elapsed_seconds'] = None
        if report and report.get('finished_at'):
            data['production_stage_elapsed_seconds'] = (datetime.fromisoformat(report['finished_at']) - datetime.fromisoformat(report['started_at'])).total_seconds()
        data['timing_scope'] = 'Shared production-stage duration for PCA/univariate blocks; do not add twice.' if block in {'03_pca','03_univariate'} else 'Production stage wall time.'
        return data

    def get(self, selector, stage):
        evidence = []
        if selector.document == "table":
            candidates = [aid for aid, artifact in self.store.artifacts.items()
                          if (aid == selector.artifact_id if selector.artifact_id else artifact.kind == selector.artifact_kind)]
            if len(candidates) != 1:
                raise ValueError("A table selector must identify exactly one registered artifact.")
            value = self.store.read_table(candidates[0])
            evidence = candidates
            if selector.column:
                value = value[selector.column].tolist()
        elif selector.document == "state":
            value = self.state
        elif selector.document == "run":
            value = self.info
        elif selector.document == "audit":
            value = self.audit
        elif selector.document == "findings":
            value = json.loads((self.root / "findings.json").read_text())
        else:
            value = self.stage_report(stage)
            if value is None:
                raise KeyError("No report for this stage.")
        for key in selector.path:
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value, evidence


def check_metric(spec, stage, observations, references, base, case_id):
    try:
        if spec.when is not None:
            condition, _ = observations.get(spec.when.observation, stage)
            if (condition == spec.when.equals) == spec.when.negate:
                return result(spec, "not_applicable", explanation="Registered metric condition is false.")
        if spec.validator == "manual":
            entry = references.get(spec.reference_id)
            if entry is None or entry.review_status != "reviewed":
                return result(spec, "not_assessed", explanation="Human/scientific review has not been recorded.")
            path = safe_path(base, entry.path)
            if fingerprint(path) != entry.sha256:
                return result(spec, "failed", explanation="Review record checksum mismatch.")
            review = ManualReview.model_validate_json(path.read_text())
            if review.case_id != case_id or review.metric_id != spec.id or review.evidence_index_sha256 != fingerprint(observations.root / 'evidence_index.json'):
                return result(spec, "not_assessed", explanation="Review does not bind this case, criterion and frozen evidence.")
            return result(spec, review.status, observed={'reviewer':review.reviewer,'reviewed_on':review.reviewed_on},
                          details={'scope':spec.review_scope}, explanation=review.explanation)
        if spec.validator == "audit":
            checks = [check for check in observations.audit["checks"]
                      if any(check["id"].startswith(prefix) for prefix in spec.audit_prefixes)]
            if not checks:
                return result(spec, "not_assessed", explanation="No audit checks match the registered scope.")
            failed = [check for check in checks if not check["passed"]]
            return result(spec, "failed" if failed else "passed", denominator=len(checks),
                          observed={"checked": len(checks), "failed": len(failed)},
                          details={"failed_checks": failed}, explanation="Read-only audit of the registered scope.")
        if spec.reference_id:
            entry = references[spec.reference_id]
            path = safe_path(base, entry.path)
            if fingerprint(path) != entry.sha256:
                return result(spec, "failed", explanation="Reference checksum mismatch.")
            expected = pd.read_csv(path, dtype={key: str for key in spec.keys}) if spec.validator in {"table", "pca_weights"} else json.loads(path.read_text())
        elif spec.expected_is_set:
            expected = spec.expected
        else:
            return result(spec, "not_assessed", explanation="No independent expected answer is registered.")
        actual, evidence = observations.get(spec.observation, stage)
        details, denominator = {}, None
        if spec.validator == "numeric":
            passed, details, denominator = numeric_comparison(actual, expected, spec.tolerance)
        elif spec.validator == "table":
            passed, details, denominator = table_comparison(actual, expected, spec.keys, spec.columns, spec.tolerance)
        elif spec.validator == "pca_weights":
            passed, details, denominator = pca_weights_comparison(actual, expected, spec.keys, spec.columns,
                                                                spec.component_blocks, spec.tolerance)
        elif spec.validator == "set":
            passed, details, denominator = set_comparison(actual, expected)
        elif spec.validator == "one_of":
            passed = actual in expected
        else:
            passed = actual == expected
        return result(spec, "passed" if passed else "failed",
                      expected={"reference_id": spec.reference_id} if spec.reference_id else expected,
                      observed={"rows": len(actual)} if isinstance(actual, pd.DataFrame) else actual,
                      details=details, denominator=denominator, evidence=evidence,
                      explanation="Compared with the frozen registered expectation.")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        return result(spec, "failed", explanation=f"Required evidence is invalid or missing: {error}")


def evaluate_saved_case(case, base, run, *, stage_only=None):
    observations = RunObservations(run)
    references = {entry.id: entry for entry in case.references.entries}
    try:
        reference_integrity = all(fingerprint(safe_path(base, entry.path)) == entry.sha256
                                  for entry in [*case.references.entries, *case.references.support_files])
    except (OSError, ValueError):
        reference_integrity = False
    stages = []
    status_by_stage = {}
    for expectation in case.stages:
        report = observations.stage_report(expectation.stage_id)
        inactive = expectation.applicability == "out_of_scope"
        condition_missing = False
        if expectation.condition:
            try:
                value, _ = observations.get(expectation.condition.observation, expectation.stage_id)
                inactive = (value == expectation.condition.equals) == expectation.condition.negate
            except (KeyError, ValueError, OSError, TypeError, IndexError):
                condition_missing = True
        blocked = any(status_by_stage.get(stage) in {"failed", "blocked_by_dependency", "not_assessed"}
                      for stage in expectation.depends_on)
        blocked |= bool(set(expectation.requires_artifact_kinds) - {artifact.kind for artifact in observations.store.artifacts.values()})
        metrics = []
        for spec in expectation.metrics:
            if inactive:
                item = result(spec, "not_applicable", explanation=expectation.reason)
            elif condition_missing:
                item = result(spec, "not_assessed", explanation="Applicability condition cannot be resolved.")
            elif blocked:
                item = result(spec, "blocked_by_dependency", explanation="A registered dependency is not validated.")
            else:
                item = check_metric(spec, expectation.stage_id, observations, references, base, case.case_id)
            metrics.append(item)
        status, summary = aggregate(metrics)
        try:
            summary['efficiency'] = observations.efficiency(expectation.stage_id, report)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            summary['efficiency'] = {'status':'not_assessed','reason':str(error)}
        if inactive:
            status = "not_applicable"
        status_by_stage[expectation.stage_id] = status
        production_stage = "03_univariate" if expectation.stage_id == "03_pca" else expectation.stage_id
        if stage_only is None or stage_only == expectation.stage_id:
            stages.append(StageEvaluation(stage_id=expectation.stage_id, production_stage_id=production_stage,
                attempt_id=report.get("attempt_id") if report else None,
                execution_status=report.get("status", "not_started") if report else "not_started",
                status=status, metrics=metrics, summary=summary, reason=expectation.reason))
    if not stages:
        raise ValueError("The requested stage is not registered in this case.")
    status, summary = aggregate([metric for stage in stages for metric in stage.metrics])
    expected_hashes = {entry.sha256 for entry in case.dataset.files}
    observed_hashes = {entry["sha256"] for entry in observations.info["inputs"]}
    from .models import StudyConfig
    if expected_hashes != observed_hashes or StudyConfig.model_validate(observations.info["study"]).model_dump() != case.study.model_dump():
        status = "failed"
        summary["input_contract_error"] = "Saved input hashes or study configuration differ from the case."
    match = observations.state["status"] == case.expected_execution_status
    if stage_only is None and not match:
        status = "failed"
    if not reference_integrity:
        status = "failed"
        summary["reference_contract_error"] = "Reference or producer provenance files differ from their frozen hashes."
    manual_results = [(spec, item) for expectation in case.stages for spec in expectation.metrics
                      for stage in stages if stage.stage_id == expectation.stage_id
                      for item in stage.metrics if item.id == spec.id and spec.validator == 'manual' and spec.review_scope == 'scientific']
    reviewed = sum(item.status == 'passed' for _, item in manual_results)
    review_status = 'reviewed' if manual_results and reviewed == len(manual_results) else 'partial' if reviewed else 'pending'
    return CaseEvaluation(case_id=case.case_id, mode=case.mode, scientific_review=review_status,
        scientific_run=str(observations.root), evidence_index_sha256=fingerprint(observations.root / 'evidence_index.json'),
        execution_mode="stage_isolated" if stage_only else "end_to_end",
        analysis_status=observations.state["status"], analysis_finished=observations.state["finished"],
        expected_outcome_match=match, status=status, stages=stages, summary=summary)


def render_evaluation(report):
    lines = [f"# Evaluation: {report.case_id}", "", f"Evaluation: **{report.status}**.",
             f"Analysis: **{report.analysis_status}**; completed: {report.analysis_finished}.",
             f"Scientific review: **{report.scientific_review}**.", "", report.narrative_scope, "",
             "| Stage | Evaluation | Coverage | Passed / evaluated |", "|---|---|---:|---:|"]
    for stage in report.stages:
        summary = stage.summary
        lines.append(f"| {stage.stage_id} | {stage.status} | {summary['coverage']} | "
                     f"{summary['counts'].get('passed', 0)} / {summary['evaluated']} |")
    lines += ["", "## Criteria", ""]
    for stage in report.stages:
        lines += [f"- {metric.id}: **{metric.status}** — {metric.explanation}" for metric in stage.metrics]
    return "\n".join(lines) + "\n"


def _scripted_run(case, base, output, *, stage_only=None):
    from .agent import Agent
    from .evaluation_cases import ScriptedProvider
    from .llm import Reply
    from .models import LLMConfig
    from .state import RunState

    if not case.script:
        raise ValueError("New offline runs require an explicit scripted stimulus; use --run for other runs.")
    dataset_root = (base / case.dataset.root).resolve()
    for entry in case.dataset.files:
        if fingerprint(safe_path(dataset_root, entry.path)) != entry.sha256:
            raise ValueError(f"Dataset checksum mismatch: {entry.path}")
    if stage_only:
        package = next((item for item in case.stage_packages if item.stage_id == stage_only), None)
        if package is None:
            raise ValueError("Isolated evaluation requires a frozen stage input package.")
        source = safe_path(base, package.root)
        actual_files = {path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file()}
        if actual_files != {entry.path for entry in package.files}:
            raise ValueError("Stage package inventory differs from the frozen manifest.")
        for entry in package.files:
            if fingerprint(safe_path(source, entry.path)) != entry.sha256:
                raise ValueError("Stage package checksum mismatch.")
        shutil.copytree(source, output)
        state = RunState.restore(output)
        current = "03_pca" if state.workflow.in_pca_block() else state.workflow.stage
        if current != stage_only:
            raise ValueError("Stage package does not start at the requested stage.")
    else:
        state = RunState(case.study, dataset_root, output, case.seed)

    class Driver(ScriptedProvider):
        def complete(self, messages, tools, **kwargs):
            if stage_only:
                production = "03_univariate" if stage_only == "03_pca" else stage_only
                done = state.workflow.data.pca_reviewed if stage_only == "03_pca" else production in state.workflow.data.completed_reports
                if done:
                    return Reply({"role": "assistant", "content": "The isolated stage has finished. Subsequent stages were not executed."},
                                 {"total_tokens": 0}, "stop")
            if case.continuation_policy == "stop_on_critical" and state.workflow.data.completed_reports:
                evaluated = evaluate_saved_case(case, base, output)
                if any(metric.critical and metric.status == "failed" for stage in evaluated.stages
                       if stage.production_stage_id in state.workflow.data.completed_reports for metric in stage.metrics):
                    return Reply({"role": "assistant", "content": "An external critical validation failed. Dependent analysis is stopped."},
                                 {"total_tokens": 0}, "stop")
            reply = super().complete(messages, tools, **kwargs)
            if not reply.message.get('tool_calls') and not state.finished:
                reply.message['content'] = 'The scripted stimulus ended with an incomplete analysis. Inspect the saved tool errors and stage reports before proceeding.'
            for call in reply.message.get("tool_calls", []):
                if call["function"]["name"] == "finish_analysis":
                    arguments = json.loads(call["function"]["arguments"])
                    for finding in arguments.get("findings", []):
                        for claim in finding.get("claims", []):
                            if claim.get("value") == "@observed_comparison_aggregate":
                                if claim["metric"] not in {"significant_features", "features_tested"}:
                                    raise ValueError("The scripted aggregate placeholder requires an aggregate metric.")
                                claim["value"] = state.comparisons[claim["comparison_id"]][claim["metric"]]
                    call["function"]["arguments"] = json.dumps(arguments)
            return reply

    model = state.run_info.get("llm", {}).get("model", "offline-case-script") if stage_only else "offline-case-script"
    if stage_only and not model.startswith("offline"):
        raise ValueError("A scripted stage package must originate from a declared offline reference run.")
    state.run_info["evaluation_input"] = {"mode": "stage_isolated" if stage_only else "end_to_end",
                                        "case_id": case.case_id, "provider": "deterministic_script"}
    try:
        Agent(state, Driver([step.model_dump() for step in case.script]),
              LLMConfig(model=model, max_turns=200, max_tool_calls=500,
                        max_total_tokens=2000000, max_run_seconds=3600)).run(
                            case.request if not stage_only else "Execute the isolated requested stage from the frozen input package.",
                            resume=bool(stage_only), progress=lambda _: None)
    except (ValueError, TypeError, RuntimeError, OSError, KeyError) as error:
        return f"{type(error).__name__}: {error}"
    return None


def evaluate_case(case_path, output, *, run=None, stage_only=None):
    started = monotonic()
    case_path, output = case_path.resolve(), output.resolve()
    case = load_case(case_path)
    if run is not None and output.is_relative_to(run.resolve()):
        raise ValueError("Evaluation output must be outside the inspected run.")
    output.mkdir(parents=True, exist_ok=False)
    store = EvidenceStore(output)
    store.write_json("case_snapshot.json", case.model_dump())
    store.write_json("reference_manifest.json", case.references.model_dump())
    store.write_json("live_evaluation_manifest.json", {'execution_status':'not_run','case_id':case.case_id,
        'request':case.request,'study':case.study.model_dump(exclude={'data_path','metadata_path'}),
        'model':None,'provider':None,'maximum_spend':None,'attempts':None,
        'note':'Prepared only. References, expected answers and scripted decisions are excluded.'})
    store.write_json("evaluator.json", {"python": platform.python_version(),
        "packages": {name: importlib.metadata.version(name) for name in ['numpy','pandas','scipy','statsmodels','scikit-learn']},
        "source_hashes": {path.name: fingerprint(path) for path in Path(__file__).parent.glob('*.py')},
        "reference_answers_sent_to_provider": False, "api_calls": 0})
    execution_error = None
    if run is None:
        run = output / "scientific_run"
        execution_error = _scripted_run(case, case_path.parent, run, stage_only=stage_only)
    elif stage_only:
        raise ValueError("--stage launches a frozen input package; use --run for full saved-run evaluation.")
    report = evaluate_saved_case(case, case_path.parent, run.resolve(), stage_only=stage_only)
    if execution_error:
        report.status = "failed"
        report.summary['execution_error'] = execution_error
        store.write_json('execution_error.json', {'error': execution_error, 'analysis_status': report.analysis_status})
    report.summary["evaluation_elapsed_seconds"] = monotonic() - started
    store.write_json("evaluation.json", report.model_dump())
    store.atomic_write("evaluation.md", lambda path: path.write_text(render_evaluation(report)))
    for stage in report.stages:
        directory = f"stages/{stage.stage_id}/{stage.attempt_id or 'not_started'}"
        store.write_json(f"{directory}/evaluation.json", stage.model_dump())
        store.atomic_write(f"{directory}/evaluation.md", lambda path, item=stage: path.write_text(
            f"# {item.stage_id}\n\nEvaluation: **{item.status}**.\n\n" + "\n".join(
                f"- {metric.id}: **{metric.status}** — {metric.explanation}" for metric in item.metrics) + "\n"))
    return report
