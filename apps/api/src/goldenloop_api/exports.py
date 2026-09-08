import io
import json
import zipfile

from goldenloop_eval import SDK_VERSION, BundleManifest, Case, release_hash

from .config import clean

MAX_RELEASE_BYTES = 8 * 1024 * 1024


def cases_bytes(cases: list[Case]) -> bytes:
    return json.dumps(
        [case.model_dump(mode="json") for case in sorted(cases, key=lambda case: (case.id, case.revision))],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def export_bundle(release_id: str, payloads: list[dict], content_hash: str) -> bytes:
    cases = [Case.model_validate(clean(payload)) for payload in payloads]
    if release_hash(cases) != content_hash:
        raise ValueError("Release redaction policy changed; publish a newly reviewed release")
    manifest = BundleManifest(
        schema_version="1",
        sdk_version=SDK_VERSION,
        release_id=release_id,
        content_hash=content_hash,
        cases_file="cases.json",
        agent_revision="fixed",
        mode="mock",
    )
    files = {
        "cases.json": cases_bytes(cases),
        "manifest.json": manifest.model_dump_json(indent=2),
        "test_release.py": '''"""Run with pytest; requires SDK and adapter dependencies, never the API."""
import asyncio
from pathlib import Path

from goldenloop_eval.cli import load_bundle, run_cases
from adapter import run_case


def test_release():
    manifest, cases = load_bundle(Path(__file__).parent)
    results = asyncio.run(run_cases(cases, run_case, revision=manifest.agent_revision, mode=manifest.mode))
    assert results and all(result.gate == "pass" for result in results), [r.model_dump() for r in results]
''',
        "adapter.py": '''"""Replace this trusted adapter to test your own sandbox agent.

The shipped mock adapter uses synthetic read-only tools. No API server is required.
Return an SDK Observation with matching case, revision, agent revision and mode.
Never send customer data: sanitizer is not a general DLP guarantee.
"""
from goldenloop_demo_agent import run_case

__all__ = ["run_case"]
''',
        "requirements.txt": f"goldenloop-eval=={SDK_VERSION}\ngoldenloop-demo-agent==0.1.0\npytest>=8\n",
        ".env.example": "# Mock export needs no credentials. Live requires explicitly editing manifest mode.\n"
        "# Install goldenloop-demo-agent[live] first. Never commit credential values.\n"
        "AZURE_OPENAI_ENDPOINT=\nAZURE_OPENAI_CHAT_COMPLETION_MODEL=\n"
        "AZURE_OPENAI_API_VERSION=\nAZURE_OPENAI_API_KEY=\n"
        "# Judge checks fail closed without an explicitly configured SDK judge.\n",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()
