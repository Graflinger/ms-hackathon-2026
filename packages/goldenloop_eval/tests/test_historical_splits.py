import asyncio
import hashlib
import json

import pytest

from goldenloop_eval import AgentSpec, Case, agent_spec_hash, case_split, release_hash, run_bundle
from goldenloop_eval.cli import load_bundle
from goldenloop_eval.models import DATASET_SPLITS, validate_case_split


SPLIT_CASES = [
    ([], "development", True),
    (["priority:p0"], "development", True),
    (["split:development"], "development", True),
    (["priority:p0", "split:validation"], "validation", True),
    (["split:test"], "test", True),
    (["split:train"], "development", False),
    (["split:unknown"], "development", False),
    (["split:"], "development", False),
    (["split:TEST"], "development", False),
    (["split:test "], "development", False),
    (["split:test", "split:validation"], "development", False),
    (["split:validation", "split:test"], "development", False),
    (["split:test", "split:test"], "development", False),
    (["split:train", "split:test"], "development", False),
    (["split:test", "split:train"], "development", False),
]


def historical_payload(tags, case_id="historical"):
    # Raw canonical data from before split tags had semantics. Do not construct a
    # Case here: that would allow new validation/normalization to hide regressions.
    return {
        "id": case_id, "revision": 1, "title": "Historical synthetic café",
        "tags": tags, "source": {"synthetic": True}, "context": "",
        "turns": [{"user": "Look up C-123", "reference_answer": None}],
        "checks": [{"id": "tool", "kind": "tool_arguments", "required": True, "turn": None,
                    "config": {"tool": "lookup_customer", "path": "customer_id",
                               "operator": "equals", "value": "C-123"}}],
        "fixture_version": "synthetic-v1",
    }


def historical_hash(payloads):
    canonical = json.dumps(sorted(payloads, key=lambda p: (p["id"], p["revision"])),
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("tags", "expected_split", "valid_write"), SPLIT_CASES)
@pytest.mark.parametrize("from_json", [False, True])
def test_historical_deserialization_preserves_payload_and_hash(tags, expected_split, valid_write, from_json):
    payload = historical_payload(tags)
    case = Case.model_validate_json(json.dumps(payload)) if from_json else Case.model_validate(payload)
    assert case_split(case) == expected_split
    assert case.model_dump(mode="json") == payload
    assert release_hash([case]) == historical_hash([payload])


@pytest.mark.parametrize(("tags", "expected_split", "valid_write"), SPLIT_CASES)
def test_write_split_validation_is_strict_and_nonmutating(tags, expected_split, valid_write):
    payload = historical_payload(tags)
    case = Case.model_validate(payload)
    if valid_write:
        assert validate_case_split(case) is None
    else:
        with pytest.raises(ValueError, match="at most one split:development, split:validation, or split:test"):
            validate_case_split(case)
    assert case.model_dump(mode="json") == payload


@pytest.mark.parametrize(("schema_version", "sdk_version"), [("1", "0.1.0"), ("1", "0.2.0"), ("2", "0.2.0")])
def test_historical_bundles_load_and_execute_every_case_unchanged(tmp_path, schema_version, sdk_version):
    payloads = [historical_payload(tags, f"historical-{index:02}")
                for index, (tags, _, _) in enumerate(SPLIT_CASES)]
    manifest = {
        "schema_version": schema_version, "sdk_version": sdk_version,
        "release_id": "historical-release", "content_hash": historical_hash(payloads),
        "cases_file": "cases.json", "agent_revision": "fixed", "mode": "mock",
    }
    if schema_version == "2":
        spec = AgentSpec(variant="fixed")
        manifest.update(project_id="p", agent_id="a", agent_revision="historical-revision",
                        agent_spec=spec.model_dump(mode="json"), agent_spec_hash=agent_spec_hash(spec),
                        judge={"selection": "none"})
    cases_path, manifest_path = tmp_path / "cases.json", tmp_path / "manifest.json"
    cases_path.write_text(json.dumps(payloads), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    original_bytes = cases_path.read_bytes(), manifest_path.read_bytes()

    loaded_manifest, cases = load_bundle(tmp_path)
    assert [case.model_dump(mode="json") for case in cases] == payloads
    assert release_hash(cases) == loaded_manifest.content_hash == manifest["content_hash"]
    selections = {split: [case.id for case in cases if case_split(case) == split] for split in DATASET_SPLITS}
    for split in DATASET_SPLITS:
        assert selections[split] == [payload["id"] for payload, (_, expected, _) in zip(payloads, SPLIT_CASES)
                                     if expected == split]

    report = asyncio.run(run_bundle(tmp_path))
    assert report["gate"] == "pass"
    assert report["content_hash"] == manifest["content_hash"]
    assert [result["case_id"] for result in report["evaluations"]] == [payload["id"] for payload in payloads]
    assert (cases_path.read_bytes(), manifest_path.read_bytes()) == original_bytes

    # Legacy tags remain part of the immutable hash, even when their effective
    # split stays development.
    payloads[5]["tags"] = ["split:another-legacy-value"]
    cases_path.write_text(json.dumps(payloads), encoding="utf-8")
    with pytest.raises(ValueError, match="content hash mismatch"):
        load_bundle(tmp_path)
