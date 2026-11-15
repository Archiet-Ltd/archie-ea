"""A person who can see costs records the organisation's money and sees how many months of cash it has.

Ada administers an organisation and Vic only views it. Ada opens Spend, then Money, and finds the empty
page asking for cash first. She records cash of 0 USD as of 14 Nov 2026 from the bank statement and monthly
outgoings of 2,500 USD as of 6 Aug 2026. The stat row says what each figure is, when it was true and where
it came from, monthly revenue is "not recorded" and links to the form, and months of cash is 0 from the
cash of 14 Nov 2026. The history lists both with their source and who stated them. She corrects the
outgoings to 2,700 USD: the new figure shows and the old row reads "replaced 14 Nov 2026". Read on
1 Mar 2027 the page still shows cash as of 14 Nov 2026 and adds "Cash not updated for 107 days". Vic sees
one plain sentence and no figures. Another organisation's cash is never on Ada's page.
"""
import os
import pathlib
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WITHHELD = "Cost figures are withheld for your role."


@pytest.fixture(scope="module")
def server_in_november(request, app):
    return boot_live_server(request, None, app, extra_env={"FINANCE_FACTS_TODAY": "2026-11-14"})


@pytest.fixture(scope="module")
def server_in_march(request, app):
    return boot_live_server(request, None, app, extra_env={"FINANCE_FACTS_TODAY": "2027-03-01"})


def _workspace():
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services import finance_fact_store
        from app.services.billing_plans import set_contract_plan
        from datetime import date

        Role.insert_roles()
        orgs = []
        for label in ("Money", "Neighbour"):
            org = Organization(name="%s %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
            db.session.add(org)
            db.session.flush()
            set_contract_plan(org, "enterprise", None)
            orgs.append(org)
        people = {}
        for key, first, role, persona, org in (
            ("ada", "Ada", "Administrator", "enterprise_architect", orgs[0]),
            ("vic", "Vic", "Viewer", "solution_architect", orgs[0]),
            ("ben", "Ben", "Administrator", "enterprise_architect", orgs[1]),
        ):
            user = User(email="%s-%s@example.com" % (key, suffix), first_name=first, last_name="Tester",
                        organization_id=org.id, enterprise_role=persona, confirmed=True)
            user.role = Role.query.filter_by(name=role).one()
            user.password = PASSWORD
            db.session.add(user)
            db.session.flush()
            people[key] = user.email
        from app.models.archimate_core import ArchiMateElement

        product = ArchiMateElement(organization_id=orgs[0].id, name="Starter plan %s" % suffix, type="Product", layer="business")
        db.session.add(product)
        db.session.flush()
        ben = User.query.filter_by(email=people["ben"]).one()
        finance_fact_store.record_fact(orgs[1].id, "cash_balance", "50000", date(2026, 11, 14), "neighbour bank statement",
                                       currency="USD", stated_by_user_id=ben.id, recorded_by_id=ben.id)
        db.session.commit()
        out = dict(people, org_id=orgs[0].id, product=product.name)
        db.session.remove()
        return out


@pytest.fixture
def shots(tmp_path):
    folder = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _shot(page, folder, name, dark=False):
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d%s.png" % (name, page.viewport_size["width"], "-dark" if dark else ""))),
                    full_page=True)


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _context(browser, width, dark=False):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme="dark" if dark else "light")
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    return context


