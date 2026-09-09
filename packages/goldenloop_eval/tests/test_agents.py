import json

import pytest

from goldenloop_eval import AgentConnection, AgentSpec, BundleManifestV2, Case, agent_spec_hash, release_hash, sanitize


def connection(**updates):
    return AgentConnection(endpoint="https://unit.openai.azure.com", deployment="unit",
                           api_version="2025-01-01-preview", auth="api_key", binding="team-key", **updates)


def test_spec_canonical_hash_and_safe_projection():
    spec = AgentSpec(variant="fixed")
    assert agent_spec_hash(spec) == "68ceff748598bbf9e200e55b60e0572e3085ae52c0043ca3b6c89f69dbe80060"
    explicit = AgentSpec.model_validate(dict(reversed(list(spec.model_dump().items()))))
    assert agent_spec_hash(spec) == agent_spec_hash(explicit)
    a = AgentSpec(variant="fixed", modes=["mock", "live"], connection=connection())
    payload = a.model_dump(mode="json")
    payload["modes"].reverse()
    payload["connection"]["endpoint"] = "https://UNIT.openai.azure.com:443/"
    b = AgentSpec.model_validate(payload)
    assert agent_spec_hash(a) == agent_spec_hash(b)
    assert sanitize(a.model_dump()) == a.model_dump()
    assert a.model_dump()["connection"]["binding"] == "team-key"
    b.connection.binding = "other-team"
    assert agent_spec_hash(a) != agent_spec_hash(b)


@pytest.mark.parametrize("endpoint", [
    "http://unit.test", "https://user:pass@unit.test", "https://unit.test/openai",
    "https://unit.test/?api-key=x", "https://unit.test/#fragment", "https://unit.test?",
    "https://unit.test#", "https://unit.test\\@evil.test", "https://unit.test\n",
    "https://unit.test:invalid", "https://unit.test/%2f", "https://%65vil.test",
])
def test_connection_rejects_non_origin(endpoint):
    payload = connection().model_dump()
    payload["endpoint"] = endpoint
    with pytest.raises(ValueError, match="HTTPS"):
        AgentConnection.model_validate(payload)


@pytest.mark.parametrize("updates", [
    {"modes": ["live"]}, {"modes": ["mock", "mock"]}, {"modes": []},
    {"adapter": "arbitrary"}, {"tool_contract": "custom"}, {"fixture_version": "custom"},
    {"api_key": "secret"}, {"instructions": "api_key=secret"}, {"instructions": "Bearer abcdef"},
])
def test_spec_rejects_invalid_or_sensitive(updates):
    with pytest.raises(ValueError):
        AgentSpec(variant="fixed", **updates)


@pytest.mark.parametrize("auth", ["api_key", "azure_cli"])
def test_binding_required_for_both_auth_methods(auth):
    payload = connection().model_dump()
    payload["auth"] = auth
    del payload["binding"]
    with pytest.raises(ValueError):
        AgentConnection.model_validate(payload)


def test_legacy_case_dump_and_hash_unchanged():
    raw = ('{"id":"legacy-case","revision":1,"title":"Synthetic","tags":[],"source":{},'
           '"context":"","turns":[{"user":"Look up C-123","reference_answer":null}],'
           '"checks":[],"fixture_version":"synthetic-v1"}')
    case = Case.model_validate_json(raw)
    assert case.model_dump_json() == raw
    assert release_hash([case]) == "1a19b295556d634102b5d6cb1c90187ada6938e5fbd0f579ca39db7454fdacfc"


def test_manifest_rejects_spec_tamper_and_secret_judge():
    spec = AgentSpec(variant="fixed")
    payload = dict(project_id="p", release_id="r", content_hash="0" * 64, agent_id="a",
                   agent_revision="revision-id", agent_spec=spec.model_dump(),
                   agent_spec_hash=agent_spec_hash(spec), mode="mock")
    manifest = BundleManifestV2(**payload)
    assert manifest.judge == {"selection": "none"}
    payload["agent_spec"]["variant"] = "buggy"
    with pytest.raises(ValueError, match="spec hash mismatch"):
        BundleManifestV2(**payload)
    payload = json.loads(manifest.model_dump_json())
    payload["judge"] = {"selection": "azure", "api_key": "plain-value"}
    with pytest.raises(ValueError, match="sensitive"):
        BundleManifestV2(**payload)
