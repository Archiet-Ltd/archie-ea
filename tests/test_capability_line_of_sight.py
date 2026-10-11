"""Capability line-of-sight view.

The screen answers three questions about one capability — how good are we, who
owns it, what are we doing about it — and the whole point is that it must not
answer any of them by guessing. These tests pin the five ways it could lie:

1. an assessed capability shows the levels that were actually recorded;
2. an unassessed one shows em dashes and is never rendered as Level 1
   (``maturity_assessment_date`` is the only proof an assessment happened —
   270 fabricated maturity rows were cleared from production because the
   defaults said otherwise);
3. missing ownership and missing initiatives are stated in a sentence, not
   rendered as a blank or a zero;
4. another organization's capability is not reachable at all;
5. the maturity edit page and its JSON detail do not reach another
   organisation's capability either.

Written against the shared fixtures in tests/conftest.py (``db_session``
rolls everything back), per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"los-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Line",
        last_name="Sight",
        organization_id=org.id,
        # Pass the Role instance, not role_id: User.__init__ overwrites an
        # unresolved role_id with the default role at construction time.
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _make_capability(db_session, tenant_ctx, org, **kwargs):
    """Seed a BusinessCapability inside the org's tenant context.

    Without ``tenant_ctx`` the before_flush listener has no ``g.current_org_id``
    and organization_id lands NULL, which would make every assertion below
    meaningless.
    """
    from app.models.capability_models import BusinessCapability

    with tenant_ctx(org.id):
        cap = BusinessCapability(
            name=kwargs.pop("name", f"Capability {uuid.uuid4().hex[:6]}"),
            **kwargs,
        )
        db_session.add(cap)
        db_session.flush()
        cap_id = cap.id
    return cap_id


def _get(client, login_as, user, cap_id):
    login_as(client, user)
    return client.get(f"/capability-maturity/{cap_id}/line-of-sight")


class TestMaturityIsNeverInferred:
    def test_assessed_capability_shows_the_recorded_levels(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org = make_org("los-assessed")
        user = _make_user(db_session, org)
        cap_id = _make_capability(
            db_session,
            tenant_ctx,
            org,
            name="Order Fulfilment",
            current_maturity_level=2,
            target_maturity_level=4,
            maturity_assessment_date=datetime(2026, 5, 12),
            business_owner="Priya Raman",
        )
        db_session.commit()

        with app.app_context():
            resp = _get(client, login_as, user, cap_id)

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Order Fulfilment" in body
        assert "12 May 2026" in body
        # The gap is computed from the two recorded levels, not stored blindly.
        assert "+2" in body
        assert "2 levels below where this capability needs to be." in body
        assert "This capability has never been assessed." not in body

    def test_unassessed_capability_is_not_rendered_as_level_one(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org = make_org("los-unassessed")
        user = _make_user(db_session, org)
        # Levels present but NO assessment date: the levels are not evidence,
        # so the page must ignore them rather than present them as measured.
        cap_id = _make_capability(
            db_session,
            tenant_ctx,
            org,
            name="Unassessed Capability",
            current_maturity_level=1,
            target_maturity_level=3,
            maturity_assessment_date=None,
        )
        db_session.commit()

        with app.app_context():
            resp = _get(client, login_as, user, cap_id)

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "This capability has never been assessed." in body
        # No maturity block at all — no level chips, no gap, no scale bar.
        assert "Gap to target" not in body
        assert "Assessed 0" not in body
        assert "levels below where this capability needs to be" not in body
        # The em dash carries the unknowns on the identity strip instead.
        assert "—" in body


class TestAbsenceIsAFinding:
    def test_no_owner_and_no_initiative_are_stated_explicitly(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org = make_org("los-absent")
        user = _make_user(db_session, org)
        cap_id = _make_capability(
            db_session, tenant_ctx, org, name="Unowned Capability"
        )
        db_session.commit()

        with app.app_context():
            resp = _get(client, login_as, user, cap_id)

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "No owner recorded." in body
        assert "No initiative is currently improving this capability." in body
        assert "No application is mapped to this capability." in body
        # Never a zero standing in for "not known".
        assert "0 initiatives" not in body

    def test_an_owner_suppresses_the_no_owner_finding(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org = make_org("los-owned")
        user = _make_user(db_session, org)
        cap_id = _make_capability(
            db_session,
            tenant_ctx,
            org,
            name="Owned Capability",
            business_owner="Dana Whitfield",
        )
        db_session.commit()

        with app.app_context():
            resp = _get(client, login_as, user, cap_id)

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Dana Whitfield" in body
        assert "No owner recorded." not in body
        # The half-known case is still called out rather than left blank.
        assert "No IT owner is recorded" in body


class TestInitiativesAreReal:
    def test_a_linked_work_package_is_reported_as_the_answer(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        """A work package reached through the ArchiMate backbone.

        There is no WorkPackage -> BusinessCapability FK; the link is an
        ArchiMateRelationship between the two elements, which is what the view
        traverses.
        """
        from app.models.implementation_migration import WorkPackage
        from app.models.models import ArchiMateElement, ArchiMateRelationship

        org = make_org("los-initiative")
        user = _make_user(db_session, org)

        with tenant_ctx(org.id):
            cap_el = ArchiMateElement(
                # The column is `type`, not `element_type`.
                name="Order Fulfilment",
                type="Capability",
                layer="strategy",
            )
            wp_el = ArchiMateElement(
                name="Fulfilment Replatform",
                type="WorkPackage",
                layer="implementation",
            )
            db_session.add_all([cap_el, wp_el])
            db_session.flush()

            db_session.add(
                ArchiMateRelationship(
                    type="realization", source_id=wp_el.id, target_id=cap_el.id
                )
            )
            db_session.add(
                WorkPackage(
                    name="Fulfilment Replatform",
                    archimate_element_id=wp_el.id,
                    status="in_progress",
                    priority="high",
                    target_date=date(2026, 12, 31),
                    percent_complete=40,
                )
            )
            db_session.flush()
            cap_el_id = cap_el.id

        cap_id = _make_capability(
            db_session,
            tenant_ctx,
            org,
            name="Order Fulfilment",
            archimate_element_id=cap_el_id,
        )
        db_session.commit()

        with app.app_context():
            resp = _get(client, login_as, user, cap_id)

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Fulfilment Replatform" in body
        assert "1 initiative linked to this capability." in body
        assert "40% complete" in body
        assert "No initiative is currently improving this capability." not in body


class TestTenantIsolation:
    def test_another_orgs_capability_is_not_reachable(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org_a = make_org("los-a")
        org_b = make_org("los-b")
        user_a = _make_user(db_session, org_a)
        cap_b_id = _make_capability(
            db_session, tenant_ctx, org_b, name="Foreign Capability B"
        )
        db_session.commit()

        with app.app_context():
            resp = _get(client, login_as, user_a, cap_b_id)

        # Redirected back to the heatmap — the tenant predicate filtered the row
        # out, so it is indistinguishable from one that never existed.
        assert resp.status_code in (302, 404)
        assert b"Foreign Capability B" not in resp.data

    def test_edit_page_does_not_reach_another_orgs_capability(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org_a = make_org("mat-edit-a")
        org_b = make_org("mat-edit-b")
        user_a = _make_user(db_session, org_a)
        suffix = uuid.uuid4().hex[:8]
        cap_b_id = _make_capability(
            db_session,
            tenant_ctx,
            org_b,
            name=f"Foreign maturity B-{suffix}",
            current_maturity_level=2,
            target_maturity_level=4,
            maturity_assessment_notes=f"B notes {suffix}",
        )
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.get(
                f"/capability-maturity/edit/{cap_b_id}", follow_redirects=False
            )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/capability-maturity/search")
        body = resp.data.decode()
        assert f"Foreign maturity B-{suffix}" not in body
        assert f"B notes {suffix}" not in body

        with client.session_transaction() as s:
            flashes = s.get("_flashes", [])
        assert flashes == [("error", "Capability not found")]

    def test_json_detail_does_not_reach_another_orgs_capability(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org_a = make_org("mat-api-a")
        org_b = make_org("mat-api-b")
        user_a = _make_user(db_session, org_a)
        suffix = uuid.uuid4().hex[:8]
        cap_b_id = _make_capability(
            db_session,
            tenant_ctx,
            org_b,
            name=f"Foreign maturity B-{suffix}",
            current_maturity_level=2,
            target_maturity_level=4,
            maturity_assessment_notes=f"B notes {suffix}",
        )
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.get(f"/capability-maturity/api/capability/{cap_b_id}")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Capability not found"}
        assert f"Foreign maturity B-{suffix}" not in resp.data.decode()

    def test_own_capability_is_reachable_on_both_routes(
        self, app, db_session, make_org, tenant_ctx, login_as, client
    ):
        org_a = make_org("mat-own-a")
        user_a = _make_user(db_session, org_a)
        suffix = uuid.uuid4().hex[:8]
        cap_a_id = _make_capability(
            db_session,
            tenant_ctx,
            org_a,
            name=f"Own maturity A-{suffix}",
            current_maturity_level=1,
            target_maturity_level=3,
            maturity_assessment_date=datetime(2026, 4, 1),
        )
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            edit_resp = client.get(
                f"/capability-maturity/edit/{cap_a_id}", follow_redirects=False
            )
            login_as(client, user_a)
            api_resp = client.get(f"/capability-maturity/api/capability/{cap_a_id}")

        assert edit_resp.status_code == 200
        assert f"Own maturity A-{suffix}" in edit_resp.data.decode()

        assert api_resp.status_code == 200
        payload = api_resp.get_json()
        assert set(payload.keys()) == {
            "id",
            "name",
            "business_domain",
            "description",
            "current_maturity_level",
            "target_maturity_level",
            "maturity_gap",
            "strategic_importance",
            "business_owner",
            "maturity_assessment_notes",
            "maturity_assessment_date",
        }
        assert payload["name"] == f"Own maturity A-{suffix}"
        assert payload["maturity_assessment_date"] == "2026-04-01T00:00:00"

    def test_no_tenant_context_fails_closed_on_own_capability(
        self, app, db_session, make_org, tenant_ctx, login_as, client, monkeypatch
    ):
        org_a = make_org("mat-notenant-a")
        user_a = _make_user(db_session, org_a)
        suffix = uuid.uuid4().hex[:8]
        cap_a_id = _make_capability(
            db_session,
            tenant_ctx,
            org_a,
            name=f"No tenant A-{suffix}",
            current_maturity_level=2,
            target_maturity_level=4,
        )
        db_session.commit()

        monkeypatch.setattr(
            "app.modules.capabilities.routes.maturity_routes.current_org_id",
            lambda: None,
        )

        with app.app_context():
            login_as(client, user_a)
            edit_resp = client.get(
                f"/capability-maturity/edit/{cap_a_id}", follow_redirects=False
            )
            login_as(client, user_a)
            api_resp = client.get(f"/capability-maturity/api/capability/{cap_a_id}")

        assert edit_resp.status_code == 302
        assert edit_resp.headers["Location"].endswith("/capability-maturity/search")
        assert f"No tenant A-{suffix}" not in edit_resp.data.decode()

        with client.session_transaction() as s:
            flashes = s.get("_flashes", [])
        assert flashes == [("error", "Capability not found")]

        assert api_resp.status_code == 404
        assert api_resp.get_json() == {"error": "Capability not found"}
