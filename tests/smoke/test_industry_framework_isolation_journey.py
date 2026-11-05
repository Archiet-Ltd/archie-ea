"""An organisation sees the shared industry processes and its own, never another organisation's.

Two organisations open the same industry framework. Each sees the shared process and its own two, and none of the
other's, on the framework screen and through the framework's process API. Seeing its own rows and the shared row is
asserted in the same test, so a fence that hides too much fails as well.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
expect.set_options(timeout=PAGE_TIMEOUT)


def _seed_framework(org_a, org_b):
    from app import create_app, db
    from app.models.industry_apqc import IndustryAPQCFramework, IndustryAPQCProcess

    code = "ISO" + uuid.uuid4().hex[:6].upper()
    with create_app("testing").app_context():
        fw = IndustryAPQCFramework(industry_code=code, industry_name="Isolation Walk Industry", is_active=True)
        db.session.add(fw)
        db.session.flush()
        for number, owner, label in (
            ("1.0", None, "Shared category"),
            ("1.1", org_a, "Alpha own process"),
            ("1.2", org_a, "Alpha own process"),
            ("1.3", org_b, "Bravo private process"),
            ("1.4", org_b, "Bravo private process"),
        ):
            db.session.add(IndustryAPQCProcess(
                industry_framework_id=fw.id, industry_process_code=number,
                industry_process_name=f"{label} {number}", is_active=True, organization_id=owner,
            ))
        db.session.commit()
    return code


SHARED = ["Shared category 1.0"]
ALPHA = ["Alpha own process 1.1", "Alpha own process 1.2"]
BRAVO = ["Bravo private process 1.3", "Bravo private process 1.4"]


def _remove_framework(code):
    from app import create_app, db
    from app.models.industry_apqc import IndustryAPQCFramework, IndustryAPQCProcess

    with create_app("testing").app_context():
        fw = IndustryAPQCFramework.query.filter_by(industry_code=code).one()
        IndustryAPQCProcess.query.filter_by(industry_framework_id=fw.id).delete()
        db.session.delete(fw)
        db.session.commit()


def _visit(browser, live_server, email, path):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    sign_in(page, live_server, email)
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return context, page


def test_each_organisation_sees_the_shared_rows_and_its_own_only(browser, live_server):
    a = create_fresh_org("enterprise_architect")
    b = create_fresh_org("enterprise_architect")
    code = _seed_framework(a["org_id"], b["org_id"])
    try:
        _check(browser, live_server, a, b, code)
    finally:
        _remove_framework(code)


def _check(browser, live_server, a, b, code):
    for me, mine, theirs in ((a, ALPHA, BRAVO), (b, BRAVO, ALPHA)):
        context, page = _visit(browser, live_server, me["emails"]["enterprise_architect"],
                               f"/industry-apqc/framework/{code}")
        try:
            body = page.locator("body")
            for name in SHARED + mine:
                expect(body).to_contain_text(name)
            for name in theirs:
                expect(body).not_to_contain_text(name)
            status, payload = api(page, "GET", f"/industry-apqc/api/frameworks/{code}/processes")
            assert status == 200, (status, payload)
            names = sorted(p["industry_process_name"] for p in payload["processes"])
            assert names == sorted(SHARED + mine), names
            assert payload["total"] == 3, payload["total"]
        finally:
            context.close()
