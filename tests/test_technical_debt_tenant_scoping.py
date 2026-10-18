"""TechnicalDebt rows belong to exactly one organisation.

A user of organisation A cannot read or count organisation B's technical debt
rows through any listing query or service. Without the fix, TechnicalDebt is a
plain model with no organisation column, so every query returns every
organisation's rows.
"""

from __future__ import annotations

import uuid


def _app(db_session, org, label):
    from app.models.application_portfolio import ApplicationComponent

    app_obj = ApplicationComponent(
        name=f"{label} {uuid.uuid4().hex[:6]}", organization_id=org.id,
    )
    db_session.add(app_obj)
    db_session.flush()
    return app_obj


def _debt(db_session, app_obj, label):
    from app.models.software_quality import TechnicalDebt

    row = TechnicalDebt(
        name=f"{label} {uuid.uuid4().hex[:6]}",
        application_component_id=app_obj.id,
        status="open",
        priority="medium",
    )
    db_session.add(row)
    db_session.flush()
    return row


class TestTechnicalDebtTenantScoping:
    def test_each_organisation_sees_only_its_own_technical_debt_rows(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.software_quality import TechnicalDebt

        org_a = make_org("td-fence-a")
        org_b = make_org("td-fence-b")
        app_a = _app(db_session, org_a, "TD-A")
        app_b = _app(db_session, org_b, "TD-B")

        with tenant_ctx(org_a.id):
            _debt(db_session, app_a, "debt-a")
        with tenant_ctx(org_b.id):
            _debt(db_session, app_b, "debt-b")

        with tenant_ctx(org_a.id):
            # Fails on main: TechnicalDebt has no organisation column, so this
            # query returns both organisations' rows (count == 2).
            rows = TechnicalDebt.query.order_by(TechnicalDebt.id).all()
            assert len(rows) == 1
            assert rows[0].name.startswith("debt-a")

        with tenant_ctx(org_b.id):
            rows = TechnicalDebt.query.order_by(TechnicalDebt.id).all()
            assert len(rows) == 1
            assert rows[0].name.startswith("debt-b")

    def test_an_application_scoped_listing_never_returns_another_organisations_rows(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.software_quality import TechnicalDebt

        org_a = make_org("td-fence-list-a")
        org_b = make_org("td-fence-list-b")
        app_a = _app(db_session, org_a, "TD-L-A")
        app_b = _app(db_session, org_b, "TD-L-B")

        with tenant_ctx(org_a.id):
            _debt(db_session, app_a, "debt-la")
        with tenant_ctx(org_b.id):
            _debt(db_session, app_b, "debt-lb")

        with tenant_ctx(org_a.id):
            own = TechnicalDebt.query.filter_by(application_component_id=app_a.id).all()
            assert len(own) == 1

        # Organisation B listing debt for A's application sees nothing.
        with tenant_ctx(org_b.id):
            foreign = TechnicalDebt.query.filter_by(application_component_id=app_a.id).all()
            # Fails on main: returns A's row.
            assert foreign == []

    def test_counting_technical_debt_is_organisation_scoped(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.software_quality import TechnicalDebt

        org_a = make_org("td-fence-count-a")
        org_b = make_org("td-fence-count-b")
        app_a = _app(db_session, org_a, "TD-C-A")
        app_b = _app(db_session, org_b, "TD-C-B")

        with tenant_ctx(org_a.id):
            _debt(db_session, app_a, "debt-ca")
        with tenant_ctx(org_b.id):
            _debt(db_session, app_b, "debt-cb")

        with tenant_ctx(org_a.id):
            # Fails on main: counts both organisations' rows (2, not 1).
            assert TechnicalDebt.query.count() == 1
        with tenant_ctx(org_b.id):
            assert TechnicalDebt.query.count() == 1

    def test_rows_created_in_a_tenant_context_carry_that_organisation(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.software_quality import TechnicalDebt

        org = make_org("td-fence-write")
        app_obj = _app(db_session, org, "TD-W")

        with tenant_ctx(org.id):
            row = _debt(db_session, app_obj, "debt-w")
            # Fails on main: the model has no organisation column at all.
            assert row.organization_id == org.id

        # The row is reachable only from its own organisation.
        with tenant_ctx(org.id):
            assert TechnicalDebt.query.filter_by(id=row.id).count() == 1