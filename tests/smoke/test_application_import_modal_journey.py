"""A person importing a file through the Applications modal sees what was stored and why a row was refused.

The same three rows go in as a CSV file, an Excel file and a JSON file: Alpha (design),
Bravo (an unknown status) and Charlie (Live). The modal says "2 stored", names Bravo's
row with the one refusal sentence, and stays open until the person closes it. The
rows are in the signed-in organisation and not in a second one.
"""

import io
import json
import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PERSONA = "enterprise_architect"


def _shot(page, tmp_path, name, dark=False):
    folder = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    page.evaluate("d => document.documentElement.classList.toggle('dark', d)", dark)
    width = page.viewport_size["width"]
    page.screenshot(path=str(folder / f"{name}-{width}-{'dark' if dark else 'light'}.png"),
                    full_page=False)
    page.evaluate("document.documentElement.classList.remove('dark')")


def _shots(page, tmp_path, name):
    """The screen at 1440 wide in the light theme and at 390 wide in the dark one."""
    page.set_viewport_size({"width": 1440, "height": 900})
    _shot(page, tmp_path, name)
    page.set_viewport_size({"width": 390, "height": 844})
    _shot(page, tmp_path, name, dark=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def _stored(org_id, names):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        rows = ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == org_id,
            ApplicationComponent.name.in_(names)).all()
        out = {row.name: row.deployment_status for row in rows}
        db.session.remove()
    return out


def _file(kind, rows):
    if kind == "csv":
        text = "Name,Status\n" + "".join(f"{n},{s}\n" for n, s in rows)
        return "apps.csv", "text/csv", text.encode()
    if kind == "json":
        body = json.dumps([{"name": n, "deployment_status": s} for n, s in rows])
        return "apps.json", "application/json", body.encode()
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Name", "Status"])
    for row in rows:
        sheet.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return ("apps.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", out.getvalue())


@pytest.fixture
def orgs():
    return [fresh_org.create_fresh_org(PERSONA, org_admin=False) for _ in range(2)]


def _sentence(word):
    from app.services.application_status import refusal_for

    return refusal_for(word)


@pytest.mark.parametrize("kind", ["csv", "xlsx", "json"])
def test_the_modal_says_two_stored_and_why_the_third_row_was_refused(
    page, live_server, orgs, tmp_path, kind
):
    org_a, org_b = orgs
    tag = uuid.uuid4().hex[:6]
    rows = [(f"Alpha {tag}", "design"), (f"Bravo {tag}", "Mystery"), (f"Charlie {tag}", "Live")]
    filename, mime, payload = _file(kind, rows)
    fresh_org.sign_in(page, live_server, org_a["emails"][PERSONA])
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server + "/applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("btn-import").click()
    dialog = page.get_by_role("dialog", name="Import Applications", exact=True)
    expect(dialog).to_be_visible()
    page.locator("#excel-file-input").set_input_files(
        {"name": filename, "mimeType": mime, "buffer": payload})
    dialog.get_by_role("button", name="Run direct import", exact=True).click()
    import_button = dialog.get_by_role("button", name="Import applications", exact=True)
    expect(import_button).to_be_visible(timeout=PAGE_TIMEOUT)

    with page.expect_response(lambda r: r.url.endswith("/applications/import")
                              and r.request.method == "POST", timeout=PAGE_TIMEOUT) as sent:
        import_button.click()
    reply = sent.value.json()
    assert reply["stored"] == 2 and len(reply["refused"]) == 1, reply

    result = page.get_by_role("dialog", name="Import saved", exact=True)
    expect(result).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(result.get_by_text("2 stored", exact=True)).to_be_visible()
    # A person can see it: the element at the middle of the "2 stored" line is that line,
    # not another layer drawn over the dialog.
    covered = result.get_by_text("2 stored", exact=True).evaluate("""el => {
        const box = el.getBoundingClientRect();
        const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        return top === null || !el.contains(top);
    }""")
    assert not covered, "the result is behind another layer"
    expect(result.get_by_text("1 refused:", exact=True)).to_be_visible()
    # Bravo's row, by its number in the file, with the one sentence.
    row_number = 2 if kind == "json" else 3
    line = result.get_by_text(f"Row {row_number} (Bravo {tag}): ", exact=False)
    expect(line).to_be_visible()
    assert _sentence("Mystery") in line.inner_text()
    _shots(page, tmp_path, f"import-modal-{kind}")
    # It stays open: nothing closes it but the person.
    page.wait_for_timeout(1500)
    expect(result).to_be_visible()

    stored = _stored(org_a["org_id"], [name for name, _ in rows])
    assert stored == {rows[0][0]: "design", rows[2][0]: "production"}, stored
    assert _stored(org_b["org_id"], [name for name, _ in rows]) == {}

    # Closing it reloads the list, which now holds the two stored applications.
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        result.get_by_role("button", name="Done — refresh applications", exact=True).click()
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    body = page.locator("main").inner_text()
    assert f"Alpha {tag}" in body and f"Charlie {tag}" in body
    assert f"Bravo {tag}" not in body, "the refused row appears in the list"
