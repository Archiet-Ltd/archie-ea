"""Unmeasured scoring inputs are carried as null and rendered as a dash.

The rationalization score reads its cost input from the one cost accessor and
its risk input from the vendor/risk registers. An application with no cost
record and no risk record must score with those inputs null -- never a baseline
number that looks measured -- and the overall score must state how many of its
four inputs were actually measured.

Without the fix, `_calculate_cost_efficiency` returns a 50-baseline number and
`_calculate_vendor_risk` returns the 40 "in-house penalty" even when nothing
was measured, so both inputs render as numbers.
"""

from __future__ import annotations

from datetime import date

import uuid


def _bare_app(db_session, org):
    from app.models.application_portfolio import ApplicationComponent

    app_obj = ApplicationComponent(
        name=f"Bare {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        lifecycle_status="operational",
    )
    db_session.add(app_obj)
    db_session.flush()
    return app_obj


class TestUnmeasuredCostAndRiskInputs:
    def test_app_with_no_cost_and_no_risk_records_inputs_are_null(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("unmeasured-null")
        with tenant_ctx(org.id):
            app_obj = _bare_app(db_session, org)

            score = RationalizationScoringService.calculate_app_score(app_obj.id, app=app_obj)
            db_session.flush()

            assert score is not None
            # Fails on main: cost_efficiency_score is the 50.0 baseline and
            # vendor_risk_score is 40.0 -- numbers carved from no measurement.
            assert score.cost_efficiency_score is None
            assert score.vendor_risk_score is None
            # The overall score still exists (re-weighted over the measured
            # inputs) and the technical/business inputs are still numbers.
            assert score.overall_health_score is not None
            assert score.technical_health_score is not None
            assert score.business_value_score is not None

    def test_evidence_trail_shows_null_inputs_and_the_measured_count(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("unmeasured-trail")
        with tenant_ctx(org.id):
            app_obj = _bare_app(db_session, org)

            trail = RationalizationScoringService.get_evidence_trail(app_obj.id, app=app_obj)

            assert "error" not in trail
            # Fails on main: both are numbers (50.0 / 40.0).
            assert trail["scores"]["cost_efficiency"] is None
            assert trail["scores"]["vendor_risk"] is None
            # The score states how many of its inputs were measured.
            assert trail["inputs_measured"] == 2
            assert trail["inputs_total"] == 4
            assert "2 of 4" in trail["overall_statement"]

    def test_evidence_trail_route_returns_null_inputs_for_the_surface(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.user import Role, User

        org = make_org("unmeasured-route")
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"unmeasured.{suffix}@example.test", first_name="U", last_name="R",
            organization_id=org.id, confirmed=True, enterprise_role="architect",
        )
        db_session.add(user)
        app_obj = _bare_app(db_session, org)
        user_id, app_id = user.id, app_obj.id
        db_session.commit()
        db_session.expunge_all()

        from app.models.user import User as UserModel

        login_as(client, db_session.get(UserModel, user_id))
        response = client.get(f"/applications/rationalization/api/evidence-trail/{app_id}")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["success"] is True
        # Fails on main: cost_efficiency and vendor_risk are numbers here.
        assert payload["scores"]["cost_efficiency"] is None
        assert payload["scores"]["vendor_risk"] is None
        assert payload["inputs_measured"] == 2
        assert payload["inputs_total"] == 4

    def test_overall_weighted_only_over_measured_inputs(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("unmeasured-reweight")
        with tenant_ctx(org.id):
            app_obj = _bare_app(db_session, org)

            score = RationalizationScoringService.calculate_app_score(app_obj.id, app=app_obj)
            db_session.flush()

            # cost (25% default) and vendor (10%) are unmeasured, so the
            # overall must be (tech*30 + biz*35) / 65 -- not a plain weighted
            # sum that lets a fabricated cost number pull it down.
            expected = round(
                (score.technical_health_score * 0.3 + score.business_value_score * 0.35) / 0.65,
                2,
            )
            assert abs(float(score.overall_health_score) - expected) <= 1.0

    def test_a_recorded_cost_measures_the_cost_input(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("unmeasured-cost-ok")
        with tenant_ctx(org.id):
            app_obj = _bare_app(db_session, org)
            app_obj.total_cost_of_ownership = 30000
            db_session.flush()

            score = RationalizationScoringService.calculate_app_score(app_obj.id, app=app_obj)
            db_session.flush()

            assert score is not None
            assert score.cost_efficiency_score is not None
            # The measured input now participates in the overall score.
            assert score.vendor_risk_score is None

    def test_a_risk_register_row_measures_the_risk_input(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.risk import Risk
        from app.models.solution_models import Solution, solution_applications
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("unmeasured-risk-ok")
        with tenant_ctx(org.id):
            app_obj = _bare_app(db_session, org)
            solution = Solution(name=f"Solution {uuid.uuid4().hex[:6]}", organization_id=org.id)
            db_session.add(solution)
            db_session.flush()
            db_session.execute(
                solution_applications.insert().values(
                    solution_id=solution.id, application_component_id=app_obj.id,
                )
            )
            db_session.add(Risk(
                solution_id=solution.id, title=f"Risk {uuid.uuid4().hex[:6]}",
                likelihood=4, impact=3,
            ))
            db_session.flush()

            score = RationalizationScoringService.calculate_app_score(app_obj.id, app=app_obj)
            db_session.flush()

            assert score is not None
            # The risk register row counts as a measured risk record.
            assert score.vendor_risk_score is not None
            assert score.cost_efficiency_score is None

    def test_evidence_trail_entries_carry_a_measured_flag(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("unmeasured-flag")
        with tenant_ctx(org.id):
            app_obj = _bare_app(db_session, org)

            trail = RationalizationScoringService.get_evidence_trail(app_obj.id, app=app_obj)
            dims = {e["dimension"]: e for e in trail["evidence_trail"]}

            assert dims["cost_efficiency"]["measured"] is False
            assert dims["vendor_risk"]["measured"] is False
            assert dims["technical_health"]["measured"] is True
            assert dims["business_value"]["measured"] is True

class TestRiskInputComesFromTheRiskRows:
    def test_measured_risk_is_aggregated_from_likelihood_and_impact_not_a_constant(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("risk-aggregate")
        with tenant_ctx(org.id):
            app_obj = _bare_app(db_session, org)
            high = RationalizationScoringService._calculate_vendor_risk(
                app_obj, risk_scores_by_app={app_obj.id: [25, 25]}
            )
            low = RationalizationScoringService._calculate_vendor_risk(
                app_obj, risk_scores_by_app={app_obj.id: [1, 3]}
            )
            none = RationalizationScoringService._calculate_vendor_risk(
                app_obj, risk_scores_by_app={}
            )

        assert high["score"] == 0.0
        assert low["score"] == 92.0  # mean 2 of 25
        assert high["score"] != 40.0 and low["score"] != 40.0
        assert none["score"] is None

    def test_portfolio_scoring_reads_the_risk_register_once(
        self, app, db_session, make_org, tenant_ctx, monkeypatch
    ):
        from app.services.rationalization_scoring_service import RationalizationScoringService

        org = make_org("risk-batched")
        with tenant_ctx(org.id):
            for _ in range(3):
                _bare_app(db_session, org)
            db_session.flush()

            calls = []
            real = RationalizationScoringService._load_risk_scores

            def spy(app_ids):
                calls.append(list(app_ids))
                return real(app_ids)

            monkeypatch.setattr(RationalizationScoringService, "_load_risk_scores", staticmethod(spy))
            RationalizationScoringService.calculate_portfolio_scores(org.id, force_recalculate=True)

        assert len(calls) == 1
