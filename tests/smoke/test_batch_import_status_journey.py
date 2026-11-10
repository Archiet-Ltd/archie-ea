"""A data architect imports a file of applications and each one keeps the status the preview promised.

The person uploads a file whose status column holds `design`, `planned`, `Live`,
`Retiring ` and a word nobody knows, opens the job, presses Analyze Import and reads
what the preview says about each row, commits the batch, opens each created
application and finds the status the preview named. At 390 wide the issues table
scrolls inside its own box so the Message column can be read. A person from a
second organisation who opens the job gets the plain not-found page.

Processing a job reads each row with an AI model, which the test server does not
have, so the journey moves the staged rows to the state processing leaves them in
(finished, one approved element each) and then clicks the real Commit button.
"""

import os
import pathlib
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .test_batch_import_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# file word -> (row's issue text the preview shows or None, status page label)
ROWS = [
    ("design", None, "Design"),
    ("planned", "Mapped 'planned' to 'design'", "Design"),
    ("Live", "Mapped 'Live' to 'production'", "Production"),
    ("Retiring ", None, "Retiring"),
    ("Mystery", "Invalid value 'Mystery' for deployment_status", None),
]


def _shot(page, tmp_path, name, dark=False):
    folder = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    page.evaluate("d => document.documentElement.classList.toggle('dark', d)", dark)
    page.screenshot(path=str(folder / f"{name}-{page.viewport_size['width']}-{'dark' if dark else 'light'}.png"))
    page.evaluate("document.documentElement.classList.remove('dark')")


def _issues(page):
    """(row, field, message) for each line of the Validation Issues table."""
    heading = page.get_by_role("heading", name="Validation Issues")
    table = heading.locator("xpath=ancestor::div[contains(@class,'mb-6')][1]").locator("table")
    expect(table.locator("tbody tr").first).to_be_visible(timeout=PAGE_TIMEOUT)
    lines = []
    for row in table.locator("tbody tr").all():
        cells = [c.strip() for c in row.locator("td").all_inner_texts()]
        lines.append((cells[0], cells[1], cells[3]))
    return table, lines


def _stage_as_processed(job_id):
    """Leave the job's rows as processing leaves them: finished, with one approved element each."""
    from app import create_app, db
    from app.models.batch_import import (
        AppProcessingStatus, BatchImportApplication, BatchImportBatch,
        BatchImportElement, BatchStatus, ElementApprovalStatus,
    )

    with create_app("testing").app_context():
        batch = BatchImportBatch.query.filter_by(job_id=job_id).one()
        for staged in BatchImportApplication.query.filter_by(batch_id=batch.id).all():
            staged.status = AppProcessingStatus.COMPLETED
            db.session.add(BatchImportElement(
                batch_id=batch.id, application_id=staged.id, element_type="business_actor",
                element_name="Owner of " + staged.application_name, element_data={"name": "x"},
                archimate_layer="business", approval_status=ElementApprovalStatus.APPROVED,
            ))
        batch.elements_generated = len(ROWS)
        for step in (BatchStatus.PROCESSING, BatchStatus.READY_FOR_REVIEW, BatchStatus.APPROVED):
            batch.status = step
            db.session.flush()
        db.session.commit()
        return batch.id


