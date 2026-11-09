"""The screens the module pictures show, walked by the person each one is for, in an organisation with linked data.

One journey per screen, each from what the person needs to read: the board's figures and where its pending
reviews are, which contracts renew and when, the vendors in the order they come up for renewal with nothing
unset printed as a word, a business case whose labels name their currency and whose sections say they are
empty only when they are, the portfolio's line about objectives, and the compliance findings control by control.
A second organisation holds records that must not change any figure.
"""
import datetime
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")
TODAY = datetime.date.today()


def _page(browser, live_server, org, role, width=1440):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"][role])
    return page


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, page.viewport_size["width"])), full_page=True)


def _words(locator):
    return " ".join(locator.inner_text().split())


# ── the board ──────────────────────────────────────────────────────────────────────────────────────────────────

def _reviews(org_id, submitter_id, noun, statuses):
    from app import create_app, db
    from app.models.architecture_review_board import ARBReviewItem

    with create_app("testing").app_context():
        titles = []
        for number, status in enumerate(statuses):
            title = "%s %s review %d" % (noun, status.replace("_", " "), number)
            db.session.add(ARBReviewItem(review_number="%s-%d" % (noun.upper(), number), title=title,
                                         review_type="architecture_change", status=status, priority="medium",
                                         organization_id=org_id, submitter_id=submitter_id))
            titles.append(title)
        db.session.commit()
        db.session.remove()
    return titles


def _review_in_a_cycle(org_id, submitter_id, noun):
    """A pending review that sits in a review cycle, as the board's queue lists it. The cycle's database checks
    expect rows only the submission service writes, so triggers are bypassed for this transaction, as the other
    governance journeys do for synthetic rows."""
    from datetime import datetime, timezone

    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
    from app.models.transformation_decision import ARBSubjectEvidenceSnapshot

    title = "%s in the queue" % noun
    with create_app("testing").app_context():
        db.session.execute(db.text("SET LOCAL session_replication_role = replica"))
        adr = ArchitectureDecision(organization_id=org_id, decision_id="AD-%s" % noun, title=title,
                                   status="proposed", context="c", decision="d", rationale="r", consequences="q",
                                   created_by_id=submitter_id)
        db.session.add(adr)
        db.session.flush()
        snapshot = ARBSubjectEvidenceSnapshot(
            organization_id=org_id, subject_type="adr", subject_id=adr.id, adr_id=adr.id, schema_version=1,
            policy_version="adr-arb-r2", captured_by_id=submitter_id, captured_at=datetime.now(timezone.utc),
            payload={"title": title}, citations={"linked_resources": []}, content_hash="0" * 64)
        db.session.add(snapshot)
        db.session.flush()
        cycle = ARBReviewCycle(organization_id=org_id, subject_type="adr", subject_id=adr.id, adr_id=adr.id,
                               subject_evidence_snapshot_id=snapshot.id, review_number="CYC-%s" % noun,
                               cycle_number=1, status="submitted")
        db.session.add(cycle)
        db.session.flush()
        db.session.add(ARBReviewItem(
            organization_id=org_id, review_number="QUE-%s" % noun, title=title, review_type="architecture_change",
            subject_type="adr", subject_id=adr.id, adr_id=adr.id, subject_evidence_snapshot_id=snapshot.id,
            review_cycle_id=cycle.id, status="submitted", submitter_id=submitter_id))
        db.session.commit()
        db.session.remove()
    return title


