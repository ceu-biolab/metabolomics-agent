"""Offline G evaluates execution records, never model reasoning or paid calls."""

import hashlib
import json
import shutil
import socket
from pathlib import Path

import pytest

from metabolomics_agent import cli, llm
from metabolomics_agent.agent import Agent
from metabolomics_agent.evaluation import evaluate_offline, save_audit
from metabolomics_agent.evaluation_cases import CASES, create_inputs
from metabolomics_agent.evidence import EvidenceStore
from metabolomics_agent.models import LLMConfig
from metabolomics_agent.run_audit import audit_run
from metabolomics_agent.state import RunState


def read(path):
    return json.loads(path.read_text())


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):
    output = tmp_path_factory.mktemp("evaluation")/"suite"
    def deny_network(*args, **kwargs):
        raise AssertionError("Offline evaluation attempted a network/live-provider call")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", deny_network)
        patch.setattr(llm.OpenAICompatibleProvider, "__init__", deny_network)
        result = evaluate_offline(output, progress=lambda _: None)
    return output, result


def test_all_cases_pass_without_network_or_live_grades(evaluated):
    output, result = evaluated
    assert result["status"] == "passed"
    assert result["cases_passed"] == len(CASES) == 10
    assert result["api_calls"] == 0 and result["live_model_evaluation"] == "not_run"
    assert result["llm_reasoning_quality"] == "not_assessed"
    assert result["numeric_values_checked"] > 5000
    for case in result["cases"]:
        audit = read(output/"cases"/case["name"]/"audit.json")
        assert audit["checks_failed"] == 0, [check for check in audit["checks"] if not check["passed"]]
        assert audit["live_llm_decisions"] == "not_assessed"
    manifest = read(output/"manifest.json")
    assert all(hashlib.sha256((output/path).read_bytes()).hexdigest() == digest for path, digest in manifest["files"].items())


def test_resume_and_guardrail_evidence_are_explicit(evaluated):
    output, result = evaluated
    assert all(value for value in result["checks"][0]["observed"].values())
    resumed = read(output/"cases/resume/audit.json")
    assert resumed["recovery"]["restored_without_sources"]
    assert resumed["trace"]["successful_tools"].count("preprocess") == 1
    assert resumed["trace"]["successful_tools"].count("compare_groups") == 1
    guarded = read(output/"cases/guardrails/audit.json")
    assert guarded["trace"]["rejected_calls"] == 2
    report = (output/"cases/guardrails/run/report.md").read_text()
    assert "AGENT_TOOL_CALL_REJECTED" in report


def test_partial_case_preserves_outputs_and_never_claims_completion(evaluated):
    output, result = evaluated
    case = next(item for item in result["cases"] if item["name"] == "unsafe_log")
    assert case["status"] == "passed" and case["audit_status"] == "incomplete"
    assert case["expected_incomplete"] and not case["analysis_finished"]
    run = output/"cases/unsafe_log/run"
    state = read(run/"state.json")
    assert not state["datasets"] and not state["comparisons"]
    assert not (run/"report.md").exists() and (run/"incomplete.md").exists()
    assert {"imputed", "imputation_mask"} <= {a.kind for a in EvidenceStore(run).artifacts.values()}


def test_audit_is_read_only_and_report_is_separate(evaluated, tmp_path):
    output, _ = evaluated
    run = output/"cases/independent/run"
    before = hashes(run)
    audited = save_audit(run, tmp_path/"audit")
    assert audited["status"] == "passed"
    assert hashes(run) == before
    assert (tmp_path/"audit/audit.md").exists()
    with pytest.raises(ValueError, match="outside"):
        save_audit(run, run/"nested_audit")
    with pytest.raises(FileExistsError):
        save_audit(run, tmp_path/"audit")


