import json

import httpx
import pytest
from openai import OpenAI

from metabolomics_agent.agent import Agent
from metabolomics_agent.cli import llm_config_from_env
from metabolomics_agent.llm import OpenAICompatibleProvider
from metabolomics_agent.models import LLMConfig


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
