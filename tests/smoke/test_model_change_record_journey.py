"""enterprise_architect: change the model three ways, then read the Changes page.

The architect draws an element on the composer, corrects an application's cost
and owner on the enrichment screen, and imports a model file. Then they open
Changes from the sidebar and need to see each of those: what it was, what it
became, who made it and where it came from. Another organisation's change must
never appear, an empty organisation must be told why and where to start, and
the Model as of page must show the same elements.
"""
import re
import uuid
from datetime import datetime

import pytest
from playwright.sync_api import expect

from .chat_ask import role_of
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OEF = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
       identifier="id-model-{s}" version="3.0">
  <name xml:lang="en">Change record {s}</name>
  <elements>
    <element identifier="id-a-{s}" xsi:type="BusinessActor"><name xml:lang="en">Imported actor {s}</name></element>
    <element identifier="id-p-{s}" xsi:type="BusinessProcess"><name xml:lang="en">Imported process {s}</name></element>
  </elements>
  <relationships>
    <relationship identifier="id-r-{s}" source="id-a-{s}" target="id-p-{s}" xsi:type="Assignment"/>
  </relationships>
</model>
"""


def _open_from_search(page, query, label):
    """Find a page the way a person does: type its name in the sidebar search box."""
    page.locator("#sidebar-search").fill(query)
    hit = page.get_by_test_id("sidebar-search-page").filter(has_text=label).first
    try:
        hit.wait_for(timeout=15000)
    except Exception:
        page.screenshot(path="/tmp/audit01_dbg.png")
        raise AssertionError("no hit; url=%s; search=%r; body=%s" % (
            page.url, page.locator("#sidebar-search").input_value(), page.inner_text("body")[:900]))
    hit.click()


def _seed_org_with_linked_data(suffix):
    """An organisation with an application that has a cost and an owner, plus two people."""
    from decimal import Decimal

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User
    from app.services.application_cost_accessor import set_annual_cost
    from app.services.element_ownership_service import set_element_owner

    org = create_fresh_org("enterprise_architect")
    app = create_app("testing")
    with app.app_context():
        people = {}
        for first, last in (("Ola", "Ledger"), ("Priya", "Shah")):
            user = User(
                email=f"{first.lower()}.{suffix}@example.com", first_name=first,
                last_name=f"{last}{suffix}", organization_id=org["org_id"],
                enterprise_role="application_manager", confirmed=True,
            )
            user.password = "Journey-pass-1!"
            db.session.add(user)
            db.session.flush()
            people[first] = user
        application = ApplicationComponent(name=f"Ledger {suffix}", organization_id=org["org_id"])
        db.session.add(application)
        db.session.flush()
        set_annual_cost(application, Decimal("1000"))
        set_element_owner(
            element_type="application", element_id=application.id, user_id=people["Ola"].id,
            organization_id=org["org_id"], ownership_type="primary", commit=False,
        )
        db.session.commit()
        org["application_name"] = application.name
        org["new_owner"] = f"Priya Shah{suffix}"
        db.session.remove()
    return org


def _seed_other_org_change(suffix):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    other = create_fresh_org("enterprise_architect")
    app = create_app("testing")
    with app.app_context():
        db.session.add(ArchiMateElement(
            name=f"Rival secret {suffix}", type="ApplicationComponent", layer="application",
            organization_id=other["org_id"],
        ))
        db.session.commit()
        db.session.remove()
    return other


def test_architect_sees_what_changed_who_changed_it_and_where_it_came_from(browser, live_server, tmp_path):
    suffix = uuid.uuid4().hex[:6]
    org = _seed_org_with_linked_data(suffix)
    _seed_other_org_change(suffix)
    email = org["emails"]["enterprise_architect"]
    assert role_of(email) == "enterprise_architect", "the journey must run as the architect, not an administrator"

    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, email)

        # 1. Draw an element on the composer.
        canvas_name = f"Canvas element {suffix}"
        page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded")
        expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible()
        page.keyboard.press("Control+k")
        page.locator("#quick-add-input").fill(canvas_name)
        page.get_by_role("button", name=re.compile("^Create$")).click()
        expect(page.locator(".joint-element")).to_have_count(1)

        # 2. Correct the application's cost and owner.
        page.goto(live_server + "/applications/rationalization/enrich", wait_until="domcontentloaded")
        row = page.locator("tr", has=page.get_by_text(org["application_name"], exact=True))
        expect(row).to_be_visible()
        row.get_by_label("Total cost of ownership").fill("2500")
        row.get_by_label("Owner name").fill(org["new_owner"])
        row.get_by_role("button", name="Save", exact=True).click()
        expect(row.get_by_role("button", name="Saved", exact=True)).to_be_visible()

        # 3. Import a model file.
        path = tmp_path / "model.xml"
        path.write_text(OEF.format(s=suffix), encoding="utf-8")
        page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded")
        page.click("[data-testid='import-oef-link']")
        page.wait_for_url("**/architecture/import/oef")
        page.set_input_files("#oef_file", str(path))
        page.click("[data-testid='btn-preview-import']")
        expect(page.locator("[data-testid='import-preview']")).to_be_visible()
        page.click("[data-testid='btn-import-model']")
        expect(page.locator("[data-testid='import-result']")).to_be_visible()

        # 4. Read the changes, from the sidebar.
        page.goto(live_server + "/", wait_until="domcontentloaded")
        _open_from_search(page, "what changed", "Model changes")
        page.wait_for_url("**/intelligence/history/changes**")
        rows = page.get_by_test_id("change-row")

        drawn = rows.filter(has_text=canvas_name)
        expect(drawn).to_have_count(1)
        expect(drawn).to_contain_text("Created")
        expect(drawn).to_contain_text("Screen")
        expect(drawn).to_contain_text(email)

        cost = rows.filter(has_text=org["application_name"]).filter(has_text="Total cost of ownership: 1000 → 2500")
        expect(cost).to_have_count(1)
        expect(cost).to_contain_text("Updated")
        expect(cost).to_contain_text("Screen")

        owner = rows.filter(has_text=f"Owner: ola.{suffix}@example.com → priya.{suffix}@example.com")
        expect(owner).to_have_count(1)
        expect(owner).to_contain_text("Updated")
        expect(owner).to_contain_text("Screen")
        # The relationship the import drew reads as its two ends, not as a number.
        expect(rows.filter(has_text=f"Assignment: Imported actor {suffix} to Imported process {suffix}")).to_have_count(1)
        # Nothing is shown twice.
        expect(rows.filter(has_text=f"Name: Imported actor {suffix}")).to_have_count(1)

        imported = rows.filter(has_text=f"Name: Imported actor {suffix}")
        expect(imported).to_have_count(1)
        expect(imported).to_contain_text("Import")
        assert f"Rival secret {suffix}" not in page.inner_text("body"), "another organisation's change is shown"

        # Narrow to imports: the screen edits go, the import stays.
        page.get_by_label("Source").select_option("import")
        page.get_by_role("button", name="Show changes", exact=True).click()
        page.wait_for_url("**source=import**")
        expect(page.get_by_test_id("change-row").filter(has_text=f"Name: Imported actor {suffix}")).to_have_count(1)
        expect(page.get_by_test_id("change-row").filter(has_text=canvas_name)).to_have_count(0)

        # The record can be checked, and it is intact.
        page.get_by_role("button", name="Check the record is intact").click()
        expect(page.get_by_text(re.compile(r"^Intact: \d+ entries checked"))).to_be_visible()

        # 5. Model as of today shows the elements just drawn and imported.
        _open_from_search(page, "model as of", "Model as of")
        page.wait_for_url("**/intelligence/history/as-of**")
        for name in (canvas_name, f"Imported actor {suffix}", f"Imported process {suffix}"):
            expect(page.get_by_test_id("element-row").filter(has_text=name)).to_have_count(1)
        expect(page.get_by_test_id("relationship-row").filter(has_text=f"Imported actor {suffix}")).to_have_count(1)
        # A date before any of it: the model is empty and says so.
        page.fill("#as-of-date", "2020-01-01")
        page.get_by_role("button", name="Show the model", exact=True).click()
        expect(page.get_by_text("The model was empty on this date")).to_be_visible()
        expect(page.get_by_role("link", name="Open the composer")).to_be_visible()

        # The link from a date to what changed since lands on its changes.
        page.fill("#as-of-date", datetime.utcnow().strftime("%Y-%m-%d"))
        page.get_by_role("button", name="Show the model", exact=True).click()
        page.get_by_role("link", name="What changed since").click()
        page.wait_for_url("**/intelligence/history/changes?from=**")
        expect(page.get_by_test_id("change-row").first).to_be_visible()
    finally:
        context.close()


def test_an_organisation_with_no_changes_is_told_why_and_where_to_start(browser, live_server):
    empty = create_fresh_org("enterprise_architect")
    email = empty["emails"]["enterprise_architect"]
    assert role_of(email) == "enterprise_architect"
    context = browser.new_context(viewport={"width": 390, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/intelligence/history/changes", wait_until="domcontentloaded")
        expect(page.get_by_text("No changes recorded between these dates")).to_be_visible()
        page.get_by_role("link", name="Open the composer").click()
        page.wait_for_url("**/archimate/composer**")
        expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible()
    finally:
        context.close()


def test_on_a_phone_each_change_reads_as_a_card_with_before_and_after(browser, live_server):
    suffix = uuid.uuid4().hex[:6]
    org = _seed_org_with_linked_data(suffix)
    email = org["emails"]["enterprise_architect"]
    assert role_of(email) == "enterprise_architect"
    context = browser.new_context(viewport={"width": 390, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/intelligence/history/changes", wait_until="domcontentloaded")
        card = page.get_by_test_id("change-card").filter(has_text=f"Ledger {suffix}").filter(
            has_text="Total cost of ownership: — → 1000"
        )
        expect(card).to_have_count(1)
        expect(card).to_be_visible()
        expect(card).to_contain_text("Updated")
        # The wide table is not what a phone is given, so nothing is cut off at the edge.
        expect(page.get_by_test_id("change-row").first).to_be_hidden()
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, "the page scrolls sideways on a phone by %spx" % overflow
    finally:
        context.close()
