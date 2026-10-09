"""Validation the 30 Aug 2026 QA audit found missing where it mattered.

Two findings, same shape: a form accepted input that made the data contradict
itself, returned 200, and told the user nothing. Both are pinned here because a
validation rule with no test is a rule that survives exactly until someone
refactors the handler.
"""

import pytest

from tests.journeys.conftest import login, make_org, make_user


@pytest.fixture
def app():
    from app import create_app, db
    from app.models.user import Role

    application = create_app("testing")
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        db.create_all()
        Role.insert_roles()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------- High #7


def test_a_contract_may_not_end_before_it_starts(app, client):
    """The audit created one ending 01/01/2020 and got two contradictory views.

    "the contract's stored status field ('Active') is computed independently of,
     and never reconciled with, the separately-computed 'Expired' status shown on
     the Renewal Alerts page."

    Two screens disagreeing about the same contract is the worst failure mode for
    a system of record, and the range is nonsense on its own terms besides.
    """
    from app import db
    from app.models.application_portfolio import VendorContract

    with app.app_context():
        org_id = make_org(db, "ContractDates")
        buyer_id = make_user(db, org_id, "proc", enterprise_role="procurement",
                             role_name="Architect")
        before = db.session.execute(
            db.select(db.func.count(VendorContract.id))
        ).scalar()

    login(client, buyer_id)
    response = client.post(
        "/procurement/contracts/new",
        data={
            "contract_name": "Backwards Contract",
            "start_date": "2026-01-01",
            "end_date": "2020-01-01",
        },
    )
    assert response.status_code == 400

    # The message must name the actual problem. The catch-all it replaced said
    # "check the dates (YYYY-MM-DD)" -- formatting advice for a problem that is
    # not about formatting.
    assert "before the start date" in response.get_data(as_text=True)

    with app.app_context():
        after = db.session.execute(
            db.select(db.func.count(VendorContract.id))
        ).scalar()
        assert after == before, "a refused contract must not be written"


def test_a_valid_contract_range_still_saves(app, client):
    """Guard the guard: validation that rejects everything is not validation."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "ContractOk")
        buyer_id = make_user(db, org_id, "proc2", enterprise_role="procurement",
                             role_name="Architect")

    login(client, buyer_id)
    response = client.post(
        "/procurement/contracts/new",
        data={
            "contract_name": "Ordinary Contract",
            "start_date": "2026-01-01",
            "end_date": "2027-01-01",
        },
    )
    # A redirect to the detail page is the success path.
    assert response.status_code == 302


# --------------------------------------------------------------- High #12


def test_the_application_name_cannot_be_saved_empty(app, client):
    """The audit cleared the field, saved, and got a 200.

    app-name is the platform's own branding, rendered in the header of every
    page. There was no validation on either side, so it went blank
    platform-wide.

    /api/system-settings/save writes a genuinely global, platform-wide
    setting and is now platform_admin_required (PR 430 route-fixes split,
    v1 defect 4) -- the fixture needs the actual is_platform_admin flag, not
    just the enterprise_role="platform_admin" persona string, to reach the
    validation this test exists to prove.
    """
    with app.app_context():
        from app import db

        org_id = make_org(db, "SettingsVal")
        admin_id = make_user(db, org_id, "admin", enterprise_role="platform_admin",
                             role_name="Administrator", is_platform_admin=True)

    login(client, admin_id)

    response = client.post(
        "/api/system-settings/save", json={"settings": {"app-name": "   "}}
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert "app-name" in payload.get("fields", [])

    # And a real value still saves, plus settings with no such rule are untouched.
    assert client.post(
        "/api/system-settings/save", json={"settings": {"app-name": "Archie EA"}}
    ).status_code == 200
    assert client.post(
        "/api/system-settings/save", json={"settings": {"theme": "dark"}}
    ).status_code == 200
