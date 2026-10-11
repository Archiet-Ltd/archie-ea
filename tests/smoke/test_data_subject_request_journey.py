"""Data Protection Officer (security_architect): scope a data-subject request,
give a search to an owner, and erase a user's data — reloading after each
step to see it recorded.

Drives the real controls: the sidebar link, the request form, the Assign
button on a scoped system, then an erasure request's review and run buttons.
After each reload the assignment and the evidence entry must still be there.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    """A personal-data category used by one application, an owner to assign
    the search to, and a user to erase — all in the seeded organisation."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.process_data import DataDomain, DataEntity
    from app.models.user import User

    s = uuid.uuid4().hex[:8]
    app = create_app("testing")
    with app.app_context():
        domain = DataDomain(name="Customer domain %s" % s, organization_id=org_id)
        db.session.add(domain)
        db.session.flush()
        category = DataEntity(name="Customer contact %s" % s, domain_id=domain.id,
                              contains_pii=True, organization_id=org_id)
        system = ApplicationComponent(name="Customer portal %s" % s, organization_id=org_id)
        db.session.add_all([category, system])
        db.session.flush()
        db.session.add(ArchiMateRelationship(
            type="access", source_id=system.archimate_element_id,
            target_id=category.archimate_element_id, organization_id=org_id,
        ))
        owner = User(email="dsr.owner.%s@example.com" % s, first_name="Search", last_name="Owner",
                     organization_id=org_id, enterprise_role="data_architect", confirmed=True)
        leaver = User(email="dsr.leaver.%s@example.com" % s, first_name="Leaving", last_name="Person",
                      organization_id=org_id, enterprise_role="solution_architect", confirmed=True)
        db.session.add_all([owner, leaver])
        db.session.commit()
        out = {"system": system.name, "owner": owner.email, "leaver": leaver.email, "leaver_id": leaver.id,
               "reference": "Customer reference %s" % s}
        db.session.remove()
    return out


def test_dpo_scopes_assigns_and_erases_with_evidence(browser, live_server, seeded):
    data = _seed(seeded["ids"]["org"])
    context = browser.new_context()
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["security_architect"])

        page.get_by_test_id("sidebar").get_by_role("link", name="Data Subject Requests", exact=True).click()
        page.wait_for_url("**/compliance/data-subject-requests", timeout=PAGE_TIMEOUT)

        # 1. Enter a request type and see the scoped system list.
        page.get_by_test_id("dsr-type").select_option("access")
        page.get_by_test_id("dsr-subject-reference").fill(data["reference"])
        page.get_by_test_id("dsr-create-submit").click()
        page.wait_for_url("**/compliance/data-subject-requests/*", timeout=PAGE_TIMEOUT)
        scope = page.get_by_test_id("dsr-scope")
        expect(scope).to_contain_text(data["system"], timeout=PAGE_TIMEOUT)

        # 2. Assign the system's search to an owner, reload, see it recorded.
        item = scope.locator("li[data-key]", has_text=data["system"]).first
        item.locator("select[name=assignee_id]").select_option(label=data["owner"])
        item.get_by_role("button", name="Assign").click()
        expect(page.get_by_text("assigned.", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        item = page.get_by_test_id("dsr-scope").locator("li[data-key]", has_text=data["system"]).first
        expect(item.get_by_test_id("dsr-item-assignee")).to_have_text(data["owner"], timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("dsr-evidence-dsr_assigned")).to_be_visible(timeout=PAGE_TIMEOUT)

        # 3. Erase a test user: record, review, confirm, run, reload, see evidence.
        page.get_by_role("link", name="Data Subject Requests").first.click()
        page.wait_for_url("**/compliance/data-subject-requests", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("dsr-type").select_option("erasure")
        page.get_by_test_id("dsr-subject-user").select_option(label=data["leaver"])
        page.get_by_test_id("dsr-create-submit").click()
        page.wait_for_url("**/compliance/data-subject-requests/*", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("dsr-erasure-preview")).to_contain_text("email", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("dsr-erasure-review").click()
        expect(page.get_by_test_id("dsr-erasure-reviewed")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_test_id("dsr-erasure-confirm").check()
        page.get_by_test_id("dsr-erasure-run").click()
        expect(page.get_by_test_id("dsr-erasure-done")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        evidence = page.get_by_test_id("dsr-evidence-gdpr_delete")
        expect(evidence).to_contain_text("Erasure run", timeout=PAGE_TIMEOUT)
        expect(evidence).to_contain_text("email", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("dsr-erasure-done")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("dsr-subject")).to_contain_text("Erased platform user", timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        leaver = db.session.get(User, data["leaver_id"])
        assert leaver.email is None and leaver.first_name is None
        # Leave nothing behind in the shared organisation for later journeys.
        for user in (leaver, User.query.filter_by(email=data["owner"]).first()):
            if user is not None:
                db.session.delete(user)
        db.session.commit()
        db.session.remove()
