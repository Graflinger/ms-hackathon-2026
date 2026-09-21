"""VaR: read-only synthetic powerplant decision-support tools, never plant control."""
import csv
import hashlib
import random
import re
from importlib.resources import files
from time import perf_counter
from typing import Literal

from goldenloop_eval import Case, Observation, ToolCall, sanitize

Category = Literal["cooling_water", "fuel_gas", "steam_water_leak", "fire_smoke"]
CATEGORIES = ("cooling_water", "fuel_gas", "steam_water_leak", "fire_smoke")
FIXTURE_VERSION = "synthetic-powerplant-v1"
DISCLAIMER = (
    "Synthetic demo only — random ratings and fictional incidents, not an operational risk assessment "
    "or a monetary forecast. Follow approved site procedures and qualified operator judgment."
)
INSTRUCTIONS = """You are VaR, a powerplant operator decision-support DEMO using synthetic data.
You are connected to a real LLM, but ALL tool outputs are synthetic, not plant telemetry or market data.
For a reported problem, call commercial_outlook, risk_assessment, and similar_occurrences.
Choose the matching category: cooling_water (cooling/intake/condenser), fuel_gas (gas supply/leaks),
steam_water_leak (steam/feedwater/boiler leaks), fire_smoke (fire/smoke). If ambiguous, ask for clarification.
For multiple problem categories, retrieve all applicable categories; never hide a fire or gas warning.
Commercial returns are only no/low/medium/high for today, NOT monetary earnings.
Technical risks are only none/low/medium/high, randomly mocked, NOT a calibrated safety assessment.
State the actual tool ratings and distinguish them from reported symptoms and your recommendations.
Include matching incidents in a Markdown table with ID, category, component, symptoms, action taken, outcome.
Never invent incidents or alter their results; describe them as fictional precedents, not validated instructions.
Provide a concise issue summary, technical risk, today's commercial outlook, and cautious recommended next steps.
Safety/protection limits always override commercial return. Even a 'none' mock rating never clears a hazard.
For fire, smoke, suspected gas release, or pressurized steam leaks, prioritize approved site emergency
procedures and authorized responders; never recommend continued operation based on a random rating.
Do not give hands-on repair, bypass, control commands, restart clearance, or claims of safety.
Ask for relevant alarms/operating limits and qualified operator assessment. No real-time plant access exists.
Always label the answer synthetic/demo, not operational advice. VaR is the agent name, not calculated Value at Risk.
"""


def _category(category: str) -> None:
    if category not in CATEGORIES:
        raise ValueError("Unknown incident category")


