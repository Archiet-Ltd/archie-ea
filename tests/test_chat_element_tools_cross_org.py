"""An element the chat creates for one organisation is never seen by another."""

import uuid

from tests.test_change_review import PASS_ALL, FakeReviewer, _user
from tests.test_chat_element_tools_reviewed import _ask_for_element, _elements_named


def test_an_applied_element_belongs_to_the_requesters_organisation_only(db_session, make_org, tenant_ctx, monkeypatch):
    org_a, org_b = make_org("el-a"), make_org("el-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    name = "Shared name %s" % uuid.uuid4().hex[:6]
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org_a.id):
        _id, verdict = _ask_for_element(user_a, "create_assessment", name)
        assert verdict["state"] == "applied", verdict
        assert [e.organization_id for e in _elements_named(name)] == [org_a.id]
    with tenant_ctx(org_b.id):
        assert _elements_named(name) == [], "the second organisation never sees the first one's element"
        FakeReviewer([PASS_ALL]).install(monkeypatch)
        _id, verdict_b = _ask_for_element(user_b, "create_assessment", name)
        assert verdict_b["state"] == "applied", verdict_b
        assert [e.organization_id for e in _elements_named(name)] == [org_b.id]