def test_audit_detects_corrupted_artifact_and_prompt_snapshot(evaluated, tmp_path):
    output, _ = evaluated
    run = tmp_path/"corrupted"
    shutil.copytree(output/"cases/independent/run", run)
    state = read(run/"state.json")
    artifact = state["artifacts"][state["datasets"]["d001"]["artifact_ids"]["concentrations"]]
    (run/artifact["path"]).write_text("corrupted")
    report = audit_run(run)
    assert report["status"] == "failed" and report["checks_failed"] > 0
    second = tmp_path/"prompt_corruption"
    shutil.copytree(output/"cases/independent/run", second)
    info = read(second/"run.json")
    info["workflow_prompts"]["03_univariate"] += "\nChanged frozen instructions."
    (second/"run.json").write_text(json.dumps(info))
    report = audit_run(second)
    assert any(not check["passed"] and "frozen_prompt" in check["id"] for check in report["checks"])
    third = tmp_path/"schema_corruption"
    shutil.copytree(output/"cases/independent/run", third)
    for path in sorted((third/"requests").glob("*.json")):
        record = read(path)
        if record["stage"] == "02_preparation":
            plot = next(tool for tool in record["tools"] if tool["function"]["name"] == "make_plot")
            plot["function"]["parameters"]["properties"]["kind"]["enum"].append("pca_scores")
            path.write_text(json.dumps(record))
            break
    report = audit_run(third)
    assert any(not check["passed"] and "tool_parameters" in check["id"] for check in report["checks"])


@pytest.mark.parametrize("change,failed_check", [
    ("cursor", "context_transition"),
    ("phase", "context_phase"),
    ("clarification", "user_requests"),
    ("missing_window", "context_window"),
    ("short_history", "context_messages"),
    ("strategy", "legacy_context_records"),
])
def test_audit_detects_corrupted_phase_trace(evaluated, tmp_path, change, failed_check):
    output, _ = evaluated
    run = tmp_path / change
    shutil.copytree(output / "cases/resume/run", run)
    files = sorted((run / "requests").glob("*.json"))
    target = (next(path for path in files if path != files[0] and read(path)["stage"] == "01_coordination")
              if change == "short_history" else
              next(path for path in files if read(path)["stage"] == "02_preparation"))
    record = read(target)
    if change == "cursor":
        record["context_window"]["start_index"] = 2
    elif change == "phase":
        record["context_window"]["phase"] = "wrong-phase"
    elif change == "missing_window":
        del record["context_window"]
    elif change == "short_history":
        record["messages"] = record["messages"][:2] + record["messages"][-1:]
    elif change == "clarification":
        info = read(run / "run.json")
        info["user_requests"][-1]["text"] = "altered clarification"
        (run / "run.json").write_text(json.dumps(info))
    elif change == "strategy":
        info = read(run / "run.json")
        info["context_strategy"] = "full_history"
        (run / "run.json").write_text(json.dumps(info))
    if change in {"cursor", "phase", "missing_window", "short_history"}:
        target.write_text(json.dumps(record))
    report = audit_run(run)
    assert report["status"] == "failed"
    assert any(not item["passed"] and failed_check in item["id"] for item in report["checks"])


def test_audit_reports_request_sizes_and_accepts_old_staged_trace(evaluated, tmp_path):
    output, _ = evaluated
    current = audit_run(output / "cases/independent/run")
    trace = current["trace"]
    assert trace["context_strategy"] == "phase_window_v1"
    assert len(trace["request_payload_bytes"]) == trace["requests"]
    assert trace["maximum_request_payload_bytes"] == max(trace["request_payload_bytes"])
    run = tmp_path / "old_staged"
    shutil.copytree(output / "cases/independent/run", run)
    info = read(run / "run.json")
    info.pop("context_strategy")
    info.pop("user_requests")
    (run / "run.json").write_text(json.dumps(info))
    state = read(run / "state.json")
    state.pop("context_window")
    (run / "state.json").write_text(json.dumps(state))
    for path in (run / "requests").glob("*.json"):
        record = read(path)
        record.pop("context_window")
        path.write_text(json.dumps(record))
    old = audit_run(run)
    assert old["status"] == "passed", [item for item in old["checks"] if not item["passed"]]
    assert old["trace"]["context_strategy"] == "full_history"


def test_numeric_oracle_detects_wrong_results_even_with_valid_hashes(evaluated, tmp_path):
    output, _ = evaluated
    run = tmp_path/"numerical_error"
    shutil.copytree(output/"cases/independent/run", run)
    state = read(run/"state.json")
    store = EvidenceStore(run)
    original = state["comparisons"]["c001"]["artifact_id"]
    table = store.read_table(original)
    table.loc[0, "adjusted_p_value"] = .987
    fabricated = store.table(table, "comparison", aid="a999", relative="a999_comparison.csv", parent_ids=[original])
    state["artifacts"][fabricated.id] = fabricated.model_dump()
    state["comparisons"]["c001"]["artifact_id"] = fabricated.id
    (run/"state.json").write_text(json.dumps(state))
    report = audit_run(run)
    assert next(check for check in report["checks"] if check["id"] == "evidence.integrity")["passed"]
    assert not next(check for check in report["checks"] if check["id"] == "comparison.c001.adjusted_p")["passed"]
    assert report["status"] == "failed"


