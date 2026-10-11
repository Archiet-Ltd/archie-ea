"""Cross-organisation read leaks: unmapped capabilities and roadmap initiatives.

Two critical leaks where a signed-in user of organisation A could see
organisation B's data:

1. GET /capability-analysis/unmapped and GET /api/capability-analysis/unmapped/export
   returned every organisation's UnifiedCapability rows with no org predicate.

2. GET /solutions/api/roadmap/initiatives returned every organisation's
   TechnologyRoadmapInitiative rows with no org predicate.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org):
    from app.models.user import Role, User

    from tests.smoke.conftest import PASSWORD

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"leak-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Leak",
        last_name="Test",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = PASSWORD
    db_session.add(user)
    db_session.flush()
    return user


# ── Leak 1: unmapped capabilities ────────────────────────────────────────────


def test_unmapped_page_shows_only_own_and_reference_capabilities(
    db_session, make_org, client, login_as,
):
    """Org A must not see Org B's tenant capabilities on the unmapped page."""
    from app.models.unified_capability import UnifiedCapability

    org_a = make_org("um-a")
    org_b = make_org("um-b")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]
    ref = UnifiedCapability(
        name=f"Reference-{suffix}",
        code=f"REF-{suffix}",
        scope="reference",
        organization_id=None,
        level=1,
    )
    own = UnifiedCapability(
        name=f"OrgA-Cap-{suffix}",
        code=f"A-{suffix}",
        scope="tenant",
        organization_id=org_a.id,
        level=1,
    )
    foreign = UnifiedCapability(
        name=f"OrgB-Cap-{suffix}",
        code=f"B-{suffix}",
        scope="tenant",
        organization_id=org_b.id,
        level=1,
    )
    db_session.add_all([ref, own, foreign])
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/capability-analysis/unmapped")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Org A sees its own capability and the reference capability
    assert own.name in html, "Org A must see its own tenant capability"
    assert ref.name in html, "Org A must see the shared reference capability"
    # Org A must NOT see Org B's tenant capability
    assert foreign.name not in html, (
        "Org A must NOT see Org B's tenant capability on the unmapped page"
    )


def test_unmapped_export_shows_only_own_and_reference_capabilities(
    db_session, make_org, client, login_as,
):
    """Org A must not see Org B's tenant capabilities in the export."""
    from app.models.unified_capability import UnifiedCapability

    org_a = make_org("ue-a")
    org_b = make_org("ue-b")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]
    ref = UnifiedCapability(
        name=f"Ref-Export-{suffix}",
        code=f"REF-EXP-{suffix}",
        scope="reference",
        organization_id=None,
        level=1,
    )
    own = UnifiedCapability(
        name=f"OrgA-Export-{suffix}",
        code=f"A-EXP-{suffix}",
        scope="tenant",
        organization_id=org_a.id,
        level=1,
    )
    foreign = UnifiedCapability(
        name=f"OrgB-Export-{suffix}",
        code=f"B-EXP-{suffix}",
        scope="tenant",
        organization_id=org_b.id,
        level=1,
    )
    db_session.add_all([ref, own, foreign])
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/api/capability-analysis/unmapped/export")
    assert resp.status_code == 200
    data = resp.get_json()
    names = {c["name"] for c in data["capabilities"]}

    assert own.name in names, "Org A must see its own tenant capability in export"
    assert ref.name in names, "Org A must see the shared reference capability in export"
    assert foreign.name not in names, (
        "Org A must NOT see Org B's tenant capability in export"
    )


# ── Leak 2: roadmap initiatives ──────────────────────────────────────────────


