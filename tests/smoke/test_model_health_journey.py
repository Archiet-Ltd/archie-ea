"""Model health uses the real rescan control and persists stored results."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_TIMESTAMP_RE = re.compile(
    r"Last computed:\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} UTC)"
)


def _seed_model_health_fixture(app, enterprise_role="solution_architect"):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.drift_report import DriftReport
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        db.session.remove()
        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()

        org = Organization(
            name=f"Smoke Model Health {suffix}",
            slug=f"smoke-model-health-{suffix}",
        )
        db.session.add(org)
        db.session.flush()
        organization_id = org.id
        set_contract_plan(org, "enterprise", None)
        db.session.commit()

        user = User(
            email=f"smoke.model-health.{suffix}@example.com",
            first_name="Smoke",
            last_name="Health",
            organization_id=org.id,
            enterprise_role=enterprise_role,
            confirmed=True,
        )
        user.role = architect_role
        user.password = PASSWORD
        db.session.add(user)

        orphan = ArchiMateElement(
            name=f"Detached Platform {suffix}",
            type="ApplicationComponent",
            layer="application",
            organization_id=org.id,
        )
        db.session.add(orphan)
        db.session.commit()

        # Seed a stored drift report so the page renders findings immediately
        # rather than showing the pending state (the detector is scheduled, not
        # run synchronously on page load).
        from app.modules.genome.services.drift_detector import detect_model_drift

        report = detect_model_drift(org.id)
        DriftReport.upsert(org.id, report)
        db.session.commit()

        email = user.email
        orphan_name = orphan.name
        db.session.remove()

        return {
            "email": email,
            "organization_id": organization_id,
            "orphan_name": orphan_name,
        }


def _stored_timestamp(app, organization_id):
    from app import db
    from app.models.drift_report import DriftReport

    with app.app_context():
        db.session.remove()
        stored = DriftReport.for_org(organization_id)
        assert stored is not None
        timestamp = stored.computed_at.strftime("%Y-%m-%d %H:%M UTC")
        finding_total = stored.report_json["summary"]["total"]
        db.session.remove()
        return timestamp, finding_total


def _page_timestamp(page):
    body_text = page.locator("body").inner_text()
    match = _TIMESTAMP_RE.search(body_text)
    assert match, f"page did not render a stored timestamp: {body_text[:500]}"
    return match.group(1)


def test_model_health_rescan_persists_timestamp_and_findings(browser, live_server, app):
    seeded = _seed_model_health_fixture(app)

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, seeded["email"])

        response = page.goto(
            live_server + "/genome/model-health/",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        assert response.status == 200
        expect(page.get_by_role("heading", name="Model Health", exact=True)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
        # A stored report was seeded in the fixture, so the orphan element is
        # visible immediately — no synchronous detector run on page load.
        expect(page.locator("body")).to_contain_text(
            seeded["orphan_name"], timeout=PAGE_TIMEOUT
        )

        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT) as navigation:
            page.get_by_role("button", name="Re-scan for drift", exact=True).click()
        assert navigation.value.status == 200

        expect(page.get_by_text("Model-health scan completed", exact=False)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
        expect(page.locator("body")).to_contain_text(
            seeded["orphan_name"], timeout=PAGE_TIMEOUT
        )

        stored_timestamp, finding_total = _stored_timestamp(app, seeded["organization_id"])
        assert finding_total == 1
        assert _page_timestamp(page) == stored_timestamp

        reload_response = page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert reload_response.status == 200
        expect(page.locator("body")).to_contain_text(
            seeded["orphan_name"], timeout=PAGE_TIMEOUT
        )
        assert _page_timestamp(page) == stored_timestamp
    finally:
        context.close()


def test_enterprise_architect_dismisses_a_finding_with_a_reason(browser, live_server, app):
    seeded = _seed_model_health_fixture(app, enterprise_role="enterprise_architect")

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, seeded["email"])
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # reached from the sidebar, not by typing the address
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            page.get_by_role("link", name="Model Health", exact=True).first.click()
        expect(page).to_have_url(re.compile(r"/genome/model-health/?$"), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(seeded["orphan_name"], timeout=PAGE_TIMEOUT)

        reason = page.get_by_label("Reason for dismissing this finding").first
        reason.fill("Kept as an archive copy")
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            page.get_by_role("button", name="Dismiss", exact=True).first.click()

        for _ in range(2):
            section = page.locator("#dismissed-findings")
            expect(section).to_contain_text("Kept as an archive copy", timeout=PAGE_TIMEOUT)
            expect(page.locator(".genome-drift-finding")).to_have_count(0)
            page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            page.get_by_role("button", name="Restore to open list").click()
        expect(page.locator(".genome-drift-finding")).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(page.locator("#dismissed-findings")).to_have_count(0)
    finally:
        context.close()
