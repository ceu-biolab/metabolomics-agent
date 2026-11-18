import json

import httpx
import pytest
from openai import OpenAI

from metabolomics_agent.agent import Agent
from metabolomics_agent.examples import generate_example
from metabolomics_agent.llm import DemoProvider, OpenAICompatibleProvider, Reply
from metabolomics_agent.models import FinishArgs, LLMConfig, StudyConfig
from metabolomics_agent.report import finish_analysis
from metabolomics_agent.state import RunState


class SequenceProvider:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        return next(self.replies)


def tool_reply(name="inspect_dataset", arguments="{}", call_id="call_1", usage=10):
    return Reply({"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": name, "arguments": arguments},
    }]}, {"total_tokens": usage}, "tool_calls")


def test_offline_end_to_end_demo(tmp_path):
    study_path = generate_example(tmp_path / "synthetic")
    cfg = StudyConfig.model_validate_json(study_path.read_text())
    state = RunState(cfg, study_path.parent, tmp_path / "run", protocol="legacy")
    result = Agent(state, DemoProvider(), LLMConfig(model="offline", max_total_tokens=500000)).run(
        cfg.objective, progress=lambda _: None)
    assert result["status"] == "complete"
    assert result["total_tokens"] == 0
    assert state.plans["p002"].purpose == "exploratory"
    assert state.comparisons["c001"]["features_tested"] == 12
    assert (state.output_dir / "report.md").exists()
    assert len([a for a in state.artifacts.values() if a["path"].endswith(".png")]) == 5
    assert not (state.output_dir / "incomplete.md").exists()
    trace = json.loads((state.output_dir / "conversation.json").read_text())
    initial = json.loads((state.output_dir / "model_input.json").read_text())
    assert initial["messages"] == trace[:2]
    assert "data_path" not in json.loads(initial["messages"][1]["content"])["study"]
    assert initial["tools"]
    report = (state.output_dir / "report.md").read_text()
    assert "## Prompt and inputs received" in report
    assert f"```text\n{cfg.objective}\n```" in report
    assert "a001_input_values.csv" in report and "a002_input_metadata.csv" in report
    assert "model_input.json" in report
    pending = []
    for message in trace:
        if message["role"] == "assistant":
            pending.extend(c["id"] for c in message.get("tool_calls", []))
        if message["role"] == "tool":
            pending.remove(message["tool_call_id"])
    assert not pending
    with pytest.raises(ValueError, match="Unknown evidence IDs"):
        finish_analysis(state, FinishArgs(findings=[{
            "text": "This fabricated claim has no saved evidence.", "evidence_ids": ["missing-id"],
        }], limitations=["Test of evidence validation."]))


def test_invalid_call_is_observed_and_recoverable(state):
    provider = SequenceProvider([
        tool_reply("preprocess", '{"plan_id": "missing"}'),
        tool_reply(call_id="call_2"),
        Reply({"role": "assistant", "content": "Please clarify the missing study information."}),
    ])
    agent = Agent(state, provider, LLMConfig(model="test", max_total_tokens=500000))
    result = agent.run("Analyze", progress=lambda _: None)
    assert result["status"] == "needs_input"
    assert state.inspected
    responses = [json.loads(m["content"]) for m in agent.messages if m["role"] == "tool"]
    assert not responses[0]["ok"] and responses[1]["ok"]
    assert "Please clarify" in (state.output_dir / "incomplete.md").read_text()


@pytest.mark.parametrize("budget,status", [(1, "turn_limit"), (2, "tool_limit")])
def test_execution_limits(state, budget, status):
    if status == "turn_limit":
        provider = SequenceProvider([tool_reply()])
        cfg = LLMConfig(model="test", max_turns=budget, max_total_tokens=500000)
    else:
        message = tool_reply().message
        message["tool_calls"].append(tool_reply(call_id="call_2").message["tool_calls"][0])
        provider = SequenceProvider([Reply(message)])
        cfg = LLMConfig(model="test", max_tool_calls=1, max_total_tokens=500000)
    result = Agent(state, provider, cfg).run("Analyze", progress=lambda _: None)
    assert result["status"] == status
    assert not state.finished


def test_token_limit_before_any_request(state):
    provider = SequenceProvider([])
    result = Agent(state, provider, LLMConfig(model="test", max_total_tokens=1000)).run(
        "Analyze", progress=lambda _: None)
    assert result["status"] == "token_limit"
    assert provider.calls == 0


def test_provider_failure_saves_partial_state(state):
    class BrokenProvider:
        def complete(self, *args, **kwargs):
            raise RuntimeError("A test transport failed")

    with pytest.raises(RuntimeError):
        Agent(state, BrokenProvider(), LLMConfig(model="test")).run("Analyze")
    assert state.status == "failed"
    assert (state.output_dir / "conversation.json").exists()
    assert (state.output_dir / "incomplete.md").exists()


