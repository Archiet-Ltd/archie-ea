"""Give archimate_element_comments an organisation and fence it at row level.

Every comment row takes the organisation of the ArchiMate element it comments
on. New comments are stamped by the comment route from the element's
organisation, never from the request. Row-level security then enforces at the
database level that a comment of one organisation cannot be read or written
from another organisation's session.

The column is added nullable, backfilled from the element's organisation,
tightened to NOT NULL, indexed and foreign-keyed to organizations. Comments
whose element is missing or has no organisation are not guessed: the migration
fails loudly and lists their ids.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261018_element_comment_org_id
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261018_element_comment_org_id"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None

TABLE = "archimate_element_comments"
ELEMENT_TABLE = "archimate_elements"
FK_NAME = "fk_archimate_element_comments_organization_id"
INDEX_NAME = "ix_archimate_element_comments_organization_id"

TENANT_TABLES = (TABLE,)
TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")
_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"
_TENANT_DEFINITIONS = (
    ("SELECT", _TENANT_RULE, None),
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)


def _present(bind):
    return bool(bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{TABLE}"}).scalar())


def _has_column(bind):
    return bool(bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = :t AND column_name = 'organization_id')"
        ),
        {"t": TABLE},
    ).scalar())


def _may_alter(bind):
    return bool(bind.execute(
        text(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_class c WHERE c.oid = to_regclass(:t)"
        ),
        {"t": f"public.{TABLE}"},
    ).scalar())


def _fk_exists(bind):
    return bool(bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": FK_NAME}).scalar())


def _fence(bind):
    # Table name and policy text are module constants; nothing here is user input.
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{TABLE}"))  # nosec B608 - literal names
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{TABLE} FOR {command}{clause}"))  # nosec B608 - literal names
    bind.execute(text(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal name


def upgrade():
    bind = op.get_bind()
    if not _present(bind):
        print(f"element comment tenancy: skipped {TABLE} (table absent)")
        return
    if not _may_alter(bind):
        raise RuntimeError(f"{TABLE}: migration role does not own the table")
    if not _has_column(bind):
        op.add_column(TABLE, sa.Column("organization_id", sa.Integer(), nullable=True))
    # Backfill from the element's organisation; never guess a value.
    bind.execute(
        text(
            f"UPDATE {TABLE} AS c SET organization_id = e.organization_id "
            f"FROM {ELEMENT_TABLE} AS e WHERE c.element_id = e.id "
            f"AND c.organization_id IS NULL"
        )
    )
    orphans = bind.execute(
        text(
            f"SELECT c.id FROM {TABLE} AS c "
            f"WHERE c.organization_id IS NULL"
        )
    ).scalars().all()
    if orphans:
        raise RuntimeError(
            f"{TABLE}: {len(orphans)} comment row(s) have no derivable organisation "
            f"(element missing or element has no organisation): {sorted(orphans)}"
        )
    op.alter_column(TABLE, "organization_id", existing_type=sa.Integer(), nullable=False)
    op.create_index(INDEX_NAME, TABLE, ["organization_id"], unique=False, if_not_exists=True)
    if not _fk_exists(bind):
        op.create_foreign_key(
            FK_NAME, TABLE, "organizations", ["organization_id"], ["id"], ondelete="CASCADE"
        )
    _fence(bind)
    print(f"element comment tenancy: fenced {TABLE}")


def downgrade():
    bind = op.get_bind()
    if not _present(bind) or not _may_alter(bind):
        return
    for name in TENANT_POLICIES:
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{TABLE}"))  # nosec B608 - literal names
    bind.execute(text(f"ALTER TABLE public.{TABLE} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal name
    if _fk_exists(bind):
        op.drop_constraint(FK_NAME, TABLE, type_="foreignkey")
    op.drop_index(INDEX_NAME, table_name=TABLE, if_exists=True)
    if _has_column(bind):
        op.drop_column(TABLE, "organization_id")
