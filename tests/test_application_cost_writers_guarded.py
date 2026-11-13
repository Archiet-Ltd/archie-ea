"""No route saves an application cost for a person who may not set one, or for another organisation's system.

Two real organisations throughout. The limited person is an architect with no spend authority; the
other person administers the organisation and may set costs. Every route that writes a cost is
called by both, and by a person of the other organisation.
"""
from __future__ import annotations

import io
import uuid

import pytest

from tests.test_vendor_pages import _user
from tests.test_vendor_pages_agree import _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")


def _admin(db_session, org):
    user = _user(db_session, org, "enterprise_architect")
    user.grant_org_admin()
    db_session.flush()
    return user


def _system(db_session, org, name=None, cost=None):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import set_annual_cost

    row = ApplicationComponent(name=name or "Sys " + uuid.uuid4().hex[:6], organization_id=org.id,
                               lifecycle_status="operational")
    db_session.add(row)
    db_session.flush()
    if cost is not None:
        set_annual_cost(row, cost)
    db_session.commit()
    return row


def _cost(db_session, app_id):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import get_annual_cost

    db_session.expire_all()
    row = db_session.get(ApplicationComponent, app_id)
    return get_annual_cost(row) if row is not None else None


def _as(client, login_as, user):
    _forget_cached_user()
    login_as(client, user)