def _record(page, measure, amount, as_of, source):
    page.select_option("#money-measure", value=measure)
    page.fill("#money-amount", amount)
    page.fill("#money-currency", "USD")
    page.fill("#money-as-of", as_of)
    page.fill("#money-source", source)
    page.get_by_test_id("money-save").click()
    expect(page.get_by_test_id("money-saved")).to_be_visible()


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_ada_records_the_money_and_reads_months_of_cash_and_vic_sees_a_sentence(
    browser, server_in_november, server_in_march, shots, width, dark
):
    ws = _workspace()
    context = _context(browser, width, dark)
    page = context.new_page()
    try:
        _sign_in(page, server_in_november, ws["ada"])

        # Spend, then Money, from the Spend group in the navigation
        page.goto(server_in_november + "/procurement/spend", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.get_by_role("link", name="Money", exact=True)
        if link.first.is_visible():
            link.first.click()
        else:
            page.goto(server_in_november + "/procurement/spend/money", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Money", exact=True).first).to_be_visible()

        # the empty page asks for cash first and lands on the form
        empty = page.get_by_test_id("money-empty")
        expect(empty).to_contain_text("No money figures recorded yet")
        expect(empty).to_contain_text("Start with the cash in your bank")
        expect(page.get_by_test_id("money-stat-cards")).to_have_count(0)
        _shot(page, shots, "money-empty", dark)
        empty.get_by_role("link", name="Record your cash").click()
        assert page.locator("#money-measure").input_value() == "cash_balance"
        expect(page.get_by_test_id("money-form-card")).to_be_in_viewport()

        # cash 0 USD as of 14 Nov 2026, then monthly outgoings
        assert page.locator("#money-currency").input_value() == "GBP", "the form offers the organisation's own currency"
        page.fill("#money-amount", "0")
        page.fill("#money-currency", "USD")
        page.fill("#money-as-of", "2026-11-14")
        page.fill("#money-source", "bank statement")
        page.get_by_test_id("money-save").click()
        expect(page.get_by_test_id("money-saved")).to_be_visible()
        _record(page, "spend", "2500", "2026-08-06", "founder's figure")

        cash, spend, revenue, months = (page.get_by_test_id(t) for t in ("money-cash", "money-spend", "money-revenue", "money-months"))
        expect(cash).to_contain_text("Cash in bank")
        expect(page.get_by_test_id("money-cash-value")).to_have_text("0 USD")
        expect(cash).to_contain_text("As of 14 Nov 2026, from bank statement")
        expect(page.get_by_test_id("money-spend-value")).to_have_text("2,500 USD")
        expect(spend).to_contain_text("As of 6 Aug 2026, from founder's figure")
        expect(revenue).to_contain_text("Monthly revenue: not recorded")
        expect(page.get_by_test_id("money-months-value")).to_have_text("0")
        expect(months).to_contain_text("From cash on 14 Nov 2026")
        expect(months).to_contain_text("outgoings as of 6 Aug 2026")
        expect(page.get_by_test_id("money-cash-stale")).to_have_count(0)
        body = page.locator("body").inner_text()
        assert "50,000" not in body and "neighbour bank statement" not in body
        _no_sideways_scroll(page)
        _shot(page, shots, "money-recorded", dark)

        # the revenue card leads to the form with revenue chosen
        revenue.click()
        assert page.locator("#money-measure").input_value() == "revenue"
        expect(page.get_by_test_id("money-form-card")).to_be_in_viewport()

        # the history: source and who stated each row
        rows = page.get_by_test_id("money-history-row")
        expect(rows).to_have_count(2)
        history = page.get_by_test_id("money-history")
        for text in ("Cash in bank", "bank statement", "Monthly outgoings", "founder's figure", "14 Nov 2026", "6 Aug 2026"):
            expect(history).to_contain_text(text)
        stated_by = page.get_by_test_id("money-history-row").filter(has_text="Cash in bank").locator("td").nth(4)
        assert stated_by.inner_text().strip() == "Ada Tester" or width < 768, stated_by.inner_text()
        expect(history).to_contain_text("Ada Tester")
        text_now = page.locator("body").inner_text()
        assert "bound method" not in text_now and "<User" not in text_now, "the page prints a program object"

        # correct the outgoings to 2,700 USD as of 14 Nov 2026
        page.get_by_role("button", name="Correct Monthly outgoings as of 6 Aug 2026").or_(
            page.get_by_role("link", name="Correct Monthly outgoings as of 6 Aug 2026")).first.click()
        expect(page.get_by_test_id("money-form-card")).to_contain_text("Correct a figure")
        assert page.locator("#money-measure").input_value() == "spend"
        assert page.locator("#money-currency").input_value() == "USD"
        page.fill("#money-amount", "2700")
        page.fill("#money-as-of", "2026-11-14")
        page.fill("#money-source", "revised budget")
        page.get_by_test_id("money-save").click()
        expect(page.get_by_test_id("money-saved")).to_be_visible()
        expect(page.get_by_test_id("money-spend-value")).to_have_text("2,700 USD")
        expect(page.get_by_test_id("money-spend")).to_contain_text("As of 14 Nov 2026, from revised budget")
        expect(page.get_by_test_id("money-history-row")).to_have_count(3)
        old = page.get_by_test_id("money-history-row").filter(has_text="2,500 USD")
        expect(old).to_contain_text("replaced 14 Nov 2026")
        newer = page.get_by_test_id("money-history-row").filter(has_text="2,700 USD")
        expect(newer).to_contain_text("Current")
        _shot(page, shots, "money-corrected", dark)

        # still there after a reload
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("money-spend-value")).to_have_text("2,700 USD")
        expect(page.get_by_test_id("money-cash-value")).to_have_text("0 USD")

        # funding and paying customers read back in Latest figures
        page.goto(server_in_november + "/procurement/spend/money?measure=funding_in#record-fact", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _record(page, "funding_in", "250000", "2026-11-10", "seed round")
        page.select_option("#money-measure", value="paying_customers")
        page.fill("#money-amount", "12")
        page.fill("#money-as-of", "2026-11-12")
        page.fill("#money-source", "billing export")
        page.get_by_test_id("money-save").click()
        expect(page.get_by_test_id("money-saved")).to_be_visible()
        latest = page.get_by_test_id("money-latest")
        expect(latest).to_contain_text("250,000 USD")
        expect(latest).to_contain_text("seed round")
        expect(latest).to_contain_text("12")
        expect(latest).to_contain_text("billing export")
        assert page.get_by_test_id("money-latest-facts").inner_text().count("Not recorded") == 1, "only monthly revenue is still unrecorded"

        # revenue for a product: named in the history, the revenue card says it is organisation-level,
        # and a correction stays on the product
        product = ws["product"]
        page.goto(server_in_november + "/procurement/spend/money?measure=revenue#record-fact", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("#money-subject", label=product)
        _record(page, "revenue", "3000", "2026-11-13", "plan invoices")
        expect(page.get_by_test_id("money-revenue")).to_contain_text("Revenue recorded for a product is listed in the history")
        row = page.get_by_test_id("money-history-row").filter(has_text="plan invoices")
        expect(row).to_contain_text(product)
        row.get_by_test_id("money-correct").click()
        expect(page.get_by_test_id("money-form-card")).to_contain_text("Correct a figure")
        assert page.locator("#money-subject option:checked").inner_text() == product, "the correction opens on the product"
        page.fill("#money-amount", "3500")
        page.fill("#money-source", "revised invoices")
        page.get_by_test_id("money-save").click()
        expect(page.get_by_test_id("money-saved")).to_be_visible()
        new_row = page.get_by_test_id("money-history-row").filter(has_text="revised invoices")
        expect(new_row).to_contain_text(product)
        expect(new_row).to_contain_text("Current")
        expect(page.get_by_test_id("money-history-row").filter(has_text="plan invoices")).to_contain_text("replaced")

        # read on 1 Mar 2027: the same figures, and the cash is flagged as old
        late = _context(browser, width, dark)
        late_page = late.new_page()
        try:
            _sign_in(late_page, server_in_march, ws["ada"])
            late_page.goto(server_in_march + "/procurement/spend/money", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(late_page.get_by_test_id("money-cash-stale")).to_contain_text("Cash not updated for 107 days")
            expect(late_page.get_by_test_id("money-cash-value")).to_have_text("0 USD")
            expect(late_page.get_by_test_id("money-cash")).to_contain_text("As of 14 Nov 2026")
            expect(late_page.get_by_test_id("money-months-value")).to_have_text("0")
            expect(late_page.get_by_test_id("money-months")).to_contain_text("From cash on 14 Nov 2026")
            expect(late_page.get_by_test_id("money-spend-value")).to_have_text("2,700 USD")
            _shot(late_page, shots, "money-stale", dark)
        finally:
            late.close()

        # Vic cannot see costs: one sentence, no figures
        vic_context = _context(browser, width, dark)
        vic = vic_context.new_page()
        try:
            _sign_in(vic, server_in_november, ws["vic"])
            vic.goto(server_in_november + "/procurement/spend/money", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(vic.get_by_test_id("money-withheld-text")).to_have_text(WITHHELD)
            text = vic.locator("main, body").first.inner_text()
            for figure in ("USD", "2,700", "2,500", "bank statement", "Cash in bank"):
                assert figure not in text, figure
            expect(vic.get_by_test_id("money-form")).to_have_count(0)
            _shot(vic, shots, "money-no-access", dark)
        finally:
            vic_context.close()
    finally:
        context.close()


def test_the_form_can_be_filled_and_sent_with_the_keyboard_alone(browser, server_in_november, shots):
    ws = _workspace()
    context = _context(browser, 1440)
    page = context.new_page()
    try:
        _sign_in(page, server_in_november, ws["ada"])
        page.goto(server_in_november + "/procurement/spend/money?measure=revenue", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#money-measure").focus()
        assert page.evaluate("document.activeElement.id") == "money-measure"
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "money-amount"
        page.keyboard.type("1800")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "money-currency"
        page.keyboard.press("Control+a")
        page.keyboard.type("USD")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "money-as-of"
        # the date is three fields in one control (month, day, year): the keyboard passes through each
        for _ in range(4):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.id") == "money-source":
                break
        assert page.evaluate("document.activeElement.id") == "money-source"
        page.keyboard.type("invoices paid")
        _shot(page, shots, "money-keyboard-filled")
        page.keyboard.press("Enter")
        expect(page.get_by_test_id("money-saved")).to_be_visible()
        expect(page.get_by_test_id("money-revenue-value")).to_have_text("1,800 USD")
        expect(page.get_by_test_id("money-revenue")).to_contain_text("from invoices paid")
    finally:
        context.close()
