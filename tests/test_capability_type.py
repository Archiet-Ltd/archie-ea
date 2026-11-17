"""A capability is strategic, operational or supporting, in one field, set in the data."""
import uuid

import pytest

from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.unified_capability import UnifiedCapability
from app.models.user import Role, User


pytestmark = pytest.mark.usefixtures("db_session")


def _user(org, role_name, enterprise_role):
    Role.insert_roles()
    user = User(
        email=f"{enterprise_role}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test", last_name=enterprise_role, organization_id=org.id,
        enterprise_role=enterprise_role, confirmed=True,
    )
    user.role = Role.query.filter_by(name=role_name).one()
    user.password = "password"
    db.session.add(user)
    db.session.commit()
    return user


def _capability(org, tenant_ctx, name, level=1, category=None):
    with tenant_ctx(org.id):
        row = BusinessCapability(
            name=name, code=f"T-{uuid.uuid4().hex[:8]}", organization_id=org.id,
            level=level, category=category,
        )
        db.session.add(row)
        db.session.flush()
        db.session.refresh(row)
        return row.id


def _unified(org, business_capability_id):
    return UnifiedCapability.query.filter_by(
        organization_id=org.id, source_table="business_capability",
        source_id=str(business_capability_id),
    ).first()


def test_the_starting_rule_is_written_once_and_sql_agrees_with_python():
    from app.services.capability_type import default_capability_type, default_type_sql

    cases = [("core", 3), ("Supporting", 1), ("Operations", 1), (None, 1), (None, 2),
             (None, 3), ("Customer", 1), ("Customer", 2), ("", 3)]
    for category, level in cases:
        sql = default_type_sql(":c", ":l")
        got = db.session.execute(
            db.text("SELECT " + sql), {"c": category, "l": level}
        ).scalar()
        assert got == default_capability_type(category, level), (category, level)
    assert default_capability_type("core", 3) == "strategic"
    assert default_capability_type(None, 3) == "supporting"


def test_a_capability_is_typed_when_it_arrives(make_org, tenant_ctx):
    org = make_org()
    top = _capability(org, tenant_ctx, "Sell online", level=1)
    deep = _capability(org, tenant_ctx, "Pick items", level=3)
    assert _unified(org, top).capability_type == "strategic"
    assert _unified(org, deep).capability_type == "supporting"


def test_the_backfill_types_the_untyped_and_never_a_typed_one(make_org, tenant_ctx):
    from app.services.capability_type import backfill_capability_types

    org = make_org()
    first = _unified(org, _capability(org, tenant_ctx, "Alpha", level=2))
    second = _unified(org, _capability(org, tenant_ctx, "Beta", level=2))
    db.session.execute(db.text(
        "UPDATE unified_capabilities SET capability_type = NULL WHERE id = :i"), {"i": first.id})
    db.session.execute(db.text(
        "UPDATE unified_capabilities SET capability_type = 'supporting' WHERE id = :i"),
        {"i": second.id})
    backfill_capability_types(db.session.connection())
    db.session.expire_all()
    assert db.session.get(UnifiedCapability, first.id).capability_type == "operational"
    assert db.session.get(UnifiedCapability, second.id).capability_type == "supporting"


def test_the_demo_company_types_every_capability():
    from app.commands.seed_demo_company import _CAPABILITIES, _CAPABILITY_TYPES
    from app.services.capability_type import CAPABILITY_TYPES

    assert {row[1] for row in _CAPABILITIES} == set(_CAPABILITY_TYPES)
    assert set(_CAPABILITY_TYPES.values()) == set(CAPABILITY_TYPES)


def test_the_sample_company_arrives_typed(client, login_as, make_org):
    org = make_org()
    architect = _user(org, "Architect", "enterprise_architect")
    login_as(client, architect)
    assert client.post("/architecture/sample-company/load").status_code == 302
    rows = UnifiedCapability.query.filter_by(organization_id=org.id).all()
    assert len(rows) == 5
    assert {r.name: r.capability_type for r in rows} == {
        "Sell to customers online": "strategic",
        "Process online payments": "operational",
        "Track customer orders": "operational",
        "Manage stock": "operational",
        "Run customer support": "supporting",
    }


def _set_type(client, login_as, user, business_capability_id, value):
    login_as(client, user)
    return client.post(
        f"/enterprise/capabilities/{business_capability_id}/type",
        data={"capability_type": value}, follow_redirects=False,
    )


def test_an_architect_and_a_cto_set_the_type_and_it_persists(client, login_as, make_org, tenant_ctx):
    org = make_org()
    bc_id = _capability(org, tenant_ctx, "Sell online", level=1)
    for enterprise_role, role_name, chosen in (
        ("enterprise_architect", "Architect", "supporting"),
        ("cto", "Architect", "operational"),
    ):
        user = _user(org, role_name, enterprise_role)
        assert user.enterprise_role == enterprise_role
        response = _set_type(client, login_as, user, bc_id, chosen)
        assert response.status_code == 302
        db.session.expire_all()
        assert _unified(org, bc_id).capability_type == chosen


def test_someone_who_may_not_edit_capabilities_cannot_set_the_type(client, login_as, make_org, tenant_ctx):
    org = make_org()
    bc_id = _capability(org, tenant_ctx, "Sell online", level=1)
    viewer = _user(org, "Viewer", "procurement")
    assert _set_type(client, login_as, viewer, bc_id, "supporting").status_code == 403
    db.session.expire_all()
    assert _unified(org, bc_id).capability_type == "strategic"


def test_another_organisation_cannot_set_the_type_and_a_bad_value_changes_nothing(
    client, login_as, make_org, tenant_ctx,
):
    mine, theirs = make_org(), make_org()
    bc_id = _capability(mine, tenant_ctx, "Sell online", level=1)
    outsider = _user(theirs, "Architect", "enterprise_architect")
    assert _set_type(client, login_as, outsider, bc_id, "supporting").status_code == 404
    insider = _user(mine, "Architect", "enterprise_architect")
    assert _set_type(client, login_as, insider, bc_id, "banana").status_code == 302
    db.session.expire_all()
    assert _unified(mine, bc_id).capability_type == "strategic"


def test_the_map_groups_by_type_and_a_capability_moves_when_its_type_changes(
    client, login_as, make_org, tenant_ctx,
):
    org = make_org()
    bc_id = _capability(org, tenant_ctx, "Sell online", level=1)
    _capability(org, tenant_ctx, "Pick items", level=3)
    architect = _user(org, "Architect", "enterprise_architect")

    def group_of(name):
        login_as(client, architect)
        html = client.get("/capability-map/simple").get_data(as_text=True)
        for key in ("strategic", "operational", "supporting"):
            start = html.index(f'data-testid="type-group-{key}"')
            nxt = html.find('data-testid="type-group-', start + 10)
            if name in html[start: nxt if nxt != -1 else len(html)]:
                return key
        return None

    assert group_of("Sell online") == "strategic"
    assert group_of("Pick items") == "supporting"
    _set_type(client, login_as, architect, bc_id, "operational")
    assert group_of("Sell online") == "operational"


def test_the_investment_rows_carry_the_type(make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    org = make_org()
    _capability(org, tenant_ctx, "Sell online", level=1)
    with tenant_ctx(org.id):
        analysis = InvestmentPrioritizationService().analyze_investment_priorities()
    assert [c["capability_type"] for c in analysis["capability_scores"]] == ["strategic"]
