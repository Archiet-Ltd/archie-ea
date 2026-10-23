"""Regression tests for the AI chat SAD-section generation route.

A code review found that ``_SAD_SECTION_SERVICE_MAP`` in
``data_interaction_routes.py`` mapped "SAD-02" to
``app.services.predictive_analytics_engine.PredictiveAnalyticsEngine
.forecast_solution_lifecycle`` — a method that never existed on that class.
Nothing else in the codebase called the engine, so the only path that ever
reached it was this map entry, and every request for SAD-02 would fail with
an ``AttributeError`` caught by the route's blanket ``except Exception`` and
surfaced as a generic 500.

The fix removes the dangling map entry (and the now-fully-unreferenced
``predictive_analytics_engine.py`` module it pointed at) and replaces it with
an explicit "not available" response, so the route never claims a forecast
capability that does not exist and never leaks an internal attribute-error
message to the caller.

These tests use a real ``app.test_client()`` request with a faked
Flask-Login session, following the pattern in
``tests/test_ba_tenant_and_authz.py``.
"""

import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org_id(db):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"SAD Test Org {suffix}", slug=f"sad-test-org-{suffix}")
    db.session.add(org)
    db.session.flush()
    db.session.commit()
    return org.id


def _make_user_id(db, org_id):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"sad-tester-{suffix}@example.com",
        first_name="Sad",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="solution_architect",
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def _login(client, user_id):
    """Fake a Flask-Login session — see tests/test_ba_tenant_and_authz.py::_login
    for why the session cookie alone is not sufficient here."""
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id, app=client.application)
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


class TestSadSectionMapHasNoBrokenReference:
    """Pure unit checks — no DB or request needed."""

    def test_predictive_analytics_engine_no_longer_shipped(self):
        """The engine was unreferenced by anything except the broken map
        entry; it has been removed rather than left as unreachable dead code
        with a misleading ML-forecasting docstring."""
        with pytest.raises(ModuleNotFoundError):
            import importlib

            importlib.import_module("app.services.predictive_analytics_engine")

    def test_sad02_not_in_working_service_map(self):
        from app.modules.ai_chat.routes.data_interaction_routes import (
            _SAD_SECTION_SERVICE_MAP,
        )

        assert "SAD-02" not in _SAD_SECTION_SERVICE_MAP

    def test_no_map_entry_references_the_missing_method(self):
        from app.modules.ai_chat.routes.data_interaction_routes import (
            _SAD_SECTION_SERVICE_MAP,
        )

        for entry in _SAD_SECTION_SERVICE_MAP.values():
            assert "forecast_solution_lifecycle" not in entry
            assert "predictive_analytics_engine" not in entry[0]

    def test_sad02_is_declared_unavailable(self):
        from app.modules.ai_chat.routes.data_interaction_routes import (
            _SAD_SECTION_UNAVAILABLE,
        )

        assert "SAD-02" in _SAD_SECTION_UNAVAILABLE
        assert _SAD_SECTION_UNAVAILABLE["SAD-02"]  # non-empty message


class TestGenerateSadSectionRoute:
    def test_anonymous_request_is_not_served(self, client):
        resp = client.post(
            "/ai-chat/data/generate-sad-section",
            json={"solution_id": 1, "sad_section": "SAD-02"},
        )
        assert resp.status_code in (302, 401)

    def test_sad02_returns_honest_not_available_response(self, app, client):
        from app import db

        with app.app_context():
            org_id = _make_org_id(db)
            user_id = _make_user_id(db, org_id)

        _login(client, user_id)

        resp = client.post(
            "/ai-chat/data/generate-sad-section",
            json={"solution_id": 1, "sad_section": "SAD-02"},
        )

        assert resp.status_code == 501
        payload = resp.get_json()
        assert payload["success"] is False
        assert payload["sad_section"] == "SAD-02"
        # The old bug leaked an AttributeError message ("... object has no
        # attribute 'forecast_solution_lifecycle'") through the route's
        # blanket exception handler. That must never happen again.
        assert "attribute" not in payload["error"].lower()
        assert "forecast_solution_lifecycle" not in payload["error"]
        assert "not implemented" in payload["error"].lower()

    def test_unsupported_section_still_reports_supported_list(self, app, client):
        from app import db

        with app.app_context():
            org_id = _make_org_id(db)
            user_id = _make_user_id(db, org_id)

        _login(client, user_id)

        resp = client.post(
            "/ai-chat/data/generate-sad-section",
            json={"solution_id": 1, "sad_section": "SAD-99"},
        )

        assert resp.status_code == 400
        payload = resp.get_json()
        assert payload["success"] is False
        assert "SAD-02" not in payload["error"]
