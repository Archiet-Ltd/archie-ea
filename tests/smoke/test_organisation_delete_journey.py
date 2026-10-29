"""A platform administrator deletes an organisation from the admin pages.

The organisation's page says plainly what deleting it does, the administrator confirms, and the
organisation is gone from the organisations list with its only member. The Default organisation's
page says it cannot be deleted, and a delete sent anyway is refused with that reason on the page.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    pg.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    yield pg
    ctx.close()


@pytest.fixture
def doomed(app):
    """An organisation with one member who belongs nowhere else."""
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name="Journey Doomed %s" % marker, slug="journey-doomed-%s" % marker)
        db.session.add(org)
        db.session.flush()
        member = User(first_name="Only", last_name="Member", email="only-member-%s@example.test" % marker,
                      password="test-password-123", confirmed=True, organization_id=org.id)
        db.session.add(member)
        db.session.commit()
        made = {"org_id": org.id, "name": org.name, "member_id": member.id}
        db.session.remove()
    yield made
    with app.app_context():
        from app.services.org_deletion_service import OrganizationDeletionService

        if db.session.get(Organization, made["org_id"]) is not None:
            OrganizationDeletionService.delete_organization(made["org_id"])
        db.session.remove()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_an_administrator_deletes_an_organisation_and_it_is_gone(page, live_server, seeded, app, doomed):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/organizations/%d" % doomed["org_id"], wait_until="domcontentloaded")

    delete_form = page.locator("form[action$='/organizations/%d/delete']" % doomed["org_id"])
    confirm_text = delete_form.get_attribute("data-confirm")
    assert "Default" not in confirm_text, "the confirmation still promises a move to Default: %r" % confirm_text
    assert "removed" in confirm_text, confirm_text

    delete_form.get_by_role("button", name="Delete organisation").click()
    page.get_by_role("button", name="Confirm", exact=True).click()
    page.wait_for_url("**/admin/organizations", timeout=PAGE_TIMEOUT)

    assert page.get_by_text('Organisation "%s" deleted.' % doomed["name"], exact=False).first.is_visible()
    assert page.get_by_role("link", name=doomed["name"]).count() == 0, "the deleted organisation is still listed"

    response = page.goto(live_server + "/admin/organizations/%d" % doomed["org_id"], wait_until="domcontentloaded")
    assert response.status == 404

    with app.app_context():
        assert db.session.get(Organization, doomed["org_id"]) is None
        assert db.session.get(User, doomed["member_id"]) is None, "the organisation's only member was not removed"
        db.session.remove()


def test_the_default_organisation_says_it_cannot_be_deleted_and_refuses(page, live_server, seeded, app):
    from app import db
    from app.models.organization import Organization

    with app.app_context():
        default_id = Organization.query.filter_by(slug="default").one().id
        db.session.remove()

    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/organizations/%d" % default_id, wait_until="domcontentloaded")
    reason = page.get_by_test_id("organisation-delete-refused")
    assert reason.is_visible(), "the Default organisation's page does not say why it cannot be deleted"
    assert reason.inner_text().strip() == "The Default organisation cannot be deleted."

    # A delete sent anyway (an old tab, a hand-made request) is refused with the same reason.
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.evaluate(
            """id => {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/admin/organizations/' + id + '/delete';
                const token = document.createElement('input');
                token.type = 'hidden';
                token.name = 'csrf_token';
                token.value = document.querySelector('meta[name=csrf-token]').content;
                form.appendChild(token);
                document.body.appendChild(form);
                form.submit();
            }""",
            default_id,
        )
    assert page.url.rstrip("/").endswith("/admin/organizations/%d" % default_id)
    # The refusal is said as well as the page's own statement: the reason appears twice.
    page.wait_for_function(
        "t => document.body.innerText.split(t).length - 1 >= 2",
        arg="The Default organisation cannot be deleted.",
        timeout=15000,
    )
    with app.app_context():
        assert db.session.get(Organization, default_id) is not None
        db.session.remove()
