import json
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from goldenloop_eval import sanitize


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("GOLDENLOOP_DATA_DIR", ".goldenloop")))
    local_demo: bool = field(default_factory=lambda: os.getenv("GOLDENLOOP_LOCAL_DEMO", "").lower() == "true")
    allow_live: bool = field(
        default_factory=lambda: os.getenv("GOLDENLOOP_ALLOW_LIVE_SYNTHETIC", "").lower() == "true"
    )
    allow_live_judge: bool = field(
        default_factory=lambda: os.getenv("GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE", "").lower() == "true"
    )
    case_timeout: float = 120
    run_timeout: float = 600
    poll_interval: float = 0.1
    max_upload_bytes: int = 2 * 1024 * 1024
    max_request_bytes: int = 3 * 1024 * 1024
    max_rows: int = 1000
    max_cases: int = 100
    max_pending: int = 100
    max_judge_calls: int = 20

    @property
    def db_path(self) -> Path:
        return self.data_dir.resolve() / "goldenloop.db"


class SanitizationError(ValueError):
    pass


invocation_secrets: ContextVar[tuple[str, ...]] = ContextVar("invocation_secrets", default=())


def clean(value):
    # Include configured credentials even when their value lacks a recognizable prefix.
    secrets = tuple(
        v
        for k, v in os.environ.items()
        if v
        and any(
            part in k.upper()
            for part in ("API_KEY", "SECRET", "PASSWORD", "ACCESS_TOKEN", "CONNECTION_STRING")
        )
    )
    # Binding names may point to opaque env names that do not contain API_KEY/SECRET.
    try:
        raw = os.environ.get("GOLDENLOOP_CONNECTION_BINDINGS", "{}")
        bindings = json.loads(raw) if len(raw) <= 1024 * 1024 else {}
        if isinstance(bindings, dict):
            for entry in bindings.values():
                key_env = entry.get("key_env") if isinstance(entry, dict) else None
                if isinstance(key_env, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
                    key = os.environ.get(key_env)
                    if key:
                        secrets += (key,)
    except ValueError:
        pass
    secrets += invocation_secrets.get()
    try:
        return sanitize(value, secrets=secrets)
    except ValueError:
        raise SanitizationError("Input cannot be safely sanitized") from None
