import re

from goldenloop_eval import Case, Observation, ToolCall, sanitize

REVISIONS = ("buggy", "fixed")
FIXTURE_VERSION = "synthetic-v1"
_CUSTOMERS = {
    "C-123": {"customer_id": "C-123", "name": "Synthetic Alpine Bikes", "tier": "gold", "synthetic": True},
    "C-999": {"customer_id": "C-999", "name": "Synthetic Cedar Cycles", "tier": "silver", "synthetic": True},
}


def lookup_customer(customer_id: str) -> dict:
    """Look up a synthetic customer in the read-only sandbox, never a real CRM."""
    if customer_id not in _CUSTOMERS:
        raise ValueError("Synthetic customer not found")
    return dict(_CUSTOMERS[customer_id])


async def run_case(case: Case, revision: str = "fixed", mode: str = "mock") -> Observation:
    if revision not in REVISIONS:
        raise ValueError("Unknown agent revision; registered revisions are buggy and fixed")
    if case.fixture_version != FIXTURE_VERSION:
        raise ValueError("Unsupported synthetic fixture version")
    if mode == "live":
        from .live import run_live
        return await run_live(case, revision)
    if mode != "mock":
        raise ValueError("Demo agent supports mock or live invocation, not recorded replay")
    observation = Observation(case_id=case.id, case_revision=case.revision,
                              agent_revision=revision, mode="mock", trace_complete=True)
    for index, turn in enumerate(case.turns):
        user = sanitize(turn.user)
        # Read generated history, never reference answers. Each case owns its history.
        prior = next((m["content"] for m in reversed(observation.messages) if m["role"] == "assistant"), "")
        observation.messages.append({"role": "user", "content": user, "turn": index})
        match = re.search(r"\bC-\d+\b", user)
        if match:
            customer_id = "C-999" if revision == "buggy" else match.group()
            call = ToolCall(id=f"{case.id}:t{index}:lookup:0", tool="lookup_customer",
                            arguments={"customer_id": customer_id}, turn=index, latency_ms=0)
            observation.tool_calls.append(call)
            try:
                customer = lookup_customer(customer_id)
                call.result = customer
                answer = (f"{customer['name']} ({customer['customer_id']}) is a {customer['tier']} tier customer. "
                          "This is synthetic demo data.")
            except ValueError:
                call.error = "Synthetic customer not found"
                answer = "No matching synthetic customer was found."
        elif prior:
            answer = f"From my previous answer: {prior}"
        else:
            answer = "Please provide a synthetic customer ID, for example C-123."
        observation.messages.append({"role": "assistant", "content": answer, "turn": index})
    return Observation.model_validate(sanitize(observation.model_dump(mode="json")))
