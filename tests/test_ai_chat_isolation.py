"""AI chat threads, saved sessions, history and the records chat hangs off them
stay inside the caller's organisation.

Release-rule line 1 (isolation), launch-checklist Phase 1.

Two organisations. Organisation A's user ``owner`` has a conversation thread, a
saved session, plain chat history, a page-guide history, a workbench workspace
with a version, an uploaded document, a pending approval and an agent run
record. Organisation B's administrator (the strongest ordinary caller B has)
asks for each of them by id, through every AI chat route that takes one.
Another organisation's id must answer 404 (never 403, so ids cannot be probed),
must show none of A's content, and must leave A's records untouched.

Every case has an owner control: the owner asks for the same record in their own
organisation and gets it, so a "not found" cannot come from a route that was
never reachable in the first place.

The switched-organisation case: a user who belongs to both organisations, with
organisation B active, asking for the records they created while A was active.
Conversation threads, saved sessions, chat history and the page-guide history
carry the organisation that was active when they were made, so every route
refuses them while the other organisation is active.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")

AI = "/ai-chat"
MARK = "ISOLATIONMARK"


@pytest.fixture(autouse=True)
def _csrf_off(monkeypatch):
    from app.utils import csrf_helper

    monkeypatch.setattr(csrf_helper, "validate_csrf", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _guide_on(monkeypatch):
    """The page guide is a feature flag; the history routes are the subject here."""
    from app.modules.ai_chat.services.page_guide_service import PageGuideService

    monkeypatch.setattr(PageGuideService, "is_enabled", staticmethod(lambda: True))


def _user(db_session, org, org_role, label):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"aci-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
        role=Role.query.filter_by(name="User").first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.ai_chat_document import AIChatDocumentUpload
    from app.models.agent_run_record import AgentRunRecord
    from app.models.org_role import OrgRole
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionSessionVersion,
    )
    from app.models.vector_embeddings import ChatMessageEmbedding
    from app.modules.ai_chat.services.page_guide_service import PageGuideService

    a, b = make_org("aci-a"), make_org("aci-b")
    b_admin = _user(db_session, b, "org_admin", "badmin")
    b_admin_id = b_admin.id
    # Home organisation A, also an administrator in B; owns everything seeded below.
    owner = _user(db_session, a, "org_admin", "owner")
    OrgRole.set_role(b.id, owner.id, "org_admin")
    db_session.flush()

    tag = uuid.uuid4().hex[:8]
    now = datetime.utcnow()
    # --- conversation thread (made in organisation A)
    def make_thread(user_id):
        tid = str(uuid.uuid4())
        db_session.execute(
            text(
                "INSERT INTO conversation_threads "
                "(id, user_id, title, model, created_at, updated_at, message_count, organization_id) "
                "VALUES (:i, :u, :t, 'm', :n, :n, 1, :o)"
            ),
            {"i": tid, "u": user_id, "t": f"{MARK} thread {tag}", "n": now, "o": a.id},
        )
        db_session.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, thread_id, role, content, model, tokens, created_at) "
                "VALUES (:i, :t, 'user', :c, NULL, NULL, :n)"
            ),
            {"i": str(uuid.uuid4()), "t": tid, "c": f"{MARK} message {tag}", "n": now},
        )
        return tid

    def make_chat_rows(user_id, session_id, saved=False):
        db_session.add(
            ChatMessageEmbedding(
                chat_session_id=session_id,
                user_id=user_id,
                organization_id=a.id,
                message_text=f"{MARK} history {tag}",
                message_role="user",
                domain="general",
                metadata_json={"session_name": f"{MARK} saved {tag}", "context": {}},
            )
        )

    out = {"a": a, "b": b, "owner": owner, "b_admin_id": b_admin_id, "tag": tag}
    u = owner.id
    out["thread_owner"] = make_thread(u)
    make_chat_rows(u, f"chat_user_{u}")
    make_chat_rows(u, f"saved_{u}_abc12345")
    make_chat_rows(u, PageGuideService(u)._build_session_id("dashboard.overview", f"scope-{tag}"))
    db_session.flush()

    ws = SolutionAnalysisSession(
        name=f"{MARK} workspace {tag}", created_by_id=owner.id, organization_id=a.id
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(
        SolutionSessionVersion(
            session_id=ws.id, version_number=1, version_name=f"{MARK} v1",
            snapshot={"k": MARK}, created_by_id=owner.id,
        )
    )
    doc = AIChatDocumentUpload(
        file_name=f"{tag}.txt", original_filename=f"{MARK} {tag}.txt", status="completed",
        analysis_results=json.dumps({"summary": MARK}), uploaded_by_id=owner.id,
        organization_id=a.id,
    )
    approval = AIChatCRUDApproval(
        user_id=owner.id, organization_id=a.id, operation_type="create",
        entity_type="application", original_command=f"{MARK} create", summary=f"{MARK} summary",
        operation_payload=json.dumps({"name": f"{MARK} app {tag}"}),
        expires_at=datetime.utcnow() + timedelta(days=1), chat_session_id=out["thread_owner"],
    )
    run = AgentRunRecord(
        user_id=owner.id, organization_id=a.id, persona="enterprise_architect",
        tools_called=[], outcome=f"{MARK} run {tag}",
    )
    db_session.add_all([doc, approval, run])
    db_session.flush()
    out.update(workspace=ws.id, doc=doc.id, approval=approval.id, run=run.id)
    db_session.commit()
    return out


def _call(client, login_as, user, method, url, body=None, active_org=None):
    login_as(client, user)
    if active_org is not None:
        with client.session_transaction() as sess:
            sess["current_org_id"] = active_org
    kwargs = {"headers": {"X-CSRFToken": "test"}}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(url, **kwargs)


def _count(db_session, sql, **params):
    # Raw SQL so no organisation filter can hide a row.
    return db_session.execute(text(sql), params).scalar()


def _intact(db_session, w):
    """Everything seeded for the owner, counted without any tenant filter."""
    uid = w["owner"].id
    return {
        "thread": _count(db_session, "select count(*) from conversation_threads where id = :i", i=w["thread_owner"]),
        "messages": _count(db_session, "select count(*) from conversation_messages where thread_id = :i", i=w["thread_owner"]),
        "history": _count(db_session, "select count(*) from chat_message_embeddings where user_id = :u", u=uid),
        "doc": _count(db_session, "select count(*) from ai_chat_document_uploads where id = :i", i=w["doc"]),
        "approval": _count(db_session, "select status from ai_chat_crud_approvals where id = :i", i=w["approval"]),
        "run": _count(db_session, "select count(*) from agent_run_records where id = :i", i=w["run"]),
        "workspace": _count(db_session, "select count(*) from solution_analysis_sessions where id = :i", i=w["workspace"]),
    }


# name -> (method, url builder, body builder or None)
ID_ROUTES = {
    "thread-read": ("get", lambda w: f"{AI}/threads/{w['thread_owner']}", None),
    "thread-delete": ("delete", lambda w: f"{AI}/threads/{w['thread_owner']}", None),
    "session-load": ("get", lambda w: f"{AI}/session/abc12345", None),
    "workspace-read": ("get", lambda w: f"{AI}/workspace/{w['workspace']}", None),
    "workspace-versions": ("get", lambda w: f"{AI}/workspace/{w['workspace']}/versions", None),
    "approval-impact": ("get", lambda w: f"{AI}/approvals/{w['approval']}/impact", None),
    "approval-approve": ("post", lambda w: f"{AI}/approvals/{w['approval']}/approve", {}),
    "approval-reject": ("post", lambda w: f"{AI}/approvals/{w['approval']}/reject", {}),
    "tool-approve": ("post", lambda w: f"{AI}/tools/approve/{w['approval']}", {}),
    "tool-reject": ("post", lambda w: f"{AI}/tools/reject/{w['approval']}", {}),
    "document-read": ("get", lambda w: f"{AI}/documents/{w['doc']}", None),
    "document-delete": ("delete", lambda w: f"{AI}/documents/{w['doc']}", None),
    "document-reanalyze": ("post", lambda w: f"{AI}/documents/{w['doc']}/re-analyze", {}),
    "document-feedback": ("post", lambda w: f"{AI}/documents/{w['doc']}/feedback", {"feedback": "x", "corrections": []}),
    "document-compare": ("get", lambda w: f"{AI}/documents/{w['doc']}/compare", None),
    "run-record-read": ("get", lambda w: f"{AI}/run-records/{w['run']}", None),
    "run-record-replay": ("post", lambda w: f"{AI}/run-records/{w['run']}/replay", {}),
}

# Routes that list or clear the caller's own rows and take no id of a record.
LIST_ROUTES = {
    "history-read": ("get", f"{AI}/history", None),
    "history-clear": ("post", f"{AI}/history/clear", {}),
    "sessions-list": ("get", f"{AI}/sessions", None),
    "threads-list": ("get", f"{AI}/threads", None),
    "guide-history-read": ("get", f"{AI}/guide/history?page_key=dashboard.overview&scope_key=scope-{{tag}}", None),
    "documents-list": ("get", f"{AI}/documents", None),
    "approvals-pending": ("get", f"{AI}/approvals/pending?chat_session_id={{thread}}", None),
    "approvals-queue": ("get", f"{AI}/approvals/queue", None),
    "approvals-decided": ("get", f"{AI}/approvals/decided", None),
    "guide-history-clear": ("post", f"{AI}/guide/history/clear", {"page_key": "dashboard.overview", "scope_key": "scope-{tag}"}),
}

def _fill(value, w):
    if isinstance(value, str):
        return value.replace("{tag}", w["tag"]).replace("{thread}", w["thread_owner"])
    if isinstance(value, dict):
        return {k: _fill(v, w) for k, v in value.items()}
    return value


class _B:
    """Organisation B's administrator, loaded fresh each time."""

    @staticmethod
    def user(w):
        from app.models.user import User

        return User.query.get(w["b_admin_id"])


