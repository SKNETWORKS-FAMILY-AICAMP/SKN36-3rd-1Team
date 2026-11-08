"""pytest -q tests/unit/test_eligibility.py"""
from copy import deepcopy
from backend.eligibility.checker import check_eligibility


def make_policy(logic='AND', value='19~35', *, extra=None, quality=None):
    condition = {'type':'AGE','operator':'BETWEEN','value':value,'condition_id':'COND_AGE'}
    root = {'group_id':'ROOT','logic':logic,'conditions':[condition], 'groups':extra or []}
    return {'policy_id':'POL_TEST','graph_status':'FOUND',
            'graph':{'policy':{'application_period':'상시신청'},'eligibility':{'condition_tree':[root], 'unknown_logic_groups':[], 'structure_issues':[]}},
            'data_quality':quality or {'hold_auto_eligibility':False,'affected_attributes':[]}}


def states(result, field):
    return [c['status'] for c in result['conditions'] if c['field']==field]


def test_age_range_boundaries():
    p=make_policy()
    assert states(check_eligibility({'age':19},p),'age')==['eligible']
    assert states(check_eligibility({'age':35},p),'age')==['eligible']
    assert states(check_eligibility({'age':36},p),'age')==['ineligible']
    assert states(check_eligibility({'age':None},p),'age')==['needs_check']


def test_unknown_and_missing_graph():
    p=make_policy('UNKNOWN')
    assert all(c['status']=='needs_check' for c in check_eligibility({'age':25},p)['conditions'])
    p['graph']=None; p['graph_status']='MISSING_IN_GRAPH'
    assert states(check_eligibility({'age':25},p),'eligibility')==['needs_check']


def test_or_not_three_valued_logic():
    child={'group_id':'OTHER','logic':'AND','conditions':[{'type':'INCOME','value':'중위소득_0_50_'}],'groups':[]}
    p=make_policy('OR',extra=[child])
    assert states(check_eligibility({'age':25},p),'eligibility')[-1]=='eligible'
    assert states(check_eligibility({'age':50},p),'eligibility')[-1]=='needs_check'
    p=make_policy('NOT')
    assert states(check_eligibility({'age':25},p),'eligibility')[-1]=='ineligible'
    assert states(check_eligibility({'age':50},p),'eligibility')[-1]=='eligible'


def test_quality_only_region_held_and_age_preserved():
    p=make_policy(quality={'hold_auto_eligibility':True,'affected_attributes':['region']})
    assert states(check_eligibility({'age':25},p),'age')==['eligible']
    assert states(check_eligibility({'age':25},p),'region')==['needs_check']


def test_quality_age_or_all_held():
    for affected in (['age'],['all'],[]):
        p=make_policy(quality={'hold_auto_eligibility':True,'affected_attributes':affected})
        assert states(check_eligibility({'age':25},p),'age')==['needs_check']


def test_bad_age_and_structural_warning():
    p=make_policy(value='19~bad')
    assert states(check_eligibility({'age':25},p),'age')==['needs_check']
    p=make_policy()
    p['graph']['eligibility']['structure_issues']=[{'issue':'cycle'}]
    assert all(c['status']=='needs_check' for c in check_eligibility({'age':25},p)['conditions'])


def test_period_text_and_no_mutation():
    p=make_policy(); original=deepcopy(p)
    result=check_eligibility({'age':25},p)
    assert result['reception_status']=='unknown'
    assert p==original


def test_income_is_never_decided():
    p=make_policy()
    p['graph']['eligibility']['condition_tree'][0]['conditions']=[{'type':'INCOME','value':'중위소득_0_50_'}]
    assert states(check_eligibility({'age':25,'income':1000},p),'income')==['needs_check']
