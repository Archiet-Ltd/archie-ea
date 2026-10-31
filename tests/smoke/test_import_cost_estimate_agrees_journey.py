"""An administrator reads one import cost estimate before starting an import and
the same figure, in the same currency format, on the job page afterwards.

Goal: "I know what this import will cost before I start it, and the job tells me
the same". The organisation reports in euros, so a bare dollar sign on either
page, or two different figures, fails the journey. Starting the import creates
the job in its awaiting-confirmation state; the language model is not called.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MONEY = re.compile(r"^€\d[\d,]*\.\d{2}$")


USD_TO_EUR = "0.80000000"


def _set_currency(org_id, code, rate=USD_TO_EUR):
    """Set the reporting currency and put a dated dollar rate on file (None: no rate)."""
    from datetime import date

    from app import create_app, db
    from app.models.cost_fact import ExchangeRate
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        db.session.get(Organization, org_id).reporting_currency = code
        if rate is not None and code != "USD":
            row = db.session.execute(db.select(ExchangeRate).where(
                ExchangeRate.from_currency == "USD", ExchangeRate.to_currency == code,
                ExchangeRate.effective_date == date(2026, 1, 1))).scalar_one_or_none()
            if row is None:
                db.session.add(ExchangeRate(from_currency="USD", to_currency=code, rate=rate,
                                            effective_date=date(2026, 1, 1), source="test"))
            else:
                row.rate = rate
        db.session.commit()
        db.session.remove()


def _shot(page, name):
    out = os.environ.get("IMPORTCOST_SHOTS")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path=os.path.join(out, name), full_page=False)


@pytest.mark.parametrize(
    "viewport,scheme,tag",
    [({"width": 1440, "height": 900}, "light", "1440-light"),
     ({"width": 390, "height": 844}, "dark", "390-dark")],
)
def test_the_estimate_before_the_import_equals_the_estimate_on_the_job(
    browser, live_server, tmp_path, viewport, scheme, tag
):
    org = create_fresh_org("data_architect", org_admin=True)
    _set_currency(org["org_id"], "EUR")
    csv = tmp_path / "two-apps.csv"
    csv.write_text("name,description\nCostApp1,First\nCostApp2,Second\n", encoding="utf-8")

    context = browser.new_context(ignore_https_errors=True, viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["data_architect"])

    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill("Cost agreement %s" % uuid.uuid4().hex[:6])
    page.locator("#fileUpload").set_input_files(str(csv))

    # Located by what a person reads (the Estimated Cost row), not by a hook the fix adds.
    before = page.get_by_text("Estimated Cost", exact=True).locator("xpath=following-sibling::span").first
    expect(before).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)  # the figure may be fetched from the server
    figure_before = before.inner_text().strip()
    before.scroll_into_view_if_needed()
    _shot(page, "a-new-import-cost-estimate-%s.png" % tag)

    button = page.get_by_role("button", name="Create Import Job")
    expect(button.first).to_be_enabled(timeout=PAGE_TIMEOUT)
    button.first.click()
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("domcontentloaded")

    after = page.get_by_text("Estimated Cost", exact=True).locator("xpath=ancestor::div[1]/following-sibling::span").first
    expect(after).to_be_visible(timeout=PAGE_TIMEOUT)
    after.scroll_into_view_if_needed()
    _shot(page, "a-job-cost-summary-%s.png" % tag)
    figure_after = after.inner_text().strip()
    print("FIGURES new-import=%s job=%s" % (figure_before, figure_after))

    assert figure_before == figure_after, (
        "the new-import page estimated %s but the job page says %s" % (figure_before, figure_after))
    assert MONEY.match(figure_after), "job page figure %r is not in the organisation's format" % figure_after
    assert figure_before == figure_after, (
        "the new-import page estimated %s but the job page says %s" % (figure_before, figure_after))
    context.close()


# ---------------------------------------------------------------------------
# Every money figure on the import pages is in the organisation's currency, and
# the Batches table fits a phone.
# ---------------------------------------------------------------------------

EURO_FIGURE = re.compile(r"[+-]?€-?\d[\d,]*\.\d{2,4}")
DOLLAR_FIGURE = re.compile(r"[+-]?\$-?\d[\d,]*\.\d{2,4}")


def _main_text(page):
    return page.locator("#main-content").first.inner_text()


def _import_two_apps(page, live_server, tmp_path, with_budget):
    csv = tmp_path / "two-apps.csv"
    csv.write_text("name,description\nCostApp1,First\nCostApp2,Second\n", encoding="utf-8")
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill("Currency %s" % uuid.uuid4().hex[:6])
    page.locator("#fileUpload").set_input_files(str(csv))
    est = page.get_by_text("Estimated Cost", exact=True).locator("xpath=following-sibling::span").first
    expect(est).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    if with_budget:
        page.locator("button", has_text="Set limit").first.click()
        page.locator('input[type="number"][min="1"]').first.fill("10")
    new_page_text = _main_text(page)
    button = page.get_by_role("button", name="Create Import Job")
    expect(button.first).to_be_enabled(timeout=PAGE_TIMEOUT)
    button.first.click()
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2500)  # the job and its batches are painted by script after a poll
    return new_page_text


@pytest.mark.parametrize("code,symbol,other", [("EUR", "€", "$"), ("USD", "$", "€")])
def test_every_import_money_figure_is_in_the_organisation_currency(
    browser, live_server, tmp_path, code, symbol, other
):
    org = create_fresh_org("data_architect", org_admin=True)
    _set_currency(org["org_id"], code)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["data_architect"])

    new_text = _import_two_apps(page, live_server, tmp_path, with_budget=True)
    job_text = _main_text(page)
    page.goto(live_server + "/batch-import/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2500)
    list_text = _main_text(page)

    for name, text in (("new-import", new_text), ("job", job_text), ("jobs list", list_text)):
        assert other not in text, "%s page shows %r for a %s organisation: %s" % (
            name, other, code, [l for l in text.splitlines() if other in l])

    assert symbol in new_text, "new-import page shows no %s (estimate and budget)" % symbol
    # Cost Summary: estimated, actual and variance, plus the Total Cost card, all in the symbol.
    for label in ("Estimated Cost", "Actual Cost", "Variance", "Total Cost"):
        assert label in job_text, "job page has no %r" % label
    figures = (EURO_FIGURE if symbol == "€" else DOLLAR_FIGURE).findall(job_text)
    assert len(figures) >= 5, "job page shows %d %s figures, expected estimate, actual, variance, total, budget and batch cost: %s" % (
        len(figures), symbol, figures)
    assert "Budget:" in job_text and re.search(r"Budget:\s*%s" % re.escape(symbol), job_text), (
        "budget line is not in %s" % symbol)
    assert symbol in list_text
    context.close()


def test_batches_table_headers_are_fully_visible_on_a_phone(browser, live_server, tmp_path):
    org = create_fresh_org("data_architect", org_admin=True)
    _set_currency(org["org_id"], "EUR")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844}, color_scheme="dark")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["data_architect"])
    _import_two_apps(page, live_server, tmp_path, with_budget=False)

    assert page.locator("table:has(th:text-is('Applications'))").count() == 1
    page.locator("table:has(th:text-is('Applications'))").scroll_into_view_if_needed()
    _shot(page, "b-job-batches-390-dark.png")
    problems = page.evaluate(
        """() => {
        const table = [...document.querySelectorAll('table')].find(t => [...t.querySelectorAll('th')].some(h => h.innerText.trim().toUpperCase() === 'APPLICATIONS'));
        const heads = [...table.querySelectorAll('thead th')];
        const scroller = table.parentElement;
        const card = scroller.closest('[class*="rounded"]') || scroller;
        const out = [];
        if (heads.length < 7) out.push('only ' + heads.length + ' headers');
        const pinned = heads[0].getBoundingClientRect().width;
        const covered = (th, x, y) => { const el = document.elementFromPoint(x, y); return !(el && el.closest('th') === th); };
        const check = (th, label) => {
            const text = th.innerText.trim();
            const b = th.getBoundingClientRect(), s = scroller.getBoundingClientRect();
            const cardBox = card.getBoundingClientRect();
            const y = b.top + b.height / 2;
            const left = Math.max(b.left, s.left) + 4, right = Math.min(b.right, s.right) - 4;
            if (th.scrollWidth > th.clientWidth + 1) out.push(label + ' ' + text + ': text clipped');
            if (b.left < cardBox.left - 1 || b.right > cardBox.right + 1) out.push(label + ' ' + text + ': outside the card');
            if (covered(th, left, y) || covered(th, right, y)) out.push(label + ' ' + text + ': covered by another column');
        };
        // at rest: nothing visible may be hidden behind a pinned column
        heads.forEach(th => { const b = th.getBoundingClientRect(), s = scroller.getBoundingClientRect(); if (b.right > s.left && b.left < s.right) { const y = b.top + b.height/2; const l = Math.max(b.left, s.left) + 4, r = Math.min(b.right, s.right) - 4; if (covered(th, l, y) || covered(th, r, y)) out.push('at rest ' + th.innerText.trim() + ': covered by another column'); } });
        // scrolled to each column: it must be whole, inside the card, unclipped
        heads.forEach((th, i) => { scroller.scrollLeft = i === 0 ? 0 : th.offsetLeft - pinned; check(th, 'scrolled to'); });
        scroller.scrollLeft = 0;
        return out;
    }""")
    assert not problems, "Batches headers at 390: %s" % problems
    context.close()


def _fill_new_import(page, live_server, tmp_path):
    csv = tmp_path / "two-apps.csv"
    csv.write_text("name,description\nCostApp1,First\nCostApp2,Second\n", encoding="utf-8")
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill("Rate %s" % uuid.uuid4().hex[:6])
    page.locator("#fileUpload").set_input_files(str(csv))
    est = page.get_by_text("Estimated Cost", exact=True).locator("xpath=following-sibling::span").first
    expect(est).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    return est


def _job_id_from_url(page):
    return re.search(r"/batch-import/jobs/(\d+)", page.url).group(1)


def test_a_euro_figure_is_the_dollar_figure_converted_and_the_budget_is_stored_in_dollars(
    browser, live_server, tmp_path
):
    org = create_fresh_org("data_architect", org_admin=True)
    _set_currency(org["org_id"], "EUR")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["data_architect"])
    est = _fill_new_import(page, live_server, tmp_path)
    shown = float(re.sub(r"[^\d.]", "", est.inner_text()))
    usd = page.evaluate(
        """async () => (await (await fetch('/api/batch-import/cost-estimate?rows=2&ai_generation=true')).json()).estimated_total_usd""")
    assert abs(shown - usd * 0.8) < 0.006, "page shows EUR %s for a USD %s estimate at 0.80" % (shown, usd)

    page.locator("button", has_text="Set limit").first.click()
    page.locator('input[type="number"][min="1"]').first.fill("10")
    page.get_by_role("button", name="Create Import Job").first.click()
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    job = page.evaluate(
        """async (id) => (await (await fetch('/api/batch-import/jobs/' + id)).json())""", _job_id_from_url(page))
    job = job.get("data", job)
    stored = float(job.get("budget_limit_usd"))
    assert abs(stored - 12.5) < 0.01, "a EUR 10 limit at 0.80 must be stored as USD 12.50, got %s" % stored
    page.wait_for_timeout(2500)
    assert re.search(r"Budget:\s*€10\.00", _main_text(page)), "job page does not show the typed limit back as EUR 10.00"
    context.close()


def test_ai_off_on_the_page_creates_a_job_with_ai_off_and_no_ai_cost(browser, live_server, tmp_path):
    org = create_fresh_org("data_architect", org_admin=True)
    _set_currency(org["org_id"], "EUR")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["data_architect"])
    _fill_new_import(page, live_server, tmp_path)
    page.get_by_text("Use AI to generate", exact=False).first.scroll_into_view_if_needed()
    toggle = page.locator("button[class*='w-11']").first
    toggle.click()
    expect(page.get_by_text("AI generation is disabled")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Create Import Job").first.click()
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2500)
    job = page.evaluate(
        """async (id) => (await (await fetch('/api/batch-import/jobs/' + id)).json())""", _job_id_from_url(page))
    job = job.get("data", job)
    assert job.get("enable_ai_generation") is False, "job was created with AI on: %s" % job.get("enable_ai_generation")
    assert float(job.get("estimated_cost_usd") or 0) == 0.0, "job carries an AI cost estimate %s" % job.get("estimated_cost_usd")
    assert not re.search(r"€\s*0\.[1-9]|€[1-9]", _main_text(page).split("Actual Cost")[0]), "job page shows an AI cost"
    context.close()


def test_with_no_rate_on_file_the_page_says_why_and_never_relabels_dollars(browser, live_server, tmp_path):
    org = create_fresh_org("data_architect", org_admin=True)
    _set_currency(org["org_id"], "JPY", rate=None)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["data_architect"])
    est = _fill_new_import(page, live_server, tmp_path)
    assert est.inner_text().strip() == "—", "estimate %r shown with no rate on file" % est.inner_text()
    expect(page.get_by_text("No exchange rate from US dollars to JPY is on file")).to_be_visible(timeout=PAGE_TIMEOUT)
    context.close()