# --------------------------------------------------------------------------
# Another organisation's id
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ID_ROUTES))
def test_other_organisation_id_answers_404_and_changes_nothing(
    client, login_as, db_session, world, name
):
    method, url_for, body = ID_ROUTES[name]
    before = _intact(db_session, world)
    resp = _call(client, login_as, _B.user(world), method, url_for(world), body)
    text_ = resp.get_data(as_text=True)
    assert MARK not in text_, f"{name} showed organisation A's content to organisation B"
    assert resp.status_code == 404, f"{name} answered {resp.status_code}: {text_[:200]}"
    assert _intact(db_session, world) == before, f"{name} changed organisation A's records"


READ_ROUTES = {n for n, (m, _u, _b) in ID_ROUTES.items() if m == "get"}


@pytest.mark.parametrize("name", sorted(ID_ROUTES))
def test_owner_control_reaches_own_record(client, login_as, db_session, world, name):
    """The route is reachable and finds the record, so the 404 above means something."""
    method, url_for, body = ID_ROUTES[name]
    resp = _call(client, login_as, world["owner"], method, url_for(world), body)
    detail = f"{name} owner control answered {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
    if name in READ_ROUTES:
        assert resp.status_code == 200, detail
    else:
        body_text = resp.get_data(as_text=True).lower()
        assert not (resp.status_code == 404 and "not found" in body_text), detail


