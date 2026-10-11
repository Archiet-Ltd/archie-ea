"""R1-B39: aggregate answers catalogue + NL interpreter.

Two-organisation isolation on the catalogue entries, the NL interpreter's
own evaluation set at >=90%, and the "missing fact renders 'not recorded',
never a fabricated owner or a 0" rule.
"""
from __future__ import annotations

import uuid

import pytest


def _make_user(db_session, org, email=None):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email or f"agganswers-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(org_id, name, criticality=None, owner_user_id=None):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_owner import ApplicationOwner

    app_row = ApplicationComponent(
        name=name, organization_id=org_id, business_criticality=criticality,
    )
    db.session.add(app_row)
    db.session.flush()
    if owner_user_id is not None:
        owner = ApplicationOwner(
            application_id=app_row.id,
            user_id=owner_user_id,
            organization_id=org_id,
            ownership_type="primary",
        )
        db.session.add(owner)
        db.session.flush()
    return app_row


class TestApplicationsWithoutOwnerTwoOrgIsolation:
    def test_org_a_never_sees_org_b_unowned_applications(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org_a = make_org("agganswers-a")
        org_b = make_org("agganswers-b")
        _make_app(org_a.id, "A-unowned", criticality="Critical")
        _make_app(org_b.id, "B-unowned", criticality="Critical")

        result_a = run_entry("applications_without_owner", org_a.id)
        names_a = {row["name"] for group in result_a["groups"] for row in group["rows"]}
        assert "A-unowned" in names_a
        assert "B-unowned" not in names_a

    def test_owned_application_is_excluded(self, app, db_session, make_org):
        from app.models.user import User
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("agganswers-owned")
        user = User(email="owner@agganswers.example", organization_id=org.id)
        db_session.add(user)
        db_session.flush()
        _make_app(org.id, "Owned-app", criticality="Low", owner_user_id=user.id)
        _make_app(org.id, "Unowned-app", criticality="Low")

        result = run_entry("applications_without_owner", org.id)
        names = {row["name"] for group in result["groups"] for row in group["rows"]}
        assert "Unowned-app" in names
        assert "Owned-app" not in names

    def test_every_organisation_has_an_owner_renders_honest_reason_not_zero_fabrication(
        self, app, db_session, make_org
    ):
        from app.models.user import User
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("agganswers-allowned")
        user = User(email="owner2@agganswers.example", organization_id=org.id)
        db_session.add(user)
        db_session.flush()
        _make_app(org.id, "Fully-owned", criticality="Medium", owner_user_id=user.id)

        result = run_entry("applications_without_owner", org.id)
        assert result["total"] == 0
        assert result["reason"] == "every application in this organisation has a recorded owner"


class TestBusinessContinuityTwoOrgIsolation:
    def test_org_a_never_sees_org_b_critical_systems(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org_a = make_org("bc-a")
        org_b = make_org("bc-b")
        _make_app(org_a.id, "A-critical", criticality="Critical")
        _make_app(org_b.id, "B-critical", criticality="Critical")

        result = run_entry("business_continuity_criticality", org_a.id, criticality="Critical")
        names = {row["name"] for row in result["rows"]}
        assert "A-critical" in names
        assert "B-critical" not in names

    def test_application_with_no_owner_shows_not_recorded_not_fabricated(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("bc-no-owner")
        _make_app(org.id, "Orphan-critical", criticality="Critical")

        result = run_entry("business_continuity_criticality", org.id, criticality="Critical")
        row = next(r for r in result["rows"] if r["name"] == "Orphan-critical")
        assert row["owners"] == []
        assert row["owner_reason"] == "no recorded owner for this application"
        assert row["vendor_name"] == "not recorded"


class TestTruthClass:
    def test_truth_class_for_drawn_source_table(self):
        from app.modules.intelligence.services.query_catalogue import truth_class_for

        assert truth_class_for("application_components") == "drawn"

    def test_truth_class_for_unknown_source_table_is_derived_not_guessed(self):
        from app.modules.intelligence.services.query_catalogue import truth_class_for

        assert truth_class_for("some_future_table") == "derived"
        assert truth_class_for(None) == "derived"


class TestNLInterpreterEvaluationSet:
    def test_keyword_fallback_meets_the_90_percent_bar(self):
        """Runs the committed evaluation set against the deterministic
        fallback path (what actually runs when no LLM provider is
        configured, which is this test's own environment)."""
        from app.modules.intelligence.services.nl_query_interpreter import (
            evaluation_set,
            interpret,
        )

        cases = evaluation_set()
        correct = 0
        for case in cases:
            result = interpret(case["question"])
            if result["entry_id"] == case["expected_entry_id"]:
                correct += 1
        accuracy = correct / len(cases)
        assert accuracy >= 0.90, f"only {correct}/{len(cases)} correct ({accuracy:.0%})"

    def test_interpretation_shows_entry_and_params_before_running(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("what would stop the business trading?")
        assert result["entry_id"] == "business_continuity_criticality"
        assert result["title"] == "What would stop the business trading?"
        assert result["params"] == {"criticality": "Critical"}

    def test_unrecognised_question_returns_no_entry_not_a_guess(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("what is the meaning of life")
        assert result["entry_id"] is None
        assert result["method"] == "none"


class TestAskRouteTwoOrgIsolation:
    def test_ask_route_requires_login(self, client):
        resp = client.post("/api/v1/intelligence/ask", json={"question": "which applications have no owner?"})
        assert resp.status_code in (302, 401)

    def test_ask_route_runs_the_mapped_entry_and_returns_interpretation(
        self, app, db_session, make_org, client, login_as
    ):
        # Seed data BEFORE login_as, not after: flushing a TenantMixin row
        # after login_as's own g-cache clearing left the request unable to
        # resolve current_user (401) in this harness -- seed-then-login is
        # also the order every other passing fixture in this file uses.
        org = make_org("ask-route")
        _make_app(org.id, "Ask-route-unowned", criticality="High")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={"question": "which applications have no owner?"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert data["interpretation"]["entry_id"] == "applications_without_owner"
        names = {row["name"] for group in data["groups"] for row in group["rows"]}
        assert "Ask-route-unowned" in names

    def test_ask_route_respects_a_corrected_entry_id(self, app, db_session, make_org, client, login_as):
        org = make_org("ask-route-corrected")
        _make_app(org.id, "Corrected-critical", criticality="Critical")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text the interpreter would not map correctly",
                "entry_id": "business_continuity_criticality",
                "params": {"criticality": "Critical"},
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["interpretation"]["method"] == "corrected"
        names = {row["name"] for row in data["rows"]}
        assert "Corrected-critical" in names

    def test_ask_route_org_b_never_sees_org_a_rows(self, app, db_session, make_org, client, login_as):
        org_a = make_org("ask-isolation-a")
        org_b = make_org("ask-isolation-b")
        _make_app(org_a.id, "IsolationA-unowned", criticality="Low")
        _make_app(org_b.id, "IsolationB-unowned", criticality="Low")

        user_b = _make_user(db_session, org_b)
        login_as(client, user_b)
        resp = client.post(
            "/api/v1/intelligence/ask",
            json={"question": "which applications have no owner?"},
        )
        data = resp.get_json()["data"]
        names = {row["name"] for group in data["groups"] for row in group["rows"]}
        assert "IsolationB-unowned" in names
        assert "IsolationA-unowned" not in names


class TestAskRouteBadCorrectionBodiesNeverCrash:
    """PR 361 fix: a caller correcting the interpretation can send a
    ``params`` dict that collides with ``run_entry``'s own positional
    arguments, a ``params`` that isn't a dict at all, or an ``entry_id``
    that isn't a string. All three used to reach an unhandled TypeError
    (500) inside ``ask_nl_question``; now each is rejected with a 400, or
    (for the colliding-key case) answered normally with the dangerous key
    silently dropped -- never a 500, and never another organisation's
    rows."""

    def test_params_colliding_with_organization_id_is_dropped_not_a_500(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("ask-collide-a")
        org_b = make_org("ask-collide-b")
        _make_app(org_a.id, "CollideA-critical", criticality="Critical")
        _make_app(org_b.id, "CollideB-critical", criticality="Critical")

        user_a = _make_user(db_session, org_a)
        login_as(client, user_a)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text",
                "entry_id": "business_continuity_criticality",
                "params": {"criticality": "Critical", "organization_id": org_b.id},
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        names = {row["name"] for row in data["rows"]}
        assert "CollideA-critical" in names
        assert "CollideB-critical" not in names

    def test_params_colliding_with_entry_id_is_dropped_not_a_500(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("ask-collide-entryid")
        _make_app(org.id, "CollideEntryId-critical", criticality="Critical")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text",
                "entry_id": "business_continuity_criticality",
                "params": {"criticality": "Critical", "entry_id": "applications_without_owner"},
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        names = {row["name"] for row in data["rows"]}
        assert "CollideEntryId-critical" in names

    def test_params_not_a_dict_returns_400_not_a_500(self, app, db_session, make_org, client, login_as):
        org = make_org("ask-params-list")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text",
                "entry_id": "business_continuity_criticality",
                "params": ["not", "a", "dict"],
            },
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_PARAMS"

    def test_params_as_string_returns_400_not_a_500(self, app, db_session, make_org, client, login_as):
        org = make_org("ask-params-string")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text",
                "entry_id": "business_continuity_criticality",
                "params": "not-a-dict-either",
            },
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"]["code"] == "INVALID_PARAMS"

    def test_entry_id_not_a_string_returns_400_not_a_500(self, app, db_session, make_org, client, login_as):
        org = make_org("ask-entryid-list")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text",
                "entry_id": ["not", "a", "string"],
            },
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_ENTRY_ID"

    def test_entry_id_as_dict_returns_400_not_a_500(self, app, db_session, make_org, client, login_as):
        org = make_org("ask-entryid-dict")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text",
                "entry_id": {"not": "a string"},
            },
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"]["code"] == "INVALID_ENTRY_ID"

    def test_legitimate_correction_with_real_declared_params_still_answers(
        self, app, db_session, make_org, client, login_as
    ):
        """The fix must not silently break the working case: a correction
        naming only the entry's own declared parameter, with a string
        value, still runs and answers correctly."""
        org = make_org("ask-legit-correction")
        _make_app(org.id, "LegitCorrection-critical", criticality="Critical")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={
                "question": "irrelevant text the interpreter would not map correctly",
                "entry_id": "business_continuity_criticality",
                "params": {"criticality": "Critical"},
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert data["interpretation"]["method"] == "corrected"
        assert data["interpretation"]["params"] == {"criticality": "Critical"}
        names = {row["name"] for row in data["rows"]}
        assert "LegitCorrection-critical" in names
