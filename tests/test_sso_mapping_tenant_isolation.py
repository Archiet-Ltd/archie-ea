"""Cross-tenant isolation for sso_group_role_mappings.

Before TenantMixin, /admin/sso-settings had no tenant boundary at all: any
org's admin could see, edit and delete every other org's SSO group->role
mappings, and a global UNIQUE(sso_group_name) meant two orgs could never both
use a group named e.g. "Admins". Worse, app/auth/sso.py's login-time role
lookup (_load_db_group_role_map) mixed every org's mappings together with no
filter -- a real cross-tenant privilege-confusion risk: an IdP group name used
by one org's mapping could grant its role to a completely different org's
user during SSO login, purely by name collision.

See app/commands/reconcile_schema.py's `_backfill_sso_mapping_organizations`
and `_ensure_sso_mapping_tenant_unique_constraint` for the existing-database
migration this needed.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_mapping(db_session, org_id, group_name, role_name="solution_architect"):
    from app.models.miscellaneous import SSOGroupRoleMapping

    row = SSOGroupRoleMapping(
        sso_group_name=group_name, role_name=role_name, is_active=True,
        organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_sso_mapping_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """The admin UI (/admin/sso-settings) must not show org A org B's mappings."""
    from app.models.miscellaneous import SSOGroupRoleMapping

    org_a, org_b = make_org("a"), make_org("b")
    _make_mapping(db_session, org_a.id, "Architects")
    b_mapping = _make_mapping(db_session, org_b.id, "Architects")

    with tenant_ctx(org_a.id):
        visible_ids = {m.id for m in SSOGroupRoleMapping.query.all()}

    assert b_mapping.id not in visible_ids, (
        "TENANT LEAK: org A's admin can see org B's SSO group->role mapping."
    )


def test_two_orgs_can_use_the_same_sso_group_name(db_session, make_org):
    """The old global UNIQUE(sso_group_name) blocked this -- a real functional
    bug riding along with the leak: two tenants' IdPs commonly share common
    group names like "Admins" or "Architects"."""
    org_a, org_b = make_org("a"), make_org("b")

    _make_mapping(db_session, org_a.id, "Admins", role_name="platform_admin")
    # Must not raise IntegrityError -- this is the whole point of the
    # composite (organization_id, sso_group_name) constraint.
    _make_mapping(db_session, org_b.id, "Admins", role_name="portfolio_manager")


def test_login_time_role_lookup_does_not_cross_tenants(db_session, make_org):
    """The specific privilege-confusion bug: SSOService.map_groups_to_role must
    only ever match the logging-in user's own org's mappings, even though this
    runs with no g.current_org_id (see _load_db_group_role_map's docstring)."""
    from app.auth.sso import SSOService

    org_a, org_b = make_org("a"), make_org("b")
    # Org B's admin mapped "Everyone" -> platform_admin (maybe a misconfigured
    # test IdP group). Org A's IdP happens to also send a group called
    # "Everyone" for its own, unrelated reasons.
    _make_mapping(db_session, org_b.id, "Everyone", role_name="platform_admin")
    _make_mapping(db_session, org_a.id, "Everyone", role_name="solution_architect")

    service = SSOService()
    # No tenant_ctx active -- mirrors the real pre-authentication SSO callback.
    role = service.map_groups_to_role(["Everyone"], org_a.id)

    assert role == "solution_architect", (
        f"PRIVILEGE ESCALATION: org A's user matched org B's mapping and got "
        f"role={role!r} instead of org A's own solution_architect mapping."
    )


def test_login_time_role_lookup_ignores_other_orgs_groups_entirely(db_session, make_org):
    """A group name that exists ONLY in another org's mappings must not match
    at all -- not fall back to some other org's role, just no match."""
    from app.auth.sso import SSOService

    org_a, org_b = make_org("a"), make_org("b")
    _make_mapping(db_session, org_b.id, "OnlyInOrgB", role_name="platform_admin")

    service = SSOService()
    role = service.map_groups_to_role(["OnlyInOrgB"], org_a.id)

    assert role is None, (
        f"TENANT LEAK: org A's user matched org B's group-only mapping and "
        f"got role={role!r}."
    )
