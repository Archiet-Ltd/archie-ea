"""The business case as a document on a work package.

Figures on the page are the cost fact store's; payback is computed; the case
and everything it reads are the signed-in organisation's alone.
"""

import math
from decimal import Decimal

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _portfolio_manager(db_session, org, tag):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Contributor").first() or Role.query.filter_by(default=True).first()
    user = User(email=f"bcdoc-{tag}-{org.id}@example.com", organization_id=org.id,
                enterprise_role="portfolio_manager", role=role, confirmed=True,
                first_name="Pia", last_name=f"Manager{tag}")
    db_session.add(user)
    db_session.commit()
    return user


def _world(db_session, org, tag):
    """A user, a work package, two register risks and a costed case in one organisation."""
    from app.commands.seed_demo_company import (
        _BUSINESS_CASE_RISKS,
        _BUSINESS_CASE_TITLE,
        _BUSINESS_CASE_WORK_PACKAGE,
        _seed_business_case,
    )
    from app.models.business_case import BusinessCase
    from app.models.risk import Risk
    from app.services import work_package_service

    user = _portfolio_manager(db_session, org, tag)
    wp = work_package_service.create_work_package(
        organization_id=org.id, name=_BUSINESS_CASE_WORK_PACKAGE)
    for title in _BUSINESS_CASE_RISKS:
        db_session.add(Risk(title=title, likelihood=4, impact=4,
                            organization_id=org.id, owner="Ivo Reed", mitigation_plan="Second region"))
    db_session.flush()
    assert _seed_business_case(org.id, user.id) == {"business_cases_created": 1}
    db_session.commit()
    case = BusinessCase.query.filter_by(organization_id=org.id, title=_BUSINESS_CASE_TITLE).one()
    return user, wp, case


