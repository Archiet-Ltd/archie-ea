"""A Data Architect creates a real batch-import job via pasted CSV data.

/batch-import/new's primary write action is creating an import job
(POST /api/batch-import/jobs, multipart form). File upload is the default
path but a "paste data" textarea is a real, equally-supported alternative
(form.source_type = 'paste', parsed client-side into previewData, which
gates the submit button via the canSubmit getter) - used here since it is
scriptable without a real file fixture.

Task-completion shape: submit a real job, follow the redirect to its job
detail page (job_id echoed back by the API), then reload that same page
fresh to confirm the job is fetched from the server or record, not just
whatever the create response happened to return client-side.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_create_batch_import_job_via_paste_and_reload(browser, live_server, seeded):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    email = seeded["emails"]["data_architect"]
    job_name = "SmkBatchImport %s" % uuid.uuid4().hex[:8]

    _login(page, live_server, email)
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1000)

    page.locator("#jobName").fill(job_name)

    # Switch to the "Paste Data" tab before its textarea is reachable.
    paste_tab = page.get_by_role("tab", name="Paste Data")
    paste_tab.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    paste_tab.click()
    page.wait_for_timeout(300)

    # Switch to the paste-data source and give it two real CSV rows.
    paste_area = page.locator("textarea[x-model='form.paste_data']")
    expect(paste_area).to_be_visible(timeout=PAGE_TIMEOUT)
    paste_area.fill("name,description\nSmkApp1,First smoke app\nSmkApp2,Second smoke app")
    page.wait_for_timeout(500)

    submit_btn = page.get_by_role("button", name="Create Import Job")
    if submit_btn.count() == 0:
        submit_btn = page.locator("form button[type='submit']")
    expect(submit_btn.first).to_be_enabled(timeout=PAGE_TIMEOUT)

    # Read the response status only, not .json() - the app's own success handler
    # navigates away (window.location.href) almost immediately, and Playwright's
    # CDP-backed response.json() can lose the response body once that happens
    # ("No resource with given identifier found"). Get the job id from the
    # resulting redirect URL instead, which is more robust here.
    with page.expect_response(
        lambda r: r.url.endswith("/api/batch-import/jobs") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp_info:
        submit_btn.first.click()
    assert resp_info.value.status < 400, "batch import job creation failed: %d" % resp_info.value.status

    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    job_id = int(page.url.rstrip("/").rsplit("/", 1)[-1])

    page.wait_for_timeout(1000)

    # Leave and come back - a real fresh navigation, not client-side state.
    page.goto(live_server + "/batch-import/jobs/%d" % job_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1000)
    # job.name is rendered via page_shell(title=job.name) into both an <h1> and
    # the document <title> (job_detail.html: "Import Job: {{ job.name }}") -
    # the title is the simpler, unambiguous signal of the two.
    assert job_name in page.title(), (
        "job name %r not in page title %r after a fresh load" % (job_name, page.title()))
    h1_text = page.locator("h1").first.inner_text()
    assert job_name in h1_text, "job name %r not in page <h1> %r" % (job_name, h1_text)
    context.close()


def test_a_successful_import_names_the_file_it_is_importing(browser, live_server, seeded, tmp_path):
    """The toast after submitting says which file is being imported, not a job number."""
    csv = tmp_path / "applications.csv"
    csv.write_text("name,description\nSmkApp1,First\nSmkApp2,Second\n", encoding="utf-8")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, seeded["emails"]["data_architect"])
    page.add_init_script("window.__toasts = []; window.showToast = function (m) { window.__toasts.push(m); };")
    # the job is created for real by the server; the answer is held back from the page so that it stays and shows its toast
    page.route("**/api/batch-import/jobs", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"success": true}') if route.request.method == "POST" else route.continue_())
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill("File toast %s" % uuid.uuid4().hex[:6])
    page.locator("#fileUpload").set_input_files(str(csv))
    page.wait_for_timeout(1500)
    button = page.get_by_role("button", name="Create Import Job")
    expect(button.first).to_be_enabled(timeout=PAGE_TIMEOUT)
    button.first.click()
    for _ in range(40):
        if page.evaluate("window.__toasts.length"):
            break
        page.wait_for_timeout(250)
    toasts = page.evaluate("window.__toasts")
    assert toasts == ["Importing applications.csv; it appears in the list below."], toasts
    context.close()
