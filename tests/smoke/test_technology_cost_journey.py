"""An architect records what a technology costs and everyone reads the same shares.

The organisation holds three applications and two technologies. The Cluster serves all three, the
Database serves two; costs are recorded in two currencies and the organisation reports in a third.
The architect adds the Cluster's costs on its page, then reads the same total and each share on the
technology page, on every application's page and on the canvas cost structure. A viewer is shown no
way to add a cost and is refused when they try, and a second organisation sees nothing of it.
"""

import os
import re
import uuid
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org
from .fresh_org import add_administrator
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
expect.set_options(timeout=PAGE_TIMEOUT)

WALK_DIR = os.environ.get("TECHCOST_WALK_DIR")
REPORTING, FOREIGN, RATE_DAY = "CAD", "AUD", date(2001, 1, 1)


@pytest.fixture(scope="module")
def estate(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_model import BusinessModelCanvas
    from app.models.cost_fact import ExchangeRate
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services import cost_fact_store
    from app.services.billing_plans import set_contract_plan

    _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    out = {"tag": tag, "emails": {}}
    with app.app_context():
        Role.insert_roles()
        roles = {n: Role.query.filter_by(name=n).one() for n in ("Architect", "Viewer")}
        orgs = []
        for label in ("first", "second"):
            org = Organization(name="Tech cost %s %s" % (label, tag), slug="techcost-%s-%s" % (label, tag),
                               reporting_currency=REPORTING)
            db.session.add(org)
            db.session.flush()
            set_contract_plan(org, "enterprise", None)
            db.session.commit()
            add_administrator(org.id, "%s%s" % (label, tag))
            orgs.append(org)
        first, second = orgs
        for key, org, role, persona in (
                ("architect", first, "Architect", "enterprise_architect"),
                ("viewer", first, "Viewer", "solution_architect"),
                ("other", second, "Architect", "enterprise_architect")):
            email = "techcost.%s.%s@example.com" % (key, tag)
            user = User(email=email, first_name="Tech", last_name=key, organization_id=org.id,
                        enterprise_role=persona, confirmed=True)
            user.role = roles[role]
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            out["emails"][key] = email
        both = User(email="techcost.both.%s@example.com" % tag, first_name="Tech", last_name="both",
                    organization_id=first.id, enterprise_role="enterprise_architect", confirmed=True)
        both.role = roles["Architect"]
        both.password = PASSWORD
        db.session.add(both)
        db.session.flush()
        db.session.add(OrgRole(organization_id=second.id, user_id=both.id, role="architect"))
        db.session.commit()
        out["emails"]["both"] = both.email
        db.session.add(ExchangeRate(from_currency=FOREIGN, to_currency=REPORTING, rate=0.9,
                                    effective_date=RATE_DAY, source="journey"))

        def application(name, org=first):
            element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                       organization_id=org.id)
            db.session.add(element)
            db.session.flush()
            row = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id)
            db.session.add(row)
            db.session.flush()
            return row

        def technology(name, served, org=first):
            node = ArchiMateElement(name=name, type="Node", layer="technology", organization_id=org.id)
            db.session.add(node)
            db.session.flush()
            for row in served:
                db.session.add(ArchiMateRelationship(source_id=node.id, target_id=row.archimate_element_id,
                                                     type="realization", organization_id=org.id))
            db.session.flush()
            return node

        alpha, beta, gamma = (application("Alpha %s" % tag), application("Beta %s" % tag),
                              application("Gamma %s" % tag))
        cluster = technology("Cluster %s" % tag, [alpha, beta, gamma])
        database = technology("Database %s" % tag, [alpha, beta])
        spare = technology("Spare node %s" % tag, [])
        store = technology("Store %s" % tag, [gamma])
        elsewhere = technology("Elsewhere %s" % tag, [application("Delta %s" % tag, second)], second)
        cost_fact_store.upsert_fact(first.id, "application", alpha.id, 2000, REPORTING, "journey")
        cost_fact_store.upsert_fact(first.id, "technology", database.id, 600, REPORTING, "journey",
                                    period_start=date(date.today().year, 1, 1),
                                    period_end=date(date.today().year, 12, 31))
        canvas = BusinessModelCanvas(name="Cost canvas %s" % tag, organization_id=first.id)
        other_canvas = BusinessModelCanvas(name="Other canvas %s" % tag, organization_id=second.id)
        db.session.add_all([canvas, other_canvas])
        db.session.commit()
        out.update(
            apps={"Alpha": alpha.id, "Beta": beta.id, "Gamma": gamma.id},
            cluster=cluster.id, database=database.id, spare=spare.id, store=store.id, elsewhere=elsewhere.id,
            canvas=canvas.id, other_canvas=other_canvas.id, second=second.id, first=first.id,
        )

    def remove_rate():
        with app.app_context():
            ExchangeRate.query.filter_by(from_currency=FOREIGN, to_currency=REPORTING,
                                         effective_date=RATE_DAY, source="journey").delete()
            db.session.commit()

    request.addfinalizer(remove_rate)
    return out