def test_the_board_reads_its_approval_rate_from_decided_reviews_and_says_where_the_pending_ones_are(
        live_server, browser):
    noun = "Board%s" % uuid.uuid4().hex[:6]
    org, other = create_fresh_org("enterprise_architect"), create_fresh_org("enterprise_architect")
    titles = _reviews(org["org_id"], org["user_ids"]["enterprise_architect"], noun, ["approved", "approved", "approved", "rejected", "submitted", "submitted",
                                            "under_review"])
    _reviews(other["org_id"], other["user_ids"]["enterprise_architect"], "Other" + noun, ["rejected"] * 5)
    queued = _review_in_a_cycle(org["org_id"], org["user_ids"]["enterprise_architect"], noun)
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    figures = _words(page.get_by_test_id("figure-cards"))
    assert "Total reviews 8" in figures and "Pending 4" in figures, figures
    # three approved of the four decided: 75.0%, not three of all eight, and the other board's five rejections stay out
    assert "Approval rate 75.0%" in figures, figures
    line = _words(page.get_by_test_id("arb-outside-queue"))
    assert line.startswith("3 of the 4 pending reviews are not yet in a review cycle, so they are not listed below."), line
    _shot(page, "board")

    # the link opens the ledger filtered to exactly those three: the review in a cycle, the decided ones and the
    # other board's reviews are not in it
    page.get_by_test_id("arb-outside-queue-link").click()
    page.wait_for_url(re.compile(r"/arb/reviews\?cycle=none"), timeout=PAGE_TIMEOUT)
    ledger = page.locator("main").inner_text()
    pending_outside = [t for t in titles if "submitted" in t or "under review" in t]
    assert len(pending_outside) == 3
    for title in pending_outside:
        assert title in ledger, title
    for title in titles:
        if title not in pending_outside:
            assert title not in ledger, title
    assert queued not in ledger
    assert "Other" + noun not in ledger
    expect(page.get_by_test_id("arb-outside-queue-filter")).to_contain_text("not yet in a review cycle")

    # the way back shows every review, the one in a cycle included
    page.get_by_test_id("arb-outside-queue-clear").click()
    page.wait_for_url(re.compile(r"/arb/reviews$"), timeout=PAGE_TIMEOUT)
    assert queued in page.locator("main").inner_text()
    assert page.get_by_test_id("arb-outside-queue-filter").count() == 0


def test_choosing_a_status_on_the_filtered_ledger_keeps_the_outside_a_cycle_filter(live_server, browser):
    noun = "Keep%s" % uuid.uuid4().hex[:6]
    org, other = create_fresh_org("enterprise_architect"), create_fresh_org("enterprise_architect")
    titles = _reviews(org["org_id"], org["user_ids"]["enterprise_architect"], noun, ["submitted", "submitted", "under_review"])
    _reviews(other["org_id"], other["user_ids"]["enterprise_architect"], "Other" + noun, ["submitted"])
    queued = _review_in_a_cycle(org["org_id"], org["user_ids"]["enterprise_architect"], noun)

    for width in (1440, 390):
        page = _page(browser, live_server, org, "enterprise_architect", width)
        if width == 390:
            page.evaluate("localStorage.setItem('theme', 'dark')")
        page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("arb-outside-queue-link").click()
        page.wait_for_url(re.compile(r"/arb/reviews\?cycle=none"), timeout=PAGE_TIMEOUT)
        ledger = page.locator("main").inner_text()
        assert all(t in ledger for t in titles) and queued not in ledger

        page.get_by_label("Status").select_option("submitted")
        page.wait_for_url(re.compile(r"status=submitted"), timeout=PAGE_TIMEOUT)
        assert "cycle=none" in page.url, page.url
        ledger = page.locator("main").inner_text()
        assert titles[0] in ledger and titles[1] in ledger, ledger
        assert titles[2] not in ledger and queued not in ledger and "Other" + noun not in ledger
        expect(page.get_by_test_id("arb-outside-queue-filter")).to_contain_text("not yet in a review cycle")
        _shot(page, "ledger-status-kept")
        page.context.close()


def test_a_board_with_no_decisions_shows_a_dash_for_its_rate_and_no_outside_queue_line(live_server, browser):
    noun = "Quiet%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    assert "Approval rate —" in _words(page.get_by_test_id("figure-cards")), noun
    assert page.get_by_test_id("arb-outside-queue").count() == 0


# ── the renewals and the vendors ───────────────────────────────────────────────────────────────────────────────

# (name, renewal date offset, end date offset): a renewal on each edge of the 30 and 90 day bands, one just past
# the 90 day edge, a contract whose renewal date passed 20 days ago (still active, ending in 160 days), and a far one.
CONTRACTS = (("edge30", 30, 213), ("edge90", 90, 273), ("past90", 91, 274), ("renewed", -20, 160), ("far", 400, 583))


