"""Portable invocation; the scoring SDK has no mandatory adapter dependency."""
import importlib
import json
import math
import os
from pathlib import Path

from .agents import BundleManifestV2, TRUSTED_AGENT_ARTIFACT, validate_agent_case
from .evaluator import validate_case_checks
from .judge import OpenAIJudge
from .models import Case
from .privacy import sanitize

REVISION_ADAPTER = "goldenloop_demo_agent:run_revision"
LEGACY_ADAPTER = "goldenloop_demo_agent:run_case"


async def run_bundle(
    directory: str | Path, judge_selection: str = "none", *, adapter: str | None = None,
    timeout: float = 120,
) -> dict:
    """Return a JSON-safe report; configuration errors raise, failed cases return gates.

    V2 defaults to the allowlisted revision adapter. Live bindings are resolved locally
    once per bundle. A pinned Azure judge requires an explicit azure selection and an
    exact GOLDENLOOP_JUDGE_* configuration match. V1 defaults to legacy demo invocation.
    """
    from .cli import build_report, load_bundle, run_cases

    manifest, cases = load_bundle(directory)
    if not math.isfinite(timeout) or timeout <= 0 or manifest.mode not in {"mock", "live"}:
        raise ValueError("Bundle invocation requires a supported mode and positive finite timeout")
    if judge_selection not in {"none", "azure"}:
        raise ValueError("Unknown judge selection")
    v2 = isinstance(manifest, BundleManifestV2)
    if v2:
        if manifest.agent_spec.artifact != TRUSTED_AGENT_ARTIFACT:
            raise ValueError("Unsupported pinned agent artifact")
        if adapter not in {None, REVISION_ADAPTER}:
            raise ValueError("V2 synthetic-customer bundles require goldenloop_demo_agent:run_revision")
        if judge_selection != manifest.judge["selection"]:
            raise ValueError("Judge selection must explicitly match the pinned bundle judge")
        adapter = REVISION_ADAPTER
    else:
        adapter = adapter or LEGACY_ADAPTER
    try:
        for case in cases:
            validate_case_checks(case, judge_available=judge_selection == "azure")
            if v2:
                validate_agent_case(case, manifest.agent_spec, manifest.mode)
    except ValueError:
        raise ValueError("Bundle preflight failed: incompatible case or check configuration") from None
    module, name = adapter.split(":", 1)
    installed = importlib.import_module(module)
    invoke = getattr(installed, name)
    key = None
    if v2 and manifest.mode == "live":
        raw = os.environ.get("GOLDENLOOP_CONNECTION_BINDINGS", "{}")
        if len(raw.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Connection bindings exceed size limit")
        key = installed.resolve_binding(manifest.agent_spec.connection, manifest.project_id, json.loads(raw))
    elif not v2 and manifest.mode == "live":
        key = os.environ.get("AZURE_OPENAI_API_KEY")

    async def runner(case, revision, mode):
        if v2:
            return await invoke(case, revision_id=revision, spec=manifest.agent_spec, mode=mode, api_key=key)
        return await invoke(case, revision=revision, mode=mode)

    judge = None
    try:
        if judge_selection == "azure":
            judge = OpenAIJudge.from_azure_env(expected=manifest.judge if v2 else None)
        secrets = tuple(s for s in (key, *getattr(judge, "secrets", ())) if s)
        lineage = manifest.model_dump(mode="json")
        try:
            if sanitize(lineage, secrets=secrets) != lineage:
                raise ValueError("Unsafe pinned configuration")
            # The original cases were hash-validated above. Redact only invocation copies;
            # never hash them into replacement source lineage or rewrite bundle files.
            cases = [Case.model_validate(sanitize(c.model_dump(mode="json"), secrets=secrets)) for c in cases]
            for case in cases:
                validate_case_checks(case, judge_available=judge is not None)
                if v2:
                    validate_agent_case(case, manifest.agent_spec, manifest.mode)
        except ValueError:
            raise ValueError("Bundle preflight failed: unsafe execution data or configuration") from None
        results = await run_cases(cases, runner, revision=manifest.agent_revision,
                                  mode=manifest.mode, judge=judge, timeout=timeout, secrets=secrets)
        return build_report(results, secrets=secrets, **lineage)
    finally:
        if judge is not None:
            judge.client.close()
