"""A gap made on the ADM board is a gap register record, and only a person who may change the model makes one."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _person(db_session, org, enterprise_role, role_name):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="gap.%s@example.com" % uuid.uuid4().hex[:10], first_name="G", last_name="W",
        organization_id=org.id, enterprise_role=enterprise_role, confirmed=True,
        role=Role.query.filter_by(name=role_name).first(),
    )
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    return user


def _phase(db_session):
    from app.models.adm_kanban import ADMPhase

    phase = ADMPhase.query.filter_by(code="A").first()
    if phase is None:
        phase = ADMPhase(name="Architecture Vision", code="A", order=1)
        db_session.add(phase)
        db_session.flush()
    return phase


def test_a_reader_cannot_make_a_gap_and_an_editor_can(db_session, make_org, tenant_ctx, client, login_as):
    from app.services.gap_register_service import count_gaps

    mine, theirs = make_org("gap-writers"), make_org("gap-others")
    reader = _person(db_session, mine, "viewer", "Viewer")
    editor = _person(db_session, mine, "enterprise_architect", "User")
    _phase(db_session)
    body = {"title": "No single customer record", "phase": "A", "arch_element_type": "Gap"}

    with tenant_ctx(mine.id):
        before = count_gaps(mine.id)
    login_as(client, reader)
    refused = client.post("/api/adm-kanban/v2/cards", json=body)
    assert refused.status_code == 403
    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == before

    login_as(client, editor)
    made = client.post("/api/adm-kanban/v2/cards", json=body)
    assert made.status_code in (200, 201), made.get_data(as_text=True)
    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == before + 1
    with tenant_ctx(theirs.id):
        assert count_gaps(theirs.id) == 0
