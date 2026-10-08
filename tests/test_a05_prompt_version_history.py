"""A-05 remainder: version history with diff and rollback for AIPromptTemplate.

The audit-metadata half (updated_by_id/version columns) shipped earlier; this
covers the actual ask — a snapshot is taken before every mutation, history is
listable, two versions can be diffed, and rollback restores prior content
without destroying history (it's undoable by rolling back again).

New table only (AIPromptTemplateVersion) — relies on `flask init-db`'s
create_all(), not reconcile-schema, per CLAUDE.md schema management rules.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def admin_client(app, db_session, make_org, login_as):
    import uuid

    from app.models import Permission, Role, User

    org = make_org("a05")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"a05-{uuid.uuid4().hex[:8]}@example.com",
        first_name="A05",
        last_name="Admin",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    # The update/reset/rollback routes this fixture exercises are
    # platform-admin-only (pr307-review: AIPromptTemplate has no tenant
    # column, one override serves every organisation) -- an org admin now
    # gets 403/404 on them, same defect class as the Abacus and Jira fixes.
    user.is_platform_admin = True
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client, user


def _prompt_key(db_session):
    """Pick a default prompt key and clear any pre-existing override for it.

    The shared test database is persistent (see tests/conftest.py), and this
    key is deterministic across runs, so a prior session's committed override
    for the same key would otherwise leak into this test's version numbering.
    Deleting it here happens inside db_session's SAVEPOINT, so it is undone
    at teardown exactly like everything else this test writes.
    """
    from app.models.ai_service import AIPromptTemplate, AIPromptTemplateVersion
    from app.modules.admin.routes.solution_prompt_admin import _get_prompt_defaults
    from app.services.solution_prompt_override_service import override_key

    defaults = _get_prompt_defaults()
    assert defaults, "no solution prompt defaults registered — cannot exercise A-05 endpoints"
    key = next(iter(defaults))
    override_name = override_key(key)
    AIPromptTemplateVersion.query.filter_by(template_name=override_name).delete()
    AIPromptTemplate.query.filter_by(name=override_name).delete()
    db_session.flush()
    return key


def test_update_snapshots_prior_version_into_history(admin_client, db_session):
    client, user = admin_client
    key = _prompt_key(db_session)

    r1 = client.post(f"/admin/solution-prompts/{key}/update", json={"prompt_text": "Version one text"})
    assert r1.status_code == 200
    assert r1.get_json()["prompt"]["version"] == 1

    r2 = client.post(f"/admin/solution-prompts/{key}/update", json={"prompt_text": "Version two text"})
    assert r2.status_code == 200
    assert r2.get_json()["prompt"]["version"] == 2

    hist = client.get(f"/admin/solution-prompts/{key}/history")
    assert hist.status_code == 200
    versions = hist.get_json()["versions"]
    # current (v2) plus the snapshotted v1
    texts_by_version = {v["version"]: v["system_prompt"] for v in versions}
    assert texts_by_version[2] == "Version two text"
    assert texts_by_version[1] == "Version one text"


def test_diff_between_two_versions(admin_client, db_session):
    client, user = admin_client
    key = _prompt_key(db_session)
    client.post(f"/admin/solution-prompts/{key}/update", json={"prompt_text": "Alpha"})
    client.post(f"/admin/solution-prompts/{key}/update", json={"prompt_text": "Beta"})

    diff = client.get(f"/admin/solution-prompts/{key}/diff?from=1&to=current")
    assert diff.status_code == 200
    payload = diff.get_json()
    assert payload["identical"] is False
    assert any("Alpha" in line for line in payload["diff"])
    assert any("Beta" in line for line in payload["diff"])


def test_rollback_restores_prior_content_and_stays_undoable(admin_client, db_session):
    client, user = admin_client
    key = _prompt_key(db_session)
    client.post(f"/admin/solution-prompts/{key}/update", json={"prompt_text": "Original"})
    client.post(f"/admin/solution-prompts/{key}/update", json={"prompt_text": "Mistake"})

    rb = client.post(f"/admin/solution-prompts/{key}/rollback/1")
    assert rb.status_code == 200
    body = rb.get_json()
    assert body["prompt"]["current_prompt"] == "Original"
    # rollback got its own version number, not a rewrite of version 1
    assert body["prompt"]["version"] == 3

    # "Mistake" (the state rollback overwrote) must still be in history —
    # rollback did not destroy it.
    hist = client.get(f"/admin/solution-prompts/{key}/history").get_json()["versions"]
    all_texts = {v["system_prompt"] for v in hist}
    assert "Mistake" in all_texts
    assert "Original" in all_texts
