"""With approval required, a chat update naming an uneditable field is refused at queue time.

REQUIRE_AI_APPROVAL on, two organisations. A request naming a field outside
the edit allow-list gets 400 naming the field and queues nothing; a request
naming only allowed fields is still queued (202).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_chat_update_editable_fields import (  # noqa: F401  (fixtures)
    D, _bulk, _csrf_off, _forbidden, _send, world, ALLOWED, KINDS,
)

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture(autouse=True)
def _approval_on(monkeypatch, app, _csrf_off):
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)


def _approvals(db_session):
    db_session.expire_all()
    return db_session.execute(text("select count(*) from ai_chat_crud_approvals")).scalar()


@pytest.mark.parametrize("kind", KINDS)
def test_forbidden_field_is_refused_at_queue_time(client, login_as, db_session, world, kind):
    before = _approvals(db_session)
    field, value = _forbidden(kind, world)[0]
    resp = _send(client, login_as, world["architect"], kind, world["recs_a"][kind],
                 {**ALLOWED[kind], field: value})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert field in resp.get_data(as_text=True)
    assert _approvals(db_session) == before


@pytest.mark.parametrize("kind", ["app", "bcap"])
def test_allowed_only_payload_is_still_queued(client, login_as, db_session, world, kind):
    before = _approvals(db_session)
    resp = _send(client, login_as, world["architect"], kind, world["recs_a"][kind],
                 ALLOWED[kind])
    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert _approvals(db_session) == before + 1


@pytest.mark.parametrize("path,id_key,kind", [
    ("bulk-update-applications", "application_id", "app"),
    ("bulk-update-capabilities", "capability_id", "bcap"),
])
def test_bulk_forbidden_field_queues_nothing(client, login_as, db_session, world, path, id_key, kind):
    before = _approvals(db_session)
    resp = _bulk(client, login_as, world, path, id_key, world["recs_a"][kind],
                 {"organization_id": world["recs_b"]["org"]})
    # The item is refused before anything is queued.
    assert resp.get_json()["summary"]["success"] == 0
    assert "organization_id" in resp.get_data(as_text=True)
    assert _approvals(db_session) == before
