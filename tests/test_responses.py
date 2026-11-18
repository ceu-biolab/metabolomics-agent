import json

import httpx
import pytest
from openai import OpenAI

from metabolomics_agent.agent import Agent
from metabolomics_agent.cli import llm_config_from_env
from metabolomics_agent.examples import generate_example
from metabolomics_agent.llm import DemoProvider, OpenAICompatibleProvider
from metabolomics_agent.models import LLMConfig, StudyConfig
from metabolomics_agent.state import RunState


def response_payload(output, *, status="completed", incomplete=None):
    return {
        "id": "resp_test", "object": "response", "created_at": 1,
        "model": "openai/gpt-6-luna", "status": status, "output": output,
        "error": None, "incomplete_details": incomplete,
        "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 60}},
    }


REASONING = {"id": "rs_test", "type": "reasoning", "summary": [],
             "encrypted_content": "mock-encrypted-reasoning"}
CALL = {"id": "fc_test", "type": "function_call", "call_id": "call_test",
        "name": "inspect_dataset", "arguments": "{}", "status": "completed"}
TEXT = {"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": "Additional study information is needed.",
                     "annotations": []}]}


def provider_with_transport(config, handler):
    provider = OpenAICompatibleProvider(config, "mock-key")
    provider.client.close()
    provider.client = OpenAI(api_key="mock-key", base_url=config.base_url, max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return provider


def test_responses_preserves_reasoning_and_tool_linkage(state):
    requests = []

    def handle(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        requests.append(body)
        assert body["reasoning"] == {"effort": "low"}
        assert body["store"] is False
        assert body["include"] == ["reasoning.encrypted_content"]
        assert body["max_output_tokens"] <= 2500
        assert body["tools"][0]["type"] == "function"
        assert body["tools"][0]["strict"] is False
        assert "function" not in body["tools"][0]
        if len(requests) == 1:
            output = [REASONING, CALL]
        else:
            assert REASONING in body["input"]
            assert CALL in body["input"]
            tool = body["input"][-1]
            assert tool["type"] == "function_call_output"
            assert tool["call_id"] == "call_test"
            assert json.loads(tool["output"])["ok"]
            assert all("_responses_output" not in item for item in body["input"])
            output = [TEXT]
        return httpx.Response(200, json=response_payload(output))

    config = LLMConfig(model="openai/gpt-6-luna", base_url="https://local.test/v1",
                       api="responses", reasoning_effort="low", max_total_tokens=500000)
    provider = provider_with_transport(config, handle)
    try:
        result = Agent(state, provider, config).run("Analyze", progress=lambda _: None)
    finally:
        provider.client.close()
    assert result["status"] == "needs_input"
    assert result["total_tokens"] == 2200
    assert state.inspected
    assert state.run_info["llm"]["reasoning_effort"] == "low"
    assert len(requests) == 2
    conversation = json.loads((state.output_dir / "conversation.json").read_text())
    assert conversation[2]["_responses_output"][0] == REASONING


def test_responses_truncation_does_not_execute_partial_tools(state):
    def handle(request):
        return httpx.Response(200, json=response_payload([REASONING, CALL], status="incomplete",
                              incomplete={"reason": "max_output_tokens"}))

    config = LLMConfig(model="openai/gpt-6-luna", base_url="https://local.test/v1",
                       api="responses", reasoning_effort="low")
    provider = provider_with_transport(config, handle)
    try:
        result = Agent(state, provider, config).run("Analyze", progress=lambda _: None)
    finally:
        provider.client.close()
    assert result["status"] == "model_output_incomplete"
    assert not state.inspected


def test_explicit_reasoning_environment_configuration(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-6-luna")
    monkeypatch.setenv("LLM_API", "responses")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium")
    monkeypatch.setenv("LLM_EXTRA_JSON", "{}")
    config = llm_config_from_env()
    assert config.api == "responses"
    assert config.reasoning_effort == "medium"
    assert "reasoning_effort" not in config.extra
    monkeypatch.setenv("LLM_API", "chat_completions")
    with pytest.raises(ValueError, match="LLM_API=responses"):
        llm_config_from_env()


def test_reasoning_settings_do_not_silently_override_each_other():
    with pytest.raises(ValueError, match="conflicts"):
        LLMConfig(model="test", reasoning_effort="low", extra={"reasoning_effort": "high"})
    config = LLMConfig(model="test", extra={"reasoning_effort": "high"})
    assert config.reasoning_effort == "high"
    assert config.extra["reasoning_effort"] == "high"
    with pytest.raises(ValueError, match="minimal"):
        LLMConfig(model="openai/gpt-6-luna", api="responses", reasoning_effort="minimal")


def test_resume_cannot_switch_api_format(state):
    state.run_info["llm"] = LLMConfig(model="test").model_dump()
    state.write_json("conversation.json", [{"role": "user", "content": "Analyze"}])
    from test_agent import SequenceProvider

    with pytest.raises(ValueError, match="original API format"):
        Agent(state, SequenceProvider([]), LLMConfig(model="test", api="responses")).run("", resume=True)


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
def test_transport_serializes_current_phase_window_only(tmp_path, api):
    study = generate_example(tmp_path / "input")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    result = Agent(state, DemoProvider(), LLMConfig(
        model="offline", max_turns=3, max_total_tokens=500000,
    )).run(config.objective, progress=lambda _: None)
    assert result["status"] == "turn_limit"
    saved = [json.loads(path.read_text()) for path in sorted((state.output_dir / "requests").glob("*.json"))]
    assert len(saved) == 3
    assert saved[0]["stage"] == saved[1]["stage"] == "01_coordination"
    assert saved[2]["stage"] == "02_preparation"
    assert saved[0]["context_window"]["epoch"] < saved[2]["context_window"]["epoch"]

    sent = []
    def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        if api == "responses":
            assert request.url.path == "/v1/responses"
            return httpx.Response(200, json=response_payload([TEXT]))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={
            "id": "chat_test", "object": "chat.completion", "created": 1,
            "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Done."}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    live = LLMConfig(model="test", base_url="https://local.test/v1", api=api,
                     max_total_tokens=500000)
    provider = provider_with_transport(live, handle)
    try:
        for record in saved[1:]:
            messages = json.loads(json.dumps(record["messages"]))
            if api == "responses":
                for message in messages:
                    if message.get("role") == "assistant" and message.get("tool_calls"):
                        message["_responses_output"] = [{
                            "id": "fc_test", "type": "function_call",
                            "call_id": call["id"], "name": call["function"]["name"],
                            "arguments": call["function"]["arguments"], "status": "completed",
                        } for call in message["tool_calls"]]
            provider.complete(messages, record["tools"], max_tokens=100, timeout=5)
    finally:
        provider.client.close()
    assert len(sent) == 2
    first = sent[0]["input" if api == "responses" else "messages"]
    next_phase = sent[1]["input" if api == "responses" else "messages"]
    assert any(item.get("tool_call_id") or item.get("type") == "function_call_output"
               for item in first)
    assert not any(item.get("tool_call_id") or item.get("type") == "function_call_output"
                   for item in next_phase)
    assert "set_workflow_plan" not in json.dumps(next_phase)
