from labs.recall_walkthrough import run_demo


def test_framework_fusion_does_not_imply_version_validity():
    result = run_demo()
    before = {(r['memory_id'],r['version']) for r in result['fusion_before_filter']}
    after = {(r['memory_id'],r['version']) for r in result['after_explicit_filter']}
    assert ('m_coffee', 1) in before
    assert ('m_coffee', 1) not in after
    assert ('m_coffee', 2) in after
    assert result['remote_model_calls'] == 0
    assert result['milvus_tested'] is False