@pytest.mark.parametrize("name", ["history-read", "sessions-list", "threads-list", "guide-history-read", "documents-list", "approvals-pending"])
def test_owner_control_lists_show_own_rows(client, login_as, db_session, world, name):
    method, url, body = LIST_ROUTES[name]
    resp = _call(client, login_as, world["owner"], method, _fill(url, world), _fill(body, world))
    assert resp.status_code == 200, f"{name} answered {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
    assert MARK in resp.get_data(as_text=True), f"{name} did not list the owner's own rows"


@pytest.mark.parametrize("name", sorted(LIST_ROUTES))
def test_other_organisation_lists_show_nothing_of_ours_and_clears_touch_nothing(
    client, login_as, db_session, world, name
):
    method, url, body = LIST_ROUTES[name]
    before = _intact(db_session, world)
    resp = _call(client, login_as, _B.user(world), method, _fill(url, world), _fill(body, world))
    assert MARK not in resp.get_data(as_text=True), f"{name} showed organisation A's content to B"
    assert resp.status_code in (200, 404, 503), resp.status_code
    assert _intact(db_session, world) == before, f"{name} changed organisation A's records"


def test_other_organisation_saving_a_session_does_not_write_into_ours(
    client, login_as, db_session, world
):
    before = _intact(db_session, world)
    resp = _call(
        client, login_as, _B.user(world), "post", f"{AI}/session/save",
        {"session_name": "b session", "messages": [{"role": "user", "content": "b only"}], "context": {}},
    )
    assert resp.status_code in (200, 400, 500)
    assert _intact(db_session, world) == before
    leaked = _count(
        db_session,
        "select count(*) from chat_message_embeddings where user_id = :u",
        u=world["owner"].id,
    )
    assert leaked == before["history"]