def _vendors(org_id, noun):
    """One vendor, system and contract for each of CONTRACTS, and a vendor with a system but no contract."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    made = {}
    with create_app("testing").app_context():
        for name, renews, ends in CONTRACTS + (("nodeal", None, None),):
            vendor = VendorOrganization(name="%s %s" % (noun, name))
            db.session.add(vendor)
            db.session.flush()
            system = ApplicationComponent(name="%s %s system" % (noun, name), organization_id=org_id,
                                          lifecycle_status="operational", vendor_organization_id=vendor.id)
            db.session.add(system)
            db.session.flush()
            contract = None
            if renews is not None:
                contract = VendorContract(
                    contract_name="%s %s contract" % (noun, name), organization_id=org_id, vendor_id=vendor.id,
                    application_id=system.id, annual_cost=1000.0, status="active",
                    start_date=TODAY - datetime.timedelta(days=90),
                    renewal_date=TODAY + datetime.timedelta(days=renews),
                    end_date=TODAY + datetime.timedelta(days=ends))
                db.session.add(contract)
            db.session.commit()
            made[name] = {"vendor": vendor.id, "contract": contract.id if contract else None}
        db.session.remove()
    return made


def _lapsed(org_id, noun, vendor_id):
    """An active contract whose end date passed five days ago, on the vendor of the far contract."""
    from app import create_app, db
    from app.models.application_portfolio import VendorContract

    with create_app("testing").app_context():
        db.session.add(VendorContract(
            contract_name="%s lapsed contract" % noun, organization_id=org_id, vendor_id=vendor_id, annual_cost=500.0,
            status="active", start_date=TODAY - datetime.timedelta(days=400),
            renewal_date=TODAY - datetime.timedelta(days=30), end_date=TODAY - datetime.timedelta(days=5)))
        db.session.commit()
        db.session.remove()


def test_the_renewals_tiles_their_lists_and_the_contracts_behind_them_agree(live_server, browser):
    noun = "Renew%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", extra_roles=("procurement",))
    other = create_fresh_org("enterprise_architect")
    made = _vendors(org["org_id"], noun)
    _vendors(other["org_id"], "Other" + noun)
    _lapsed(org["org_id"], noun, made["far"]["vendor"])
    page = _page(browser, live_server, org, "procurement")

    page.goto(live_server + "/procurement/renewals", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    def tile(testid):
        return _words(page.get_by_test_id(testid))

    # 30 days: the contract renewing on the 30th day and the one whose renewal date passed (overdue counts as due
    # at once); 90: those and the one on the 90th; 180: those and the one on day 91; the far one is in none
    assert re.search(r"\b2 Renew within 30 days", tile("renewals-critical")), tile("renewals-critical")
    assert re.search(r"\b3 Renew within 90 days", tile("renewals-warning")), tile("renewals-warning")
    assert re.search(r"\b4 Renew within 180 days", tile("renewals-upcoming")), tile("renewals-upcoming")
    _shot(page, "renewals")

    page.get_by_test_id("renewals-warning").click()
    page.wait_for_url(re.compile(r"days=90"), timeout=PAGE_TIMEOUT)
    rows = page.get_by_test_id("renewal-row")
    assert rows.count() == 3
    text = rows.all_inner_texts()
    assert "%s renewed contract" % noun in text[0], text[0]
    assert "%s edge30 contract" % noun in text[1] and "%s edge90 contract" % noun in text[2]
    assert "Other" + noun not in page.locator("main").inner_text()

    page.goto(live_server + "/procurement/renewals?days=180", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    listed = page.get_by_test_id("renewal-row")
    assert listed.count() == 4
    renewed_row = listed.filter(has_text="renewed contract")
    cells = renewed_row.locator("td")
    # the renewal date passed, so the row keeps that date and says how overdue it is
    assert _words(cells.nth(3)) == (TODAY - datetime.timedelta(days=20)).strftime("%d %b %Y"), _words(cells.nth(3))
    assert _words(cells.nth(4)) == "Renewal overdue by 20 days", _words(cells.nth(4))

    # a lapsed contract is in no tile and no list, and the page says so once
    assert "lapsed contract" not in page.locator("main table").inner_text()
    expect(page.get_by_test_id("renewals-lapsed-note")).to_contain_text("1 contract is still marked active")
    for days, testid in ((30, "renewals-critical"), (90, "renewals-warning"), (180, "renewals-upcoming")):
        page.goto(live_server + "/procurement/renewals?days=%d" % days, wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        figure = int(re.match(r"\s*(\d+)", _words(page.get_by_test_id(testid))).group(1))
        assert page.get_by_test_id("renewal-row").count() == figure, (days, figure)

    narrow = _page(browser, live_server, org, "procurement", width=390)
    narrow.evaluate("localStorage.setItem('theme', 'dark')")
    narrow.goto(live_server + "/procurement/renewals?days=180", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert narrow.get_by_test_id("renewal-row").count() == 4
    assert narrow.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    expect(narrow.get_by_test_id("renewals-lapsed-note")).to_be_visible()
    _shot(narrow, "renewals-narrow-dark")

    # the same contracts, as the renewals page words them: the days and the band's colour
    def badge(row):
        cell = row.locator("td").nth(4)
        return _words(cell), cell.locator("span[class*='rounded-full']").first.get_attribute("class")

    on_renewals = {name: badge(listed.filter(has_text="%s %s contract" % (noun, name)))
                   for name in ("edge30", "edge90", "past90", "renewed")}
    assert on_renewals["edge30"][0] == "In 30 days" and on_renewals["renewed"][0] == "Renewal overdue by 20 days", on_renewals

    # the Contracts register says the same days and the same band for each, not a count from the end date alone
    page.goto(live_server + "/procurement/contracts", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    register = page.get_by_test_id("contract-row")
    for name, (words, classes) in on_renewals.items():
        row = register.filter(has_text="%s %s contract" % (noun, name))
        assert badge(row) == (words, classes), (name, badge(row), words)
    assert "Other" + noun not in page.locator("main").inner_text()
    _shot(page, "contracts-register")

    # and the contract's own page: the days to its renewal and the renewal date the other two name
    for name, days, said in (("edge30", 30, "30 days until renewal"), ("renewed", -20, "Renewal overdue by 20 days")):
        register.filter(has_text="%s %s contract" % (noun, name)).get_by_test_id("contract-link").click()
        page.wait_for_url(re.compile(r"/procurement/contracts/%d" % made[name]["contract"]), timeout=PAGE_TIMEOUT)
        detail = _words(page.locator("main"))
        assert said in detail, (name, detail[:500])
        assert "Renewal Date " + (TODAY + datetime.timedelta(days=days)).strftime("%d %b %Y") in detail, (name, detail)
        page.go_back(wait_until="domcontentloaded")
    _shot(page, "contract-detail")

    page.goto(live_server + "/procurement/renewals?days=180", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    listed.filter(has_text="edge30 contract").get_by_test_id("renewal-contract-link").click()
    page.wait_for_url(re.compile(r"/procurement/contracts/%d" % made["edge30"]["contract"]), timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("%s edge30 contract" % noun)


def _missed_renewal(org_id, noun):
    """An active contract whose renewal date passed 20 days ago and that records no end date."""
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    with create_app("testing").app_context():
        vendor = VendorOrganization(name="%s missed vendor" % noun)
        db.session.add(vendor)
        db.session.flush()
        contract = VendorContract(
            contract_name="%s missed contract" % noun, organization_id=org_id, vendor_id=vendor.id, annual_cost=700.0,
            status="active", start_date=TODAY - datetime.timedelta(days=400),
            renewal_date=TODAY - datetime.timedelta(days=20), end_date=None)
        db.session.add(contract)
        db.session.commit()
        found = contract.id
        db.session.remove()
    return found


def test_a_missed_renewal_with_no_end_date_keeps_its_date_and_reads_as_overdue_on_all_three_screens(
        live_server, browser):
    noun = "Missed%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", extra_roles=("procurement",))
    other = create_fresh_org("enterprise_architect")
    contract_id = _missed_renewal(org["org_id"], noun)
    _missed_renewal(other["org_id"], "Other" + noun)
    stamp = (TODAY - datetime.timedelta(days=20)).strftime("%d %b %Y")
    name = "%s missed contract" % noun

    for width in (1440, 390):
        page = _page(browser, live_server, org, "procurement", width)

        page.goto(live_server + "/procurement/contracts", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_role("row").filter(has_text=name)
        assert "Renewal overdue by 20 days" in _words(row), _words(row)
        assert "No renewal date" not in _words(page.locator("main"))
        _shot(page, "missed-register")

        page.goto(live_server + "/procurement/renewals", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        listed = page.get_by_test_id("renewal-row").filter(has_text=name)
        assert listed.count() == 1 and "Renewal overdue by 20 days" in _words(listed), _words(page.locator("main"))
        assert stamp in _words(listed)
        assert re.search(r"\b1 Renew within 30 days", _words(page.get_by_test_id("renewals-critical")))
        expect(page.get_by_test_id("renewals-overdue-note")).to_contain_text("1 active contract has a renewal date")
        assert "Other" + noun not in _words(page.locator("main"))
        _shot(page, "missed-renewals")

        listed.get_by_test_id("renewal-contract-link").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith("/procurement/contracts/%d" % contract_id), page.url
        detail = _words(page.locator("main"))
        assert "Renewal Date %s" % stamp in detail and "Renewal overdue" in detail
        expect(page.get_by_test_id("renewal-passed")).to_contain_text("Add the next renewal date")
        _shot(page, "missed-contract")

        page.get_by_role("link", name="Edit contract").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("input[name=renewal_date]").input_value() == (TODAY - datetime.timedelta(days=20)).isoformat()
        page.context.close()


# (key, status, renewal date offset, end date offset): one contract in each state the renewal rule tells apart
STATES = (("ahead", "active", 12, 200), ("overdue", "active", -9, 200), ("ended", "active", -30, -4),
          ("terminated", "terminated", 12, 200), ("expired", "expired", -5, -2), ("draft", "draft", 20, 90))


def _states(org_id, noun):
    """A vendor, a system and a contract for each of STATES; returns {key: (vendor id, contract id)}."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    made = {}
    with create_app("testing").app_context():
        for key, status, renews, ends in STATES:
            vendor = VendorOrganization(name="%s %s" % (noun, key))
            db.session.add(vendor)
            db.session.flush()
            system = ApplicationComponent(name="%s %s system" % (noun, key), organization_id=org_id,
                                          lifecycle_status="operational", vendor_organization_id=vendor.id)
            db.session.add(system)
            db.session.flush()
            contract = VendorContract(
                contract_name="%s %s contract" % (noun, key), organization_id=org_id, vendor_id=vendor.id,
                application_id=system.id, annual_cost=1000.0, status=status,
                start_date=TODAY - datetime.timedelta(days=300),
                renewal_date=TODAY + datetime.timedelta(days=renews), end_date=TODAY + datetime.timedelta(days=ends))
            db.session.add(contract)
            db.session.commit()
            made[key] = (vendor.id, contract.id)
        db.session.remove()
    return made


