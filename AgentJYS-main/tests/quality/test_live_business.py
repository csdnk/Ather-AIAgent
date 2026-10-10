import importlib.util
from pathlib import Path
import pytest

path=Path(__file__).parents[2]/'scripts/quality/live_business.py'
def module():
    spec=importlib.util.spec_from_file_location('live_business',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_cleanup_never_accepts_another_users_or_sessions_object():
    m=module()
    owned={'memory_id':'m1','scope':{'user_id':'ry_user_123','tenant_id':'t1','session_id':'run1'}}
    assert m.owns(owned,'ry_user_123','t1','run1')
    assert not m.owns(owned,'ry_user_999','t1','run1')
    assert not m.owns(owned,'ry_user_123','t1','other')

def test_cleanup_receipt_pending_is_not_physical_restoration():
    m=module()
    assert not m.cleanup_complete({'cleanup_state':'pending','remaining_targets':[]})
    assert not m.cleanup_complete({'cleanup_state':'completed','remaining_targets':['ceph']})
    assert m.cleanup_complete({'cleanup_state':'completed','remaining_targets':[]})

def test_account_cleanup_does_not_remove_unknown_names():
    m=module()
    assert m.owned_account('aetherqaabcdef012345a','abcdef012345')
    assert not m.owned_account('aetherusera','abcdef012345')
    assert not m.owned_account('aetherqa111111111111a','abcdef012345')

def test_started_runner_requires_an_intact_checkpoint(tmp_path):
    m=module();p=tmp_path/'checkpoint.json'
    assert m.read_checkpoint(p,False)=={}
    with pytest.raises(ValueError):m.read_checkpoint(p,True)
    p.write_text('{broken')
    with pytest.raises(ValueError):m.read_checkpoint(p,True)
    p.write_text('{"run":"run1","submitted":false,"owned_memories":[]}')
    with pytest.raises(ValueError):m.read_checkpoint(p,True)

def test_overall_never_passes_when_cleanup_or_account_restoration_is_pending():
    m=module()
    state={'result':'PASS','cleanup':{'status':'PASS'},'accounts':[{'cleanup':'deleted_and_absence_verified'}]}
    assert m.overall(state)=='PASS'
    state['accounts'][0]['cleanup']='retained_for_reconciliation'
    assert m.overall(state)=='BLOCKED'
    state['accounts'][0]['cleanup']='deleted_and_absence_verified';state['cleanup']['status']='BLOCKED'
    assert m.overall(state)=='BLOCKED'

def test_processing_cleanup_evidence_is_bound_to_owned_memory():
    m=module();ref={'memory_id':'m1','scope':{'user_id':'u','tenant_id':'t','session_id':'r'}}
    p={'memory':ref,'tasks':[{'task_id':'c','kind':'remember.cleanup','state':'succeeded'}]}
    assert m.processing_cleanup_tasks(p,'u','t','r')=={'c':'succeeded'}
    with pytest.raises(ValueError):m.processing_cleanup_tasks(p,'other','t','r')

def test_contract_restoration_does_not_claim_physical_erasure():
    m=module()
    assert m.restoration_status(True,{'a':'succeeded'})=='PASS'
    assert m.restoration_status(True,{'a':'running'})=='BLOCKED'
    assert m.restoration_status(True,{'a':'failed'})=='FAIL'
    assert m.restoration_status(False,{'a':'succeeded'})=='FAIL'

def test_cleanup_accepts_owned_derived_gone_and_preserves_retention(tmp_path):
    m=module()
    ref={'memory_id':'m1','scope':{'user_id':'u','tenant_id':'t','session_id':'r'}}
    source={'source_id':'s1'}
    cp={'run':'r','submitted':True,'owned_memories':[ref],'owned_sources':[source],'source_bindings':[{'source':source,'memory':ref}]}
    class Fake:
        def p3(self,method,path,*args):
            if path=='/p3/sources/s1':return 200,{'source_id':'s1','revision':1,'valid':False}
            if path.endswith('/delete'):return 200,{'blocked':True,'cleanup_state':'pending','task_ids':['c1'],'remaining_targets':['retained_source_policy']}
            if path.endswith('/body'):return 200,{'outcome':'excluded','content':None}
            if path.endswith('/processing'):return 200,{'memory':ref,'memory_status':'deleted','source_retention':'retained','physical_erasure':False,'derived_memory_ids':['d1'],'tasks':[{'task_id':'c1','kind':'remember.cleanup','state':'succeeded'}]}
            if path=='/p3/remember/d1':return 410,{'code':'MEMORY_GONE'}
            raise AssertionError(path)
    got=m.cleanup(Fake(),'token',cp,'u','t',tmp_path/'cleanup.json')
    assert got['status']=='PASS'
    assert got['logical_restored'] is True
    assert got['physical_restored'] is False
    assert got['source_retention']=='retained'

def test_unknown_account_create_with_empty_lookup_is_not_restored():
    m=module()
    assert m.account_recovery_state([],submitted=True)=='retained_for_reconciliation'
    assert m.account_recovery_state([],submitted=False)=='not_created_verified'
    assert m.account_recovery_state([{'id':123}],submitted=True)=='found'
