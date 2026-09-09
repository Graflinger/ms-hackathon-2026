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


def completed_run(client, path):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        detail = checked(client.get(path))
        if detail["status"] not in {"queued", "running"}:
            assert detail["status"] == "completed", detail
            return detail
        time.sleep(0.1)
    pytest.fail(f"Run did not complete: {detail}")


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
            detail = completed_run(client, f"evaluation-runs/{run['id']}")
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


@pytest.mark.e2e
def test_project_isolation_pinned_revisions_archive_and_offline_v2_export(tmp_path):
    from goldenloop_eval import SDK_VERSION

    exports = []
    with local_server(tmp_path) as client:
        client.base_url = client.base_url.copy_with(path="/")
        registry = "/api/v2/projects"
        demo = registry + "/synthetic-demo"
        legacy_cases = checked(client.get("/api/v1/cases"))
        assert checked(client.get(demo))["name"] == "Synthetic Demo"
        assert [row["case"] for row in checked(client.get(demo + "/cases"))] == [
            row["case"] for row in legacy_cases
        ]
        projects = []
        for name in ("HTTP customer team", "HTTP isolated team"):
            project = checked(client.post(registry, json={"name": name, "description": "Synthetic HTTP test"}), 201)
            path = registry + "/" + project["id"]
            assert checked(client.get(path)) == project
            project = checked(client.patch(path, json={"name": name + " reviewed"}))
            assert project["name"] == name + " reviewed"
            assert checked(client.get(path + "/cases")) == []
            agent = checked(client.post(path + "/agents", json={"name": "Customer agent"}), 201)
            revision_path = path + f"/agents/{agent['id']}/revisions"
            revisions = {
                variant: checked(client.post(revision_path, json={
                    "label": variant.title(), "spec": {"variant": variant, "modes": ["mock"]},
                }), 201)
                for variant in ("buggy", "fixed")
            }
            assert [row["number"] for row in checked(client.get(revision_path))] == [1, 2]

            # Identical imports are distinct project datasets, not global duplicates.
            preview = checked(client.post(path + "/imports/preview", files={
                "file": ("customer.csv", b"Title,Question\nHTTP customer case,Look up customer C-123\n", "text/csv"),
            }))
            assert preview["errors"] == []
            imported = checked(client.post(path + f"/imports/{preview['id']}/commit", json={
                "mapping": {"title": "Title", "user": "Question"}, "duplicate_policy": "reject",
            }))
            assert imported["errors"] == []
            candidate = imported["cases"][0]
            assert candidate["project_id"] == project["id"] and candidate["status"] == "candidate"
            case = candidate["case"]
            assert "project_id" not in case
            case["checks"] = [{
                "id": "customer-id", "kind": "tool_arguments", "required": True,
                "config": {"tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123"},
            }]
            case = checked(client.put(path + f"/cases/{case['id']}", json={
                "case": case, "expected_revision": 1, "reason": "Reviewed exact synthetic customer ID",
            }))["case"]
            checked(client.post(path + f"/cases/{case['id']}/approve", json={
                "revision": 2, "reason": "Reviewed synthetic fixture and required tool check",
            }))
            release = checked(client.post(path + "/dataset-releases", json={
                "name": "Shared customer baseline", "case_ids": [case["id"]],
                "expected_revisions": {case["id"]: 2},
            }), 201)
            snapshot = checked(client.get(path + f"/dataset-releases/{release['id']}"))
            runs = []
            for variant, gate, customer in (("buggy", "fail", "C-999"), ("fixed", "pass", "C-123")):
                revision = revisions[variant]
                body = {"release_id": release["id"], "agent_revision_id": revision["id"],
                        "mode": "mock", "idempotency_key": "shared-" + variant}
                run = checked(client.post(path + "/evaluation-runs", json=body), 202)
                assert checked(client.post(path + "/evaluation-runs", json=body), 202)["id"] == run["id"]
                assert client.post(path + "/evaluation-runs", json={
                    **body, "agent_revision_id": revisions["fixed" if variant == "buggy" else "buggy"]["id"],
                }).status_code == 409
                detail = completed_run(client, path + f"/evaluation-runs/{run['id']}")
                assert detail["gate"] == gate, detail
                assert detail["project_id"] == project["id"]
                assert detail["agent_id"] == agent["id"]
                assert detail["agent_revision_id"] == revision["id"]
                assert detail["spec_hash"] == revision["spec_hash"]
                assert detail["lineage"]["agent_spec_hash"] == revision["spec_hash"]
                assert detail["lineage"]["content_hash"] == release["content_hash"]
                result = detail["results"][0]
                assert result["case_revision"] == 2
                assert result["observation"]["agent_revision"] == revision["id"]
                assert result["observation"]["trace_complete"] is True
                assert result["observation"]["tool_calls"][0]["arguments"]["customer_id"] == customer
                runs.append(detail)
            projects.append((path, project, agent, revisions, case, release, snapshot, runs))

        left, right = projects
        assert {run["id"] for run in left[-1]}.isdisjoint(run["id"] for run in right[-1])
        for own, foreign in ((left, right), (right, left)):
            path, project, agent, revisions, case, release, snapshot, runs = own
            other_path = foreign[0]
            assert {row["case"]["id"] for row in checked(client.get(path + "/cases"))} == {case["id"]}
            assert {row["id"] for row in checked(client.get(path + "/dataset-releases"))} == {release["id"]}
            assert {row["id"] for row in checked(client.get(path + "/evaluation-runs"))} == {run["id"] for run in runs}
            for prefix in (other_path, demo, "/api/v1"):
                for suffix in (f"/cases/{case['id']}", f"/dataset-releases/{release['id']}",
                               f"/evaluation-runs/{runs[0]['id']}", f"/evaluation-runs/{runs[0]['id']}/events"):
                    assert client.get(prefix + suffix).status_code == 404, prefix + suffix
            assert client.get(other_path + f"/agents/{agent['id']}").status_code == 404
            assert client.post(other_path + "/evaluation-runs", json={
                "release_id": foreign[5]["id"], "agent_revision_id": revisions["fixed"]["id"],
                "mode": "mock", "idempotency_key": "foreign-revision",
            }).status_code == 404
            assert client.post(other_path + "/dataset-releases", json={
                "name": "Foreign case", "case_ids": [case["id"]], "expected_revisions": {case["id"]: 2},
            }).status_code == 404

        path, project, agent, revisions, case, release, snapshot, runs = left
        second_agent = checked(client.post(path + "/agents", json={"name": "Second logical agent"}), 201)
        second_revision = checked(client.post(path + f"/agents/{second_agent['id']}/revisions", json={
            "label": "Independent fixed", "spec": {"variant": "fixed"},
        }), 201)
        shared = checked(client.post(path + "/evaluation-runs", json={
            "release_id": release["id"], "agent_revision_id": second_revision["id"],
            "mode": "mock", "idempotency_key": "second-agent",
        }), 202)
        shared = completed_run(client, path + f"/evaluation-runs/{shared['id']}")
        assert shared["gate"] == "pass" and shared["agent_id"] == second_agent["id"]
        assert shared["lineage"]["content_hash"] == release["content_hash"]
        assert len(checked(client.get(path + "/dataset-releases"))) == 1

        checked(client.patch(path + f"/agents/{agent['id']}", json={"name": "Renamed customer agent"}))
        checked(client.post(path + f"/agents/{agent['id']}/revisions", json={
            "label": "Later fixed", "spec": {"variant": "fixed"},
        }), 201)
        checked(client.put(path + f"/cases/{case['id']}", json={
            "case": {**case, "title": "Unreviewed later edit"}, "expected_revision": 2,
            "reason": "Working copy must not alter release or prior evidence",
        }))
        for variant, revision in revisions.items():
            assert checked(client.get(path + f"/agents/{agent['id']}/revisions/{revision['id']}")) == revision
        for run in runs:
            # Display metadata follows the agent; pinned lineage and evidence do not.
            run = {**run, "agent_name": "Renamed customer agent"}
            assert checked(client.get(path + f"/evaluation-runs/{run['id']}")) == run
        assert checked(client.get(path + f"/dataset-releases/{release['id']}")) == snapshot

        checked(client.patch(path + f"/agents/{agent['id']}", json={"archived": True}))
        blocked_run = {"release_id": release["id"], "agent_revision_id": revisions["fixed"]["id"],
                       "mode": "mock", "idempotency_key": "archived-agent"}
        assert client.post(path + "/evaluation-runs", json=blocked_run).status_code == 409
        assert client.post(path + "/chat-sessions", json={"agent_revision_id": revisions["fixed"]["id"]}).status_code == 409
        assert client.post(path + f"/agents/{agent['id']}/revisions", json={
            "label": "Blocked", "spec": {"variant": "fixed"},
        }).status_code == 409
        archived = checked(client.patch(path, json={"archived": True}))
        assert archived["archived"] is True
        for suffix, body in (("/cases", {"case": case}), ("/agents", {"name": "Blocked"}),
                             ("/dataset-releases", {"name": "Blocked", "case_ids": [case["id"]],
                                                    "expected_revisions": {case["id"]: 2}}),
                             ("/evaluation-runs", {**blocked_run, "agent_revision_id": second_revision["id"]}),
                             ("/chat-sessions", {"agent_revision_id": second_revision["id"]})):
            assert client.post(path + suffix, json=body).status_code == 409
        assert checked(client.get(path + f"/dataset-releases/{release['id']}")) == snapshot
        for run in runs:
            run = {**run, "agent_name": "Renamed customer agent"}
            assert checked(client.get(path + f"/evaluation-runs/{run['id']}")) == run
            assert client.get(path + f"/evaluation-runs/{run['id']}/events").status_code == 200

        export_path = path + f"/dataset-releases/{release['id']}/export"
        assert client.get(export_path).status_code == 422
        assert client.get(export_path, params={"agent_revision_id": right[3]["fixed"]["id"], "mode": "mock"}).status_code == 404
        for variant, revision in revisions.items():
            response = client.get(export_path, params={"agent_revision_id": revision["id"], "mode": "mock"})
            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == "application/zip"
            exports.append((variant, revision, response.content))
        assert checked(client.patch(path, json={"archived": False}))["archived"] is False
        assert checked(client.patch(path + f"/agents/{agent['id']}", json={"archived": False}))["archived"] is False
        restored = checked(client.post(path + "/evaluation-runs", json=blocked_run), 202)
        assert completed_run(client, path + f"/evaluation-runs/{restored['id']}")["gate"] == "pass"
        assert checked(client.get("/api/v1/cases")) == legacy_cases
        assert checked(client.get(demo + "/evaluation-runs")) == []
        assert {row["id"] for row in checked(client.get(registry))} == {"synthetic-demo", left[1]["id"], right[1]["id"]}

    # Run installed SDK/adapter in isolated Python outside the checkout, with API imports forbidden.
    guard = """import importlib.abc, runpy, sys
class NoBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'goldenloop_api' or fullname.startswith('goldenloop_api.'):
            raise RuntimeError('Portable bundle attempted to import backend: ' + fullname)
sys.meta_path.insert(0, NoBackend())
module = sys.argv.pop(1)
runpy.run_module(module, run_name='__main__')
"""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GOLDENLOOP_", "AZURE_"))}
    env.pop("PYTHONPATH", None)
    for variant, revision, archive_bytes in exports:
        bundle = tmp_path / ("v2-" + variant)
        bundle.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            assert {"manifest.json", "cases.json", "test_release.py", "requirements.txt", ".env.example"} == set(archive.namelist())
            assert all((bundle / entry.filename).resolve().parent == bundle.resolve() for entry in archive.infolist())
            archive.extractall(bundle)
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "2" and manifest["sdk_version"] == SDK_VERSION
        assert manifest["project_id"] == project["id"] and manifest["agent_id"] == agent["id"]
        assert manifest["agent_revision"] == revision["id"] and manifest["agent_spec_hash"] == revision["spec_hash"]
        assert manifest["agent_spec"] == revision["spec"] and manifest["mode"] == "mock"
        assert "goldenloop-api" not in (bundle / "requirements.txt").read_text(encoding="utf-8")
        replay = subprocess.run(
            [sys.executable, "-I", "-c", guard, "goldenloop_eval", "bundle", str(bundle),
             "--json", str(bundle / "report.json")],
            cwd=bundle, env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        assert replay.returncode == (0 if variant == "fixed" else 1), replay.stdout + replay.stderr
        report = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
        assert report["gate"] == ("pass" if variant == "fixed" else "fail")
        for field in ("release_id", "content_hash", "project_id", "agent_id", "agent_revision", "agent_spec_hash"):
            assert report[field] == manifest[field]
        wrapper = subprocess.run(
            [sys.executable, "-I", "-c", guard, "pytest", "-q", "test_release.py"],
            cwd=bundle, env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        assert wrapper.returncode == (0 if variant == "fixed" else 1), wrapper.stdout + wrapper.stderr
        assert ("1 passed" if variant == "fixed" else "1 failed") in wrapper.stdout