def test_each_contract_state_reads_the_same_on_renewals_vendors_register_and_contract_page(live_server, browser):
    noun = "State%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", extra_roles=("procurement",), org_admin=True)
    other = create_fresh_org("enterprise_architect")
    made = _states(org["org_id"], noun)
    _states(other["org_id"], "Other" + noun)
    # what each state says on the Contracts register and on the contract's own page
    wording = {"ahead": ("In 12 days", "12 days until renewal"),
               "overdue": ("Renewal overdue by 9 days", "Renewal overdue by 9 days"),
               "ended": ("Ended 4 days ago", "Ended 4 days ago"),
               "terminated": ("Not active, no renewal", "No renewal: the contract is terminated"),
               "expired": ("Not active, no renewal", "No renewal: the contract is expired"),
               "draft": ("Not active, no renewal", "No renewal: the contract is draft")}

    for width in (1440, 390):
        page = _page(browser, live_server, org, "procurement", width)
        if width == 390:
            page.evaluate("localStorage.setItem('theme', 'dark')")

        # Renewals: the overdue and the coming contract, counted within 90 days and listed, overdue first; the
        # ended, terminated, expired and draft ones are in no tile and no list
        page.goto(live_server + "/procurement/renewals?days=90", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        tile = _words(page.get_by_test_id("renewals-warning"))
        assert re.search(r"\b2 Renew within 90 days", tile), tile
        rows = page.get_by_test_id("renewal-row")
        assert rows.count() == 2, _words(page.locator("main"))
        assert "%s overdue contract" % noun in _words(rows.nth(0)) and "Renewal overdue by 9 days" in _words(rows.nth(0))
        assert "%s ahead contract" % noun in _words(rows.nth(1)) and "In 12 days" in _words(rows.nth(1))
        main = page.locator("main").inner_text()
        for key in ("ended", "terminated", "expired", "draft"):
            assert "%s %s contract" % (noun, key) not in main, key
        assert "Other" + noun not in main
        _shot(page, "states-renewals")

        # Vendors: the same two, no more, and each row says the same words
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "2 contracts with 2 vendors renew within 90 days" in _words(page.locator("main")), _words(page.locator("main"))[:600]

        def vendor_row(key):
            return page.locator("tr", has=page.get_by_test_id("vendor-link").filter(has_text="%s %s" % (noun, key)))

        assert "Renewal overdue by 9 days" in _words(vendor_row("overdue"))
        assert "Due within 90 days" in _words(vendor_row("ahead"))
        for key in ("ended", "terminated", "expired", "draft"):
            row = _words(vendor_row(key))
            assert "No renewal ahead" in row and "overdue" not in row and "Due within" not in row, (key, row)
        assert "Other" + noun not in page.locator("main").inner_text()
        _shot(page, "states-vendors")

        # the Contracts register, then each contract's own page opened from its row
        page.goto(live_server + "/procurement/contracts", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        register = page.get_by_test_id("contract-row")
        for key, (in_register, _on_page) in wording.items():
            row = register.filter(has_text="%s %s contract" % (noun, key))
            assert in_register in _words(row), (key, _words(row))
        _shot(page, "states-register")
        for key, (_in_register, on_page) in wording.items():
            page.goto(live_server + "/procurement/contracts", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            register.filter(has_text="%s %s contract" % (noun, key)).get_by_test_id("contract-link").click()
            page.wait_for_url(re.compile(r"/procurement/contracts/%d$" % made[key][1]), timeout=PAGE_TIMEOUT)
            detail = _words(page.locator("main"))
            assert "%s %s contract" % (noun, key) in detail and on_page in detail, (key, detail[:600])
            if key in ("terminated", "expired", "draft"):
                assert "until renewal" not in detail and "overdue" not in detail, (key, detail[:600])
        _shot(page, "states-contract")

        # the vendor the overdue contract belongs to opens with its passed date said as overdue
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("vendor-link").filter(has_text="%s overdue" % noun).click()
        page.wait_for_url(re.compile(r"/applications/vendors/%d" % made["overdue"][0]), timeout=PAGE_TIMEOUT)
        assert "renewal overdue by 9 days" in _words(page.locator("#vendor-systems"))
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.context.close()


def test_the_vendor_list_orders_by_renewal_says_what_is_missing_and_opens_a_vendor_with_the_same_date(
        live_server, browser):
    noun = "Vend%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", org_admin=True)
    other = create_fresh_org("enterprise_architect")
    made = _vendors(org["org_id"], noun)
    _vendors(other["org_id"], "Other" + noun)
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    names = page.get_by_test_id("vendor-link").all_inner_texts()
    mine = [n.replace(noun + " ", "") for n in names if n.startswith(noun)]
    # due within 90 days soonest first, then the vendors with a renewal ahead by name, then the vendor with no contract
    assert mine == ["renewed", "edge30", "edge90", "far", "past90", "nodeal"], mine
    assert "3 contracts with 3 vendors renew within 90 days" in _words(page.locator("main")), _words(page.locator("main"))[:600]
    table = _words(page.locator("main table").first)
    assert not re.search(r"\bNone\b", table), table
    nodeal = _words(page.locator("tr", has=page.get_by_test_id("vendor-link").filter(has_text="nodeal")))
    assert "No contract" in nodeal, nodeal
    renewed_row = page.locator("tr", has=page.get_by_test_id("vendor-link").filter(has_text="renewed"))
    missed = TODAY - datetime.timedelta(days=20)
    sort_keys = renewed_row.locator("td[data-sort]").evaluate_all("(cells) => cells.map(c => c.dataset.sort)")
    assert missed.isoformat() in sort_keys, sort_keys
    assert "Renewal overdue by 20 days" in _words(renewed_row), _words(renewed_row)
    _shot(page, "vendors")

    page.get_by_test_id("vendor-link").filter(has_text="renewed").click()
    page.wait_for_url(re.compile(r"/applications/vendors/%d" % made["renewed"]["vendor"]), timeout=PAGE_TIMEOUT)
    systems = _words(page.locator("#vendor-systems"))
    assert "next renewal %s (renewal overdue by 20 days)" % missed.strftime("%d %b %Y") in systems, systems
    expect(page.locator("#vendor-systems")).to_contain_text("%s renewed system" % noun)


# ── the business case ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_business_case_names_the_currency_of_its_money_and_calls_only_its_empty_sections_empty(
        live_server, browser):
    from app import create_app, db
    from app.models.business_case import BusinessCase

    noun = "Case%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        db.session.add(BusinessCase(title="%s move the event backbone" % noun, organization_id=org["org_id"],
                                    problem_statement="Queues lose messages.", capex=120000, opex_annual=45000,
                                    tco_3yr=255000))
        db.session.commit()
        db.session.remove()
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/business-case/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="%s move the event backbone" % noun).first.click()
    page.wait_for_url(re.compile(r"/business-case/\d+"), timeout=PAGE_TIMEOUT)

    main = _words(page.locator("main"))
    for label in (r"Capex \(\S+\)", r"Opex, annual \(\S+\)", r"3-year total cost \(\S+\)",
                  r"Annual financial benefit \(\S+\)"):
        assert re.search(label, main, re.I), (label, main[:400])
    assert "Capex ()" not in main
    assert page.locator("#bc-capex").input_value().replace(",", "").startswith("120000")
    # the section with costs in it is not called empty; the section with no risks in it is
    assert "Costs is empty" not in main and "Reasons (strategic context) is empty" not in main
    assert "Major risks is empty" in main, main
    _shot(page, "business-case")