def _tech_url(base, element_id):
    return "%s/architecture/technology/Node/%d?ae=1" % (base, element_id)


def _open(page, url):
    assert page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).status == 200, url


def _text(locator):
    return re.sub(r"\s+", " ", locator.inner_text())


def _shot(target, name):
    """Save the screen the person is looking at: the given part of the page, scrolled into view."""
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        target.screenshot(path=os.path.join(WALK_DIR, name + ".png"))


def _add_cost(page, amount, currency, category):
    form = page.get_by_test_id("technology-cost-form")
    form.get_by_label("Amount").fill(amount)
    form.get_by_label("Currency").select_option(currency)
    form.get_by_label("Category").select_option(category)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_test_id("technology-cost-submit").click()


def test_architect_records_costs_and_every_page_states_the_same_shares(browser, live_server, estate):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["emails"]["architect"])

        # Nothing recorded yet: the panel says so, names the rule and offers the next step.
        _open(page, _tech_url(live_server, estate["cluster"]))
        panel = page.get_by_test_id("technology-cost")
        expect(panel).to_contain_text("No cost recorded yet")
        expect(panel.get_by_test_id("technology-cost-rule")).to_contain_text("split evenly")
        expect(panel.get_by_test_id("technology-cost-share-row")).to_have_count(3)
        _shot(page.get_by_test_id("technology-cost"), "technology-empty-1440-light")

        # Add a cost in the reporting currency, then one in the other currency (0.9 to the reporting one).
        _add_cost(page, "1000", REPORTING, "hosting")
        _add_cost(page, "500", FOREIGN, "support")
        panel = page.get_by_test_id("technology-cost")
        expect(panel.get_by_test_id("technology-cost-fact-row")).to_have_count(2)
        expect(panel.get_by_test_id("technology-cost-total")).to_contain_text("1,450.00 CAD")
        shares = {}
        for row in panel.get_by_test_id("technology-cost-share-row").all():
            name = _text(row.get_by_test_id("technology-cost-share-link")).split(" ")[0]
            shares[name] = _text(row.get_by_test_id("technology-cost-share-amount"))
        assert shares == {"Alpha": "483.34 CAD", "Beta": "483.33 CAD", "Gamma": "483.33 CAD"}, shares
        # The card beside the total always states a figure: the total does not divide to the penny, so it
        # gives the range and says where the leftover pennies go.
        each = panel.get_by_test_id("technology-cost-each")
        expect(each).to_contain_text("483.33 to 483.34 CAD")
        expect(each).to_contain_text("pennies left over go to the first applications")
        assert round(sum(float(v.split(" ")[0]) for v in shares.values()), 2) == 1450.00, shares
        # It persisted.
        page.reload()
        expect(page.get_by_test_id("technology-cost-total")).to_contain_text("1,450.00 CAD")
        _shot(page.get_by_test_id("technology-cost"), "technology-costed-1440-light")

        # Every application's page states the same share, and links back to a page stating the same total.
        direct = {"Alpha": "483.34 CAD", "Beta": "483.33 CAD", "Gamma": "483.33 CAD"}
        for name, share in direct.items():
            _open(page, _tech_url(live_server, estate["cluster"]))
            link = page.get_by_test_id("technology-cost-share-link").filter(has_text=name)
            with page.expect_navigation(timeout=PAGE_TIMEOUT):
                link.click()
            assert "/applications/%d" % estate["apps"][name] in page.url
            section = page.get_by_test_id("technology-cost-shares-section")
            expect(section).to_be_visible()
            row = section.get_by_test_id("app-technology-cost-row").filter(has_text="Cluster %s" % tag)
            expect(row.get_by_test_id("app-technology-cost-share")).to_have_text(share)
            expect(row.get_by_test_id("app-technology-cost-total")).to_have_text("1,450.00 CAD")
            with page.expect_navigation(timeout=PAGE_TIMEOUT):
                row.get_by_test_id("app-technology-cost-link").click()
            expect(page.get_by_test_id("technology-cost-total")).to_contain_text("1,450.00 CAD")
            expect(page.get_by_test_id("technology-cost-share-row").filter(has_text=name)).to_contain_text(share)

        # Alpha also runs on the Database (600.00 split two ways): its own page shows both shares.
        _open(page, "%s/applications/%d?tab=technology" % (live_server, estate["apps"]["Alpha"]))
        section = page.get_by_test_id("technology-cost-shares-section")
        expect(section.get_by_test_id("app-technology-cost-row")).to_have_count(2)
        database = section.get_by_test_id("app-technology-cost-row").filter(has_text="Database %s" % tag)
        expect(database.get_by_test_id("app-technology-cost-share")).to_have_text("300.00 CAD")
        _shot(page.get_by_test_id("technology-cost-shares-section"), "application-shares-1440-light")

        # The canvas cost structure: its total is Alpha's own 2,000.00 plus 1,450.00 plus 600.00, and each
        # application's line states the sum of the shares its own page shows.
        _open(page, "%s/business-model/%d" % (live_server, estate["canvas"]))
        notes = page.locator('[data-canvas-notes="cost-structure"]')
        expect(page.locator('[data-canvas-total="cost-structure"]')).to_have_text("Total a year: 4,050.00 CAD")
        expected = {"Alpha": "783.34", "Beta": "783.33", "Gamma": "483.33"}
        for name, amount in expected.items():
            line = notes.locator("li", has_text="%s %s, technology share" % (name, tag))
            expect(line).to_contain_text("%s CAD a year" % amount)
        _shot(page.locator('[data-canvas-total="cost-structure"]').locator("xpath=.."), "canvas-1440-light")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            notes.locator("a", has_text="Alpha %s, technology share" % tag).click()
        assert "/applications/%d" % estate["apps"]["Alpha"] in page.url
        parts = [float(_text(c).split(" ")[0].replace(",", "")) for c in
                 page.get_by_test_id("app-technology-cost-share").all()]
        assert round(sum(parts), 2) == 783.34, parts

        # Changing the cost moves every page together: raise the Cluster's hosting to 1,100.
        _open(page, _tech_url(live_server, estate["cluster"]))
        _add_cost(page, "1100", REPORTING, "hosting")
        expect(page.get_by_test_id("technology-cost-total")).to_contain_text("1,550.00 CAD")
        _open(page, "%s/business-model/%d" % (live_server, estate["canvas"]))
        expect(page.locator('[data-canvas-total="cost-structure"]')).to_have_text("Total a year: 4,150.00 CAD")

        # A technology with no cost and no application says so, and what to do.
        _open(page, _tech_url(live_server, estate["spare"]))
        panel = page.get_by_test_id("technology-cost")
        expect(panel).to_contain_text("No cost recorded yet")
        expect(panel).to_contain_text("No application is mapped to this technology yet")
    finally:
        page.close()


