import asyncio
import json
import xml.etree.ElementTree as ET

import pytest

from goldenloop_eval import SDK_VERSION, Case, Observation, evaluate, release_hash
from goldenloop_eval.cli import build_report, load_bundle, load_cases, main, run_cases, write_junit


def sample():
    return Case(id="case", title="Synthetic", turns=[{"user": "hello"}],
                checks=[{"id": "answer", "kind": "exact_match", "config": {"value": "ok"}}])


async def runner(case, revision="fixed", mode="mock"):
    return Observation(case_id=case.id, case_revision=case.revision, agent_revision=revision, mode=mode,
                       messages=[{"role": "assistant", "turn": 0, "content": "ok"}], trace_complete=True)


def bundle(tmp_path, **updates):
    case = sample()
    (tmp_path / "cases.json").write_text(json.dumps([case.model_dump(mode="json")]), encoding="utf-8")
    manifest = {"schema_version": "1", "sdk_version": SDK_VERSION, "release_id": "unit-release",
                "content_hash": release_hash([case]), "cases_file": "cases.json", "agent_revision": "fixed", "mode": "mock", **updates}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return case


def test_bundle_roundtrip_and_primitive_parity(tmp_path, capsys):
    case = bundle(tmp_path)
    manifest, cases = load_bundle(tmp_path)
    assert cases == [case]
    results = asyncio.run(run_cases(cases, runner, revision=manifest.agent_revision, mode=manifest.mode))
    assert results[0] == evaluate(case, asyncio.run(runner(case)))
    assert main(["bundle", str(tmp_path), "--junit", str(tmp_path / "report.xml"),
                 "--json", str(tmp_path / "report.json")], runner=runner) == 0
    assert json.loads(capsys.readouterr().out)["gate"] == "pass"
    assert ET.parse(tmp_path / "report.xml").getroot().attrib["failures"] == "0"


@pytest.mark.parametrize("updates", [
    {"schema_version": "2"}, {"sdk_version": "99"}, {"cases_file": "../cases.json"},
    {"content_hash": "0" * 64}, {"mode": "magic"},
])
def test_invalid_manifest(tmp_path, updates):
    bundle(tmp_path, **updates)
    with pytest.raises(ValueError):
        load_bundle(tmp_path)
    assert main(["bundle", str(tmp_path)], runner=runner) == 2


def test_tampered_and_empty_cases(tmp_path):
    case = bundle(tmp_path)
    case.turns[0].user = "modified"
    (tmp_path / "cases.json").write_text(json.dumps([case.model_dump(mode="json")]))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_bundle(tmp_path)
    (tmp_path / "cases.json").write_text("[]")
    with pytest.raises(ValueError, match="No selected"):
        load_cases(tmp_path / "cases.json")


@pytest.mark.parametrize("mode", ["failure", "exception", "timeout", "wrong_revision", "wrong_mode"])
def test_exit_codes(tmp_path, mode):
    bundle(tmp_path)
    async def bad(case, revision, mode):
        if behavior == "exception":
            raise RuntimeError("secret-should-not-appear")
        if behavior == "timeout":
            await asyncio.sleep(1)
        obs = await runner(case, revision, mode)
        if behavior == "failure":
            obs.messages[0]["content"] = "wrong"
        if behavior == "wrong_revision":
            obs.agent_revision = "wrong"
        if behavior == "wrong_mode":
            obs.mode = "live"
        return obs
    behavior = mode
    code = main(["bundle", str(tmp_path), "--timeout", "0.01"], runner=bad)
    assert code == (1 if mode == "failure" else 2)


def test_recorded_scoring(tmp_path):
    case = bundle(tmp_path, mode="recorded")
    obs = asyncio.run(runner(case, mode="recorded"))
    path = tmp_path / "observations.json"
    path.write_text(json.dumps([obs.model_dump(mode="json")]))
    assert main(["bundle", str(tmp_path), "--observations", str(path)]) == 0
    path.write_text("[]")
    assert main(["bundle", str(tmp_path), "--observations", str(path)]) == 2


def test_configuration_errors_and_empty_report(tmp_path):
    bundle(tmp_path)
    assert main(["bundle", str(tmp_path)]) == 2
    assert main(["bundle", str(tmp_path), "--adapter", "does_not_exist:run"]) == 2
    assert main(["bundle", str(tmp_path), "--timeout", "nan"], runner=runner) == 2
    assert main(["evaluate", str(tmp_path / "cases.json"), "--mode", "recorded"], runner=runner) == 2
    report = build_report([])
    assert report["gate"] == "error"
    write_junit(report, tmp_path / "empty.xml")
    assert ET.parse(tmp_path / "empty.xml").getroot().find("testcase/error") is not None


def test_adapter_import(tmp_path):
    bundle(tmp_path)
    assert main(["bundle", str(tmp_path), "--adapter", f"{__name__}:runner"]) == 0


def test_cancellation_is_not_swallowed():
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_cases([sample()], cancelled))


def test_junit_error_fail_and_xml_controls(tmp_path):
    case = sample()
    obs = asyncio.run(runner(case))
    obs.messages[0]["content"] = "wrong\x00<&"
    result = evaluate(case, obs)
    report = build_report([result])
    write_junit(report, tmp_path / "failure.xml")
    root = ET.parse(tmp_path / "failure.xml").getroot()
    assert root.attrib["failures"] == "1"
    assert root.find("testcase/failure") is not None
