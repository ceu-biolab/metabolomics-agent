"""Phase requests are smaller views of a complete, resumable local trace."""

import json

import pytest

from metabolomics_agent.agent import Agent
from metabolomics_agent.evidence_models import StageIssue
from metabolomics_agent.examples import generate_example
from metabolomics_agent.llm import DemoProvider
from metabolomics_agent.models import LLMConfig, StudyConfig
from metabolomics_agent.state import RunState


def requests(root):
    return [json.loads(path.read_text()) for path in sorted((root / "requests").glob("*.json"))]


def test_stage_requests_drop_prior_batches_without_orphaning_tools(tmp_path):
    study = generate_example(tmp_path / "input")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    result = Agent(state, DemoProvider(), LLMConfig(model="offline", max_total_tokens=500000)).run(
        config.objective, progress=lambda _: None)
    assert result["status"] == "complete"

    saved = requests(state.output_dir)
    assert len(saved) == result["model_requests"]
    assert len({record["context_window"]["epoch"] for record in saved}) == 5
    for record in saved:
        messages = record["messages"]
        context = json.loads(messages[-1]["content"])["stage_context"]
        assert context["stage"] == record["stage"]
        assert "incoming_handoff" not in context
        assert "study" not in context
        assert messages[1]["role"] == "user"
        calls = {call["id"] for message in messages if message["role"] == "assistant"
                 for call in message.get("tool_calls", [])}
        assert all(message["tool_call_id"] in calls for message in messages
                   if message["role"] == "tool")
    preparation = next(record for record in saved if record["stage"] == "02_preparation")
    assert not any(call["function"]["name"] == "set_workflow_plan"
                   for message in preparation["messages"] if message["role"] == "assistant"
                   for call in message.get("tool_calls", []))
    assert "set_workflow_plan" in (state.output_dir / "conversation.json").read_text()


def test_resume_keeps_clarification_across_stage_windows(tmp_path):
    study = generate_example(tmp_path / "input")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    provider = DemoProvider()
    base = LLMConfig(model="offline", max_total_tokens=500000)
    assert Agent(state, provider, base.model_copy(update={"max_turns": 2})).run(
        config.objective, progress=lambda _: None)["status"] == "turn_limit"
    clarification = "Keep the declared groups and do not normalize again."
    state = RunState.restore(state.output_dir)
    assert Agent(state, provider, base.model_copy(update={"max_turns": 7})).run(
        clarification, resume=True, progress=lambda _: None)["status"] == "turn_limit"
    assert state.workflow.stage == "03_univariate"
    state = RunState.restore(state.output_dir)
    assert Agent(state, provider, base.model_copy(update={"max_turns": 1})).run(
        "", resume=True, progress=lambda _: None)["status"] == "turn_limit"
    last = requests(state.output_dir)[-1]
    assert last["stage"] == "03_univariate"
    assert any(message.get("content") == clarification for message in last["messages"])
    assert last["context_window"]["start_index"] > state.run_info["user_requests"][1]["message_index"]
    assert len(state.run_info["user_requests"]) == 2
    assert sum(message["role"] == "user" and message.get("content") == clarification
               for message in last["messages"]) == 1


def test_pca_and_univariate_have_separate_windows(tmp_path):
    study = generate_example(tmp_path / "input")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    result = Agent(state, DemoProvider(pca=True), LLMConfig(
        model="offline", max_turns=48, max_tool_calls=64, max_total_tokens=500000,
    )).run(config.objective, progress=lambda _: None)
    assert result["status"] == "complete"
    statistical = [record for record in requests(state.output_dir)
                   if record["stage"] == "03_univariate"]
    pca = [record for record in statistical if json.loads(
        record["messages"][-1]["content"])["stage_context"]["analysis_block"] == "pca"]
    univariate = [record for record in statistical if json.loads(
        record["messages"][-1]["content"])["stage_context"]["analysis_block"] == "univariate"]
    assert pca and univariate
    assert pca[0]["context_window"]["epoch"] != univariate[0]["context_window"]["epoch"]
    assert not any(call["function"]["name"] == "run_pca"
                   for message in univariate[0]["messages"] if message["role"] == "assistant"
                   for call in message.get("tool_calls", []))


def test_resume_rejects_window_inside_tool_batch(tmp_path):
    study = generate_example(tmp_path / "input")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    provider = DemoProvider()
    settings = LLMConfig(model="offline", max_turns=1, max_total_tokens=500000)
    assert Agent(state, provider, settings).run(config.objective, progress=lambda _: None)[
        "status"] == "turn_limit"
    state.context_window["start_index"] = 3  # between the assistant call and its tool result
    state.snapshot()
    restored = RunState.restore(state.output_dir)
    with pytest.raises(ValueError, match="inside a tool-call batch"):
        Agent(restored, provider, settings).run("", resume=True, progress=lambda _: None)


def test_old_staged_run_resumes_with_full_history(tmp_path):
    study = generate_example(tmp_path / "input")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    provider = DemoProvider()
    settings = LLMConfig(model="offline", max_turns=2, max_total_tokens=500000)
    assert Agent(state, provider, settings).run(config.objective, progress=lambda _: None)[
        "status"] == "turn_limit"
    info = json.loads((state.output_dir / "run.json").read_text())
    info.pop("context_strategy")
    info.pop("user_requests")
    (state.output_dir / "run.json").write_text(json.dumps(info))
    saved = json.loads((state.output_dir / "state.json").read_text())
    saved.pop("context_window")
    (state.output_dir / "state.json").write_text(json.dumps(saved))
    restored = RunState.restore(state.output_dir)
    assert Agent(restored, provider, settings.model_copy(update={"max_turns": 1})).run(
        "", resume=True, progress=lambda _: None)["status"] == "turn_limit"
    final_request = requests(state.output_dir)[-1]
    assert "context_window" not in final_request
    assert any(call["function"]["name"] == "set_workflow_plan"
               for message in final_request["messages"] if message["role"] == "assistant"
               for call in message.get("tool_calls", []))


def test_compact_context_keeps_blocking_issue_after_many_warnings(tmp_path):
    study = generate_example(tmp_path / "input")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    state.workflow.activate()
    state.evidence.active.issues.extend(StageIssue(
        code=f"WARNING_{index}", severity="warning", source="computed_check",
        message=f"Warning {index}",
    ) for index in range(25))
    state.evidence.active.issues.append(StageIssue(
        code="LATE_BLOCKER", severity="error", source="computed_check",
        message="The current input cannot be used.", blocking=True,
    ))
    context = state.workflow.model_context()
    assert context["issue_count"] == 26
    assert len(context["issues"]) == 20
    assert context["issues"][0]["code"] == "LATE_BLOCKER"
    state.workflow.interrupt("End of compact-context verification.")
