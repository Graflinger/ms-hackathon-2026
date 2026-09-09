"""Trusted adapter invocation and operator-owned credential binding resolution."""
import os
import re

from goldenloop_eval import AgentConnection, AgentSpec, Case, Observation, sanitize
from goldenloop_eval.agents import resource_origin, validate_agent_case

from .agent import run_case


def resolve_binding(connection: AgentConnection, project_id: str, bindings: dict) -> str | None:
    """Authorize origin/auth/project before reading an operator-allowlisted key env."""
    connection = AgentConnection.model_validate(connection.model_dump(mode="json"))
    if not isinstance(bindings, dict):
        raise ValueError("Connection bindings must be an object")
    entry = bindings.get(connection.binding)
    if (not isinstance(entry, dict) or set(entry) - {"endpoint", "auth", "key_env", "projects"}
            or not isinstance(entry.get("projects"), list)
            or not all(isinstance(p, str) for p in entry["projects"])
            or not project_id or project_id not in entry["projects"]
            or entry.get("auth") != connection.auth
            or not isinstance(entry.get("endpoint"), str)
            or resource_origin(entry["endpoint"]) != connection.endpoint):
        raise ValueError("Connection binding does not authorize this project, origin and auth")
    if connection.auth == "azure_cli":
        if entry.get("key_env") is not None:
            raise ValueError("Azure CLI bindings cannot specify key_env")
        return None
    key_env = entry.get("key_env")
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("API key binding requires an operator key_env")
    key = os.environ.get(key_env)
    if not key or not key.strip():
        raise ValueError("Connection binding credential is unavailable")
    return key


async def run_revision(
    case: Case, revision_id: str, spec: AgentSpec, mode: str = "mock", *, api_key: str | None = None,
) -> Observation:
    """Execute a new invocation, validate observed identity, then pin its revision ID."""
    spec = AgentSpec.model_validate(spec.model_dump(mode="json"))
    if not isinstance(revision_id, str) or not revision_id.strip():
        raise ValueError("An agent revision ID is required")
    secrets = (api_key,) if api_key else ()
    if sanitize(spec.model_dump(mode="json"), secrets=secrets) != spec.model_dump(mode="json"):
        raise ValueError("Unsafe agent specification")
    if sanitize(revision_id, secrets=secrets) != revision_id:
        raise ValueError("Unsafe agent revision identity")
    try:
        case = Case.model_validate(sanitize(case.model_dump(mode="json"), secrets=secrets))
    except ValueError:
        raise ValueError("Unsafe execution data") from None
    validate_agent_case(case, spec, mode)
    if mode == "live":
        from .live import run_live
        observation = await run_live(case, spec.variant, connection=spec.connection,
                                     api_key=api_key, instructions=spec.instructions)
    else:
        observation = await run_case(case, revision=spec.variant, mode=mode)
    observation = Observation.model_validate(observation.model_dump(mode="json"))
    if (observation.case_id != case.id or observation.case_revision != case.revision
            or observation.agent_revision != spec.variant or observation.mode != mode):
        raise ValueError("Agent adapter returned mismatched execution lineage")
    observation = observation.model_copy(update={"agent_revision": revision_id})
    return Observation.model_validate(sanitize(observation.model_dump(mode="json"), secrets=secrets))
