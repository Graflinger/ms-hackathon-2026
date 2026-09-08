from fastapi import HTTPException
from goldenloop_eval import Case, Observation, evaluate
from goldenloop_eval.evaluator import JudgeConfig
from pydantic import ValidationError


def validate_expectations(case: Case):
    """Validate configuration, not correctness or approval, without invoking an agent/judge."""
    if not any(check.required for check in case.checks):
        raise HTTPException(422, "Approval requires at least one required actionable check")
    try:
        for check in case.checks:
            if check.kind == "judge":
                JudgeConfig.model_validate(check.config)
    except ValidationError:
        raise HTTPException(422, "Invalid judge check configuration") from None

    # The SDK currently validates deterministic configs during evaluation. Probe with complete
    # synthetic evidence: failed assertions are fine; errors are not. Never persist this probe.
    checks = [check.model_copy(update={"required": True}) for check in case.checks if check.kind != "judge"]
    if not checks:
        return
    probe = case.model_copy(update={"checks": checks})
    observation = Observation(
        case_id=case.id,
        case_revision=case.revision,
        agent_revision="configuration-probe",
        mode="mock",
        trace_complete=True,
        messages=[{"role": "assistant", "content": "{}", "turn": turn} for turn in range(len(case.turns))],
    )
    result = evaluate(probe, observation)
    if any(check.status in {"error", "skipped"} for check in result.checks):
        raise HTTPException(
            422, "Unsupported or invalid check configuration; edit this candidate before approval"
        )
