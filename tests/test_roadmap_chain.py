"""The gap, work package, plateau and requirement rules the roadmap pages share.

Each rule has one implementation; these tests pin its answer and that another
organisation's records never enter it.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _package(service, org, name, **fields):
    return service.create_work_package(organization_id=org.id, name=name, **fields)


def test_progress_is_one_figure_from_status_and_dates(db_session, make_org, tenant_ctx):
    from app.services import work_package_service as service

    org = make_org("progress")
    with tenant_ctx(org.id):
        done = _package(service, org, "Done", status="completed")
        planned = _package(service, org, "Planned", status="planned", progress_percentage=0)
        bare = _package(service, org, "Bare", status="planned")
        bare.progress_percentage = None
        under_way = _package(
            service, org, "Under way", status="in_progress",
            start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 11))
        recorded = _package(
            service, org, "Recorded", status="in_progress", progress_percentage=40,
            start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 11))
        undated = _package(service, org, "Undated", status="in_progress")
        cancelled = _package(service, org, "Cancelled", status="cancelled")
        late = _package(
            service, org, "Late", status="in_progress",
            start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 11))

        # A completed package reads 100 whatever was stored; a stored 0 does not hide it.
        assert done.progress_percentage in (0, None)
        assert service.progress_percent(done) == 100
        assert service.progress_percent(planned) == 0
        assert service.progress_percent(bare) is None
        assert service.progress_percent(under_way, today=date(2026, 1, 6)) == 50
        assert service.progress_percent(recorded, today=date(2026, 1, 6)) == 40
        # Past its end date and not completed, it never claims to be finished.
        assert service.progress_percent(late, today=date(2026, 3, 1)) == 99
        # Nothing to measure from is a dash, not a zero.
        assert service.progress_percent(undated) is None
        assert service.progress_percent(cancelled) is None


def test_every_roadmap_reader_gives_the_same_progress(db_session, make_org, tenant_ctx):
    from app.modules.genome.services.roadmap_slice import build_roadmap_slice
    from app.services import work_package_service as service

    org = make_org("agree")
    with tenant_ctx(org.id):
        done = _package(service, org, "Done", status="completed")
        db_session.flush()
        shown = service.to_roadmap_dict(done, org.id)
        in_slice = {w["id"]: w for w in build_roadmap_slice(org.id)["work_packages"]}[done.id]
        assert shown["percent_complete"] == 100
        assert in_slice["percent_complete"] == 100


def test_plateau_membership_is_one_rule_for_both_roadmaps(db_session, make_org, tenant_ctx):
    from app.models.implementation_migration import Gap, Plateau
    from app.modules.genome.services.roadmap_board import build_roadmap_board
    from app.services import work_package_service as service
    from app.services.gap_register_service import count_gaps, get_plateau_gap_rows

    mine, theirs = make_org("mine"), make_org("theirs")
    with tenant_ctx(mine.id):
        plateau = Plateau(name="Target", sequence_order=1, organization_id=mine.id)
        gap = Gap(name="A gap", organization_id=mine.id, severity="high")
        db_session.add_all([plateau, gap])
        db_session.flush()
        package = _package(service, mine, "Close it", status="in_progress")
        service.link_plateau(package.id, plateau.id, organization_id=mine.id)
        service.link_gap(package.id, gap.id, organization_id=mine.id)
        # Linking twice changes nothing.
        service.link_plateau(package.id, plateau.id, organization_id=mine.id)
        db_session.flush()
    with tenant_ctx(theirs.id):
        other_plateau = Plateau(name="Theirs", sequence_order=1, organization_id=theirs.id)
        other_gap = Gap(name="Their gap", organization_id=theirs.id)
        db_session.add_all([other_plateau, other_gap])
        db_session.flush()
        other_package = _package(service, theirs, "Their package", status="planned")
        service.link_plateau(other_package.id, other_plateau.id, organization_id=theirs.id)
        # Another organisation's plateau cannot be linked.
        with pytest.raises(service.WorkPackageNotFound):
            service.link_plateau(other_package.id, plateau.id, organization_id=theirs.id)
        db_session.flush()

    with tenant_ctx(theirs.id):
        assert count_gaps(theirs.id) == 1
    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == 1
        assert service.plateau_work_package_ids([plateau.id], mine.id) == {plateau.id: {package.id}}
        rows = get_plateau_gap_rows([plateau.id], mine.id)[plateau.id]
        assert [(r["name"], r["via"]) for r in rows] == [("A gap", "work package")]
        board = build_roadmap_board(mine.id)
        cards = [c for col in board["columns"] for c in col["cards"]]
        assert [c["title"] for c in cards] == ["Target"]
        assert [i["id"] for i in cards[0]["items"]] == [package.id]
        assert [g["name"] for g in cards[0]["gaps"]] == ["A gap"]
        assert "Not on a plateau yet" not in [c["title"] for c in cards]


def test_prioritisation_lists_the_backlogs_requirements(db_session, make_org, tenant_ctx):
    from app.models.solution_architect_models import RequirementType, SolutionRequirement
    from app.services import prioritisation_service as service

    mine, theirs = make_org("backlog"), make_org("other")
    with tenant_ctx(mine.id):
        db_session.add_all([
            SolutionRequirement(
                name="Export invoices", description="x", requirement_type=RequirementType.FUNCTIONAL,
                organization_id=mine.id, moscow_priority="MUST"),
            SolutionRequirement(
                name="Archive ledgers", description="x", requirement_type=RequirementType.FUNCTIONAL,
                organization_id=mine.id),
        ])
        db_session.flush()
    with tenant_ctx(theirs.id):
        db_session.add(SolutionRequirement(
            name="Their secret", description="x", requirement_type=RequirementType.FUNCTIONAL,
            organization_id=theirs.id))
        db_session.flush()

    with tenant_ctx(mine.id):
        assert service.project_backlog_requirements(mine.id) == 2
        # Projecting again adds nothing.
        assert service.project_backlog_requirements(mine.id) == 0
        listed = service.get_backlog_prioritised(method="moscow")
        assert sorted(r["title"] for r in listed) == ["Archive ledgers", "Export invoices"]
        assert [r["moscow_priority"] for r in listed] == ["MUST", None]

        # Ranking a requirement here changes the priority the backlog shows.
        archive = next(r for r in listed if r["title"] == "Archive ledgers")
        service.set_moscow(archive["id"], "COULD")
        source = SolutionRequirement.query.filter_by(name="Archive ledgers").one()
        assert source.moscow_priority == "COULD"
