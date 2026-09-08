"""Repository-local SDK integration: no API imports, running server, or cloud calls."""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from goldenloop_eval import BundleManifest, Case, release_hash


@pytest.mark.parametrize(("revision", "exit_code", "gate"), [("buggy", 1, "fail"), ("fixed", 0, "pass")])
def test_standalone_bundle(tmp_path, revision, exit_code, gate):
    if os.getenv("GOLDENLOOP_ASSERT_NO_API") == "true":
        assert importlib.util.find_spec("goldenloop_api") is None
    case = Case(
        id="synthetic-repository-customer",
        title="Synthetic repository-local customer check",
        source={"type": "synthetic-test"},
        turns=[{"user": "Look up customer C-123"}],
        checks=[{
            "id": "exact-customer",
            "kind": "tool_arguments",
            "config": {"tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123"},
        }],
    )
    manifest = BundleManifest(
        schema_version="1", sdk_version="0.1.0", release_id="synthetic-test-release",
        content_hash=release_hash([case]), cases_file="cases.json", agent_revision=revision, mode="mock",
    )
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps([case.model_dump(mode="json")]), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GOLDENLOOP_", "AZURE_"))}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-m", "goldenloop_eval", "bundle", str(tmp_path),
         "--adapter", "goldenloop_demo_agent:run_case", "--json", str(tmp_path / "report.json"),
         "--junit", str(tmp_path / "junit.xml")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["gate"] == gate
    assert report["content_hash"] == manifest.content_hash
    assert report["agent_revision"] == revision
    assert report["mode"] == "mock"
    assert report["evaluations"][0]["gate"] == gate
    assert (tmp_path / "junit.xml").is_file()
