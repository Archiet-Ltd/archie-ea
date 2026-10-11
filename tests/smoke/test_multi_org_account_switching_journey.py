"""Browser journey for multi-organisation account switching."""

from __future__ import annotations

import json
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit", force=True)
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def second_org_membership(seeded):
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    second_org_name = "Smoke second org %s" % suffix
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.organization import Organization
        from app.models.pending_invitation import PendingInvitation
        from app.models.user import User

        user = User.query.filter_by(email=seeded["emails"]["platform_admin"]).one()
        org = Organization(name=second_org_name, slug="smoke-second-%s" % suffix)
        db.session.add(org)
        db.session.flush()

        invitation, _ = PendingInvitation.create_for(
            org.id, user.id, "architect", invited_by_id=user.id
        )
        token = invitation.issue_link()
        db.session.add(
            ApplicationComponent(
                name="Org B smoke app %s" % suffix,
                organization_id=org.id,
                lifecycle_status="operational",
            )
        )
        db.session.commit()

        return {
            "org_id": org.id,
            "org_name": second_org_name,
            "token": token,
        }


def test_platform_admin_accepts_second_org_switches_and_sees_only_that_org_data(
    page, live_server, seeded, second_org_membership
):
    _login(page, live_server, seeded["emails"]["platform_admin"])

    page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.goto(
        live_server + "/account/join/%s" % second_org_membership["token"],
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    page.locator('[data-join-existing] button', has_text="Accept invitation").click()
    page.wait_for_url("**/dashboard/overview**", timeout=PAGE_TIMEOUT)

    page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    memberships = page.locator('[data-testid="organization-memberships-card"]')
    assert memberships.count() == 1
    assert memberships.get_by_text(second_org_membership["org_name"], exact=True).count() == 1

    page.locator(
        'input[name="organization_id"][value="%s"]' % second_org_membership["org_id"]
    ).check(force=True)
    page.get_by_role("button", name="Switch organisation").click()
    page.wait_for_url("**/account/manage**", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    chip = page.locator('[data-testid="active-organization-chip"]')
    expect_text = second_org_membership["org_name"]
    assert chip.count() == 1
    assert expect_text in chip.inner_text()
    assert "Active: %s" % expect_text in page.content()

    created_name = "AAAA smoke switched app %s" % uuid.uuid4().hex[:8]
    created = page.request.post(
        live_server + "/api/applications/",
        data=json.dumps({"name": created_name}),
        headers={"Content-Type": "application/json"},
    )
    assert created.status == 201, created.text()

    page.goto(
        live_server + "/applications/?search=%s" % created_name,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    assert created_name in page.content()

    page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    home_radio = page.locator('input[name="organization_id"]:checked')
    if home_radio.input_value() != str(seeded["ids"]["org"]):
        page.locator(
            'input[name="organization_id"][value="%s"]' % seeded["ids"]["org"]
        ).check(force=True)
    page.get_by_role("button", name="Switch organisation").click()
    page.wait_for_url("**/account/manage**", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    chip = page.locator('[data-testid="active-organization-chip"]')
    assert second_org_membership["org_name"] not in chip.inner_text()

    page.goto(
        live_server + "/applications/?search=%s" % created_name,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    body_text = page.locator("body").inner_text()
    assert created_name not in body_text