def test_a_cost_with_no_exchange_rate_is_reported_as_missing(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["emails"]["architect"])
        _open(page, _tech_url(live_server, estate["spare"]))
        _add_cost(page, "75", "JPY", "licence")
        panel = page.get_by_test_id("technology-cost")
        expect(panel.get_by_test_id("technology-cost-total")).to_contain_text("No exchange rate on file")
        expect(panel.get_by_test_id("technology-cost-total")).not_to_contain_text("0.00")
    finally:
        page.close()


def test_a_viewer_is_shown_no_way_to_add_a_cost_and_is_refused(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["emails"]["viewer"])
        _open(page, _tech_url(live_server, estate["database"]))
        panel = page.get_by_test_id("technology-cost")
        expect(panel.get_by_test_id("technology-cost-total")).to_contain_text("600.00 CAD")
        expect(page.get_by_test_id("technology-cost-form")).to_have_count(0)
        expect(panel.get_by_test_id("technology-cost-readonly")).to_contain_text("Only an architect")
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        refused = page.evaluate(
            """async ([url, token]) => { const r = await fetch(url, {method: 'POST', redirect: 'manual',
                headers: {'X-CSRFToken': token, 'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'amount=1&currency=CAD&category=hosting&period=annual&csrf_token=' + encodeURIComponent(token)});
                return r.status; }""",
            ["%s/architecture/technology-costs/%d" % (live_server, estate["database"]), token])
        assert refused in (0, 302, 401, 403), refused
        _open(page, _tech_url(live_server, estate["database"]))
        expect(page.get_by_test_id("technology-cost-total")).to_contain_text("600.00 CAD")
        expect(page.get_by_test_id("technology-cost-fact-row")).to_have_count(1)
    finally:
        page.close()


