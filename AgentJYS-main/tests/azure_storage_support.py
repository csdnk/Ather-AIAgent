"""Explicit opt-in real Redis fixtures; never disable TLS to cross a test relay."""

import os
import socket
from uuid import uuid4

import pytest


@pytest.fixture
def azure_redis(monkeypatch):
    import redis

    host = os.environ.get("P3_TEST_REDIS_HOST")
    if not host:
        if os.environ.get("P3_REQUIRE_REDIS") == "1":
            pytest.fail("required real Redis test configuration is missing")
        pytest.skip("real Redis requires P3_TEST_REDIS_HOST and dedicated test credentials")
    connect_host = os.environ.get("P3_TEST_REDIS_CONNECT_HOST")
    if connect_host:
        original = socket.getaddrinfo

        def resolve(name, *args, **kwargs):
            return original(connect_host if name == host else name, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
    client = redis.Redis(
        host=host,
        port=int(os.environ.get("P3_TEST_REDIS_PORT", "6380")),
        password=os.environ.get("P3_TEST_REDIS_PASSWORD"),
        ssl=True,
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        ssl_ca_certs=os.environ.get("P3_TEST_REDIS_CA_FILE"),
        socket_connect_timeout=5,
        socket_timeout=5,
        decode_responses=False,
    )
    namespace = "test-" + uuid4().hex
    try:
        try:
            assert client.ping()
        except Exception as error:
            pytest.fail(f"real Redis connection failed: {type(error).__name__}")
        yield client, namespace
    finally:
        # This prefix was minted by this fixture. Never flush shared databases.
        try:
            for key in client.scan_iter(match=f"aether:{namespace}*:body:*"):
                client.delete(key)
        finally:
            client.close()
