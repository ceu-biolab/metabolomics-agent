"""The only provider-specific module. Credentials are never part of run state."""

import json
from dataclasses import dataclass, field
from typing import Protocol

from .models import LLMConfig


@dataclass
class Reply:
    message: dict
    usage: dict = field(default_factory=dict)
    finish_reason: str = "stop"


class Provider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], *, max_tokens: int,
                 timeout: float) -> Reply: ...


class ProviderError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    def __init__(self, config: LLMConfig, api_key: str):
        from openai import OpenAI

        if not api_key.strip():
            raise ValueError("Set LLM_API_KEY in .env or the environment before a live run.")
        self.config = config
        self.client = OpenAI(api_key=api_key, base_url=config.base_url,
                             timeout=config.timeout_seconds, max_retries=0)

    def complete(self, messages, tools, *, max_tokens, timeout):
        if self.config.api == "responses":
            return self._complete_responses(messages, tools, max_tokens=max_tokens, timeout=timeout)
        from openai import APIError

        kwargs = {"model": self.config.model, "messages": messages, "tools": tools,
                  "tool_choice": "auto", self.config.token_parameter: max_tokens,
                  "timeout": timeout, **self.config.extra}
        try:
            completion = self.client.chat.completions.create(**kwargs)
        except APIError as error:
            # Do not persist provider error bodies/headers: they can echo credentials/data.
            status = getattr(error, "status_code", None)
            raise ProviderError(f"LLM request failed ({type(error).__name__}, HTTP {status}). "
                                "Check endpoint, key, model, quota, and token parameter. "
                                "No automatic paid retries were made.") from None
        if not completion.choices:
            raise ProviderError("Provider returned no completion choices.")
        choice = completion.choices[0]
        msg = choice.message
        message = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            if any(call.type != "function" for call in msg.tool_calls):
                raise ProviderError("Provider returned unsupported non-function tool calls.")
            message["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                "name": call.function.name, "arguments": call.function.arguments,
            }} for call in msg.tool_calls]
        return Reply(message, completion.usage.model_dump() if completion.usage else {},
                     choice.finish_reason or "stop")

    def _complete_responses(self, messages, tools, *, max_tokens, timeout):
        from openai import APIError

        # Stateless replay includes all native output items, especially reasoning
        # items and their encrypted content. Tool results retain their call IDs.
        inputs = []
        for msg in messages:
            if msg["role"] == "assistant" and "_responses_output" in msg:
                inputs.extend(msg["_responses_output"])
            elif msg["role"] == "tool":
                inputs.append({"type": "function_call_output", "call_id": msg["tool_call_id"],
                               "output": msg["content"]})
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                raise ProviderError("Cannot replay Chat Completions tool history through Responses.")
            else:
                inputs.append({"role": msg["role"], "content": msg.get("content") or ""})
        functions = [{"type": "function", **tool["function"], "strict": False} for tool in tools]
        kwargs = {"model": self.config.model, "input": inputs, "tools": functions,
                  "tool_choice": "auto", "max_output_tokens": max_tokens, "timeout": timeout,
                  "store": False, "include": ["reasoning.encrypted_content"], **self.config.extra}
        if self.config.reasoning_effort is not None:
            kwargs["reasoning"] = {"effort": self.config.reasoning_effort}
        try:
            response = self.client.responses.create(**kwargs)
        except APIError as error:
            raise ProviderError(f"LLM Responses request failed ({type(error).__name__}, "
                                f"HTTP {getattr(error, 'status_code', None)}). "
                                "Check provider Responses support, model, reasoning effort, key, and quota. "
                                "No automatic paid retries were made.") from None
        if response.status not in {"completed", "incomplete"}:
            raise ProviderError(f"Provider returned an unsuccessful Responses status: {response.status}.")
        native = [item.model_dump(exclude_none=True) for item in response.output]
        message = {"role": "assistant", "content": response.output_text or None,
                   "_responses_output": native}
        calls = [item for item in response.output if item.type == "function_call"]
        if calls:
            message["tool_calls"] = [{"id": call.call_id, "type": "function", "function": {
                "name": call.name, "arguments": call.arguments,
            }} for call in calls]
        reason = "tool_calls" if calls else "stop"
        if response.status == "incomplete":
            detail = response.incomplete_details.reason if response.incomplete_details else "unknown"
            reason = "length" if detail == "max_output_tokens" else "content_filter"
        usage = response.usage.model_dump() if response.usage else {}
        if response.usage:
            usage.update(prompt_tokens=response.usage.input_tokens,
                         completion_tokens=response.usage.output_tokens)
        return Reply(message, usage, reason)


