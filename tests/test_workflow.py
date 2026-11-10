"""Stage permissions, dependencies, privacy, revisions, and real offline execution."""

import json

import numpy as np
import pytest

from conftest import call
from metabolomics_agent.agent import Agent
from metabolomics_agent.examples import generate_example
from metabolomics_agent.llm import DemoProvider
from metabolomics_agent.models import LLMConfig, StudyConfig
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry
from metabolomics_agent.workflow import STAGES


@pytest.fixture
def staged(independent_study):
    config, root, _ = independent_study
    return ToolRegistry(RunState(config, root, root / "staged"))


def assess(tools):
    return call(tools, "submit_stage_assessment", {"summary": "The current stage outputs have been reviewed."})


def coordinate(tools, *, descriptive=False, figures=None):
    call(tools, "set_workflow_plan", {
        "objective": "Describe and compare the quantified groups.",
        "rationale": "Execute the supported objective with auditable preparation and appropriate figures.",
        "analysis_kind": "descriptive" if descriptive else "two_group",
        "required_figures": figures if figures is not None else ["distributions", "volcano"],
    })
    assess(tools)


def prepare_staged(tools, **options):
    call(tools, "inspect_dataset")
    call(tools, "summarize_dataset", {"dataset_id": "raw", "scale": "original"})
    plan = call(tools, "set_preprocessing_plan", {
        "rationale": "Prespecified preparation appropriate for the quantified observations.", **options,
    })
    dataset = call(tools, "preprocess", {"plan_id": plan["plan_id"]})
    for scale in ["concentrations", "testing"]:
        call(tools, "summarize_dataset", {"dataset_id": dataset["dataset_id"], "scale": scale,
                                         "value_policy": "including_imputed"})
    assess(tools)
    return dataset


def compare_staged(tools, **options):
    workflow = tools.state.workflow
    plan = call(tools, "set_univariate_plan", {
        "dataset_id": workflow.data.dataset_id,
        "rationale": "Compare the independent group means with multiplicity correction.", **options,
    })
    result = call(tools, "compare_groups", {
        "dataset_id": workflow.data.dataset_id, "plan_id": plan["plan_id"],
    })
    return plan, result


def test_default_protocol_and_all_catalogs_exclude_multivariate(staged):
    assert staged.state.protocol == "staged_v1"
    for stage in STAGES:
        schemas = ToolRegistry(None).schemas(stage=stage)
        names = {schema["function"]["name"] for schema in schemas}
        assert "run_pca" not in names and "set_plan" not in names
        if stage == STAGES[1]:
            plan = next(schema for schema in schemas if schema["function"]["name"] == "set_preprocessing_plan")
            assert "pca_scaling" not in plan["function"]["parameters"]["properties"]
            plot = next(schema for schema in schemas if schema["function"]["name"] == "make_plot")
            assert plot["function"]["parameters"]["properties"]["kind"]["enum"] == ["distributions", "boxplot"]


def test_stage_permissions_and_closure_are_enforced_in_python(staged):
    assert not staged.execute("inspect_dataset", {})["ok"]
    assert not staged.execute("run_pca", {"dataset_id": "d001"})["ok"]
    assert not staged.execute("submit_stage_assessment", {"summary": "Advance without a plan."})["ok"]
    assert staged.state.workflow.stage == STAGES[0]
    coordinate(staged)
    assert not staged.execute("compare_groups", {"dataset_id": "d001", "plan_id": "u001"})["ok"]
    assert not staged.execute("set_preprocessing_plan", {"rationale": "Preparation chosen without diagnostics."})["ok"]
    assert staged.state.workflow.stage == STAGES[1]
    staged.state.workflow.interrupt("End of permission test.")


def test_preparation_and_statistics_have_independent_plans(staged):
    coordinate(staged)
    result = prepare_staged(staged, transform="log2")
    state = staged.state
    assert state.plans == {}
    assert state.datasets[result["dataset_id"]].pca_values is None
    assert "pca" not in result["artifacts"]
    assert not any(artifact["kind"] == "pca_values" for artifact in state.artifacts.values())
    before = state.datasets[result["dataset_id"]].testing.copy()
    plan, comparison = compare_staged(staged)
    assert comparison["plan_id"] == plan["plan_id"] == "u001"
    assert comparison["preprocessing_plan_id"] == "pp001"
    np.testing.assert_array_equal(before, state.datasets[result["dataset_id"]].testing)
    assert not staged.execute("compare_groups", {"dataset_id": result["dataset_id"], "plan_id": "u001"})["ok"]
    state.workflow.interrupt("End of plan separation test.")


def test_handoff_carries_imputation_warning_without_sample_values(staged):
    staged.state.raw.iloc[0, 0] = np.nan
    coordinate(staged)
    result = prepare_staged(staged, imputation="median")
    context = call(staged, "get_stage_context")
    assert context["stage"] == STAGES[2]
    assert context["dataset_id"] == result["dataset_id"]
    assert context["preparation"]["imputation"] == "median"
    assert any(issue["code"] == "IMPUTATION_USED" for issue in context["issues"])
    assert context["incoming_handoff"]["next_stage"] == STAGES[2]
    serialized = json.dumps(context)
    assert not any(sample_id in serialized for sample_id in staged.state.raw.index)
    sample_table = result["artifacts"]["testing"]
    assert not staged.execute("read_result", {"artifact_id": sample_table})["ok"]
    staged.state.workflow.interrupt("End of handoff privacy test.")


