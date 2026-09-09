"""Repository-local SDK integration: no API imports, running server, or cloud calls."""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from goldenloop_eval import AgentSpec, BundleManifest, BundleManifestV2, Case, agent_spec_hash, release_hash


@pytest.mark.parametrize(("revision", "exit_code", "gate"), [("buggy", 1, "fail"), ("fixed", 0, "pass")])
@pytest.mark.parametrize("version", ["1", "2"])
def test_standalone_bundle(tmp_path, revision, exit_code, gate, version):
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
    adapter_args = ["--adapter", "goldenloop_demo_agent:run_case"]
    if version == "2":
        spec = AgentSpec(variant=revision)
        manifest = BundleManifestV2(
            project_id="repository-project", release_id="synthetic-test-release", agent_id="customer-agent",
            content_hash=release_hash([case]), agent_revision=f"revision-{revision}", mode="mock",
            agent_spec=spec, agent_spec_hash=agent_spec_hash(spec),
        )
        adapter_args = []
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps([case.model_dump(mode="json")]), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GOLDENLOOP_", "AZURE_"))}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-m", "goldenloop_eval", "bundle", str(tmp_path),
         *adapter_args, "--json", str(tmp_path / "report.json"),
         "--junit", str(tmp_path / "junit.xml")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["gate"] == gate
    assert report["content_hash"] == manifest.content_hash
    assert report["agent_revision"] == manifest.agent_revision
    assert report["sdk_version"] == "0.2.0"
    assert report["mode"] == "mock"
    assert report["evaluations"][0]["gate"] == gate
    assert (tmp_path / "junit.xml").is_file()


def test_sdk_import_without_adapter_backend_or_provider(tmp_path):
    script = """
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'goldenloop_api', 'goldenloop_demo_agent', 'openai', 'agent_framework', 'azure'}:
            raise ImportError('Optional package unavailable')
sys.meta_path.insert(0, BlockOptional())
from goldenloop_eval import AgentSpec, BundleManifestV2, evaluate, run_bundle
assert AgentSpec(variant='fixed').adapter == 'synthetic-customer'
"""
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
