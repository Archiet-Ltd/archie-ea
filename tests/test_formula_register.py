"""R1-B34: Formula register (TB-0135).

Every composite score names the reviewed formula and version it was
computed with. A score's formula_version is None (renders "—") when no
formula has been registered yet for that organisation -- never fabricated
as a plausible-looking default.
"""
from __future__ import annotations

import re
import uuid

from app.models.formula_register import FormulaRegister


def _weights():
    return {"technical_health": 0.3, "business_value": 0.3, "cost_efficiency": 0.2, "vendor_risk": 0.2}


class TestFormulaRegisterVersioning:
    def test_activate_new_version_starts_at_one(self, app, db_session, make_org):
        org = make_org("formula-v1")
        row = FormulaRegister.activate_new_version(
            org.id, "rationalization_overall", inputs=_weights(),
        )
        db_session.flush()
        assert row.version == 1
        assert row.is_active is True
        assert FormulaRegister.active_for(org.id, "rationalization_overall").id == row.id

    def test_activate_new_version_deactivates_the_previous_one(self, app, db_session, make_org):
        org = make_org("formula-v2")
        first = FormulaRegister.activate_new_version(
            org.id, "rationalization_overall", inputs=_weights(),
        )
        db_session.flush()
        second = FormulaRegister.activate_new_version(
            org.id, "rationalization_overall", inputs=_weights(),
        )
        db_session.flush()

        assert second.version == 2
        db_session.refresh(first)
        assert first.is_active is False
        active = FormulaRegister.active_for(org.id, "rationalization_overall")
        assert active.id == second.id

    def test_no_registered_formula_returns_none_not_a_default(self, app, db_session, make_org):
        org = make_org("formula-none")
        assert FormulaRegister.active_for(org.id, "rationalization_overall") is None

    def test_two_organisations_formula_versions_never_cross(self, app, db_session, make_org):
        org_a = make_org("formula-fence-a")
        org_b = make_org("formula-fence-b")
        FormulaRegister.activate_new_version(org_a.id, "rationalization_overall", inputs=_weights())
        db_session.flush()

        assert FormulaRegister.active_for(org_b.id, "rationalization_overall") is None


class TestScoreRecordsFormulaVersion:
    def test_a_score_records_the_active_formula_version(self, app, db_session, make_org, tenant_ctx):
        from app.models.application_portfolio import ApplicationComponent
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("formula-score")
        FormulaRegister.activate_new_version(org.id, "rationalization_overall", inputs=_weights())
        db_session.flush()

        with tenant_ctx(org.id):
            comp = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
            db_session.add(comp)
            db_session.flush()

            score = RationalizationScoringService.calculate_app_score(comp.id, app=comp)
            db_session.flush()

            assert score.formula_version == 1

    def test_evidence_trail_agrees_with_the_saved_score_when_a_formula_is_active(
        self, app, db_session, make_org, tenant_ctx
    ):
        """Review finding A: get_evidence_trail must re-derive the same
        overall score calculate_app_score saved, not quietly fall back to
        ScoringConfiguration's weights while a formula is active."""
        from app.models.application_portfolio import ApplicationComponent
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("formula-trail-agreement")
        # Deliberately NOT ScoringConfiguration's own default split, so the
        # two surfaces could only agree by both reading the formula.
        FormulaRegister.activate_new_version(
            org.id, "rationalization_overall",
            inputs={"technical_health": 0.7, "business_value": 0.1, "cost_efficiency": 0.1, "vendor_risk": 0.1},
        )
        db_session.flush()

        with tenant_ctx(org.id):
            comp = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
            db_session.add(comp)
            db_session.flush()

            score = RationalizationScoringService.calculate_app_score(comp.id, app=comp)
            db_session.flush()
            assert score.formula_version == 1

            trail = RationalizationScoringService.get_evidence_trail(comp.id, app=comp)
            # overall_health_score is stored as an Integer column (a
            # pre-existing storage choice, not something this PR touches),
            # so compare at that same precision rather than against the
            # trail's unrounded float.
            assert round(trail["overall_score"]) == score.overall_health_score

    def test_an_incomplete_registered_formula_is_never_used_or_recorded(
        self, app, db_session, make_org, tenant_ctx
    ):
        """A registered formula missing a required dimension must not be
        treated as the weights used -- recording its version would name a
        formula that did not actually produce the score."""
        from app.models.application_portfolio import ApplicationComponent
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("formula-score-incomplete")
        FormulaRegister.activate_new_version(
            org.id, "rationalization_overall", inputs={"technical_health": 1.0},
        )
        db_session.flush()

        with tenant_ctx(org.id):
            comp = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
            db_session.add(comp)
            db_session.flush()

            score = RationalizationScoringService.calculate_app_score(comp.id, app=comp)
            db_session.flush()

            assert score.formula_version is None

    def test_an_unregistered_formula_leaves_the_score_version_none(self, app, db_session, make_org, tenant_ctx):
        from app.models.application_portfolio import ApplicationComponent
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("formula-score-none")

        with tenant_ctx(org.id):
            comp = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
            db_session.add(comp)
            db_session.flush()

            score = RationalizationScoringService.calculate_app_score(comp.id, app=comp)
            db_session.flush()

            assert score.formula_version is None


