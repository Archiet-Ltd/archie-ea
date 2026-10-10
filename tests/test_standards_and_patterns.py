"""Technology standards, the pattern catalogue and conformance.

Three journeys, each checked for two organisations:

* A technology standard is retired: moved to the hold ring with a sunset date
  and a replacement. The applications running it (found through the modelled
  relationships) and their owners are listed, and the owners are notified.
* A solution's interfaces are checked against the integration pattern
  catalogue for protocol, security and the data they carry; every breach
  names the rule it violates, the result is kept on the interface, and it
  clears when the fixed interface is checked again.
* A reference architecture is recommended for a stated solution context with
  the fit explained, and applying it adds its components to the solution.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = os.path.dirname(os.path.dirname(__file__))

_CANONICAL_PRINCIPLE_SCRIPT = textwrap.dedent(
    """
    import app.models.motivation_extended as motivation_extended
    import app.models.models as models_module
    from app import db

    assert motivation_extended.Principle is models_module.Principle, (
        "motivation_extended must reuse the canonical Principle class"
    )

    principle_mappers = sorted(
        f"{mapper.class_.__module__}.{mapper.class_.__name__}"
        for mapper in db.Model.registry.mappers
        if getattr(mapper.class_, "__tablename__", None) == "principles"
    )
    assert principle_mappers == ["app.models.models.Principle"], principle_mappers
    """
)


# --------------------------------------------------------------------- #
# Builders                                                               #
# --------------------------------------------------------------------- #


def _user(db_session, org_id, label):
    from app.models.user import Role, User

    user = User(
        email=f"{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter(Role.name.in_(("Administrator", "Admin"))).first()
    if role is None:
        role = Role(name="Administrator")
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


def _tech(db_session, org_id, name, element_type="SystemSoftware"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=element_type, layer="Technology", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _application(db_session, org_id, name):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(comp)
    db_session.flush()
    return comp


def _relate(db_session, org_id, source_id, target_id, rel_type="serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        type=rel_type, source_id=source_id, target_id=target_id, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _owner(db_session, org_id, application_id, user_id, ownership_type="primary"):
    from app.models.application_owner import ApplicationOwner

    row = ApplicationOwner(
        application_id=application_id, user_id=user_id,
        ownership_type=ownership_type, organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _pattern(db_session, **fields):
    from app.models.integration_pattern import IntegrationPattern

    defaults = {
        "name": f"Pattern {uuid.uuid4().hex[:8]}",
        "vendor_key": "GENERIC",
        "pattern_type": "api",
        "approval_status": "approved",
    }
    defaults.update(fields)
    pattern = IntegrationPattern(**defaults)
    db_session.add(pattern)
    db_session.flush()
    return pattern


def _solution(db_session, org_id, name="Order tracking"):
    from app.models.solution_models import Solution

    sol = Solution(name=name, organization_id=org_id)
    db_session.add(sol)
    db_session.flush()
    return sol


def _flow(db_session, solution, source, target, name, **fields):
    from app.models.solution_sad_models import SolutionIntegrationFlow

    flow = SolutionIntegrationFlow(
        solution_id=solution.id, source_app_id=source.id, target_app_id=target.id,
        flow_name=name, **fields,
    )
    db_session.add(flow)
    db_session.flush()
    return flow


@pytest.fixture
def two_orgs(db_session, make_org):
    return make_org("standards-a"), make_org("standards-b")


# --------------------------------------------------------------------- #
# Retiring a technology standard                                         #
# --------------------------------------------------------------------- #


class TestSunsetStandard:
    def _estate(self, db_session, org):
        """A database version on a server; one application runs on the server,
        another only talks to that application."""
        db = _tech(db_session, org.id, "PostgreSQL 11", "SystemSoftware")
        server = _tech(db_session, org.id, "Orders DB server", "Node")
        newer = _tech(db_session, org.id, "PostgreSQL 16", "SystemSoftware")
        running = _application(db_session, org.id, "Order Service")
        caller = _application(db_session, org.id, "Shop Front")
        _relate(db_session, org.id, db.id, server.id, "assignment")
        _relate(db_session, org.id, server.id, running.archimate_element_id, "serving")
        _relate(db_session, org.id, caller.archimate_element_id, running.archimate_element_id, "flow")
        return db, newer, running, caller

    def test_sunset_lists_running_applications_and_notifies_their_owners(
        self, app, db_session, two_orgs, tenant_ctx
    ):
        from app.models.models import Notification
        from app.models.tech_radar import TechRadarEntry
        from app.modules.tech_radar import service

        org_a, _org_b = two_orgs
        cto = _user(db_session, org_a.id, "Cto")
        owner = _user(db_session, org_a.id, "Owner")
        with tenant_ctx(org_a.id):
            db, newer, running, caller = self._estate(db_session, org_a)
            _owner(db_session, org_a.id, running.id, owner.id)

            result = service.sunset(db.id, date(2027, 3, 31), newer.id, "Out of support", cto.id, "/technology/radar/")

            entry = TechRadarEntry.query.filter_by(archimate_element_id=db.id).one()
            assert entry.ring == "hold"
            assert entry.sunset_date == date(2027, 3, 31)
            assert entry.replacement_element_id == newer.id
            assert entry.owners_notified_count == 1
            names = [a["name"] for a in result["affected"]["applications"]]
            # Running on the server that hosts the database: yes. Merely calling
            # an application that does: no.
            assert names == ["Order Service"]
            assert result["affected"]["applications"][0]["owners"][0]["user_id"] == owner.id
            notes = Notification.query.filter(Notification.user_id == owner.id).all()
            assert len(notes) == 1
            assert "PostgreSQL 11" in notes[0].message and "PostgreSQL 16" in notes[0].message

    def test_missing_facts_are_reported_not_invented(self, app, db_session, two_orgs, tenant_ctx):
        from app.modules.tech_radar import service

        org_a, _ = two_orgs
        cto = _user(db_session, org_a.id, "Cto")
        with tenant_ctx(org_a.id):
            lonely = _tech(db_session, org_a.id, "Unused runtime")
            result = service.sunset(lonely.id, date(2027, 1, 1), None, "", cto.id, "/technology/radar/")
            assert result["affected"]["applications"] == []
            assert result["affected"]["reason"] == service.NO_APPLICATIONS_REASON

            db, _newer, running, _caller = self._estate(db_session, org_a)
            result = service.sunset(db.id, date(2027, 1, 1), None, "", cto.id, "/technology/radar/")
            row = result["affected"]["applications"][0]
            assert row["owners"] == []
            assert row["owner_reason"] == service.NO_OWNER_REASON
            assert result["notified"]["notified_users"] == 0

    def test_sunset_and_owner_list_persist_on_the_radar_page(
        self, app, db_session, two_orgs, tenant_ctx, client, login_as
    ):
        org_a, _ = two_orgs
        cto = _user(db_session, org_a.id, "Cto")
        owner = _user(db_session, org_a.id, "Owner")
        with tenant_ctx(org_a.id):
            db, newer, running, _ = self._estate(db_session, org_a)
            _owner(db_session, org_a.id, running.id, owner.id)
        db_id, newer_id, running_id = db.id, newer.id, running.id

        login_as(client, cto)
        resp = client.post("/technology/radar/sunset", data={
            "archimate_element_id": db_id,
            "sunset_date": "2027-03-31",
            "replacement_element_id": newer_id,
            "rationale": "Out of support",
        })
        assert resp.status_code == 302

        login_as(client, cto)
        body = client.get("/technology/radar/").get_data(as_text=True)
        assert f'data-testid="radar-sunset-{db_id}"' in body
        assert "31 Mar 2027" in body
        assert "Replacement: PostgreSQL 16" in body
        assert f'data-testid="radar-affected-app-{running_id}"' in body
        assert "Owner Tester (primary)" in body

    def test_other_organisation_never_sees_or_touches_the_sunset(
        self, app, db_session, two_orgs, tenant_ctx, client, login_as
    ):
        from app.models.models import Notification
        from app.models.tech_radar import TechRadarEntry
        from app.modules.tech_radar import service

        org_a, org_b = two_orgs
        cto_a = _user(db_session, org_a.id, "CtoA")
        cto_b = _user(db_session, org_b.id, "CtoB")
        owner_b = _user(db_session, org_b.id, "OwnerB")
        with tenant_ctx(org_a.id):
            db, newer, running, _ = self._estate(db_session, org_a)
            # A forged ownership row pointing an org B user at org A's
            # application must not reach that user.
            _owner(db_session, org_a.id, running.id, owner_b.id)
            service.sunset(db.id, date(2027, 3, 31), newer.id, "", cto_a.id, "/technology/radar/")
        assert Notification.query.filter(Notification.user_id == owner_b.id).count() == 0

        with tenant_ctx(org_b.id):
            b_tech = _tech(db_session, org_b.id, "B runtime")
            with pytest.raises(ValueError):
                service.sunset(db.id, date(2027, 1, 1), None, "", cto_b.id, "/technology/radar/")
            with pytest.raises(ValueError):
                service.sunset(b_tech.id, date(2027, 1, 1), newer.id, "", cto_b.id, "/technology/radar/")
            assert service.sunset_entries() == []
        db_id = db.id

        login_as(client, cto_b)
        body = client.get("/technology/radar/").get_data(as_text=True)
        assert f'data-testid="radar-sunset-{db_id}"' not in body
        assert "PostgreSQL 11" not in body
        login_as(client, cto_b)
        resp = client.post("/technology/radar/sunset", data={
            "archimate_element_id": db_id, "sunset_date": "2027-01-01",
        }, headers={"Accept": "application/json"})
        assert resp.status_code == 400
        with tenant_ctx(org_a.id):
            assert TechRadarEntry.query.filter_by(archimate_element_id=db_id).one().sunset_date == date(2027, 3, 31)


# --------------------------------------------------------------------- #
# Interfaces against the pattern catalogue                               #
# --------------------------------------------------------------------- #


class TestInterfaceCheck:
    def _setup(self, db_session, org):
        rest = _pattern(
            db_session, name=f"Approved REST {uuid.uuid4().hex[:6]}", protocol="rest", data_format="json",
            allowed_auth_methods=["oauth2", "mtls"], requires_encryption=True, allows_personal_data=False,
        )
        sol = _solution(db_session, org.id)
        a = _application(db_session, org.id, "Partner Portal")
        b = _application(db_session, org.id, "Order Service")
        good = _flow(db_session, sol, a, b, "Orders API", pattern_id=rest.id, protocol="REST",
                     data_format="json", auth_method="oauth2", encryption_required=True, contains_pii=False)
        bad = _flow(db_session, sol, b, a, "Customer sync", pattern_id=rest.id, protocol="soap",
                    data_format="json", auth_method="api_key", encryption_required=False, contains_pii=True)
        loose = _flow(db_session, sol, a, b, "Nightly dump", protocol="sftp")
        return sol, good, bad, loose

    def test_each_breach_names_the_rule_it_violates(self, app, db_session, two_orgs, tenant_ctx):
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import ConformanceReviewer

        org_a, _ = two_orgs
        with tenant_ctx(org_a.id):
            sol, good, bad, loose = self._setup(db_session, org_a)
            result = ConformanceReviewer.check_interfaces(sol.id, [good.id, bad.id, loose.id])
            assert result["success"], result
            by_id = {r["id"]: r for r in result["checked"]}
            assert by_id[good.id]["conforms"] and by_id[good.id]["breaches"] == []
            assert {b["rule"] for b in by_id[bad.id]["breaches"]} == {
                "pattern-protocol", "pattern-security-auth",
                "pattern-security-encryption", "pattern-data-personal",
            }
            for breach in by_id[bad.id]["breaches"]:
                assert breach["rule_name"] and breach["detail"]
            assert [b["rule"] for b in by_id[loose.id]["breaches"]] == ["pattern-named"]
            assert result["breaching"] == 2 and result["conforming"] == 1

    def test_unrecorded_values_are_reported_with_a_reason(self, app, db_session, two_orgs, tenant_ctx):
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import ConformanceReviewer

        org_a, _ = two_orgs
        with tenant_ctx(org_a.id):
            pattern = _pattern(db_session, protocol="rest", data_format="json", allowed_auth_methods=["oauth2"])
            sol = _solution(db_session, org_a.id)
            a = _application(db_session, org_a.id, "A")
            b = _application(db_session, org_a.id, "B")
            flow = _flow(db_session, sol, a, b, "Undescribed", pattern_id=pattern.id)
            row = ConformanceReviewer.check_interfaces(sol.id, [flow.id])["checked"][0]
            assert row["breaches"] == []
            assert {n["fact"] for n in row["not_recorded"]} == {"Protocol", "Data format", "Authentication"}
            assert all(n["reason"] for n in row["not_recorded"])

    def test_check_persists_after_reload_and_clears_after_a_fix(
        self, app, db_session, two_orgs, tenant_ctx, client, login_as
    ):
        from app.models.solution_sad_models import SolutionIntegrationFlow

        org_a, _ = two_orgs
        architect = _user(db_session, org_a.id, "Integration")
        with tenant_ctx(org_a.id):
            sol, good, bad, loose = self._setup(db_session, org_a)
        sol_id, bad_id = sol.id, bad.id

        login_as(client, architect)
        resp = client.post(f"/solutions/{sol_id}/conformance/interfaces/check",
                           data={"interface_ids": [bad_id]})
        assert resp.status_code == 302
        login_as(client, architect)
        body = client.get(f"/solutions/{sol_id}/conformance").get_data(as_text=True)
        assert f'data-testid="interface-breach-{bad_id}-pattern-protocol"' in body
        assert "The interface uses the protocol its pattern specifies" in body

        # Fix the design, check again: the breach list clears.
        with tenant_ctx(org_a.id):
            flow = SolutionIntegrationFlow.query.filter_by(id=bad_id).one()
            flow.protocol, flow.auth_method = "rest", "mtls"
            flow.encryption_required, flow.contains_pii = True, False
            db_session.flush()
        login_as(client, architect)
        client.post(f"/solutions/{sol_id}/conformance/interfaces/check", data={"interface_ids": [bad_id]})
        login_as(client, architect)
        body = client.get(f"/solutions/{sol_id}/conformance").get_data(as_text=True)
        assert f'data-testid="interface-breach-{bad_id}-' not in body
        assert "Conforms to Approved REST" in body

    def test_whole_solution_review_reports_interface_breaches(self, app, db_session, two_orgs, tenant_ctx):
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import ConformanceReviewer

        org_a, _ = two_orgs
        with tenant_ctx(org_a.id):
            sol, good, bad, loose = self._setup(db_session, org_a)
            # The review needs at least one modelled element to assess.
            from app.models.solution_models import SolutionArchiMateElement
            el = _tech(db_session, org_a.id, "Some node", "Node")
            db_session.add(SolutionArchiMateElement(
                solution_id=sol.id, element_id=el.id, layer_type="technology",
                element_table="archimate_elements", element_name=el.name,
            ))
            db_session.flush()
            review = ConformanceReviewer.review(sol.id)
            titles = [f["title"] for f in review["findings"]]
            assert "1 interface follows no catalogue pattern" in titles
            assert "Interface 'Customer sync' breaches its pattern" in titles
            assert {"check": "interfaces", "available": True} in review["checks_run"]

    def test_other_organisation_cannot_check_or_see_interfaces(
        self, app, db_session, two_orgs, tenant_ctx, client, login_as
    ):
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import ConformanceReviewer

        org_a, org_b = two_orgs
        user_b = _user(db_session, org_b.id, "IntegrationB")
        with tenant_ctx(org_a.id):
            sol, good, bad, loose = self._setup(db_session, org_a)
            ConformanceReviewer.check_interfaces(sol.id, [bad.id])
        with tenant_ctx(org_b.id):
            sol_b = _solution(db_session, org_b.id, "B solution")
            assert ConformanceReviewer.check_interfaces(sol.id, [bad.id]) == {
                "success": False, "error": "Solution not found."}
            assert ConformanceReviewer.interface_results(sol.id)["interfaces"] == []
            # A's interface id smuggled into B's own solution is not checked.
            assert ConformanceReviewer.check_interfaces(sol_b.id, [bad.id])["success"] is False
        sol_id, bad_id = sol.id, bad.id

        login_as(client, user_b)
        resp = client.post(f"/solutions/{sol_id}/conformance/interfaces/check",
                           data={"interface_ids": [bad_id]}, headers={"Accept": "application/json"})
        assert resp.status_code == 404

    def test_other_organisation_conformance_page_404s_even_with_identity_map_hit(
        self, app, db_session, two_orgs, tenant_ctx
    ):
        from flask import g
        from flask_login import login_user, logout_user

        from app.models.solution_models import Solution
        from app.modules.solutions_strategic.v2.routes.programme_routes import solution_conformance

        org_a, org_b = two_orgs
        user_b = _user(db_session, org_b.id, "IntegrationB")
        with tenant_ctx(org_a.id):
            sol, _good, _bad, _loose = self._setup(db_session, org_a)

        with app.test_request_context(f"/solutions/{sol.id}/conformance"):
            login_user(user_b)
            g.current_org_id = user_b.organization_id
            g.current_org = None
            assert db_session.get(Solution, sol.id).id == sol.id
            response = solution_conformance(sol.id)
            logout_user()

        assert isinstance(response, tuple)
        assert response[1] == 404


# --------------------------------------------------------------------- #
# Reference architectures                                                #
# --------------------------------------------------------------------- #


def _reference_architectures(db_session):
    event = _pattern(
        db_session, name=f"Event-driven {uuid.uuid4().hex[:6]}", pattern_type="event_driven",
        is_reference_architecture=True, description="Events through a broker.",
        fit_context={"data": ["internal", "personal"], "latency": ["real_time", "near_real_time"],
                     "hosting": ["cloud"]},
        components=[
            {"name": f"Event Broker {uuid.uuid4().hex[:6]}", "type": "TechnologyService", "layer": "technology"},
            {"name": f"Event Store {uuid.uuid4().hex[:6]}", "type": "DataObject", "layer": "application"},
        ],
        applies_to_controls=[{"name": "Encryption in transit", "description": "TLS everywhere."}],
    )
    batch = _pattern(
        db_session, name=f"Batch {uuid.uuid4().hex[:6]}", pattern_type="batch",
        is_reference_architecture=True,
        fit_context={"data": ["internal"], "latency": ["batch"], "hosting": ["cloud", "on_premise"]},
        components=[{"name": f"Scheduler {uuid.uuid4().hex[:6]}", "type": "SystemSoftware", "layer": "technology"}],
    )
    return event, batch


class TestReferenceArchitecture:
    def test_recommendation_ranks_by_fit_and_explains_it(self, app, db_session, two_orgs, tenant_ctx):
        from app.modules.solutions_strategic.v2.services import reference_architecture_service as ras

        org_a, _ = two_orgs
        with tenant_ctx(org_a.id):
            event, batch = _reference_architectures(db_session)
            context = ras.clean_context({"data": "personal", "latency": "near_real_time", "hosting": "cloud"})
            rec = ras.recommend(context)
            ours = [c for c in rec["candidates"] if c["id"] in (event.id, batch.id)]
            assert [c["id"] for c in ours] == [event.id, batch.id]
            top = ours[0]
            assert top["fit"]["matched"] == 3 and top["fit"]["confidence"] == "high"
            assert all(f["matches"] for f in top["fit"]["facts"])
            assert top["controls"][0]["name"] == "Encryption in transit"
            assert any(f["matches"] is False for f in ours[1]["fit"]["facts"])

            unstated = ras.recommend(ras.clean_context({"latency": "nonsense"}))
            assert unstated["stated"] == 0

    def test_apply_adds_components_and_persists_after_reload(
        self, app, db_session, two_orgs, tenant_ctx, client, login_as
    ):
        from app.models.solution_models import SolutionArchiMateElement

        org_a, _ = two_orgs
        architect = _user(db_session, org_a.id, "Solution")
        with tenant_ctx(org_a.id):
            event, _batch = _reference_architectures(db_session)
            sol = _solution(db_session, org_a.id)
        sol_id, event_id = sol.id, event.id
        component_names = {c["name"] for c in event.components}

        login_as(client, architect)
        resp = client.post(f"/solutions/{sol_id}/reference-architecture/apply", data={
            "pattern_id": event_id, "data": "personal", "latency": "real_time", "hosting": "cloud",
        })
        assert resp.status_code == 302
        # Applying twice adds nothing new.
        login_as(client, architect)
        client.post(f"/solutions/{sol_id}/reference-architecture/apply", data={"pattern_id": event_id})

        with tenant_ctx(org_a.id):
            links = SolutionArchiMateElement.query.filter(SolutionArchiMateElement.solution_id == sol_id).all()
            assert {link.element_name for link in links} == component_names
            assert all(link.spec_data["pattern_id"] == event_id for link in links)
            assert all(link.spec_data["pending_review"] for link in links)

        login_as(client, architect)
        body = client.get(f"/solutions/{sol_id}/reference-architecture?data=personal").get_data(as_text=True)
        assert f'data-testid="refarch-applied-{event_id}"' in body
        assert "Controls inherited: Encryption in transit" in body

    def test_other_organisation_cannot_apply_or_see_it(
        self, app, db_session, two_orgs, tenant_ctx, client, login_as
    ):
        from app.models.archimate_core import ArchiMateElement
        from app.modules.solutions_strategic.v2.services import reference_architecture_service as ras

        org_a, org_b = two_orgs
        user_b = _user(db_session, org_b.id, "SolutionB")
        with tenant_ctx(org_a.id):
            event, _ = _reference_architectures(db_session)
            sol = _solution(db_session, org_a.id)
            assert ras.apply(sol.id, event.id, {}, None)["success"]
            broker = event.components[0]["name"]
            assert ArchiMateElement.query.filter_by(name=broker).one().organization_id == org_a.id
        with tenant_ctx(org_b.id):
            assert ras.apply(sol.id, event.id, {}, user_b.id) == {"success": False, "error": "Solution not found."}
            # B's own solution gets B's own elements, never A's.
            sol_b = _solution(db_session, org_b.id, "B solution")
            assert ras.apply(sol_b.id, event.id, {}, user_b.id)["added"] == 2
            assert ArchiMateElement.query.filter_by(name=broker).one().organization_id == org_b.id
        sol_id = sol.id

        login_as(client, user_b)
        assert client.get(f"/solutions/{sol_id}/reference-architecture").status_code == 404
        login_as(client, user_b)
        resp = client.post(f"/solutions/{sol_id}/reference-architecture/apply",
                           data={"pattern_id": event.id}, headers={"Accept": "application/json"})
        assert resp.status_code == 404


# --------------------------------------------------------------------- #
# Principle canonical store and citation count                           #
# --------------------------------------------------------------------- #


def test_principles_table_has_one_canonical_mapping_under_fast_init():
    env = os.environ.copy()
    env["APP_FAST_INIT"] = "1"
    env["FLASK_CONFIG"] = "testing"
    completed = subprocess.run(
        [sys.executable, "-c", _CANONICAL_PRINCIPLE_SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_principle_citation_count_stays_inside_each_organisation(app, db_session, two_orgs, tenant_ctx):
    from app.commands.reconcile_schema import _reconcile
    from app.models.models import Principle

    _reconcile(dry_run=False)

    org_a, org_b = two_orgs
    with tenant_ctx(org_a.id):
        canonical = Principle(
            name="One source of truth",
            statement="Keep one authoritative record for each concept.",
            citation_count=2,
        )
        retiring = Principle(
            name="Legacy duplicate",
            statement="This principle record has been retired.",
            citation_count=1,
            retired_into=canonical,
        )
        db_session.add_all([canonical, retiring])
        db_session.flush()
        canonical_id = canonical.id
        retiring_id = retiring.id

    with tenant_ctx(org_b.id):
        other = Principle(
            name="Protect customer data",
            statement="Personal data stays within its intended boundary.",
            citation_count=7,
        )
        db_session.add(other)
        db_session.flush()
        other_id = other.id

    with tenant_ctx(org_a.id):
        rows = Principle.query.order_by(Principle.id).all()
        assert [(row.id, row.citation_count) for row in rows] == [
            (canonical_id, 2),
            (retiring_id, 1),
        ]
        retired = Principle.query.filter(Principle.id == retiring_id).one()
        assert retired.retired_into_id == canonical_id
        assert retired.retired_into.id == canonical_id

    with tenant_ctx(org_b.id):
        rows = Principle.query.order_by(Principle.id).all()
        assert [(row.id, row.citation_count) for row in rows] == [(other_id, 7)]