# ── the portfolio ──────────────────────────────────────────────────────────────────────────────────────────────

def _initiatives(org_id, noun, linked):
    from app import create_app, db
    from app.models.motivation import Goal, initiative_goals
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    with create_app("testing").app_context():
        goal = Goal(name="%s grow retail" % noun, organization_id=org_id)
        served = EnterpriseInitiative(name="%s digital checkout" % noun, organization_id=org_id)
        db.session.add_all([goal, served])
        db.session.flush()
        db.session.execute(initiative_goals.insert().values(initiative_id=served.id, goal_id=goal.id))
        if not linked:
            db.session.add(EnterpriseInitiative(name="%s back office tidy" % noun, organization_id=org_id))
        db.session.commit()
        db.session.remove()


def test_the_portfolio_says_which_initiatives_serve_no_objective_and_lists_them(live_server, browser):
    noun = "Folio%s" % uuid.uuid4().hex[:6]
    org, tidy = create_fresh_org("portfolio_manager"), create_fresh_org("portfolio_manager")
    _initiatives(org["org_id"], noun, linked=False)
    _initiatives(tidy["org_id"], "Tidy" + noun, linked=True)

    page = _page(browser, live_server, org, "portfolio_manager")
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    link = page.locator("#objective-filter").get_by_role("link", name="1 initiative serving no objective")
    expect(link).to_be_visible()
    assert "Every initiative serves an objective" not in page.locator("main").inner_text()
    _shot(page, "portfolio")
    link.click()
    page.wait_for_url(re.compile(r"filter=no-objective"), timeout=PAGE_TIMEOUT)
    expect(page.locator("#objective-filter")).to_contain_text("Showing initiatives serving no objective (1)")
    expect(page.locator("main table")).to_contain_text("%s back office tidy" % noun)
    assert "%s digital checkout" % noun not in page.locator("main table").inner_text()

    page = _page(browser, live_server, tidy, "portfolio_manager")
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("every-initiative-has-objective")).to_have_text("Every initiative serves an objective")
    assert page.locator("#objective-filter a").count() == 0