def _org_with_user(db_session, make_org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    architect_role = Role.query.filter_by(name="Architect").one()
    org = make_org(label)
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"formula.{label}.{suffix}@example.com", first_name="Formula",
        last_name=label[:12], organization_id=org.id, enterprise_role="portfolio_manager",
        confirmed=True,
    )
    user.role = architect_role
    db_session.add(user)
    db_session.flush()
    return org, user


class TestFormulaRegisterRoutes:
    def test_index_renders_no_version_state_then_activate_creates_one(
        self, app, db_session, make_org, client, login_as
    ):
        org, user = _org_with_user(db_session, make_org, "route-activate")
        db_session.commit()
        login_as(client, user)

        resp = client.get("/admin/formula-register/")
        assert resp.status_code == 200
        assert b"No version registered yet" in resp.data

        # Pull a fresh CSRF token off the rendered page rather than
        # reimplementing Flask-WTF's token generation.
        token_match = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
        assert token_match, "page did not render a csrf_token field"
        token = token_match.group(1).decode()

        resp2 = client.post(
            "/admin/formula-register/rationalization_overall/new-version",
            data={
                "csrf_token": token,
                "input_name": ["technical_health", "business_value", "cost_efficiency", "vendor_risk"],
                "input_weight": ["0.3", "0.3", "0.2", "0.2"],
            },
        )
        assert resp2.status_code == 302

        from app.models.formula_register import FormulaRegister

        active = FormulaRegister.active_for(org.id, "rationalization_overall")
        assert active is not None
        assert active.version == 1
        assert active.inputs == {
            "technical_health": 0.3, "business_value": 0.3, "cost_efficiency": 0.2, "vendor_risk": 0.2,
        }

    def test_a_partial_submission_is_refused_not_silently_activated(
        self, app, db_session, make_org, client, login_as
    ):
        """Review finding C: a version missing a dimension must never become
        active -- it would show as "Active version" on the page while the
        scorer silently ignores it and falls back to ScoringConfiguration."""
        import re as _re

        org, user = _org_with_user(db_session, make_org, "route-partial")
        db_session.commit()
        login_as(client, user)

        resp = client.get("/admin/formula-register/")
        token = _re.search(rb'name="csrf_token" value="([^"]+)"', resp.data).group(1).decode()

        resp2 = client.post(
            "/admin/formula-register/rationalization_overall/new-version",
            data={
                "csrf_token": token,
                "input_name": ["technical_health"],
                "input_weight": ["0.6"],
            },
        )
        assert resp2.status_code == 302

        from app.models.formula_register import FormulaRegister

        assert FormulaRegister.active_for(org.id, "rationalization_overall") is None

    def test_weights_must_sum_to_one(self, app, db_session, make_org, client, login_as):
        """Review finding B: these weights are applied directly by the
        scorer, not normalised as a percentage -- an unnormalised set must
        be refused rather than silently distort every score."""
        import re as _re

        org, user = _org_with_user(db_session, make_org, "route-sum")
        db_session.commit()
        login_as(client, user)

        resp = client.get("/admin/formula-register/")
        token = _re.search(rb'name="csrf_token" value="([^"]+)"', resp.data).group(1).decode()

        resp2 = client.post(
            "/admin/formula-register/rationalization_overall/new-version",
            data={
                "csrf_token": token,
                "input_name": ["technical_health", "business_value", "cost_efficiency", "vendor_risk"],
                "input_weight": ["30", "30", "20", "20"],  # percentage convention, not normalised
            },
        )
        assert resp2.status_code == 302

        from app.models.formula_register import FormulaRegister

        assert FormulaRegister.active_for(org.id, "rationalization_overall") is None

    def test_a_non_portfolio_manager_cannot_activate_a_new_version(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.user import Role, User

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = make_org("route-role-fence")
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"formula.procurement.{suffix}@example.com", first_name="Formula",
            last_name="procurement", organization_id=org.id, enterprise_role="procurement",
            confirmed=True,
        )
        user.role = architect_role
        db_session.add(user)
        db_session.commit()
        login_as(client, user)

        resp = client.get("/admin/formula-register/")
        token_match = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
        token = token_match.group(1).decode()

        resp2 = client.post(
            "/admin/formula-register/rationalization_overall/new-version",
            data={
                "csrf_token": token,
                "input_name": ["technical_health"],
                "input_weight": ["1.0"],
            },
        )
        assert resp2.status_code == 403

        from app.models.formula_register import FormulaRegister

        assert FormulaRegister.active_for(org.id, "rationalization_overall") is None

    def test_two_organisations_page_never_shows_the_others_version(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.formula_register import FormulaRegister

        org_a, user_a = _org_with_user(db_session, make_org, "route-fence-a")
        org_b, user_b = _org_with_user(db_session, make_org, "route-fence-b")
        FormulaRegister.activate_new_version(
            org_a.id, "rationalization_overall", inputs={"technical_health": 1.0},
        )
        db_session.commit()

        login_as(client, user_b)
        resp = client.get("/admin/formula-register/")
        assert resp.status_code == 200
        assert b"No version registered yet" in resp.data
