"""An organisation administrator works the organisation's data subject requests and
reads its framework configuration.

Two real organisations, each with an administrator whose working persona is an
enterprise architect, a recorded access request scoped against linked systems,
and a framework configuration. The administrator of the first organisation opens
Data subject requests from All modules, opens their own request and sees the
systems to search, then opens Framework configuration from Organisation
administration, reads their own configuration and sees where the shared
catalogue's changes belong. Nothing of the second organisation appears anywhere along the way, and
every link followed lands on a page with content.

Played at 1440 wide in the light theme and 390 wide in the dark theme.
Screenshots go to the directory named by ORGADMIN_JOURNEY_SHOTS, or a
temporary directory.
"""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, admin_id, label):
    """One linked estate, a scoped access request and a configuration for the organisation."""
    from flask import g

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.framework_configuration import CapabilityFrameworkConfiguration
    from app.models.process_data import DataDomain, DataEntity
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.user import User
    from app.services.gdpr_service import GDPRService

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {}
    with app.test_request_context("/"):
        g.current_org_id = org_id
        domain = DataDomain(name="Domain %s %s" % (label, suffix), organization_id=org_id)
        db.session.add(domain)
        db.session.flush()
        customer = DataEntity(name="Customer %s %s" % (label, suffix), domain_id=domain.id,
                              contains_pii=True, organization_id=org_id)
        db.session.add(customer)
        db.session.flush()
        crm = ApplicationComponent(name="CRM %s %s" % (label, suffix), organization_id=org_id)
        db.session.add(crm)
        db.session.flush()
        db.session.add(ArchiMateRelationship(type="access", source_id=crm.archimate_element_id,
                                             target_id=customer.archimate_element_id, organization_id=org_id))
        db.session.flush()
        admin = db.session.get(User, admin_id)
        out["reference"] = "Subject %s %s" % (label, suffix)
        req = GDPRService.create_request(org_id, admin, "access", subject_reference=out["reference"])
        out["request_id"] = req.id
        out["system"] = crm.name
        out["config_name"] = "Framework %s %s" % (label, suffix)
        cfg = CapabilityFrameworkConfiguration(
            configuration_name=out["config_name"], configuration_code="J-%s-%s" % (label, suffix),
            organization_id=org_id, status="draft",
        )
        db.session.add(cfg)
        db.session.commit()
        out["config_id"] = cfg.id
        db.session.remove()
    return out


@pytest.fixture(scope="module")
def two_organisations():
    orgs = {}
    for label in ("Alpha", "Beta"):
        made = create_fresh_org("enterprise_architect", org_admin=True)
        email = made["emails"]["enterprise_architect"]
        user_id = made["user_ids"]["enterprise_architect"]
        orgs[label] = dict(made, email=email, **_seed(made["org_id"], user_id, label))
    return orgs


def _shot_dir(tmp_path):
    root = os.environ.get("ORGADMIN_JOURNEY_SHOTS")
    path = Path(root) if root else tmp_path
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize(
    "width,height,dark",
    [(1440, 900, False), (390, 844, True)],
    ids=["1440-light", "390-dark"],
)
def test_org_admin_works_their_own_requests_and_framework_configuration(
    browser, live_server, two_organisations, tmp_path, width, height, dark
):
    mine, theirs = two_organisations["Alpha"], two_organisations["Beta"]
    shots = _shot_dir(tmp_path)
    tag = "%d-%s" % (width, "dark" if dark else "light")

    context = browser.new_context(viewport={"width": width, "height": height},
                                  color_scheme="dark" if dark else "light")
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["email"])
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if dark:
            page.evaluate("() => document.documentElement.classList.add('dark')")

        # All modules lists Data subject requests; following it lands on this organisation's list.
        page.goto(live_server + "/modules/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.get_by_role("link", name="Data subject requests").first
        expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
        link.click()
        page.wait_for_url("**/compliance/data-subject-requests", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(mine["reference"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert theirs["reference"] not in page.content()
        assert "Access denied" not in page.content()
        page.screenshot(path=str(shots / ("requests-%s.png" % tag)))

        # Opening the request shows the systems to search, from this organisation's own model.
        page.get_by_test_id("dsr-row-%d" % mine["request_id"]).click()
        page.wait_for_url("**/data-subject-requests/%d" % mine["request_id"], timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("dsr-subject")).to_have_text(mine["reference"])
        expect(page.get_by_text(mine["system"]).first).to_be_visible()
        assert theirs["system"] not in page.content()
        assert page.get_by_test_id("dsr-scope-count").inner_text().strip() not in ("", "—", "0")
        page.screenshot(path=str(shots / ("request-detail-%s.png" % tag)))

        # The other organisation's request reads as not found.
        other = page.goto(live_server + "/compliance/data-subject-requests/%d" % theirs["request_id"],
                          wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert other.status == 404
        assert theirs["reference"] not in page.content()

        # Framework configuration: their own configuration, a note about the shared catalogue.
        page.goto(live_server + "/admin/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.get_by_role("link", name="Framework Configuration").first
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        card.click()
        page.wait_for_url("**/framework-config/", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(mine["config_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert theirs["config_name"] not in page.content()
        expect(page.get_by_test_id("framework-config-platform-note")).to_contain_text("platform administrator")
        assert page.get_by_test_id("framework-config-new").count() == 0
        page.screenshot(path=str(shots / ("framework-config-%s.png" % tag)))

        # Their configuration opens; the other organisation's does not.
        page.get_by_role("link", name=mine["config_name"]).first.click()
        page.wait_for_url("**/framework-config/configuration/%d" % mine["config_id"], timeout=PAGE_TIMEOUT)
        assert "could not be found" not in page.content()
        expect(page.locator("h1").first).to_be_visible()
        page.screenshot(path=str(shots / ("framework-configuration-%s.png" % tag)))
        gone = page.goto(live_server + "/framework-config/configuration/%d" % theirs["config_id"],
                         wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert gone.status == 404

        # The platform's own pages stay refused, with the standard page, not a blank.
        refused = page.goto(live_server + "/framework-config/templates", wait_until="domcontentloaded",
                            timeout=PAGE_TIMEOUT)
        assert refused.status == 403
    finally:
        context.close()