def test_descriptive_scope_skips_tests_and_optional_figures(staged):
    coordinate(staged, descriptive=True, figures=[])
    prepare_staged(staged)
    workflow = staged.state.workflow
    assert workflow.stage == STAGES[4]
    for stage in STAGES[2:4]:
        report = json.loads((staged.state.output_dir / workflow.data.completed_reports[stage]).read_text())
        assert report["status"] == "skipped"
        assert (staged.state.output_dir / workflow.data.completed_reports[stage]).with_name("handoff.json").is_file()
    result = call(staged, "finish_analysis", {
        "findings": [{"text": "The original descriptive diagnostics are available.",
                      "evidence_ids": [workflow.data.diagnostic_ids[0]]}],
        "limitations": ["Only descriptive analysis was requested."],
    })
    assert result["status"] == "complete" and staged.state.finished
    assert not staged.state.comparisons and not staged.state.pcas
    assert len(workflow.data.completed_reports) == 5
    report = (staged.state.output_dir / "report.md").read_text()
    assert "## PCA" not in report


def test_revision_invalidates_downstream_and_preserves_history(staged):
    coordinate(staged)
    first = prepare_staged(staged)
    _, comparison = compare_staged(staged)
    assess(staged)
    artifact = staged.state.artifacts[comparison["artifact_id"]]
    saved = (staged.state.output_dir / artifact["path"]).read_bytes()
    call(staged, "request_revision", {
        "target": STAGES[1], "rationale": "Explore the dependence of results on a different transformation.",
    })
    workflow = staged.state.workflow
    assert workflow.data.dataset_id is None and workflow.data.comparison_ids == []
    assert STAGES[1] not in workflow.data.completed_reports
    assert first["dataset_id"] in staged.state.datasets
    assert (staged.state.output_dir / artifact["path"]).read_bytes() == saved
    prepare_staged(staged, transform="log2")
    plan, newer = compare_staged(staged)
    assert plan["plan"]["purpose"] == newer["purpose"] == "exploratory"
    assert newer["dataset_id"] != comparison["dataset_id"]
    assert not staged.execute("read_result", {"artifact_id": comparison["artifact_id"]})["ok"]
    workflow.interrupt("End of revision test.")


def test_figure_plan_and_sources_are_required(staged):
    coordinate(staged)
    prepare_staged(staged)
    compare_staged(staged)
    assess(staged)
    assert not staged.execute("make_plot", {"kind": "volcano", "source_id": "c001"})["ok"]
    assert not staged.execute("make_plot", {"kind": "pca", "source_id": "pc001"})["ok"]
    assert not staged.execute("set_figure_plan", {
        "rationale": "An incomplete figure plan deliberately omits the volcano.",
        "figures": [{"kind": "distributions", "source_id": "d001"}],
    })["ok"]
    assert not staged.execute("submit_stage_assessment", {"summary": "Missing planned figures."})["ok"]
    staged.state.workflow.interrupt("End of figure permission test.")


def test_staged_offline_demo_has_five_reports_and_no_pca(tmp_path):
    study = generate_example(tmp_path / "synthetic")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    result = Agent(state, DemoProvider(), LLMConfig(model="offline", max_total_tokens=500000)).run(
        config.objective, progress=lambda _: None)
    assert result["status"] == "complete" and result["total_tokens"] == 0
    assert len(state.workflow.data.completed_reports) == 5
    assert not state.pcas
    assert all(dataset.pca_values is None for dataset in state.datasets.values())
    assert all(artifact.kind not in {"pca_values", "pca_scores", "pca_loadings", "pca"}
               for artifact in state.evidence.artifacts.values())
    assert state.comparisons["c001"]["features_tested"] == 12
    for relative in state.workflow.data.completed_reports.values():
        folder = (state.output_dir / relative).parent
        for filename in ["stage_report.json", "stage_report.md", "handoff.json", "prompt_snapshot.json"]:
            assert (folder / filename).is_file()
    events = [json.loads(line) for line in (state.output_dir / "events.jsonl").read_text().splitlines()]
    assert not any(event.get("tool") == "run_pca" for event in events)
    assert len(list((state.output_dir / "requests").glob("*.json"))) == result["model_requests"]
    for request in (state.output_dir / "requests").glob("*.json"):
        saved = json.loads(request.read_text())
        assert saved["messages"][0]["content"].startswith(
            state.workflow.bundle["common"] + "\n\n" + state.workflow.bundle[saved["stage"]])
        assert json.loads(saved["messages"][-1]["content"])["stage_context"]["stage"] == saved["stage"]


def test_resume_restores_stage_and_does_not_repeat_preparation(tmp_path):
    study = generate_example(tmp_path / "synthetic")
    config = StudyConfig.model_validate_json(study.read_text())
    state = RunState(config, study.parent, tmp_path / "run")
    provider = DemoProvider()
    cfg = LLMConfig(model="offline", max_total_tokens=500000, max_turns=9)
    first = Agent(state, provider, cfg).run(config.objective, progress=lambda _: None)
    assert first["status"] == "turn_limit"
    assert state.workflow.stage == STAGES[2]
    restored = RunState.restore(state.output_dir)
    assert restored.workflow.stage == STAGES[2]
    assert restored.workflow.bundle == state.workflow.bundle
    resumed = Agent(restored, provider, cfg.model_copy(update={"max_turns": 20})).run(
        "Continue the saved analysis.", resume=True, progress=lambda _: None)
    assert resumed["status"] == "complete"
    assert len(restored.datasets) == 1 and len(restored.preprocessing_plans) == 1
    assert len(restored.comparisons) == 1


def test_modified_handoff_is_rejected_before_next_consumer(staged):
    coordinate(staged)
    aid = staged.state.workflow.data.handoff_ids[STAGES[0]]
    path = staged.state.evidence.path(staged.state.evidence.artifacts[aid].path)
    path.write_text("{}")
    with pytest.raises(ValueError, match="modified evidence"):
        staged.execute("inspect_dataset", {})
