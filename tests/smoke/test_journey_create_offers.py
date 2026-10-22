"""Browser journey: an enterprise architect creates a business case and a value
stream from the buttons the pages offer.

The person's goal is to record a business case and a value stream. Two things
used to go wrong: a read-only account carrying the Enterprise Architect label was
offered "New Business Case" and "New Value Stream", filled the form in, and was
refused; and opening the create address directly ended on a bare, unstyled
"Method Not Allowed" page with no way back.

What this holds the product to:

* every role the page offers the button to can use it, and lands on a page with
  the thing it just made;
* a role that may not create is not offered the button, and the page says who can;
* the "not a link" answer renders inside the app frame in plain words.
"""

import uuid

import pytest

from .conftest import PASSWORD
from .test_archetype_journeys import _login, _visit, page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def read_only_architect(seeded):
    """An Enterprise Architect on a read-only (Viewer) account: the shape of the demo organisation."""
    from app import create_app, db

    app = create_app("testing")
    email = "smoke.viewer-ea.%s@example.com" % uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.user import Role, User

        viewer = Role.query.filter_by(name="Viewer").one()
        user = User(
            email=email, first_name="Read", last_name="Only",
            organization_id=seeded["ids"]["org"], enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user.role = viewer
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


def _frame_present(page):
    """The app frame: sidebar navigation and the signed-in header, not a bare page."""
    return page.locator("aside, nav[aria-label], [data-testid='sidebar']").count() > 0


def test_enterprise_architect_creates_a_business_case_from_the_offered_button(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/business-case/")
    title = "Consolidate calibration records %s" % uuid.uuid4().hex[:5]

    page.locator("button:visible", has_text="New Business Case").first.click()
    page.fill("#bc-title", title)
    page.fill("#bc-description", "One record for certificates, dispatch and quality.")
    page.locator("[data-testid='bc-create-submit']").click()
    page.wait_for_url("**/business-case/*", timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    body = " ".join(page.inner_text("body").split())
    assert "Method Not Allowed" not in body and "don't have access" not in body.lower()
    assert title in body, "the business case page does not show the business case just made"

    _visit(page, live_server, "/business-case/")
    assert title in " ".join(page.inner_text("body").split()), "the list does not show it"
    page.get_by_text(title).first.click()
    page.wait_for_load_state("domcontentloaded")
    assert title in " ".join(page.inner_text("body").split()), "the list link does not land on it"


def test_enterprise_architect_creates_a_value_stream_from_the_offered_button(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/value-streams/")
    name = "Calibrate to certificate %s" % uuid.uuid4().hex[:5]

    page.locator("button:visible", has_text="New Value Stream").first.click()
    page.fill("#vs-name", name)
    page.fill("#vs-code", "C2C")
    page.select_option("#vs-type", "internal")
    page.fill("#vs-description", "From a sensor arriving to a certificate leaving.")
    page.locator("form:visible button[type=submit]").first.click()
    page.wait_for_url("**/value-streams/*", timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    body = " ".join(page.inner_text("body").split())
    assert "Method Not Allowed" not in body and "don't have access" not in body.lower()
    assert name in body, "the value stream page does not show the value stream just made"

    _visit(page, live_server, "/value-streams/")
    assert name in " ".join(page.inner_text("body").split())


@pytest.mark.parametrize("path,noun", [("/business-case/", "business case"), ("/value-streams/", "value stream")])
def test_read_only_architect_is_not_offered_what_would_be_refused(page, live_server, read_only_architect, path, noun):
    _login(page, live_server, read_only_architect)
    _visit(page, live_server, path)
    body = " ".join(page.inner_text("body").split())

    offered = page.locator("button:visible, a:visible", has_text="New %s" % noun.title()).count()
    offered += page.locator("button:visible, a:visible", has_text="Create %s" % noun.title()).count()
    assert offered == 0, "a read-only account is offered to create a %s, then refused" % noun
    assert "Architects and administrators" in body, "the page does not say who can create a %s" % noun


@pytest.mark.parametrize("path", ["/business-case/create", "/value-streams/create"])
def test_opening_a_create_address_as_a_link_is_plain_and_inside_the_app(page, live_server, seeded, path):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    response, _ = _visit(page, live_server, path)
    body = " ".join(page.inner_text("body").split())

    assert response.status == 405
    assert "Method Not Allowed" not in body, "the bare server page is showing"
    assert "Go back to the page and use the button there" in body, body[:300]
    assert _frame_present(page), "the page is not inside the app frame"
    assert page.locator("[data-testid='plain-error']").count() == 1