def test_the_page_figures_are_the_cost_store_and_payback_is_computed(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.cost_fact import ELEMENT_BUSINESS_CASE_OPTION
    from app.modules.business_case import document
    from app.services import cost_fact_store

    org = make_org("bcdoc-figures")
    with tenant_ctx(org.id):
        user, _wp, case = _world(db_session, org, "f")
        recommended = next(o for o in case.options if o.is_recommended)

        def stored(category):
            return cost_fact_store.total_in_reporting_currency(
                org.id, element_type=ELEMENT_BUSINESS_CASE_OPTION, element_ids=[recommended.id],
                category=category).amount

        view = document.build_document(case).recommended
        assert (view.one_off, view.run_annual, view.benefit_annual) == (
            stored("one_off"), stored("run_annual"), stored("benefit_annual"))
        assert view.three_year_cost == stored("one_off") + 3 * stored("run_annual")
        expected = math.ceil(stored("one_off") / (stored("benefit_annual") - stored("run_annual")) * 12)
        assert view.payback_months == expected == 56

        client = app.test_client()
        login_as(client, user)
        page = client.get(f"/business-case/{case.id}").get_data(as_text=True)
        assert "56 months" in page
        assert f'data-figure="payback-{recommended.id}"' in page


def test_a_case_that_never_pays_back_says_why_instead_of_a_number():
    from app.modules.business_case.document import payback

    months, note = payback(Decimal(100), Decimal(50), Decimal(40))
    assert months is None and "running cost" in note
    months, note = payback(Decimal(0), Decimal(10), Decimal(0))
    assert months is None and "Nothing is invested" in note
    months, note = payback(None, None, Decimal(40))
    assert months is None and "Add the one-off cost" in note


def test_editing_writes_the_figures_as_cost_facts_and_stamps_the_decision(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.business_case import BusinessCase
    from app.models.cost_fact import ELEMENT_BUSINESS_CASE_OPTION, CostFact

    org = make_org("bcdoc-edit")
    with tenant_ctx(org.id):
        user, wp, case = _world(db_session, org, "e")
        client = app.test_client()
        login_as(client, user)
        form = {
            "title": case.title, "work_package_id": str(wp.id), "problem_statement": "Changed problem",
            "status": "rejected", "decision_note": "Too dear this year.", "recommended": "0",
            "option_0_name": "Only option", "option_0_one_off": "1200.50", "option_0_run_annual": "100",
            "option_0_benefit_annual": "700",
        }
        resp = client.post(f"/business-case/{case.id}/edit", data=form, follow_redirects=False)
        assert resp.status_code == 302, resp.get_data(as_text=True)[:400]
        db_session.expire_all()
        case = db_session.get(BusinessCase, case.id)
        assert [o.name for o in case.options] == ["Only option"]
        assert case.status == "rejected" and case.decided_by_id == user.id and case.decided_at is not None
        facts = {f.category: f.amount for f in CostFact.query.filter_by(
            organization_id=org.id, element_type=ELEMENT_BUSINESS_CASE_OPTION,
            element_id=case.options[0].id)}
        assert facts == {"one_off": Decimal("1200.5000"), "run_annual": Decimal("100.0000"),
                         "benefit_annual": Decimal("700.0000")}
        # The two dropped options leave no cost facts behind.
        assert CostFact.query.filter_by(
            organization_id=org.id, element_type=ELEMENT_BUSINESS_CASE_OPTION).count() == 3
        first_stamp = case.decided_at

        form["status"] = "draft"
        client.post(f"/business-case/{case.id}/edit", data=form)
        db_session.expire_all()
        case = db_session.get(BusinessCase, case.id)
        assert case.decided_at is None and case.decided_by_id is None
        assert first_stamp is not None


def test_a_negative_figure_is_refused_with_a_reason(app, db_session, make_org, tenant_ctx, login_as):
    org = make_org("bcdoc-negative")
    with tenant_ctx(org.id):
        user, wp, case = _world(db_session, org, "n")
        client = app.test_client()
        login_as(client, user)
        resp = client.post(f"/business-case/{case.id}/edit", data={
            "title": case.title, "work_package_id": str(wp.id), "status": "draft",
            "option_0_name": "X", "option_0_one_off": "-5"})
        assert resp.status_code == 400
        assert "One-off cost must be a number, zero or more" in resp.get_data(as_text=True)


def test_another_organisation_sees_none_of_it(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.business_case import BusinessCase
    from app.models.cost_fact import ELEMENT_BUSINESS_CASE_OPTION, CostFact
    from app.models.risk import Risk
    from app.modules.business_case import document

    org_a, org_b = make_org("bcdoc-iso-a"), make_org("bcdoc-iso-b")
    with tenant_ctx(org_a.id):
        _user_a, wp_a, case_a = _world(db_session, org_a, "a")
        option_a = case_a.options[0].id
    with tenant_ctx(org_b.id):
        user_b = _portfolio_manager(db_session, org_b, "b")
        client = app.test_client()
        login_as(client, user_b)
        # Cannot open, edit or save the other organisation's case.
        assert client.get(f"/business-case/{case_a.id}").status_code == 404
        assert client.get(f"/business-case/{case_a.id}/edit").status_code == 404
        assert client.post(f"/business-case/{case_a.id}/edit", data={"title": "stolen"}).status_code == 404
        assert db_session.get(BusinessCase, case_a.id).title != "stolen"
        # Cannot list it, or link its own case to the other organisation's work package.
        assert "Move the event backbone" not in client.get("/business-case/").get_data(as_text=True)
        own = BusinessCase(title="B case", organization_id=org_b.id)
        db_session.add(own)
        db_session.commit()
        resp = client.post(f"/business-case/{own.id}/edit",
                           data={"title": "B case", "work_package_id": str(wp_a.id), "status": "draft"})
        assert resp.status_code == 400
        assert "not found in this organisation" in resp.get_data(as_text=True)
        db_session.expire_all()
        assert db_session.get(BusinessCase, own.id).work_package_id is None
        # Its document shows no risk, option or cost of the other organisation.
        doc_b = document.build_document(own)
        assert doc_b.options == [] and doc_b.risks == []
        assert document.linked_risks(org_b.id, wp_a.id) == []
        assert document.option_figures(org_b.id, [option_a], "USD")[option_a] == {
            "one_off": None, "run_annual": None, "benefit_annual": None}
        # Organisation A's rows exist, and organisation B's session cannot read them.
        assert Risk.query.filter_by(organization_id=org_a.id).count() == 0
        assert CostFact.query.filter_by(organization_id=org_a.id,
                                        element_type=ELEMENT_BUSINESS_CASE_OPTION).count() == 0
    with tenant_ctx(org_a.id):
        assert Risk.query.filter_by(organization_id=org_a.id).count() == 2
        assert CostFact.query.filter_by(organization_id=org_a.id,
                                        element_type=ELEMENT_BUSINESS_CASE_OPTION).count() == 9


def test_the_demo_case_is_complete(db_session, make_org, tenant_ctx):
    from app.modules.business_case import document

    org = make_org("bcdoc-seed")
    with tenant_ctx(org.id):
        _user, wp, case = _world(db_session, org, "s")
        doc = document.build_document(case)
        assert case.work_package_id == wp.id
        assert len(doc.options) == 3 and doc.recommended.option.name == "Managed broker"
        assert all(o.one_off is not None and o.run_annual is not None for o in doc.options)
        assert len(doc.risks) == 2
        assert case.status == "approved" and case.decided_by_id and case.decided_at
