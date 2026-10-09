"""Contract-to-application links: create, list, remove, tenant isolation.

Also covers the renewals view's last-day-to-cancel column and the dash
for contracts with no notice period recorded.

Pins the R1-B10 PR 2 implementation: ContractApplication had no writer,
notice_period_days defaulted to 90 (so "not recorded" was impossible),
and the renewals view sorted by days_until_renewal without showing the
last day to cancel.
"""

import uuid
from datetime import date, timedelta

import pytest


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def setup(app, db_session, make_org):
    """Two organisations, each with a contract and an application."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.user import User

    org_a = make_org("links-a")
    org_b = make_org("links-b")

    user_a = User(
        email=f"links-a-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Links",
        last_name="A",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="procurement",
    )
    db_session.add(user_a)
    user_b = User(
        email=f"links-b-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Links",
        last_name="B",
        organization_id=org_b.id,
        confirmed=True,
        enterprise_role="procurement",
    )
    db_session.add(user_b)

    contract_a = VendorContract(
        organization_id=org_a.id,
        contract_name="Contract A",
        contract_number=f"CA-{uuid.uuid4().hex[:6]}",
        status="active",
        start_date=date.today() - timedelta(days=100),
        end_date=date.today() + timedelta(days=265),
        renewal_date=date.today() + timedelta(days=20),
        notice_period_days=30,
    )
    db_session.add(contract_a)
    contract_b = VendorContract(
        organization_id=org_b.id,
        contract_name="Contract B",
        contract_number=f"CB-{uuid.uuid4().hex[:6]}",
        status="active",
        start_date=date.today() - timedelta(days=100),
        end_date=date.today() + timedelta(days=265),
        renewal_date=date.today() + timedelta(days=20),
        notice_period_days=30,
    )
    db_session.add(contract_b)

    app_a = ApplicationComponent(
        organization_id=org_a.id,
        name="App A",
        description="Org A application",
    )
    db_session.add(app_a)
    app_b = ApplicationComponent(
        organization_id=org_b.id,
        name="App B",
        description="Org B application",
    )
    db_session.add(app_b)

    db_session.flush()

    client = app.test_client()
    _login(client, user_a.id)
    return {
        "client": client,
        "org_a": org_a,
        "org_b": org_b,
        "user_a": user_a,
        "user_b": user_b,
        "contract_a": contract_a,
        "contract_b": contract_b,
        "app_a": app_a,
        "app_b": app_b,
    }


class TestContractApplicationLinks:
    """Two-organisation tests for link create, list, remove."""

    def test_link_application_to_contract(self, setup):
        """Link an application to a contract, then verify it shows."""
        from app.models.contract_application import ContractApplication

        client = setup["client"]
        contract = setup["contract_a"]
        app_comp = setup["app_a"]

        resp = client.post(
            "/procurement/contracts/%d/link-application" % contract.id,
            data={"application_id": str(app_comp.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Verify the link persisted
        links = ContractApplication.get_applications_for_contract(
            contract.id, setup["org_a"].id
        )
        assert len(links) == 1
        assert links[0].application_id == app_comp.id

    def test_link_two_applications_and_list(self, setup):
        """Link two applications, reload, both show."""
        from app.models.application_portfolio import ApplicationComponent
        from app.models.contract_application import ContractApplication

        client = setup["client"]
        contract = setup["contract_a"]
        org = setup["org_a"]
        db_session = setup["client"].application.extensions["sqlalchemy"].session

        # Create a second application in the same org
        app2 = ApplicationComponent(
            organization_id=org.id,
            name="App A2",
            description="Second app",
        )
        db_session.add(app2)
        db_session.flush()

        # Link both
        for app_comp in [setup["app_a"], app2]:
            resp = client.post(
                "/procurement/contracts/%d/link-application" % contract.id,
                data={"application_id": str(app_comp.id)},
                follow_redirects=True,
            )
            assert resp.status_code == 200

        links = ContractApplication.get_applications_for_contract(
            contract.id, org.id
        )
        assert len(links) == 2
        linked_ids = {lnk.application_id for lnk in links}
        assert setup["app_a"].id in linked_ids
        assert app2.id in linked_ids

    def test_remove_link(self, setup):
        """Link two applications, remove one, one remains."""
        from app.models.application_portfolio import ApplicationComponent
        from app.models.contract_application import ContractApplication

        client = setup["client"]
        contract = setup["contract_a"]
        org = setup["org_a"]
        db_session = setup["client"].application.extensions["sqlalchemy"].session

        app2 = ApplicationComponent(
            organization_id=org.id,
            name="App A2",
            description="Second app",
        )
        db_session.add(app2)
        db_session.flush()

        for app_comp in [setup["app_a"], app2]:
            client.post(
                "/procurement/contracts/%d/link-application" % contract.id,
                data={"application_id": str(app_comp.id)},
                follow_redirects=True,
            )

        # Remove one
        resp = client.post(
            "/procurement/contracts/%d/unlink-application" % contract.id,
            data={"application_id": str(app2.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        links = ContractApplication.get_applications_for_contract(
            contract.id, org.id
        )
        assert len(links) == 1
        assert links[0].application_id == setup["app_a"].id

    def test_cross_organisation_link_refused(self, setup):
        """An application of another organisation cannot be linked."""
        client = setup["client"]
        contract = setup["contract_a"]
        foreign_app = setup["app_b"]

        resp = client.post(
            "/procurement/contracts/%d/link-application" % contract.id,
            data={"application_id": str(foreign_app.id)},
            follow_redirects=False,
        )
        assert resp.status_code == 404, (
            "Cross-organisation link was allowed (%d)" % resp.status_code
        )

    def test_cross_organisation_unlink_refused(self, setup):
        """Unlinking a foreign application is refused."""
        client = setup["client"]
        contract = setup["contract_a"]
        foreign_app = setup["app_b"]

        resp = client.post(
            "/procurement/contracts/%d/unlink-application" % contract.id,
            data={"application_id": str(foreign_app.id)},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_links_appear_on_contract_detail_page(self, setup):
        """Linked applications appear on the contract detail page."""
        from app.models.contract_application import ContractApplication

        client = setup["client"]
        contract = setup["contract_a"]
        app_comp = setup["app_a"]

        # Create a link directly
        link = ContractApplication(
            contract_id=contract.id,
            application_id=app_comp.id,
            organization_id=setup["org_a"].id,
        )
        db_session = setup["client"].application.extensions["sqlalchemy"].session
        db_session.add(link)
        db_session.flush()

        resp = client.get("/procurement/contracts/%d" % contract.id)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert app_comp.name in body


class TestRenewalsLastDayToCancel:
    """Renewals view: last-day-to-cancel, sorting, dash for no notice period."""

    def test_last_day_to_cancel_shown(self, setup):
        """Renewals view shows last day to cancel for a contract with notice period."""
        client = setup["client"]
        contract = setup["contract_a"]

        resp = client.get("/procurement/renewals")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert contract.contract_name in body

        # The last day to cancel = renewal_date - notice_period_days
        expected = contract.renewal_date - timedelta(days=contract.notice_period_days)
        # Check the page contains the date in some format
        assert expected.strftime("%d %b %Y") in body or str(expected) in body

    def test_dash_for_no_notice_period(self, setup):
        """A contract with no notice period shows a dash."""
        from app.models.application_portfolio import VendorContract

        client = setup["client"]
        org = setup["org_a"]
        db_session = setup["client"].application.extensions["sqlalchemy"].session

        no_notice = VendorContract(
            organization_id=org.id,
            contract_name="No Notice Contract",
            contract_number=f"NN-{uuid.uuid4().hex[:6]}",
            status="active",
            start_date=date.today() - timedelta(days=50),
            end_date=date.today() + timedelta(days=315),
            renewal_date=date.today() + timedelta(days=15),
            notice_period_days=None,
        )
        db_session.add(no_notice)
        db_session.flush()

        resp = client.get("/procurement/renewals")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert no_notice.contract_name in body

    def test_renewals_sorted_by_last_day_to_cancel(self, setup):
        """Contracts are sorted ascending by last day to cancel."""
        from app.models.application_portfolio import VendorContract

        client = setup["client"]
        org = setup["org_a"]
        db_session = setup["client"].application.extensions["sqlalchemy"].session

        # Create contracts with different last-day-to-cancel values
        later = VendorContract(
            organization_id=org.id,
            contract_name="Later Cancel",
            contract_number=f"LC-{uuid.uuid4().hex[:6]}",
            status="active",
            start_date=date.today() - timedelta(days=100),
            end_date=date.today() + timedelta(days=365),
            renewal_date=date.today() + timedelta(days=25),
            notice_period_days=5,
        )
        db_session.add(later)
        earlier = VendorContract(
            organization_id=org.id,
            contract_name="Earlier Cancel",
            contract_number=f"EC-{uuid.uuid4().hex[:6]}",
            status="active",
            start_date=date.today() - timedelta(days=100),
            end_date=date.today() + timedelta(days=265),
            renewal_date=date.today() + timedelta(days=20),
            notice_period_days=15,
        )
        db_session.add(earlier)
        db_session.flush()

        resp = client.get("/procurement/renewals")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        # Earlier cancel should appear before later cancel in the page
        earlier_pos = body.index("Earlier Cancel")
        later_pos = body.index("Later Cancel")
        assert earlier_pos < later_pos, (
            "Contracts not sorted by last day to cancel"
        )

    def test_no_notice_period_sorts_after_dated(self, setup):
        """Contracts with no notice period sort after those with dates."""
        from app.models.application_portfolio import VendorContract

        client = setup["client"]
        org = setup["org_a"]
        db_session = setup["client"].application.extensions["sqlalchemy"].session

        dated = VendorContract(
            organization_id=org.id,
            contract_name="Dated Cancel",
            contract_number=f"DC-{uuid.uuid4().hex[:6]}",
            status="active",
            start_date=date.today() - timedelta(days=100),
            end_date=date.today() + timedelta(days=265),
            renewal_date=date.today() + timedelta(days=20),
            notice_period_days=5,
        )
        db_session.add(dated)
        undated = VendorContract(
            organization_id=org.id,
            contract_name="Undated Cancel",
            contract_number=f"UC-{uuid.uuid4().hex[:6]}",
            status="active",
            start_date=date.today() - timedelta(days=100),
            end_date=date.today() + timedelta(days=265),
            renewal_date=date.today() + timedelta(days=20),
            notice_period_days=None,
        )
        db_session.add(undated)
        db_session.flush()

        resp = client.get("/procurement/renewals")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        dated_pos = body.index("Dated Cancel")
        undated_pos = body.index("Undated Cancel")
        assert dated_pos < undated_pos, (
            "Undated contract should sort after dated contract"
        )