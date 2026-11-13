"""A solution architect without spend authority adds a system and opens it; no cost was recorded for it.

The people: an Administrator, and a solution architect who may create and edit applications but
holds no spend authority. The goal: the system the architect adds is in the application list and on
its own page, carries no cost, and the Administrator of the same organisation can still record one.
A second organisation holds a system of its own; the architect's enrichment request for it finds
nothing and leaves its cost as it was.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org, name, cost=None):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import set_annual_cost

    with create_app("testing").app_context():
        row = ApplicationComponent(name=name, organization_id=org["org_id"], lifecycle_status="operational")
        db.session.add(row)
        db.session.flush()
        if cost is not None:
            set_annual_cost(row, cost)
        db.session.commit()
        app_id = row.id
        db.session.remove()
        return app_id


def _recorded_cost(app_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = db.session.get(ApplicationComponent, app_id)
        value = row.total_cost_of_ownership
        db.session.remove()
        return value


@pytest.fixture(scope="module")
def estate(live_server):
    tag = uuid.uuid4().hex[:6]
    org = create_fresh_org("platform_admin", org_admin=True, extra_roles=("solution_architect",))
    other = create_fresh_org("platform_admin", org_admin=True)
    return {
        "tag": tag,
        "admin": org["emails"]["platform_admin"],
        "limited": org["emails"]["solution_architect"],
        "mine_name": "Mine %s" % tag,
        "mine": _seed(org, "Mine %s" % tag),
        "rival_name": "Rival %s" % tag,
        "rival": _seed(other, "Rival %s" % tag, cost=2500),
    }


def _person(browser, live_server, email):
    narrow = os.environ.get("WALK_MODE") == "narrow-dark"
    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 390, "height": 900} if narrow else {"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


def _walk(page, name):
    folder = os.environ.get("WALK_DIR")
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name)),
                    full_page=True)


def _open(page, live_server, path):
    return page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def test_a_system_added_without_spend_authority_has_no_cost_and_stays_in_its_organisation(
        browser, live_server, estate):
    context, page = _person(browser, live_server, estate["limited"])
    try:
        _open(page, live_server, "/applications/")
        added = "Added %s" % estate["tag"]
        # The add-system request a person's browser sends, with a cost typed in by hand.
        csrf = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
        reply = context.request.post(
            live_server + "/applications/create",
            form={"name": added, "total_cost_of_ownership": "91000", "csrf_token": csrf},
            headers={"X-CSRFToken": csrf},
            max_redirects=0,
        )
        assert reply.status in (200, 302), reply.status

        _open(page, live_server, "/applications/")
        link = page.get_by_role("link", name=added).first
        expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
        link.click()
        expect(page.get_by_role("heading", name=added).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "91,000" not in page.content() and "91000" not in page.content()
        _walk(page, "added-without-spend")

        from app import create_app
        from app.models.application_portfolio import ApplicationComponent

        with create_app("testing").app_context():
            row = ApplicationComponent.query.filter_by(name=added).first()
            assert row is not None and row.total_cost_of_ownership is None
            added_id = row.id
        assert _recorded_cost(added_id) is None

        # Enrichment of the other organisation's system finds nothing and changes nothing.
        response = context.request.post(
            live_server + "/applications/rationalization/api/enrich/%d" % estate["rival"],
            data={"total_cost_of_ownership": 1}, headers={"X-CSRFToken": csrf})
        assert response.status == 404
        assert float(_recorded_cost(estate["rival"])) == 2500.0
        # And on its own system the architect's cost is refused in plain words, the rest saved.
        response = context.request.post(
            live_server + "/applications/rationalization/api/enrich/%d" % estate["mine"],
            data={"total_cost_of_ownership": 5, "business_criticality": "High"}, headers={"X-CSRFToken": csrf})
        assert response.status == 200
        assert "cost was not saved" in response.json()["cost_not_saved"]
        assert _recorded_cost(estate["mine"]) is None
    finally:
        context.close()


def test_the_administrator_can_still_record_a_cost(browser, live_server, estate):
    context, page = _person(browser, live_server, estate["admin"])
    try:
        _open(page, live_server, "/applications/")
        csrf = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
        response = context.request.post(
            live_server + "/applications/rationalization/api/enrich/%d" % estate["mine"],
            data={"total_cost_of_ownership": 4200}, headers={"X-CSRFToken": csrf})
        assert response.status == 200, response.text()[:200]
        assert float(_recorded_cost(estate["mine"])) == 4200.0
        _open(page, live_server, "/applications/%d" % estate["mine"])
        expect(page.get_by_role("heading", name=estate["mine_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _walk(page, "administrator-cost")
    finally:
        context.close()