def test_a_second_organisation_sees_nothing_of_it(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["emails"]["other"])
        assert page.goto(_tech_url(live_server, estate["cluster"]), timeout=PAGE_TIMEOUT).status == 404
        assert page.goto("%s/applications/%d" % (live_server, estate["apps"]["Alpha"]),
                         timeout=PAGE_TIMEOUT).status in (403, 404)
        token_page = page.goto("%s/business-model/%d" % (live_server, estate["canvas"]), timeout=PAGE_TIMEOUT)
        assert token_page.status == 404
        _open(page, "%s/business-model/%d" % (live_server, estate["other_canvas"]))
        # Its own canvas has no cost to state: the box says it is empty rather than showing the first one's.
        expect(page.locator('[data-canvas-empty="cost-structure"]')).to_be_visible()
        assert page.locator('[data-canvas-notes="cost-structure"]').count() == 0
        body = _text(page.locator("main"))
        assert "Total a year" not in body and "technology share" not in body
        assert page.locator('[data-canvas-total="cost-structure"]').count() == 0
    finally:
        page.close()


def test_the_cost_pages_fit_a_phone_in_dark_mode(browser, live_server, estate):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, estate["emails"]["architect"])
        pages = (
            ("technology", _tech_url(live_server, estate["database"]), "technology-cost-share-row", "300.00 CAD"),
            ("technology-uneven", _tech_url(live_server, estate["cluster"]), "technology-cost-share-row", "516.67 CAD"),
            ("application", "%s/applications/%d?tab=technology" % (live_server, estate["apps"]["Beta"]),
             "app-technology-cost-row", "300.00 CAD"),
            ("canvas", "%s/business-model/%d" % (live_server, estate["canvas"]), None, "Total a year"),
        )
        for name, url, row, text in pages:
            _open(page, url)
            if row:
                rows = page.get_by_test_id(row)
                if name == "application":
                    rows = rows.filter(has_text="Database %s" % estate["tag"])
                expect(rows.first).to_contain_text(text)
            else:
                total = page.locator('[data-canvas-total="cost-structure"]')
                expect(total).to_contain_text(text)
                # A cost no application carries (the spare node's, in a currency with no rate) never hides the total.
                expect(total).not_to_contain_text("not shown")
            if name.startswith("technology"):
                # The pager under each table stays inside the table's own edges.
                for tid in ("technology-cost-shares", "technology-cost-facts"):
                    table = page.locator('[data-table-id="%s"]' % tid)
                    frame = table.locator('[data-slot="table-container"]').bounding_box()
                    for pager in table.locator('[data-slot="data-table-pagination"] button:visible').all():
                        box = pager.bounding_box()
                        assert box["x"] + box["width"] <= frame["x"] + frame["width"] + 0.5, (
                            "%s pager runs past the table at 390 wide" % tid)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 1, "%s page scrolls sideways by %dpx at 390 wide" % (name, overflow)
            _shot(page.get_by_test_id({"technology": "technology-cost", "technology-uneven": "technology-cost", "application": "technology-cost-shares-section"}[name])
                  if name != "canvas" else page.locator('[data-canvas-total="cost-structure"]').locator("xpath=.."), "%s-390-dark" % name)
    finally:
        context.close()


