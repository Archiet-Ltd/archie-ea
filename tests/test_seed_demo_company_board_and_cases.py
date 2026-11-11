"""What the demonstration organisation holds for the module pictures: decisions ready for the board's queue, business cases whose figures agree, and contracts that renew on a spread.

One seed (``seed_demo_company``) holds all of it; the capture script adds none of it a second time.
"""
from __future__ import annotations

import datetime as dt
import pathlib

from tests.test_seed_demo_company import _get_lantern_org_id, _seed_once


def test_every_proposed_decision_can_be_put_before_the_board_and_a_rerun_adds_none(app, db_session, make_org):
    """The submission command reads the decision and the person through its own connection, which cannot see
    rows this test has not committed, so here the board's queue stays empty and the seed says why in its log
    rather than failing; the submitted reviews are checked against a committed seed in the module-image run.
    What this test pins is what the command needs to accept a decision: it is proposed and states its
    consequences."""
    from app.models.architecture_decision import ArchitectureDecision

    first = _seed_once()
    org_id = _get_lantern_org_id()
    decisions = ArchitectureDecision.query.filter_by(organization_id=org_id).all()
    proposed = [d for d in decisions if d.status == "proposed"]

    assert len(decisions) == 6 and len(proposed) == 4
    assert all(d.consequences and d.context and d.decision and d.rationale for d in decisions)
    assert "arb_typed_reviews_created" in first
    assert _seed_once()["architecture_decisions_created"] == 0


def test_business_cases_have_their_three_year_cost_and_return_worked_out_from_their_figures(app, db_session, make_org):
    from app.models.business_case import BusinessCase

    _seed_once()
    org_id = _get_lantern_org_id()
    cases = BusinessCase.query.filter_by(organization_id=org_id).all()

    assert len(cases) == 3
    for case in cases:
        assert float(case.tco_3yr) == float(case.capex) + 3 * float(case.opex_annual)
        assert float(case.roi_percentage) == round(
            (3 * float(case.financial_benefit_annual) - float(case.tco_3yr)) / float(case.tco_3yr) * 100, 1)
        assert case.problem_statement and case.options_considered and case.recommended_option and case.solution_id
    assert _seed_once()["business_cases_created"] == 0


def test_contracts_renew_on_a_spread_that_fills_every_window_the_renewals_screen_counts(app, db_session, make_org):
    from app.models.application_portfolio import VendorContract

    _seed_once()
    org_id = _get_lantern_org_id()
    today = dt.date.today()
    days = sorted((c.renewal_date - today).days for c in VendorContract.query.filter_by(organization_id=org_id))

    assert len(days) == 16 and all(c.end_date for c in VendorContract.query.filter_by(organization_id=org_id))
    assert any(d <= 30 for d in days) and any(30 < d <= 90 for d in days) and any(90 < d <= 180 for d in days)
    assert _seed_once()["contract_terms_recorded"] == 0


def test_the_capture_script_seeds_no_second_set_of_initiatives_or_business_cases():
    source = (pathlib.Path(__file__).resolve().parent.parent / "scripts" / "capture_screenshots.py").read_text(
        encoding="utf-8")

    assert "LQ-INIT-" not in source and "BusinessCase(" not in source and "EnterpriseInitiative(" not in source
    # nor a second set of board reviews or contracts: the seed holds them, and a picture shows the seed's figures
    assert "ARBReviewItem(" not in source and "VendorContract(" not in source and "VendorOrganization(" not in source


def test_the_procurement_person_reads_the_renewals_and_another_organisations_contracts_stay_out(
        app, db_session, make_org, login_as, client):
    """The renewals picture is made as the seeded procurement person, so that person must be able to open the
    page on a fresh seed, and see this organisation's contracts only."""
    import uuid

    from app.models.application_portfolio import VendorContract
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization

    _seed_once()
    org_id = _get_lantern_org_id()
    other = make_org("renewals-other")
    vendor = VendorOrganization(name="Other vendor %s" % uuid.uuid4().hex[:6])
    db_session.add(vendor)
    db_session.flush()
    foreign = "Foreign deal %s" % uuid.uuid4().hex[:6]
    db_session.add(VendorContract(
        contract_name=foreign, organization_id=other.id, vendor_id=vendor.id, status="active",
        start_date=dt.date.today() - dt.timedelta(days=60), renewal_date=dt.date.today() + dt.timedelta(days=10),
        end_date=dt.date.today() + dt.timedelta(days=190)))
    db_session.flush()

    person = User.query.filter_by(email="taylor.brennan@lantern-quay.example.com", organization_id=org_id).one()
    assert person.enterprise_role == "procurement"
    login_as(client, person)
    page = client.get("/procurement/renewals")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "renewals-table" in html and foreign not in html


