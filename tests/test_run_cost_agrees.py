"""An application with no recorded cost takes its run cost from its active contracts, and the
Overview and the applications list state the same total. Another organisation's contracts never count."""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest

from tests.test_vendor_pages_agree import _admin_client, _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")


def _system(db_session, org, name, cost=None):
    from app.models.application_portfolio import ApplicationComponent

    a = ApplicationComponent(name=name, organization_id=org.id, lifecycle_status="operational",
                             total_cost_of_ownership=cost)
    db_session.add(a)
    db_session.flush()
    return a


def _contract(db_session, org, app, annual):
    from app.models.application_portfolio import VendorContract

    c = VendorContract(contract_name="Deal " + uuid.uuid4().hex[:5], organization_id=org.id,
                       application_id=app.id, annual_cost=annual, status="active",
                       start_date=date.today() - timedelta(days=60))
    db_session.add(c)
    db_session.flush()


def test_run_cost_falls_back_to_active_contracts_with_its_source(db_session, make_org, tenant_ctx):
    from app.services.application_cost_accessor import get_run_cost_with_source

    mine, theirs = make_org("run-a"), make_org("run-b")
    own = _system(db_session, mine, "Own cost", cost=5000)
    viacontract = _system(db_session, mine, "Via contract")
    nothing = _system(db_session, mine, "Nothing stated")
    foreign = _system(db_session, theirs, "Their system")
    _contract(db_session, mine, viacontract, 30000.0)
    _contract(db_session, mine, viacontract, 12000.0)
    _contract(db_session, theirs, viacontract, 999999.0)  # another organisation names this system's id
    _contract(db_session, theirs, foreign, 777.0)
    with tenant_ctx(mine.id):
        assert get_run_cost_with_source(own) == (5000.0, "recorded on the application")
        assert get_run_cost_with_source(viacontract) == (42000.0, "from 2 active contracts")
        assert get_run_cost_with_source(nothing) == (None, None)


def test_overview_and_applications_list_state_the_same_run_cost(app, db_session, make_org):
    mine = make_org("run-c")
    a, b = _system(db_session, mine, "Alpha " + uuid.uuid4().hex[:4]), _system(db_session, mine, "Beta " + uuid.uuid4().hex[:4])
    _contract(db_session, mine, a, 30000.0)
    _contract(db_session, mine, b, 12500.0)
    client = _admin_client(app, db_session, mine)
    seen = {}
    for path in ("/dashboard/overview", "/applications/"):
        _forget_cached_user()
        resp = client.get(path)
        assert resp.status_code == 200, path
        seen[path] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resp.get_data(as_text=True)))
    assert "42,500" in seen["/applications/"]
    assert "Run cost: 42,500" in seen["/applications/"].replace("£", "").replace("$", "")
    assert "42,500" in seen["/dashboard/overview"] or "42.5k" in seen["/dashboard/overview"]
    # The Overview's application rows carry each application's contract cost instead of "Not recorded".
    assert "30,000" in seen["/dashboard/overview"] and "12,500" in seen["/dashboard/overview"]