def test_real_sdk_contract_with_mock_http_and_no_key_in_trace(state):
    requests = []
    test_key = "local-test-credential-never-sent-to-network"

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == f"Bearer {test_key}"
        if len(requests) == 1:
            assert "max_completion_tokens" in body and body["tools"]
            message = tool_reply().message
        else:
            assert body["messages"][-1]["role"] == "tool"
            assert body["messages"][-1]["tool_call_id"] == "call_1"
            message = {"role": "assistant", "content": "Please provide more study metadata."}
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "test",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        })

    cfg = LLMConfig(model="test", base_url="https://local.test/v1", max_total_tokens=500000)
    provider = OpenAICompatibleProvider(cfg, test_key)
    provider.client.close()
    provider.client = OpenAI(api_key=test_key, base_url=cfg.base_url, max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    try:
        result = Agent(state, provider, cfg).run("Analyze", progress=lambda _: None)
    finally:
        provider.client.close()
    assert result["status"] == "needs_input"
    assert result["total_tokens"] == 60
    for filename in ["events.jsonl", "conversation.json", "run.json"]:
        assert test_key not in (state.output_dir / filename).read_text()


def test_invalid_configuration_cannot_override_request_limits():
    with pytest.raises(ValueError):
        LLMConfig(model="test", extra={"max_tokens": 999999})
    with pytest.raises(ValueError):
        LLMConfig(model="test", base_url="https://secret:password@example.com")


def test_luna_tool_calling_compatibility_is_configured_without_a_paid_probe():
    cfg = LLMConfig(model="openai/gpt-6-luna")
    assert cfg.extra["reasoning_effort"] == "none"
    with pytest.raises(ValueError, match="reasoning_effort=none"):
        LLMConfig(model="openai/gpt-6-luna", extra={"reasoning_effort": "high"})


def test_truncated_response_does_not_execute_tools(state):
    reply = tool_reply()
    reply.finish_reason = "length"
    result = Agent(state, SequenceProvider([reply]), LLMConfig(model="test")).run(
        "Analyze", progress=lambda _: None)
    assert result["status"] == "model_output_incomplete"
    assert not state.inspected


def test_resume_restores_snapshots_and_finishes_without_repeating_calls(state):
    initial = [
        tool_reply(call_id="c1"),
        tool_reply("set_plan", json.dumps({"rationale": "Prespecified independent comparison without missing values."}), "c2"),
        tool_reply("preprocess", '{"plan_id":"p001"}', "c3"),
        tool_reply("compare_groups", '{"dataset_id":"d001"}', "c4"),
    ]
    cfg = LLMConfig(model="test", max_turns=4, max_total_tokens=500000)
    Agent(state, SequenceProvider(initial), cfg).run("Analyze", progress=lambda _: None)
    assert state.status == "turn_limit"
    # Sources may change or disappear; resume uses frozen numerical snapshots.
    source = state.output_dir.parent / "values.csv"
    source.unlink()
    restored = RunState.restore(state.output_dir)
    import numpy as np

    np.testing.assert_allclose(restored.datasets["d001"].testing, state.datasets["d001"].testing)
    following = [
        tool_reply("run_pca", '{"dataset_id":"d001"}', "c5"),
        tool_reply("make_plot", '{"kind":"distributions","source_id":"d001"}', "c6"),
        tool_reply("make_plot", '{"kind":"volcano","source_id":"c001"}', "c7"),
        tool_reply("make_plot", '{"kind":"pca","source_id":"pc001"}', "c8"),
        tool_reply("finish_analysis", json.dumps({
            "findings": [{"text": "The comparison and PCA are available.", "evidence_ids": ["c001", "pc001"]}],
            "limitations": ["Synthetic data; exploratory interpretation."],
        }), "c9"),
    ]
    provider = SequenceProvider(following)
    cfg = cfg.model_copy(update={"max_turns": 5})
    result = Agent(restored, provider, cfg).run("Finish the saved analysis.", resume=True, progress=lambda _: None)
    assert result["status"] == "complete"
    assert provider.calls == 5
    assert restored.run_info["usage"]["total_tokens"] == 90
    assert restored.run_info["usage"]["model_turns"] == 9
    assert restored.run_info["usage"]["tool_calls"] == 9
    assert result["tool_calls"] == 9
    assert result["session_tool_calls"] == 5
    assert result["model_requests"] == 9
    assert len(restored.comparisons) == 1
    assert not (state.output_dir / "incomplete.md").exists()
    with pytest.raises(ValueError, match="complete"):
        RunState.restore(state.output_dir)


def test_resume_rejects_unresolved_tool_batches(state):
    reply = tool_reply()
    reply.finish_reason = "length"
    Agent(state, SequenceProvider([reply]), LLMConfig(model="test")).run("Analyze", progress=lambda _: None)
    restored = RunState.restore(state.output_dir)
    with pytest.raises(ValueError, match="incomplete tool-call batch"):
        Agent(restored, SequenceProvider([]), LLMConfig(model="test")).run("", resume=True)


def test_input_budget_anchors_to_observed_provider_usage(state):
    # First request fits a byte bound, but a second full byte bound would not.
    first = tool_reply()
    first.usage = {"prompt_tokens": 2000, "total_tokens": 2010}
    second = Reply({"role": "assistant", "content": "Need additional metadata."},
                   {"prompt_tokens": 2300, "total_tokens": 2310})
    provider = SequenceProvider([first, second])
    result = Agent(state, provider, LLMConfig(model="test", max_total_tokens=12500)).run(
        "Analyze", progress=lambda _: None)
    assert provider.calls == 2
    assert result["total_tokens"] == 4320
    assert result["status"] == "needs_input"
