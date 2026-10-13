"""R1-06 / SDD 13 R1 tests 3, 4, 5: preview, lead search and confirm.

As enterprise_architect: a typed journey's preview shows the plan; a missing
lead is refused naming the workstream; a full confirm creates the structure,
which is still there on a fresh load; a second confirm changes no count.
As solution_architect (journey member, not a programme creator): the preview
shows the plan with no confirm control, and a direct POST is 403.
Also the security.md 10.1 authorisation row for confirm over all eleven
archetypes -- deliberately NOT in POLICY (which asserts platform_admin passes
every entry).
"""

import uuid

import pytest

from .conftest import ARCHETYPES, PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

from .test_archetype_journeys import _login, _visit, page  # noqa: F401

CONFIRM_ALLOWED = {"enterprise_architect", "cto"}


@pytest.fixture
def typed_journey(seeded):
    from app import create_app, db
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.architecture_journey_link import ArchitectureJourneyMember
    from app.models.user import User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        journey = ArchitectureJourney(
            owner_id=owner.id, organization_id=org_id, title="Structure smoke %s" % suffix,
            intent="portfolio_change", selected_layers=["motivation"], programme_type="s4hana",
            journey_state={}, current_stage="frame", status="active",
        )
        db.session.add(journey)
        db.session.flush()
        for archetype in ARCHETYPES:
            if archetype == "enterprise_architect":
                continue
            user = User.query.filter_by(email=seeded["emails"][archetype]).one()
            db.session.add(ArchitectureJourneyMember(
                journey_id=journey.id, user_id=user.id, organization_id=org_id, role="contributor"))
        db.session.commit()
        journey_id = journey.id
    yield {"journey_id": journey_id, "org_id": org_id}


def _counts(org_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        q = lambda sql: db.session.execute(db.text(sql), {"o": org_id}).scalar()  # noqa: E731
        return (
            q("SELECT count(*) FROM strategic_initiatives WHERE organization_id=:o "
              "AND record_kind='transformation_programme' AND name LIKE 'Smoke S4%'"),
            q("SELECT count(*) FROM programme_workstreams WHERE organization_id=:o AND template_key LIKE 's4hana.%'"),
        )


def _url(ids):
    return "/architecture-journey/work/%s/programme-structure" % ids["journey_id"]


def _pick(page, testid):
    box = page.locator('[data-testid="%s"]' % testid)
    box.locator("input[type=search]").fill("Smoke")
    option = box.locator('[role="option"]').first
    option.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    option.click()


def _fill_common(page):
    page.fill('input[name="name"]', "Smoke S4 programme")
    page.fill('textarea[name="objective"]', "Move core finance onto the new platform.")
    page.fill('input[name="target_date_unavailable_reason"]', "Not yet scheduled")
    page.fill('input[name="outcome_statement"]', "Close the books faster")
    page.fill('input[name="metric_name"]', "Days to close")
    page.fill('input[name="unit"]', "days")
    page.fill('input[name="target_value"]', "5")
    page.fill('input[name="unavailable_reason"]', "Not yet measured")
    _pick(page, "owner-picker")


def _lead_testids(page):
    return [
        el.get_attribute("data-testid")
        for el in page.locator('[data-testid^="lead-picker-"]').all()
    ]


def test_enterprise_architect_confirms_the_structure(page, live_server, seeded, typed_journey):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, _url(typed_journey))
    assert page.locator('[data-testid="structure-preview"]').count() == 1
    assert page.locator('[data-testid="structure-form"]').count() == 1
    before = _counts(typed_journey["org_id"])

    _fill_common(page)
    testids = _lead_testids(page)
    assert len(testids) >= 2
    # leave the last workstream's lead empty: refused, naming it, nothing created
    for testid in testids[:-1]:
        _pick(page, testid)
    page.locator('[data-testid="structure-confirm"]').click(force=True)
    page.wait_for_selector('[data-testid="structure-error"]', timeout=PAGE_TIMEOUT)
    assert "Choose a lead for the workstream" in page.locator('[data-testid="structure-error"]').inner_text()
    assert _counts(typed_journey["org_id"]) == before

    # the re-rendered form is fresh: fill everything, confirm
    _fill_common(page)
    for testid in _lead_testids(page):
        _pick(page, testid)
    page.locator('[data-testid="structure-confirm"]').click(force=True)
    page.wait_for_url("**/architecture-journey/work/%s" % typed_journey["journey_id"], timeout=PAGE_TIMEOUT)

    _visit(page, live_server, _url(typed_journey))
    created = page.locator('[data-testid="structure-created"]')
    assert created.count() == 1
    text = created.inner_text()
    assert "Finance and controlling" in text
    assert "Business case and value drivers" in text
    after = _counts(typed_journey["org_id"])
    assert after[0] == before[0] + 1 and after[1] > before[1]

    # a second confirm (direct POST, any key) changes no count
    token = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
    page.request.post(live_server + _url(typed_journey), headers={"X-CSRFToken": token},
                      form={"csrf_token": token, "command_key": uuid.uuid4().hex})
    assert _counts(typed_journey["org_id"]) == after


def test_solution_architect_sees_the_preview_but_cannot_confirm(page, live_server, seeded, typed_journey):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _visit(page, live_server, _url(typed_journey))
    assert page.locator('[data-testid="structure-preview"]').count() == 1
    assert page.locator('[data-testid="structure-form"]').count() == 0
    assert page.locator('[data-testid="structure-no-create"]').count() == 1
    token = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
    response = page.request.post(live_server + _url(typed_journey), headers={"X-CSRFToken": token},
                                 form={"csrf_token": token, "command_key": uuid.uuid4().hex})
    assert response.status == 403


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_programme_structure_confirm_authorisation(archetype, page, live_server, seeded, typed_journey):
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/architecture-journey/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    token = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
    response = page.request.post(live_server + _url(typed_journey), headers={"X-CSRFToken": token},
                                 form={"csrf_token": token, "command_key": uuid.uuid4().hex},
                                 max_redirects=0)
    if archetype in CONFIRM_ALLOWED:
        # an empty form is refused for content (400), never for who they are
        assert response.status not in (401, 403), (archetype, response.status)
    else:
        assert response.status in (401, 403, 404), (archetype, response.status)
