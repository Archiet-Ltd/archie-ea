"""R1-B34: Formula register (TB-0135).

Every composite score names the reviewed formula and version it was
computed with. A score's formula_version is None (renders "—") when no
formula has been registered yet for that organisation -- never fabricated
as a plausible-looking default.
"""
from __future__ import annotations

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