def test_a_portfolio_with_no_initiatives_does_not_claim_they_all_serve_an_objective(live_server, browser):
    empty = create_fresh_org("portfolio_manager")
    page = _page(browser, live_server, empty, "portfolio_manager")
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("No initiatives recorded yet")
    assert "Every initiative serves an objective" not in page.locator("main").inner_text()


# ── the compliance findings ────────────────────────────────────────────────────────────────────────────────────

def _drop_framework(code):
    """Remove the framework this journey made, with what hangs off it, by its own code, so the shared database
    holds no extra active framework for the tests that count them."""
    from app import create_app, db

    with create_app("testing").app_context():
        params = {"code": code}
        controls = "(SELECT id FROM compliance_controls WHERE framework_id IN (SELECT id FROM regulatory_frameworks WHERE code = :code))"
        framework = "(SELECT id FROM regulatory_frameworks WHERE code = :code)"
        for statement in (
                "DELETE FROM application_compliance_controls WHERE control_id IN " + controls,
                "DELETE FROM compliance_requirements WHERE framework_id IN " + framework,
                "DELETE FROM framework_adoptions WHERE framework_id IN " + framework,
                "DELETE FROM compliance_controls WHERE framework_id IN " + framework,
                "DELETE FROM regulatory_frameworks WHERE code = :code"):
            db.session.execute(db.text(statement), params)
        db.session.commit()
        db.session.remove()


