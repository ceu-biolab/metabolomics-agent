"""H: reusable scientific criteria, injected faults and additional cohort scope."""

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from metabolomics_agent.case_evaluation import evaluate_case, evaluate_saved_case, fingerprint, load_case
from metabolomics_agent.evaluation_examples import make_evaluation_example
from metabolomics_agent.evaluation_metrics import pca_weights_comparison
from metabolomics_agent.evaluation_models import Tolerance
from metabolomics_agent.evidence import EvidenceStore
from metabolomics_agent.run_audit import audit_run


@pytest.fixture(scope="module")
def reference_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("H-reference")
    path = make_evaluation_example(root / "case", "independent", seed=913)
    report = evaluate_case(path, root / "evaluation")
    assert report.status == "passed"
    return path, root / "evaluation/scientific_run"


def test_new_case_cli_never_reads_credentials_or_network(tmp_path, monkeypatch):
    import socket
    from metabolomics_agent import cli
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline case accessed credentials/network")
    monkeypatch.setattr(cli, 'load_dotenv', forbidden)
    monkeypatch.setattr(cli, 'OpenAICompatibleProvider', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    assert cli.main(['make-evaluation-example','--variant','descriptive','--seed','914','--output',str(tmp_path/'case')]) == 0
    assert cli.main(['evaluate-case','--case',str(tmp_path/'case/case.json'),'--output',str(tmp_path/'evaluation')]) == 0


def test_numeric_mutation_is_detected_with_updated_integrity(reference_run, tmp_path):
    path, reference = reference_run
    run = tmp_path / "mutated"
    shutil.copytree(reference, run)
    state = json.loads((run / "state.json").read_text())
    store = EvidenceStore(run)
    original = state['comparisons']['c001']['artifact_id']
    table = store.read_table(original)
    table.loc[0, 'adjusted_p_value'] = .99
    replacement = store.table(table, 'comparison', aid='a999', relative='a999_comparison.csv', parent_ids=[original])
    state['artifacts'][replacement.id] = replacement.model_dump()
    state['comparisons']['c001']['artifact_id'] = replacement.id
    (run / 'state.json').write_text(json.dumps(state))
    report = evaluate_saved_case(load_case(path), path.parent, run)
    failed = [metric for stage in report.stages for metric in stage.metrics if metric.status == 'failed']
    assert report.status == 'failed' and any(metric.id == 'univariate.computation' for metric in failed)
    assert next(check for check in audit_run(run)['checks'] if check['id']=='evidence.integrity')['passed']


def test_wrong_design_blocks_preparation_evaluation(reference_run, tmp_path):
    path, run = reference_run
    case = load_case(path)
    case.stages[0].metrics[0].expected = 'paired'
    report = evaluate_saved_case(case, path.parent, run)
    assert report.stages[0].status == 'failed'
    assert report.stages[1].status == 'blocked_by_dependency'


def test_external_audit_rejects_fabricated_structured_claim(reference_run, tmp_path):
    _, original = reference_run
    run = tmp_path / 'false-claim'
    shutil.copytree(original, run)
    findings = json.loads((run / 'findings.json').read_text())
    findings['findings'][0]['claims'] = [{'comparison_id':'c001','metric':'significant_features','value':999}]
    (run / 'findings.json').write_text(json.dumps(findings))
    # Even a newly registered valid artifact cannot make the false number true.
    index = json.loads((run / 'evidence_index.json').read_text())
    for artifact in index['artifacts'].values():
        if artifact['path'] == 'findings.json':
            artifact['sha256'] = fingerprint(run / 'findings.json')
            artifact['bytes'] = (run / 'findings.json').stat().st_size
    (run / 'evidence_index.json').write_text(json.dumps(index))
    report = audit_run(run)
    assert report['status'] == 'failed'
    assert any(check['id'].startswith('report.structured_claim.') and not check['passed'] for check in report['checks'])


def test_runtime_rejects_wrong_number_with_valid_evidence(tmp_path):
    path = make_evaluation_example(tmp_path / 'case', 'independent', seed=915)
    data = json.loads(path.read_text())
    finish = data['script'][-1]
    finish['arguments']['findings'][0]['claims'] = [{'comparison_id':'c001','metric':'significant_features','value':999}]
    finish['expect_ok'] = False
    data['expected_execution_status'] = 'needs_input'
    path.write_text(json.dumps(data))
    report = evaluate_case(path, tmp_path / 'evaluation')
    assert not report.analysis_finished
    run = tmp_path / 'evaluation/scientific_run'
    assert not (run / 'report.md').exists()
    conversation = json.loads((run / 'conversation.json').read_text())
    errors = [json.loads(item['content']) for item in conversation if item['role']=='tool' and not json.loads(item['content'])['ok']]
    assert any('Quantitative claim disagrees' in item['error'] for item in errors)
    assert 'zero handling' not in conversation[-1].get('content','').lower()


def test_three_group_pca_preserves_population_and_scatter_labels(tmp_path):
    path = make_evaluation_example(tmp_path / 'case', 'pca_only', seed=916)
    document = json.loads(path.read_text())
    data_path, metadata_path = path.parent / 'input/data.csv', path.parent / 'input/metadata.csv'
    data, meta = pd.read_csv(data_path), pd.read_csv(metadata_path)
    rng = np.random.default_rng(916)
    extra = pd.DataFrame(np.exp(rng.normal(3, .25, (5,4))),columns=[f'feature_{i+1:02d}' for i in range(4)])
    extra['constant_feature'] = 5.
    extra['sample_id'] = [f'additional_sample_{i:04d}' for i in range(5)]
    data = pd.concat([data,extra],ignore_index=True)
    meta = pd.concat([meta,pd.DataFrame({'sample_id':extra.sample_id,'group':'PAN'})],ignore_index=True)
    data.to_csv(data_path,index=False,float_format='%.17g'); meta.to_csv(metadata_path,index=False)
    document['study'].update(analysis_scope='descriptive',reference_group=None,treatment_group=None,
                             selected_groups=['reference','PAN','treatment'])
    document['stages'][0]['metrics'][1]['expected'] = data.sample_id.tolist()
    for entry in document['dataset']['files']:
        entry['sha256'] = fingerprint(path.parent / 'input' / entry['path'])
    figure = {'kind':'pca_loadings_scatter','source_id':'pc001'}
    for step in document['script']:
        if step['tool']=='set_workflow_plan': step['arguments']['required_figures'].append('pca_loadings_scatter')
        if step['tool']=='set_figure_plan': step['arguments']['figures'].append(figure)
    insertion = max(i for i,step in enumerate(document['script']) if step['tool']=='make_plot')+1
    document['script'].insert(insertion,{'tool':'make_plot','arguments':figure,'expect_ok':True})
    path.write_text(json.dumps(document))
    report = evaluate_case(path,tmp_path / 'evaluation')
    assert report.status == 'passed',report.model_dump()
    run = tmp_path / 'evaluation/scientific_run'
    state = json.loads((run / 'state.json').read_text())
    assert state['pcas']['pc001']['samples']==21 and not state['comparisons']
    scatter = next(item for item in state['artifacts'].values() if item['kind']=='pca_loadings_scatter')
    assert scatter['selected_features'] == [f'feature_{i+1:02d}' for i in range(4)]
    assert 'Label' not in scatter['selected_features']


def test_pca_comparison_admits_signs_and_declared_degeneracy():
    a = pd.DataFrame({'feature':['a','b','c'],'PC1':[1.,0,0],'PC2':[0,1.,0]})
    flipped = a.copy(); flipped['PC1'] *= -1
    assert pca_weights_comparison(flipped,a,['feature'],['PC1','PC2'],[],Tolerance(atol=1e-12))[0]
    angle=.4
    rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    rotated=a.copy(); rotated[['PC1','PC2']] = a[['PC1','PC2']].to_numpy() @ rotation
    assert not pca_weights_comparison(rotated,a,['feature'],['PC1','PC2'],[],Tolerance(atol=1e-12))[0]
    assert pca_weights_comparison(rotated,a,['feature'],['PC1','PC2'],[['PC1','PC2']],Tolerance(atol=1e-12))[0]


def test_critical_gate_stops_downstream_execution(tmp_path):
    path = make_evaluation_example(tmp_path / 'case','independent',seed=917)
    data = json.loads(path.read_text())
    data['continuation_policy'] = 'stop_on_critical'
    data['stages'][0]['metrics'][0]['expected'] = 'paired'
    path.write_text(json.dumps(data))
    report = evaluate_case(path,tmp_path / 'evaluation')
    state = json.loads((tmp_path / 'evaluation/scientific_run/state.json').read_text())
    assert report.status == 'failed' and not state['datasets'] and not state['comparisons']


def test_expected_stop_is_evaluated_separately_from_scientific_completion(tmp_path):
    path=make_evaluation_example(tmp_path/'case','unsafe_log',seed=918)
    report=evaluate_case(path,tmp_path/'evaluation')
    assert report.status=='passed' and report.expected_outcome_match and not report.analysis_finished
    assert report.analysis_status=='needs_input'
    assert report.stages[-1].status=='blocked_by_dependency'


def test_open_analysis_accepts_registered_alternative(reference_run):
    path, run = reference_run
    case = load_case(path)
    case.mode = 'open_analysis'
    criterion = case.stages[0].metrics[0]
    criterion.validator = 'one_of'
    criterion.expected = ['independent','paired']
    assert evaluate_saved_case(case,path.parent,run).status=='passed'


def test_human_review_is_bound_to_exact_case_and_evidence(reference_run, tmp_path):
    from metabolomics_agent.evaluation_models import ReferenceEntry
    path, run = reference_run
    case = load_case(path)
    review_path = tmp_path/'review.json'
    review = {'case_id':case.case_id,'metric_id':'reporting.scientific_review','reviewer':'Synthetic reviewer (test fixture)',
              'reviewed_on':'2026-11-11','evidence_index_sha256':fingerprint(run/'evidence_index.json'),
              'status':'passed','explanation':'Recorded test review of the declared scope only.'}
    review_path.write_text(json.dumps(review))
    entry = ReferenceEntry(id='review',path='review.json',sha256=fingerprint(review_path),origin='collaborator',
        source='Test fixture, not a real scientific review.',producer='human fixture',review_status='reviewed',independence='Independent test reviewer.')
    case.references.entries.append(entry)
    case.stages[-1].metrics[-1].reference_id = 'review'
    report = evaluate_saved_case(case,tmp_path,run)
    assert report.scientific_review=='reviewed'
    review['evidence_index_sha256']='0'*64
    review_path.write_text(json.dumps(review)); entry.sha256=fingerprint(review_path)
    report = evaluate_saved_case(case,tmp_path,run)
    assert report.scientific_review=='pending'
    assert report.stages[-1].metrics[-1].status=='not_assessed'
