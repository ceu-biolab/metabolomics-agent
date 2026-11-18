"""Register tutorial cases using the generic evaluator; dataset-specific constants stay here."""

import os
from pathlib import Path
import shutil

from .case_evaluation import fingerprint
from .evaluation_models import EvaluationCase
from .lipidomics_reference import DATASET_SOURCE, TEST_SOURCE, PCA_SOURCE, build_references
from .models import StudyConfig
from .serialization import dumps


def register_lipidomics(dataset: Path, output: Path):
    dataset, output = dataset.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frame, features, tables, metadata = build_references(dataset, output / "references")
    producer = output / "references/producer.py"
    shutil.copyfile(Path(__file__).with_name('lipidomics_reference.py'), producer)
    entries = [{"id": name, "path": f"references/{name}.csv", "sha256": fingerprint(output / f"references/{name}.csv"),
                "origin": "author_script", "source": PCA_SOURCE if name.startswith('pca_') else TEST_SOURCE,
                "producer": "references/producer.py", "independence": "Recalculated outside production analysis; shares the pinned scientific libraries.",
                "review_status": "pending"} for name in tables]
    manifest = {"dataset_id": "gitbook-lipidomics-no1", "root": os.path.relpath(dataset.parent, output),
        "files": [{"path": dataset.name, "sha256": fingerprint(dataset)}], "source": DATASET_SOURCE,
        "sample_column": "Sample Name", "feature_columns": features, "group_counts": {"N":97,"T":109,"PAN":21},
        "units": "Concentrations; confirm physical units from the original study before biological interpretation.",
        "normalization_status": metadata['normalization']}
    def exact(mid, document, path, expected):
        return {"id": mid, "validator": "exact", "observation": {"document": document, "path": path},
                "expected": expected, "critical": True, "provenance": "Frozen tutorial reproduction contract."}
    def audit(mid, prefixes):
        return {"id": mid, "validator": "audit", "audit_prefixes": prefixes,
                "critical": True, "provenance": "Independent read-only reconstruction; scientific libraries are shared."}
    def table(mid, kind, reference, keys, columns, pca_weights=False):
        return {"id": mid, "validator": "pca_weights" if pca_weights else "table",
                "observation": {"document": "table", "artifact_kind": kind},
                "reference_id": reference, "keys": keys, "columns": columns,
                "tolerance": {"rtol": 1e-8, "atol": 1e-10 if pca_weights else 0},
                "critical": True, "provenance": "Frozen independent author-script calculation; see reference manifest."}
    paths = []
    for name in ['student_t', 'mannwhitney', 'pca']:
        is_pca = name == 'pca'
        study = StudyConfig(data_path=dataset.name, sample_column="Sample Name", group_column="Label",
            reference_group=None if is_pca else "N", treatment_group=None if is_pca else "T",
            analysis_scope="descriptive" if is_pca else "two_group", selected_groups=["N","PAN","T"] if is_pca else [],
            feature_columns=features, normalization_status=manifest['normalization_status'],
            objective=("Reproduce the Python PCA tutorial on all N, PAN and T samples: original concentrations, StandardScaler with ddof=0, ten components, no log or imputation."
                       if is_pca else f"Reproduce the Python {name} T-versus-N tutorial using original concentrations, no imputation/normalization/transformation, two-sided tests, Holm-Sidak across all 127 lipids and alpha 0.05. Include Lilliefors diagnostics. Do not substitute methods."))
        rows = frame if is_pca else frame[frame.Label.isin(['N','T'])]
        stages = [{"stage_id": "01_coordination", "reason": "Fix the tutorial population and scope.",
            "metrics": [exact('coordination.scope_match','run',['study','analysis_scope'],study.analysis_scope),
                {"id": "coordination.cohort_match", "validator": "set", "critical": True,
                 "observation": {"document": "table", "artifact_kind": "input_values", "column": "Sample Name"},
                 "expected": rows['Sample Name'].tolist(), "provenance": "Verified original sample roster."},
                table('coordination.group_assignment_match','input_metadata','cohort_all' if is_pca else 'cohort_TN',['Sample Name'],['Label'])]},
            {"stage_id": "02_preparation", "reason": "Preserve original concentrations and check all descriptives.",
             "depends_on": ["01_coordination"], "metrics": [
                table('preparation.concentration_match','concentrations','concentrations_all' if is_pca else 'concentrations_TN',['Sample Name'],features),
                table('preparation.test_scale_match','testing_values','concentrations_all' if is_pca else 'concentrations_TN',['Sample Name'],features),
                audit('preparation.independent_checks',['preparation.','descriptive.'])]}]
        if is_pca:
            stages += [{"stage_id": "03_univariate", "reason": "Descriptive PCA has no group-comparison inference.",
                "metrics": [exact('univariate.expected_omission','state',['comparisons'],{})]},
                {"stage_id": "03_pca", "reason": "Reproduce ten components from the full cohort.", "depends_on": ["02_preparation"],
                 "metrics": [table('pca.matrix_match','pca_matrix','pca_matrix',['Sample Name'],features),
                     table('pca.variance_match','pca_variance','pca_variance',['component'],['explained_variance','explained_variance_ratio','cumulative_variance_ratio']),
                     table('pca.weight_match','pca_loadings','pca_weights',['feature'],[f'PC{i+1}' for i in range(10)],True),
                     audit('pca.projection_and_reconstruction',['pca.'])]}]
            figures = [{"kind": kind, "source_id": "pc001"} for kind in ['pca_scores','pca_scree','pca_loadings_scatter']]
        else:
            columns = ['statistic','p_value','adjusted_p_value','log2_fold_change','significant']
            stages += [{"stage_id": "03_univariate", "reason": "Fixed independent two-group tutorial tests.", "depends_on": ["02_preparation"],
                "metrics": [table('univariate.reference_match','comparison',name,['feature'],columns),
                    table('univariate.normality_match','univariate_diagnostics','normality',['feature'],
                          [f'lilliefors_{metric}_{group}' for group in ['reference','treatment'] for metric in ['statistic','p']]),
                    exact('univariate.test_match','state',['comparisons','c001','test'],name),
                    exact('univariate.family_count','state',['comparisons','c001','features_tested'],127),
                    audit('univariate.additional_checks',['comparison.'])]},
                {"stage_id": "03_pca", "reason": "PCA belongs to its separate full-cohort case.",
                 "metrics": [exact('pca.expected_omission','state',['pcas'],{})]}]
            if name == 'student_t':
                stages[-2]['metrics'].append(table('univariate.degrees_of_freedom','univariate_audit',name,['feature'],['df']))
            figures = [{"kind": 'distributions', "source_id": 'd001', "features": ['PC 34:1','PC 36:5']},
                       {"kind": 'boxplot', "source_id": 'd001', "features": ['PC 34:1','PC 36:5']},
                       {"kind": 'volcano', "source_id": 'c001'}]
            if name == 'student_t':
                figures.append({"kind": 'heatmap', "source_id": 'd001', "selection_mode": 'significant',
                    "comparison_id": 'c001', "centering": 'feature_mean', "scaling": 'feature_sd', "rows_per_page": 40})
        stages += [{"stage_id": "04_visualization", "reason": "Check figure sources, labels and selections; human legibility review remains pending.",
                    "depends_on": ['03_pca' if is_pca else '03_univariate'],
                    "metrics": [audit('visualization.source_checks',['figure.','heatmap.']),
                                {"id": 'visualization.legibility_review', "validator": 'manual', "required": False,
                                 "review_scope": 'visual', "provenance": 'Figure legibility requires a recorded human review.'}]},
                   {"stage_id": "05_reporting", "reason": "Verify saved results and retain explicit review limits.",
                    "depends_on": ['04_visualization'],
                    "metrics": [audit('reporting.record_checks',['report.']),
                                {"id": 'reporting.scientific_review', "validator": 'manual', "required": False,
                                 "provenance": 'Biological interpretation requires human scientific review.'}]}]
        steps = []
        def add(tool, arguments=None):
            steps.append({"tool":tool,"arguments":arguments or {}})
        assessment = {"summary": "The scripted reproduction has checked current evidence and saved its required outputs.",
                      "limitations": ["This is offline execution validation; interpretation and figure legibility await expert review."]}
        add('set_workflow_plan',{'objective':study.objective,'rationale':'Use the fixed author tutorial protocol and keep other analyses in independent cases.',
            'analysis_kind':'descriptive' if is_pca else 'two_group','pca_requirement':'required' if is_pca else 'disabled',
            'required_figures':[figure['kind'] for figure in figures]})
        add('submit_stage_assessment',assessment)
        add('inspect_dataset')
        add('summarize_dataset',{'dataset_id':'raw','scale':'original'})
        add('set_preprocessing_plan',{'rationale':'The supplied complete original concentrations need no imputation, additional normalization or transformation.',
                                     'imputation':'none','normalization':'none','transform':'none'})
        add('preprocess',{'plan_id':'pp001'})
        for scale in ['concentrations','testing']:
            add('summarize_dataset',{'dataset_id':'d001','scale':scale,'value_policy':'including_imputed'})
        add('submit_stage_assessment',assessment)
        if is_pca:
            add('inspect_pca_inputs',{'dataset_id':'d001','scale':'concentrations'})
            add('set_pca_plan',{'dataset_id':'d001','scale':'concentrations','diagnostic_id':'pv001',
                               'rationale':'Use all original features and all three groups with population-SD autoscaling, independently of significance.',
                               'components':10,'scaling':'auto','scaling_ddof':0})
            add('run_pca',{'dataset_id':'d001','plan_id':'ppc001'})
            add('submit_stage_assessment',assessment)
        else:
            add('inspect_univariate',{'dataset_id':'d001','normality_method':'lilliefors_norm'})
            add('set_univariate_plan',{'dataset_id':'d001','diagnostic_id':'uv001','test':name,'correction':'holm-sidak','alpha':.05,
                                      'rationale':'Reproduce the fixed tutorial test without using normality p-values to choose a different method.'})
            add('compare_groups',{'dataset_id':'d001','plan_id':'u001'})
            add('submit_stage_assessment',assessment)
        add('set_figure_plan',{'rationale':'Reproduce the declared tutorial outputs and mark supplementary figures explicitly.','figures':figures})
        for figure in figures:
            add('make_plot',figure)
        add('submit_stage_assessment',assessment)
        add('validate_workflow')
        add('finish_analysis',{'findings':[{'text':'The fixed tutorial outputs have been computed and saved; scientific interpretation remains pending.',
                                          'evidence_ids':['pc001' if is_pca else 'c001'],
                                          **({} if is_pca else {'claims':[{'comparison_id':'c001','metric':'significant_features','value':'@observed_comparison_aggregate'}]})}],
                               'limitations':['No live model or independent clinical validation was evaluated. Significant heatmaps are supplementary and use the same data for selection and display.']})
        document = EvaluationCase.model_validate({'case_id':f'lipidomics.{name}','mode':'reproduction','dataset':manifest,
            'study':study.model_dump(),'request':study.objective,'references':{'entries':entries,
                'support_files':[{'path':str(path.relative_to(output)), 'sha256':fingerprint(path)}
                                 for path in [producer, output/'references/reference_metadata.json']]},'stages':stages,'script':steps})
        path = output / f'{name}.json'
        path.write_text(dumps(document.model_dump()) + '\n')
        paths.append(path)
    return paths
