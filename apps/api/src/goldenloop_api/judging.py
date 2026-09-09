import asyncio
import importlib.util
import os
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException
from goldenloop_eval import JUDGE_PROMPT_VERSION, Case, Check, Observation, OpenAIJudge, evaluate

from .config import clean


@dataclass(frozen=True, repr=False)
class JudgeSnapshot:
    endpoint: str
    deployment: str
    api_version: str
    api_key: str


def judge_lineage(settings, cases: list[Case], selection: str, *, execution: bool = True) -> dict:
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
    keys = (
        "GOLDENLOOP_JUDGE_ENDPOINT",
        "GOLDENLOOP_JUDGE_DEPLOYMENT",
        "GOLDENLOOP_JUDGE_API_VERSION",
        "GOLDENLOOP_JUDGE_API_KEY",
    )
    if (execution and not settings.allow_live_judge) or any(
        not os.getenv(key) for key in (keys if execution else keys[:3])
    ):
        raise HTTPException(
            409,
            "Azure judging requires GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=true and GOLDENLOOP_JUDGE_* configuration",
        )
    try:
        endpoint = urlsplit(os.environ[keys[0]])
        valid = (
            endpoint.scheme == "https"
            and endpoint.hostname
            and not endpoint.username
            and not endpoint.password
            and not endpoint.query
            and not endpoint.fragment
        )
    except ValueError:
        valid = False
    if not valid or (execution and importlib.util.find_spec("openai") is None):
        raise HTTPException(
            409, "Azure judging requires a credential-free HTTPS endpoint and goldenloop-eval[live]"
        )
    if not checks or len(checks) > settings.max_judge_calls:
        raise HTTPException(
            422,
            f"Azure judging requires 1 to {settings.max_judge_calls} explicitly configured judge checks per run",
        )
    lineage = {
        "configured": True,
        "selection": "azure",
        "provider": "azure-openai",
        "mode": "live",
        "model": os.environ[keys[1]],
        "endpoint": os.environ[keys[0]],
        "api_version": os.environ[keys[2]],
        "prompt_version": JUDGE_PROMPT_VERSION,
        "settings": {"temperature": 0, "timeout": 60, "max_retries": 0},
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

    def score():
        if stopped.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("Judge run deadline exceeded")
        judge = OpenAIJudge.from_azure(
            endpoint=snapshot.endpoint,
            deployment=snapshot.deployment,
            api_version=snapshot.api_version,
            api_key=snapshot.api_key,
        )
        try:
            if judge.model != lineage["model"] or judge.provider != lineage["provider"]:
                raise ValueError("Judge lineage mismatch")

            def bounded_judge(case, observation, check):
                if stopped.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("Judge run deadline exceeded")
                return judge(
                    Case.model_validate(clean(case.model_dump(mode="json"))),
                    Observation.model_validate(clean(observation.model_dump(mode="json"))),
                    Check.model_validate(clean(check.model_dump(mode="json"))),
                )

            return evaluate(case, observation, judge=bounded_judge)
        finally:
            judge.client.close()

    task = asyncio.create_task(asyncio.to_thread(score))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A sync HTTP request cannot be force-cancelled safely. Its SDK timeout is 60 seconds,
        # no retries. Do not free the single runner slot or close the client until it finishes.
        stopped.set()
        await asyncio.gather(task, return_exceptions=True)
        raise
