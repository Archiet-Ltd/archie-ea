"""Reuse tests for the account organisation switcher."""

from __future__ import annotations

import inspect

from flask import session
from flask_login import login_user

from tests.test_multi_org_account_switching import _make_user


def test_both_account_blueprints_delegate_to_the_same_switcher(
    app, db_session, make_org, monkeypatch
):
    from app.modules.account.routes import account_routes as legacy_account_routes
    from app.modules.account.services.account_service import AccountService
    from app.modules.account.v2.routes import account_routes as v2_account_routes

    org = make_org("shared-switch")
    user = _make_user(db_session, org, email="shared-switch@example.test")
    db_session.commit()

    calls = []

    def fake_switch(user, requested_org_id):
        calls.append((user.id, requested_org_id))
        return True, "Shared switch handler"

    monkeypatch.setattr(
        AccountService,
        "switch_active_organization",
        staticmethod(fake_switch),
    )

    def invoke(handler):
        with app.test_request_context(
            "/account/switch-organization",
            method="POST",
            data={"organization_id": str(org.id)},
        ):
            login_user(user)
            response = inspect.unwrap(handler)()
            return response.status_code, response.location, session.get("_flashes", [])

    legacy_result = invoke(legacy_account_routes.switch_organization)
    v2_result = invoke(v2_account_routes.switch_organization)

    assert legacy_result[0] == v2_result[0] == 302
    assert legacy_result[2] == v2_result[2] == [("success", "Shared switch handler")]
    assert legacy_result[1] in {"/account/manage", "/account/manage/info"}
    assert v2_result[1] in {"/account/manage", "/account/manage/info"}
    assert calls == [(user.id, org.id), (user.id, org.id)]
