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

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(tempfile.gettempdir()) / "opencode" / "goldenloop-browser-artifacts"


def screenshot(page, name):
    page.evaluate("() => { document.activeElement?.blur(); window.scrollTo(0, 0); }")
    page.screenshot(path=str(ARTIFACTS / name), full_page=True)


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


def test_review_publish_fail_fix_export(workbench, browser_page):
    base, directory = workbench
    page = browser_page
    page.goto(base + "/candidates")
    page.get_by_role("button", name="New candidate", exact=True).click()
    page.get_by_label("Case title", exact=True).fill("Browser customer regression")
    page.get_by_label("User message", exact=True).fill("Look up customer C-123")
    page.get_by_label("Required tool", exact=False).fill("lookup_customer")
    page.get_by_label("Argument path", exact=False).fill("customer_id")
    page.get_by_label("Argument equals", exact=False).fill('"C-123"')
    page.get_by_role("button", name="Save candidate", exact=True).click()
    page.get_by_label("Approval reason", exact=True).fill("Reviewed synthetic customer fixture")
    page.get_by_label("I reviewed the inputs", exact=False).check()
    page.get_by_role("button", name="Approve revision 1", exact=True).click()
    expect(page.get_by_text("This revision is approved and eligible", exact=False)).to_be_visible()

    page.goto(base + "/releases")
    page.get_by_role("button", name="Create release", exact=True).click()
    page.get_by_label("Release name", exact=True).fill("Browser baseline")
    page.get_by_label("Browser customer regression", exact=False).check()
    page.get_by_label("Publish an immutable release", exact=False).check()
    page.get_by_role("button", name="Publish 1 selected cases", exact=True).click()
    expect(page.get_by_role("link", name="Export test bundle", exact=True)).to_be_visible()
    release_id = parse_qs(urlsplit(page.url).query)["release"][0]
    with page.expect_download() as downloaded:
        page.get_by_role("link", name="Export test bundle", exact=True).click()
    assert downloaded.value.suggested_filename.endswith(".zip")
    downloaded.value.save_as(directory / "release.zip")
    assert (directory / "release.zip").stat().st_size > 0

    run_ids = []
    for revision, gate in [("buggy", "fail"), ("fixed", "pass")]:
        page.goto(base + f"/runs?release={release_id}")
        page.get_by_role("combobox", name="Agent revision", exact=True).select_option(revision)
        page.get_by_role("button", name="Start evaluation", exact=True).click()
        expect(page.locator(".run-signals")).to_contain_text("completed", timeout=20000)
        expect(page.locator(".run-signals .status").nth(1)).to_have_text(gate)
        run_ids.append(parse_qs(urlsplit(page.url).query)["run"][0])
        page.locator(".result-case > summary").click()
        expect(page.get_by_text("tool_arguments", exact=True).first).to_be_visible()
    screenshot(page, "desktop-results.png")
    page.get_by_role("combobox", name=re.compile("Compare with another run")).select_option(run_ids[0])
    expect(page.get_by_role("heading", name="Same dataset. Different behavior.")).to_be_visible()
    expect(page.locator(".comparison tbody td > .status").nth(0)).to_have_text("pass")
    expect(page.locator(".comparison tbody td > .status").nth(1)).to_have_text("fail")


def test_chat_feedback_candidate(workbench, browser_page):
    base, _ = workbench
    page = browser_page
    page.goto(base + "/playground")
    page.get_by_role("button", name="New session", exact=True).click()
    page.get_by_label("Session title", exact=False).fill("Browser investigation")
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
    for route in ["/", "/import", "/playground", "/candidates", "/releases", "/runs"]:
        page.goto(base + route)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(page.locator(".state")).to_have_count(0)
        expect(page.locator(".error-state")).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), route
        if route == "/candidates" and width == 390:
            scroller = page.locator(".table-scroll")
            assert scroller.evaluate("el => el.scrollWidth > el.clientWidth")
            scroller.evaluate("el => el.scrollLeft = el.scrollWidth")
            expect(page.get_by_role("button", name=re.compile(f"Review Responsive populated case {width}"))).to_be_in_viewport()
            screenshot(page, "candidates-390-scrolled.png")
            scroller.evaluate("el => el.scrollLeft = 0")
        screenshot(page, f"{route.strip('/') or 'overview'}-{width}.png")
    if width == 390:
        page.get_by_role("button", name="Open navigation").click()
        page.get_by_role("link", name=re.compile("Candidates")).click()
        expect(page.get_by_role("heading", level=1)).to_have_text("Nothing golden without review.")
