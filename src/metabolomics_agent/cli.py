import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from .agent import Agent
from .benchmark import reference_check
from .examples import generate_example
from .llm import DemoProvider, OpenAICompatibleProvider, ProviderError
from .models import LLMConfig, StudyConfig
from .state import RunState, dumps
from .workflow import STAGES


def llm_config_from_env() -> LLMConfig:
    if not os.environ.get("LLM_MODEL", "").strip():
        raise ValueError("Set LLM_MODEL in .env or the environment to the tool-calling model you want to test.")
    return LLMConfig(
        model=os.environ.get("LLM_MODEL", ""),
        base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        api=os.environ.get("LLM_API", "chat_completions"),
        reasoning_effort=os.environ.get("LLM_REASONING_EFFORT", "").strip() or None,
        max_turns=int(os.environ.get("LLM_MAX_TURNS", "24")),
        max_tool_calls=int(os.environ.get("LLM_MAX_TOOL_CALLS", "48")),
        max_completion_tokens=int(os.environ.get("LLM_MAX_COMPLETION_TOKENS", "2500")),
        max_total_tokens=int(os.environ.get("LLM_MAX_TOTAL_TOKENS", "60000")),
        timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "45")),
        max_run_seconds=float(os.environ.get("LLM_MAX_RUN_SECONDS", "600")),
        token_parameter=os.environ.get("LLM_TOKEN_PARAMETER", "max_completion_tokens"),
        extra=json.loads(os.environ.get("LLM_EXTRA_JSON", "{}")),
    )


