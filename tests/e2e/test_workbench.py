"""Exercise the actual loopback server, then replay its export with the server stopped."""

import io
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest


@contextmanager
def local_server(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AZURE_", "GOLDENLOOP_"))}
    env.update(GOLDENLOOP_LOCAL_DEMO="true", GOLDENLOOP_DATA_DIR=str(tmp_path / "data"),
               GOLDENLOOP_ALLOW_LIVE_SYNTHETIC="false")
    env.pop("PYTHONPATH", None)
    # Repeating bootstrap must preserve the database and avoid duplicate seed cases.
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-I", "-m", "goldenloop_api.bootstrap", "--seed"],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (tmp_path / "server.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-I", "-m", "uvicorn", "goldenloop_api.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--workers", "1", "--no-proxy-headers"],
            cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1/", trust_env=False, timeout=10) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        health = client.get("health")
                        if health.status_code == 200:
                            assert health.json()["mode"] == "local-synthetic"
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    log.flush()
                    log.seek(0)
                    pytest.fail("Local server failed to become ready:\n" + log.read())
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.mark.e2e
def test_import_review_release_failed_fixed_and_offline_export(tmp_path):
    with local_server(tmp_path) as client:
        seeded = checked(client.get("cases"))
        assert len(seeded) == 1
        assert seeded[0]["status"] == "candidate"
        assert client.get("health", headers={"Origin": "https://untrusted.example"}).status_code == 403

        uploaded = checked(client.post("imports/preview", files={
            "file": ("synthetic.csv", b"Title,Question,Answer\nSynthetic customer,Look up customer C-123,C-123\n", "text/csv"),
        }))
        assert uploaded["errors"] == []
        imported = checked(client.post(f"imports/{uploaded['id']}/commit", json={
            "mapping": {"title": "Title", "user": "Question", "reference_answer": "Answer"},
            "duplicate_policy": "reject",
        }))
        assert imported["errors"] == []
        candidate = imported["cases"][0]
        assert candidate["status"] == "candidate"
        case = candidate["case"]
        case_id = case["id"]
        release_body = {"name": "Synthetic smoke release", "case_ids": [case_id],
                        "expected_revisions": {case_id: case["revision"]}}
        assert client.post("dataset-releases", json=release_body).status_code == 409
        case["checks"] = [{
            "id": "exact-customer", "kind": "tool_arguments", "required": True,
            "config": {"tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123"},
        }]
        edited = checked(client.put(f"cases/{case_id}", json={
            "case": case, "expected_revision": case["revision"], "reason": "Reviewed synthetic tool requirement",
        }))
        assert edited["case"]["revision"] == 2
        approved = checked(client.post(f"cases/{case_id}/approve", json={
            "revision": 2, "reason": "Synthetic sandbox exact-ID check reviewed for this integration test",
        }))
        assert approved["status"] == "approved"
        assert client.post("dataset-releases", json=release_body).status_code == 409
        release_body["expected_revisions"] = {case_id: approved["case"]["revision"]}
        release = checked(client.post("dataset-releases", json=release_body), 201)
        snapshot = checked(client.get(f"dataset-releases/{release['id']}"))
        assert snapshot["cases"][0]["revision"] == 2

        for revision, gate, customer in [("buggy", "fail", "C-999"), ("fixed", "pass", "C-123")]:
            request = {"release_id": release["id"], "agent_revision": revision, "mode": "mock",
                       "idempotency_key": str(uuid4())}
            run = checked(client.post("evaluation-runs", json=request), 202)
            assert checked(client.post("evaluation-runs", json=request), 202)["id"] == run["id"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                detail = checked(client.get(f"evaluation-runs/{run['id']}"))
                if detail["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"Run did not complete: {detail}")
            assert detail["status"] == "completed", detail
            assert detail["gate"] == gate, detail
            assert detail["lineage"]["content_hash"] == release["content_hash"]
            result = detail["results"][0]
            assert result["case_revision"] == 2
            assert result["observation"]["trace_complete"] is True
            assert result["observation"]["tool_calls"][0]["arguments"]["customer_id"] == customer

        # Editing the working candidate cannot change the published dataset or its export.
        next_case = {**edited["case"], "title": "Unreviewed later edit"}
        checked(client.put(f"cases/{case_id}", json={
            "case": next_case, "expected_revision": 2, "reason": "Verify immutable release snapshot",
        }))
        assert checked(client.get(f"dataset-releases/{release['id']}")) == snapshot
        exported = client.get(f"dataset-releases/{release['id']}/export")
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-type"] == "application/zip"
        archive_bytes = exported.content

    # API process is now stopped. The bundle is evaluated from outside the checkout.
    bundle = tmp_path / "exported"
    bundle.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert {"manifest.json", "cases.json", "test_release.py", "adapter.py", "requirements.txt"} <= set(archive.namelist())
        for entry in archive.infolist():
            assert (bundle / entry.filename).resolve().parent == bundle.resolve()
        archive.extractall(bundle)
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GOLDENLOOP_", "AZURE_"))}
    env.pop("PYTHONPATH", None)
    replay = subprocess.run(
        [sys.executable, "-I", "-m", "goldenloop_eval", "bundle", str(bundle),
         "--adapter", "goldenloop_demo_agent:run_case", "--json", str(bundle / "report.json")],
        cwd=bundle, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert replay.returncode == 0, replay.stdout + replay.stderr
    report = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
    assert report["gate"] == "pass"
    assert report["content_hash"] == release["content_hash"]
    assert report["release_id"] == release["id"]
    wrapper = subprocess.run(
        [sys.executable, "-I", "-m", "pytest", "-q", "test_release.py"],
        cwd=bundle, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert wrapper.returncode == 0, wrapper.stdout + wrapper.stderr
