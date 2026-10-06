from aether_platform.operations.collector import classify_probe, validate_probe


def test_probe_rejects_public_plaintext_credentials_and_arbitrary_schemes():
    for value in (
        "file:///etc/passwd",
        "http://public.example/",
        "https://user:pass@example.org",
        "http://169.254.169.254/metadata",
    ):
        assert not validate_probe(value)
    assert validate_probe("https://example.org/health")
    assert validate_probe("http://aether-agent-p3.aether-p3-demo.svc.cluster.local:8080/p3/readyz")


def test_probe_status_requires_success_and_ignores_response_body():
    assert classify_probe(200) == 0
    assert classify_probe(503) == 1
    assert classify_probe(302) == 1
    assert classify_probe(None) == 1
