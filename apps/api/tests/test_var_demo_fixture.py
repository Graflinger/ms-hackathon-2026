from pathlib import Path

from goldenloop_api.config import Settings
from goldenloop_api.imports import mapped_cases, parse_upload, preview


def test_var_demo_csv_imports_as_six_unreviewed_scenarios():
    path = Path(__file__).resolve().parents[3] / "fixtures/synthetic/var-demo-scenarios.csv"
    payload = parse_upload(path.read_bytes(), path.name, None, Settings())
    table = preview(payload)
    assert not table["errors"]
    assert all(not row["errors"] for row in table["rows"])
    cases = mapped_cases(payload, {column: column for column in table["columns"]}, None, "demo-import")
    assert len(cases) == 6
    assert len({case.title for case in cases}) == 6
    for case in cases:
        assert case.fixture_version == "synthetic-powerplant-v1"
        assert case.source["synthetic"] is True
        assert "split:development" in case.tags
        assert len(case.turns) == 1
        assert case.turns[0].reference_answer.startswith("Draft reviewer expectation:")
        assert case.checks == []  # Imported expectations are not automatically assertions.
    assert all(category in cases[index].tags for index, category in enumerate(
        ("cooling_water", "fuel_gas", "steam_water_leak", "fire_smoke")
    ))
