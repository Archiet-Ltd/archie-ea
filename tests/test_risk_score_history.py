"""Inherent and residual likelihood/impact are stored on a Risk, not only
displayed, and every change appends a RiskScoreHistory row -- scoped per
organisation the same as every other risk read/write in this service.

Uses the shared fixtures in tests/conftest.py.
"""

import pytest

from app.services import risk_service

pytestmark = pytest.mark.usefixtures("db_session")


def _risk(org, **overrides):
    fields = dict(
        solution_id=None, title="Vendor lock-in", description=None,
        likelihood=2, impact=2, owner=None, mitigation_plan=None,
    )
    fields.update(overrides)
    return risk_service.create_risk(**fields)


def test_setting_inherent_and_residual_scores_persists_them_on_the_risk(app, db_session, make_org, tenant_ctx):
    org = make_org("score-basic")
    with tenant_ctx(org.id):
        risk = _risk(org)

        risk_service.set_risk_score(risk.id, "inherent", likelihood=4, impact=5)
        risk_service.set_risk_score(risk.id, "residual", likelihood=2, impact=2)

        from app.models.risk import Risk
        reloaded = Risk.query.get(risk.id)
        assert (reloaded.inherent_likelihood, reloaded.inherent_impact) == (4, 5)
        assert (reloaded.residual_likelihood, reloaded.residual_impact) == (2, 2)
        # The legacy likelihood/impact pair (still read by the existing heat
        # map) is untouched by setting the new scores.
        assert (reloaded.likelihood, reloaded.impact) == (2, 2)


def test_changing_the_residual_score_appends_a_second_history_row(app, db_session, make_org, tenant_ctx):
    org = make_org("score-history")
    with tenant_ctx(org.id):
        risk = _risk(org)

        risk_service.set_risk_score(risk.id, "inherent", likelihood=4, impact=4)
        risk_service.set_risk_score(risk.id, "residual", likelihood=3, impact=3)
        # A no-op re-set (same values) must not add a spurious row.
        risk_service.set_risk_score(risk.id, "residual", likelihood=3, impact=3)

        residual_rows = risk_service.risk_score_history(risk.id, "residual")
        assert len(residual_rows) == 1
        assert (residual_rows[0].likelihood, residual_rows[0].impact) == (3, 3)

        risk_service.set_risk_score(risk.id, "residual", likelihood=2, impact=2)
        residual_rows = risk_service.risk_score_history(risk.id, "residual")
        assert len(residual_rows) == 2
        assert [(r.likelihood, r.impact) for r in residual_rows] == [(3, 3), (2, 2)]

        # The inherent row is untouched by residual changes.
        inherent_rows = risk_service.risk_score_history(risk.id, "inherent")
        assert len(inherent_rows) == 1

        all_rows = risk_service.risk_score_history(risk.id)
        assert len(all_rows) == 3


def test_invalid_score_kind_is_rejected(app, db_session, make_org, tenant_ctx):
    org = make_org("score-badkind")
    with tenant_ctx(org.id):
        risk = _risk(org)
        with pytest.raises(ValueError):
            risk_service.set_risk_score(risk.id, "residual_but_typo", likelihood=1, impact=1)


def test_score_history_is_scoped_per_organisation(app, db_session, make_org, tenant_ctx):
    """Two real organisations: organisation B's history rows never appear in
    organisation A's read, and organisation A cannot record history against
    organisation B's risk."""
    from app.models.risk import Risk
    from app.models.risk_score_history import RiskScoreHistory

    org_a, org_b = make_org("score-org-a"), make_org("score-org-b")
    with tenant_ctx(org_a.id):
        risk_a = _risk(org_a, title="Org A risk")
        risk_service.set_risk_score(risk_a.id, "inherent", likelihood=3, impact=3)
        risk_service.set_risk_score(risk_a.id, "residual", likelihood=1, impact=1)

    with tenant_ctx(org_b.id):
        risk_b = _risk(org_b, title="Org B risk")
        risk_service.set_risk_score(risk_b.id, "inherent", likelihood=5, impact=5)

        # Organisation B cannot even name organisation A's risk to score it --
        # the tenant-scoped get_or_404 makes it a clean 404, not a data leak.
        with pytest.raises(Exception):
            risk_service.set_risk_score(risk_a.id, "residual", likelihood=4, impact=4)

    # Read as an unscoped observer, not as either tenant: tenant_ctx pushes a
    # request context that shares the already-active app context (and its
    # g) db_session opened for this test, so g.current_org_id from the last
    # `with tenant_ctx(...)` block above is still set here unless cleared --
    # the same trap scripts/check_store_agreement.py's _fresh_identity exists
    # to avoid.
    from flask import g
    if hasattr(g, "current_org_id"):
        delattr(g, "current_org_id")

    # Every history row is correctly attributed; B's write never touched A's.
    a_rows = RiskScoreHistory.query.filter_by(risk_id=risk_a.id).all()
    b_rows = RiskScoreHistory.query.filter_by(risk_id=risk_b.id).all()
    assert {r.organization_id for r in a_rows} == {org_a.id}
    assert {r.organization_id for r in b_rows} == {org_b.id}
    assert len(a_rows) == 2  # inherent + residual, unaffected by org B
    assert len(b_rows) == 1

    with tenant_ctx(org_a.id):
        assert risk_service.risk_score_history(risk_a.id) != []
        with pytest.raises(Exception):
            # Organisation A cannot read organisation B's risk's history either.
            risk_service.risk_score_history(risk_b.id)

    if hasattr(g, "current_org_id"):
        delattr(g, "current_org_id")
    assert Risk.query.filter_by(id=risk_a.id).first().residual_likelihood == 1  # untouched by org B's attempt
