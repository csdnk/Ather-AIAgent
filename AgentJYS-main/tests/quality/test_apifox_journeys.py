import importlib.util
from pathlib import Path


def load():
    path = Path(__file__).parents[2] / 'scripts/quality/apifox_journeys.py'
    spec = importlib.util.spec_from_file_location('journeys', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_business_flows_have_visible_dependent_steps():
    flows = load().definitions()
    assert len(flows) >= 7
    for flow in flows:
        assert len(flow['stages']) >= 3
        assert flow['stages'][0]['first']
        assert flow['stages'][-1]['last']
    memory = next(f for f in flows if f['key'] == 'memory-correct')
    actions = [s['action'] for s in memory['stages']]
    assert all(a in actions for a in ['login', 'identity', 'p3identity', 'save', 'correct', 'recall', 'delete', 'deleted', 'logout'])
    assert 'poll' in actions


def test_scripts_do_not_hide_business_requests_in_callbacks():
    engine = load().ENGINE
    assert 'pm.sendRequest' not in engine
    assert 'FULL_STACK_ADMITTED = false' in engine
    assert 'PERFORMANCE_ADMITTED = false' in engine
    assert 'pm.environment.set' not in engine


def test_source_ids_are_not_hard_coded_for_new_scenarios():
    for f in load().definitions():
        assert 'id' not in f
        for stage in f['stages']:
            assert 'id' not in stage


def test_negative_input_and_forged_claims_have_authenticated_controls_and_cleanup():
    flows = {f['key']: f for f in load().definitions()}
    boundaries = flows.get('identity-input-boundaries')
    forged = flows.get('identity-forged-claims')
    assert boundaries is not None and forged is not None
    for flow in (boundaries, forged):
        assert flow['stages'][0]['action'] == 'login'
        assert any(s['action'] == 'identity' for s in flow['stages'])
        assert flow['stages'][-2]['action'] == 'logout'
    assert len([s for s in boundaries['stages'] if 'payload' in s]) >= 10
    assert any(s.get('headers', {}).get('Authorization') for s in forged['stages'])
