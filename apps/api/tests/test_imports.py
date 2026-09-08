import io
import zipfile

import pytest
from openpyxl import Workbook


async def upload(client, raw, name="cases.csv", **kwargs):
    return await client.post("/api/v1/imports/preview", files={"file": (name, raw)}, **kwargs)


async def test_csv_preview_mapping_and_commit(client):
    response = await upload(client, b"question,reference_answer,tags\nLook up C-123,C-123,synthetic\n")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["columns"] == ["question", "reference_answer", "tags"]
    assert preview["rows"][0]["row"] == 2 and preview["sheets"] == ["CSV"]
    response = await client.post(
        f"/api/v1/imports/{preview['id']}/commit",
        json={
            "mapping": {"question": "question", "reference_answer": "reference_answer", "tags": "tags"},
            "duplicate_policy": "reject",
        },
    )
    assert response.status_code == 200, response.text
    record = response.json()["cases"][0]
    assert record["status"] == "candidate" and record["case"]["checks"] == []
    assert record["case"]["turns"][0]["reference_answer"] == "C-123"
    assert record["case"]["source"]["import_id"] == preview["id"]
    assert (
        await client.post(
            f"/api/v1/imports/{preview['id']}/commit",
            json={"mapping": {"question": "question"}, "duplicate_policy": "new"},
        )
    ).status_code == 409


async def test_multiturn_grouping(client):
    data = b"scenario_id,turn_index,question\nfirst,0,C-123\nfirst,1,Repeat\nsecond,0,C-999\n"
    preview = (await upload(client, data)).json()
    response = await client.post(
        f"/api/v1/imports/{preview['id']}/commit",
        json={
            "mapping": {name: name for name in ("scenario_id", "turn_index", "question")},
            "duplicate_policy": "new",
        },
    )
    assert response.status_code == 200, response.text
    cases = response.json()["cases"]
    assert len(cases) == 2 and len(cases[0]["case"]["turns"]) == 2


@pytest.mark.parametrize(
    "rows",
    [
        "first,1,C-123\n",
        "first,0,C-123\nfirst,2,Repeat\n",
        "first,0,C-123\nfirst,0,Repeat\n",
        "first,0,C-123\nsecond,0,C-999\nfirst,1,Repeat\n",
        "first,no,C-123\n",
        ",0,C-123\n",
    ],
)
async def test_invalid_multiturn_commit_atomic(client, rows):
    preview = (await upload(client, ("scenario_id,turn_index,question\n" + rows).encode())).json()
    response = await client.post(
        f"/api/v1/imports/{preview['id']}/commit",
        json={
            "mapping": {name: name for name in ("scenario_id", "turn_index", "question")},
            "duplicate_policy": "new",
        },
    )
    assert response.status_code == 422, response.text
    assert (await client.get("/api/v1/cases")).json() == []


async def test_reject_duplicates_rolls_back_entire_commit(client):
    preview = (await upload(client, b"case_id,question\nsame,C-123\nsame,C-999\n")).json()
    body = {"mapping": {"case_id": "case_id", "question": "question"}, "duplicate_policy": "reject"}
    response = await client.post(f"/api/v1/imports/{preview['id']}/commit", json=body)
    assert response.status_code == 409, response.text
    assert (await client.get("/api/v1/cases")).json() == []
    response = await client.post(
        f"/api/v1/imports/{preview['id']}/commit", json={**body, "duplicate_policy": "new"}
    )
    assert response.status_code == 200
    cases = response.json()["cases"]
    assert len(cases) == 2 and cases[0]["case"]["id"] != cases[1]["case"]["id"]


async def test_duplicate_content_and_invalid_mapping(client):
    preview = (await upload(client, b"question\nC-123\nC-123\n")).json()
    path = f"/api/v1/imports/{preview['id']}/commit"
    assert (
        await client.post(path, json={"mapping": {"question": "question"}, "duplicate_policy": "reject"})
    ).status_code == 409
    for mapping in (
        {"checks": "question"},
        {"question": "missing"},
        {},
        {"turn_index": "question", "question": "question"},
    ):
        assert (
            await client.post(path, json={"mapping": mapping, "duplicate_policy": "new"})
        ).status_code == 422


@pytest.mark.parametrize(
    "data,name",
    [
        (b"not excel", "bad.xlsx"),
        (b"macro", "bad.xlsm"),
        (b"old excel", "bad.xls"),
        (b"\xff\xfe", "bad.csv"),
        (b'question\n"unterminated', "bad.csv"),
        (b"question\n\x00bad", "bad.csv"),
    ],
)
async def test_malformed_upload_rejected(client, data, name):
    assert (await upload(client, data, name)).status_code == 422


@pytest.mark.parametrize(
    "data",
    [
        b"question,question\na,b\n",
        b"question,\na,b\n",
        b"question\na,b\n",
        b"question\n=1+1\n",
        b"",
    ],
)
async def test_preview_errors_block_commit(client, data):
    response = await upload(client, data)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["errors"] or any(row["errors"] for row in preview["rows"])
    assert (
        await client.post(
            f"/api/v1/imports/{preview['id']}/commit",
            json={"mapping": {"question": "question"}, "duplicate_policy": "new"},
        )
    ).status_code == 422


async def test_xlsx_sheets_formulas_and_redaction(client):
    workbook = Workbook()
    workbook.active.title = "Good"
    workbook.active.append(["question", "reference"])
    workbook.active.append(["Contact person@example.com", "C-123"])
    other = workbook.create_sheet("Formulas")
    other.append(["question"])
    other.append(['=HYPERLINK("https://attacker.example")'])
    output = io.BytesIO()
    workbook.save(output)
    response = await upload(client, output.getvalue(), "cases.xlsx", data={"sheet": "Good"})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["sheets"] == ["Good", "Formulas"]
    assert "person@example.com" not in response.text
    response = await client.post(
        f"/api/v1/imports/{preview['id']}/commit",
        json={"mapping": {"question": "question"}, "duplicate_policy": "new", "sheet": "Formulas"},
    )
    assert response.status_code == 422
    assert (
        await upload(client, output.getvalue(), "cases.xlsx", data={"sheet": "Absent"})
    ).status_code == 422
    response = await client.post(
        f"/api/v1/imports/{preview['id']}/commit",
        json={"mapping": {"question": "question"}, "duplicate_policy": "new", "sheet": "Good"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "name,content",
    [
        ("xl/vbaProject.bin", b"macro"),
        ("xl/externalLinks/link.xml", b"external"),
        ("xl/worksheets/sheet1.xml", b"a" * (2 * 1024 * 1024)),
        ("../bad", b"bad"),
    ],
    ids=["macro", "external-link", "zip-bomb", "traversal"],
)
async def test_unsafe_workbook_archives(client, name, content):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(name, content)
    assert (await upload(client, output.getvalue(), "unsafe.xlsx")).status_code == 422


async def test_upload_and_row_limits(client, app):
    object.__setattr__(app.state.settings, "max_upload_bytes", 100)
    assert (await upload(client, b"x" * 101)).status_code == 413
    object.__setattr__(app.state.settings, "max_rows", 1)
    assert (await upload(client, b"question\nC-123\nC-999\n")).status_code == 422
