"""The store-agreement gate asks every store and screen the same question.

Three things are pinned here:

* the registry names every store and screen that answers each concept, so a
  consolidation can prove it worked (a missing surface is a disagreement the
  gate can never see);
* the comparison judgement, driven through the gate's own ``--root`` probe
  mode: agreeing surfaces pass, a disagreement fails naming both surfaces and
  both numbers, a declared narrower scope is not reported, all-zero is
  ``no-evidence``, and a store that cannot be scoped to one organisation is a
  finding once it holds rows;
* tenancy: organisation B's rows never change organisation A's counts, whether
  the store has TenantMixin, a bare ``organization_id`` column, or only a link
  to a tenant-owned row.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_store_agreement.py")


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_store_agreement", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

# concept -> the stores and screens that must be asked. Model paths for stores,
# URL paths (without query string) for screens.
EXPECTED = {
    "work packages": [
        "app.models.unified_work_package.UnifiedWorkPackage",
        "app.models.implementation_migration.WorkPackage",
        "app.models.roadmap_models.RoadmapWorkPackage",
        "app.models.implementation_planning.ImplementationWorkPackage",
        "app.models.implementation_migration.TechnologyRoadmapInitiative",
        "/enterprise/api/work-packages",
        "/api/roadmap/work-packages",
        "/api/roadmap-builder/work-packages",
        "/implementation/api/work-packages",
        "/capability-map/api/roadmap/work-packages",
    ],
    "gaps": [
        "app.models.implementation_migration.Gap",
        "app.models.implementation_planning.ImplementationGap",
        "/api/roadmap/gaps",
        "/api/roadmap/statistics",
        "/implementation/api/gaps",
    ],
    "risks": [
        "app.models.risk.Risk",
        "/api/risks",
    ],
    "application owners": [
        "app.models.application_owner.ApplicationOwner",
        "app.models.enterprise_intelligence.ApplicationOwnership",
        "app.models.application_portfolio.ApplicationComponent",
    ],
    # ArchitectureDecisionRecord dropped from this list by the decision-register
    # consolidation: every row is dual-write paired into ArchitectureDecision
    # (app.models.adr.ArchitectureDecisionRecord.pair_with_canonical_register()),
    # so it is a satellite detail-store for review-board fields the canonical
    # model has no column for, not an independent answer to "how many
    # architecture decisions" -- see scripts/check_store_agreement.py's own
    # "architecture decisions" entry for the full reasoning.
    "architecture decisions": [
        "app.models.architecture_decision.ArchitectureDecision",
        "/arb/api/decisions",
    ],
    "pending AI change approvals": [
        "app.models.ai_chat_crud_approval.AIChatCRUDApproval",
        "/ai-chat/approvals/pending",
    ],
    "pending review queue items": [
        "app.models.confidence_review.ReviewQueueItem",
        "/api/confidence/queue",
    ],
    "pending relationship suggestions": [
        "app.models.archimate_core.RelationshipSuggestion",
        "/capability-map/api/archimate/relationship-suggestions",
    ],
    "applications with a recorded annual cost": [
        "app.models.application_portfolio.ApplicationComponent",
        "app.models.enterprise_intelligence.ApplicationCost",
    ],
    "contracts": [
        "app.models.application_portfolio.VendorContract",
        "app.models.archimate_business.Contract",
    ],
    "vendors": [
        "app.models.vendor.vendor_organization.VendorOrganization",
        "/api/v1/vendors/",
    ],
}

# Stores and screens that answer a DIFFERENT question from every concept they
# resemble, and so must never be registered: each would manufacture findings.
NOT_REGISTERED = {
    # computed live from capability coverage; what a gap is here is a product
    # decision, not a gate's
    "/capability-map/api/roadmap/gaps",
    # rows converted out of that live analysis
    "app.models.roadmap_models.RoadmapGap",
    # a compliance control not met, not an architecture gap
    "app.models.compliance_models.ComplianceGap",
    # append-only governance events, several per capability
    "app.models.decision_ledger.DecisionLedger",
}

# Surfaces whose store has no organisation column, no link to attribute a row
# by, and no shared declaration. Each is reported as unscoped once it holds
# rows; adding one is a decision made here, in review.
KNOWN_UNSCOPED = set()


def _targets(concept):
    return {s.target.split("?", 1)[0] for s in gate.CONCEPTS[concept]}


@pytest.mark.parametrize("concept", sorted(EXPECTED))
def test_registry_asks_every_store_and_screen(concept):
    assert concept in gate.CONCEPTS, "%r is not registered" % concept
    missing = set(EXPECTED[concept]) - _targets(concept)
    assert not missing, "%r does not ask %s" % (concept, sorted(missing))


def test_different_questions_are_not_registered():
    registered = set()
    for concept in gate.CONCEPTS:
        registered |= _targets(concept)
    assert not (NOT_REGISTERED & registered), sorted(NOT_REGISTERED & registered)


def test_gaps_compare_only_surfaces_showing_the_same_kinds():
    by_scope = {}
    for surface in gate.CONCEPTS["gaps"]:
        by_scope.setdefault(surface.scope, set()).add(surface.name)
    assert by_scope == {
        "all": {"orm:Gap", "GET /implementation/api/gaps",
                "GET /api/roadmap/gaps"},
        "capability shortfall": {"orm:Gap(capability shortfall)",
                                 "GET /api/roadmap/statistics"},
        "plateau transition": {"orm:Gap(plateau transition)",
                               "orm:ImplementationGap"},
    }


def test_every_orm_surface_resolves_and_is_tenant_scoped(app):
    from app import db

    problems = []
    unscoped = set()
    with app.app_context():
        for concept, surfaces in gate.CONCEPTS.items():
            for surface in surfaces:
                if surface.kind != "orm":
                    continue
                module, _, cls = surface.target.rpartition(".")
                model = getattr(importlib.import_module(module), cls)
                columns = model.__table__.c
                named = list(surface.filter_eq)
                if surface.filter_null:
                    named += ([surface.filter_null]
                              if isinstance(surface.filter_null, str)
                              else list(surface.filter_null))
                if surface.filter_not_null:
                    named += ([surface.filter_not_null]
                              if isinstance(surface.filter_not_null, str)
                              else list(surface.filter_not_null))
                if surface.distinct:
                    named.append(surface.distinct)
                named += [fk for fk, _ in surface.tenant_via]
                for column in named:
                    if column not in columns:
                        problems.append("%s: %s has no column %s"
                                        % (concept, cls, column))
                for _, parent in surface.tenant_via:
                    table = db.metadata.tables.get(parent)
                    if table is None or "organization_id" not in table.c:
                        problems.append("%s: %s links to %s, which has no "
                                        "organization_id" % (concept, cls, parent))
                if (not gate._session_scoped(model)
                        and "organization_id" not in columns
                        and not surface.tenant_via and not surface.shared):
                    unscoped.add(surface.name)
    assert not problems, problems
    assert unscoped == KNOWN_UNSCOPED


def test_every_http_surface_is_a_registered_get_route(app):
    adapter = app.url_map.bind("localhost")
    missing = []
    for concept, surfaces in gate.CONCEPTS.items():
        for surface in surfaces:
            if surface.kind != "http":
                continue
            path = surface.target.split("?", 1)[0]
            try:
                adapter.match(path, method="GET")
            except Exception as exc:  # NotFound, MethodNotAllowed, redirects
                missing.append("%s: %s (%s)" % (concept, path, type(exc).__name__))
    assert not missing, missing


# ---------------------------------------------------------------------------
# The judgement, through the gate's own --root probe mode.
# ---------------------------------------------------------------------------
def _run_probe(tmp_path, probe):
    (tmp_path / "store_agreement_probe.json").write_text(json.dumps(probe))
    proc = subprocess.run([sys.executable, SCRIPT, "--root", str(tmp_path)],
                          capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    return int(lines[-1]), proc.stdout


def _probe(concept, count, overrides=None):
    rows = []
    for surface in gate.CONCEPTS[concept]:
        n = count if surface.scope == "all" else max(count - 1, 0)
        row = {"surface": surface.name, "count": n, "scope": surface.scope}
        if surface.expect_zero:
            # A retired store must hold nothing that is not copied across.
            row["count"] = 0
            row["expect_zero"] = True
        rows.append(row)
    for name, value in (overrides or {}).items():
        for row in rows:
            if row["surface"] == name:
                row["count"] = value
    return {concept: rows}


def _whole_population(concept):
    return [s.name for s in gate.CONCEPTS[concept] if s.scope == "all"]


@pytest.mark.parametrize("concept", sorted(EXPECTED))
def test_probe_agreeing_surfaces_pass(tmp_path, concept):
    probe = _probe(concept, 7)
    # A declared narrower surface reading less than the whole is explained by
    # its declaration, never reported.
    probe[concept].append({"surface": "declared narrower", "count": 2,
                           "scope": "status=open"})
    count, out = _run_probe(tmp_path, probe)
    assert count == 0, out


def _with_two_whole_population_surfaces():
    return sorted(c for c in EXPECTED if len(_whole_population(c)) >= 2)


@pytest.mark.parametrize("concept", _with_two_whole_population_surfaces())
def test_probe_disagreement_names_both_surfaces_and_numbers(tmp_path, concept):
    first, second = _whole_population(concept)[:2]
    count, out = _run_probe(tmp_path, _probe(concept, 7, {second: 10}))
    assert count == 1, out
    finding = [ln for ln in out.splitlines() if "[store-disagreement]" in ln]
    assert len(finding) == 1, out
    assert "%s=10" % second in finding[0]
    assert "%s=7" % first in finding[0]
    assert finding[0].strip().startswith(concept)


@pytest.mark.parametrize("concept", sorted(EXPECTED))
def test_probe_all_zero_is_no_evidence(tmp_path, concept):
    count, out = _run_probe(tmp_path, _probe(concept, 0))
    assert count == 0
    assert "%s [no-evidence]" % concept in out


@pytest.mark.parametrize("concept", sorted(
    c for c in EXPECTED
    if any(s.scope != "all" and not s.expect_zero for s in gate.CONCEPTS[c])))
def test_probe_narrower_scope_exceeding_the_whole_is_reported(tmp_path, concept):
    probe = _probe(concept, 3)
    narrower = [row for row in probe[concept]
                if row["scope"] != "all" and not row.get("expect_zero")]
    narrower[0]["count"] = 9
    count, out = _run_probe(tmp_path, probe)
    assert count >= 1, out
    assert "%s reports 9 under the declared narrowing" % narrower[0]["surface"] in out


def test_probe_unmerged_retired_row_is_its_own_finding(tmp_path):
    probe = _probe("work packages", 7)
    retired = [row for row in probe["work packages"] if row.get("expect_zero")]
    assert len(retired) == 4, retired
    retired[0]["count"] = 2
    count, out = _run_probe(tmp_path, probe)
    assert count == 1, out
    finding = [ln for ln in out.splitlines() if "[retired-store-unmerged]" in ln]
    assert len(finding) == 1, out
    assert retired[0]["surface"] in finding[0] and "2 row(s)" in finding[0]
    assert "[store-disagreement]" not in out


def test_probe_different_gap_kinds_are_not_a_disagreement(tmp_path):
    # 5 rows in the register: 3 capability shortfalls and 2 plateau
    # transitions. Each kind agrees with itself, so nothing is reported.
    counts = {"all": 5, "capability shortfall": 3, "plateau transition": 2}
    probe = {"gaps": [{"surface": s.name, "count": counts[s.scope],
                       "scope": s.scope} for s in gate.CONCEPTS["gaps"]]}
    count, out = _run_probe(tmp_path, probe)
    assert count == 0, out

    # One plateau transition the ArchiMate Gap store does not hold is a real
    # disagreement between two stores answering the same question.
    for row in probe["gaps"]:
        if row["surface"] == "orm:ImplementationGap":
            row["count"] = 1
    count, out = _run_probe(tmp_path, probe)
    assert count == 1, out
    assert "orm:Gap(plateau transition)=2, orm:ImplementationGap=1" in out


def test_probe_unscoped_store_with_rows_is_a_finding(tmp_path):
    probe = _probe("architecture decisions", 4)
    unscoped = {"surface": "orm:SomeUnscopedStore", "count": 11,
                "scope": "all", "unscoped": True}
    probe["architecture decisions"].append(unscoped)
    count, out = _run_probe(tmp_path, probe)
    assert count == 1, out
    assert "[unscoped-store] orm:SomeUnscopedStore holds 11 rows" in out

    unscoped["count"] = 0
    count, out = _run_probe(tmp_path, probe)
    assert count == 0, out


# ---------------------------------------------------------------------------
# Two organisations: B's rows never move A's counts.
# ---------------------------------------------------------------------------
def _seed(db_session, org, user, owners, costs, risks, packages, approvals):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationCost
    from app.models.risk import Risk
    from app.models.roadmap_models import RoadmapWorkPackage

    for i in range(max(owners, costs)):
        application = ApplicationComponent(
            name="App %s %s" % (i, uuid.uuid4().hex[:6]),
            organization_id=org.id,
            business_owner="Owner %s" % i if i < owners else None)
        db_session.add(application)
        db_session.flush()
        if i < owners:
            # Two owner rows for one application still count it once.
            for kind in ("primary", "technical"):
                db_session.add(ApplicationOwner(
                    application_id=application.id, user_id=user.id,
                    organization_id=org.id, ownership_type=kind))
        if i < costs:
            db_session.add(ApplicationCost(
                application_id=application.id, fiscal_year=2026,
                total_cost=1000))
    for i in range(risks):
        db_session.add(Risk(title="Risk %s" % i, likelihood=2, impact=3,
                            organization_id=org.id))
    for i in range(packages):
        db_session.add(RoadmapWorkPackage(name="Package %s" % i,
                                          business_capability="Billing",
                                          created_by=user.id))
    for i in range(approvals):
        db_session.add(AIChatCRUDApproval(
            user_id=user.id, organization_id=org.id, operation_type="create",
            entity_type="capability", original_command="add capability",
            operation_payload="{}", summary="Add a capability",
            expires_at=datetime.utcnow() + timedelta(hours=1)))
    db_session.flush()


def _make_user(db_session, org):
    from app.models.user import User

    user = User(email="sa-%s@example.com" % uuid.uuid4().hex[:10],
                first_name="Store", last_name="Agreement",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _counts(app, tenant_ctx, org_id):
    from app import db

    with tenant_ctx(org_id):
        observations, _notes = gate.observe_tenant(
            app, db, org_id, http=False,
            concepts={k: gate.CONCEPTS[k] for k in (
                "application owners", "applications with a recorded annual cost",
                "risks", "work packages", "pending AI change approvals",
                "architecture decisions")})
    return ({concept: {row[0]: row[1] for row in rows if len(row) < 4 or not row[3]}
             for concept, rows in observations.items()},
            {concept: {row[0] for row in rows if len(row) > 3 and row[3]}
             for concept, rows in observations.items()})


def test_two_organisations_never_change_each_others_counts(
        app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("store-a"), make_org("store-b")
    user_a, user_b = _make_user(db_session, org_a), _make_user(db_session, org_b)
    _seed(db_session, org_a, user_a, owners=2, costs=1, risks=3, packages=1,
          approvals=2)

    before, unscoped = _counts(app, tenant_ctx, org_a.id)
    assert before["application owners"]["orm:ApplicationOwner(applications)"] == 2
    assert before["application owners"][
        "orm:ApplicationOwner(applications, primary)"] == 2
    assert before["application owners"][
        "orm:ApplicationComponent(owner text recorded)"] == 2
    assert before["applications with a recorded annual cost"][
        "orm:ApplicationCost(applications)"] == 1
    assert before["risks"]["orm:Risk"] == 3
    # The roadmap package is copied into the one store as it is written, so
    # the organisation's one store holds it and the retired store holds
    # nothing that is not copied.
    assert before["work packages"]["orm:UnifiedWorkPackage"] == 1
    assert before["work packages"]["orm:RoadmapWorkPackage(unmerged)"] == 0
    assert before["pending AI change approvals"][
        "orm:AIChatCRUDApproval(pending)"] == 2
    assert all(not names for names in unscoped.values()), unscoped

    _seed(db_session, org_b, user_b, owners=5, costs=4, risks=6, packages=3,
          approvals=1)
    after, _ = _counts(app, tenant_ctx, org_a.id)
    assert after == before

    b_counts, _ = _counts(app, tenant_ctx, org_b.id)
    assert b_counts["application owners"]["orm:ApplicationOwner(applications)"] == 5
    assert b_counts["applications with a recorded annual cost"][
        "orm:ApplicationCost(applications)"] == 4
    assert b_counts["risks"]["orm:Risk"] == 6
    assert b_counts["work packages"]["orm:UnifiedWorkPackage"] == 3
    assert b_counts["pending AI change approvals"][
        "orm:AIChatCRUDApproval(pending)"] == 1


def test_row_no_link_attributes_counts_for_no_organisation(
        app, db_session, make_org, tenant_ctx):
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.services import work_package_bridge

    org = make_org("store-orphan")
    user = _make_user(db_session, org)
    # Not yet copied across: the rows stay in the retired store.
    with work_package_bridge.suspended():
        db_session.add(RoadmapWorkPackage(name="Owned", business_capability="Billing",
                                          created_by=user.id))
        db_session.add(RoadmapWorkPackage(name="Orphan", business_capability="Billing"))
        db_session.flush()

    from app import db

    surface = next(s for s in gate.CONCEPTS["work packages"]
                   if s.name == "orm:RoadmapWorkPackage(unmerged)")
    with tenant_ctx(org.id):
        count, why, unscoped, unattributed = gate._count_orm(
            surface, db, org.id)
    assert (count, why, unscoped) == (1, None, False)
    assert unattributed >= 1


# ---------------------------------------------------------------------------
# Live: seeded rows, the gate's own observation and comparison, red and green.
# ---------------------------------------------------------------------------
def _live_findings(app, tenant_ctx, org_id, user, concepts, http):
    from app import db

    with tenant_ctx(org_id):
        observations, notes = gate.observe_tenant(
            app, db, org_id, user, http=http,
            concepts={k: gate.CONCEPTS[k] for k in concepts})
    findings, more = gate.compare(observations)
    return findings, notes + more, observations


def test_live_seeded_disagreement_is_red_and_consistent_state_is_green(
        app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationCost

    concept = "applications with a recorded annual cost"
    org = make_org("store-live")
    user = _make_user(db_session, org)
    application = ApplicationComponent(
        name="Costed %s" % uuid.uuid4().hex[:6], organization_id=org.id,
        license_cost=1200)
    db_session.add(application)
    db_session.flush()

    # The application records a cost; the cost store holds nothing for it.
    findings, notes, observations = _live_findings(
        app, tenant_ctx, org.id, user, [concept], http=False)
    assert len(findings) == 1, (findings, notes, observations)
    assert "[store-disagreement]" in findings[0]
    assert "orm:ApplicationComponent(annual cost recorded)=1" in findings[0]
    assert "orm:ApplicationCost(applications)=0" in findings[0]

    # Record the same cost in the cost store: both stores answer 1.
    db_session.add(ApplicationCost(application_id=application.id,
                                   fiscal_year=2026, total_cost=1200))
    db_session.flush()
    findings, notes, observations = _live_findings(
        app, tenant_ctx, org.id, user, [concept], http=False)
    assert findings == [], (findings, notes)
    assert {row[0]: row[1] for row in observations[concept]} == {
        "orm:ApplicationComponent(annual cost recorded)": 1,
        "orm:ApplicationCost(applications)": 1}


def test_live_gap_kinds_agree_across_stores_and_screens(
        app, db_session, make_org, tenant_ctx):
    """Screens are asked too: a register holding both kinds is not red."""
    from app.models.implementation_migration import Gap

    org = make_org("store-gaps")
    user = _make_user(db_session, org)
    for i in range(3):
        db_session.add(Gap(name="Shortfall %s" % i, organization_id=org.id,
                           gap_kind="capability_shortfall"))
    db_session.flush()

    findings, notes, observations = _live_findings(
        app, tenant_ctx, org.id, user, ["gaps"], http=True)
    counts = {row[0]: row[1] for row in observations["gaps"]}
    assert findings == [], (findings, notes)
    assert counts["orm:Gap"] == 3, (counts, notes)
    assert counts["orm:Gap(capability shortfall)"] == 3, counts
    assert counts.get("GET /api/roadmap/statistics") == 3, (counts, notes)
    # The implementation-planning module is deprecated and answers 404 unless
    # its feature flag is switched on; a screen nobody can open is reported
    # unanswered, never compared.
    if "GET /implementation/api/gaps" in counts:
        assert counts["GET /implementation/api/gaps"] == 3, counts
    else:
        assert any("GET /implementation/api/gaps: HTTP 404" in n
                   for n in notes), notes
    assert counts.get("GET /api/roadmap/gaps") == 3, (counts, notes)


# ---------------------------------------------------------------------------
# R1-B04 PR 2 fix round 1 (D-01): the work packages concept counts the retired
# stores honestly (unmerged rows must be zero) and every list screen reads the
# one store.
# ---------------------------------------------------------------------------
def _admin_user(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator").first()
    user = User(email="wp-sa-%s@example.com" % uuid.uuid4().hex[:10],
                first_name="Store", last_name="Agreement",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _seed_retired_stores(db_session, org, user, work_packages, roadmap, implementation):
    from app.models.implementation_migration import WorkPackage
    from app.models.implementation_planning import ImplementationWorkPackage
    from app.models.roadmap_models import RoadmapWorkPackage
    from tests.test_work_package_consolidation import _app_component

    component = _app_component(db_session, org)
    for i in range(work_packages):
        db_session.add(WorkPackage(name="WP %s %s" % (i, uuid.uuid4().hex[:6]),
                                   organization_id=org.id))
    for i in range(roadmap):
        db_session.add(RoadmapWorkPackage(name="RM %s %s" % (i, uuid.uuid4().hex[:6]),
                                          business_capability="Billing",
                                          created_by=user.id))
    for i in range(implementation):
        db_session.add(ImplementationWorkPackage(
            name="IMP %s %s" % (i, uuid.uuid4().hex[:6]),
            application_component_id=component.id))
    db_session.flush()


def _merge(app):
    result = app.test_cli_runner().invoke(args=["merge-work-package-stores"])
    assert result.exit_code == 0, result.output
    return result


def test_work_packages_concept_agrees_after_merge(
        app, db_session, make_org, tenant_ctx):
    from app.services import work_package_bridge, work_package_service

    org_a, org_b = make_org("wp-agree-a"), make_org("wp-agree-b")
    user_a, user_b = _admin_user(db_session, org_a), _admin_user(db_session, org_b)
    with work_package_bridge.suspended():
        _seed_retired_stores(db_session, org_a, user_a, 5, 3, 4)
        _seed_retired_stores(db_session, org_b, user_b, 2, 2, 2)
    for i in range(2):
        work_package_service.create_work_package(
            organization_id=org_a.id, name="Writer A %s" % i)
    work_package_service.create_work_package(organization_id=org_b.id, name="Writer B")
    db_session.flush()

    # Before the merge the retired stores hold rows that are not copied.
    findings, _notes, _obs = _live_findings(
        app, tenant_ctx, org_a.id, user_a, ["work packages"], http=False)
    assert any("[retired-store-unmerged]" in f for f in findings), findings

    _merge(app)

    findings, notes, observations = _live_findings(
        app, tenant_ctx, org_a.id, user_a, ["work packages"], http=True)
    assert findings == [], (findings, notes)
    assert not any("work packages [no-evidence]" in n for n in notes), notes
    whole = {row[0]: row[1] for row in observations["work packages"]
             if row[2] == "all"}
    assert whole["orm:UnifiedWorkPackage"] == 14, whole
    for screen in ("GET /enterprise/api/work-packages",
                   "GET /api/roadmap/work-packages",
                   "GET /api/roadmap-builder/work-packages",
                   "GET /capability-map/api/roadmap/work-packages"):
        assert whole.get(screen) == 14, (screen, whole, notes)
    assert set(whole.values()) == {14}, whole
    unmerged = {row[0]: row[1] for row in observations["work packages"]
                if row[2] == "unmerged retired rows"}
    assert unmerged and set(unmerged.values()) == {0}, unmerged


def test_work_packages_concept_reports_one_unmerged_retired_row(
        app, db_session, make_org, tenant_ctx):
    from sqlalchemy import text

    from app.services import work_package_bridge

    org = make_org("wp-agree-one")
    user = _admin_user(db_session, org)
    with work_package_bridge.suspended():
        _seed_retired_stores(db_session, org, user, 1, 2, 1)
    _merge(app)
    findings, notes, _obs = _live_findings(
        app, tenant_ctx, org.id, user, ["work packages"], http=False)
    assert findings == [], (findings, notes)

    # One retired row back to unmerged: exactly one finding, naming the store.
    db_session.execute(text(
        "UPDATE roadmap_work_packages SET retired_into_id = NULL, retired_at = NULL "
        "WHERE id = (SELECT min(id) FROM roadmap_work_packages WHERE created_by = :u)"),
        {"u": user.id})
    findings, notes, _obs = _live_findings(
        app, tenant_ctx, org.id, user, ["work packages"], http=False)
    assert len(findings) == 1, (findings, notes)
    assert "[retired-store-unmerged]" in findings[0]
    assert "orm:RoadmapWorkPackage(unmerged)" in findings[0]
    assert "1 row(s)" in findings[0]
