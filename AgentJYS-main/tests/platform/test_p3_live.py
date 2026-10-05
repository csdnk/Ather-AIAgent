"""Opt-in real Agent-to-P3 acceptance; no mocked memory service."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_bff_live import ORIGIN, app, authenticate, csrf  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv("AETHER_P3_LIVE") != "1", reason="explicit live P3 run required"
)


def test_real_p3_login_scopes_and_save(app):  # noqa: F811
    with TestClient(app, base_url=ORIGIN) as a, TestClient(app, base_url=ORIGIN) as b:
        assert authenticate(a, "user_a").status_code == 200
        assert authenticate(b, "user_b").status_code == 200
        response = a.get("/memory-api/status")
        assert response.status_code == 200, response.text
        assert response.json()["connected"]
        key = "accept_" + uuid4().hex
        response = a.post(
            "/memory-api/commands",
            headers=csrf(a),
            json={
                "command_id": key,
                "action": "save",
                "params": {"text": "验收事实：星港项目的交付日期是十一月十七日。"},
            },
        )
        assert response.status_code == 200, response.text
        receipt = response.json()["result"]
        assert receipt["saved"]
        mid = receipt["memories"][0]["memory_id"]
        assert a.get("/memory-api/memories/" + mid).status_code == 200
        assert b.get("/memory-api/memories/" + mid).status_code in {403, 404}
        assert (
            a.post(
                "/memory-api/commands",
                headers=csrf(a),
                json={
                    "command_id": key + "bad",
                    "action": "save",
                    "params": {"text": "attack", "tenant_id": "demo_tenant_b"},
                },
            ).status_code
            == 422
        )


@pytest.fixture(scope="module")
def live_client():
    from pathlib import Path

    from aether_platform.auth.bff import create_lab_app

    application = create_lab_app(Path(os.environ["AETHER_PLATFORM_LAB_CONFIG"]))
    with TestClient(application, base_url=ORIGIN) as client:
        assert authenticate(client, "user_a").status_code == 200
        yield client


def command(client, action, params=None, mid=None, **extra):
    body = {"command_id": "live_" + uuid4().hex, "action": action, "params": params or {}, **extra}
    if mid:
        body["memory_id"] = mid
    for _ in range(5):
        response = client.post("/memory-api/commands", headers=csrf(client), json=body)
        if response.status_code != 409 or "VERSION_CONFLICT" not in response.text or not mid:
            break
        # Real background indexing can advance the CAS revision between reads.
        import time

        time.sleep(2)
        current = detail(client, mid)
        if "expected_version" in body["params"]:
            body["params"]["expected_version"] = current["ref"]["version"]
        if "expected_object_revision" in body["params"]:
            body["params"]["expected_object_revision"] = current["object_revision"]
        if "expected_revision" in body["params"]:
            body["params"]["expected_revision"] = (
                current["source_metadata"][0]["revision"]
                if action.startswith("source_")
                else current["object_revision"]
            )
        body["command_id"] = "live_" + uuid4().hex
    assert response.status_code == 200, (action, response.text)
    return response.json()["result"]


def saved(client, text="验收资料：星港项目由林晓负责，交付日期为十一月十七日。", **extra):
    result = command(client, "save", {"text": text}, **extra)
    assert result["saved"]
    return result["memories"][0]["memory_id"]


def detail(client, mid):
    response = client.get("/memory-api/memories/" + mid)
    assert response.status_code == 200, response.text
    return response.json()


def wait_ready(client, mid):
    import time

    end = time.monotonic() + 120
    while time.monotonic() < end:
        current = detail(client, mid)
        if current["projection_state"] == "ready":
            return current
        time.sleep(2)
    pytest.fail("Memory never became searchable: " + str(current["projection_state"]))


def test_real_working_recall_and_body_ranges(live_client):
    c = live_client
    session = c.post("/chat-api/conversations", headers=csrf(c)).json()["id"]
    mid = saved(c, session_id=session)
    wait_ready(c, mid)
    pack = command(
        c,
        "recall",
        {"text": "星港项目的交付日期是什么？", "sources": "working"},
        session_id=session,
    )
    assert pack["outcome"] == "available", pack
    assert "十一月十七日" in pack["rendered_context"]
    for view in ["body", "range", "source", "processing", "retention"]:
        result = c.get("/memory-api/memories/" + mid + "/" + view)
        assert result.status_code == 200, (view, result.text)
    assert c.get("/memory-api/recalls/" + pack["recall_id"]).status_code == 200


def test_real_correction_lifecycle_retention_and_delete(live_client):
    c = live_client
    mid = saved(c, "验收资料：远帆项目的会议时间是周一。")
    item = wait_ready(c, mid)
    command(
        c,
        "retention",
        {
            "expected_version": item["ref"]["version"],
            "expected_object_revision": item["object_revision"],
            "enabled": True,
            "archive_after_idle_hours": 168,
            "completed": True,
            "legal_hold": False,
            "reason": "验收策略",
        },
        mid,
    )
    item = detail(c, mid)
    command(
        c,
        "correct",
        {
            "expected_version": item["ref"]["version"],
            "expected_object_revision": item["object_revision"],
            "content": "验收资料：远帆项目会议改为周二。",
            "reason": "验收更正",
        },
        mid,
    )
    item = detail(c, mid)
    assert item["ref"]["version"] > 1
    assert "周二" in item["content"]
    for action, target in [("archive", "archived"), ("activate", "active")]:
        item = detail(c, mid)
        command(
            c,
            action,
            {
                "expected_version": item["ref"]["version"],
                "expected_object_revision": item["object_revision"],
                "reason": "验收生命周期",
            },
            mid,
        )
        assert detail(c, mid)["status"] == target
    command(c, "reindex", mid=mid)
    command(c, "reprocess", mid=mid)
    item = detail(c, mid)
    receipt = command(
        c, "delete", {"expected_revision": item["object_revision"], "reason": "清理验收记忆"}, mid
    )
    assert receipt["blocked"]
    assert c.get("/memory-api/memories/" + mid + "/body").status_code in {404, 410}


def test_real_document_import_and_sources(live_client):
    import io

    from docx import Document
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    c = live_client
    word = Document()
    word.add_paragraph("文档验收：松林项目的负责人是陈墨。")
    data = io.BytesIO()
    word.save(data)
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Acceptance: Orion delivery is November 17.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    pdf = io.BytesIO()
    writer.write(pdf)
    for media, content in [
        ("text/plain", "文档验收：蓝桥项目预算为三万元。".encode()),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data.getvalue(),
        ),
        ("application/pdf", pdf.getvalue()),
    ]:
        result = c.post(
            "/memory-api/documents",
            headers={**csrf(c), "content-type": media, "x-upload-id": uuid4().hex},
            content=content,
        )
        assert result.status_code == 200, (media, result.text)
        assert result.json()["result"]["saved"]


def test_real_reflection_and_source_revocation(live_client):
    c = live_client
    current = c.get("/memory-api/reflection").json()
    result = command(
        c,
        "reflection",
        {
            "expected_revision": current["revision"],
            "enabled": True,
            "reason": "验收反思设置",
            "min_episodes": 2,
            "max_episodes": 4,
        },
    )
    assert result["revision"] > current["revision"]
    for action in ["source_revoke", "source_delete"]:
        mid = saved(c, "一次性来源验收：灯塔项目使用青色标识。")
        item = detail(c, mid)
        result = command(
            c,
            action,
            {
                "expected_revision": item["source_metadata"][0]["revision"],
                "reason": "撤回一次性验收来源",
            },
            mid,
        )
        assert result["blocked"]


def test_real_same_tenant_user_and_admin_cannot_read_private_memory(live_client):
    from pathlib import Path

    from aether_platform.auth.bff import create_lab_app

    c = live_client
    mid = saved(c)
    otherapp = create_lab_app(Path(os.environ["AETHER_PLATFORM_LAB_CONFIG"]))
    with TestClient(otherapp, base_url=ORIGIN) as other:
        for username in ["user_a2", "user_b", "tenant_admin_a", "platform_admin"]:
            response = authenticate(other, username)
            if "admin" in username:
                assert response.status_code == 403
                continue
            assert response.status_code == 200
            assert other.get("/memory-api/memories/" + mid).status_code in {403, 404}


def test_real_consolidation_distillation_long_term_and_invalidated_result(live_client):
    import time

    c = live_client
    session = c.post("/chat-api/conversations", headers=csrf(c)).json()["id"]
    text = "验收事件：2026年10月5日上午，林晓参加了青岚项目评审会议，确认下次会议时间为周四。"
    receipt = command(c, "save", {"text": text, "category": "event"}, session_id=session)
    mid = receipt["memories"][0]["memory_id"]
    command(c, "consolidate", session_id=session)
    end = time.monotonic() + 150
    episodes = []
    while time.monotonic() < end:
        catalog = c.get("/memory-api/memories").json()["items"]
        episodes = [
            i
            for i in catalog
            if i["kind"] == "episodic"
            and i["ref"]["scope"].get("session_id") == session
            and i["projection_state"] == "ready"
        ]
        if episodes:
            break
        time.sleep(3)
    assert episodes, "Consolidation produced no searchable episodic memory"
    distilled = command(c, "distill", memory_ids=[i["ref"]["memory_id"] for i in episodes])
    assert distilled["task_id"]
    result = c.get("/memory-api/operations/" + distilled["task_id"])
    assert result.status_code == 200, result.text
    pack = command(c, "recall", {"text": "青岚项目下次会议是什么时候？"}, session_id=session)
    assert pack["outcome"] in {"available", "degraded"}, pack
    assert set(pack["degradation_reasons"]) <= {"long_term_index_pending"}, pack
    assert "周四" in pack["rendered_context"]
    item = detail(c, mid)
    command(
        c,
        "source_revoke",
        {"expected_revision": item["source_metadata"][0]["revision"], "reason": "检索失效验收"},
        mid,
    )
    result = c.get("/memory-api/recalls/" + pack["recall_id"])
    assert result.status_code == 410, result.text
