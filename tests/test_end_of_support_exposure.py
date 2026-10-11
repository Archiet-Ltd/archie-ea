"""The end-of-support exposure list on the CTO scorecard.

A CTO opens one list of the applications that depend on a technology
product reaching end of support within a chosen window (default: the
organisation's configured lead time), with each application's owner and
the product's own end-of-support date. The list is a read model: it reads
VendorProduct.end_of_life_date (the technology-product register's own
field, entered or imported on the product record), reuses the alert
scanner's window arithmetic, dependents and ownership readers, and never
stores a date a second time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.modules.architecture.services.cto_scorecard_service import (
    end_of_support_exposure,
)


def _vendor_product(db_session, *, months_to_eol=None, end_of_life_date=None):
    vendor = VendorOrganization(name=f"Vendor {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    eol = end_of_life_date
    if eol is None and months_to_eol is not None:
        eol = datetime.utcnow() + timedelta(days=30 * months_to_eol)
    product = VendorProduct(
        vendor_organization_id=vendor.id,
        name=f"Product {uuid.uuid4().hex[:6]}",
        end_of_life_date=eol,
    )
    db_session.add(product)
    db_session.flush()
    return product


def _app(db_session, org, vendor_product_id, name="App"):
    from app.models.application_portfolio import ApplicationComponent

    app_component = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        vendor_product_id=vendor_product_id,
    )
    db_session.add(app_component)
    db_session.flush()
    return app_component


def _owner(db_session, org, application_id, first="Pat", last="Owner"):
    from app.models.application_owner import ApplicationOwner
    from app.models.user import User

    user = User(
        first_name=first, last_name=last,
        email=f"exposure-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ApplicationOwner(
        application_id=application_id, user_id=user.id, ownership_type="primary",
        organization_id=org.id,
    ))
    db_session.flush()


class TestEndOfSupportExposure:
    def test_products_inside_and_outside_the_window_return_exactly_the_inside_dependents_soonest_first(
        self, db_session, make_org
    ):
        org = make_org("exposure-window")
        soon = _vendor_product(db_session, months_to_eol=4)
        later = _vendor_product(db_session, months_to_eol=10)
        outside = _vendor_product(db_session, months_to_eol=30)
        soon_app = _app(db_session, org, soon.id, name="Soon")
        later_app = _app(db_session, org, later.id, name="Later")
        _app(db_session, org, outside.id, name="Outside")
        db_session.commit()

        rows = end_of_support_exposure(org.id, window_months=12)

        assert [r["application_id"] for r in rows] == [soon_app.id, later_app.id]
        # Soonest end-of-support date first.
        assert rows[0]["vendor_product_id"] == soon.id
        assert rows[0]["end_of_life_date"] == soon.end_of_life_date.date().isoformat()

    def test_each_row_carries_the_owner_summary(self, db_session, make_org):
        org = make_org("exposure-owner")
        product = _vendor_product(db_session, months_to_eol=6)
        app_component = _app(db_session, org, product.id, name="Owned")
        _owner(db_session, org, app_component.id)
        db_session.commit()

        rows = end_of_support_exposure(org.id, window_months=12)

        assert len(rows) == 1
        assert "Pat Owner" in rows[0]["owners"]

    def test_an_application_with_no_recorded_owner_reads_as_absence(self, db_session, make_org):
        org = make_org("exposure-no-owner")
        product = _vendor_product(db_session, months_to_eol=6)
        _app(db_session, org, product.id, name="Orphan")
        db_session.commit()

        rows = end_of_support_exposure(org.id, window_months=12)

        assert rows[0]["owners"] == "no owner recorded"

    def test_a_product_with_no_date_is_never_listed(self, db_session, make_org):
        org = make_org("exposure-no-date")
        product = _vendor_product(db_session, end_of_life_date=None)
        _app(db_session, org, product.id)
        db_session.commit()

        assert end_of_support_exposure(org.id, window_months=60) == []

    def test_a_window_with_no_exposure_is_empty(self, db_session, make_org):
        org = make_org("exposure-empty")
        _vendor_product(db_session, months_to_eol=30)
        db_session.commit()

        assert end_of_support_exposure(org.id, window_months=12) == []

    def test_the_default_window_is_the_organisations_lead_time(self, db_session, make_org):
        org = make_org("exposure-default-window")
        inside = _vendor_product(db_session, months_to_eol=10)
        outside = _vendor_product(db_session, months_to_eol=24)
        inside_app = _app(db_session, org, inside.id)
        _app(db_session, org, outside.id)
        db_session.commit()

        # No settings -> lead_months_for defaults to 18 months.
        rows = end_of_support_exposure(org.id)

        assert [r["application_id"] for r in rows] == [inside_app.id]

    def test_an_organisation_lead_time_override_is_honoured(self, db_session, make_org):
        org = make_org("exposure-lead-override")
        org.settings = {"alert_eol_lead_months": 6}
        inside = _vendor_product(db_session, months_to_eol=4)
        outside = _vendor_product(db_session, months_to_eol=10)
        inside_app = _app(db_session, org, inside.id)
        _app(db_session, org, outside.id)
        db_session.commit()

        rows = end_of_support_exposure(org.id)

        assert [r["application_id"] for r in rows] == [inside_app.id]

    def test_an_explicit_window_overrides_the_organisation_lead_time(self, db_session, make_org):
        org = make_org("exposure-explicit-window")
        org.settings = {"alert_eol_lead_months": 4}
        product = _vendor_product(db_session, months_to_eol=10)
        app_component = _app(db_session, org, product.id)
        db_session.commit()

        # Organisation's own lead time (4 months) would exclude this;
        # the explicit window overrides it.
        rows = end_of_support_exposure(org.id, window_months=12)

        assert [r["application_id"] for r in rows] == [app_component.id]

    @pytest.mark.parametrize("bad", [0, -1, 61, 1.5, "12", True])
    def test_window_validation_refuses_everything_outside_one_to_sixty_whole_months(
        self, db_session, make_org, bad
    ):
        org = make_org("exposure-validation")
        with pytest.raises(ValueError):
            end_of_support_exposure(org.id, window_months=bad)

    def test_two_organisations_never_see_each_others_exposure(self, db_session, make_org):
        org_a = make_org("exposure-fence-a")
        org_b = make_org("exposure-fence-b")
        product = _vendor_product(db_session, months_to_eol=6)
        _app(db_session, org_a, product.id, name="A App")
        _app(db_session, org_b, product.id, name="B App")
        db_session.commit()

        rows_a = end_of_support_exposure(org_a.id, window_months=12)
        rows_b = end_of_support_exposure(org_b.id, window_months=12)

        assert len(rows_a) == 1 and rows_a[0]["application_name"].startswith("A App")
        assert len(rows_b) == 1 and rows_b[0]["application_name"].startswith("B App")

    def test_two_organisations_never_see_each_others_owners(self, db_session, make_org):
        org_a = make_org("exposure-owner-fence-a")
        org_b = make_org("exposure-owner-fence-b")
        product = _vendor_product(db_session, months_to_eol=6)
        app_a = _app(db_session, org_a, product.id, name="A Owned")
        app_b = _app(db_session, org_b, product.id, name="B Owned")
        _owner(db_session, org_a, app_a.id, first="Alice", last="A")
        _owner(db_session, org_b, app_b.id, first="Bob", last="B")
        db_session.commit()

        rows_a = end_of_support_exposure(org_a.id, window_months=12)
        rows_b = end_of_support_exposure(org_b.id, window_months=12)

        assert "Alice A" in rows_a[0]["owners"]
        assert "Bob B" not in rows_a[0]["owners"]
        assert "Bob B" in rows_b[0]["owners"]
        assert "Alice A" not in rows_b[0]["owners"]


class TestEndOfSupportExposureSingleSourceOfTruth:
    def test_the_alert_scanner_imports_the_shared_window_not_a_copy(self):
        """Criterion: the 18-month default and the month arithmetic exist once.

        The alert scanner must bind the SAME function objects the exposure
        list uses -- a move, not a copy -- and the 18-month default must
        live in the shared module only.
        """
        import app.modules.intelligence.services.deadline_alert_service as das
        from app.services import eol_window

        assert das._add_months is eol_window.add_months
        assert das._dependents is eol_window.dependents
        assert das._owner_summary is eol_window.owner_summary
        assert das.lead_months_for is eol_window.lead_months_for
        assert not hasattr(das, "_DEFAULT_LEAD_MONTHS"), "the service must not own a duplicate constant"
        assert eol_window.DEFAULT_EOL_LEAD_MONTHS == 18

    def test_the_exposure_list_reads_one_end_of_life_date_per_product(self, db_session, make_org):
        org = make_org("exposure-one-date")
        product = _vendor_product(db_session, months_to_eol=6)
        app_one = _app(db_session, org, product.id, name="One")
        app_two = _app(db_session, org, product.id, name="Two")
        db_session.commit()

        rows = end_of_support_exposure(org.id, window_months=12)

        # One row per dependent application, all carrying the register's date.
        assert {r["application_id"] for r in rows} == {app_one.id, app_two.id}
        assert rows[0]["end_of_life_date"] == product.end_of_life_date.date().isoformat()

    def test_an_end_of_life_date_exactly_at_the_cutoff_is_included(self, db_session, make_org):
        from app.services.eol_window import add_months, lead_months_for

        org = make_org("exposure-cutoff")
        product = _vendor_product(
            db_session,
            end_of_life_date=add_months(datetime.utcnow(), lead_months_for(org.id)),
        )
        _app(db_session, org, product.id)
        db_session.commit()

        rows = end_of_support_exposure(org.id)

        assert len(rows) == 1
        assert rows[0]["vendor_product_id"] == product.id

    def test_a_soft_deleted_application_is_excluded(self, db_session, make_org):
        from datetime import datetime
        from sqlalchemy import text

        org = make_org("exposure-soft-delete")
        product = _vendor_product(db_session, months_to_eol=6)
        app_component = _app(db_session, org, product.id, name="Active")
        # Create a second app and set deleted_at via raw SQL to bypass
        # any ORM-level issues with the version_id_col lock_version.
        deleted_app = _app(db_session, org, product.id, name="Deleted")
        db_session.execute(
            text(
                "UPDATE application_components SET deleted_at = :now "
                "WHERE id = :aid"
            ),
            {"now": datetime.utcnow(), "aid": deleted_app.id},
        )
        db_session.commit()

        rows = end_of_support_exposure(org.id, window_months=12)

        assert len(rows) == 1
        assert rows[0]["application_id"] == app_component.id

        assert len(rows) == 1
        assert rows[0]["application_id"] == app_component.id


class TestEndOfSupportExposureRoute:
    def _scorecard_user(self, db_session, org, role="cto"):
        from app.models.user import User

        user = User(
            email=f"scorecard-{uuid.uuid4().hex[:8]}@example.com",
            first_name="Scorecard", last_name="User",
            organization_id=org.id, enterprise_role=role, confirmed=True,
        )
        user.password = "Sup3rSecret!123"
        db_session.add(user)
        db_session.flush()
        return user

    def test_the_scorecard_page_renders_the_empty_state_for_a_user_who_may_see_it(
        self, db_session, make_org, client, login_as
    ):
        org = make_org("exposure-page-empty")
        user = self._scorecard_user(db_session, org)
        login_as(client, user)

        response = client.get("/architecture/cto-scorecard/")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-testid="end-of-support-exposure"' in html
        assert "Nothing reaches end of support in this window" in html

    def test_the_scorecard_page_renders_no_owner_recorded_for_an_exposed_application(
        self, db_session, make_org, client, login_as
    ):
        org = make_org("exposure-page-no-owner")
        product = _vendor_product(db_session, months_to_eol=6)
        _app(db_session, org, product.id, name="Orphan App")
        db_session.commit()
        user = self._scorecard_user(db_session, org)
        login_as(client, user)

        response = client.get("/architecture/cto-scorecard/")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "no owner recorded" in html
        assert "Orphan App" in html

    def test_the_scorecard_page_lists_the_product_date_for_an_exposed_application(
        self, db_session, make_org, client, login_as
    ):
        org = make_org("exposure-page-date")
        product = _vendor_product(db_session, months_to_eol=6)
        _app(db_session, org, product.id)
        db_session.commit()
        user = self._scorecard_user(db_session, org)
        login_as(client, user)

        response = client.get("/architecture/cto-scorecard/")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert product.end_of_life_date.date().isoformat() in html

    def test_a_user_who_may_not_see_the_scorecard_is_refused(
        self, db_session, make_org, client, login_as
    ):
        org = make_org("exposure-page-forbidden")
        user = self._scorecard_user(db_session, org, role="operations")
        login_as(client, user)

        response = client.get("/architecture/cto-scorecard/")

        assert response.status_code == 403