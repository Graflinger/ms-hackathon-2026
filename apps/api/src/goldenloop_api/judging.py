import asyncio
import importlib.util
import os
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException
from goldenloop_eval import Case, Check, Observation, OpenAIJudge, azure_judge_snapshot_from_env, evaluate
from goldenloop_eval.agents import resource_origin

from .config import clean, invocation_secrets


@dataclass(frozen=True, repr=False)
class JudgeSnapshot:
    endpoint: str
    deployment: str
    api_version: str
    api_key: str | None = None
    auth: str = "api_key"
    temperature: float | None = 0


def normalized_judge_lineage(lineage: dict) -> dict:
    """Compare historical Azure runs as key-auth without rewriting their evidence."""
    normalized = dict(lineage)
    if normalized.get("selection") == "azure":
        normalized.setdefault("auth", "api_key")
        normalized["endpoint"] = resource_origin(normalized["endpoint"])
    return normalized


def judge_lineage(
    settings, cases: list[Case], selection: str, *, execution: bool = True, repetitions: int = 1
) -> dict:
    checks = [
        {
            "case_id": case.id,
            "case_revision": case.revision,
            "check_id": check.id,
            "turn": check.turn,
            "required": check.required,
            **check.config,
        }
        for case in cases
        for check in case.checks
        if check.kind == "judge"
    ]
    if selection == "none":
        if any(check["required"] for check in checks):
            raise HTTPException(
                409, "Required judge checks need explicit judge='azure' and configured synthetic judging"
            )
        return {"configured": False, "selection": "none"}
    if execution and not settings.allow_live_judge:
        raise HTTPException(
            409,
            "Azure judging requires GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=true and GOLDENLOOP_JUDGE_* configuration",
        )
    try:
        snapshot = azure_judge_snapshot_from_env()
    except ValueError:
        raise HTTPException(
            409, "Azure judging requires a credential-free HTTPS endpoint, deployment, API version, "
            "GOLDENLOOP_JUDGE_AUTH=api_key (default) or azure_cli, "
            "and GOLDENLOOP_JUDGE_TEMPERATURE=0 (default) or default"
        ) from None
    if execution:
        if snapshot["auth"] == "api_key" and not os.getenv("GOLDENLOOP_JUDGE_API_KEY", "").strip():
            raise HTTPException(409, "Azure judging requires GOLDENLOOP_JUDGE_API_KEY for api_key auth")
        dependencies = ("openai", "azure.identity") if snapshot["auth"] == "azure_cli" else ("openai",)
        try:
            installed = all(importlib.util.find_spec(name) is not None for name in dependencies)
        except (ImportError, ValueError):
            installed = False
        if not installed:
            raise HTTPException(409, "Azure judging requires goldenloop-eval[live] dependencies")
    if not checks or len(checks) * repetitions > settings.max_judge_calls:
        raise HTTPException(
            422,
            f"Azure judging requires 1 to {settings.max_judge_calls} explicitly configured judge checks "
            "per run, including repetitions",
        )
    lineage = {
        "configured": True,
        "selection": "azure",
        "provider": "azure-openai",
        "mode": "live",
        "model": snapshot["deployment"],
        "endpoint": snapshot["endpoint"],
        "api_version": snapshot["api_version"],
        "auth": snapshot["auth"],
        "prompt_version": snapshot["prompt_version"],
        "settings": {**snapshot["settings"], "timeout": 60, "max_retries": 0},
        "checks": checks,
    }
    safe = clean(lineage)
    if safe != lineage:
        raise HTTPException(
            409, "Judge configuration or release requires redaction; review safe values before execution"
        )
    return safe


async def evaluate_with_judge(case, observation, lineage, deadline, snapshot: JudgeSnapshot):
    """Keep the synchronous SDK client in its worker, including cleanup after cancellation."""
    stopped = threading.Event()
    # Worker ContextVar changes do not reach the awaiting runner. Keep discovered
    # credentials local, then transfer them only after the worker has finished.
    discovered_secrets = []

    def score():
        if stopped.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("Judge run deadline exceeded")
        judge = OpenAIJudge.from_azure(
            endpoint=snapshot.endpoint,
            deployment=snapshot.deployment,
            api_version=snapshot.api_version,
            api_key=snapshot.api_key,
            auth=snapshot.auth,
            temperature=snapshot.temperature,
        )
        try:
            if judge.model != lineage["model"] or judge.provider != lineage["provider"]:
                raise ValueError("Judge lineage mismatch")

            class BoundedJudge:
                @property
                def secrets(self):
                    return judge.secrets

                def __call__(self, case, observation, check):
                    if stopped.is_set() or time.monotonic() >= deadline:
                        raise TimeoutError("Judge run deadline exceeded")
                    return judge(
                        Case.model_validate(clean(case.model_dump(mode="json"))),
                        Observation.model_validate(clean(observation.model_dump(mode="json"))),
                        Check.model_validate(clean(check.model_dump(mode="json"))),
                    )

            return evaluate(case, observation, judge=BoundedJudge())
        finally:
            try:
                discovered_secrets.extend(judge.secrets)
            finally:
                judge.close()

    task = asyncio.create_task(asyncio.to_thread(score))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A sync HTTP request cannot be force-cancelled safely. Its SDK timeout is 60 seconds,
        # no retries. Do not free the single runner slot or close the client until it finishes.
        stopped.set()
        await asyncio.gather(task, return_exceptions=True)
        raise
    finally:
        # This runs in the caller's task, before it sanitizes the original agent
        # observation/results or persists a failure. Runner resets its context
        # at the end of the invocation; credentials never enter result models.
        invocation_secrets.set(tuple(dict.fromkeys((*invocation_secrets.get(), *discovered_secrets))))
