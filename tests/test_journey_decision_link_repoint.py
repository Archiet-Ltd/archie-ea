"""R1-02: journey decision links resolve against the live decision register.

`JOURNEY_LINK_RESOLVERS["decision"]` used to read `ArchitectureDecisionRecord`, a
model with live AI-chat writers but no ARB-facing reader (sdd.md 9.1-9.3). This
repoints it to `ArchitectureDecision`, the register ARB itself uses, and proves
no ARB-facing surface still depends on the dead model (M8), that a missing
target renders honestly instead of vanishing, and that the one-off CLI remap
never crosses a tenant boundary and never deletes a row.

Follows tests/test_tenant_isolation.py's pattern: shared db_session/make_org
fixtures, never a hand-rolled module-scoped app fixture.
"""

from __future__ import annotations

import pathlib
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------- #
# M8: source scan -- the ARB-facing surfaces carry no live reference to  #
# the dead model, only comments (sdd.md 9.3).                            #
# --------------------------------------------------------------------- #

_M8_SCAN_ROOTS = (
    "app/modules/architecture/routes",
    "app/modules/transformation_room",
)
_M8_SCAN_FILES = (
    "app/modules/solutions_strategic/v2/services/journey_home.py",
)


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _live_references(path: pathlib.Path) -> list[str]:
    """Lines naming ArchitectureDecisionRecord outside a `#` comment."""
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if "ArchitectureDecisionRecord" not in line:
            continue
        code_part = line.split("#", 1)[0]
        if "ArchitectureDecisionRecord" in code_part:
            hits.append(f"{path}:{lineno}: {line.strip()}")
    return hits


def test_m8_arb_facing_surfaces_carry_no_live_reference_to_the_dead_model():
    root = _repo_root()
    offenders: list[str] = []
    for scan_root in _M8_SCAN_ROOTS:
        for path in (root / scan_root).glob("arb_*.py"):
            offenders.extend(_live_references(path))
    for relative in _M8_SCAN_FILES:
        offenders.extend(_live_references(root / relative))
    assert offenders == [], "live ArchitectureDecisionRecord reference(s):\n" + "\n".join(offenders)


def test_journey_link_entity_types_comment_names_the_live_register():
    """The comment at architecture_journey_link.py naming the decision link's
    target model is corrected, not merely silent."""
    path = _repo_root() / "app" / "models" / "architecture_journey_link.py"
    text = path.read_text(encoding="utf-8")
    match = re.search(r'"decision",\s*#\s*(.+)', text)
    assert match is not None
    assert "ArchitectureDecisionRecord" not in match.group(1)
    assert "ArchitectureDecision" in match.group(1)


# --------------------------------------------------------------------- #
# Resolver: reads the register, not the dead model.                      #
# --------------------------------------------------------------------- #


@pytest.fixture
def org(make_org):
    return make_org("decision-repoint")


