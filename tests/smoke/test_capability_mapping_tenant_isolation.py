"""Two-org browser check for the ApplicationCapabilityCoverage tenant leak.

Closes the live cross-tenant read/write leak on `application_capability_coverage`
(docs/buckets/capability-coverage-tenant-leak/tasks/01-application-capability-coverage.md):
ApplicationCapabilityCoverage gained TenantMixin, and this drives the real
`/enterprise/capability-map/mapping` screen as two different organisations'
users to prove neither can see, nor set up a mapping dialog to see, the
other's coverage rows -- and that a mapping saved by one org persists after
reload and is still invisible to the other after a fresh login.

No interception or handler doubles: real login, real click, real reload,
per this repo's "Done means DEMONSTRATED" standard.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, _require_explicit_test_database
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MAPPING_URL = "/enterprise/capability-map/mapping"


@pytest.fixture
def coverage_test_database():
    _require_explicit_test_database(dict(os.environ))


@pytest.fixture
def two_org_capability_fixture(coverage_test_database, app, seeded):
    """A second, wholly separate organisation with its own capability, app
    and editor user, alongside org A's equivalents from the shared `seeded`
    fixture -- so each org has something real to leak.
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:10]
    org_a_id = seeded["ids"]["org"]
    org_a_email = seeded["emails"]["enterprise_architect"]

    ids = {"app": [], "cap": [], "org": [], "user": []}

    with app.app_context():
        db.session.remove()
        architect_role = Role.query.filter_by(name="Architect").one()

        org_b = Organization(name=f"Tenant-leak org B {suffix}", slug=f"tenant-leak-b-{suffix}")
        db.session.add(org_b)
        db.session.flush()
        ids["org"].append(org_b.id)

        user_b = User(
            email=f"tenant-leak-b-{suffix}@example.com",
            first_name="Tenant",
            last_name="LeakB",
            organization_id=org_b.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user_b.role = architect_role
        user_b.password = PASSWORD
        db.session.add(user_b)
        db.session.flush()
        ids["user"].append(user_b.id)

        app_a = ApplicationComponent(name=f"Org A app {suffix}", organization_id=org_a_id)
        cap_a = BusinessCapability(
            name=f"Org A capability {suffix}", code=f"TL-A-{suffix}", organization_id=org_a_id
        )
        app_b = ApplicationComponent(name=f"Org B app {suffix}", organization_id=org_b.id)
        cap_b = BusinessCapability(
            name=f"Org B capability {suffix}", code=f"TL-B-{suffix}", organization_id=org_b.id
        )
        db.session.add_all((app_a, cap_a, app_b, cap_b))
        db.session.flush()
        ids["app"].extend([app_a.id, app_b.id])
        ids["cap"].extend([cap_a.id, cap_b.id])
        db.session.commit()

        org_b_id, app_a_id, cap_a_id, app_b_id, cap_b_id = (
            org_b.id, app_a.id, cap_a.id, app_b.id, cap_b.id,
        )
        org_b_email = user_b.email
        db.session.remove()

    try:
        yield {
            "org_a_id": org_a_id,
            "org_a_email": org_a_email,
            "org_b_id": org_b_id,
            "org_b_email": org_b_email,
            "app_a_id": app_a_id,
            "cap_a_id": cap_a_id,
            "cap_a_name": f"Org A capability {suffix}",
            "app_b_id": app_b_id,
            "cap_b_id": cap_b_id,
            "cap_b_name": f"Org B capability {suffix}",
            "app_b_name": f"Org B app {suffix}",
        }
    finally:
        with app.app_context():
            db.session.remove()
            from app.models.business_capabilities import ApplicationCapabilityCoverage

            if ids["cap"]:
                ApplicationCapabilityCoverage.query.filter(
                    ApplicationCapabilityCoverage.capability_id.in_(ids["cap"])
                ).delete(synchronize_session=False)
            if ids["app"]:
                # A legacy duplicate mapping table (app/models/... -- see
                # ADR 0008 "one system of record") carries its own FK to
                # application_components with no cascade, so the real UI
                # write path this test exercises can leave a row there too.
                db.session.execute(
                    db.text(
                        "DELETE FROM application_capability_mapping "
                        "WHERE application_component_id = ANY(:ids)"
                    ),
                    {"ids": ids["app"]},
                )
                ApplicationComponent.query.filter(ApplicationComponent.id.in_(ids["app"])).delete(
                    synchronize_session=False
                )
            if ids["cap"]:
                BusinessCapability.query.filter(BusinessCapability.id.in_(ids["cap"])).delete(
                    synchronize_session=False
                )
            if ids["user"]:
                # soc2_audit_log rows the login itself generates carry their
                # own FK to users with no cascade.
                db.session.execute(
                    db.text("DELETE FROM soc2_audit_log WHERE user_id = ANY(:ids)"),
                    {"ids": ids["user"]},
                )
                User.query.filter(User.id.in_(ids["user"])).delete(synchronize_session=False)
            if ids["org"]:
                Organization.query.filter(Organization.id.in_(ids["org"])).delete(
                    synchronize_session=False
                )
            db.session.commit()
            db.session.remove()


def test_capability_mapping_screen_is_isolated_per_organization(
    browser, live_server, seeded, two_org_capability_fixture
):
    """Org A creates a mapping through the real dialog; org B never sees it,
    on the mapping screen or in its own map-dialog checkbox list, and the
    mapping persists for org A across a reload.
    """
    fx = two_org_capability_fixture
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    errors = []
    page = context.new_page()
    page.on(
        "response",
        lambda response: errors.append(f"HTTP {response.status}: {response.url}")
        if response.status >= 500
        else None,
    )
    try:
        # --- Org A: create the mapping through the real dialog ---------------
        _login(page, live_server, fx["org_a_email"])
        assert page.goto(live_server + MAPPING_URL, wait_until="domcontentloaded").status == 200

        cap_row = page.locator(f'.cap-row[data-cap-id="{fx["cap_a_id"]}"]')
        expect(cap_row).to_be_visible()
        cap_row.get_by_role("button", name="Map applications").click()
        dialog = page.locator("#map-dialog")
        expect(dialog).to_be_visible()
        checkbox = dialog.locator(f'.map-app-checkbox[value="{fx["app_a_id"]}"]')
        expect(checkbox).to_be_visible()
        checkbox.check()
        # saveMappings() itself triggers window.location.reload() ~400ms after
        # a successful save; wait for THAT reload's navigation event rather
        # than a fixed sleep (which either races the reload on a slow run or
        # burns 800ms unnecessarily on a fast one) or issuing a second
        # explicit reload, which would race the in-flight navigation and
        # abort it.
        with page.expect_response(
            lambda r: "/capability-map/api/mappings" in r.url and r.request.method == "POST"
        ) as saved, page.expect_navigation(
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT
        ):
            page.locator("#map-save").click()
        assert saved.value.status == 200, saved.value.text()
        cap_row = page.locator(f'.cap-row[data-cap-id="{fx["cap_a_id"]}"]')
        expect(cap_row).to_contain_text("Org A app")
        assert fx["app_b_name"] not in page.content(), (
            "TENANT LEAK: org A's mapping page rendered org B's application name."
        )
        assert fx["cap_b_name"] not in page.content(), (
            "TENANT LEAK: org A's mapping page rendered org B's capability name."
        )
        context.close()

        # --- Org B: must not see org A's capability, application, or mapping --
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.set_default_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        page.on(
            "response",
            lambda response: errors.append(f"HTTP {response.status}: {response.url}")
            if response.status >= 500
            else None,
        )
        _login(page, live_server, fx["org_b_email"])
        assert page.goto(live_server + MAPPING_URL, wait_until="domcontentloaded").status == 200

        assert fx["cap_a_id"] and page.locator(
            f'.cap-row[data-cap-id="{fx["cap_a_id"]}"]'
        ).count() == 0, "TENANT LEAK: org B's mapping page rendered org A's capability row."
        page_content = page.content()
        assert "Org A capability" not in page_content, (
            "TENANT LEAK: org A's capability name appeared on org B's mapping page."
        )
        assert "Org A app" not in page_content, (
            "TENANT LEAK: org A's application name appeared on org B's mapping page."
        )

        # Org B's own capability's map-dialog application checkbox list must
        # not offer org A's application as something to map.
        cap_b_row = page.locator(f'.cap-row[data-cap-id="{fx["cap_b_id"]}"]')
        expect(cap_b_row).to_be_visible()
        cap_b_row.get_by_role("button", name="Map applications").click()
        dialog = page.locator("#map-dialog")
        expect(dialog).to_be_visible()
        assert dialog.locator(f'.map-app-checkbox[value="{fx["app_a_id"]}"]').count() == 0, (
            "TENANT LEAK: org B's map dialog offered org A's application as mappable."
        )

        assert errors == [], errors
    finally:
        context.close()
