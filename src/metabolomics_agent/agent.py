import json
import time
from importlib.resources import files

from .llm import Provider
from .models import LLMConfig
from .state import RunState, dumps
from .tools import ToolRegistry


class Agent:
    def __init__(self, state: RunState, provider: Provider, config: LLMConfig):
        self.state, self.provider, self.config = state, provider, config
        self.registry = ToolRegistry(state)
        self.messages: list[dict] = []

    def run(self, request: str, *, progress=print, resume=False):
        state, config = self.state, self.config
        system = files("metabolomics_agent").joinpath("prompts/system.md").read_text()
        if resume:
            self.messages = json.loads((state.output_dir / "conversation.json").read_text())
            pending = set()
            for message in self.messages:
                if message["role"] == "assistant":
                    pending.update(c["id"] for c in message.get("tool_calls", []))
                elif message["role"] == "tool":
                    pending.discard(message["tool_call_id"])
            if pending:
                raise ValueError("Cannot resume an incomplete tool-call batch; inspect the saved trace.")
            previous = state.run_info.get("llm", {})
            if any(previous.get(k) != getattr(config, k) for k in ("model", "base_url")):
                raise ValueError("Resume must use the original model and endpoint.")
            if previous.get("api", "chat_completions") != config.api:
                raise ValueError("Resume must use the original API format; start a new run to switch APIs.")
            if request:
                self.messages.append({"role": "user", "content": request})
            state.run_info.setdefault("sessions", []).append({
                "previous_status": state.status, "previous_usage": state.run_info.get("usage", {}),
                "previous_llm": previous,
            })
            state.event("run_resumed", request=request)
        else:
            self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": dumps({
                "request": request, "study": state.config.model_dump(exclude={"data_path", "metadata_path"}),
                "seed": state.seed,
            })},
            ]
        state.run_info["llm"] = config.model_dump()
        state.run_info["system_prompt"] = system
        if not resume:
            state.run_info["request"] = request
            state.write_json("model_input.json", {
                "messages": self.messages, "tools": self.registry.schemas(),
            })
        state.write_json("run.json", state.run_info)
        state.status = "running"
        state.snapshot()
        prior_usage = state.run_info.get("usage", {}) if resume else {}
        total_tokens = prior_usage.get("total_tokens", 0)
        tool_count = 0
        previous_tool_count = prior_usage.get("tool_calls", 0)
        previous_model_turns = prior_usage.get("model_turns", 0)
        # Anchor later input estimates to actual provider usage instead of repeatedly
        # treating the entire growing context as one token per UTF-8 byte.
        anchor_tokens = anchor_count = None
        if resume:
            events = [json.loads(line) for line in (state.output_dir / "events.jsonl").read_text().splitlines()]
            if not previous_model_turns:
                previous_model_turns = sum(e["kind"] == "model_response" for e in events)
            for event in reversed(events):
                tokens = event.get("usage", {}).get("prompt_tokens")
                if event["kind"] == "model_response" and isinstance(tokens, int) and tokens > 0:
                    for index in range(len(self.messages) - 1, -1, -1):
                        if self.messages[index] == event["message"]:
                            anchor_tokens, anchor_count = tokens, index
                            break
                    break
        started = time.monotonic()
        turns_used = 0
        try:
            for turn in range(1, config.max_turns + 1):
                remaining_seconds = config.max_run_seconds - (time.monotonic() - started)
                if remaining_seconds <= 0:
                    state.status = "time_limit"
                    break
                # UTF-8 bytes plus protocol overhead is a deliberately conservative
                # input-token estimate. Provider-reported usage replaces it after a call.
                payload_bytes = len(json.dumps({"messages": self.messages,
                                               "tools": self.registry.schemas()}, ensure_ascii=False).encode())
                input_bound = payload_bytes + 64 * (len(self.messages) + len(self.registry.schemas()))
                if anchor_tokens is not None:
                    added = self.messages[anchor_count:]
                    input_bound = anchor_tokens + len(json.dumps(added, ensure_ascii=False).encode()) + 64 * (len(added) + 2)
                remaining = config.max_total_tokens - total_tokens
                if input_bound + 128 > remaining:
                    state.status = "token_limit"
                    break
                completion_limit = min(config.max_completion_tokens, remaining - input_bound)
                reply = self.provider.complete(self.messages, self.registry.schemas(),
                                               max_tokens=completion_limit,
                                               timeout=min(config.timeout_seconds, remaining_seconds))
                turns_used += 1
                prompt_tokens = reply.usage.get("prompt_tokens")
                if isinstance(prompt_tokens, int) and prompt_tokens > 0:
                    anchor_tokens, anchor_count = prompt_tokens, len(self.messages)
                usage = reply.usage.get("total_tokens")
                if usage is None:
                    usage = input_bound + completion_limit
                total_tokens += int(usage)
                state.event("model_response", turn=previous_model_turns + turn, message=reply.message,
                            usage=reply.usage, budget_tokens_charged=int(usage),
                            finish_reason=reply.finish_reason)
                self.messages.append(reply.message)
                state.write_json("conversation.json", self.messages)
                if reply.finish_reason in {"length", "content_filter"}:
                    state.status = "model_output_incomplete"
                    break
                calls = reply.message.get("tool_calls") or []
                if not calls:
                    state.status = "needs_input" if reply.message.get("content") else "incomplete"
                    break
                if (len(calls) + tool_count > config.max_tool_calls
                        or total_tokens > config.max_total_tokens
                        or time.monotonic() - started >= config.max_run_seconds):
                    state.status = ("tool_limit" if len(calls) + tool_count > config.max_tool_calls
                                    else "token_limit" if total_tokens > config.max_total_tokens else "time_limit")
                    break
                ids = [c.get("id") for c in calls]
                if not all(isinstance(i, str) and i for i in ids) or len(ids) != len(set(ids)):
                    raise ValueError("Provider returned invalid/duplicate tool call IDs.")
                for index, call in enumerate(calls):
                    tool_count += 1
                    function = call.get("function", {})
                    name, arguments = function.get("name", ""), function.get("arguments", "")
                    progress(f"[{turn:02d}] {name}")
                    if time.monotonic() - started >= config.max_run_seconds:
                        result = {"ok": False, "error": "Run time limit reached before tool execution."}
                        state.status = "time_limit"
                    elif name == "finish_analysis" and index != len(calls) - 1:
                        result = {"ok": False, "error": "finish_analysis must be the last call in a batch."}
                        state.event("tool_error", tool=name, error=result["error"])
                    else:
                        result = self.registry.execute(name, arguments)
                    self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": dumps(result)})
                    state.write_json("conversation.json", self.messages)
                if state.finished or state.status == "time_limit":
                    break
            else:
                state.status = "turn_limit"
        except Exception as error:
            state.status = "failed"
            state.event("run_error", error_type=type(error).__name__)
            raise
        finally:
            state.run_info["usage"] = {"total_tokens": total_tokens, "tool_calls": tool_count,
                                       "model_turns": previous_model_turns + turns_used,
                                       "elapsed_seconds": prior_usage.get("elapsed_seconds", 0) + time.monotonic() - started}
            state.run_info["usage"]["tool_calls"] += previous_tool_count
            state.write_json("run.json", state.run_info)
            state.write_json("conversation.json", self.messages)
            state.snapshot()
            if not state.finished:
                content = self.messages[-1].get("content") if self.messages else None
                (state.output_dir / "incomplete.md").write_text(
                    f"# Analysis did not finish\n\nStatus: {state.status}.\n\n"
                    f"{content if self.messages[-1]['role'] == 'assistant' and content else ''}\n\n"
                    "Inspect state.json and events.jsonl before starting a new run. "
                    "Partial numerical outputs are preserved.\n"
                )
        return {"status": state.status, "output_dir": str(state.output_dir),
                "total_tokens": total_tokens,
                "tool_calls": state.run_info["usage"]["tool_calls"],
                "session_tool_calls": tool_count,
                "model_requests": state.run_info["usage"]["model_turns"]}
