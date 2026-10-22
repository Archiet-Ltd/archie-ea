"""Attaching a work package to an interface gap: two organisations.

Another organisation's gap, or an initiative that is not the caller's, answers
404 and nothing is written; a Viewer is refused with 403; the caller's own gap
and initiative create the work package.
"""

from __future__ import annotations

import uuid

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role_name="Editor"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"irw-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="data_architect",
        role=Role.query.filter_by(name=role_name).first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    return user


def _register(db_session, org):
    """An architecture, an initiative, its plateau pair and a transition gap."""
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import (
        GAP_KIND_PLATEAU_TRANSITION,
        Gap,
        Plateau,
        TechnologyRoadmapInitiative,
    )

    arch = ArchitectureModel(name=f"Arch {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(arch)
    db_session.flush()
    initiative = TechnologyRoadmapInitiative(
        name="Init", fiscal_year_start=2026, fiscal_year_end=2027, architecture_id=arch.id
    )
    db_session.add(initiative)
    db_session.flush()
    as_is = Plateau(
        name="Current Integration Landscape", architecture_id=arch.id,
        initiative_id=initiative.id, sequence_order=1, organization_id=org.id,
    )
    db_session.add(as_is)
    db_session.flush()
    to_be = Plateau(
        name="S/4HANA-Integrated Landscape", architecture_id=arch.id,
        initiative_id=initiative.id, sequence_order=2, baseline_plateau_id=as_is.id,
        organization_id=org.id,
    )
    db_session.add(to_be)
    db_session.flush()
    gap = Gap(
        name="Interface gap", organization_id=org.id, architecture_id=arch.id,
        gap_kind=GAP_KIND_PLATEAU_TRANSITION,
        originating_plateau_id=as_is.id, target_plateau_id=to_be.id,
    )
    db_session.add(gap)
    db_session.flush()
    return {"initiative": initiative.id, "gap": gap.id}


@pytest.fixture
def world(db_session, make_org):
    a, b = make_org("irw-a"), make_org("irw-b")
    return {
        "a": a, "b": b,
        "editor_a": _user(db_session, a, "edita"),
        "viewer_a": _user(db_session, a, "viewa", role_name="Viewer"),
        "ra": _register(db_session, a),
        "rb": _register(db_session, b),
    }


def _wp_state(db_session):
    return db_session.execute(
        db.text(
            "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.id), '')) FROM work_packages t"
        )
    ).scalar(), db_session.execute(db.text("SELECT count(*) FROM gap_work_packages")).scalar()


def _post(client, login_as, user, gap, initiative):
    login_as(client, user)
    return client.post(
        f"/interface-register/gaps/{gap}/work-packages",
        data={"initiative_id": initiative, "name": "Attached"},
    )


def test_viewer_is_refused_and_nothing_changes(db_session, world, client, login_as):
    before = _wp_state(db_session)
    resp = _post(client, login_as, world["viewer_a"], world["ra"]["gap"], world["ra"]["initiative"])
    assert resp.status_code == 403
    assert _wp_state(db_session) == before


def test_own_gap_and_initiative_attach_a_work_package(db_session, world, client, login_as):
    before = _wp_state(db_session)
    resp = _post(client, login_as, world["editor_a"], world["ra"]["gap"], world["ra"]["initiative"])
    assert resp.status_code == 302, resp.get_data(as_text=True)[:300]
    assert _wp_state(db_session) != before


@pytest.mark.parametrize("gap_org,initiative_org", [("rb", "rb"), ("rb", "ra"), ("ra", "rb")])
def test_other_organisations_gap_or_initiative_is_404_and_nothing_changes(
    db_session, world, client, login_as, gap_org, initiative_org
):
    before = _wp_state(db_session)
    resp = _post(
        client, login_as, world["editor_a"], world[gap_org]["gap"], world[initiative_org]["initiative"]
    )
    assert resp.status_code == 404, (gap_org, initiative_org, resp.status_code)
    assert _wp_state(db_session) == before
