import asyncio
import os

import pytest

from goldenloop_demo_agent import resolve_binding, run_revision
from goldenloop_eval import AgentConnection, AgentSpec, Case, Observation, evaluate


def connection(**updates):
    return AgentConnection.model_validate({"endpoint": "https://unit.openai.azure.com", "deployment": "unit",
        "api_version": "2025-01-01-preview", "auth": "api_key", "binding": "team", **updates})


def case():
    return Case(id="customer", title="Synthetic", turns=[
        {"user": "Look up C-123", "reference_answer": "GOLDEN ANSWER NEVER USED"},
        {"user": "Which customer?", "reference_answer": "GOLDEN ANSWER NEVER USED"},
    ], checks=[{"id": "tool", "kind": "tool_arguments", "turn": 0,
                "config": {"tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123"}}])


@pytest.mark.parametrize(("variant", "gate"), [("fixed", "pass"), ("buggy", "fail")])
def test_revision_identity_and_generated_history(variant, gate):
    spec = AgentSpec(variant=variant, instructions="Not used in mock mode")
    obs = asyncio.run(run_revision(case(), "immutable-revision", spec))
    assert obs.agent_revision == "immutable-revision"
    assert evaluate(case(), obs).gate == gate
    assert "GOLDEN ANSWER" not in obs.model_dump_json()
    assert obs.messages[-2]["role"] == "user"
    assert obs.messages[1]["content"] in obs.messages[-1]["content"]


@pytest.mark.parametrize("updates", [
    {"artifact": "goldenloop-demo-agent==0.1.0"}, {"artifact": "evil:runner"},
    {"supports_multi_turn": False}, {"trace_available": False},
    {"modes": ["live"], "connection": connection()},
])
def test_incompatible_spec(updates):
    with pytest.raises(ValueError):
        asyncio.run(run_revision(case(), "revision", AgentSpec(variant="fixed", **updates)))


@pytest.mark.parametrize("updates", [
    {"case_id": "wrong"}, {"case_revision": 2}, {"agent_revision": "wrong"}, {"mode": "live"},
])
def test_observed_identity_is_checked_before_normalization(monkeypatch, updates):
    async def mismatched(*args, **kwargs):
        return Observation.model_validate({"case_id": "customer", "case_revision": 1,
                                            "agent_revision": "fixed", "mode": "mock", **updates})
    monkeypatch.setattr("goldenloop_demo_agent.revisions.run_case", mismatched)
    with pytest.raises(ValueError, match="lineage"):
        asyncio.run(run_revision(case(), "revision", AgentSpec(variant="fixed")))


def test_binding_authorization_and_no_environment_mutation(monkeypatch):
    monkeypatch.setenv("UNIT_BINDING_KEY", "unit-credential")
    before = dict(os.environ)
    entry = {"endpoint": connection().endpoint, "auth": "api_key", "key_env": "UNIT_BINDING_KEY", "projects": ["p"]}
    assert resolve_binding(connection(), "p", {"team": entry}) == "unit-credential"
    for updates in ({"endpoint": "https://other.test"}, {"auth": "azure_cli"}, {"projects": ["other"]},
                    {"key_env": "MISSING_UNIT_CREDENTIAL"}, {"api_key": "secret"}, {"projects": "p"}):
        with pytest.raises(ValueError):
            resolve_binding(connection(), "p", {"team": {**entry, **updates}})
    cli = connection(auth="azure_cli")
    assert resolve_binding(cli, "p", {"team": {"endpoint": cli.endpoint, "auth": "azure_cli", "projects": ["p"]}}) is None
    assert dict(os.environ) == before


def test_explicit_live_missing_key_never_uses_legacy_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "legacy-key")
    spec = AgentSpec(variant="fixed", modes=["live"], connection=connection())
    with pytest.raises(ValueError, match="resolved credential"):
        asyncio.run(run_revision(case(), "revision", spec, mode="live"))