def _found(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    db_session.expire_all()
    return ApplicationComponent.query.filter_by(name=name, organization_id=org.id).first()


# ── POST /applications/create ─────────────────────────────────────────────────

def test_create_by_a_person_without_spend_saves_the_system_but_not_the_cost(client, login_as, db_session, make_org):
    mine = make_org("cw-a")
    limited = _user(db_session, mine, "solution_architect")
    name = "Limited Create " + uuid.uuid4().hex[:6]
    _as(client, login_as, limited)
    reply = client.post("/applications/create", data={"name": name, "total_cost_of_ownership": "91000",
                                                      "license_cost_annual": "5000"}, follow_redirects=True)
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    assert "The cost was not saved" in reply.get_data(as_text=True)
    row = _found(db_session, mine, name)
    assert row is not None
    assert _cost(db_session, row.id) in (None, 0)
    assert not row.license_cost_annual


def test_create_by_an_administrator_saves_the_cost(client, login_as, db_session, make_org):
    mine = make_org("cw-b")
    name = "Admin Create " + uuid.uuid4().hex[:6]
    _as(client, login_as, _admin(db_session, mine))
    reply = client.post("/applications/create", data={"name": name, "total_cost_of_ownership": "91000"},
                        follow_redirects=True)
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    assert "The cost was not saved" not in reply.get_data(as_text=True)
    assert float(_cost(db_session, _found(db_session, mine, name).id)) == 91000.0


# ── POST /applications/rationalization/api/enrich/<id> ────────────────────────

def test_enrich_by_a_person_without_spend_leaves_the_cost_and_saves_the_rest(client, login_as, db_session, make_org):
    mine = make_org("cw-c")
    row = _system(db_session, mine, cost=1000)
    _as(client, login_as, _user(db_session, mine, "solution_architect"))
    reply = client.post(f"/applications/rationalization/api/enrich/{row.id}",
                        json={"total_cost_of_ownership": 999999, "business_criticality": "High"})
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    body = reply.get_json()
    assert body["updated_fields"] == ["business_criticality"]
    assert "cost was not saved" in body["cost_not_saved"]
    assert float(_cost(db_session, row.id)) == 1000.0


def test_enrich_of_another_organisations_system_answers_404_and_changes_nothing(client, login_as, db_session, make_org):
    mine, theirs = make_org("cw-d"), make_org("cw-e")
    row = _system(db_session, theirs, cost=2000)
    _as(client, login_as, _admin(db_session, mine))
    reply = client.post(f"/applications/rationalization/api/enrich/{row.id}",
                        json={"total_cost_of_ownership": 1, "business_criticality": "Low"})
    assert reply.status_code == 404
    assert float(_cost(db_session, row.id)) == 2000.0


def test_enrich_by_an_administrator_sets_the_cost_of_their_own_system(client, login_as, db_session, make_org):
    mine = make_org("cw-f")
    row = _system(db_session, mine)
    _as(client, login_as, _admin(db_session, mine))
    reply = client.post(f"/applications/rationalization/api/enrich/{row.id}",
                        json={"total_cost_of_ownership": 4200})
    assert reply.status_code == 200 and "total_cost_of_ownership" in reply.get_json()["updated_fields"]
    assert float(_cost(db_session, row.id)) == 4200.0


# ── application_mgmt create, edit, quick-fill ─────────────────────────────────

def test_the_dashboard_editor_by_a_person_without_spend_keeps_the_cost(client, login_as, db_session, make_org):
    mine = make_org("cw-g")
    row = _system(db_session, mine, name="Edited " + uuid.uuid4().hex[:6], cost=3000)
    _as(client, login_as, _user(db_session, mine, "solution_architect"))
    reply = client.post(f"/dashboard/applications/{row.id}/edit",
                        data={"name": row.name + " renamed", "component_type": "Web Application",
                              "deployment_status": "design", "total_cost_of_ownership": "777",
                              "license_cost_annual": "888", "application_category": "Custom"})
    assert reply.status_code == 302, reply.get_data(as_text=True)[:300]
    assert float(_cost(db_session, row.id)) == 3000.0
    db_session.expire_all()
    assert row.name.endswith(" renamed")
    assert not row.license_cost_annual


def test_the_dashboard_editor_for_another_organisations_system_answers_404(client, login_as, db_session, make_org):
    mine, theirs = make_org("cw-h"), make_org("cw-i")
    row = _system(db_session, theirs, cost=3000)
    _as(client, login_as, _admin(db_session, mine))
    assert client.get(f"/dashboard/applications/{row.id}/edit").status_code == 404
    reply = client.post(f"/dashboard/applications/{row.id}/edit",
                        data={"name": "Taken", "total_cost_of_ownership": "1", "lifecycle_status": "operational"})
    assert reply.status_code == 404
    assert float(_cost(db_session, row.id)) == 3000.0


def test_quick_fill_cost_is_refused_without_spend_and_for_another_organisation(client, login_as, db_session, make_org):
    mine, theirs = make_org("cw-k"), make_org("cw-l")
    row = _system(db_session, mine)
    other = _system(db_session, theirs)
    _as(client, login_as, _user(db_session, mine, "solution_architect"))
    refused = client.patch(f"/dashboard/api/applications/{row.id}/quick-fill", json={"field": "annual_cost", "value": "99"})
    assert refused.status_code == 403 and "cost was not saved" in refused.get_json()["error"]
    db_session.expire_all()
    assert not row.license_cost_annual
    _as(client, login_as, _admin(db_session, mine))
    foreign = client.patch(f"/dashboard/api/applications/{other.id}/quick-fill",
                           json={"field": "annual_cost", "value": "99"})
    assert foreign.status_code == 404
    db_session.expire_all()
    assert not other.license_cost_annual


# ── onboarding ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/dashboard/api/rationalization/onboard"])
def test_onboarding_by_a_person_without_spend_saves_no_cost(client, login_as, db_session, make_org, path):
    mine = make_org("cw-m")
    name = "Onboarded " + uuid.uuid4().hex[:6]
    _as(client, login_as, _user(db_session, mine, "solution_architect"))
    reply = client.post(path, json={"name": name, "annual_cost": 123456})
    assert reply.status_code in (200, 201), reply.get_data(as_text=True)[:300]
    row = _found(db_session, mine, name)
    assert row is not None and _cost(db_session, row.id) in (None, 0)


# ── consolidation list ────────────────────────────────────────────────────────

def _entry(db_session, row):
    from app.modules.governance.models.consolidation_list import ConsolidationListEntry

    entry = ConsolidationListEntry(application_id=row.id, source_group_name="probe",
                                   recommended_action="pending_review", priority="high")
    db_session.add(entry)
    db_session.commit()
    return entry


def test_consolidation_update_by_a_person_without_spend_leaves_the_application_cost(client, login_as, db_session, make_org):
    mine = make_org("cw-n")
    row = _system(db_session, mine, cost=5000)
    entry = _entry(db_session, row)
    _as(client, login_as, _user(db_session, mine, "solution_architect"))
    reply = client.put(f"/consolidation-list/api/entry/{entry.id}",
                       json={"priority": "low", "annual_operating_cost": 1})
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    assert "cost was not saved" in reply.get_json()["cost_not_saved"]
    assert float(_cost(db_session, row.id)) == 5000.0


def test_bulk_cost_import_is_refused_without_spend_and_skips_another_organisations_system(client, login_as, db_session, make_org):
    mine, theirs = make_org("cw-o"), make_org("cw-p")
    row = _system(db_session, mine, cost=100)
    foreign = _system(db_session, theirs, cost=200)
    csv_text = "application_id,annual_operating_cost\n%d,9\n%d,9\n" % (row.id, foreign.id)

    def upload():
        return client.post("/consolidation-list/api/bulk-cost-import",
                           data={"file": (io.BytesIO(csv_text.encode()), "costs.csv")},
                           content_type="multipart/form-data")

    _as(client, login_as, _user(db_session, mine, "solution_architect"))
    assert upload().status_code == 403
    assert float(_cost(db_session, row.id)) == 100.0
    _as(client, login_as, _admin(db_session, mine))
    allowed = upload().get_json()
    assert allowed["updated_count"] == 1
    assert float(_cost(db_session, row.id)) == 9.0
    assert float(_cost(db_session, foreign.id)) == 200.0


# ── reading and writing follow one rule ───────────────────────────────────────

def test_a_finance_person_is_allowed_to_set_a_cost_exactly_when_they_may_read_spend(client, login_as, db_session, make_org):
    from app.decorators.requires_role import may_see_spend_pages
    from app.services.cost_visibility import costs_visible
    from app.utils.role_access import can_see_costs

    mine, theirs = make_org("cw-h"), make_org("cw-i")
    finance = _user(db_session, mine, "finance")
    # the two older checks disagree for this role: spend pages are open to finance, the cost set is not
    assert may_see_spend_pages(finance) is True and can_see_costs(finance) is False
    assert costs_visible(finance) is True
    row = _system(db_session, mine)
    _as(client, login_as, finance)
    reply = client.post(f"/applications/rationalization/api/enrich/{row.id}", json={"total_cost_of_ownership": 6100})
    assert reply.status_code == 200 and "total_cost_of_ownership" in reply.get_json()["updated_fields"]
    assert float(_cost(db_session, row.id)) == 6100.0
    # a finance person of the other organisation still cannot reach this organisation's system
    other = _user(db_session, theirs, "finance")
    _as(client, login_as, other)
    refused = client.post(f"/applications/rationalization/api/enrich/{row.id}", json={"total_cost_of_ownership": 1})
    assert refused.status_code == 404
    assert float(_cost(db_session, row.id)) == 6100.0
