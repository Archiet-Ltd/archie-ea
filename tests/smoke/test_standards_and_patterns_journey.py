"""Standards, patterns and conformance, driven through the rendered screens.

* The CTO retires a technology standard from the Tech Radar: picks the
  technology, a sunset date and its replacement, submits, reloads, and sees the
  application running it with its owner listed under "Standards being retired".
* A solution architect checks a solution's interfaces against the pattern
  catalogue, sees the named rule a non-conformant interface breaks, fixes the
  interface, checks again after a reload and sees the breach clear.
* A solution architect states a solution context, gets a reference
  architecture recommended with its fit and controls, applies it, reloads and
  sees it applied with the controls the solution inherits.

Every row this module creates carries a random suffix; the database is shared.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def standards_data(seeded):
    from app import create_app, db
    from app.models import ArchiMateRelationship
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.integration_pattern import IntegrationPattern
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionIntegrationFlow

    suffix = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    out = {"suffix": suffix}
    app = create_app("testing")
    with app.app_context():
        old = ArchiMateElement(name=f"Smoke DB 11 {suffix}", type="SystemSoftware",
                               layer="Technology", organization_id=org_id)
        new = ArchiMateElement(name=f"Smoke DB 16 {suffix}", type="SystemSoftware",
                               layer="Technology", organization_id=org_id)
        db.session.add_all([old, new])
        db.session.flush()
        running = ApplicationComponent(name=f"Smoke Orders {suffix}", organization_id=org_id)
        other = ApplicationComponent(name=f"Smoke Portal {suffix}", organization_id=org_id)
        db.session.add_all([running, other])
        db.session.flush()
        db.session.add(ArchiMateRelationship(type="serving", source_id=old.id,
                                             target_id=running.archimate_element_id,
                                             organization_id=org_id))
        db.session.add(ApplicationOwner(application_id=running.id,
                                        user_id=seeded["ids"]["app_manager_user"],
                                        organization_id=org_id, ownership_type="primary"))

        rest = IntegrationPattern(
            name=f"Smoke approved REST {suffix}", vendor_key="GENERIC", pattern_type="api",
            approval_status="approved", protocol="rest", data_format="json",
            allowed_auth_methods=["oauth2"], requires_encryption=True, allows_personal_data=False,
        )
        refarch = IntegrationPattern(
            name=f"Smoke event-driven {suffix}", vendor_key="GENERIC", pattern_type="event_driven",
            approval_status="approved", is_reference_architecture=True,
            description="Events through a managed broker.",
            fit_context={"data": ["personal"], "latency": ["near_real_time"], "hosting": ["cloud"]},
            components=[
                {"name": f"Smoke Broker {suffix}", "type": "TechnologyService", "layer": "technology"},
                {"name": f"Smoke Event Store {suffix}", "type": "DataObject", "layer": "application"},
            ],
            applies_to_controls=[{"name": f"Smoke encryption {suffix}", "description": "TLS."}],
        )
        db.session.add_all([rest, refarch])
        db.session.flush()

        solution = Solution(name=f"Smoke order tracking {suffix}", organization_id=org_id,
                            created_by_id=seeded["ids"]["solution_architect_user"])
        db.session.add(solution)
        db.session.flush()
        flow = SolutionIntegrationFlow(
            solution_id=solution.id, source_app_id=other.id, target_app_id=running.id,
            flow_name=f"Smoke customer sync {suffix}", pattern_id=rest.id, protocol="soap",
            data_format="json", auth_method="oauth2", encryption_required=True, contains_pii=False,
        )
        db.session.add(flow)
        db.session.commit()
        out.update(old=old.id, new=new.id, running=running.id, solution=solution.id,
                   flow=flow.id, refarch=refarch.id, refarch_name=refarch.name,
                   control=f"Smoke encryption {suffix}")
        db.session.remove()
    return out


def test_cto_retires_a_standard_and_sees_the_affected_owner(browser, live_server, seeded, standards_data):
    d = standards_data
    context = browser.new_context()
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["cto"])
        assert page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT).status == 200

        page.get_by_test_id("radar-sunset-element").select_option(str(d["old"]))
        page.get_by_test_id("radar-sunset-date").fill("2027-03-31")
        page.get_by_test_id("radar-sunset-replacement").select_option(str(d["new"]))
        with page.expect_navigation(timeout=PAGE_TIMEOUT) as submitted:
            page.get_by_test_id("radar-sunset-submit").click()
        assert submitted.value.status == 200

        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        retired = page.get_by_test_id(f"radar-sunset-{d['old']}")
        expect(retired).to_contain_text("Sunset 31 Mar 2027", timeout=PAGE_TIMEOUT)
        expect(retired).to_contain_text(f"Replacement: Smoke DB 16 {d['suffix']}")
        affected = retired.get_by_test_id(f"radar-affected-app-{d['running']}")
        expect(affected).to_contain_text(f"Smoke Orders {d['suffix']}")
        expect(affected).to_contain_text("(primary)")
        expect(retired).to_contain_text("Owners notified: 1")
    finally:
        context.close()


def test_architect_checks_interfaces_and_sees_the_breach_clear(browser, live_server, seeded, standards_data):
    from app import create_app, db
    from app.models.solution_sad_models import SolutionIntegrationFlow

    d = standards_data
    context = browser.new_context()
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["solution_architect"])
        url = live_server + f"/solutions/{d['solution']}/conformance"
        assert page.goto(url, timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_test_id(f"interface-status-{d['flow']}")).to_contain_text("Not checked yet")

        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("interface-check-submit").click()
        page.reload(timeout=PAGE_TIMEOUT)
        breach = page.get_by_test_id(f"interface-breach-{d['flow']}-pattern-protocol")
        expect(breach).to_contain_text("The interface uses the protocol its pattern specifies",
                                       timeout=PAGE_TIMEOUT)

        # The architect fixes the interface design, then checks again.
        app = create_app("testing")
        with app.app_context():
            flow = SolutionIntegrationFlow.query.filter_by(id=d["flow"]).one()
            flow.protocol = "rest"
            db.session.commit()
            db.session.remove()

        page.reload(timeout=PAGE_TIMEOUT)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("interface-check-submit").click()
        page.reload(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id(f"interface-status-{d['flow']}")).to_contain_text(
            "Conforms to Smoke approved REST", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id(f"interface-breach-{d['flow']}-pattern-protocol")).to_have_count(0)
    finally:
        context.close()


def test_architect_applies_a_reference_architecture(browser, live_server, seeded, standards_data):
    d = standards_data
    context = browser.new_context()
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["solution_architect"])
        url = live_server + f"/solutions/{d['solution']}/reference-architecture"
        assert page.goto(url, timeout=PAGE_TIMEOUT).status == 200

        page.get_by_test_id("refarch-context-data").select_option("personal")
        page.get_by_test_id("refarch-context-latency").select_option("near_real_time")
        page.get_by_test_id("refarch-context-hosting").select_option("cloud")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("refarch-recommend").click()
        candidate = page.get_by_test_id(f"refarch-candidate-{d['refarch']}")
        expect(candidate).to_contain_text("3 of 3 stated facts match", timeout=PAGE_TIMEOUT)
        expect(candidate).to_contain_text(d["control"])

        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            candidate.get_by_test_id(f"refarch-apply-{d['refarch']}").click()
        page.reload(timeout=PAGE_TIMEOUT)
        applied = page.get_by_test_id(f"refarch-applied-{d['refarch']}")
        expect(applied).to_contain_text(d["refarch_name"], timeout=PAGE_TIMEOUT)
        expect(applied).to_contain_text(f"Smoke Broker {d['suffix']}")
        expect(applied).to_contain_text(f"Controls inherited: {d['control']}")
    finally:
        context.close()
