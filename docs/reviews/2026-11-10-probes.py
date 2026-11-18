import json, tempfile, sys
from pathlib import Path
from metabolomics_agent.agent import Agent
from metabolomics_agent.examples import generate_example
from metabolomics_agent.llm import DemoProvider
from metabolomics_agent.models import LLMConfig, StudyConfig
from metabolomics_agent.state import RunState
from metabolomics_agent.tools import ToolRegistry
from metabolomics_agent.data import load_study
sys.path.insert(0, str(Path.cwd()/'tests'))
from test_workflow import coordinate, prepare_staged, compare_staged, assess
from conftest import call
root = Path(tempfile.mkdtemp(prefix='metabolomics-review-'))
outputs = {'root':str(root)}
study = generate_example(root/'inputs')
cfg = StudyConfig.model_validate_json(study.read_text())
state = RunState(cfg, study.parent, root/'default-budget')
outputs['default_budget'] = Agent(state, DemoProvider(), LLMConfig(model='offline')).run(cfg.objective, progress=lambda _:None)
outputs['default_budget']['stage'] = state.workflow.stage
outputs['default_budget']['last_tool'] = next((x.get('tool') for x in reversed([json.loads(line) for line in (state.output_dir/'events.jsonl').read_text().splitlines()]) if x['kind']=='tool_result'),None)
tools = ToolRegistry(RunState(cfg, study.parent, root/'sensitivity'))
coordinate(tools, figures=[])
prepare_staged(tools, imputation='median', transform='log2')
_, first = compare_staged(tools)
assess(tools)
call(tools,'request_revision',{'target':'03_univariate','rationale':'Assess robustness with a separate rank-based sensitivity analysis.'})
_, second = compare_staged(tools,test='mannwhitney')
assess(tools)
outputs['sensitivity'] = {'first':first['comparison_id'],'second':second['comparison_id'],'current':tools.state.workflow.data.comparison_ids, 'read_primary':tools.execute('read_result',{'artifact_id':first['artifact_id']}),'primary_in_evidence_ids':first['comparison_id'] in tools.state.workflow.evidence_ids()}
outputs['fabricated_claim'] = tools.execute('finish_analysis',{'findings':[{'text':'There are exactly 999 statistically significant features and this proves a causal mechanism.','evidence_ids':[second['comparison_id']]}],'limitations':['Synthetic review probe.']})
outputs['actual_significant_features'] = tools.state.comparisons[second['comparison_id']]['significant_features']
outputs['fabricated_claim_in_report'] = '999 statistically significant' in (tools.state.output_dir/'report.md').read_text()
duplicate = root/'duplicate.csv'
duplicate.write_text('sample_id,group,lipid,lipid\nS1,N,1,2\nS2,N,2,3\nS3,N,3,4\nS4,T,4,5\nS5,T,5,6\nS6,T,6,7\n')
x,_,_,_ = load_study(StudyConfig(data_path='duplicate.csv',reference_group='N',treatment_group='T'),root)
outputs['duplicate_headers'] = list(x.columns)
(root/'probes.json').write_text(json.dumps(outputs,indent=2))
print(json.dumps(outputs,indent=2))