def default_output(prefix: str):
    return Path("runs") / f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Auditable metabolomics analysis agent")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Offline synthetic end-to-end demo; no API/key/spending")
    demo.add_argument("--output", type=Path)
    demo.add_argument("--pca", action="store_true", help="Also run exploratory PCA and its figures")
    demo.add_argument("--pca-only", action="store_true", help="Descriptive/PCA objective without univariate tests")
    example = commands.add_parser("make-example", help="Generate a synthetic dataset and study.json")
    example.add_argument("--output", type=Path, default=Path("examples/synthetic"))
    run = commands.add_parser("run", help="Run the adaptive agent with a live LLM")
    run.add_argument("--study", type=Path, required=True)
    run.add_argument("--request", help="Override the objective in the study configuration")
    run.add_argument("--output", type=Path)
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    run.add_argument("--seed", type=int, default=42)
    resume = commands.add_parser("resume", help="Continue an incomplete run without repeating completed calls")
    resume.add_argument("--run", type=Path, required=True)
    resume.add_argument("--request", default="", help="Optional clarification for the continued analysis")
    resume.add_argument("--env-file", type=Path, default=Path(".env"))
    benchmark = commands.add_parser("reference-check", help="Check selected published GitBook numbers, without an LLM")
    benchmark.add_argument("--dataset", type=Path, required=True)
    benchmark.add_argument("--output", type=Path)
    evaluation = commands.add_parser("evaluate-offline", help="Run deterministic local evaluation cases; no key or live model")
    evaluation.add_argument("--output", type=Path)
    evaluation.add_argument("--seed", type=int, default=42)
    from .evaluation_cases import CASES
    evaluation.add_argument("--case", action="append", choices=[case.name for case in CASES], dest="cases")
    audit = commands.add_parser("audit-run", help="Audit saved staged evidence without changing the run or calling a model")
    audit.add_argument("--run", type=Path, required=True)
    audit.add_argument("--output", type=Path)
    case_evaluation = commands.add_parser("evaluate-case", help="Evaluate a versioned case offline or audit its saved run; no LLM/key")
    case_evaluation.add_argument("--case", type=Path, required=True)
    case_evaluation.add_argument("--run", type=Path)
    case_evaluation.add_argument("--output", type=Path, required=True)
    case_evaluation.add_argument("--stage", choices=[*STAGES, "03_pca"])
    evaluation_example = commands.add_parser("make-evaluation-example", help="Write a reusable synthetic case without a key")
    evaluation_example.add_argument("--output", type=Path, required=True)
    evaluation_example.add_argument("--variant", choices=[case.name for case in CASES if not case.guards and not case.resume_after], default="independent")
    evaluation_example.add_argument("--seed", type=int, default=42)
    lipidomics = commands.add_parser("register-lipidomics", help="Build independent tutorial references and three reusable cases from the original Excel; no LLM")
    lipidomics.add_argument("--dataset", type=Path, required=True)
    lipidomics.add_argument("--output", type=Path, required=True)
    schemas = commands.add_parser("tool-schemas", help="Print staged function schemas (coordination by default)")
    schemas.add_argument("--stage", choices=STAGES, default=STAGES[0])
    schemas.add_argument("--analysis-block", choices=["univariate", "pca"], default="univariate")
    args = parser.parse_args(argv)
    try:
        if args.command == "register-lipidomics":
            from .lipidomics_cases import register_lipidomics
            print(dumps({"cases": register_lipidomics(args.dataset, args.output), "api_calls": 0}))
            return 0
        if args.command == "make-evaluation-example":
            from .evaluation_examples import make_evaluation_example
            print(make_evaluation_example(args.output, args.variant, args.seed))
            return 0
        if args.command == "evaluate-case":
            from .case_evaluation import evaluate_case
            report = evaluate_case(args.case, args.output, run=args.run, stage_only=args.stage)
            print(dumps({"status": report.status, "analysis_status": report.analysis_status,
                         "coverage": report.summary["coverage"], "output_dir": args.output, "api_calls": 0}))
            return 0 if report.status == "passed" else 1 if report.status == "failed" else 2
        if args.command in {"evaluate-offline", "audit-run"}:
            from .evaluation import evaluate_offline, save_audit
            if args.command == "evaluate-offline":
                output = args.output or default_output("offline-evaluation")
                report = evaluate_offline(output, cases=args.cases, seed=args.seed)
                print(dumps({"status": report["status"], "output_dir": output,
                             "cases_passed": report["cases_passed"], "cases": len(report["cases"]),
                             "numeric_values_checked": report["numeric_values_checked"],
                             "live_model_evaluation": "not_run", "api_calls": 0}))
            else:
                output = args.output or default_output("audit")
                report = save_audit(args.run, output)
                print(dumps({"status": report["status"], "output_dir": output,
                             "analysis_status": report.get("analysis_status"),
                             "checks_failed": report["checks_failed"], "live_llm_decisions": "not_assessed"}))
            return 0 if report["status"] == "passed" else 2 if report["status"] == "incomplete" else 1
        if args.command == "make-example":
            print(generate_example(args.output))
            return 0
        if args.command == "tool-schemas":
            from .tools import ToolRegistry

            print(dumps(ToolRegistry(None).schemas(stage=args.stage, analysis_block=args.analysis_block)))
            return 0
        if args.command == "reference-check":
            summary = reference_check(args.dataset, args.output or default_output("reference"))
            print(dumps(summary))
            return 0 if summary["passed"] else 1
        if args.command == "demo":
            # Keep numerical input snapshots in the run itself; temporary sources may expire.
            with tempfile.TemporaryDirectory(prefix="metabolomics-demo-") as temporary:
                study_file = generate_example(Path(temporary) / "synthetic")
                study = StudyConfig.model_validate_json(study_file.read_text())
                state = RunState(study, study_file.parent, args.output or default_output("demo"))
                result = Agent(state, DemoProvider(pca=args.pca, pca_only=args.pca_only), LLMConfig(model="offline-scripted-demo",
                               max_turns=48, max_tool_calls=64, max_total_tokens=500000)).run(
                                   "Explore the synthetic prepared feature space using descriptive statistics and exploratory PCA."
                                   if args.pca_only else study.objective)
        else:
            load_dotenv(args.env_file, override=False)
            config = llm_config_from_env()
            provider = OpenAICompatibleProvider(config, os.environ.get("LLM_API_KEY", ""))
            if args.command == "resume":
                state = RunState.restore(args.run)
                result = Agent(state, provider, config).run(args.request, resume=True)
            else:
                study = StudyConfig.model_validate_json(args.study.read_text())
                state = RunState(study, args.study.resolve().parent,
                                 args.output or default_output("analysis"), args.seed)
                result = Agent(state, provider, config).run(args.request or study.objective)
        print(dumps(result))
        if result["status"] == "complete":
            print(f"Report: {result['output_dir']}/report.md")
            return 0
        print("Analysis did not finish; see incomplete.md and the saved trace.", file=sys.stderr)
        return 2
    except (ValueError, OSError, ValidationError, ProviderError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