def test_each_imported_application_keeps_the_status_the_preview_promised(browser, live_server, tmp_path):
    tag = uuid.uuid4().hex[:6]
    names = [f"Status{index} {tag}" for index in range(len(ROWS))]
    csv = tmp_path / "statuses.csv"
    csv.write_text(
        "name,description,business_owner,deployment_status\n"
        + "".join(f"{n},Ledger {i},Ada Owner,{word}\n" for i, (n, (word, _m, _l)) in enumerate(zip(names, ROWS))),
        encoding="utf-8",
    )
    org = create_fresh_org("data_architect")
    outsider = create_fresh_org("data_architect")

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, org["emails"]["data_architect"])
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill("Statuses " + tag)
    page.locator("#fileUpload").set_input_files(str(csv))
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="Create Import Job").first.click()
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    job_url = page.url
    job_id = int(job_url.rstrip("/").rsplit("/", 1)[-1])

    # The preview says, row by row, what it made of each status.
    page.get_by_role("button", name="Analyze Import").click()
    _table, lines = _issues(page)
    said = {}
    for row_number, field, message in lines:
        if field == "deployment_status":
            said.setdefault(row_number, []).append(message)
    # File rows 2 to 6: `design` (2) and `Retiring ` (5) are on the list and draw no
    # complaint; `planned` (3), `Live` (4) and `Mystery` (6) are each explained.
    assert sorted(said) == ["3", "4", "6"], said
    assert said["3"] == [ROWS[1][1]], said
    assert said["4"] == [ROWS[2][1]], said
    assert ROWS[4][1] in said["6"][0], said
    _shot(page, tmp_path, "preview-issues")

    # Commit the batch from its review page.
    batch_id = _stage_as_processed(job_id)
    page.goto(live_server + "/batch-import/batches/%d/review" % batch_id, wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    commit = page.get_by_role("button", name="Commit batch to repository")
    expect(commit).to_be_enabled(timeout=PAGE_TIMEOUT)
    commit.click()
    confirm = page.get_by_role("button", name="Confirm", exact=True)
    expect(confirm).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_response(lambda r: r.url.endswith("/commit") and r.request.method == "POST",
                              timeout=PAGE_TIMEOUT) as committed:
        confirm.click()
    assert committed.value.status == 200, committed.value.status

    # Each created application, opened from the list, shows the status the preview named.
    for name, (word, _promise, label) in zip(names, ROWS):
        page.goto(live_server + "/applications/?search=" + name.replace(" ", "+"), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        link = page.get_by_role("link", name=name).first
        expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
        link.click()
        page.wait_for_url(re.compile(r"/applications/\d+"), timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(name)
        app_url = page.url.split("?")[0]
        page.goto(app_url + "?edit=1&tab=overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        stored = page.locator("#edit-deployment_status").input_value()
        if label is None:
            assert stored == "", f"{word!r} was guessed as {stored!r}"
        else:
            assert stored == label.lower(), f"{word!r} stored as {stored!r}, the preview said {label!r}"
        if label == "Design":
            _shot(page, tmp_path, "created-application")

    # A person from another organisation gets the plain not-found page for the job.
    other = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    foreign = other.new_page()
    _login(foreign, live_server, outsider["emails"]["data_architect"])
    response = foreign.goto(job_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 404, response.status
    assert "statuses.csv" not in foreign.locator("body").inner_text()
    other.close()
    context.close()


def test_the_issues_table_can_be_read_to_the_end_at_390_wide(browser, live_server, tmp_path):
    csv = tmp_path / "narrow.csv"
    csv.write_text("name,deployment_status\nNarrow one,planned\nNarrow two,Live\n", encoding="utf-8")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844})
    page = context.new_page()
    _login(page, live_server, create_fresh_org("data_architect")["emails"]["data_architect"])
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill("Narrow " + uuid.uuid4().hex[:6])
    page.locator("#fileUpload").set_input_files(str(csv))
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="Create Import Job").first.click()
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Analyze Import").click()
    table, lines = _issues(page)
    assert any("Mapped 'planned' to 'design'" in message for _r, _f, message in lines), lines

    box = table.locator("xpath=..")
    assert box.evaluate("e => getComputedStyle(e).overflowX") in ("auto", "scroll")
    box.evaluate("e => { e.scrollLeft = e.scrollWidth; }")
    message_head = table.get_by_role("columnheader", name="Message")
    inside = page.evaluate(
        """([head, box]) => {
            const h = head.getBoundingClientRect(), b = box.getBoundingClientRect();
            return h.left >= b.left - 1 && h.right <= b.right + 1;
        }""",
        [message_head.element_handle(), box.element_handle()],
    )
    assert inside, "the Message column cannot be scrolled into view"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"
    _shot(page, tmp_path, "preview-issues-narrow", dark=True)
    context.close()
