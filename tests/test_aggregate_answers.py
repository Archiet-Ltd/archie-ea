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


class TestNLInterpreterSuperlativePhrases:
    """Superlative/descriptive phrases name no specific system; the
    interpreter must treat them as "no service named" so the answer falls
    through to single points of failure without a "nothing recorded matches"
    reason line.
    """

    def test_most_connected_phrase_returns_no_service_param(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if our most connected application fails?")
        assert result["entry_id"] == "what_breaks_if_fails"
        assert "service" not in result["params"]

    def test_most_depended_on_phrase_returns_no_service_param(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if our most depended-on system fails?")
        assert result["entry_id"] == "what_breaks_if_fails"
        assert "service" not in result["params"]

    def test_most_critical_phrase_returns_no_service_param(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if our most critical service fails?")
        assert result["entry_id"] == "what_breaks_if_fails"
        assert "service" not in result["params"]

    def test_biggest_single_point_of_failure_phrase_returns_no_service_param(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if our biggest single point of failure fails?")
        assert result["entry_id"] == "what_breaks_if_fails"
        assert "service" not in result["params"]


class TestChatKeepsSuperlativeQuestions:
    def test_most_connected_question_is_answered_from_the_records_not_the_model(self):
        from app.modules.ai_chat.services.catalogue_answer import _keep
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        question = "What breaks if our most connected application fails?"
        assert _keep(question, interpret(question)) is True

    def test_unrelated_what_breaks_chat_still_goes_to_the_model(self):
        from app.modules.ai_chat.services.catalogue_answer import _keep
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        question = "What do I do if the meeting is cancelled?"
        assert _keep(question, interpret(question)) is False


class TestNLInterpreterRealSystemNames:
    def test_core_banking_is_a_name_not_a_superlative(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if Core banking fails?")
        assert result["params"].get("service") == "Core banking"

    def test_main_website_is_a_name_not_a_superlative(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if our main website fails?")
        assert result["params"].get("service") == "main website"

    def test_main_system_is_still_a_superlative(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if our main system fails?")
        assert "service" not in result["params"]

    """A genuinely named system that does not match keeps the fallback, with
    a plain line that names it. The interpreter must extract the service
    name so the catalogue entry can attempt to resolve it.
    """

    def test_payments_api_extracted_as_service(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if Payments API fails?")
        assert result["entry_id"] == "what_breaks_if_fails"
        assert result["params"].get("service") == "Payments API"

    def test_billing_platform_extracted_as_service(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("What breaks if the billing platform fails?")
        assert result["entry_id"] == "what_breaks_if_fails"
        assert result["params"].get("service") == "billing platform"


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


# ---------------------------------------------------------------------------
# Helpers for the thirty-startup-question entries (UC-S1-05, S2-01, S2-03, S2-04)
# ---------------------------------------------------------------------------


def _make_app_with_cost(org_id, name, annual_cost=None, owner_user_id=None):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_owner import ApplicationOwner

    app_row = ApplicationComponent(
        name=name, organization_id=org_id, total_cost_of_ownership=annual_cost,
    )
    db.session.add(app_row)
    db.session.flush()
    if owner_user_id is not None:
        db.session.add(ApplicationOwner(
            application_id=app_row.id, user_id=owner_user_id,
            organization_id=org_id, ownership_type="primary",
        ))
        db.session.flush()
    return app_row


def _map_capability(org_id, app_id, capability_id):
    from app import db
    from app.models.application_capability import ApplicationCapabilityMapping

    db.session.add(ApplicationCapabilityMapping(
        organization_id=org_id,
        application_component_id=app_id,
        business_capability_id=capability_id,
        support_level="primary",
        is_active=True,
    ))
    db.session.flush()


def _make_capability(org_id, name):
    from app import db
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(name=name, organization_id=org_id)
    db.session.add(cap)
    db.session.flush()
    return cap


def _make_github_element(org_id, name, repository="acme/orders", commit="1a2b3c4d5e6f"):
    """An ArchiMate element stamped as GitHub-connector-sourced, the way the
    connector itself stamps provenance."""
    from app import db
    from app.connectors.github import stamp_origin
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="application",
        organization_id=org_id,
    )
    db.session.add(el)
    db.session.flush()
    stamp_origin(el, repository=repository, commit=commit, file="go.mod")
    db.session.flush()
    return el


def _make_manual_element(org_id, name):
    from app import db
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="application",
        organization_id=org_id, source_kind="manual",
    )
    db.session.add(el)
    db.session.flush()
    return el


class TestEveryCitationHasAnOpenableTarget:
    """The brief's bar: every answer carries at least one citation whose
    target is a record a person can open. The target is checked for its
    shape here; the journey proves it actually opens with content."""

    def test_applications_without_owner_citations_point_at_the_application_record(
        self, app, db_session, make_org
    ):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("cite-noowner")
        row = _make_app(org.id, "Unowned-with-cite", criticality="High")
        result = run_entry("applications_without_owner", org.id)
        assert result["citations"], "the answer carried no citation to open"
        citation = next(c for c in result["citations"] if c["id"] == row.id)
        assert citation["target"] == f"/api/v1/applications/{row.id}"
        assert citation["type"] == "application"


class TestApplicationsAndOwnersTwoOrgIsolation:
    def test_inventory_lists_owner_and_annual_cost(self, app, db_session, make_org):
        from app.models.user import User
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("inv-a")
        user = User(email="inv-owner@example.com", first_name="Pat", last_name="Owner", organization_id=org.id)
        db_session.add(user)
        db_session.flush()
        _make_app_with_cost(org.id, "Inv-owned", annual_cost=42000, owner_user_id=user.id)

        result = run_entry("applications_and_owners", org.id)
        row = next(r for r in result["rows"] if r["name"] == "Inv-owned")
        assert row["owner"] == "Pat Owner"
        assert row["annual_cost"] == 42000
        assert result["citations"]

    def test_missing_owner_and_cost_read_as_not_recorded_not_fabricated(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("inv-missing")
        _make_app_with_cost(org.id, "Inv-bare", annual_cost=None)

        result = run_entry("applications_and_owners", org.id)
        row = next(r for r in result["rows"] if r["name"] == "Inv-bare")
        assert row["owner"] == "not recorded"
        assert row["annual_cost"] is None
        assert row["annual_cost_reason"] == "no annual cost recorded for this application"

    def test_org_a_never_sees_org_b_inventory(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org_a = make_org("inv-iso-a")
        org_b = make_org("inv-iso-b")
        _make_app_with_cost(org_a.id, "InvA-run", annual_cost=1000)
        _make_app_with_cost(org_b.id, "InvB-run", annual_cost=2000)

        result = run_entry("applications_and_owners", org_a.id)
        names = {r["name"] for r in result["rows"]}
        assert "InvA-run" in names
        assert "InvB-run" not in names


class TestDuplicateToolingByCapabilityTwoOrgIsolation:
    def test_two_applications_on_one_capability_are_flagged_with_cost(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("dup-a")
        cap = _make_capability(org.id, "Logging")
        a = _make_app_with_cost(org.id, "Logger One", annual_cost=10000)
        b = _make_app_with_cost(org.id, "Logger Two", annual_cost=15000)
        _map_capability(org.id, a.id, cap.id)
        _map_capability(org.id, b.id, cap.id)

        result = run_entry("duplicate_tooling_by_capability", org.id)
        assert result["total"] == 1
        group = result["groups"][0]
        assert group["group"] == "Logging"
        assert group["combined_annual_cost"] == 25000
        names = {r["application_name"] for r in group["rows"]}
        assert names == {"Logger One", "Logger Two"}
        assert len(result["citations"]) == 2

    def test_single_application_on_a_capability_is_not_duplicate_tooling(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("dup-single")
        cap = _make_capability(org.id, "Billing")
        a = _make_app_with_cost(org.id, "Only Biller", annual_cost=5000)
        _map_capability(org.id, a.id, cap.id)

        result = run_entry("duplicate_tooling_by_capability", org.id)
        assert result["total"] == 0
        assert result["reason"] == "no capability is covered by more than one application"

    def test_org_a_never_sees_org_b_duplicate_tooling(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org_a = make_org("dup-iso-a")
        org_b = make_org("dup-iso-b")
        cap_a = _make_capability(org_a.id, "Monitoring")
        a1 = _make_app_with_cost(org_a.id, "MonA-1", annual_cost=1)
        a2 = _make_app_with_cost(org_a.id, "MonA-2", annual_cost=2)
        _map_capability(org_a.id, a1.id, cap_a.id)
        _map_capability(org_a.id, a2.id, cap_a.id)
        cap_b = _make_capability(org_b.id, "Monitoring")
        b1 = _make_app_with_cost(org_b.id, "MonB-1", annual_cost=3)
        b2 = _make_app_with_cost(org_b.id, "MonB-2", annual_cost=4)
        _map_capability(org_b.id, b1.id, cap_b.id)
        _map_capability(org_b.id, b2.id, cap_b.id)

        result = run_entry("duplicate_tooling_by_capability", org_a.id)
        names = {r["application_name"] for g in result["groups"] for r in g["rows"]}
        assert "MonA-1" in names and "MonA-2" in names
        assert "MonB-1" not in names and "MonB-2" not in names


class TestGithubSourcedRecordsTwoOrgIsolation:
    def test_lists_only_github_connector_elements(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("gh-a")
        gh = _make_github_element(org.id, "Orders Worker")
        _make_manual_element(org.id, "Hand Drawn")

        result = run_entry("github_sourced_records", org.id)
        names = {r["name"] for r in result["rows"]}
        assert "Orders Worker" in names
        assert "Hand Drawn" not in names
        citation = next(c for c in result["citations"] if c["id"] == gh.id)
        assert citation["target"] == f"/api/v1/elements/{gh.id}"
        assert citation["type"] == "element"

    def test_no_github_records_reads_as_honest_reason_not_empty_dash(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("gh-none")
        _make_manual_element(org.id, "Only Manual")

        result = run_entry("github_sourced_records", org.id)
        assert result["total"] == 0
        assert result["reason"] == "no records in this organisation were brought in by a GitHub connector"

    def test_org_a_never_sees_org_b_github_records(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org_a = make_org("gh-iso-a")
        org_b = make_org("gh-iso-b")
        _make_github_element(org_a.id, "GhA Worker")
        _make_github_element(org_b.id, "GhB Worker")

        result = run_entry("github_sourced_records", org_a.id)
        names = {r["name"] for r in result["rows"]}
        assert "GhA Worker" in names
        assert "GhB Worker" not in names


class TestWhatBreaksIfFailsTwoOrgIsolation:
    def _seed_chain(self, org_id):
        """A service that serves a downstream element, so impact has rows."""
        from app import db
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        head = ArchiMateElement(
            name="Billing Platform", type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        tail = ArchiMateElement(
            name="Invoice Sender", type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        db.session.add_all([head, tail])
        db.session.flush()
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=head.id, target_id=tail.id, organization_id=org_id,
        ))
        db.session.flush()
        return head, tail

    def test_named_service_returns_impact_rows_with_citations(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("breaks-a")
        head, tail = self._seed_chain(org.id)

        # The impact walk reads the active organisation the way a signed-in
        # request does, so run the entry inside that organisation's context.
        with tenant_ctx(org.id):
            result = run_entry("what_breaks_if_fails", org.id, service="Billing Platform")
        assert result["service"] == "Billing Platform"
        reached = {r["name"] for r in result["rows"]}
        assert "Invoice Sender" in reached
        # The service and every reached record are openable.
        targets = {c["target"] for c in result["citations"]}
        assert f"/api/v1/elements/{head.id}" in targets
        assert f"/api/v1/elements/{tail.id}" in targets

    def test_unresolved_service_falls_back_to_single_points_of_failure(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("breaks-unknown")
        self._seed_chain(org.id)

        result = run_entry("what_breaks_if_fails", org.id, service="A Service That Does Not Exist")
        # An unmatched service name falls back to the most depended-on systems
        # rather than returning an empty "name the service" reason.
        assert "nothing recorded matches" in result["reason"]

    def test_no_service_named_returns_single_points_of_failure_with_citations(
        self, app, db_session, make_org
    ):
        """The "single points of failure" phrasing names no one service, so
        the answer is the systems the most other things depend on — each an
        openable record — not an empty "name the service" reason."""
        from app import db
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("breaks-spof")
        hub = ArchiMateElement(
            name="Shared Database", type="ApplicationComponent", layer="application",
            organization_id=org.id,
        )
        a = ArchiMateElement(name="App A", type="ApplicationComponent", layer="application", organization_id=org.id)
        b = ArchiMateElement(name="App B", type="ApplicationComponent", layer="application", organization_id=org.id)
        db.session.add_all([hub, a, b])
        db.session.flush()
        # Two things depend on the hub -> it is a single point of failure.
        db.session.add_all([
            ArchiMateRelationship(type="Serving", source_id=a.id, target_id=hub.id, organization_id=org.id),
            ArchiMateRelationship(type="Serving", source_id=b.id, target_id=hub.id, organization_id=org.id),
        ])
        db.session.flush()

        result = run_entry("what_breaks_if_fails", org.id)
        names = {r["name"] for r in result["rows"]}
        assert "Shared Database" in names
        hub_row = next(r for r in result["rows"] if r["name"] == "Shared Database")
        assert hub_row["depends_on_count"] == 2
        targets = {c["target"] for c in result["citations"]}
        assert f"/api/v1/elements/{hub.id}" in targets

    def test_processes_are_left_out_and_dependents_order_the_rows(self, app, db_session, make_org):
        """Only systems are listed; a process with more dependents than any
        system is not. Rows run from most to fewest dependents, each with its
        count and a source line that is never blank."""
        from datetime import datetime

        from app import db
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.modules.intelligence.services.query_catalogue import run_entry

        org = make_org("breaks-systems-only")
        other = make_org("breaks-systems-only-b")

        def element(name, kind, org_id, **extra):
            e = ArchiMateElement(name=name, type=kind, layer="application", organization_id=org_id, **extra)
            db.session.add(e)
            db.session.flush()
            return e

        process = element("Handle customer checkout", "BusinessProcess", org.id)
        small = element("Order Service", "ApplicationService", org.id, recorded_at=datetime(2026, 10, 24, 8, 0))
        big = element(
            "Customer Database", "DataObject", org.id,
            source_label="Application portfolio", source_recorded_at=datetime(2026, 10, 25, 9, 0),
        )
        callers = [element("Caller %d" % i, "ApplicationComponent", org.id) for i in range(5)]
        links = [(c, process) for c in callers] + [(c, big) for c in callers[:4]] + [(c, small) for c in callers[:2]]
        for caller, target in links:
            db.session.add(ArchiMateRelationship(
                type="Serving", source_id=caller.id, target_id=target.id, organization_id=org.id,
            ))
        foreign = element("Foreign Hub", "ApplicationComponent", other.id)
        foreign_callers = [element("Foreign %d" % i, "ApplicationComponent", other.id) for i in range(3)]
        for caller in foreign_callers:
            db.session.add(ArchiMateRelationship(
                type="Serving", source_id=caller.id, target_id=foreign.id, organization_id=other.id,
            ))
        db.session.flush()
        db.session.expire_all()

        result = run_entry("what_breaks_if_fails", org.id)
        names = [r["name"] for r in result["rows"]]
        assert names == ["Customer Database", "Order Service"]
        assert [r["depends_on_count"] for r in result["rows"]] == [4, 2]
        assert result["rows"][0]["source_line"] == "from Application portfolio, as of 25 Oct 2026"
        assert result["rows"][1]["source_line"] == "added by hand on 24 Oct 2026"
        from types import SimpleNamespace

        from app.modules.intelligence.services.query_catalogue import _source_line

        undated = SimpleNamespace(source_label=None, source_recorded_at=None, recorded_at=None)
        assert _source_line(undated) == "added by hand"

    def test_org_a_service_name_never_resolves_to_org_b_element(self, app, db_session, make_org):
        from app.modules.intelligence.services.query_catalogue import run_entry

        org_a = make_org("breaks-iso-a")
        org_b = make_org("breaks-iso-b")
        self._seed_chain(org_b.id)  # only org B has "Billing Platform"

        result = run_entry("what_breaks_if_fails", org_a.id, service="Billing Platform")
        assert result["total"] == 0
        assert result["citations"] == []


class TestEvaluationSetCoversAllThirtyStartupQuestions:
    def test_evaluation_set_has_all_thirty_questions_mapped(self):
        from app.modules.intelligence.services.nl_query_interpreter import evaluation_set

        cases = evaluation_set()
        answerable = [c for c in cases if c["expected_entry_id"] is not None]
        assert len(answerable) == 30, "the thirty startup questions must each map to an entry"

    def test_every_answerable_question_maps_to_a_real_entry(self):
        from app.modules.intelligence.services.nl_query_interpreter import evaluation_set, interpret
        from app.modules.intelligence.services.query_catalogue import CATALOGUE

        for case in evaluation_set():
            result = interpret(case["question"])
            assert result["entry_id"] == case["expected_entry_id"], (
                case["question"], result["entry_id"]
            )
            if case["expected_entry_id"] is not None:
                assert case["expected_entry_id"] in CATALOGUE
                assert result["method"] in ("keyword", "model")

    def test_a_question_outside_the_catalogue_still_maps_to_none(self):
        from app.modules.intelligence.services.nl_query_interpreter import interpret

        result = interpret("what is the meaning of life")
        assert result["entry_id"] is None
        assert result["method"] == "none"


class TestAskRouteAnswersTheNewEntries:
    def test_ask_route_answers_inventory_question_with_citations(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("ask-inv")
        _make_app_with_cost(org.id, "Ask-inv-app", annual_cost=1000)
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={"question": "What applications do we run and who owns each one?"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert data["interpretation"]["entry_id"] == "applications_and_owners"
        assert data["citations"]
        assert any(r["name"] == "Ask-inv-app" for r in data["rows"])

    def test_ask_route_answers_github_question_with_openable_citation(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("ask-gh")
        gh = _make_github_element(org.id, "Ask-gh Worker")
        user = _make_user(db_session, org)
        login_as(client, user)

        resp = client.post(
            "/api/v1/intelligence/ask",
            json={"question": "Which components were discovered from our GitHub repositories?"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert data["interpretation"]["entry_id"] == "github_sourced_records"
        targets = {c["target"] for c in data["citations"]}
        assert f"/api/v1/elements/{gh.id}" in targets