def test_the_figures_in_the_module_pictures_are_the_figures_the_seed_holds(app, db_session, make_org, tenant_ctx):
    """A picture's description is built from its screen when it is captured; this ties those recorded figures to
    the seed itself, so changing the seed without recapturing fails here instead of in front of a reader."""
    import json

    from app.models.architecture_review_board import ARBReviewItem
    from app.models.risk import Risk
    from app.models.vendor.vendor_organization import EnterpriseInitiative
    from app.services.public_pages import IMG_MODULES_DIR

    _seed_once()
    org_id = _get_lantern_org_id()
    facts = {slug: json.loads((IMG_MODULES_DIR / "captures.json").read_text(encoding="utf-8"))[slug]["desktop"]["facts"]
             for slug in ("arb", "risk-register", "portfolio")}
    with tenant_ctx(org_id):
        reviews = ARBReviewItem.query.filter_by(organization_id=org_id).all()
        pending = [r for r in reviews if r.status in ("submitted", "under_review", "pending_info")]
        # the four reviews put in the queue are made through a connection this test cannot see (see the first test)
        queued = facts["arb"]["in_queue"]
        assert facts["arb"]["total"] == len(reviews) + queued
        assert facts["arb"]["pending"] == len(pending) + queued
        assert facts["arb"]["outside_queue"] == len([r for r in pending if r.review_cycle_id is None])
        assert facts["risk-register"]["risks"] == Risk.query.filter_by(organization_id=org_id).count()
        assert facts["portfolio"]["initiatives"] == EnterpriseInitiative.query.filter_by(organization_id=org_id).count()


def test_the_capture_script_adds_no_system_and_no_framework_a_new_customer_would_not_have():
    """The vendors picture counted twenty systems and the compliance picture three frameworks because the capture
    script added a copy of a system and two frameworks of its own on top of the seed. A picture shows the seed."""
    source = (pathlib.Path(__file__).resolve().parent.parent / "scripts" / "capture_screenshots.py").read_text(
        encoding="utf-8")

    import inspect

    from scripts.capture_screenshots import _seed_lantern_quay, _seed_module_screen_data

    assert "RegulatoryFramework(" not in source and "ComplianceControl(" not in source
    for seeding in (_seed_lantern_quay, _seed_module_screen_data):
        assert inspect.signature(seeding).parameters["with_duplicate_copy"].default is False


def test_the_vendors_and_compliance_figures_in_the_pictures_are_what_a_new_customer_sees(
        app, db_session, make_org, login_as, client):
    """Rendered, as the person the pictures are made as, on the seeded organisation: the vendors line and the
    compliance figures read the same numbers the pictures were captured with."""
    import json
    import re

    from app.models.user import User
    from app.services.public_pages import DEMO_PERSONA, IMG_MODULES_DIR

    _seed_once()
    org_id = _get_lantern_org_id()
    manifest = json.loads((IMG_MODULES_DIR / "captures.json").read_text(encoding="utf-8"))
    login_as(client, User.query.filter_by(email=DEMO_PERSONA, organization_id=org_id).one())

    vendors = " ".join(client.get("/applications/vendors").get_data(as_text=True).split())
    line = re.search(r"(\d+) vendors? supply (\d+) of your (\d+) systems?", vendors)
    shown = manifest["vendors"]["desktop"]["facts"]
    assert line and (int(line.group(1)), int(line.group(2)), int(line.group(3))) == (
        shown["vendors"], shown["systems_supplied"], shown["systems"])

    compliance = " ".join(client.get("/dashboard/compliance").get_data(as_text=True).split())
    frameworks = re.search(r"Active frameworks\s*(\d+)", re.sub(r"<[^>]+>", " ", compliance))
    controls = re.search(r"Controls\s*(\d+)", re.sub(r"<[^>]+>", " ", compliance))
    shown = manifest["compliance-frameworks"]["desktop"]["facts"]
    assert frameworks and int(frameworks.group(1)) == shown["frameworks"]
    assert controls and int(controls.group(1)) == shown["controls"]
