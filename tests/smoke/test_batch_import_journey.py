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
from .fresh_org import create_fresh_org

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



def _upload(page, live_server, csv, name):
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill(name)
    page.locator("#fileUpload").set_input_files(str(csv))
    page.wait_for_timeout(1500)
    button = page.get_by_role("button", name="Create Import Job")
    expect(button.first).to_be_enabled(timeout=PAGE_TIMEOUT)
    button.first.click()


def test_the_page_a_person_lands_on_names_the_file_and_says_the_status_in_words(browser, live_server, tmp_path):
    """Create Import Job takes the person to the job within a moment; that page, not a toast, says what is happening."""
    csv = tmp_path / "lantern-apps.csv"
    csv.write_text("name,description\nLanternApp1,First\nLanternApp2,Second\n", encoding="utf-8")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, create_fresh_org("data_architect")["emails"]["data_architect"])
    _upload(page, live_server, csv, "From file %s" % uuid.uuid4().hex[:6])
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    expect(page.locator("main")).to_contain_text("From lantern-apps.csv")
    status = page.get_by_test_id("job-status")
    expect(status).to_be_visible()
    words = status.inner_text()
    assert words.strip() and "_" not in words and words == words.strip(), words
    assert "awaiting_confirmation" not in page.locator("main").inner_text()
    context.close()


def test_importing_a_file_that_is_already_being_imported_says_so_and_links_to_that_job(browser, live_server, tmp_path):
    csv = tmp_path / "twice-apps.csv"
    csv.write_text("name,description\nTwiceApp1,First\nTwiceApp2,Second\n", encoding="utf-8")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, create_fresh_org("data_architect")["emails"]["data_architect"])
    _upload(page, live_server, csv, "Twice one %s" % uuid.uuid4().hex[:6])
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    first = page.url
    _upload(page, live_server, csv, "Twice two %s" % uuid.uuid4().hex[:6])
    notice = page.get_by_test_id("existing-job")
    expect(notice).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(notice).to_contain_text("An active import job already exists for this file")
    assert "Invalid file or parameters" not in page.locator("body").inner_text()
    notice.get_by_role("link", name="Open the existing import").click()
    page.wait_for_url(first, timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("From twice-apps.csv")
    context.close()
