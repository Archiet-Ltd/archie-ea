"""A startup CTO reads the applications list and acts on it, in a real browser.

The goal: see which applications lack an owner, a capability or a vendor, follow
the link to exactly those applications, and open, edit, map and delete from the
row. The list must agree with itself:

* the summary's count equals the number of rows its link then shows;
* the Owner column names the people on the owner records (or the owner text
  recorded on the application), never a blank;
* the capability column agrees with the "no capability" figure;
* the Record ring differs between records that fill different fields;
* another organisation's applications never appear, in rows or in figures;
* an organisation with no applications is told why and offered the next step;
* the same list works at phone width, and every control on it is a component
  (no hand-written button, input or select).
"""

import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _make_org(app, label, with_apps):
    """One organisation with an administrator and, optionally, a linked portfolio."""
    from app import db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    suffix = uuid.uuid4().hex[:8]
    out = {"names": {}}
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="%s %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()
        user = User(
            email="%s.%s@example.com" % (label.lower(), suffix), first_name=label, last_name="Owner",
            organization_id=org.id, enterprise_role="platform_admin", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        out.update(org=org.id, email=user.email, person="%s Owner" % label)
        if not with_apps:
            return out

        capability = BusinessCapability(name="Billing %s" % suffix, organization_id=org.id)
        db.session.add(capability)
        db.session.flush()

        def add(name, **fields):
            row = ApplicationComponent(name="%s %s" % (name, suffix), organization_id=org.id, **fields)
            db.session.add(row)
            db.session.flush()
            out["names"][name] = row
            return row

        full = add(
            "Alpha Billing", description="Invoices customers.", business_criticality="high",
            lifecycle_status="2.1 strategic", business_domain="Finance", technology_stack="Python",
            deployment_model="cloud", data_classification="confidential", vendor_name="Acme Software",
            component_type="web_application",
        )
        sparse = add("Bravo CRM", lifecycle_status="2.2 tactical", vendor_name="Globex", component_type="web_application")
        legacy = add("Charlie Legacy", lifecycle_status="3. sunset", legacy_business_owner="Casey Recorded",
                     component_type="web_application")
        add("Delta Orphan", component_type="web_application")
        add("Echo Retired", lifecycle_status="5. decommissioned", component_type="web_application")

        for row in (full, sparse):
            db.session.add(ApplicationOwner(application_id=row.id, user_id=user.id, organization_id=org.id,
                                            ownership_type="primary"))
        for row in (full, legacy):
            db.session.add(ApplicationCapabilityMapping(application_component_id=row.id,
                                                        business_capability_id=capability.id,
                                                        organization_id=org.id))
        db.session.commit()
        out["ids"] = {key: row.id for key, row in out["names"].items()}
        out["names"] = {key: row.name for key, row in out["names"].items()}
        out["capability"] = capability.name
        return out


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


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


def _open_list(page, base, query=""):
    response = page.goto(base + "/applications/" + query, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200
    page.wait_for_selector('[data-testid="applications-table"], [data-testid="mobile-cards"], [role="status"]')
    page.wait_for_timeout(600)


def _summary(page):
    text = " ".join(page.locator('[data-testid="data-quality-alert"]').inner_text().split())
    numbers = {}
    for key, pattern in (("owner", r"(\d+) of (\d+) have no owner"), ("capability", r"(\d+) of (\d+) have no capability"),
                         ("vendor", r"(\d+) of (\d+) have no vendor")):
        match = re.search(pattern, text)
        numbers[key] = (int(match.group(1)), int(match.group(2))) if match else (0, None)
    return text, numbers


def _row_texts(page, testid_prefix):
    return {
        loc.get_attribute("data-testid"): " ".join(loc.inner_text().split())
        for loc in page.locator('[data-testid^="%s"]' % testid_prefix).all()
    }


@pytest.fixture
def worlds(app, live_server):
    return {
        "a": _make_org(app, "Alpha", with_apps=True),
        "b": _make_org(app, "Bravo", with_apps=True),
        "empty": _make_org(app, "Empty", with_apps=False),
    }


def test_summary_owner_capability_and_ring_agree_with_the_rows(page, live_server, worlds):
    a, b = worlds["a"], worlds["b"]
    _login(page, live_server, a["email"])
    _open_list(page, live_server)

    # The decommissioned application is hidden by default; the other four show.
    rows = page.locator('[data-testid^="app-row-"]')
    assert rows.count() == 4
    assert page.locator('[data-testid="total-count-badge"]').inner_text().startswith("4 shown")

    # The summary is in full, with no ellipsis, and counts the four listed.
    text, numbers = _summary(page)
    assert "..." not in text and "…" not in text
    assert numbers["owner"] == (2, 4), text       # Charlie has recorded owner text, Alpha and Bravo have records
    assert numbers["capability"] == (2, 4), text  # Bravo and Delta are unmapped
    assert numbers["vendor"] == (2, 4), text

    # The figure equals what the rows say.
    capability_cells = _row_texts(page, "app-capability-")
    assert sum(1 for t in capability_cells.values() if t == "Not mapped") == numbers["capability"][0]
    assert all(a["capability"] in capability_cells["app-capability-%s" % a["ids"][k]] for k in ("Alpha Billing", "Charlie Legacy"))
    owner_cells = _row_texts(page, "app-owner-")
    assert sum(1 for t in owner_cells.values() if t.startswith("No owner")) == numbers["owner"][0]
    # The Owner column names the person on the record, and the owner text recorded on the application.
    assert a["person"] in owner_cells["app-owner-%s" % a["ids"]["Alpha Billing"]]
    assert a["person"] in owner_cells["app-owner-%s" % a["ids"]["Bravo CRM"]]
    assert owner_cells["app-owner-%s" % a["ids"]["Charlie Legacy"]] == "Casey Recorded"
    assert owner_cells["app-owner-%s" % a["ids"]["Delta Orphan"]].startswith("No owner")

    # Records that fill different fields do not show the same ring.
    def ring(app_name):
        label = page.locator('[data-testid="app-row-%s"] a[aria-label^="Record"]' % a["ids"][app_name]).get_attribute("aria-label")
        return int(re.search(r"Record (\d+)%", label).group(1))
    assert ring("Alpha Billing") > ring("Bravo CRM") > ring("Delta Orphan"), (
        ring("Alpha Billing"), ring("Bravo CRM"), ring("Delta Orphan"))

    # Following "no capability" lists exactly those applications, and they all say Not mapped.
    page.locator('[data-testid="dq-no-capability"]').click()
    page.wait_for_selector('[data-testid="applications-table"]')
    listed = page.locator('[data-testid^="app-row-"]')
    assert listed.count() == numbers["capability"][0]
    assert all(t == "Not mapped" for t in _row_texts(page, "app-capability-").values())
    assert "No capability" in page.locator('[data-testid="active-filters"]').inner_text()
    # The chip takes the filter off again.
    page.locator('[data-testid="active-filters"] a').first.click()
    page.wait_for_selector('[data-testid="applications-table"]')
    assert page.locator('[data-testid^="app-row-"]').count() == 4

    # Following "no owner" does the same.
    page.locator('[data-testid="dq-no-owner"]').click()
    page.wait_for_selector('[data-testid="applications-table"]')
    assert page.locator('[data-testid^="app-row-"]').count() == numbers["owner"][0]

    # Including decommissioned applications adds the fifth, and the summary counts it too.
    _open_list(page, live_server, "?include_decom=true")
    assert page.locator('[data-testid^="app-row-"]').count() == 5
    _, with_decom = _summary(page)
    assert with_decom["owner"] == (3, 5) and with_decom["capability"] == (3, 5)

    # Another organisation's applications never appear, in rows or in figures.
    body = page.locator("main").inner_text()
    assert not any(name in body for name in b["names"].values())


def test_row_menu_links_land_on_the_application_and_delete_persists(page, live_server, worlds):
    a = worlds["a"]
    _login(page, live_server, a["email"])
    delta = a["ids"]["Delta Orphan"]
    delta_name = a["names"]["Delta Orphan"]

    for testid, expected in (("btn-view-%s", delta_name), ("btn-factsheet-%s", delta_name), ("btn-edit-%s", delta_name)):
        _open_list(page, live_server)
        page.locator('[data-testid="btn-actions-%s"]' % delta).click()
        page.locator('[data-testid="%s"]' % (testid % delta)).click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)
        assert expected in page.content(), "%s did not land on %s" % (testid, delta_name)

    # Map a capability opens the mapping dialog for that application.
    _open_list(page, live_server)
    page.locator('[data-testid="btn-actions-%s"]' % delta).click()
    page.locator('[data-testid="action-map-capability-%s"]' % delta).click()
    page.wait_for_timeout(800)
    assert delta_name in page.locator("body").inner_text()
    page.keyboard.press("Escape")

    # Delete asks first; cancelling keeps it; confirming removes it after reload.
    _open_list(page, live_server)
    page.locator('[data-testid="btn-actions-%s"]' % delta).click()
    page.locator('[data-testid="btn-delete-%s"]' % delta).click()
    dialog = page.get_by_role("dialog").filter(has_text="cannot be undone")
    dialog.wait_for()
    dialog.get_by_role("button", name=re.compile("cancel", re.I)).click()
    page.reload(wait_until="domcontentloaded")
    assert page.locator('[data-testid="app-row-%s"]' % delta).count() == 1

    page.locator('[data-testid="btn-actions-%s"]' % delta).click()
    page.locator('[data-testid="btn-delete-%s"]' % delta).click()
    dialog = page.get_by_role("dialog").filter(has_text="cannot be undone")
    dialog.wait_for()
    dialog.get_by_role("button", name=re.compile("delete|confirm|ok", re.I)).last.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(800)
    _open_list(page, live_server)
    assert page.locator('[data-testid="app-row-%s"]' % delta).count() == 0
    assert page.locator('[data-testid^="app-row-"]').count() == 3


def test_filters_sort_and_pagination_controls_work(page, live_server, worlds):
    a = worlds["a"]
    _login(page, live_server, a["email"])
    _open_list(page, live_server)

    page.fill("#filter-search", "Bravo")
    page.wait_for_url(re.compile("search=Bravo"))
    page.wait_for_selector('[data-testid="applications-table"]')
    assert page.locator('[data-testid^="app-row-"]').count() == 1
    page.locator('[data-testid="btn-clear-filters"]').click()
    page.wait_for_url(lambda url: "search=" not in url)
    page.wait_for_selector('[data-testid="applications-table"]')
    assert page.locator('[data-testid^="app-row-"]').count() == 4

    page.select_option("#filter-status", "2.1 strategic")
    page.wait_for_url(re.compile("status="))
    page.wait_for_selector('[data-testid="applications-table"]')
    assert page.locator('[data-testid^="app-row-"]').count() == 1

    _open_list(page, live_server)
    page.locator('[data-testid="th-sort-name"]').click()
    page.wait_for_url(re.compile("sort=name"))
    names = [t.split(" ")[0] for t in
             [loc.inner_text() for loc in page.locator('[data-testid^="app-row-"] td:nth-child(2) a').all()]]
    assert names == sorted(names)

    page.select_option("#page-size-select", "50")
    page.wait_for_url(re.compile("page_size=50"))
    assert page.locator('[data-testid="btn-prev-page"]').is_disabled()
    assert page.locator('[data-testid="btn-next-page"]').is_disabled()


def test_bulk_toolbar_and_create_dialog(page, live_server, worlds):
    a = worlds["a"]
    _login(page, live_server, a["email"])
    _open_list(page, live_server)

    page.locator('[data-testid="row-select-%s"]' % a["ids"]["Bravo CRM"]).check()
    toolbar = page.locator('[data-testid="bulk-toolbar"]')
    toolbar.wait_for()
    assert "1 selected" in toolbar.inner_text()
    page.locator('[data-testid="bulk-btn-lifecycle"]').click()
    page.get_by_role("menuitem", name="3. SUNSET").click()
    page.wait_for_timeout(1500)
    page.reload(wait_until="domcontentloaded")
    row = page.locator('[data-testid="app-row-%s"]' % a["ids"]["Bravo CRM"])
    assert "3. SUNSET" in row.inner_text()

    # Add an application from the dialog; it is there after a reload.
    page.locator('[data-testid="btn-add-application"]').click()
    page.fill("#ca-name", "Foxtrot Intake")
    page.select_option("#ca-type", "service")
    page.get_by_role("button", name="Add Application").last.click()
    page.wait_for_timeout(2000)
    _open_list(page, live_server, "?search=Foxtrot")
    assert page.locator('[data-testid^="app-row-"]').count() == 1


def test_phone_width_shows_the_same_answers_and_every_action(page, live_server, worlds):
    a = worlds["a"]
    page.set_viewport_size({"width": 390, "height": 844})
    _login(page, live_server, a["email"])
    _open_list(page, live_server)

    cards = page.locator('[data-testid^="mobile-card-"]')
    assert cards.count() == 4
    assert page.locator('[data-testid="applications-table"]').is_hidden()
    # The page does not scroll sideways.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    # The summary is still there in full, and the same owner and capability answers show.
    text, numbers = _summary(page)
    assert numbers["capability"] == (2, 4)
    charlie = page.locator('[data-testid="mobile-card-%s"]' % a["ids"]["Charlie Legacy"]).inner_text()
    assert "Casey Recorded" in charlie and a["capability"] in charlie
    delta = page.locator('[data-testid="mobile-card-%s"]' % a["ids"]["Delta Orphan"]).inner_text()
    assert "No owner" in delta and "Not mapped" in delta
    # Fact sheet, edit and delete are all reachable from the card's menu.
    page.locator('[data-testid="btn-actions-mobile-%s"]' % a["ids"]["Delta Orphan"]).click()
    for testid in ("btn-factsheet-mobile-%s", "btn-edit-mobile-%s", "action-map-capability-mobile-%s", "btn-delete-mobile-%s"):
        assert page.locator('[data-testid="%s"]' % (testid % a["ids"]["Delta Orphan"])).is_visible(), testid


def test_second_organisation_sees_only_its_own_and_an_empty_one_is_told_why(page, live_server, worlds):
    a, b, empty = worlds["a"], worlds["b"], worlds["empty"]
    _login(page, live_server, b["email"])
    _open_list(page, live_server)
    body = page.locator("main").inner_text()
    assert all(name in body for name in (b["names"]["Alpha Billing"], b["names"]["Delta Orphan"]))
    assert not any(name in body for name in a["names"].values())
    _, numbers = _summary(page)
    assert numbers["capability"] == (2, 4)

    page.context.clear_cookies()
    _login(page, live_server, empty["email"])
    _open_list(page, live_server)
    status = page.get_by_role("status").filter(has_text="No applications yet")
    status.wait_for()
    text = status.inner_text()
    assert "portfolio is empty" in text
    assert status.get_by_role("button", name=re.compile("Import Applications")).is_visible()
    assert page.locator('[data-testid="data-quality-alert"]').count() == 0
    status.get_by_role("button", name=re.compile("Import Applications")).click()
    page.get_by_role("dialog").filter(has_text="Import").first.wait_for()


def test_the_list_template_is_built_from_components_only():
    """No hand-written control, inline style, shadow or raw colour in the page itself."""
    import os

    template = os.path.join(os.path.dirname(__file__), "..", "..", "app", "templates", "applications", "list_simple.html")
    with open(template, encoding="utf-8") as handle:
        source = re.sub(r"{#.*?#}", "", handle.read(), flags=re.S)
    raw_control = re.compile(r"<(button|input|select|textarea|table|dialog)\b|role=[\"'](tab|menu|menuitem|dialog)[\"']", re.I)
    inline_style = re.compile(r"\bstyle\s*=\s*[\"']")
    shadow = re.compile(r"(?<![\w-])shadow(?:-(?:sm|md|lg|xl|2xl|inner))?(?![\w-])|box-shadow\s*:|drop-shadow")
    off_token = re.compile(r"(?<![\w-])(?:bg|text|border|ring)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|pink|rose)-\d{2,3}")
    for number, line in enumerate(source.splitlines(), 1):
        assert not raw_control.search(line), "hand-written control on line %d: %s" % (number, line.strip()[:100])
        assert not inline_style.search(line), "inline style on line %d" % number
        assert not shadow.search(line), "shadow on line %d" % number
        assert not off_token.search(line), "raw colour on line %d" % number
    assert re.search(r"{%-?\s*from\s+'components/", source), "the page imports no component macro"
