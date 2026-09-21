import json

import pytest

from goldenloop_eval import AgentConnection, AgentSpec, BundleManifestV2, Case, agent_spec_hash, release_hash, sanitize
from goldenloop_eval.agents import validate_agent_case


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


@pytest.mark.parametrize("adapter", ["synthetic-customer", "synthetic-powerplant-var"])
@pytest.mark.parametrize("fixture", ["synthetic-v1", "synthetic-powerplant-v1"])
@pytest.mark.parametrize("contract", ["customer-lookup-v1", "powerplant-decision-v1"])
def test_adapter_requires_its_matching_fixture_and_tool_contract(adapter, fixture, contract):
    payload = dict(adapter=adapter, variant="fixed", fixture_version=fixture, tool_contract=contract)
    expected = {
        "synthetic-customer": ("synthetic-v1", "customer-lookup-v1"),
        "synthetic-powerplant-var": ("synthetic-powerplant-v1", "powerplant-decision-v1"),
    }
    if (fixture, contract) != expected[adapter]:
        with pytest.raises(ValueError, match="Adapter fixture and tool contract must match"):
            AgentSpec(**payload)
    else:
        spec = AgentSpec(**payload)
        assert spec.adapter == adapter
        assert spec.fixture_version == fixture and spec.tool_contract == contract
        assert AgentSpec.model_validate_json(spec.model_dump_json()) == spec


@pytest.mark.parametrize("adapter", ["synthetic-customer", "synthetic-powerplant-var"])
def test_case_fixture_must_match_agent_at_execution(adapter):
    powerplant = adapter == "synthetic-powerplant-var"
    fixture = "synthetic-powerplant-v1" if powerplant else "synthetic-v1"
    spec = AgentSpec(adapter=adapter, variant="fixed", fixture_version=fixture,
                     tool_contract="powerplant-decision-v1" if powerplant else "customer-lookup-v1")
    case = Case(title="Synthetic fixture compatibility", fixture_version=fixture, turns=[{"user": "Investigate"}])
    validate_agent_case(case, spec, "mock")
    for wrong in ("synthetic-v1" if powerplant else "synthetic-powerplant-v1", "unknown-fixture"):
        case.fixture_version = wrong
        with pytest.raises(ValueError, match="Incompatible fixture version"):
            validate_agent_case(case, spec, "mock")


@pytest.mark.parametrize("variant", ["fixed", "buggy"])
@pytest.mark.parametrize("mode", ["mock", "live"])
def test_powerplant_spec_and_bundle_preserve_execution_contract(variant, mode):
    spec = AgentSpec(adapter="synthetic-powerplant-var", variant=variant,
                     fixture_version="synthetic-powerplant-v1", tool_contract="powerplant-decision-v1",
                     modes=[mode], connection=connection() if mode == "live" else None)
    case = Case(title="Synthetic VaR", fixture_version="synthetic-powerplant-v1",
                turns=[{"user": "Investigate cooling_water"}])
    validate_agent_case(case, spec, mode)
    manifest = BundleManifestV2(
        project_id="p", release_id="r", content_hash=release_hash([case]), agent_id="var",
        agent_revision="immutable-var-revision", agent_spec=spec,
        agent_spec_hash=agent_spec_hash(spec), mode=mode,
    )
    restored = BundleManifestV2.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    assert restored.agent_spec == spec
    assert restored.agent_spec_hash != agent_spec_hash(AgentSpec(variant=variant))
    assert sanitize(spec.model_dump()) == spec.model_dump()
    payload = manifest.model_dump(mode="json")
    payload["agent_spec"]["variant"] = "buggy" if variant == "fixed" else "fixed"
    with pytest.raises(ValueError, match="spec hash mismatch"):
        BundleManifestV2.model_validate(payload)


def test_powerplant_hash_revalidates_mutated_fixture_and_contract():
    for field, value in (("fixture_version", "synthetic-v1"), ("tool_contract", "customer-lookup-v1")):
        spec = AgentSpec(adapter="synthetic-powerplant-var", variant="fixed",
                         fixture_version="synthetic-powerplant-v1", tool_contract="powerplant-decision-v1")
        setattr(spec, field, value)
        with pytest.raises(ValueError, match="Adapter fixture and tool contract must match"):
            agent_spec_hash(spec)
