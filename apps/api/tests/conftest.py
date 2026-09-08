import asyncio

import httpx
import pytest

from goldenloop_api.bootstrap import migrate
from goldenloop_api.config import Settings
from goldenloop_api.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, local_demo=True, poll_interval=0.01, case_timeout=2, run_timeout=5)


@pytest.fixture
async def app(settings):
    await asyncio.to_thread(migrate, settings)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost"
    ) as client:
        yield client


@pytest.fixture
def case_payload():
    return {
        "title": "Synthetic C-123 lookup",
        "tags": ["synthetic"],
        "turns": [{"user": "Look up C-123"}, {"user": "Repeat the customer ID"}],
        "checks": [
            {"kind": "content_contains", "turn": 0, "config": {"value": "C-123"}},
            {
                "kind": "tool_arguments",
                "turn": 0,
                "config": {
                    "tool": "lookup_customer",
                    "path": "customer_id",
                    "operator": "equals",
                    "value": "C-123",
                },
            },
        ],
    }


async def publish(client, case_payload):
    response = await client.post("/api/v1/cases", json={"case": case_payload})
    assert response.status_code == 201, response.text
    case = response.json()["case"]
    response = await client.post(
        f"/api/v1/cases/{case['id']}/approve", json={"revision": 1, "reason": "Synthetic review"}
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/v1/dataset-releases",
        json={
            "name": "synthetic-v1",
            "case_ids": [case["id"]],
            "expected_revisions": {case["id"]: case["revision"]},
        },
    )
    assert response.status_code == 201, response.text
    return case, response.json()


async def wait_for(client, path, statuses=("completed", "failed", "cancelled", "interrupted")):
    for _ in range(300):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        if response.json()["status"] in statuses:
            return response.json()
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {path}: {response.text}")
