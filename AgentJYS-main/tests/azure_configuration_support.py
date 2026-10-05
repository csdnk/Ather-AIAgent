"""Complete public Azure settings for configuration-only contract tests."""

import json


def settings(tmp_path, profile="development"):
    recall = tmp_path / "recall.json"
    recall.write_text(json.dumps({"rerank_policy": "disabled"}), encoding="utf-8")
    ca = tmp_path / "ca.pem"
    ca.write_text("contract test CA path; no TLS handshake in configuration tests")
    return {
        "profile": profile,
        "storage_mode": "azure",
        "metadata_backend": "postgresql",
        "data_dir": tmp_path / "data",
        "identity_file": tmp_path / "identities.yaml",
        "embedding_profile": "native",
        "embedding_config": tmp_path / "embedding.json",
        "recall_config": recall,
        "language_model": {"model": "approved-model"},
        "temporal": {
            "endpoint": "127.0.0.1:7233", "namespace": "default",
            "deployment_id": "p3-" + profile,
        },
        "azure_storage": {
            "namespace": profile,
            "postgres": {"dsn_env": "TEST_P3_POSTGRES_DSN", "schema_name": "p3_" + profile},
            "redis": {
                "host": "redisp3.redis.cache.windows.net",
                "password_env": "TEST_REDIS_PASSWORD", "ca_file": ca,
            },
            "milvus": {
                "uri": "https://milvus.internal:19530", "database": "p3_" + profile,
                "collection": "memories", "token_env": "TEST_MILVUS_TOKEN",
                "ca_file": ca, "server_name": "milvus.internal",
            },
            "ceph": {
                "endpoint": "https://ceph.example", "bucket": "p3-memory",
                "access_key_env": "TEST_CEPH_ACCESS", "secret_key_env": "TEST_CEPH_SECRET",
            },
        },
    }
