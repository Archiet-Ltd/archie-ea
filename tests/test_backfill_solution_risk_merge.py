"""flask backfill-solution-risk-merge copies solution_risks into the one
risk register (risks + risk_entity_links), idempotently, quarantining any row
with no organisation rather than guessing one.

Uses the shared fixtures in tests/conftest.py.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _solution(db_session, org, name="Backfill Solution"):
    from app.models.solution_models import Solution

    solution = Solution(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _solution_risk(db_session, org, solution, **overrides):
    from app.models.solution_lifecycle_models import SolutionRisk

    fields = dict(
        solution_id=solution.id,
        organization_id=org.id,
        risk_name="Vendor lock-in",
        risk_description="The chosen vendor has no viable exit path.",
        impact="high",
        probability="medium",
        mitigation="Negotiate a source-code escrow clause.",
        status="open",
        owner="Jamie Rao",
    )
    fields.update(overrides)
    risk = SolutionRisk(**fields)
    db_session.add(risk)
    db_session.flush()
    return risk


def _run(app, dry_run=False, organization_id=None):
    args = ["backfill-solution-risk-merge"]
    if dry_run:
        args.append("--dry-run")
    if organization_id is not None:
        args += ["--organization-id", str(organization_id)]
    runner = app.test_cli_runner()
    return runner.invoke(args=args)


def test_backfill_merges_a_solution_risk_into_the_canonical_risk_with_a_link(
        app, db_session, make_org):
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.risk_score_history import RiskScoreHistory
    from app.models.solution_lifecycle_models import SolutionRisk

    org = make_org("backfill-basic")
    solution = _solution(db_session, org)
    source = _solution_risk(db_session, org, solution)
    db_session.commit()
    # Captured as plain values before the command runs: the backfill's own
    # db.session.remove() calls (one per organisation, matching
    # backfill_audit_trail.py's own convention) share this test's session, so
    # any ORM object held across that call is expired with nothing left to
    # refresh it from -- a fresh get()/query after the call is required.
    source_id, org_id, solution_id = source.id, org.id, solution.id

    result = _run(app, organization_id=org_id)
    assert result.exit_code == 0, result.output

    merged_source = db_session.get(SolutionRisk, source_id)
    assert merged_source.retired_into_risk_id is not None
    risk = db_session.get(Risk, merged_source.retired_into_risk_id)
    assert risk.organization_id == org_id
    assert risk.solution_id == solution_id
    assert risk.title == "Vendor lock-in"
    assert risk.description == "The chosen vendor has no viable exit path."
    assert risk.mitigation_plan == "Negotiate a source-code escrow clause."
    assert risk.owner == "Jamie Rao"
    # "high" impact / "medium" probability under the shared _level_to_int
    # mapping (app/modules/solutions_strategic/v2/routes/solution_routes.py)
    assert (risk.likelihood, risk.impact) == (3, 4)
    assert (risk.inherent_likelihood, risk.inherent_impact) == (3, 4)
    assert risk.residual_likelihood is None  # nothing in the source implies a residual score

    link = RiskEntityLink.query.filter_by(risk_id=risk.id).one()
    assert (link.entity_type, link.entity_id) == ("solution", solution_id)

    history = RiskScoreHistory.query.filter_by(risk_id=risk.id).all()
    assert len(history) == 1
    assert history[0].score_kind == "inherent"


def test_backfill_is_idempotent(app, db_session, make_org):
    from app.models.risk import Risk
    from app.models.solution_lifecycle_models import SolutionRisk

    org = make_org("backfill-idempotent")
    solution = _solution(db_session, org)
    source = _solution_risk(db_session, org, solution)
    db_session.commit()
    source_id, org_id = source.id, org.id

    result_first = _run(app, organization_id=org_id)
    assert result_first.exit_code == 0, result_first.output
    risk_count_after_first = Risk.query.filter_by(organization_id=org_id).count()
    pointer_after_first = db_session.get(SolutionRisk, source_id).retired_into_risk_id

    result_second = _run(app, organization_id=org_id)
    assert result_second.exit_code == 0, result_second.output
    risk_count_after_second = Risk.query.filter_by(organization_id=org_id).count()
    pointer_after_second = db_session.get(SolutionRisk, source_id).retired_into_risk_id

    assert risk_count_after_first == 1
    assert risk_count_after_second == risk_count_after_first
    assert pointer_after_second == pointer_after_first


def test_backfill_quarantines_a_row_with_no_organisation_without_a_nonzero_exit(
        app, db_session, make_org):
    """A row with no organisation is never guessed and never merged -- it is
    quarantined into ErrorEvent, the platform-wide admin-visible surface, and
    does not by itself force a non-zero exit (only an attributed-but-unmerged
    row does)."""
    from sqlalchemy import text

    from app.models.error_event import ErrorEvent
    from app.models.solution_lifecycle_models import SolutionRisk

    org = make_org("backfill-orphan")
    solution = _solution(db_session, org)
    # Simulate a legacy row from before this table was tenant-scoped: the
    # model declares organization_id NOT NULL, so a real orphan can only
    # exist on a database where that constraint has not been applied yet
    # (schema drift) -- relax it in this transaction only, the same technique
    # tests/test_schema_migrations.py uses for the same class of scenario,
    # and insert the orphan via raw SQL to bypass the ORM's own validation.
    db_session.execute(text(
        "ALTER TABLE solution_risks ALTER COLUMN organization_id DROP NOT NULL"
    ))
    db_session.execute(text(
        "INSERT INTO solution_risks "
        "(solution_id, organization_id, risk_description, impact, probability, status) "
        "VALUES (:solution_id, NULL, :description, 'medium', 'medium', 'open')"
    ), {"solution_id": solution.id, "description": "Orphan risk, no organisation"})
    db_session.commit()
    orphan_id = db_session.execute(text(
        "SELECT id FROM solution_risks WHERE organization_id IS NULL "
        "AND risk_description = 'Orphan risk, no organisation'"
    )).scalar()

    result = _run(app, organization_id=org.id)
    assert result.exit_code == 0, result.output

    orphan = db_session.get(SolutionRisk, orphan_id)
    assert orphan.retired_into_risk_id is None

    fingerprint = f"backfill-quarantine:solution_risks:{orphan_id}"[:64]
    event = ErrorEvent.query.filter_by(fingerprint=fingerprint).first()
    assert event is not None
    assert event.resolved is False
    assert event.organization_id is None


def test_two_organisations_backfill_never_mixes_rows(app, db_session, make_org):
    """Organisation B's solution risk is never merged into organisation A's
    risks, and vice versa, whether the backfill runs scoped or unscoped."""
    from app.models.risk import Risk

    org_a, org_b = make_org("backfill-org-a"), make_org("backfill-org-b")
    solution_a = _solution(db_session, org_a)
    solution_b = _solution(db_session, org_b)
    _solution_risk(db_session, org_a, solution_a, risk_name="Org A risk")
    _solution_risk(db_session, org_b, solution_b, risk_name="Org B risk")
    db_session.commit()
    org_a_id, org_b_id = org_a.id, org_b.id

    result = _run(app)  # unscoped: every organisation in one pass
    assert result.exit_code == 0, result.output

    risks_a = Risk.query.filter_by(organization_id=org_a_id).all()
    risks_b = Risk.query.filter_by(organization_id=org_b_id).all()
    assert [r.title for r in risks_a] == ["Org A risk"], result.output
    assert [r.title for r in risks_b] == ["Org B risk"], result.output
