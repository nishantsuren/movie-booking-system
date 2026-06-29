"""Agent-service v0 verification -- one state (GREETING), one real
platform call. Real routing/theatre/agent processes, same convention
as every other tests/integration/test_phase*.py file (no mocked HTTP).
"""
import uuid

import httpx
import pytest

ROUTING_BASE = "http://localhost:8000"


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


def test_agent_message_lists_real_cities(routing):
    cities = routing.get("/theatre/cities").json()
    assert cities, "expected at least one seeded city to compare against"

    resp = routing.post(
        "/agent/message",
        json={"session_id": str(uuid.uuid4()), "message": "hi"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["state"] == "GREETING"
    assert "entities" in body["extra"]
    assert any(city["name"] in body["response"] for city in cities)


def test_agent_message_reuses_session_state(routing):
    session_id = str(uuid.uuid4())

    first = routing.post("/agent/message", json={"session_id": session_id, "message": "hi"})
    second = routing.post("/agent/message", json={"session_id": session_id, "message": "hi again"})

    assert first.json()["session_id"] == session_id
    assert second.json()["session_id"] == session_id
    assert first.json()["state"] == second.json()["state"] == "GREETING"