def test_a_foreign_thread_id_on_a_new_message_is_not_continued(db_session, world):
    """persist_turn must start a fresh thread, not append to someone else's."""
    from app.services.conversation_history import persist_turn, thread_owned_by

    b_id = world["b_admin_id"]
    before = _count(db_session, "select count(*) from conversation_messages where thread_id = :i", i=world["thread_owner"])
    new_id = persist_turn(b_id, world["thread_owner"], "b says hi", "reply")
    after = _count(db_session, "select count(*) from conversation_messages where thread_id = :i", i=world["thread_owner"])
    assert new_id and new_id != world["thread_owner"]
    assert after == before
    assert thread_owned_by(new_id, b_id)
    assert not thread_owned_by(world["thread_owner"], b_id)


def test_a_foreign_thread_id_is_not_loaded_into_the_prompt(db_session, world):
    from app.modules.ai_chat.routes.chat_core import _load_history

    assert _load_history(world["thread_owner"], world["b_admin_id"]) == []


# --------------------------------------------------------------------------
# The switched-organisation case: one user, both organisations
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        n
        for n in sorted(ID_ROUTES)
        if n.startswith(("thread-read", "thread-delete", "session-", "workspace", "approval-impact",
                         "document-read", "run-record-read"))
    ],
)
def test_switched_organisation_does_not_see_the_other_organisations_record(
    client, login_as, db_session, world, name
):
    method, url_for, body = ID_ROUTES[name]
    resp = _call(client, login_as, world["owner"], method, url_for(world), body, active_org=world["b"].id)
    text_ = resp.get_data(as_text=True)
    assert MARK not in text_, f"{name} showed organisation A's record while B was active"
    assert resp.status_code in (404, 403), f"{name} answered {resp.status_code}"


@pytest.mark.parametrize(
    "name",
    [
        n
        for n in ("history-read", "sessions-list", "threads-list", "guide-history-read", "documents-list", "approvals-pending")
    ],
)
def test_switched_organisation_lists_do_not_include_the_other_organisations_rows(
    client, login_as, db_session, world, name
):
    method, url, body = LIST_ROUTES[name]
    resp = _call(
        client, login_as, world["owner"], method, _fill(url, world), _fill(body, world),
        active_org=world["b"].id,
    )
    assert MARK not in resp.get_data(as_text=True), f"{name} showed organisation A's rows while B was active"
