"""Tests for the isolation sweep engine's own retry-on-missing-field
mechanism (tests/_isolation_sweep.py's _drive), not for any application
route.

Runs against a freshly created app instance, not the shared, session-scoped
``app``/``sweep_app`` fixtures: the real sweep test enumerates every rule on
the shared app's url_map, so a fake route registered there for this file
alone would pollute that count for the rest of the session. A second
``create_app("testing")`` shares the same database (same TEST_DATABASE_URL)
but has its own url_map and its own secret key, so logins here are minted
against this app specifically (see ``_login`` below), never the shared one.
"""

from __future__ import annotations

import pytest
from flask import jsonify, request
from flask_login import login_required

from tests import _isolation_sweep as sweep
from tests._session_test_helpers import mint_test_sid


@pytest.fixture
def retry_app():
    from app import create_app, db
    from app.models.user import Role

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        Role.insert_roles()
        db.session.commit()
    return app


def _login(app):
    def _do(client, user):
        from flask import g, has_app_context

        user_id = getattr(user, "id", user)
        org_id = getattr(user, "organization_id", None)
        sid = mint_test_sid(user_id, organization_id=org_id, app=app)
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
            sess["_sid"] = sid
        if not has_app_context():
            return
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)

    return _do


def _add_route(app, rule, endpoint, view_func):
    app.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=["POST"])


def test_retry_proves_a_route_that_only_needed_one_more_field(retry_app):
    from app.models.user import User

    @login_required
    def view(user_id):
        data = request.get_json(silent=True) or {}
        if "widget_name" not in data:
            return jsonify({"error": "widget_name is required"}), 400
        from flask_login import current_user
        target = User.query.get_or_404(user_id)
        if target.organization_id != current_user.organization_id:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True}), 200

    _add_route(retry_app, "/zzz-retry-proven/<int:user_id>", "zzz_retry_proven", view)
    rule = next(r for r in retry_app.url_map.iter_rules() if r.endpoint == "zzz_retry_proven")

    with sweep.world(retry_app, _login(retry_app)) as ctx:
        case = sweep.Case(rule, "POST", ["user_id"])
        case.models = {"user_id": User}
        sweep._drive(case, ctx, shared_models={})

    assert case.status == sweep.PROVEN, case.detail
    assert "retried with widget_name" in case.detail


def test_retry_still_reports_a_leak_the_extra_field_does_not_fix(retry_app):
    from app.models.user import User

    @login_required
    def view(user_id):
        data = request.get_json(silent=True) or {}
        if "widget_name" not in data:
            return jsonify({"error": "widget_name is required"}), 400
        # Deliberately no tenant check at all: reads any id from any org.
        target = User.query.get_or_404(user_id)
        return jsonify({"ok": True, "first_name": target.first_name}), 200

    _add_route(retry_app, "/zzz-retry-leak/<int:user_id>", "zzz_retry_leak", view)
    rule = next(r for r in retry_app.url_map.iter_rules() if r.endpoint == "zzz_retry_leak")

    with sweep.world(retry_app, _login(retry_app)) as ctx:
        case = sweep.Case(rule, "POST", ["user_id"])
        case.models = {"user_id": User}
        sweep._drive(case, ctx, shared_models={})

    assert case.status == sweep.LEAK, case.detail
    assert "after adding widget_name" in case.detail


def test_retry_seeds_a_real_row_for_an_id_field_sharing_a_table_already_in_context(retry_app):
    """Reproduces the DetachedInstanceError PR 340 backed away from: a second
    ``_id`` field resolving to the same model/table the URL param already
    seeded, read well after the drive's first commit and the session churn
    _request causes between requests -- exactly where the stale row in
    Seeder's ``context`` used to get dereferenced.
    """
    from app.models.user import User

    @login_required
    def view(user_id):
        data = request.get_json(silent=True) or {}
        if "assignee_id" not in data:
            return jsonify({"error": "assignee_id is required"}), 400
        from flask_login import current_user

        target = User.query.get_or_404(user_id)
        if target.organization_id != current_user.organization_id:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True}), 200

    _add_route(retry_app, "/zzz-retry-id-field/<int:user_id>", "zzz_retry_id_field", view)
    rule = next(r for r in retry_app.url_map.iter_rules() if r.endpoint == "zzz_retry_id_field")

    with sweep.world(retry_app, _login(retry_app)) as ctx:
        case = sweep.Case(rule, "POST", ["user_id"])
        case.models = {"user_id": User}
        sweep._drive(case, ctx, shared_models={}, param_models={"assignee_id": User})

    assert case.status == sweep.PROVEN, case.detail
    assert "retried with assignee_id" in case.detail


def test_retry_id_field_with_no_inferable_model_stays_unproven(retry_app):
    from app.models.user import User

    @login_required
    def view(user_id):
        data = request.get_json(silent=True) or {}
        if "widget_id" not in data:
            return jsonify({"error": "widget_id is required"}), 400
        User.query.get_or_404(user_id)
        return jsonify({"ok": True}), 200

    _add_route(retry_app, "/zzz-retry-id-unresolved/<int:user_id>", "zzz_retry_id_unresolved", view)
    rule = next(r for r in retry_app.url_map.iter_rules() if r.endpoint == "zzz_retry_id_unresolved")

    with sweep.world(retry_app, _login(retry_app)) as ctx:
        case = sweep.Case(rule, "POST", ["user_id"])
        case.models = {"user_id": User}
        sweep._drive(case, ctx, shared_models={}, param_models={})

    assert case.status == "unproven", case.detail
    assert "no model inferred for 'widget_id'" in case.detail


def test_missing_fields_reads_the_recognised_error_shapes():
    class _Resp:
        def __init__(self, data):
            self._data = data

        def get_json(self, silent=False):
            return self._data

    assert sweep._missing_fields(_Resp({"error": "justification is required"})) == ["justification"]
    assert sweep._missing_fields(_Resp({"error": "capability_id required"})) == ["capability_id"]
    assert sweep._missing_fields(_Resp({"error": "section and content are required"})) == [
        "section", "content",
    ]
    assert sweep._missing_fields(_Resp({"errors": ["risk_description is required"]})) == [
        "risk_description",
    ]
    assert sweep._missing_fields(_Resp({"success": False})) == []
    assert sweep._missing_fields(_Resp(None)) == []
