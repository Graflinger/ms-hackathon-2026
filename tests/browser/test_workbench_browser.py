"""Real browser workflows against isolated loopback API and production Vite preview."""

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import zipfile

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(tempfile.gettempdir()) / "opencode" / "goldenloop-browser-artifacts"
DEMO = "/projects/synthetic-demo"


def screenshot(page, name):
    page.evaluate("() => { document.activeElement?.blur(); window.scrollTo(0, 0); }")
    page.screenshot(path=str(ARTIFACTS / name), full_page=True, animations="disabled")


@pytest.fixture
def browser_page(request):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors, console, external = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: console.append({"type": message.type, "text": message.text}))
        page.on("request", lambda req: external.append(req.url)
                if urlsplit(req.url).hostname not in ("127.0.0.1", "localhost", None) else None)
        try:
            yield page
            assert not errors, errors
            assert not [message for message in console if message["type"] == "error"], console
            assert not external, external
        finally:
            name = re.sub(r"[^a-zA-Z0-9_-]", "_", request.node.name)
            screenshot(page, f"{name}.png")
            (ARTIFACTS / f"{name}.json").write_text(json.dumps({
                "url": page.url, "errors": errors, "console": console, "external_requests": external,
                "layout": page.evaluate("""() => ({viewport: innerWidth, document: document.documentElement.scrollWidth,
                    overflow: [...document.querySelectorAll('*')]
                      .filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                      .map(el => ({tag: el.tagName, class: el.className, width: el.getBoundingClientRect().width,
                                   right: el.getBoundingClientRect().right}))})"""),
            }, indent=2), encoding="utf-8")
            browser.close()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def workbench(tmp_path_factory):
    directory = tmp_path_factory.mktemp("browser-workbench")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AZURE_", "GOLDENLOOP_"))}
    env.update(GOLDENLOOP_LOCAL_DEMO="true", GOLDENLOOP_DATA_DIR=str(directory / "data"))
    subprocess.run([sys.executable, "-m", "goldenloop_api.bootstrap"], env=env, check=True,
                   capture_output=True, timeout=30)
    api_port, web_port = free_port(), free_port()
    env["GOLDENLOOP_API_PROXY"] = f"http://127.0.0.1:{api_port}"
    node = shutil.which("node")
    assert node, "Node.js is required for browser tests"
    assert (ROOT / "apps/web/dist/index.html").exists(), "Run npm run build in apps/web first"
    commands = [
        [sys.executable, "-m", "uvicorn", "goldenloop_api.main:app", "--host", "127.0.0.1",
         "--port", str(api_port), "--no-proxy-headers"],
        [node, str(ROOT / "apps/web/node_modules/vite/bin/vite.js"), "preview", "--host", "127.0.0.1",
         "--port", str(web_port), "--strictPort"],
    ]
    processes, logs = [], []
    try:
        for index, command in enumerate(commands):
            log = (directory / f"server-{index}.log").open("w+", encoding="utf-8")
            logs.append(log)
            processes.append(subprocess.Popen(command, cwd=ROOT / "apps/web", env=env,
                                               stdout=log, stderr=subprocess.STDOUT))
        base = f"http://127.0.0.1:{web_port}"
        with httpx.Client(trust_env=False, timeout=2) as client:
            for _ in range(150):
                try:
                    if client.get(base + "/api/v1/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if any(process.poll() is not None for process in processes):
                    pytest.fail(f"Workbench process exited; inspect {directory}")
                time.sleep(0.2)
            else:
                pytest.fail(f"Workbench did not become ready; inspect {directory}")
        yield base, directory
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        for log in logs:
            log.close()


def test_project_agent_revisions_import_review_publish_fail_fix_export(workbench, browser_page):
    base, directory = workbench
    page = browser_page
    page.goto(base + "/")
    page.get_by_role("button", name="New project", exact=True).click()
    page.get_by_label("Project name", exact=True).fill("Browser customer project")
    page.get_by_role("button", name="Create project", exact=True).click()
    page.get_by_role("link", name="Open project Browser customer project", exact=True).click()
    expect(page).to_have_url(re.compile(r"/projects/[^/]+$"))
    project_path = urlsplit(page.url).path
    project_id = project_path.rsplit("/", 1)[-1]
    api_path = base + "/api/v2/projects/" + project_id
    page.get_by_role("link", name=re.compile(r"^Agents")).click()
    page.get_by_role("button", name="New agent", exact=True).click()
    page.get_by_label("Agent name", exact=True).fill("Browser customer agent")
    page.get_by_role("button", name="Create agent", exact=True).click()
    expect(page.get_by_role("heading", name="Revision history", exact=True)).to_be_visible()
    agent_id = parse_qs(urlsplit(page.url).query)["agent"][0]
    revisions = {}
    for variant in ("buggy", "fixed"):
        page.get_by_role("button", name="New revision", exact=True).click()
        page.get_by_label("Revision label", exact=True).fill("Browser " + variant)
        page.get_by_role("combobox", name="Behavior variant", exact=True).select_option(variant)
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and response.url == api_path + f"/agents/{agent_id}/revisions") as created:
            page.get_by_role("button", name="Create revision", exact=True).click()
        assert created.value.status == 201, created.value.text()
        revisions[variant] = created.value.json()
        expect(page.get_by_text(f"Browser {variant} / r{len(revisions)}", exact=True)).to_be_visible()
    assert revisions["buggy"]["id"] != revisions["fixed"]["id"]
    assert revisions["buggy"]["spec_hash"] != revisions["fixed"]["spec_hash"]

    page.goto(base + project_path + "/import")
    page.get_by_label("Choose an Excel (.xlsx) or CSV file", exact=True).set_input_files({
        "name": "customer.csv", "mimeType": "text/csv",
        "buffer": b"Scenario,Question\nBrowser customer regression,Look up customer C-123\n",
    })
    page.get_by_role("button", name="Preview file", exact=True).click()
    expect(page.get_by_role("heading", name="Inspect the source")).to_be_visible()
    page.locator("#map-title").select_option("Scenario")
    page.locator("#map-user").select_option("Question")
    page.get_by_label("I reviewed the preview and mapping", exact=False).check()
    page.get_by_role("button", name="Commit import", exact=True).click()
    expect(page.get_by_text("1 candidates created.", exact=False)).to_be_visible()
    page.get_by_role("link", name="Continue to the review desk").click()
    page.get_by_role("row").filter(has_text="Browser customer regression").get_by_role("button", name=re.compile("Review")).click()
    page.get_by_role("button", name="Edit as new revision", exact=True).click()
    editor = page.get_by_role("textbox", name="Canonical case JSON", exact=True)
    case = json.loads(editor.input_value())
    case["checks"] = [{
        "id": "browser-exact-customer", "kind": "tool_arguments", "required": True, "turn": 0,
        "config": {"tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123"},
    }]
    editor.fill(json.dumps(case))
    page.get_by_label("Revision reason", exact=True).fill("Reviewed exact customer tool requirement")
    page.get_by_role("button", name="Save new candidate revision", exact=True).click()
    page.get_by_label("Approval reason", exact=True).fill("Reviewed synthetic customer fixture")
    page.get_by_label("I reviewed the inputs", exact=False).check()
    page.get_by_role("button", name="Approve revision 2", exact=True).click()
    expect(page.get_by_text("This revision is approved and eligible", exact=False)).to_be_visible()

    page.goto(base + project_path + "/releases")
    page.get_by_role("button", name="Create release", exact=True).click()
    page.get_by_label("Release name", exact=True).fill("Browser baseline")
    page.get_by_label("Browser customer regression", exact=False).check()
    page.get_by_label("Publish an immutable release", exact=False).check()
    page.get_by_role("button", name="Publish 1 selected cases", exact=True).click()
    export = page.get_by_role("button", name="Export test bundle", exact=True)
    expect(export).to_be_disabled()
    release_id = parse_qs(urlsplit(page.url).query)["release"][0]
    page.get_by_role("combobox", name="Agent", exact=True).select_option(agent_id)
    page.get_by_role("combobox", name="Agent revision", exact=True).select_option(revisions["fixed"]["id"])
    expect(export).to_be_disabled()
    page.get_by_role("combobox", name="Export execution mode", exact=True).select_option("mock")
    with page.expect_download() as downloaded:
        export.click()
    assert downloaded.value.suggested_filename.endswith(".zip")
    downloaded.value.save_as(directory / "release.zip")
    with zipfile.ZipFile(directory / "release.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == "2"
        assert manifest["project_id"] == project_id and manifest["release_id"] == release_id
        assert manifest["agent_id"] == agent_id and manifest["agent_revision"] == revisions["fixed"]["id"]
        assert manifest["agent_spec_hash"] == revisions["fixed"]["spec_hash"]
        assert manifest["mode"] == "mock" and manifest["judge"]["selection"] == "none"
        assert json.loads(archive.read("cases.json"))[0]["revision"] == 2

    run_ids = []
    for revision, gate in [("buggy", "fail"), ("fixed", "pass")]:
        page.goto(base + project_path + f"/runs?release={release_id}")
        page.get_by_role("combobox", name="Agent", exact=True).select_option(agent_id)
        page.get_by_role("combobox", name="Agent revision", exact=True).select_option(revisions[revision]["id"])
        page.get_by_role("button", name="Start evaluation", exact=True).click()
        expect(page.locator(".run-signals")).to_contain_text("completed", timeout=20000)
        expect(page.locator(".run-signals .status").nth(1)).to_have_text(gate)
        run_ids.append(parse_qs(urlsplit(page.url).query)["run"][0])
        run_response = page.request.get(api_path + f"/evaluation-runs/{run_ids[-1]}")
        assert run_response.ok, run_response.text()
        run = run_response.json()
        assert run["agent_revision_id"] == revisions[revision]["id"]
        assert run["lineage"]["content_hash"] == manifest["content_hash"]
        page.locator(".result-case > summary").click()
        expect(page.get_by_text("tool_arguments", exact=True).first).to_be_visible()
    screenshot(page, "desktop-results.png")
    page.get_by_role("combobox", name=re.compile("Compare with another run")).select_option(run_ids[0])
    expect(page.get_by_role("heading", name="Same dataset. Different behavior.")).to_be_visible()
    expect(page.locator(".comparison tbody td > .status").nth(0)).to_have_text("pass")
    expect(page.locator(".comparison tbody td > .status").nth(1)).to_have_text("fail")

    # Switch without reloading to detect project query-cache leakage, including detail selections.
    page.get_by_role("combobox", name="Current project", exact=True).select_option("synthetic-demo")
    expect(page).to_have_url(base + DEMO)
    expect(page.get_by_text("Browser customer regression", exact=True)).to_have_count(0)
    for route, hidden in (("candidates", "Browser customer regression"), ("releases", "Browser baseline"),
                          ("runs", "Browser baseline"), ("agents", "Browser customer agent")):
        page.locator(f'nav a[href="{DEMO}/{route}"]').click()
        expect(page).to_have_url(base + DEMO + "/" + route)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(page.locator(".state")).to_have_count(0)
        expect(page.get_by_text(hidden, exact=True)).to_have_count(0)
        assert "?" not in page.url
    page.get_by_role("combobox", name="Current project", exact=True).select_option(project_id)
    expect(page).to_have_url(base + project_path)
    page.locator(f'nav a[href="{project_path}/candidates"]').click()
    expect(page.get_by_role("row").filter(has_text="Browser customer regression")).to_contain_text("approved")


def test_chat_feedback_candidate(workbench, browser_page):
    base, _ = workbench
    page = browser_page
    page.goto(base + "/playground")
    expect(page).to_have_url(base + DEMO + "/playground")
    page.get_by_role("button", name="New session", exact=True).click()
    page.get_by_label("Session title", exact=False).fill("Browser investigation")
    expect(page.get_by_role("button", name="Create session", exact=True)).to_be_disabled()
    page.get_by_role("combobox", name="Agent", exact=True).select_option("synthetic-customer-lookup")
    page.get_by_role("combobox", name="Agent revision", exact=True).select_option("synthetic-fixed")
    page.get_by_role("button", name="Create session", exact=True).click()
    page.get_by_label("Your next turn", exact=True).fill("Look up customer C-123")
    page.get_by_role("button", name="Send message", exact=True).click()
    expect(page.locator(".agent-message")).to_have_count(1, timeout=20000)
    expect(page.locator(".tool-trace")).not_to_have_count(0)
    page.get_by_role("button", name="Give answer feedback", exact=True).click()
    page.get_by_label("Issue type", exact=True).fill("positive")
    page.get_by_label("Reviewer comment", exact=True).fill("Review customer lookup coverage")
    page.get_by_role("button", name="Submit feedback", exact=True).click()
    page.get_by_role("link", name="Review feedback and create a candidate").click()
    page.locator(".feedback-item > summary").click()
    page.get_by_role("button", name="Create unreviewed candidate", exact=True).click()
    expect(page.get_by_text("Candidate created:", exact=False)).to_be_visible()
    page.goto(base + "/candidates")
    row = page.get_by_role("row").filter(has_text="Feedback: Browser investigation")
    expect(row).to_contain_text("candidate")
    row.get_by_role("button", name=re.compile("Review")).click()
    expect(page.get_by_text("No checks authored.", exact=False)).to_be_visible()


def test_import_preview_commit(workbench, browser_page):
    base, _ = workbench
    page = browser_page
    page.goto(base + "/import")
    expect(page).to_have_url(base + DEMO + "/import")
    page.get_by_label("Choose an Excel (.xlsx) or CSV file", exact=True).set_input_files({
        "name": "browser-cases.csv", "mimeType": "text/csv",
        "buffer": b"Scenario,Question,Reference\nBrowser imported case,Find order ORD-1001,Review the order\n",
    })
    page.get_by_role("button", name="Preview file", exact=True).click()
    expect(page.get_by_role("heading", name="Inspect the source")).to_be_visible()
    page.locator("#map-title").select_option("Scenario")
    page.locator("#map-user").select_option("Question")
    page.locator("#map-reference_answer").select_option("Reference")
    page.get_by_label("I reviewed the preview and mapping", exact=False).check()
    page.get_by_role("button", name="Commit import", exact=True).click()
    expect(page.get_by_role("heading", name="Import receipt")).to_be_visible()
    expect(page.get_by_text("1 candidates created.", exact=False)).to_be_visible()
    page.get_by_role("link", name="Continue to the review desk").click()
    expect(page.get_by_role("row").filter(has_text="Browser imported case")).to_contain_text("candidate")


def test_archive_blocks_new_work_but_keeps_history_and_explicit_export(workbench, browser_page):
    base, directory = workbench
    page = browser_page

    def post(path, body, status=201):
        response = page.request.post(base + path, data=body)
        assert response.status == status, response.text()
        return response.json()

    # Arrange published history through the real API, then exercise archive controls in the UI.
    project = post("/api/v2/projects", {"name": "Browser archive project"})
    path = "/api/v2/projects/" + project["id"]
    route = "/projects/" + project["id"]
    agent = post(path + "/agents", {"name": "Archive customer agent"})
    revision = post(path + f"/agents/{agent['id']}/revisions", {"label": "Historical fixed", "spec": {"variant": "fixed"}})
    case = post(path + "/cases", {"case": {
        "title": "Archived customer case", "turns": [{"user": "Look up customer C-123"}],
        "checks": [{"kind": "tool_arguments", "config": {
            "tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123",
        }}],
    }})["case"]
    post(path + f"/cases/{case['id']}/approve", {"revision": 1, "reason": "Reviewed synthetic archive fixture"}, 200)
    release = post(path + "/dataset-releases", {
        "name": "Archived baseline", "case_ids": [case["id"]], "expected_revisions": {case["id"]: 1},
    })
    run = post(path + "/evaluation-runs", {
        "release_id": release["id"], "agent_revision_id": revision["id"], "mode": "mock", "idempotency_key": "archive-browser",
    }, 202)
    page.goto(base + route + f"/runs?run={run['id']}")
    expect(page.locator(".run-signals")).to_contain_text("completed", timeout=20000)
    expect(page.locator(".run-signals .status").nth(1)).to_have_text("pass")

    page.goto(base + route + f"/agents?agent={agent['id']}")
    page.get_by_text("Agent metadata and archive controls", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Archive agent", exact=True).click()
    expect(page.get_by_role("button", name="New revision", exact=True)).to_be_disabled()
    expect(page.get_by_text("Historical fixed / r1", exact=True)).to_be_visible()
    page.get_by_role("button", name="Unarchive agent", exact=True).click()
    expect(page.get_by_role("button", name="New revision", exact=True)).to_be_enabled()

    page.get_by_text("Project settings / Browser archive project", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Archive project", exact=True).click()
    expect(page.get_by_role("button", name="New agent", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="New revision", exact=True)).to_be_disabled()
    for suffix, button in (("candidates", "New candidate"), ("playground", "New session"),
                           ("releases", "Create release"), ("runs", "New evaluation")):
        page.goto(base + route + "/" + suffix)
        expect(page.get_by_role("button", name=button, exact=True)).to_be_disabled()
    page.goto(base + route + f"/runs?run={run['id']}")
    expect(page.locator(".run-signals .status").nth(1)).to_have_text("pass")
    page.goto(base + route + f"/releases?release={release['id']}")
    page.get_by_role("combobox", name="Agent", exact=True).select_option(agent["id"])
    page.get_by_role("combobox", name="Agent revision", exact=True).select_option(revision["id"])
    page.get_by_role("combobox", name="Export execution mode", exact=True).select_option("mock")
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Export test bundle", exact=True).click()
    downloaded.value.save_as(directory / "archived.zip")
    with zipfile.ZipFile(directory / "archived.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["agent_revision"] == revision["id"]
        assert manifest["content_hash"] == release["content_hash"]
    page.get_by_text("Project settings / Browser archive project", exact=True).click()
    page.get_by_role("button", name="Unarchive project", exact=True).click()
    expect(page.get_by_role("button", name="Create release", exact=True)).to_be_enabled()


@pytest.mark.parametrize("width", [1440, 390])
def test_routes_fit_desktop_and_mobile(workbench, width, browser_page):
    base, _ = workbench
    page = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    # Seed through the real API so this test also covers populated tables when run alone.
    response = page.request.post(base + "/api/v1/cases", data={"case": {
        "title": f"Responsive populated case {width}", "turns": [{"user": "Look up customer C-123"}],
    }})
    assert response.ok
    response = page.request.post(base + "/api/v2/projects", data={"name": f"Responsive project {width}"})
    assert response.status == 201, response.text()
    project_id = response.json()["id"]
    api_path = base + "/api/v2/projects/" + project_id
    project_path = "/projects/" + project_id
    response = page.request.post(api_path + "/agents", data={"name": f"Responsive agent {width}"})
    assert response.status == 201, response.text()
    agent_id = response.json()["id"]
    response = page.request.post(api_path + f"/agents/{agent_id}/revisions", data={
        "label": "Responsive immutable revision", "spec": {"variant": "fixed"},
    })
    assert response.status == 201, response.text()
    for route in ["/", project_path, project_path + f"/agents?agent={agent_id}",
                  DEMO, DEMO + "/agents", "/import", "/playground", "/candidates", "/releases", "/runs"]:
        page.goto(base + route)
        if route in ("/import", "/playground", "/candidates", "/releases", "/runs"):
            expect(page).to_have_url(base + DEMO + route)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(page.locator(".state")).to_have_count(0)
        expect(page.locator(".error-state")).to_have_count(0)
        if "?agent=" in route:
            page.get_by_text("Responsive immutable revision / r1", exact=True).click()
            expect(page.get_by_text("Specification hash:", exact=False)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), route
        if route == "/candidates" and width == 390:
            scroller = page.locator(".table-scroll")
            assert scroller.evaluate("el => el.scrollWidth > el.clientWidth")
            scroller.evaluate("el => el.scrollLeft = el.scrollWidth")
            expect(page.get_by_role("button", name=re.compile(f"Review Responsive populated case {width}"))).to_be_in_viewport()
            screenshot(page, "candidates-390-scrolled.png")
            scroller.evaluate("el => el.scrollLeft = 0")
        screenshot(page, f"{re.sub(r'[^a-zA-Z0-9_-]', '-', route).strip('-') or 'projects'}-{width}.png")
    if width == 390:
        page.get_by_role("button", name="Open navigation").click()
        page.get_by_role("link", name=re.compile("Candidates")).click()
        expect(page.get_by_role("heading", level=1)).to_have_text("Nothing golden without review.")
