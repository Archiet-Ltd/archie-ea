"""ARCH-012: data-quality metrics must be actionable worklists, not dead counts.

/archimate/api/elements/search accepted has_rels/has_solutions query params
from the elements.html "Advanced" panel and silently ignored both — the
dropdowns did nothing. This adds server-side support for has_rels,
has_solutions and the new has_desc, and asserts each actually filters the
result set rather than returning everything regardless of the query param.
"""

import uuid

import pytest


@pytest.fixture
def org_client(app, db_session, make_org, login_as):
    from app.models.user import Permission, Role, User

    org = make_org("arch012")
    user = User(
        email=f"arch012-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Arch",
        last_name="Zero12",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    elif role.permissions is None or (role.permissions & Permission.ADMINISTER) != Permission.ADMINISTER:
        role.permissions = Permission.ADMINISTER
    user.role = role
    db_session.flush()
    client = app.test_client()
    login_as(client, user)
    return org, client, user


def _seed(db_session, tenant_ctx, org_id):
    """One element with a description, one without — both with no
    relationships and no solution links, since those subqueries are global
    (ArchiMateElement is not tenant-scoped the same way relationships are)."""
    from app import db
    from app.models.archimate_core import ArchiMateElement

    tag = uuid.uuid4().hex[:8]
    with tenant_ctx(org_id):
        described = ArchiMateElement(
            name=f"ARCH012 Described {tag}",
            type="ApplicationComponent",
            layer="application",
            description="Has a description.",
        )
        undescribed = ArchiMateElement(
            name=f"ARCH012 Undescribed {tag}",
            type="ApplicationComponent",
            layer="application",
            description=None,
        )
        db.session.add_all([described, undescribed])
        db.session.flush()
        return described.id, undescribed.id, tag


def test_has_desc_no_excludes_described_elements(org_client, db_session, tenant_ctx, login_as):
    org, client, user = org_client
    _described_id, undescribed_id, tag = _seed(db_session, tenant_ctx, org.id)

    login_as(client, user)
    resp = client.get("/archimate/api/elements/search?q=ARCH012&has_desc=no&limit=200")
    assert resp.status_code == 200
    data = resp.get_json()
    ids = {el["id"] for el in data["data"]}
    assert undescribed_id in ids, "has_desc=no must return the element with no description"
    assert all(not el["description"] for el in data["data"] if el["id"] in ids), (
        "every element returned under has_desc=no must actually lack a description"
    )


def test_has_desc_yes_excludes_undescribed_elements(org_client, db_session, tenant_ctx, login_as):
    org, client, user = org_client
    described_id, undescribed_id, tag = _seed(db_session, tenant_ctx, org.id)

    login_as(client, user)
    resp = client.get("/archimate/api/elements/search?q=ARCH012&has_desc=yes&limit=200")
    assert resp.status_code == 200
    data = resp.get_json()
    ids = {el["id"] for el in data["data"]}
    assert described_id in ids
    assert undescribed_id not in ids, (
        "has_desc=yes must not return an element with no description — before the fix "
        "this query param was silently ignored and both elements came back"
    )


def test_has_rels_no_param_is_read_not_ignored(org_client, db_session, tenant_ctx, login_as):
    """FAIL-FIRST regression: before the fix, has_rels was accepted in the
    querystring and never referenced in the handler, so has_rels=yes and
    has_rels=no returned byte-identical result sets. Neither element in this
    fixture has any relationship, so has_rels=yes must exclude both."""
    org, client, user = org_client
    _described_id, _undescribed_id, tag = _seed(db_session, tenant_ctx, org.id)

    login_as(client, user)
    resp_no = client.get(f"/archimate/api/elements/search?q={tag}&has_rels=no&limit=200")
    login_as(client, user)
    resp_yes = client.get(f"/archimate/api/elements/search?q={tag}&has_rels=yes&limit=200")
    assert resp_no.status_code == 200 and resp_yes.status_code == 200
    no_ids = {el["id"] for el in resp_no.get_json()["data"]}
    yes_ids = {el["id"] for el in resp_yes.get_json()["data"]}
    assert len(no_ids) == 2, "both unrelated elements must show up under has_rels=no"
    assert yes_ids == set(), "neither element has a relationship, so has_rels=yes must return none"
