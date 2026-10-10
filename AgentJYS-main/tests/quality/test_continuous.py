import importlib.util,json
from pathlib import Path
import pytest

def module():
    path=Path(__file__).parents[2]/'scripts/quality/continuous.py'
    spec=importlib.util.spec_from_file_location('continuous',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_missing_and_empty_test_evidence_never_pass(tmp_path):
    m=module()
    assert m.junit_rows(tmp_path/'missing.xml','unit')[0]['status']=='BLOCKED'
    f=tmp_path/'empty.xml';f.write_text('<testsuite tests="0"/>')
    assert m.junit_rows(f,'unit')[0]['status']=='BLOCKED'

def test_junit_keeps_first_failure_and_explicit_skip(tmp_path):
    m=module();f=tmp_path/'test.xml'
    f.write_text('<testsuite><testcase classname="a" name="pass"/><testcase classname="a" name="fail"><failure message="broken"/></testcase><testcase classname="a" name="skip"><skipped/></testcase><testcase classname="a" name="error"><error/></testcase></testsuite>')
    assert [r['status'] for r in m.junit_rows(f,'unit')]==['PASS','FAIL','NOT_RUN','BLOCKED']

def test_live_failure_and_cleanup_remain_separate():
    m=module();j={'run_id':'r','result':'FAIL','cleanup':{'status':'PASS','physical_restored':False},'accounts':[{'cleanup':'deleted_and_absence_verified'}]}
    rows=m.live_rows(j,{'requests':31,'steps':[]})
    assert [r['status'] for r in rows]==['FAIL','PASS','PASS']
    assert m.verdict(rows)=='FAIL'
    assert m.verdict([{'status':'PASS'},{'status':'NOT_RUN'}])=='BLOCKED'

def test_report_is_machine_readable_and_never_exposes_credentials(tmp_path):
    m=module();rows=[{'id':'u','layer':'unit','status':'PASS','reason':'one test'}]
    m.emit(tmp_path,rows,{'sha':'abc','dirty':True},'commit')
    report=json.loads((tmp_path/'summary.json').read_text(encoding='utf-8'))
    assert report['execution_status']=='PASS'
    assert report['release_gate']=='BLOCKED'
    assert (tmp_path/'agent-report.md').exists()
    assert (tmp_path/'results.xml').exists()
    with pytest.raises(FileExistsError):m.emit(tmp_path,rows,{},'commit')

def test_release_only_checks_do_not_block_a_completed_deployment_regression():
    m=module()
    assert m.verdict([{'status':'PASS'},{'status':'NOT_RUN','required':False}])=='PASS'
