"""Tenant-fence batch: Vendor tables (T-sec03 slice A).

Each table below belongs to one organisation but had no organization_id (or had one with no
fence), so it relied on every route remembering an owner check. This revision, per table:
adds organization_id when absent, backfills it from the parent row named in BACKFILL (tried in
order), tightens it to NOT NULL when every row was reached, indexes it, adds the foreign key and
switches on the four standard row-level-security policies (identical text to
20261008_row_level_security).

A row whose parent is gone or was never set has no derivable organisation. It is left NULL (and
the column stays nullable on that database) rather than guessed; the policies and the ORM filter
both exclude NULL rows from every tenant. Each table with leftover NULL rows is printed as
``NULL-ROWS <table>: <n>`` so the count is on record.

VERIFY lists tables that already inherit the mixin: the revision only makes sure the column is
NOT NULL (when no NULL rows) and the four policies exist. HYBRID lists shared-plus-override
tables (nullable forever) that get the hybrid policies.

vendor_products and the tables that hang from it are hybrid (shared-plus-override, nullable forever):
a row with no organisation is a shared catalogue row readable by every organisation, a row with one is
that company's. The organisation of a product is derived, stopping at the first hit: (a) the ArchiMate
element it points at; (b) the organisation of the applications and solutions that reference it (two
organisations that disagree leave it NULL and print ``AMBIGUOUS vendor_products id=<n>``); (c) the
vendor organisation's named_by_organization_id, for a vendor made at runtime (seed_source_id 'auto-...')
only; (d) otherwise NULL, shared. The run prints ``NULL-ROWS vendor_products: <n>`` (every product left
without an organisation) and ``SHARED-ROWS vendor_products: <n>`` (those that nothing referenced or
disagreed about, i.e. the shared catalogue). vendor_product_apqc_mapping and vendor_product_embeddings
take their product's organisation; vendor_product_pricing keeps its own nullable organisation (NULL is
the list price) and gains the foreign key.

Idempotent: a second run changes nothing. A database built fresh from the models already has the
columns, so the revision then only fences.

Revision ID: 20261027_fence_vendor
Revises: 20261027_fence_codegen
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261027_fence_vendor"
down_revision = "20261027_fence_codegen"
branch_labels = None
depends_on = None

# (table, ((parent_table, fk_column), ...)): parents before children, sources tried in order.
BACKFILL = (
    ('application_vendor_product_mappings', (('application_components', 'application_component_id'),)),
    ('unified_capability_vendor_organization_mappings', (('unified_capabilities', 'unified_capability_id'),)),
    ('vendor_archimate_templates', (('archimate_elements', 'element_id'),)),
    ('tco_calculations', (('users', 'created_by_id'), ('vendor_products', 'vendor_product_id'))),
    ('vendor_risk_assessments', (('users', 'created_by_id'), ('vendor_products', 'vendor_product_id'))),
    ('vendor_comparison_criteria', (('users', 'created_by_id'),)),
    ('vendor_proof_points', (('vendor_options', 'vendor_option_id'),)),
    ('vendor_responses', (('vendor_options', 'vendor_option_id'),)),
    ('vendor_selection_reports', (('ea_workflow_instances', 'workflow_instance_id'),)),
    ('rfp_templates', (('business_capability', 'capability_id'),)),
    ('solution_vendor_products', (('solutions', 'solution_id'),)),
    ('application_component_vendor_products', (('application_components', 'application_component_id'),)),
)

VERIFY = ()

HYBRID = (
    "vendor_products",
    "vendor_product_apqc_mapping",
    "vendor_product_embeddings",
    "vendor_product_pricing",
)

# Read by tests/test_row_level_security.py's guard that every tenant model is fenced.
TENANT_TABLES = tuple(t for t, _ in BACKFILL) + VERIFY
HYBRID_TABLES = HYBRID

_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"
_HYBRID_READ = f"organization_id IS NULL OR organization_id = {_ORG} OR {_PLATFORM}"
_HYBRID_WRITE = f"(organization_id IS NOT NULL AND organization_id = {_ORG}) OR {_PLATFORM}"

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")
HYBRID_POLICIES = ("hybrid_select", "hybrid_insert", "hybrid_update", "hybrid_delete")
_TENANT_DEFINITIONS = (
    ("SELECT", _TENANT_RULE, None),
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)
_HYBRID_DEFINITIONS = (
    ("SELECT", _HYBRID_READ, None),
    ("INSERT", None, _HYBRID_WRITE),
    ("UPDATE", _HYBRID_WRITE, _HYBRID_WRITE),
    ("DELETE", _HYBRID_WRITE, None),
)


def _short(prefix, table):
    name = f"{prefix}_{table}_organization_id"
    if len(name) <= 63:
        return name
    import hashlib

    return f"{prefix}_{hashlib.sha1(table.encode()).hexdigest()[:10]}_{table[:30]}_org"[:63]


def _exists(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _has_column(bind, table, column="organization_id"):
    return bool(
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _may_alter(bind, table):
    return bool(
        bind.execute(
            text(
                "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_class c WHERE c.oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).scalar()
    )


def _policy_count(bind, table, names):
    return bind.execute(
        text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = :t AND policyname = ANY(:n)"),
        {"t": table, "n": list(names)},
    ).scalar()


def _fence(bind, table, names, definitions):
    for name, (command, using, check) in zip(names, definitions):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table name
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}"))  # nosec B608
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608


def _column_and_backfill(bind, table, sources):
    if not _has_column(bind, table):
        op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))
    for parent, column in sources:
        if not (_exists(bind, parent) and _has_column(bind, parent) and _has_column(bind, table, column)):
            continue
        bind.execute(
            text(
                f"UPDATE public.{table} AS c SET organization_id = p.organization_id "  # nosec B608
                f"FROM public.{parent} AS p WHERE c.{column} = p.id "
                "AND c.organization_id IS NULL AND p.organization_id IS NOT NULL"
            )
        )


def _tighten_index_fk(bind, table):
    nulls = bind.execute(text(f"SELECT count(*) FROM public.{table} WHERE organization_id IS NULL")).scalar()  # nosec B608
    if nulls == 0:
        op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
    else:
        print(f"NULL-ROWS {table}: {nulls}")
    op.create_index(_short("ix", table), table, ["organization_id"], if_not_exists=True)
    covered = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint c JOIN pg_attribute a "
            "ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
            "WHERE c.contype = 'f' AND c.conrelid = to_regclass(:t) AND a.attname = 'organization_id'"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not covered:
        op.create_foreign_key(_short("fk", table), table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")


def _existing(bind, *tables):
    return [t for t in tables if _exists(bind, t)]


def _add_org_column(bind, table):
    if not _has_column(bind, table):
        op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_index(_short("ix", table), table, ["organization_id"], if_not_exists=True)
    covered = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint c JOIN pg_attribute a "
            "ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
            "WHERE c.contype = 'f' AND c.conrelid = to_regclass(:t) AND a.attname = 'organization_id'"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not covered:
        # NOT VALID: new rows are checked, existing override rows are not rewritten or dropped.
        bind.execute(text(
            f"ALTER TABLE public.{table} ADD CONSTRAINT {_short('fk', table)} "  # nosec B608 - literal names
            "FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE NOT VALID"))


def _vendor_product_references(bind):
    """SELECT text giving (vp, org) for every application or solution that references a product."""
    parts = []
    if _exists(bind, "application_vendor_product_mappings") and _has_column(bind, "application_vendor_product_mappings", "vendor_product_id") and _exists(bind, "application_components"):
        parts.append("SELECT m.vendor_product_id AS vp, a.organization_id AS org "
                     "FROM application_vendor_product_mappings m JOIN application_components a ON a.id = m.application_component_id")
    if _exists(bind, "application_component_vendor_products") and _has_column(bind, "application_component_vendor_products", "vendor_product_id") and _exists(bind, "application_components"):
        parts.append("SELECT m.vendor_product_id, a.organization_id "
                     "FROM application_component_vendor_products m JOIN application_components a ON a.id = m.application_component_id")
    if _exists(bind, "application_components") and _has_column(bind, "application_components", "vendor_product_id"):
        parts.append("SELECT a.vendor_product_id, a.organization_id FROM application_components a WHERE a.vendor_product_id IS NOT NULL")
    if _exists(bind, "solution_vendor_products") and _has_column(bind, "solution_vendor_products", "vendor_product_id") and _exists(bind, "solutions"):
        parts.append("SELECT m.vendor_product_id, s.organization_id "
                     "FROM solution_vendor_products m JOIN solutions s ON s.id = m.solution_id")
    return " UNION ".join(parts)


def _hybrid_vendor_products(bind):
    if not _exists(bind, "vendor_products"):
        return
    _add_org_column(bind, "vendor_products")
    # (a) the ArchiMate element the product points at
    if _exists(bind, "archimate_elements") and _has_column(bind, "vendor_products", "archimate_product_element_id"):
        bind.execute(text(
            "UPDATE public.vendor_products AS p SET organization_id = e.organization_id "
            "FROM public.archimate_elements AS e WHERE p.archimate_product_element_id = e.id "
            "AND p.organization_id IS NULL AND e.organization_id IS NOT NULL"))
    # (b) the applications and solutions that reference it; disagreement leaves it NULL, by id
    refs = _vendor_product_references(bind)
    ambiguous = []
    if refs:
        ambiguous = [r[0] for r in bind.execute(text(
            f"SELECT vp FROM ({refs}) r WHERE org IS NOT NULL GROUP BY vp HAVING count(DISTINCT org) > 1"))]  # nosec B608
        for vp in ambiguous:
            print(f"AMBIGUOUS vendor_products id={vp}")
        bind.execute(text(
            f"UPDATE public.vendor_products AS p SET organization_id = one.org FROM ("  # nosec B608 - built from literals above
            f"SELECT vp, min(org) AS org FROM ({refs}) r WHERE org IS NOT NULL GROUP BY vp HAVING count(DISTINCT org) = 1"
            ") one WHERE p.id = one.vp AND p.organization_id IS NULL"))
    # (c) the organisation that named a vendor made at runtime (never a seeded catalogue vendor)
    if (_exists(bind, "vendor_organizations") and _has_column(bind, "vendor_organizations", "named_by_organization_id")
            and _has_column(bind, "vendor_organizations", "seed_source_id")):
        bind.execute(text(
            "UPDATE public.vendor_products AS p SET organization_id = v.named_by_organization_id "
            "FROM public.vendor_organizations AS v WHERE p.vendor_organization_id = v.id "
            "AND p.organization_id IS NULL AND v.named_by_organization_id IS NOT NULL "
            "AND v.seed_source_id LIKE 'auto-%' AND NOT (p.id = ANY(:amb))"),
            {"amb": ambiguous or [0]})
    nulls = bind.execute(text("SELECT count(*) FROM public.vendor_products WHERE organization_id IS NULL")).scalar()
    shared = nulls - len(ambiguous)
    print(f"NULL-ROWS vendor_products: {nulls}")
    print(f"SHARED-ROWS vendor_products: {shared}")
    # children that follow their product
    for child in ("vendor_product_apqc_mapping", "vendor_product_embeddings"):
        if not _exists(bind, child):
            continue
        _add_org_column(bind, child)
        bind.execute(text(
            f"UPDATE public.{child} AS c SET organization_id = p.organization_id "  # nosec B608 - literal names
            "FROM public.vendor_products AS p WHERE c.vendor_product_id = p.id "
            "AND c.organization_id IS NULL AND p.organization_id IS NOT NULL"))
        left = bind.execute(text(f"SELECT count(*) FROM public.{child} WHERE organization_id IS NULL")).scalar()  # nosec B608
        print(f"SHARED-ROWS {child}: {left}")
    # pricing: its own nullable organisation (NULL is the list price); only the key is missing
    if _exists(bind, "vendor_product_pricing"):
        _add_org_column(bind, "vendor_product_pricing")


def upgrade():
    bind = op.get_bind()
    _hybrid_vendor_products(bind)
    for table, sources in BACKFILL:
        if not _exists(bind, table):
            continue  # create_all / the baseline brings it up with the column
        _column_and_backfill(bind, table, sources)
        _tighten_index_fk(bind, table)
        if _may_alter(bind, table):
            _fence(bind, table, TENANT_POLICIES, _TENANT_DEFINITIONS)
        else:
            print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
    for table in VERIFY:
        if not (_exists(bind, table) and _has_column(bind, table)):
            continue
        nulls = bind.execute(text(f"SELECT count(*) FROM public.{table} WHERE organization_id IS NULL")).scalar()  # nosec B608
        if nulls == 0:
            op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        else:
            print(f"NULL-ROWS {table}: {nulls}")
        if _policy_count(bind, table, TENANT_POLICIES) != 4 and _may_alter(bind, table):
            _fence(bind, table, TENANT_POLICIES, _TENANT_DEFINITIONS)
            print(f"row-level security: re-applied on {table}")
    for table in HYBRID:
        if not (_exists(bind, table) and _has_column(bind, table)):
            continue
        if _policy_count(bind, table, HYBRID_POLICIES) != 4 and _may_alter(bind, table):
            _fence(bind, table, HYBRID_POLICIES, _HYBRID_DEFINITIONS)


def downgrade():
    bind = op.get_bind()
    for table, _ in BACKFILL:
        if not _exists(bind, table):
            continue
        if _may_alter(bind, table):
            for name in TENANT_POLICIES:
                bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608
            bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608
        # The column is kept: dropping it would discard the only record of ownership.
        op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=True)
