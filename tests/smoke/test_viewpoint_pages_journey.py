"""An enterprise architect opens the Motivation viewpoint drawn from the live model.

From the viewpoints index the architect opens Motivation and sees the diagram drawn,
their own elements in the table (never another organisation's), the genome-fact column,
and the gaps the model has today. The page reads at 1440 and 390 wide, light and dark,
with no sideways scroll.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(tag):
    """Two organisations, each with a related Driver and Goal; a user in the first."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        orgs = []
        for label in ("mine", "theirs"):
            org = Organization(name=f"Viewpoint {label} {tag}", slug=f"viewpoint-{label}-{tag}")
            db.session.add(org)
            db.session.flush()
            driver = ArchiMateElement(name=f"Driver {label} {tag}", type="Driver", layer="Motivation",
                                      organization_id=org.id, description="Pressure on margin")
            goal = ArchiMateElement(name=f"Goal {label} {tag}", type="Goal", layer="Motivation",
                                    organization_id=org.id)
            db.session.add_all([driver, goal])
            db.session.flush()
            db.session.add(ArchiMateRelationship(source_id=driver.id, target_id=goal.id, type="Influence",
                                                 organization_id=org.id))
            orgs.append(org)
        email = f"smoke.viewpoints.{tag}@example.com"
        user = User(email=email, first_name="View", last_name="Points", organization_id=orgs[0].id,
                    enterprise_role="enterprise_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.is_platform_admin = False
        user.is_org_admin = False
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        db.session.remove()
    return email


def _no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_an_architect_reads_the_motivation_viewpoint(live_server, page, scheme):
    tag = uuid.uuid4().hex[:8]
    email = _seed(tag)
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": 1440, "height": 900})
    base = str(live_server)
    _login(page, base, email)

    page.goto(base + "/archimate/viewpoints/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("viewpoints-grid")).to_be_visible()
    page.locator('[data-testid="viewpoint-link"][data-viewpoint="motivation"]').click()
    page.wait_for_url("**/archimate/viewpoints/motivation", timeout=PAGE_TIMEOUT)

    expect(page.get_by_test_id("viewpoint-diagram")).to_have_attribute("data-drawn", "true", timeout=PAGE_TIMEOUT)
    table = page.get_by_test_id("viewpoint-elements")
    expect(table).to_contain_text(f"Driver mine {tag}")
    expect(table).to_contain_text(f"Goal mine {tag}")
    expect(page.locator("body")).not_to_contain_text(f"theirs {tag}")
    expect(page.get_by_test_id("viewpoint-gaps")).to_be_visible()
    # The goal has no description: that gap is stated with why and what next.
    expect(page.locator('[data-gap="no-description"]')).to_be_visible()
    expect(page.get_by_test_id("stat-relationships-value")).not_to_have_text("0")
    expect(page.get_by_test_id("viewpoint-provenance")).to_contain_text("Genome facts read")
    assert _no_sideways_scroll(page)

    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("viewpoint-elements")).to_be_visible()
    assert _no_sideways_scroll(page)
