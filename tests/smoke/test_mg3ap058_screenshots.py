"""Application manager roadmap screenshot capture.

Seeds a fresh org with an application and three work packages on the timeline,
signs in as the application_manager persona, asserts the role, and captures
the roadmap page at both viewports in light and dark colour schemes.

Screenshots are saved to /home/ubuntu/verify/seats/mg3ap058b/walk/.
"""

import datetime
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SCREENSHOT_DIR = "/home/ubuntu/verify/seats/mg3ap058b/walk"


def _assert_application_manager_role(email):
    """Assert the signed-in user is an application_manager, not a platform admin."""
    from app import create_app
    from app.models.user import User
    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "application_manager", (
            "expected application_manager, got %s" % user.enterprise_role
        )
        assert not user.is_platform_admin, "application_manager must not be platform admin"


def _seed_screenshot_org():
    """Seed a fresh org with one application and three work packages."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.implementation_migration import WorkPackage
    suffix = uuid.uuid4().hex[:8]
    app = create_app("testing")
    with app.app_context():
        org_data = create_fresh_org("application_manager", org_admin=True)
        org_id = org_data["org_id"]
        email = org_data["emails"]["application_manager"]
        user_id = org_data["user_ids"]["application_manager"]

        component = ApplicationComponent(
            name="Demo App %s" % suffix,
            organization_id=org_id,
            lifecycle_status="operational",
        )
        db.session.add(component)
        db.session.commit()

        packages = [
            WorkPackage(
                name="CRM modernisation",
                description="Migrate legacy CRM to cloud-native platform",
                application_component_id=component.id,
                owner_id=user_id, organization_id=org_id,
                status="in_progress", priority="high",
                start_date=datetime.date(2026, 6, 1),
                target_date=datetime.date(2027, 3, 31),
                element_type="Migration", togaf_phase="Implementation",
                sequence_order=1,
            ),
            WorkPackage(
                name="API gateway deployment",
                description="Deploy centralised API management",
                application_component_id=component.id,
                owner_id=user_id, organization_id=org_id,
                status="planned", priority="critical",
                start_date=datetime.date(2027, 1, 1),
                target_date=datetime.date(2027, 6, 30),
                element_type="Integration", togaf_phase="Implementation",
                sequence_order=2,
            ),
            WorkPackage(
                name="Data warehouse migration",
                description="Move analytics to Snowflake",
                application_component_id=component.id,
                owner_id=user_id, organization_id=org_id,
                status="completed", priority="medium",
                start_date=datetime.date(2026, 1, 1),
                target_date=datetime.date(2026, 5, 31),
                element_type="Migration", togaf_phase="Implementation",
                sequence_order=3,
            ),
        ]
        for wp in packages:
            db.session.add(wp)
        db.session.commit()
        app_id = component.id
        db.session.remove()
    return {"email": email, "org_id": org_id, "app_id": app_id, "suffix": suffix}


@pytest.fixture(scope="module")
def seeded(request):
    return _seed_screenshot_org()


def test_screenshot_roadmap(page, live_server, seeded):
    """Sign in as application manager, open roadmap, and take screenshots."""
    email = seeded["email"]
    _assert_application_manager_role(email)

    sign_in(page, live_server, email)

    roadmap_url = live_server + "/applications/%d/roadmap" % seeded["app_id"]
    page.goto(roadmap_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("h1")).to_be_visible(timeout=PAGE_TIMEOUT)

    # 1440 light
    page.emulate_media(color_scheme="light")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "roadmap-1440-light.png"), full_page=True)

    # 390 light
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "roadmap-390-light.png"), full_page=True)

    # 1440 dark
    page.emulate_media(color_scheme="dark")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "roadmap-1440-dark.png"), full_page=True)

    # 390 dark
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, "roadmap-390-dark.png"), full_page=True)