class LegacyDemoProvider:
    """A deterministic test driver, not an LLM. Uses actual tool observations.

    The scripted adaptation exercises the same agent loop and tools as a live run.
    It is intentionally not evidence that a real model makes good decisions.
    """

    def __init__(self):
        self.step = 0
        self.outputs = []

    def complete(self, messages, tools, *, max_tokens, timeout):
        results = [json.loads(m["content"])["result"] for m in messages
                   if m["role"] == "tool" and json.loads(m["content"]).get("ok")]
        if any(m["role"] == "tool" and not json.loads(m["content"]).get("ok") for m in messages):
            raise ProviderError("Offline demo encountered a tool error; inspect events.jsonl.")
        inspection = next((r for r in results if "missing_cells" in r), {})
        plan = {
            "rationale": "Independent synthetic groups; use a log transform to reduce right skew, "
                         "Welch's test for mean differences, and BH correction across features.",
            "test": "welch_t", "correction": "fdr_bh", "transform": "log2",
            "imputation": "median" if inspection.get("missing_cells", 0) else "none",
            "pca_scaling": "auto",
        }
        sensitivity = {**plan, "test": "mannwhitney", "transform": "none",
                       "rationale": "Exploratory sensitivity analysis compares distribution/rank "
                                    "differences with the initial log-scale mean analysis. "
                                    "This checks a different estimand and does not confirm discoveries."}
        steps = [
            ("inspect_dataset", {}),
            ("set_plan", plan),
            ("preprocess", {"plan_id": "p001"}),
            ("compare_groups", {"dataset_id": "d001"}),
            ("run_pca", {"dataset_id": "d001"}),
            ("make_plot", {"kind": "distributions", "source_id": "d001"}),
            ("make_plot", {"kind": "volcano", "source_id": "c001"}),
            ("make_plot", {"kind": "pca", "source_id": "pc001"}),
            ("make_plot", {"kind": "boxplot", "source_id": "d001", "features": ["lipid_01", "lipid_02"]}),
            ("set_plan", sensitivity),
            ("preprocess", {"plan_id": "p002"}),
            ("compare_groups", {"dataset_id": "d002"}),
            ("make_plot", {"kind": "volcano", "source_id": "c002"}),
        ]
        if self.step < len(steps):
            name, arguments = steps[self.step]
        else:
            primary = next(r for r in results if r.get("comparison_id") == "c001")
            second = next(r for r in results if r.get("comparison_id") == "c002")
            name, arguments = "finish_analysis", {
                "findings": [
                    {"text": f"The initial analysis tested {primary['features_tested']} features; "
                              f"{primary['significant_features']} had BH-adjusted p < 0.05.",
                     "evidence_ids": ["c001"]},
                    {"text": f"The exploratory rank analysis found {second['significant_features']} "
                              "features below its adjusted-p threshold. It tests a different estimand "
                              "and does not provide independent replication.", "evidence_ids": ["c002"]},
                    {"text": "PCA summarizes variation without testing group separation.",
                     "evidence_ids": ["pc001"]},
                ],
                "limitations": ["Synthetic data and a scripted offline driver; no live LLM was evaluated.",
                                "Median imputation assumes the observed values are informative."],
            }
        self.step += 1
        return Reply({"role": "assistant", "content": None, "tool_calls": [{
            "id": f"demo_call_{self.step}", "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            },
        }]}, {"total_tokens": 0}, "tool_calls")


class DemoProvider(LegacyDemoProvider):
    """Scripted five-stage driver; preserves legacy replay for reference tests."""

    def complete(self, messages, tools, *, max_tokens, timeout):
        if not any(tool["function"]["name"] == "get_stage_context" for tool in tools):
            return super().complete(messages, tools, max_tokens=max_tokens, timeout=timeout)
        results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
        if any(not result.get("ok") for result in results):
            raise ProviderError("Offline staged demo encountered a tool error; inspect stage reports.")
        values = [result["result"] for result in results]
        inspection = next((value for value in values if "missing_cells" in value), {})
        figures = [
            {"kind": "distributions", "source_id": "d001"},
            {"kind": "volcano", "source_id": "c001"},
            {"kind": "boxplot", "source_id": "d001", "features": ["lipid_01", "lipid_02"]},
        ]
        steps = [
            ("set_workflow_plan", {
                "objective": "Compare the synthetic condition and control groups with an auditable univariate analysis.",
                "rationale": "Prepare the quantified table, compare the two independent groups, and show diagnostic and result figures.",
                "required_figures": ["distributions", "volcano", "boxplot"],
            }),
            ("submit_stage_assessment", {"summary": "The supported two-group objective and required figures are registered."}),
            ("inspect_dataset", {}),
            ("set_preprocessing_plan", {
                "rationale": "Use log2 to reduce right skew and median imputation when observed missingness requires it.",
                "imputation": "median" if inspection.get("missing_cells", 0) else "none", "transform": "log2",
            }),
            ("preprocess", {"plan_id": "pp001"}),
            ("submit_stage_assessment", {
                "summary": "Preparation and its checkpoints are complete; imputation uncertainty remains a limitation.",
                "limitations": ["Median imputation assumes that observed values are informative."],
            }),
            ("set_univariate_plan", {
                "dataset_id": "d001", "rationale": "Compare log-scale means with Welch and BH correction across retained features.",
                "test": "welch_t", "correction": "fdr_bh",
            }),
            ("compare_groups", {"dataset_id": "d001", "plan_id": "u001"}),
            ("submit_stage_assessment", {"summary": "The prespecified univariate comparison has been saved."}),
            ("set_figure_plan", {
                "rationale": "Show preprocessing effects, adjusted-p results, and individual values for two illustrative features.",
                "figures": figures,
            }),
            *[("make_plot", figure) for figure in figures],
            ("submit_stage_assessment", {"summary": "All planned univariate and diagnostic figures are saved."}),
            ("validate_workflow", {}),
        ]
        if self.step < len(steps):
            name, arguments = steps[self.step]
        else:
            comparison = next(value for value in values if value.get("comparison_id") == "c001")
            name, arguments = "finish_analysis", {
                "findings": [{
                    "text": f"The analysis tested {comparison['features_tested']} features; "
                            f"{comparison['significant_features']} had BH-adjusted p < 0.05.",
                    "evidence_ids": ["c001"],
                }],
                "limitations": ["Synthetic data and scripted decisions; no live LLM was evaluated.",
                                "Imputation uncertainty is not propagated into inference."],
            }
        self.step += 1
        return Reply({"role": "assistant", "content": None, "tool_calls": [{
            "id": f"staged_demo_{self.step}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]}, {"total_tokens": 0}, "tool_calls")
