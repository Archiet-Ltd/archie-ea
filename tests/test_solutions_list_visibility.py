"""The solutions list must not hide real work (S-01).

17 Aug 2026 QA addendum: `/solutions/` reported "Showing 6 solutions · Page 1
of 1" — an explicit assertion of completeness — while the API held 8. The
default view drops "empty draft shells", but judged emptiness on description
length alone, so a draft whose description box happened to be blank was hidden
regardless of how much architecture it carried. That hid solution 17, the only
solution in the entire instance with real content (4 elements, 3 relationships,
25% blueprint completeness, version 56): the single record that best
demonstrated the product was invisible, and the page said nothing was missing.

Note on attribution: the addendum blamed a `solution_type IS NULL` filter.
Measured against production, all three hidden rows were `status='draft'` with
zero-length descriptions, and `solution_type` is merely correlated — no query
filters on it. Fixing per the addendum's stated cause would have changed
nothing, which is why these tests pin the behaviour (is real work visible?)
rather than the mechanism.
"""

import uuid

import pytest


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def org_client(app, db_session, make_org):
    from app.models.user import User

    from app.models.user import Role

    Role.insert_roles()
    org = make_org("solvis")
    user = User(
        email=f"solvis-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Sol",
        last_name="Vis",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
        is_org_admin=True,
        role=Role.query.filter_by(name="Administrator").first(),
    )
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    _login(client, user.id)
    return org, client, user


def test_draft_with_blueprint_content_is_not_hidden(org_client, tenant_ctx):
    """A draft carrying real architecture must appear even with no description.

    This is the exact solution-17 case: blank description, but genuine
    blueprint work behind it.
    """
    org, client, user = org_client
    from app import db
    from app.models.solution_models import Solution

    marker = f"ContentfulDraft{uuid.uuid4().hex[:6]}"
    with tenant_ctx(org.id):
        sol = Solution(
            name=marker,
            status="draft",
            description="",
            section_narratives={"executive_summary": "Real architecture work lives here."},
            version=7,
        )
        db.session.add(sol)
        db.session.commit()

    _login(client, user.id)
    resp = client.get("/solutions/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert marker in body, (
        "a draft with blueprint narratives and version history was hidden from "
        "the list — real work must never be filtered out by an empty description"
    )


def test_genuinely_empty_shell_is_hidden_but_disclosed(org_client, tenant_ctx):
    """A true shell may be hidden — but the page must admit it is hiding it."""
    org, client, user = org_client
    from app import db
    from app.models.solution_models import Solution

    marker = f"EmptyShell{uuid.uuid4().hex[:6]}"
    with tenant_ctx(org.id):
        db.session.add(
            Solution(name=marker, status="draft", description="", version=1)
        )
        db.session.commit()

    _login(client, user.id)
    resp = client.get("/solutions/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert marker not in body, "an genuinely empty draft shell should be filtered by default"
    assert "hidden" in body.lower(), (
        "the list hid a record without disclosing it — 'Page 1 of 1' must not "
        "assert completeness while rows are being withheld"
    )
