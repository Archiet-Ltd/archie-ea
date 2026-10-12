"""Marking an application for retirement creates a decommission work package.

The retirement action writes exactly one work package to the one work package
store through the one writer, links it to the application, gives it a planned
end date, and surfaces it on the application page and the transformation
roadmap. Marking an application twice never creates a second row, and the
portfolio key figures on the executive dashboard count retiring applications
from those same rows. Without the fix there is no such action at all.
"""

from __future__ import annotations

import json
import uuid


def _org_user(db_session, make_org, label, role_name="Architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    org = make_org(label)
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"retire.{label}.{suffix}@example.test", first_name="R", last_name=label[:10],
        organization_id=org.id, confirmed=True, enterprise_role="architect",
    )
    user.role = Role.query.filter_by(name=role_name).one()
    db_session.add(user)
    db_session.flush()
    return org, user


def _app(db_session, org, label):
    from app.models.application_portfolio import ApplicationComponent

    app_obj = ApplicationComponent(
        name=f"{label} {uuid.uuid4().hex[:6]}", organization_id=org.id,
        lifecycle_status="operational",
    )
    db_session.add(app_obj)
    db_session.flush()
    return app_obj


def _login_read(db_session, client, login_as, user_id):
    from app.models.user import User as UserModel

    login_as(client, db_session.get(UserModel, user_id))


class TestRetirementAction:
    def test_retiring_creates_exactly_one_decommission_work_package_on_the_one_store(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.unified_work_package import UnifiedWorkPackage

        org, user = _org_user(db_session, make_org, "retire-one")
        app_obj = _app(db_session, org, "Retire-Me")
        org_id, user_id, app_id = org.id, user.id, app_obj.id
        db_session.commit()
        db_session.expunge_all()

        _login_read(db_session, client, login_as, user_id)
        response = client.post(f"/applications/{app_id}/retire")

        assert response.status_code in (302, 303)
        rows = UnifiedWorkPackage.query.filter(
            UnifiedWorkPackage.organization_id == org_id,
            UnifiedWorkPackage.application_component_id == app_id,
        ).all()
        assert len(rows) == 1
        wp = rows[0]
        assert wp.name.startswith("Decommission")
        # Linked to the application, with a planned end date, through the
        # store's own vocabulary (source_type application) plus a recorded
        # reason, and readable as the decommission work package.
        assert wp.application_component_id == app_id
        assert wp.end_date is not None
        assert wp.status == "planned"
        assert wp.source_type == "application"
        assert '"kind": "retirement"' in (wp.source_data or "")
        assert wp.description and "retirement" in wp.description.lower()

    def test_retiring_twice_never_creates_a_second_row(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.unified_work_package import UnifiedWorkPackage

        org, user = _org_user(db_session, make_org, "retire-twice")
        app_obj = _app(db_session, org, "Retire-Idem")
        org_id, user_id, app_id = org.id, user.id, app_obj.id
        db_session.commit()
        db_session.expunge_all()

        _login_read(db_session, client, login_as, user_id)
        first = client.post(f"/applications/{app_id}/retire")
        second = client.post(f"/applications/{app_id}/retire")

        assert first.status_code in (302, 303)
        assert second.status_code in (302, 303)
        count = UnifiedWorkPackage.query.filter(
            UnifiedWorkPackage.organization_id == org_id,
            UnifiedWorkPackage.application_component_id == app_id,
        ).count()
        assert count == 1

    def test_the_decommission_work_package_appears_on_the_application_page(
        self, app, db_session, make_org, client, login_as
    ):
        org, user = _org_user(db_session, make_org, "retire-page")
        app_obj = _app(db_session, org, "Retire-Visible")
        user_id, app_id = user.id, app_obj.id
        db_session.commit()
        db_session.expunge_all()

        _login_read(db_session, client, login_as, user_id)
        client.post(f"/applications/{app_id}/retire")

        page = client.get(f"/applications/{app_id}")
        assert page.status_code == 200
        assert "Decommission" in page.get_data(as_text=True)

    def test_the_decommission_work_package_appears_on_the_application_roadmap(
        self, app, db_session, make_org, client, login_as
    ):
        org, user = _org_user(db_session, make_org, "retire-roadmap")
        app_obj = _app(db_session, org, "Retire-Roadmap")
        user_id, app_id = user.id, app_obj.id
        db_session.commit()
        db_session.expunge_all()

        _login_read(db_session, client, login_as, user_id)
        client.post(f"/applications/{app_id}/retire")

        roadmap = client.get(f"/applications/{app_id}/roadmap")
        assert roadmap.status_code == 200
        assert "Decommission" in roadmap.get_data(as_text=True)

    def test_retiring_another_organisations_application_is_refused(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.unified_work_package import UnifiedWorkPackage

        org_a, user_a = _org_user(db_session, make_org, "retire-fence-a")
        org_b, _user_b = _org_user(db_session, make_org, "retire-fence-b")
        app_b = _app(db_session, org_b, "Retire-Foreign")
        org_a_id, user_a_id, app_b_id = org_a.id, user_a.id, app_b.id
        db_session.commit()
        db_session.expunge_all()

        _login_read(db_session, client, login_as, user_a_id)
        response = client.post(f"/applications/{app_b_id}/retire")

        assert response.status_code == 404
        count = UnifiedWorkPackage.query.filter(
            UnifiedWorkPackage.organization_id == org_a_id,
            UnifiedWorkPackage.application_component_id == app_b_id,
        ).count()
        assert count == 0


class TestExecutiveDashboardRetiringCount:
    def test_portfolio_key_figures_count_retiring_applications_from_the_same_rows(
        self, app, db_session, make_org, client, login_as
    ):
        org, user = _org_user(db_session, make_org, "retire-exec")
        app_a = _app(db_session, org, "Retire-Exec-A")
        app_b = _app(db_session, org, "Retire-Exec-B")
        user_id, app_a_id, app_b_id = user.id, app_a.id, app_b.id
        db_session.commit()
        db_session.expunge_all()

        _login_read(db_session, client, login_as, user_id)
        client.post(f"/applications/{app_a_id}/retire")
        client.post(f"/applications/{app_b_id}/retire")

        # The second retire call for app_a must not create a second row, so
        # the count comes from one row per retiring application.
        client.post(f"/applications/{app_a_id}/retire")

        summary = client.get("/dashboard/api/executive-summary")
        assert summary.status_code == 200
        flat = summary.get_json()["data"]
        # Fails on main: no retiring count exists in the key figures.
        assert flat["Retiring Applications"] == 2

    def test_the_count_is_organisation_scoped(
        self, app, db_session, make_org, client, login_as
    ):
        org_a, user_a = _org_user(db_session, make_org, "retire-exec-fence")
        org_b, user_b = _org_user(db_session, make_org, "retire-exec-fence-b")
        app_a = _app(db_session, org_a, "Retire-Exec-Fence-A")
        user_a_id, user_b_id, app_a_id = user_a.id, user_b.id, app_a.id
        db_session.commit()
        db_session.expunge_all()

        _login_read(db_session, client, login_as, user_a_id)
        client.post(f"/applications/{app_a_id}/retire")

        # Organisation B's executive dashboard sees none of A's retirement.
        _login_read(db_session, client, login_as, user_b_id)
        summary = client.get("/dashboard/api/executive-summary")
        assert summary.status_code == 200
        flat = summary.get_json()["data"]
        assert flat["Retiring Applications"] == 0