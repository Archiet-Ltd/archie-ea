"""One decision register, consolidation PR 1.

- architecture_decision_records pairs into architecture_decisions (dual-write);
  the source stays readable and stays the system of record for its own fields.
- decision_ledger gains a real organisation column and is fenced by the
  existing tenant middleware; before this, every ARB session's ledger loaded
  every organisation's rows.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app import db

REPO_ROOT = Path(__file__).resolve().parents[1]
from app.commands.backfill_decision_register_consolidation import run_backfill
from app.models.adr import ArchitectureDecisionRecord
from app.models.architecture_decision import ArchitectureDecision
from app.models.decision_ledger import DecisionLedger
from app.models.unified_capability import UnifiedCapability


def _make_adr_record(org_id, **overrides):
    defaults = dict(
        adr_number=1,
        title="Use REST for external integrations",
        status="proposed",
        context="Need a standard integration approach",
        decision="Use RESTful APIs",
        rationale="Industry standard",
        consequences="Need an API gateway",
        organization_id=org_id,
    )
    defaults.update(overrides)
    record = ArchitectureDecisionRecord(**defaults)
    db.session.add(record)
    db.session.flush()
    return record


def _make_capability(org_id, name="Customer Acquisition"):
    cap = UnifiedCapability(name=name, level=1, organization_id=org_id)
    db.session.add(cap)
    db.session.flush()
    return cap


# ------------------------------------------------- ADR -> canonical pairing


def test_pairing_creates_a_visible_canonical_decision(db_session, make_org, tenant_ctx):
    org = make_org("adr-pair")
    with tenant_ctx(org.id):
        record = _make_adr_record(org.id)
        canonical = record.pair_with_canonical_register()
        db.session.commit()

        assert canonical is not None
        assert record.retired_into_id == canonical.id
        assert canonical.organization_id == org.id
        assert canonical.source_table == "architecture_decision_records"
        assert canonical.source_id == record.id
        assert canonical.title == record.title


def test_pairing_a_record_with_json_alternatives_does_not_crash(db_session, make_org, tenant_ctx):
    """ArchitectureDecision.alternatives is Text ("text or JSON"), not a
    structured column -- json.loads()'ing the legacy value before storing it
    used to hand SQLAlchemy a dict/list for a Text column, raising on every
    legacy row whose alternatives_considered actually held JSON (the common
    case, per that column's own comment), aborting the whole backfill."""
    import json

    org = make_org("adr-pair-json-alternatives")
    with tenant_ctx(org.id):
        record = _make_adr_record(
            org.id,
            alternatives_considered=json.dumps(["Option A", "Option B"]),
        )
        canonical = record.pair_with_canonical_register()
        db.session.commit()

        assert canonical is not None
        assert canonical.alternatives == record.alternatives_considered
        assert json.loads(canonical.alternatives) == ["Option A", "Option B"]


def test_pairing_a_record_with_plain_text_alternatives_does_not_crash(
    db_session, make_org, tenant_ctx
):
    """The same field also holds plain, non-JSON text on older rows (per its
    own "text or JSON" comment) -- that must carry over untouched too, not
    get wrapped in a {"legacy_text": ...} dict that also cannot be stored in
    a Text column."""
    org = make_org("adr-pair-text-alternatives")
    with tenant_ctx(org.id):
        record = _make_adr_record(
            org.id, alternatives_considered="Considered a vendor product; rejected on cost."
        )
        canonical = record.pair_with_canonical_register()
        db.session.commit()

        assert canonical is not None
        assert canonical.alternatives == "Considered a vendor product; rejected on cost."


def test_pairing_is_idempotent(db_session, make_org, tenant_ctx):
    org = make_org("adr-idem")
    with tenant_ctx(org.id):
        record = _make_adr_record(org.id)
        first = record.pair_with_canonical_register()
        db.session.commit()
        second = record.pair_with_canonical_register()
        db.session.commit()

        assert first.id == second.id
        assert ArchitectureDecision.query.filter_by(
            source_table="architecture_decision_records", source_id=record.id
        ).count() == 1


def test_pairing_preserves_rejected_status_verbatim(db_session, make_org, tenant_ctx):
    """Regression: pairing remapped status='rejected' to 'deprecated',
    losing the real distinction (a rejected decision is not the same as one
    superseded/deprecated by a later one)."""
    org = make_org("adr-rejected")
    with tenant_ctx(org.id):
        record = _make_adr_record(org.id, status="rejected")
        canonical = record.pair_with_canonical_register()
        db.session.commit()

        assert canonical.status == "rejected"


def test_pairing_never_visible_to_another_organisation(db_session, make_org, tenant_ctx):
    org_a = make_org("adr-a")
    org_b = make_org("adr-b")
    with tenant_ctx(org_a.id):
        record = _make_adr_record(org_a.id, title="Org A's decision")
        canonical = record.pair_with_canonical_register()
        db.session.commit()
        canonical_id = canonical.id

    with tenant_ctx(org_b.id):
        # filter_by (not .get(), which checks the identity map first and can
        # return an already-loaded row from another org without re-querying,
        # bypassing the tenant filter -- see app/models/adr.py's own note).
        assert ArchitectureDecision.query.filter_by(id=canonical_id).first() is None
        assert ArchitectureDecision.query.filter_by(title="Org A's decision").first() is None


def _legacy_record_count():
    return ArchitectureDecisionRecord.query.count()


def _clear_leaked_org_context():
    """flask.g is bound to the app context, not the (possibly nested) request
    context tenant_ctx pushes -- db_session holds one app context open for
    the whole test, so g.current_org_id set inside a `with tenant_ctx(...)`
    block survives past it and leaks into whatever runs next in the same
    test (see tests/test_motivation_bridge_org_scoping.py's docstring for
    the same trap). Without this, a second organisation's tenant_ctx call
    leaves its org_id active for every query made afterwards, including
    ones meant to check the first organisation's row."""
    from flask import g, has_app_context

    if has_app_context() and hasattr(g, "current_org_id"):
        delattr(g, "current_org_id")


def _user_for(db_session, org):
    from app.models.user import User

    user = User(
        email=f"adr-fix-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Test", last_name="User", organization_id=org.id, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_multi_domain_chat_decision_writes_only_the_canonical_row(db_session, make_org, tenant_ctx):
    """architecture_decisions is the only writer: the AI chat
    decision-recording path must create a canonical row directly and
    never touch architecture_decision_records, in either organisation."""
    from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService

    org_a = make_org("chat-decision-a")
    org_b = make_org("chat-decision-b")
    before = _legacy_record_count()

    with tenant_ctx(org_a.id):
        svc = MultiDomainChatService(user_id=None)
        svc._detect_and_handle_decision(
            "record this decision: use one message bus for all services",
            "Acknowledged.",
            {},
        )

    decision = (
        ArchitectureDecision.query
        .filter_by(title="use one message bus for all services"[:200])
        .first()
    )
    assert decision is not None
    assert decision.organization_id == org_a.id
    assert _legacy_record_count() == before, "no row should ever land in the superseded store"

    with tenant_ctx(org_b.id):
        svc_b = MultiDomainChatService(user_id=None)
        svc_b._detect_and_handle_decision(
            "record this decision: use one message bus for all services", "Acknowledged.", {},
        )
    decision_b = (
        ArchitectureDecision.query
        .filter_by(title="use one message bus for all services"[:200], organization_id=org_b.id)
        .first()
    )
    assert decision_b is not None
    assert decision_b.id != decision.id


def test_workbench_kernel_decision_writes_only_the_canonical_row(db_session, make_org, tenant_ctx):
    from app.models.solution_architect_models import SolutionADRLink, SolutionAnalysisSession, SolutionSessionStatus
    from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

    before = _legacy_record_count()

    def _record_for(org, title):
        user = _user_for(db_session, org)
        session = SolutionAnalysisSession(
            name=f"ws-{uuid.uuid4().hex[:8]}", status=SolutionSessionStatus.IN_PROGRESS,
            organization_id=org.id, created_by_id=user.id,
        )
        db_session.add(session)
        db_session.flush()
        with tenant_ctx(org.id):
            kernel = WorkbenchKernel(user_id=user.id)
            result = kernel.record_architecture_decision(
                workspace_id=session.id, title=title,
                chosen_option="Option A", rationale="Because A is simpler",
            )
        assert result["success"] is True
        return session, result["decision_id"]

    org_a = make_org("workbench-decision-a")
    org_b = make_org("workbench-decision-b")
    session_a, decision_id_a = _record_for(org_a, "Workbench decision A")
    session_b, decision_id_b = _record_for(org_b, "Workbench decision B")
    _clear_leaked_org_context()

    decision_a = ArchitectureDecision.query.filter_by(id=decision_id_a).first()
    decision_b = ArchitectureDecision.query.filter_by(id=decision_id_b).first()
    assert decision_a.organization_id == org_a.id
    assert decision_b.organization_id == org_b.id
    assert _legacy_record_count() == before, "no row should ever land in the superseded store"

    link_a = SolutionADRLink.query.filter_by(session_id=session_a.id).first()
    assert link_a.adr_id == decision_a.id


def test_sad_governance_generator_decision_writes_only_the_canonical_row(db_session, make_org, tenant_ctx):
    from app.models.solution_architect_models import SolutionAnalysisSession, SolutionSessionStatus
    from app.modules.ai_chat.services.workbench_kernel import SADGovernanceGenerator, WorkbenchKernel

    before = _legacy_record_count()

    def _record_for(org, title):
        user = _user_for(db_session, org)
        session = SolutionAnalysisSession(
            name=f"ws-{uuid.uuid4().hex[:8]}", status=SolutionSessionStatus.IN_PROGRESS,
            organization_id=org.id, created_by_id=user.id,
        )
        db_session.add(session)
        db_session.flush()
        with tenant_ctx(org.id):
            kernel = WorkbenchKernel(user_id=user.id)
            gov = SADGovernanceGenerator(kernel=kernel, user_id=user.id)
            result = gov.generate_decision_record(
                workspace_id=session.id, title=title,
                chosen_option="Option B", rationale="Because B scales better",
            )
        assert result["success"] is True
        return result["adr_id"]

    org_a = make_org("sad-gov-a")
    org_b = make_org("sad-gov-b")
    decision_id_a = _record_for(org_a, "SAD governance decision A")
    decision_id_b = _record_for(org_b, "SAD governance decision B")
    _clear_leaked_org_context()

    decision_a = ArchitectureDecision.query.filter_by(id=decision_id_a).first()
    decision_b = ArchitectureDecision.query.filter_by(id=decision_id_b).first()
    assert decision_a.organization_id == org_a.id
    assert decision_b.organization_id == org_b.id
    assert _legacy_record_count() == before, "no row should ever land in the superseded store"


def test_solution_options_advisor_persist_writes_only_the_canonical_row(db_session, make_org):
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.services.solution_options_advisor import SolutionOptionsAdvisor

    before = _legacy_record_count()

    def _persist_for(org, title):
        solution = Solution(name=f"sol-{uuid.uuid4().hex[:8]}", organization_id=org.id)
        db_session.add(solution)
        db_session.flush()
        parsed = {
            "options": [{"name": "A"}, {"name": "B"}],
            "decision": {
                "title": title, "context": "ctx", "decision": "dec", "rationale": "rat",
                "consequences": "cons", "estimated_effort": "2 weeks", "business_value": "high",
            },
        }
        SolutionOptionsAdvisor._persist(solution, parsed, user_id=None)
        return solution

    org_a = make_org("options-advisor-a")
    org_b = make_org("options-advisor-b")
    solution_a = _persist_for(org_a, "Options advisor decision A")
    solution_b = _persist_for(org_b, "Options advisor decision B")

    decision_a = ArchitectureDecision.query.filter_by(solution_id=solution_a.id).first()
    decision_b = ArchitectureDecision.query.filter_by(solution_id=solution_b.id).first()
    assert decision_a.organization_id == org_a.id
    assert decision_a.decided_by_label == "AI Solution Architect (proposed)"
    assert decision_b.organization_id == org_b.id
    assert _legacy_record_count() == before, "no row should ever land in the superseded store"


def test_options_advisor_page_renders_once_a_decision_exists(
    db_session, make_org, tenant_ctx, client, login_as
):
    """options_advisor.html used to reference adr.adr_number, a legacy
    ArchitectureDecisionRecord field the canonical ArchitectureDecision (what
    SolutionOptionsAdvisor.to_dict's "adr" dict is actually built from) never
    had, 500ing the page every time a solution had a recorded decision."""
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.services.solution_options_advisor import SolutionOptionsAdvisor

    org = make_org("options-advisor-render")
    with tenant_ctx(org.id):
        user = _user_for(db_session, org)
        solution = Solution(name=f"sol-{uuid.uuid4().hex[:8]}", organization_id=org.id)
        db_session.add(solution)
        db_session.flush()
        parsed = {
            "options": [{"name": "A"}, {"name": "B"}],
            "decision": {
                "title": "Rendered decision", "context": "ctx", "decision": "dec",
                "rationale": "rat", "consequences": "cons",
                "estimated_effort": "2 weeks", "business_value": "high",
            },
        }
        SolutionOptionsAdvisor._persist(solution, parsed, user_id=None)
        db_session.commit()
        solution_id = solution.id

    login_as(client, user)
    resp = client.get(f"/solutions/{solution_id}/options-advisor")
    assert resp.status_code == 200


# ------------------------------------------------------- backfill command


def test_backfill_pairs_every_pending_record(db_session, make_org, tenant_ctx):
    org = make_org("adr-backfill")
    with tenant_ctx(org.id):
        r1 = _make_adr_record(org.id, adr_number=1, title="First")
        r2 = _make_adr_record(org.id, adr_number=2, title="Second")
        db.session.commit()

    adr_stats, _ = run_backfill(dry_run=False)
    assert adr_stats["paired"] >= 2

    db.session.refresh(r1)
    db.session.refresh(r2)
    assert r1.retired_into_id is not None
    assert r2.retired_into_id is not None


def test_backfill_run_twice_creates_no_duplicate_pairs(db_session, make_org, tenant_ctx):
    org = make_org("adr-backfill-idem")
    with tenant_ctx(org.id):
        record = _make_adr_record(org.id)
        db.session.commit()
        record_id = record.id

    run_backfill(dry_run=False)
    run_backfill(dry_run=False)  # second run must be a no-op for this row

    assert ArchitectureDecision.query.filter_by(
        source_table="architecture_decision_records", source_id=record_id
    ).count() == 1


# No test for an ArchitectureDecisionRecord with organization_id=None: the
# column is NOT NULL at the database level (it has carried TenantMixin since
# before this brief) and that constraint is enforced regardless of insert
# path, so the state cannot actually occur. The backfill command's
# `skipped_no_org` branch is defensive (belt-and-suspenders, matching the
# same-named guard in other backfill_*.py commands) rather than reachable.


# --------------------------------------------- decision_ledger tenant fence


def test_decision_ledger_never_shows_another_organisations_rows(db_session, make_org, tenant_ctx):
    org_a = make_org("ledger-a")
    org_b = make_org("ledger-b")

    with tenant_ctx(org_a.id):
        row_a = DecisionLedger(
            capability_id="999001",
            capability_name_snapshot="Org A capability",
            decision_id="DEC-A-1",
            decision_summary="Org A decision",
        )
        db.session.add(row_a)
        db.session.commit()

    with tenant_ctx(org_b.id):
        row_b = DecisionLedger(
            capability_id="999002",
            capability_name_snapshot="Org B capability",
            decision_id="DEC-B-1",
            decision_summary="Org B decision",
        )
        db.session.add(row_b)
        db.session.commit()

        # The exact bug this closes: before TenantMixin, this query returned
        # every organisation's rows.
        visible = DecisionLedger.query.all()
        assert [r.decision_summary for r in visible] == ["Org B decision"]

    with tenant_ctx(org_a.id):
        visible = DecisionLedger.query.all()
        assert [r.decision_summary for r in visible] == ["Org A decision"]


def test_decision_ledger_backfill_derives_org_from_capability(db_session, make_org, tenant_ctx):
    org = make_org("ledger-backfill")
    with tenant_ctx(org.id):
        cap = _make_capability(org.id)
        cap_id = cap.id
        db.session.commit()

    # Raw SQL, explicit NULL organisation_id: every pre-existing row is in
    # exactly this state right after the schema expand. Deliberately not the
    # ORM constructor -- its default fills organization_id from context in
    # ways that would make this fixture's own state depend on ambient test
    # ordering rather than asserting the thing this test is actually about.
    row_id = db.session.execute(db.text(
        "INSERT INTO decision_ledger "
        "(capability_id, capability_name_snapshot, decision_id, decision_summary, decision_sequence, decision_date, created_at, organization_id) "
        "VALUES (:cap_id, 'Backfill target', 'DEC-BF-1', 'Needs an org', 1, now(), now(), NULL) RETURNING id"
    ), {"cap_id": str(cap_id)}).scalar()
    db.session.commit()

    assert db.session.execute(
        db.text("SELECT organization_id FROM decision_ledger WHERE id = :i"), {"i": row_id}
    ).scalar() is None

    _, ledger_stats = run_backfill(dry_run=False)
    assert ledger_stats["backfilled"] >= 1

    resolved_org = db.session.execute(
        db.text("SELECT organization_id FROM decision_ledger WHERE id = :i"), {"i": row_id}
    ).scalar()
    assert resolved_org == org.id


def test_decision_ledger_backfill_leaves_unresolvable_rows_as_orphans(db_session, make_org):
    row_id = db.session.execute(db.text(
        "INSERT INTO decision_ledger "
        "(capability_id, capability_name_snapshot, decision_id, decision_summary, decision_sequence, decision_date, created_at, organization_id) "
        "VALUES ('not-a-number', 'Unresolvable', 'DEC-ORPHAN-1', 'No matching capability', 1, now(), now(), NULL) "
        "RETURNING id"
    )).scalar()
    db.session.commit()

    _, ledger_stats = run_backfill(dry_run=False)
    assert ledger_stats["orphan"] >= 1

    resolved_org = db.session.execute(
        db.text("SELECT organization_id FROM decision_ledger WHERE id = :i"), {"i": row_id}
    ).scalar()
    assert resolved_org is None


def test_decision_ledger_backfill_reports_quarantine_and_updates_not_duplicates(db_session, make_org):
    """Final check v5 LOW: an unresolvable decision_ledger row must surface on
    the existing platform-administrator errors page, with the expected
    fingerprint and no organisation; a second backfill run must update that
    one entry's occurrence count rather than creating a second one."""
    from app.models.error_event import ErrorEvent

    row_id = db.session.execute(db.text(
        "INSERT INTO decision_ledger "
        "(capability_id, capability_name_snapshot, decision_id, decision_summary, decision_sequence, decision_date, created_at, organization_id) "
        "VALUES ('not-a-number', 'Unresolvable', 'DEC-ORPHAN-2', 'No matching capability', 1, now(), now(), NULL) "
        "RETURNING id"
    )).scalar()
    db.session.commit()

    run_backfill(dry_run=False)

    fingerprint = f"decision-ledger-quarantine:{row_id}"
    events = ErrorEvent.query.filter_by(fingerprint=fingerprint).all()
    assert len(events) == 1
    event = events[0]
    assert event.organization_id is None
    assert event.resolved is False
    assert str(row_id) in event.message
    assert event.occurrence_count == 1

    # A second run over the still-unresolvable row updates the same entry.
    run_backfill(dry_run=False)
    db_session.expire_all()
    events_after = ErrorEvent.query.filter_by(fingerprint=fingerprint).all()
    assert len(events_after) == 1, "a second backfill run must not create a duplicate quarantine entry"
    assert events_after[0].occurrence_count == 2


# ----------------------------------------------- final-check fix round


def test_solution_teardown_deletes_the_paired_canonical_row_too(app):
    """Regression (final check DEFECT-1): a solution's full architecture
    teardown hard-deletes architecture_decision_records by
    architecture_model_id with no awareness of retired_into_id, leaving the
    paired architecture_decisions row dangling.

    Does not use the db_session fixture: _engine_archimate_cleanup
    deliberately opens its own db.engine.begin() connection (see its own
    docstring -- avoiding lock contention with the ORM session), which is
    outside db_session's SAVEPOINT-based rollback contract and would not see
    data set up through it. Commits for real instead, with manual cleanup.
    """
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.adr import ArchitectureDecisionRecord
    from app.models.models import ArchitectureModel
    from app.models.organization import Organization
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
        _engine_archimate_cleanup,
    )

    with app.app_context():
        org = Organization(name="Teardown test org", slug=f"teardown-{uuid.uuid4().hex[:10]}")
        db.session.add(org)
        db.session.flush()

        solution = Solution(name="Teardown target", organization_id=org.id)
        db.session.add(solution)
        db.session.flush()

        model = ArchitectureModel(
            organization_id=org.id, name="Teardown model", version="1.0",
            solution_id=solution.id, model_data="{}", is_default=False,
        )
        db.session.add(model)
        db.session.flush()

        record = _make_adr_record(org.id, architecture_model_id=model.id)
        canonical = record.pair_with_canonical_register()
        db.session.commit()
        record_id, canonical_id, solution_id, model_id, org_id = (
            record.id, canonical.id, solution.id, model.id, org.id,
        )

        try:
            _engine_archimate_cleanup([solution_id])
            db.session.expire_all()

            remaining_record = db.session.get(ArchitectureDecisionRecord, record_id)
            remaining_canonical = db.session.get(ArchitectureDecision, canonical_id)
            assert remaining_record is None
            assert remaining_canonical is None, (
                "canonical row left dangling after its source was deleted"
            )
        finally:
            db.session.rollback()
            db.session.execute(
                db.text("DELETE FROM architecture_decisions WHERE id = :i"), {"i": canonical_id}
            )
            db.session.execute(
                db.text("DELETE FROM architecture_decision_records WHERE id = :i"), {"i": record_id}
            )
            db.session.execute(
                db.text("DELETE FROM architecture_models WHERE id = :i"), {"i": model_id}
            )
            db.session.execute(
                db.text("DELETE FROM solutions WHERE id = :i"), {"i": solution_id}
            )
            db.session.execute(
                db.text("DELETE FROM organizations WHERE id = :i"), {"i": org_id}
            )
            db.session.commit()


def test_set_status_updates_the_canonical_row_and_leaves_the_legacy_row_frozen(db_session, make_org, tenant_ctx):
    """architecture_decisions is the only writer (lead ruling):
    SolutionOptionsAdvisor.set_status() now writes status only to the paired
    ArchitectureDecision; the legacy ArchitectureDecisionRecord is read
    history and takes no new writes, so its own `status` stays exactly what
    it was when the record was created/paired.
    """
    from app.modules.solutions_strategic.v2.services.solution_options_advisor import (
        SolutionOptionsAdvisor,
    )

    org = make_org("setstatus")
    with tenant_ctx(org.id):
        record = _make_adr_record(org.id, status="proposed")
        canonical = record.pair_with_canonical_register()
        db.session.commit()

        result = SolutionOptionsAdvisor.set_status(canonical.id, "accepted", user_id=1)

        assert result["success"] is True
        assert result["adr"]["status"] == "accepted", "response must reflect the status actually applied"
        db.session.refresh(record)
        db.session.refresh(canonical)
        assert canonical.status == "accepted"
        assert record.status == "proposed", (
            "legacy ArchitectureDecisionRecord.status changed -- it must stay frozen "
            "history once architecture_decisions is the only writer"
        )


def test_set_status_on_one_organisations_decision_never_touches_another(db_session, make_org, tenant_ctx):
    """Two organisations each have a paired, proposed decision; accepting
    org A's must change only org A's canonical row.
    """
    from app.modules.solutions_strategic.v2.services.solution_options_advisor import (
        SolutionOptionsAdvisor,
    )

    org_a = make_org("setstatus-a")
    org_b = make_org("setstatus-b")
    with tenant_ctx(org_b.id):
        record_b = _make_adr_record(org_b.id, status="proposed")
        canonical_b = record_b.pair_with_canonical_register()
        db.session.commit()
        canonical_b_id = canonical_b.id

    with tenant_ctx(org_a.id):
        record_a = _make_adr_record(org_a.id, status="proposed")
        canonical_a = record_a.pair_with_canonical_register()
        db.session.commit()
        canonical_a_id = canonical_a.id

        result = SolutionOptionsAdvisor.set_status(canonical_a_id, "accepted", user_id=1)
        assert result["success"] is True

        db.session.refresh(canonical_a)
        assert canonical_a.status == "accepted"
        # filter_by (not .get(), which checks the identity map first and can
        # return an already-loaded row from another org without re-querying) --
        # same pitfall documented in app/models/adr.py and this file's other tests.
        org_b_row = ArchitectureDecision.query.filter_by(id=canonical_b_id).first()
        assert org_b_row is None, "org A's tenant context must not see org B's canonical row at all"

    with tenant_ctx(org_b.id):
        untouched = ArchitectureDecision.query.filter_by(id=canonical_b_id).first()
        assert untouched.status == "proposed", "org B's decision must be untouched by org A's set_status call"


def test_architecture_decision_record_is_no_longer_a_store_agreement_peer():
    """Regression (final check DEFECT-3): a canonical-only ArchitectureDecision
    (no paired ArchitectureDecisionRecord) used to make the "architecture
    decisions" store-agreement concept disagree, since
    ArchitectureDecisionRecord was listed as an independent peer surface --
    e.g. one canonical-only row gave architecture_decisions=1,
    architecture_decision_records=0 for the same organisation. It is now a
    satellite of the canonical store (paired via retired_into_id), not a
    peer answering the same question, so it must no longer be one of the
    concept's compared surfaces.
    """
    source = (REPO_ROOT / "scripts" / "check_store_agreement.py").read_text()
    start = source.index('"architecture decisions": [')
    end = source.index("],", start)
    concept_block = source[start:end]
    assert 'Surface("orm:ArchitectureDecisionRecord"' not in concept_block, (
        "ArchitectureDecisionRecord must not be a peer surface of the "
        "architecture decisions concept any more"
    )
    assert 'Surface("orm:ArchitectureDecision"' in concept_block  # the concept itself still exists


def test_adr_record_json_api_surfaces_its_canonical_pairing(db_session, make_org, tenant_ctx, client, login_as):
    """Regression (final check DEFECT-5, MEDIUM): the only ADR route that
    still reads architecture_decision_records directly (the rest already
    redirected to the canonical register before this PR) now also surfaces
    the pairing, so a caller can reach the canonical register from here too.
    This field does not exist on anioko/main.
    """
    from app.models.user import User

    org = make_org("adr-json-pairing")
    with tenant_ctx(org.id):
        user = User(
            email=f"adr-view-{uuid.uuid4().hex[:10]}@example.test",
            first_name="ADR", last_name="Viewer",
            organization_id=org.id, confirmed=True,
        )
        db_session.add(user)
        db_session.flush()

        record = _make_adr_record(org.id)
        canonical = record.pair_with_canonical_register()
        db_session.commit()
        record_id, canonical_id = record.id, canonical.id

    login_as(client, user)
    resp = client.get(f"/architecture/adrs/records/{record_id}")

    assert resp.status_code == 200
    payload = resp.get_json()["adr"]
    assert payload["canonical_decision_id"] == canonical_id
    assert payload["canonical_decision_url"] is not None
    assert str(canonical_id) in payload["canonical_decision_url"]
    assert payload["historical_snapshot"] is True


def test_view_record_keeps_showing_the_frozen_title_after_the_canonical_row_is_edited(
    db_session, make_org, tenant_ctx, client, login_as
):
    """architecture_decisions is the only writer (lead ruling): once
    paired, this legacy JSON read surface is a frozen snapshot, not a second
    live view of the same data. Editing the canonical row through
    `arch_decisions.edit_decision` must NOT be mirrored back here -- this
    test pins that divergence as the intended behaviour (final check
    DEFECT-1's repro, now asserted as correct rather than as a bug).
    """
    from app.models.user import User

    org = make_org("adr-frozen-snapshot")
    with tenant_ctx(org.id):
        user = User(
            email=f"adr-frozen-{uuid.uuid4().hex[:10]}@example.test",
            first_name="ADR", last_name="Frozen",
            organization_id=org.id, confirmed=True,
        )
        db_session.add(user)
        db_session.flush()

        record = _make_adr_record(org.id, title="Original paired title")
        canonical = record.pair_with_canonical_register()
        db_session.commit()
        record_id, canonical_id = record.id, canonical.id

    login_as(client, user)
    edit_resp = client.post(
        f"/architecture/decisions/{canonical_id}/edit",
        data={
            "title": "Edited only in canonical register",
            "status": "accepted",
            "adm_phase": "",
            "context": "",
            "decision": "",
            "consequences": "",
            "alternatives": "",
        },
    )
    assert edit_resp.status_code in (302, 303)

    resp = client.get(f"/architecture/adrs/records/{record_id}")
    payload = resp.get_json()["adr"]
    assert payload["title"] == "Original paired title", (
        "legacy record's shared fields must stay frozen after a canonical-only edit"
    )
    assert payload["historical_snapshot"] is True
    assert payload["canonical_decision_id"] == canonical_id


def test_update_adr_route_is_retired_and_redirects_to_canonical_edit(
    db_session, make_org, tenant_ctx, client, login_as
):
    """Fix 4 (final check DEFECT-3): the legacy POST /architecture/adrs/<id>
    no longer processes form data as a second writer -- it redirects to the
    one canonical edit route, like the GET routes beside it already do.
    """
    from app.models.user import User

    org = make_org("adr-update-retired")
    with tenant_ctx(org.id):
        user = User(
            email=f"adr-update-{uuid.uuid4().hex[:10]}@example.test",
            first_name="ADR", last_name="Updater",
            organization_id=org.id, confirmed=True,
        )
        db_session.add(user)
        db_session.flush()

        record = _make_adr_record(org.id, title="Untouched by the retired route")
        canonical = record.pair_with_canonical_register()
        db_session.commit()
        canonical_id = canonical.id

    login_as(client, user)
    resp = client.post(
        f"/architecture/adrs/{canonical_id}",
        data={"title": "Attempted write via the retired route"},
    )

    assert resp.status_code in (302, 303)
    assert f"/architecture/decisions/{canonical_id}/edit" in resp.headers["Location"]

    with tenant_ctx(org.id):
        unchanged = ArchitectureDecision.query.filter_by(id=canonical_id).first()
        assert unchanged.title == "Untouched by the retired route"


def _make_admin(db_session, org, label):
    from app.models import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Admin", last_name=label,
        organization_id=org.id, role=role, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_approve_and_reject_routes_touch_only_the_canonical_row_two_organisations(
    db_session, make_org, tenant_ctx, client, login_as
):
    """Fix 4 (final check DEFECT-3): ADRService.approve_adr/reject_adr
    already wrote only the canonical ArchitectureDecision, never the paired
    ArchitectureDecisionRecord -- this pins that as verified, compliant
    behaviour (not a gap) and checks it holds for a second organisation.
    """
    org_a = make_org("adr-approve-a")
    org_b = make_org("adr-approve-b")
    with tenant_ctx(org_a.id):
        admin_a = _make_admin(db_session, org_a, "ApproveA")
        record_a1 = _make_adr_record(org_a.id, status="proposed")
        canonical_a1 = record_a1.pair_with_canonical_register()
        record_a2 = _make_adr_record(org_a.id, status="proposed", title="Org A second decision")
        canonical_a2 = record_a2.pair_with_canonical_register()
        db_session.commit()
        record_a1_id, canonical_a1_id = record_a1.id, canonical_a1.id
        record_a2_id, canonical_a2_id = record_a2.id, canonical_a2.id

    with tenant_ctx(org_b.id):
        record_b = _make_adr_record(org_b.id, status="proposed")
        canonical_b = record_b.pair_with_canonical_register()
        db_session.commit()
        record_b_id, canonical_b_id = record_b.id, canonical_b.id

    login_as(client, admin_a)
    approve_resp = client.post(f"/architecture/adrs/{canonical_a1_id}/approve")
    assert approve_resp.status_code in (302, 303)
    reject_resp = client.post(
        f"/architecture/adrs/{canonical_a2_id}/reject", data={"rejection_reason": "Not viable"}
    )
    assert reject_resp.status_code in (302, 303)

    with tenant_ctx(org_a.id):
        approved = ArchitectureDecision.query.filter_by(id=canonical_a1_id).first()
        assert approved.status == "approved"
        rejected = ArchitectureDecision.query.filter_by(id=canonical_a2_id).first()
        assert rejected.status == "rejected"

        record_a1_after = ArchitectureDecisionRecord.query.filter_by(id=record_a1_id).first()
        record_a2_after = ArchitectureDecisionRecord.query.filter_by(id=record_a2_id).first()
        assert record_a1_after.status == "proposed", (
            "legacy record must stay untouched by the canonical-only approve route"
        )
        assert record_a2_after.status == "proposed", (
            "legacy record must stay untouched by the canonical-only reject route"
        )

    with tenant_ctx(org_b.id):
        untouched_canonical = ArchitectureDecision.query.filter_by(id=canonical_b_id).first()
        assert untouched_canonical.status == "proposed"
        untouched_record = ArchitectureDecisionRecord.query.filter_by(id=record_b_id).first()
        assert untouched_record.status == "proposed"


def test_deleting_a_paired_canonical_decision_orphans_but_keeps_the_legacy_record(
    db_session, make_org, tenant_ctx, client, login_as
):
    """Fix 2 (final check DEFECT-2): deleting a paired canonical decision
    used to raise ForeignKeyViolation on
    architecture_decision_records_retired_into_id_fkey. The legacy register
    stays as read history (lead ruling), so the fix orphans the
    paired record (retired_into_id -> NULL) rather than deleting it, and the
    canonical delete succeeds. Checked across two organisations so org A's
    delete cannot reach org B's pairing.
    """
    from app.models.user import User

    org_a = make_org("adr-delete-a")
    org_b = make_org("adr-delete-b")
    with tenant_ctx(org_a.id):
        user = User(
            email=f"adr-delete-{uuid.uuid4().hex[:10]}@example.test",
            first_name="ADR", last_name="Deleter",
            organization_id=org_a.id, confirmed=True,
        )
        db_session.add(user)
        db_session.flush()

        record_a = _make_adr_record(org_a.id, title="Org A paired decision")
        canonical_a = record_a.pair_with_canonical_register()
        db_session.commit()
        record_a_id, canonical_a_id = record_a.id, canonical_a.id

    with tenant_ctx(org_b.id):
        record_b = _make_adr_record(org_b.id, title="Org B paired decision")
        canonical_b = record_b.pair_with_canonical_register()
        db_session.commit()
        record_b_id, canonical_b_id = record_b.id, canonical_b.id

    login_as(client, user)
    resp = client.post(f"/architecture/decisions/{canonical_a_id}/delete")
    assert resp.status_code in (302, 303)

    with tenant_ctx(org_a.id):
        assert ArchitectureDecision.query.filter_by(id=canonical_a_id).first() is None
        surviving_record = ArchitectureDecisionRecord.query.filter_by(id=record_a_id).first()
        assert surviving_record is not None, "legacy record must survive the canonical delete"
        assert surviving_record.retired_into_id is None, "orphaned, not left pointing at a deleted row"
        assert surviving_record.title == "Org A paired decision"

    with tenant_ctx(org_b.id):
        untouched_canonical = ArchitectureDecision.query.filter_by(id=canonical_b_id).first()
        assert untouched_canonical is not None
        untouched_record = ArchitectureDecisionRecord.query.filter_by(id=record_b_id).first()
        assert untouched_record.retired_into_id == canonical_b_id, "org B's pairing must be untouched"


def test_deleting_a_workbench_recorded_decision_removes_its_traceability_link(
    db_session, make_org, tenant_ctx, client, login_as
):
    """A decision the workbench kernel recorded carries a SolutionADRLink
    back to its session (adr_id NOT NULL, no relationship()/cascade), so
    deleting the decision used to raise ForeignKeyViolation on
    solution_adr_links_adr_id_fkey. The link is metadata about the decision,
    not an independent record, so the delete removes it rather than
    refusing."""
    from app.models.solution_architect_models import (
        SolutionADRLink, SolutionAnalysisSession, SolutionSessionStatus,
    )
    from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

    org = make_org("adr-delete-workbench")
    with tenant_ctx(org.id):
        user = _user_for(db_session, org)
        session = SolutionAnalysisSession(
            name=f"ws-{uuid.uuid4().hex[:8]}", status=SolutionSessionStatus.IN_PROGRESS,
            organization_id=org.id, created_by_id=user.id,
        )
        db_session.add(session)
        db_session.flush()
        kernel = WorkbenchKernel(user_id=user.id)
        result = kernel.record_architecture_decision(
            workspace_id=session.id, title="Workbench decision to delete",
            chosen_option="Option A", rationale="Because A is simpler",
        )
    assert result["success"] is True
    decision_id = result["decision_id"]
    session_id = session.id
    db_session.commit()

    assert SolutionADRLink.query.filter_by(session_id=session_id).first() is not None

    login_as(client, user)
    resp = client.post(f"/architecture/decisions/{decision_id}/delete")
    assert resp.status_code in (302, 303)

    with tenant_ctx(org.id):
        assert ArchitectureDecision.query.filter_by(id=decision_id).first() is None
        assert SolutionADRLink.query.filter_by(session_id=session_id).first() is None
