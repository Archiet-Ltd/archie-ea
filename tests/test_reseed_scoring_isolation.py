"""A reseed of one organisation scores that organisation's applications only.

Outside a request (the CLI, the demo reseed) the tenant filter is inactive, so
the scoring service must take the organisation it scores and filter on it.
The radar's 403 names the missing role only when a role is what is missing.
"""

from __future__ import annotations

import os
import uuid

import pytest
from werkzeug.exceptions import Forbidden

pytestmark = pytest.mark.usefixtures("db_session")


def _app_in(db_session, org):
    from app.models.application_portfolio import ApplicationComponent

    obj = ApplicationComponent(
        name=f"Scored {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        lifecycle_status="operational",
    )
    db_session.add(obj)
    db_session.flush()
    return obj


def _scores_for(app_id):
    from app.models.application_rationalization import ApplicationRationalizationScore

    return (
        ApplicationRationalizationScore.query.filter_by(application_component_id=app_id)
        .order_by(ApplicationRationalizationScore.id)
        .all()
    )


class TestScoringIsScopedToTheOrganisation:
    def test_scoring_one_organisation_leaves_another_untouched(
        self, app, db_session, make_org
    ):
        from app.services.rationalization_scoring_service import (
            RationalizationScoringService,
        )

        mine, theirs = make_org("seeded"), make_org("customer")
        my_app, their_app = _app_in(db_session, mine), _app_in(db_session, theirs)

        # No request context and no g.current_org_id: exactly the reseed's shape.
        result = RationalizationScoringService.calculate_portfolio_scores(mine.id)

        assert result["total_apps"] == 1
        assert len(_scores_for(my_app.id)) == 1
        assert _scores_for(their_app.id) == []

    def test_existing_scores_of_another_organisation_do_not_change(
        self, app, db_session, make_org
    ):
        from app.services.rationalization_scoring_service import (
            RationalizationScoringService,
        )

        mine, theirs = make_org("seeded2"), make_org("customer2")
        _app_in(db_session, mine)
        their_app = _app_in(db_session, theirs)
        RationalizationScoringService.calculate_portfolio_scores(theirs.id)
        before = [(s.id, s.overall_health_score, s.updated_at) for s in _scores_for(their_app.id)]
        assert before

        RationalizationScoringService.calculate_portfolio_scores(mine.id, force_recalculate=True)

        after = [(s.id, s.overall_health_score, s.updated_at) for s in _scores_for(their_app.id)]
        assert after == before

    def test_an_organisation_is_required(self):
        from app.services.rationalization_scoring_service import (
            RationalizationScoringService,
        )

        with pytest.raises(TypeError):
            RationalizationScoringService.calculate_portfolio_scores()
        with pytest.raises(ValueError):
            RationalizationScoringService.calculate_portfolio_scores(None)

    def test_the_reseed_leaves_another_organisation_unscored(self, app, db_session, make_org):
        from app.commands.seed_demo_company import seed_demo_company

        theirs = make_org("bystander")
        their_app_id = _app_in(db_session, theirs).id

        os.environ["DEMO_USER_PASSWORD"] = "test-password"
        try:
            stats = seed_demo_company()
        finally:
            del os.environ["DEMO_USER_PASSWORD"]

        assert stats["portfolio_scores_processed"] > 0
        assert _scores_for(their_app_id) == []


class TestRadarRefusalNamesTheRoleOnlyWhenMissing:
    def _viewer_client(self, app, db_session, make_org, login_as, client):
        from app.models.user import Role, User

        org = make_org("radar403")
        user = User(
            email=f"viewer-{uuid.uuid4().hex[:8]}@example.com",
            first_name="V", last_name="T", organization_id=org.id,
            confirmed=True, enterprise_role="viewer",
        )
        db_session.add(user)
        db_session.flush()
        role = Role.query.filter_by(name="Viewer").first() or Role(name="Viewer")
        db_session.add(role)
        db_session.flush()
        user.role = role
        db_session.commit()
        login_as(client, user)
        return client

    def test_missing_role_is_told_which_role(self, app, db_session, make_org, login_as, client):
        c = self._viewer_client(app, db_session, make_org, login_as, client)
        resp = c.post(
            "/technology/radar/classify",
            data={"archimate_element_id": 1, "ring": "adopt"},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 403
        assert "architect" in resp.get_json()["error"]

    def test_other_403s_do_not_claim_a_role_is_missing(self, app):
        from app.modules.tech_radar import routes

        with app.test_request_context("/technology/radar/classify", method="POST"):
            other = Forbidden()  # CSRF or another organisation's record
            assert routes._permission_denied(other) is other
