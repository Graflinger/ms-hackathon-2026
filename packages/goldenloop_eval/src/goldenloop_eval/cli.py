import argparse
import asyncio
import importlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import TypeAdapter

from . import (
    SDK_VERSION, BundleManifest, Case, Evaluation, Judge, Observation, evaluate,
    OpenAIJudge, release_hash, sanitize,
)

AgentRunner = Callable[..., Awaitable[Observation]]
MAX_JSON_BYTES = 10 * 1024 * 1024


def _read_json(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds 10 MiB limit")
    return json.loads(raw)


def load_cases(path: str | Path) -> list[Case]:
    cases = TypeAdapter(list[Case]).validate_python(_read_json(Path(path)))
    if not cases:
        raise ValueError("No selected cases")
    release_hash(cases)
    return cases


def load_bundle(path: str | Path) -> tuple[BundleManifest, list[Case]]:
    root = Path(path).resolve()
    manifest = BundleManifest.model_validate(_read_json(root / "manifest.json"))
    cases_path = (root / manifest.cases_file).resolve()
    if cases_path.parent != root:
        raise ValueError("Bundle cases must reside inside the bundle")
    cases = load_cases(cases_path)
    if release_hash(cases) != manifest.content_hash:
        raise ValueError("Bundle content hash mismatch")
    return manifest, cases


async def run_cases(
    cases: list[Case], runner: AgentRunner, *, revision: str = "fixed", mode: str = "mock",
    judge: Judge | None = None, timeout: float = 120,
) -> list[Evaluation]:
    """Thin exported wrappers supply an async runner; SDK never imports the demo."""
    if not cases or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Cases and a positive finite timeout are required")
    if mode not in {"mock", "live"}:
        raise ValueError("Use supplied observations for recorded scoring")
    release_hash(cases)
    results = []
    for case in cases:
        try:
            async with asyncio.timeout(timeout):
                observation = Observation.model_validate(await runner(case, revision=revision, mode=mode))
            if observation.mode != mode or observation.agent_revision != revision:
                raise ValueError("Agent adapter returned mismatched execution lineage")
        except Exception as exc:
            observation = Observation(
                case_id=case.id, case_revision=case.revision, agent_revision=revision, mode=mode,
                error=f"Agent execution failed ({type(exc).__name__})", trace_complete=False,
            )
        results.append(evaluate(case, observation, judge=judge))
    return results


def build_report(results: list[Evaluation], **lineage) -> dict:
    gate = ("error" if not results or any(r.gate == "error" for r in results)
            else "fail" if any(r.gate == "fail" for r in results) else "pass")
    return sanitize({"schema_version": "1", "sdk_version": SDK_VERSION, **lineage,
                     "gate": gate, "evaluations": [r.model_dump(mode="json") for r in results]})


def write_junit(report: dict, path: str | Path) -> None:
    results = report.get("evaluations", [])
    suite = ET.Element("testsuite", name="GoldenLoop", tests=str(max(1, len(results))),
                       failures=str(sum(r["gate"] == "fail" for r in results)),
                       errors=str(sum(r["gate"] == "error" for r in results) if results else 1))
    if not results:
        case = ET.SubElement(suite, "testcase", name="configuration")
        ET.SubElement(case, "error", message=report.get("error", "No selected cases"))
    for result in results:
        case = ET.SubElement(suite, "testcase", name=f"{result['case_id']}@{result['case_revision']}",
                             classname=f"{result['agent_revision']}.{result['mode']}")
        if result["gate"] != "pass":
            details = "; ".join(c["reason"] for c in result["checks"] if c["status"] != "pass")
            ET.SubElement(case, "failure" if result["gate"] == "fail" else "error", message=details)
        ET.SubElement(case, "system-out").text = json.dumps(result, ensure_ascii=True)
    # XML 1.0 forbids most control characters, including in user-supplied evidence.
    text = ET.tostring(suite, encoding="unicode")
    text = "".join(c for c in text if c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF
                   or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)
    Path(path).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None, *, runner: AgentRunner | None = None) -> int:
    parser = argparse.ArgumentParser(prog="goldenloop-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("evaluate", "bundle"):
        sub = subparsers.add_parser(command)
        sub.add_argument("path", type=Path, help="Cases JSON file or bundle directory")
        sub.add_argument("--adapter", help="Trusted installed async callable, module:function")
        sub.add_argument("--observations", type=Path, help="Score recorded observations, no invocation")
        sub.add_argument("--json", type=Path, dest="json_path", help="Also write JSON report to file")
        sub.add_argument("--junit", type=Path)
        sub.add_argument("--timeout", type=float, default=120)
        sub.add_argument("--judge", choices=["azure"], help="Explicit live Azure judge from GOLDENLOOP_JUDGE_* settings")
        if command == "evaluate":
            sub.add_argument("--revision", default="fixed")
            sub.add_argument("--mode", choices=["mock", "live", "recorded"], default="mock")
    args = parser.parse_args(argv)
    judge = None
    try:
        if args.judge:
            judge = OpenAIJudge.from_azure_env()
        if args.command == "bundle":
            manifest, cases = load_bundle(args.path)
            revision, mode = manifest.agent_revision, manifest.mode
            lineage = manifest.model_dump(mode="json")
        else:
            cases = load_cases(args.path)
            revision, mode = args.revision, args.mode
            lineage = {"content_hash": release_hash(cases), "agent_revision": revision, "mode": mode}
        if args.observations:
            if mode != "recorded" or args.adapter:
                raise ValueError("Recorded observations require recorded mode and no adapter")
            observations = TypeAdapter(list[Observation]).validate_python(_read_json(args.observations))
            keyed = {(o.case_id, o.case_revision): o for o in observations}
            if len(keyed) != len(observations) or set(keyed) != {(c.id, c.revision) for c in cases}:
                raise ValueError("Recorded observations must match the selected cases exactly")
            if any(o.mode != "recorded" or o.agent_revision != revision for o in observations):
                raise ValueError("Recorded observation lineage mismatch")
            results = [evaluate(c, keyed[c.id, c.revision], judge=judge) for c in cases]
        else:
            if args.adapter:
                module, name = args.adapter.split(":", 1)
                runner = getattr(importlib.import_module(module), name)
            if runner is None:
                raise ValueError("Provide --adapter module:function or --observations")
            results = asyncio.run(run_cases(cases, runner, revision=revision, mode=mode, timeout=args.timeout, judge=judge))
        report = build_report(results, **lineage)
    except Exception as exc:
        report = build_report([])
        report["error"] = f"Configuration/execution failed ({type(exc).__name__})"
    finally:
        if judge is not None:
            try:
                judge.client.close()
            except Exception:
                report = build_report([])
                report["error"] = "Judge client cleanup failed"
    try:
        payload = json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False)
        if args.json_path:
            args.json_path.write_text(payload + "\n", encoding="utf-8")
        if args.junit:
            write_junit(report, args.junit)
        print(payload)
    except (OSError, ValueError):
        print('{"gate":"error","error":"Unable to write report"}', file=sys.stderr)
        return 2
    return {"pass": 0, "fail": 1, "error": 2}[report["gate"]]
