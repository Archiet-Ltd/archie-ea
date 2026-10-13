"""R1-07 / SDD 13 R1 test 7 (extended): completing a deliverable with nothing in
the model needs a reason; crediting an existing element and creating a new one
raise the "N of M in the model" count, and every change is still there after a
reload. Plus the authorisation rows for the credit and complete routes
(security.md 10.1), kept out of POLICY.
"""

import json
import uuid

import pytest

from .conftest import ARCHETYPES, PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

from .test_archetype_journeys import _login, _visit, page  # noqa: F401

# business_architect is the seeded lead of every workstream in this fixture, so
# it holds workstream_lead (a LINK_ROLES member) by assignment -- allowed by
# design; every other archetype has neither persona nor assignment.
ALLOWED = {"enterprise_architect", "cto", "business_architect"}


@pytest.fixture
def structured_journey(seeded):
    from app import create_app, db
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.modules.transformation_room.domain import ActorContext
    from app.modules.transformation_room.programme_service import TransformationProgrammeService
    from app.modules.transformation_room.programme_types.loader import ProgrammeTypeCatalogue

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        ea = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        lead = User.query.filter_by(email=seeded["emails"]["business_architect"]).one()
        journey = ArchitectureJourney(
            owner_id=ea.id, organization_id=org_id, title="Credit smoke %s" % suffix,
            intent="portfolio_change", selected_layers=["motivation"], programme_type="s4hana",
            journey_state={}, current_stage="frame", status="active",
        )
        db.session.add(journey)
        db.session.flush()
        from app.models.architecture_journey_link import ArchitectureJourneyMember

        for archetype in ARCHETYPES:
            if archetype == "enterprise_architect":
                continue
            member = User.query.filter_by(email=seeded["emails"][archetype]).one()
            db.session.add(ArchitectureJourneyMember(
                journey_id=journey.id, user_id=member.id, organization_id=org_id, role="contributor"))
        element = ArchiMateElement(organization_id=org_id, name="Existing driver %s" % suffix,
                                   type="Driver", layer="Motivation", scope="enterprise",
                                   custom_properties={})
        db.session.add(element)
        db.session.commit()
        journey_id, ea_id, lead_id = journey.id, ea.id, lead.id
        leads = {w["key"]: lead_id for w in ProgrammeTypeCatalogue().get("s4hana").workstreams}
        actor = ActorContext(ea_id, org_id, frozenset({"enterprise_architect"}), "credit-smoke")
        TransformationProgrammeService.instantiate_template(
            actor=actor, journey_id=journey_id, command_key=uuid.uuid4().hex,
            request={
                "name": "Smoke S4 credit %s" % suffix, "objective": "Credit smoke objective.",
                "owner_id": ea_id, "target_date": None,
                "target_date_unavailable_reason": "Not yet scheduled",
                "outcome": {"statement": "Close faster", "owner_id": ea_id, "direction": "decrease",
                            "measure": {"metric_name": "Days", "unit": "days", "aggregation": "average",
                                        "baseline_value": None, "unavailable_reason": "n/a",
                                        "target_value": 5}},
                "leads": leads,
            })
        rows = db.session.execute(db.text(
            "SELECT d.template_code, d.id FROM deliverables d JOIN work_packages w ON w.id=d.work_package_id "
            "WHERE w.organization_id=:o AND w.strategic_initiative_id="
            "(SELECT programme_id FROM architecture_journeys WHERE id=:j)"),
            {"o": org_id, "j": journey_id}).all()
        ids = {r[0]: r[1] for r in rows}
    yield {"journey_id": journey_id, "deliverables": ids, "suffix": suffix}


def _url(j):
    return "/architecture-journey/work/%s/programme-structure" % j["journey_id"]


def _count_text(page, did):
    return page.locator('[data-testid="deliverable-count-%s"]' % did).inner_text()


def test_credit_and_complete_persist_after_reload(page, live_server, seeded, structured_journey):
    j = structured_journey
    frame = j["deliverables"]["s4hana.frame.business_case_and_value_drivers"]
    shape = j["deliverables"]["s4hana.shape.target_design_and_delivery_plan"]
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, _url(j))
    assert "None yet" in _count_text(page, frame)

    # complete with nothing in the model: refused without a reason
    page.locator('[data-testid="complete-%s"]' % shape).click(force=True)
    page.wait_for_selector('[data-testid="deliverable-error-%s"]' % shape, state="visible", timeout=PAGE_TIMEOUT)
    assert "why" in page.locator('[data-testid="deliverable-error-%s"]' % shape).inner_text().lower()
    page.fill('[data-testid="complete-reason-%s"]' % shape, "Covered in the design pack")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.locator('[data-testid="complete-%s"]' % shape).click(force=True)
    assert "Completed" in _count_text(page, shape)
    assert "None yet" in _count_text(page, shape)  # the reason never fakes a count

    # credit an existing element
    page.locator('[data-testid="add-existing-%s"]' % frame).click(force=True)
    page.fill("#ex-%s" % frame, "Existing driver %s" % j["suffix"])
    page.locator('[data-testid="deliverable-%s"] [role="option"]' % frame).first.click()
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.locator('[data-testid="credit-existing-%s"]' % frame).click(force=True)
    assert "1 of 3 in the model" in _count_text(page, frame)

    # create a new element of another declared type
    page.locator('[data-testid="add-new-%s"]' % frame).click(force=True)
    page.select_option('[data-testid="new-type-%s"]' % frame, "Goal")
    page.fill('[data-testid="new-name-%s"]' % frame, "New goal %s" % j["suffix"])
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.locator('[data-testid="create-new-%s"]' % frame).click(force=True)
    assert "2 of 3 in the model" in _count_text(page, frame)

    # a fresh load shows the same
    _visit(page, live_server, _url(j))
    assert "2 of 3 in the model" in _count_text(page, frame)
    assert "Existing driver %s" % j["suffix"] in page.inner_text('[data-testid="deliverable-%s"]' % frame)


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_deliverable_credit_and_complete_authorisation(archetype, page, live_server, seeded, structured_journey):
    j = structured_journey
    did = j["deliverables"]["s4hana.decide.decision_readiness"]
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/architecture-journey/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    token = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
    base = live_server + "/architecture-journey/work/%s/deliverables/%s" % (j["journey_id"], did)
    headers = {"X-CSRFToken": token, "Content-Type": "application/json"}
    for suffix, body in (("/elements", {"element_type": "Assessment", "name": "auth probe " + uuid.uuid4().hex[:6]}),
                         ("/complete", {"reason": "auth probe"})):
        response = page.request.post(base + suffix, headers=headers, data=json.dumps(body))
        if archetype in ALLOWED:
            assert response.status in (200, 201), (archetype, suffix, response.status, response.text()[:200])
        else:
            assert response.status in (401, 403, 404), (archetype, suffix, response.status)