@pytest.fixture
def owner(db_session, org):
    from app.models.user import User

    user = User(
        email=f"repoint-owner-{uuid.uuid4().hex[:10]}@example.test",
        first_name="Re",
        last_name="Point",
        confirmed=True,
        organization_id=org.id,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _legacy_record_kwargs():
    """The four NOT-NULL, no-default text fields ArchitectureDecisionRecord
    requires (Michael Nygard format) -- irrelevant to this repoint, but the
    row must exist to construct one."""
    return {
        "context": "test fixture context",
        "decision": "test fixture decision",
        "rationale": "test fixture rationale",
        "consequences": "test fixture consequences",
    }


@pytest.fixture
def journey(db_session, owner):
    from app.models.architecture_journey import ArchitectureJourney

    row = ArchitectureJourney(
        owner_id=owner.id,
        organization_id=owner.organization_id,
        title="Decision link repoint",
        intent="risk_and_compliance",
        selected_layers=["motivation", "governance"],
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_decision_link_resolves_against_the_register(db_session, owner, journey):
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_journey_link import ArchitectureJourneyLink
    from app.modules.solutions_strategic.v2.services.journey_home import (
        resolve_journey_link_target,
    )

    decision = ArchitectureDecision(
        organization_id=owner.organization_id,
        title="Adopt the reference template family",
        status="accepted",
    )
    db_session.add(decision)
    db_session.flush()

    link = ArchitectureJourneyLink(
        organization_id=owner.organization_id,
        journey_id=journey.id,
        entity_type="decision",
        entity_id=decision.id,
        relation="produces",
        created_by_id=owner.id,
    )
    db_session.add(link)
    db_session.flush()

    resolved = resolve_journey_link_target("decision", decision.id, owner.organization_id)
    assert resolved == {
        "entity_id": decision.id,
        "label": "Adopt the reference template family",
        "status": "accepted",
    }


def test_missing_decision_target_renders_not_silently_dropped(db_session, owner, journey):
    """US-15 AC2: a deleted decision must not make the link vanish."""
    from app.models.architecture_journey_link import ArchitectureJourneyLink
    from app.modules.solutions_strategic.v2.services.journey_home import journey_home_view

    link = ArchitectureJourneyLink(
        organization_id=owner.organization_id,
        journey_id=journey.id,
        entity_type="decision",
        entity_id=999_999_999,
        relation="produces",
        created_by_id=owner.id,
    )
    db_session.add(link)
    db_session.flush()
    db_session.commit()

    view = journey_home_view(journey_id=journey.id, actor_user=owner)
    assert view is not None
    decisions = view["links"].get("decision", [])
    assert len(decisions) == 1
    assert decisions[0]["label"] == f"Record no longer available (link {link.id})"
    assert decisions[0]["unresolved"] is True


# --------------------------------------------------------------------- #
# CLI: flask repoint-journey-decision-links                              #
# --------------------------------------------------------------------- #


def test_repoint_cli_dry_run_changes_nothing(db_session, owner, journey):
    from app.commands.repoint_journey_decision_links import _run
    from app.models.adr import ArchitectureDecisionRecord
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_journey_link import ArchitectureJourneyLink

    old_record = ArchitectureDecisionRecord(
        organization_id=owner.organization_id,
        adr_number=1,
        title="Legacy record",
        status="accepted",
        **_legacy_record_kwargs(),
    )
    db_session.add(old_record)
    db_session.flush()
    link = ArchitectureJourneyLink(
        organization_id=owner.organization_id,
        journey_id=journey.id,
        entity_type="decision",
        entity_id=old_record.id,
        relation="produces",
        created_by_id=owner.id,
    )
    db_session.add(link)
    db_session.flush()
    db_session.commit()

    result = _run(dry_run=True, organization_id=owner.organization_id)
    assert result["backup"] is not None

    db_session.refresh(link)
    assert link.entity_id == old_record.id, "dry-run must not write"
    projections = db_session.query(ArchitectureDecision).filter_by(
        organization_id=owner.organization_id
    ).count()
    assert projections == 0


def test_repoint_cli_apply_creates_projection_and_repoints_link(db_session, owner, journey):
    from app.commands.repoint_journey_decision_links import _run
    from app.models.adr import ArchitectureDecisionRecord
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_journey_link import ArchitectureJourneyLink

    old_record = ArchitectureDecisionRecord(
        organization_id=owner.organization_id,
        adr_number=2,
        title="Legacy record to remap",
        status="accepted",
        **_legacy_record_kwargs(),
    )
    db_session.add(old_record)
    db_session.flush()
    old_record_id = old_record.id
    link = ArchitectureJourneyLink(
        organization_id=owner.organization_id,
        journey_id=journey.id,
        entity_type="decision",
        entity_id=old_record_id,
        relation="produces",
        created_by_id=owner.id,
    )
    db_session.add(link)
    db_session.flush()
    link_id = link.id
    db_session.commit()

    result = _run(dry_run=False, organization_id=owner.organization_id)
    assert result["repointed"] == 1
    assert result["created"] == 1

    refreshed = db_session.get(ArchitectureJourneyLink, link_id)
    assert refreshed.entity_id != old_record_id

    projection = db_session.get(ArchitectureDecision, refreshed.entity_id)
    assert projection is not None
    assert projection.source_table == "architecture_decision_records"
    assert projection.source_id == old_record_id
    assert projection.title == "Legacy record to remap"
    assert projection.organization_id == owner.organization_id

    # Idempotent: a second apply run finds nothing left to repoint for this org.
    second = _run(dry_run=False, organization_id=owner.organization_id)
    assert second["repointed"] == 0
    assert second["created"] == 0


def test_repoint_cli_never_maps_to_another_organisations_decision(
    db_session, make_org, owner, journey
):
    """T-D8 half: the projection created for org A's link stays scoped to org A
    even when the remap runs across every organisation at once."""
    from app.commands.repoint_journey_decision_links import _run
    from app.models.adr import ArchitectureDecisionRecord
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_journey_link import ArchitectureJourneyLink
    from app.models.user import User

    foreign_org = make_org("decision-repoint-foreign")
    foreign_user = User(
        email=f"repoint-foreign-{uuid.uuid4().hex[:10]}@example.test",
        confirmed=True,
        organization_id=foreign_org.id,
        enterprise_role="enterprise_architect",
    )
    db_session.add(foreign_user)
    db_session.flush()

    record_a = ArchitectureDecisionRecord(
        organization_id=owner.organization_id, adr_number=3, title="Org A record",
        status="accepted", **_legacy_record_kwargs(),
    )
    record_b = ArchitectureDecisionRecord(
        organization_id=foreign_org.id, adr_number=3, title="Org B record",
        status="accepted", **_legacy_record_kwargs(),
    )
    db_session.add_all([record_a, record_b])
    db_session.flush()

    link_a = ArchitectureJourneyLink(
        organization_id=owner.organization_id,
        journey_id=journey.id,
        entity_type="decision",
        entity_id=record_a.id,
        relation="produces",
        created_by_id=owner.id,
    )
    db_session.add(link_a)
    db_session.flush()
    db_session.commit()

    _run(dry_run=False, organization_id=None)

    db_session.refresh(link_a)
    projection = db_session.get(ArchitectureDecision, link_a.entity_id)
    assert projection.organization_id == owner.organization_id
    assert projection.title == "Org A record"
    assert projection.source_id == record_a.id