def test_live_manifest_is_preparation_only_and_hides_scripts(evaluated):
    output, _ = evaluated
    live = read(output/"live_evaluation_manifest.json")
    assert live["execution_status"] == "not_run"
    assert live["model"] is None and live["max_total_tokens_per_case"] is None
    assert live["maximum_spend"] is None
    assert all(item["status"] == "not_assessed" and item["human_review_required"] for item in live["rubric"])
    for case in live["cases"]:
        assert (output/case["study"]).exists()
        assert "scripted_steps" not in case and "truth" not in case


def test_cli_offline_commands_do_not_load_environment_credentials(evaluated, tmp_path, monkeypatch, capsys):
    output, _ = evaluated
    def forbidden(*args, **kwargs):
        raise AssertionError("A credentials/live-provider path was invoked")
    monkeypatch.setattr(cli, "load_dotenv", forbidden)
    monkeypatch.setattr(cli, "OpenAICompatibleProvider", forbidden)
    monkeypatch.setenv("LLM_API_KEY", "must-not-be-read-or-sent")
    assert cli.main(["evaluate-offline", "--case", "descriptive", "--output", str(tmp_path/"cli")]) == 0
    assert cli.main(["audit-run", "--run", str(output/"cases/independent/run"), "--output", str(tmp_path/"audit")]) == 0
    text = capsys.readouterr().out
    assert "not_assessed" in text and "must-not-be-read-or-sent" not in text


def test_truncated_pending_batch_is_honest_incomplete_and_not_resume_safe(tmp_path):
    case = CASES[0]
    config, request = create_inputs(case, tmp_path/"input")
    state = RunState(config, tmp_path/"input", tmp_path/"run")
    class Truncated:
        def complete(self, *args, **kwargs):
            return llm.Reply({"role": "assistant", "content": None, "tool_calls": [{"id": "pending", "type": "function",
                "function": {"name": "set_workflow_plan", "arguments": "{}"}}]}, {"total_tokens": 0}, "length")
    Agent(state, Truncated(), LLMConfig(model="offline", max_total_tokens=500000)).run(request, progress=lambda _: None)
    report = audit_run(state.output_dir)
    assert report["status"] == "incomplete" and report["checks_failed"] == 0
    assert report["trace"]["pending_tool_responses"] == ["pending"]
    assert not report["trace"]["resume_batch_safe"]


def test_case_selection_and_existing_output_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        evaluate_offline(tmp_path/"unknown", cases=["unknown"])
    with pytest.raises(ValueError):
        evaluate_offline(tmp_path/"duplicate", cases=["paired", "paired"])
    with pytest.raises(FileExistsError):
        evaluate_offline(tmp_path)


def test_prompt_documentation_matches_installed_sources():
    root = Path(__file__).resolve().parents[1]
    for source in (root/"src/metabolomics_agent/prompts/stages").glob("*.md"):
        assert source.read_bytes() == (root/"docs/staged-workflow/prompts"/source.name).read_bytes()


@pytest.mark.parametrize("test,paired", [("student_t", False), ("mannwhitney", False), ("wilcoxon", True)])
def test_audit_supports_other_registered_test_families(tmp_path, test, paired):
    from metabolomics_agent.evaluation_cases import Case, ScriptedProvider, script
    case = Case("alternative", "Numerical audit of another registered test family.", design="paired" if paired else "independent")
    config, request = create_inputs(case, tmp_path/"input")
    steps = script(case)
    planned = next(step for step in steps if step["tool"] == "set_univariate_plan")
    planned["arguments"]["test"] = test
    planned["arguments"]["rationale"] = "Use a prespecified supported comparison with the correct pairing and recorded multiplicity correction."
    state = RunState(config, tmp_path/"input", tmp_path/"run")
    Agent(state, ScriptedProvider(steps), LLMConfig(model="offline-audit", max_turns=64, max_total_tokens=500000)).run(
        request, progress=lambda _: None)
    report = audit_run(state.output_dir)
    assert report["status"] == "passed", [check for check in report["checks"] if not check["passed"]]