def similar_occurrences(category: Category) -> dict:
    """Exact category lookup of fictional powerplant incidents in a packaged CSV; no embeddings."""
    _category(category)
    resource = files("goldenloop_demo_agent").joinpath("data/powerplant_incidents.csv")
    with resource.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        expected = {"incident_id", "category", "component", "symptoms", "action_taken", "outcome", "synthetic"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError("Invalid incident CSV schema")
        rows = list(reader)
    if any(set(row) != expected or any(not value for value in row.values())
           or row["category"] not in CATEGORIES or row["synthetic"] != "true" for row in rows):
        raise ValueError("Invalid synthetic incident CSV")
    return {"category": category, "synthetic": True, "source": FIXTURE_VERSION,
            "incidents": [{**row, "synthetic": True} for row in rows if row["category"] == category]}


def make_tools(seed: str):
    """Independent seeded draws: stable across concurrency, tool order and repeated calls."""
    def rating(tool: str, category: str, levels: tuple[str, ...]) -> str:
        _category(category)
        digest = hashlib.sha256(f"{FIXTURE_VERSION}:{seed}:{tool}:{category}".encode()).digest()
        return random.Random(int.from_bytes(digest, "big")).choice(levels)

    def commercial_outlook(category: Category) -> dict:
        """Return a randomly mocked no/low/medium/high commercial return for today; no monetary forecast."""
        return {"category": category, "return": rating("commercial", category, ("no", "low", "medium", "high")),
                "horizon": "today", "synthetic": True, "method": "seeded-random-v1"}

    def risk_assessment(category: Category) -> dict:
        """Return randomly mocked none/low/medium/high technical risk; never a safety clearance."""
        return {"category": category, "risk": rating("risk", category, ("none", "low", "medium", "high")),
                "synthetic": True, "method": "seeded-random-v1", "valid_for_operations": False}

    return [commercial_outlook, risk_assessment, similar_occurrences]


def categories_for(text: str) -> list[str]:
    """Small mock-only keyword router; the live LLM selects categories itself."""
    patterns = {
        "fire_smoke": r"\b(fire|smoke|burning)\b",
        "fuel_gas": r"\b(gas|fuel|combustion)\b",
        "steam_water_leak": r"\b(steam|leak|leaks|leakage|feedwater|boiler)\b",
        "cooling_water": r"\b(water|cooling|condenser|intake)\b",
    }
    return [category for category, pattern in patterns.items()
            if category in text.lower() or re.search(pattern, text, re.I)]


async def run_powerplant(case: Case, revision: str = "fixed") -> Observation:
    """Offline test double. Real conversations use the Microsoft Agent Framework live path."""
    if case.fixture_version != FIXTURE_VERSION or revision not in {"fixed", "buggy"}:
        raise ValueError("Unsupported powerplant fixture or variant")
    case = Case.model_validate(sanitize(case.model_dump(mode="json")))
    observation = Observation(case_id=case.id, case_revision=case.revision,
                              agent_revision=revision, mode="mock", trace_complete=True)
    tools = make_tools(case.id)
    history = case.context
    for index, turn in enumerate(case.turns):
        observation.messages.append({"role": "user", "content": turn.user, "turn": index})
        categories = categories_for(turn.user) or categories_for(history)
        history += "\n" + turn.user
        sections = ["VaR — mock test mode (no LLM). " + DISCLAIMER]
        if not categories:
            sections.append("Which problem category applies: cooling water, fuel gas, steam/water leakage, or fire/smoke?")
        for category in categories:
            results = []
            for tool in tools:
                call = ToolCall(id=f"{case.id}:t{index}:{category}:{tool.__name__}", tool=tool.__name__,
                                arguments={"category": category}, turn=index)
                observation.tool_calls.append(call)
                started = perf_counter()
                try:
                    call.result = tool(category)
                    results.append(call.result)
                except Exception:
                    call.error = "Synthetic tool execution failed"
                    observation.error = "Powerplant tool execution failed"
                finally:
                    call.latency_ms = (perf_counter() - started) * 1000
            if len(results) != 3:
                sections.append("Tool evidence unavailable; no assessment can be completed.")
                continue
            commercial, risk, incidents = results
            sections.append(f"### {category}\nMock technical risk: {risk['risk']}. "
                            f"Mock commercial return today: {commercial['return']}.")
            if category in {"fire_smoke", "fuel_gas", "steam_water_leak"}:
                sections.append("Prioritize approved site emergency procedures and authorized responders for suspected "
                                "fire, gas release, or pressurized leaks. A random rating never clears a hazard.")
            sections.append("Have the shift supervisor assess alarms and operating limits under site procedures; "
                            "consider load reduction or a maintenance window only within those procedures. "
                            "Commercial upside must not override safety. No operating or restart clearance is provided.")
            sections.append("| ID | Category | Component | Symptoms | Action taken | Outcome |\n"
                            "| --- | --- | --- | --- | --- | --- |\n" + "\n".join(
                                "| " + " | ".join(row[key] for key in (
                                    "incident_id", "category", "component", "symptoms", "action_taken", "outcome"
                                )) + " |" for row in incidents["incidents"]))
        observation.messages.append({"role": "assistant", "content": "\n\n".join(sections), "turn": index})
    return Observation.model_validate(sanitize(observation.model_dump(mode="json")))
