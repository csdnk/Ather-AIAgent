import importlib.util
from pathlib import Path
import pytest

def mod():
    p=Path(__file__).parents[2]/'scripts/quality/post_deploy.py'
    s=importlib.util.spec_from_file_location('post_deploy',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def test_candidate_admission_rejects_unpinned_or_nonready_deployment():
    m=mod();digest='sha256:'+'a'*64
    deployment={'metadata':{'generation':4},'spec':{'replicas':1,'template':{'spec':{'containers':[{'name':'p3','image':'repo@'+digest}]}}},'status':{'observedGeneration':4,'updatedReplicas':1,'readyReplicas':1,'availableReplicas':1}}
    pod={'metadata':{'deletionTimestamp':None},'status':{'containerStatuses':[{'name':'p3','ready':True,'imageID':'repo@'+digest}]}}
    assert m.admitted(deployment,[pod],digest)
    assert not m.admitted(deployment,[pod],'latest')
    deployment['status']['readyReplicas']=0
    assert not m.admitted(deployment,[pod],digest)

def test_failed_or_missing_cleanup_blocks_next_owned_run(tmp_path):
    m=mod();r=tmp_path/'r';r.mkdir();(r/'journal.json').write_text('{"result":"FAIL","cleanup":{"status":"PASS"},"accounts":[{"cleanup":"retained_for_reconciliation"}]}')
    assert not m.clean_previous(tmp_path)
    (r/'reconciliation.json').write_text('{"cleanup":{"status":"PASS"},"accounts":[{"cleanup":"deleted_and_absence_verified"}]}')
    assert m.clean_previous(tmp_path)

def test_lost_supervisor_journal_keeps_lock_and_blocks_new_work(tmp_path,monkeypatch):
    m=mod();root=tmp_path/'runs'
    monkeypatch.setattr(m,'candidate',lambda:{'sha':'test'})
    monkeypatch.setattr(m,'inspect',lambda c:(True,{}))
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:None)
    result=m.execute({'runs_root':str(root),'python':'python','credentials':'private','native':'native'},tmp_path/'out')
    assert result['cleanup_pending'] is True
    assert (root/'active-run.lock').exists()
