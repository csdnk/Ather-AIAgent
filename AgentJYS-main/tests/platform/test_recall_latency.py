"""Recall latency must not include source expansion or model generation."""

import pytest
from test_directory import directory  # noqa: F401

from aether_platform.chat import Conversations
from aether_platform.operations.performance import performance
from aether_platform.p3 import P3Error


@pytest.mark.parametrize("failure", [None, "recall", "validation"])
def test_measures_verified_recall_before_source_and_model_work(directory, monkeypatch, failure):  # noqa: F811
    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    actor = db.authenticate("https://id.test", "a1")
    cid = chat.create(actor)["id"]
    chat.begin(actor, cid, "t", "项目安排")
    clock = [100.0]

    class P3:
        def __init__(self, *args, **kwargs):
            self.http = self

        def identity(self):
            clock[0] += 10

        def close(self):
            pass

        def recall(self, *args):
            clock[0] += 0.2
            if failure == "recall":
                raise P3Error("CONNECTION_UNCONFIRMED")
            return {"recall_id": "r"}

        def call(self, *args):
            clock[0] += 0.05
            if failure == "validation":
                raise P3Error("RESULT_INVALIDATED")
            return {
                "recall_id": "r",
                "outcome": "empty",
                "groups": [],
                "rendered_context": "",
                "degradation_reasons": [],
            }

        def source_excerpts(self, pack):
            clock[0] += 20
            # A downstream failure must not erase an already returned Recall sample.
            raise ValueError("source expansion failed after Recall returned")

    monkeypatch.setattr("aether_platform.chat.P3Client", P3)
    monkeypatch.setattr("aether_platform.chat.time.perf_counter", lambda: clock[0])
    chat.generate(
        actor,
        cid,
        "t",
        {"api_key": "test", "endpoint": "test"},
        1,
        {"base_url": "http://127.0.0.1:19020"},
        "test",
    )
    summary = performance(db, db.authenticate("https://id.test", "aa"))["windows"]["1h"]["summary"]
    assert summary["recall_latency_samples"] == (0 if failure else 1)
    if failure:
        assert summary["recall_return_p95_ms"] is None
    else:
        assert summary["recall_return_p95_ms"] == pytest.approx(250)


def test_recall_statistics_scope_percentile_missing_history_and_retry_fence(directory):  # noqa: F811
    db, _, _ = directory
    chat = Conversations(db)
    chat.migrate()
    for uid, durations in (("a1", [100, 200, None]), ("b1", [10000])):
        actor = db.authenticate("https://id.test", uid)
        cid = chat.create(actor)["id"]
        for i, duration in enumerate(durations):
            tid = uid + str(i)
            chat.begin(actor, cid, tid, "query")
            if duration is not None:
                assert chat.record_recall_latency(actor, cid, tid, 1, duration)
            chat.finish(actor, cid, tid, "answer")
    tenant = performance(db, db.authenticate("https://id.test", "aa"))["windows"]["24h"]["summary"]
    assert tenant["recall_latency_samples"] == 2
    assert tenant["recall_return_p95_ms"] == pytest.approx(195)
    platform = performance(db, db.authenticate("https://id.test", "root"))["windows"]["24h"][
        "summary"
    ]
    assert platform["recall_latency_samples"] == 3
    assert platform["recall_return_p95_ms"] == pytest.approx(9020)
    # Retry clears its previous timing and stale workers cannot replace the new attempt.
    chat.begin(actor, cid, "retry", "query")
    assert chat.record_recall_latency(actor, cid, "retry", 1, 300)
    chat.finish(actor, cid, "retry", "", "failed")
    chat.begin(actor, cid, "retry", "query")
    assert not chat.record_recall_latency(actor, cid, "retry", 1, 99999)
    with db.connection() as conn:
        assert (
            conn.execute("SELECT recall_return_ms FROM chat_turns WHERE id='retry'").fetchone()[
                "recall_return_ms"
            ]
            is None
        )
    assert chat.record_recall_latency(actor, cid, "retry", 2, 400)
