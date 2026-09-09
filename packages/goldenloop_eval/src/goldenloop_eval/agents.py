"""Backend-free, non-secret execution specifications and portable v2 lineage."""
import hashlib
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .models import Case, Model
from .privacy import sanitize

TRUSTED_AGENT_ARTIFACT = "goldenloop-demo-agent==0.2.0"


def resource_origin(value: str) -> str:
    """Normalize a credential-free HTTPS resource origin, never an API path."""
    if not value or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("Endpoint must be a credential-free HTTPS resource origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in {"", "/"}
                or "?" in value or "#" in value or "%" in parsed.netloc):
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        return f"https://{host}" + (f":{port}" if port not in {None, 443} else "")
    except (ValueError, UnicodeError):
        raise ValueError("Endpoint must be a credential-free HTTPS resource origin") from None


def _nonsecret(value: Any) -> Any:
    if sanitize(value) != value or "[REDACTED]" in json.dumps(value, ensure_ascii=True):
        raise ValueError("Execution specifications must not contain sensitive values")
    return value


class AgentConnection(Model):
    endpoint: str
    deployment: str = Field(min_length=1)
    api_version: str = Field(min_length=1)
    auth: Literal["api_key", "azure_cli"]
    binding: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

    _origin = field_validator("endpoint")(resource_origin)

    @model_validator(mode="after")
    def nonsecret(self):
        _nonsecret(self.model_dump(mode="json"))
        if not self.deployment.strip() or not self.api_version.strip():
            raise ValueError("Deployment and API version must be nonempty")
        return self


class AgentSpec(Model):
    schema_version: Literal["1"] = "1"
    adapter: Literal["synthetic-customer"] = "synthetic-customer"
    artifact: str = Field(default=TRUSTED_AGENT_ARTIFACT, min_length=1)
    variant: Literal["buggy", "fixed"]
    instructions: str = ""
    fixture_version: Literal["synthetic-v1"] = "synthetic-v1"
    tool_contract: Literal["customer-lookup-v1"] = "customer-lookup-v1"
    modes: list[Literal["mock", "live"]] = Field(default_factory=lambda: ["mock"], min_length=1)
    supports_multi_turn: bool = True
    trace_available: bool = True
    connection: AgentConnection | None = None

    @model_validator(mode="after")
    def valid_spec(self):
        if len(self.modes) != len(set(self.modes)):
            raise ValueError("Agent modes must be unique")
        if "live" in self.modes and self.connection is None:
            raise ValueError("Live mode requires a connection")
        _nonsecret(self.model_dump(mode="json"))
        return self


def agent_spec_hash(spec: AgentSpec) -> str:
    # Validate again: callers may have mutated an otherwise validated model.
    payload = AgentSpec.model_validate(spec.model_dump(mode="json")).model_dump(mode="json")
    payload["modes"] = sorted(payload["modes"])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def validate_agent_case(case: Case, spec: AgentSpec, mode: str) -> None:
    if spec.artifact != TRUSTED_AGENT_ARTIFACT:
        raise ValueError("Unsupported agent artifact; install the pinned trusted adapter version")
    if mode not in spec.modes:
        raise ValueError("Requested mode is not declared by the agent")
    if case.fixture_version != spec.fixture_version:
        raise ValueError("Incompatible fixture version")
    if len(case.turns) > 1 and not spec.supports_multi_turn:
        raise ValueError("Multi-turn capability is required")
    if not spec.trace_available and any(c.required and c.kind.startswith("tool_") for c in case.checks):
        raise ValueError("Tool trace capability is required")


class BundleManifestV2(Model):
    schema_version: Literal["2"] = "2"
    sdk_version: Literal["0.2.0"] = "0.2.0"
    project_id: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases_file: Literal["cases.json"] = "cases.json"
    agent_id: str = Field(min_length=1)
    agent_revision: str = Field(min_length=1)
    agent_spec: AgentSpec
    agent_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["mock", "live"]
    judge: dict[str, Any] = Field(default_factory=lambda: {"selection": "none"})

    @model_validator(mode="after")
    def valid_lineage(self):
        if agent_spec_hash(self.agent_spec) != self.agent_spec_hash:
            raise ValueError("Bundle agent spec hash mismatch")
        if self.mode not in self.agent_spec.modes:
            raise ValueError("Bundle mode is not declared by agent spec")
        _nonsecret(self.judge)
        if self.judge == {"selection": "none"}:
            return self
        required = {"selection", "provider", "endpoint", "deployment", "api_version", "prompt_version", "settings"}
        if (set(self.judge) != required or self.judge.get("selection") != "azure"
                or self.judge.get("provider") != "azure-openai"
                or self.judge.get("prompt_version") != "goldenloop-judge-v1"
                or self.judge.get("settings") != {"temperature": 0}
                or any(not isinstance(self.judge.get(k), str) or not self.judge[k].strip()
                       for k in ("endpoint", "deployment", "api_version"))):
            raise ValueError("Unsupported or incomplete pinned judge configuration")
        self.judge["endpoint"] = resource_origin(self.judge["endpoint"])
        return self