def test_a_person_in_two_organisations_works_in_the_active_one(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["emails"]["both"])
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        switched = page.evaluate(
            """async ([url, token, org]) => { const r = await fetch(url, {method: 'POST', redirect: 'manual',
                headers: {'X-CSRFToken': token, 'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'organization_id=' + org + '&csrf_token=' + encodeURIComponent(token)});
                return r.status; }""",
            ["%s/account/switch-organization" % live_server, token, estate["second"]])
        assert switched in (0, 200, 302), switched
        # Active in the second organisation: its technology shows its application and takes a cost.
        _open(page, _tech_url(live_server, estate["elsewhere"]))
        panel = page.get_by_test_id("technology-cost")
        expect(panel.get_by_test_id("technology-cost-share-row")).to_have_count(1)
        expect(panel.get_by_test_id("technology-cost-share-row")).to_contain_text("Delta %s" % estate["tag"])
        _add_cost(page, "240", REPORTING, "licence")
        panel = page.get_by_test_id("technology-cost")
        expect(panel.get_by_test_id("technology-cost-total")).to_contain_text("240.00 CAD")
        expect(panel.get_by_test_id("technology-cost-share-row")).to_contain_text("240.00 CAD")
        page.reload()
        expect(page.get_by_test_id("technology-cost-total")).to_contain_text("240.00 CAD")
        # And never the first organisation's.
        assert page.goto(_tech_url(live_server, estate["cluster"]), timeout=PAGE_TIMEOUT).status == 404
    finally:
        page.close()


def test_an_architect_removes_a_cost_after_confirming(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["emails"]["architect"])
        _open(page, _tech_url(live_server, estate["store"]))
        _add_cost(page, "90", REPORTING, "licence")
        _add_cost(page, "30", REPORTING, "support")
        panel = page.get_by_test_id("technology-cost")
        expect(panel.get_by_test_id("technology-cost-total")).to_contain_text("120.00 CAD")
        row = panel.get_by_test_id("technology-cost-fact-row").filter(has_text="Licence")
        row.get_by_test_id("technology-cost-remove").click()
        # Asking first: keeping it changes nothing.
        expect(row).to_contain_text("Remove this cost?")
        row.get_by_test_id("technology-cost-remove-cancel").click()
        expect(panel.get_by_test_id("technology-cost-fact-row")).to_have_count(2)
        row.get_by_test_id("technology-cost-remove").click()
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            row.get_by_test_id("technology-cost-remove-confirm").click()
        panel = page.get_by_test_id("technology-cost")
        expect(panel.get_by_test_id("technology-cost-fact-row")).to_have_count(1)
        expect(panel.get_by_test_id("technology-cost-total")).to_contain_text("30.00 CAD")
        # Gone for good, on this page and on the application it serves.
        page.reload()
        expect(page.get_by_test_id("technology-cost-total")).to_contain_text("30.00 CAD")
        _open(page, "%s/applications/%d?tab=technology" % (live_server, estate["apps"]["Gamma"]))
        row = page.get_by_test_id("app-technology-cost-row").filter(has_text="Store %s" % estate["tag"])
        expect(row.get_by_test_id("app-technology-cost-share")).to_have_text("30.00 CAD")
        # Removing the last cost returns to the empty state, which says what to do.
        _open(page, _tech_url(live_server, estate["store"]))
        row = page.get_by_test_id("technology-cost-fact-row")
        row.get_by_test_id("technology-cost-remove").click()
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            row.get_by_test_id("technology-cost-remove-confirm").click()
        expect(page.get_by_test_id("technology-cost-total")).to_contain_text("No cost recorded yet")
    finally:
        page.close()
