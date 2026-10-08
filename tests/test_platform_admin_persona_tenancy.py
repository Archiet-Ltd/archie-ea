"""`platform_admin` persona live-data context must never cross tenants.

`User.enterprise_role` defaults to `platform_admin`, so an ordinary member of
ANY organisation who hasn't been assigned a different role sees whatever
`_platform_admin_context()` (architect_persona_charters.py) puts in the AI
chat prompt. Two leaks existed there:

1. `last_import()` read `ImportHistory.query.order_by(...).first()` with NO
   organisation filter at all and labelled the result "platform-wide" —
   showing any organisation's most recent import filename/status to any
   other organisation's platform_admin-defaulted member.
2. The three user-count helpers (`user_counts`, `pending_invites`,
   `role_mix`) routed through `_org_scope()`, which silently returned the
   UNSCOPED query whenever `g.current_org_id` was `None` instead of failing
   safe — counting every organisation's users platform-wide.

The fix scopes `last_import()` through `ImportHistory.user_id -> User.organization_id`
(a row with no attributable user can't match any organisation's filter and
never surfaces), and makes all four sections report "unavailable" rather than
an unscoped figure when there is no organisation context at all.
"""

from datetime import datetime, timedelta

from app.modules.ai_chat.services.architect_persona_charters import (
    _platform_admin_context,
)


def _make_user(db_session, org, confirmed=True):
    from app.models.user import User, Role
    import uuid

    # The seeded "Architect" role (Role.insert_roles(), tests/conftest.py's
    # session-scoped _schema fixture) already carries GENERAL permission and
    # is the platform's default role -- reuse it rather than seeding another.
    role = Role.query.filter_by(name="Architect").first()
    assert role is not None, "Role.insert_roles() must have seeded 'Architect'"

    user = User(
        email=f"pa-{org.id}-{uuid.uuid4().hex[:10]}@example.com",
        organization_id=org.id,
        confirmed=confirmed,
    )
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _make_import(db_session, user, filename, status="completed", when=None, imported=0, failed=0):
    from app.models.import_history import ImportHistory

    row = ImportHistory(
        filename=filename,
        import_type="csv",
        status=status,
        user_id=user.id if user is not None else None,
        created_at=when or datetime.utcnow(),
        records_imported=imported,
        records_failed=failed,
    )
    db_session.add(row)
    db_session.flush()
    return row


# --------------------------------------------------------------------------- #
# Leak 1 — last_import() must never cross organisations                      #
# --------------------------------------------------------------------------- #
def test_last_import_never_leaks_across_organisations(db_session, make_org, tenant_ctx):
    org_a = make_org("pa-import-a")
    org_b = make_org("pa-import-b")
    user_a = _make_user(db_session, org_a)
    user_b = _make_user(db_session, org_b)

    now = datetime.utcnow()
    # Org B's import is the globally-latest row, so an unscoped "ORDER BY
    # created_at DESC LIMIT 1" would surface it to org A too.
    _make_import(db_session, user_a, "org-a-roster.csv", status="completed", when=now - timedelta(hours=1))
    _make_import(db_session, user_b, "org-b-secret-vendors.xlsx", status="failed", when=now)

    with tenant_ctx(org_a.id):
        block_a = _platform_admin_context()
    with tenant_ctx(org_b.id):
        block_b = _platform_admin_context()

    assert "org-a-roster.csv" in block_a
    assert "org-b-secret-vendors.xlsx" not in block_a

    assert "org-b-secret-vendors.xlsx" in block_b
    assert "org-a-roster.csv" not in block_b

    # The old "(platform-wide)" wording must be gone entirely.
    assert "platform-wide" not in block_a
    assert "platform-wide" not in block_b


def test_last_import_with_no_attributable_user_never_surfaces(db_session, make_org, tenant_ctx):
    """A row with user_id IS NULL can't match any organisation's filter and
    must stay quarantined — never shown to any organisation's chat context."""
    org = make_org("pa-import-orphan")
    _make_user(db_session, org)  # unrelated user, no import of their own

    _make_import(db_session, None, "unattributed-bulk-load.csv", status="completed")

    with tenant_ctx(org.id):
        block = _platform_admin_context()

    assert "unattributed-bulk-load.csv" not in block
    assert "Last data import: none recorded" in block


def test_last_import_positive_case_reports_the_organisations_own_import(db_session, make_org, tenant_ctx):
    """The legitimate case: a real org context with a real import belonging to
    that organisation's user must still be reported correctly — the fix must
    not regress the success path while closing the leak."""
    org = make_org("pa-import-ok")
    user = _make_user(db_session, org)
    _make_import(
        db_session, user, "quarterly-vendor-import.csv",
        status="completed", imported=42, failed=3,
    )

    with tenant_ctx(org.id):
        block = _platform_admin_context()

    assert "Last data import: quarterly-vendor-import.csv" in block
    assert "completed" in block
    assert "42 imported" in block
    assert "3 failed" in block
    assert "platform-wide" not in block


# --------------------------------------------------------------------------- #
# Leak 2 — user_counts / pending_invites / role_mix must never cross orgs    #
# --------------------------------------------------------------------------- #
def test_user_counts_never_leak_across_organisations(db_session, make_org, tenant_ctx):
    org_a = make_org("pa-users-a")
    org_b = make_org("pa-users-b")

    from app.services.billing_plans import set_contract_plan
    set_contract_plan(org_b, "enterprise", None)  # five people: more than Community admits

    _make_user(db_session, org_a, confirmed=True)
    _make_user(db_session, org_a, confirmed=True)
    for _ in range(5):
        _make_user(db_session, org_b, confirmed=False)

    with tenant_ctx(org_a.id):
        block_a = _platform_admin_context()
    with tenant_ctx(org_b.id):
        block_b = _platform_admin_context()

    assert "Users provisioned: 2" in block_a
    assert "Users provisioned: 7" not in block_a  # the platform-wide total

    assert "Users provisioned: 5" in block_b
    assert "Users provisioned: 7" not in block_b


# --------------------------------------------------------------------------- #
# No org context at all -- every section must read "unavailable"            #
# --------------------------------------------------------------------------- #
def test_no_org_context_every_section_reports_unavailable(app, db_session, make_org):
    """With g.current_org_id absent entirely, none of the four sections may
    fall back to a platform-wide figure -- even though real data exists."""
    org = make_org("pa-no-ctx")
    user = _make_user(db_session, org)
    _make_import(db_session, user, "should-never-surface.csv")

    with app.test_request_context("/"):
        # Deliberately do not set g.current_org_id -- it is absent, not None.
        block = _platform_admin_context()

    assert "- user_counts: unavailable" in block
    assert "- pending_invites: unavailable" in block
    assert "- role_mix: unavailable" in block
    assert "- last_import: unavailable" in block
    assert "should-never-surface.csv" not in block
    assert "platform-wide" not in block


def test_no_org_context_explicit_none_also_reports_unavailable(app, db_session, make_org):
    """Belt-and-braces: g.current_org_id explicitly set to None behaves the
    same as it being absent -- both must degrade to 'unavailable'."""
    from flask import g

    org = make_org("pa-none-ctx")
    user = _make_user(db_session, org)
    _make_import(db_session, user, "also-should-never-surface.csv")

    with app.test_request_context("/"):
        g.current_org_id = None
        block = _platform_admin_context()

    assert "- user_counts: unavailable" in block
    assert "- pending_invites: unavailable" in block
    assert "- role_mix: unavailable" in block
    assert "- last_import: unavailable" in block
    assert "also-should-never-surface.csv" not in block