def test_initiative_list_shows_only_own_org_initiatives(
    db_session, make_org, client, login_as,
):
    """Org A must not see initiatives linked to Org B's solutions or architecture models."""
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.solution_models import Solution

    org_a = make_org("ini-a")
    org_b = make_org("ini-b")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]

    sol_a = Solution(
        name=f"Sol-A-{suffix}",
        organization_id=org_a.id,
        solution_type="application",
    )
    sol_b = Solution(
        name=f"Sol-B-{suffix}",
        organization_id=org_b.id,
        solution_type="application",
    )
    db_session.add_all([sol_a, sol_b])
    db_session.flush()

    own_init = TechnologyRoadmapInitiative(
        name=f"OrgA-Initiative-{suffix}",
        fiscal_year_start=2026,
        fiscal_year_end=2027,
        solution_id=sol_a.id,
    )
    foreign_init = TechnologyRoadmapInitiative(
        name=f"OrgB-Initiative-{suffix}",
        fiscal_year_start=2026,
        fiscal_year_end=2027,
        solution_id=sol_b.id,
    )
    db_session.add_all([own_init, foreign_init])
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/solutions/api/roadmap/initiatives")
    assert resp.status_code == 200
    data = resp.get_json()
    names = {i["name"] for i in data["data"]}

    assert own_init.name in names, "Org A must see its own initiative"
    assert foreign_init.name not in names, (
        "Org A must NOT see Org B's initiative"
    )


def test_initiative_list_shows_initiative_linked_to_own_architecture_model(
    db_session, make_org, client, login_as,
):
    """An initiative linked through architecture_id (not solution_id) must also be visible."""
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    org_a = make_org("arch-a")
    org_b = make_org("arch-b")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]

    arch_a = ArchitectureModel(
        name=f"Arch-A-{suffix}",
        organization_id=org_a.id,
    )
    arch_b = ArchitectureModel(
        name=f"Arch-B-{suffix}",
        organization_id=org_b.id,
    )
    db_session.add_all([arch_a, arch_b])
    db_session.flush()

    own_init = TechnologyRoadmapInitiative(
        name=f"Arch-Initiative-A-{suffix}",
        fiscal_year_start=2026,
        fiscal_year_end=2027,
        architecture_id=arch_a.id,
    )
    foreign_init = TechnologyRoadmapInitiative(
        name=f"Arch-Initiative-B-{suffix}",
        fiscal_year_start=2026,
        fiscal_year_end=2027,
        architecture_id=arch_b.id,
    )
    db_session.add_all([own_init, foreign_init])
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/solutions/api/roadmap/initiatives")
    assert resp.status_code == 200
    data = resp.get_json()
    names = {i["name"] for i in data["data"]}

    assert own_init.name in names, (
        "Org A must see initiative linked to its own architecture model"
    )
    assert foreign_init.name not in names, (
        "Org A must NOT see initiative linked to Org B's architecture model"
    )


def test_initiative_list_returns_empty_when_user_has_no_organisation(
    db_session, make_org, client, login_as,
):
    """A signed-in user with no organisation must see an empty initiative list."""
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.solution_models import Solution
    from app.models.user import Role, User

    from tests.smoke.conftest import PASSWORD

    org_a = make_org("noorg-a")
    org_b = make_org("noorg-b")

    suffix = uuid.uuid4().hex[:10]

    sol_a = Solution(
        name=f"Sol-A-{suffix}",
        organization_id=org_a.id,
        solution_type="application",
    )
    sol_b = Solution(
        name=f"Sol-B-{suffix}",
        organization_id=org_b.id,
        solution_type="application",
    )
    db_session.add_all([sol_a, sol_b])
    db_session.flush()

    init_a = TechnologyRoadmapInitiative(
        name=f"Init-A-{suffix}",
        fiscal_year_start=2026,
        fiscal_year_end=2027,
        solution_id=sol_a.id,
    )
    init_b = TechnologyRoadmapInitiative(
        name=f"Init-B-{suffix}",
        fiscal_year_start=2026,
        fiscal_year_end=2027,
        solution_id=sol_b.id,
    )
    db_session.add_all([init_a, init_b])
    db_session.flush()

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    no_org_user = User(
        email=f"leak-noorg-{uuid.uuid4().hex[:8]}@example.com",
        first_name="NoOrg",
        last_name="Test",
        organization_id=None,
        role=role,
        confirmed=True,
    )
    no_org_user.password = PASSWORD
    db_session.add(no_org_user)
    db_session.flush()

    login_as(client, no_org_user)
    resp = client.get("/solutions/api/roadmap/initiatives")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["data"] == [], (
        "User with no organisation must see an empty initiative list"
    )
    assert data["count"] == 0, (
        "Count must be zero when user has no organisation"
    )