def test_the_compliance_findings_run_control_by_control_with_several_applications_at_the_head(
        live_server, browser, request):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    suffix = uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", org_admin=True)
    with create_app("testing").app_context():
        framework = RegulatoryFramework(code="O-%s" % suffix, name="Order standard %s" % suffix, status="active")
        db.session.add(framework)
        db.session.flush()
        request.addfinalizer(lambda: _drop_framework("O-%s" % suffix))
        controls = [ComplianceControl(framework_id=framework.id, control_code="A.%d-%s" % (number, suffix),
                                      title="Control %d %s" % (number, suffix)) for number in (10, 2)]
        db.session.add_all(controls)
        apps = [ApplicationComponent(name="App %s %s" % (letter, suffix), organization_id=org["org_id"])
                for letter in "ABC"]
        db.session.add_all(apps)
        db.session.flush()
        ApplicabilityService.adopt_framework(org["org_id"], framework.id, adopted_by_id=org["user_ids"]["enterprise_architect"])
        for application in apps:
            for control in controls:
                ApplicabilityService.link_requirement(org["org_id"], application.id, control.id, "planned")
        db.session.commit()
        db.session.remove()
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    rows = [_words(r) for r in page.get_by_test_id("compliance-finding-row").all()]
    shown = [(re.search(r"A\.(\d+)-", r).group(1), re.search(r"App ([ABC]) ", r).group(1)) for r in rows]
    # control 2 before control 10 (by number, not by letters), each with every application that misses it
    assert shown == [("2", "A"), ("2", "B"), ("2", "C"), ("10", "A"), ("10", "B"), ("10", "C")], shown
    _shot(page, "compliance")
    page.get_by_test_id("compliance-finding-row").first.locator("a", has_text="A.2-%s" % suffix).first.click()
    page.wait_for_selector('[data-testid="control-panel"]', timeout=PAGE_TIMEOUT)
    panel = _words(page.get_by_test_id("control-panel"))
    assert "Control 2 %s" % suffix in panel and "App A %s" % suffix in panel, panel
