"""Generate one tenant-fence batch: migration file + model codemod + list updates.

usage: python scripts/tenant_fence_gen.py spec.json   (run from the worktree root)

spec.json:
{
 "revision": "20261027_fence_solution_family", "down": "20261027_stakeholder_comm_org",
 "title": "Solution family", "date": "2026-10-27",
 "tables": [ {"table": "x", "model": "X", "file": "app/models/x.py", "sources": [["solutions","solution_id"]]} ],
 "verify": ["agent_charters", ...],          # already carry the mixin: ensure NOT NULL + four policies
 "hybrid": ["model_providers"],              # hybrid policies, nullable forever
 "shared": {"apqc_process": "reason", ...}   # move to shared_reference_tables.txt
}
"""
import json
import re
import sys
from pathlib import Path

spec = json.load(open(sys.argv[1]))
ROOT = Path('.')  # run from the repository root (the test runs it from a scratch tree)

# ---- 1. models: add TenantMixin -----------------------------------------------------------
IMPORT = "from app.models.mixins import TenantMixin\n"
for t in spec["tables"]:
    if not t.get("model"):
        continue
    p = ROOT / t["file"]
    s = p.read_text(encoding="utf-8")
    m = re.search(r"^class %s\(([^)]*)\):" % re.escape(t["model"]), s, re.M)
    if not m:
        print("MODEL NOT FOUND", t)
        continue
    bases = [b.strip() for b in m.group(1).split(",")]
    if any(b in ("TenantMixin",) for b in bases):
        continue
    new = "class %s(%s):" % (t["model"], ", ".join(["TenantMixin"] + bases))
    s = s[:m.start()] + new + s[m.end():]
    if not re.search(r"^from (app\.models\.mixins|\.mixins|app\.models\.mixins\.core) import .*TenantMixin", s, re.M) \
            and "import TenantMixin" not in s:
        # insert after the last top-level import line
        lines = s.split("\n")
        last = 0
        depth_open = False
        for i, line in enumerate(lines):
            if re.match(r"^(from|import) ", line):
                last = i
                # multi-line import: advance to closing paren
                if "(" in line and ")" not in line:
                    j = i
                    while ")" not in lines[j]:
                        j += 1
                    last = j
            elif re.match(r"^(class|def|@)", line):
                break
        lines.insert(last + 1, IMPORT.rstrip("\n"))
        s = "\n".join(lines)
    p.write_text(s, encoding="utf-8")

# ---- 1b. models: name the parents the organisation is derived from ----------------------------
for t in spec["tables"]:
    if not t.get("model") or not t["sources"]:
        continue
    p = ROOT / t["file"]
    s = p.read_text(encoding="utf-8")
    m = re.search(r"^class %s\(" % re.escape(t["model"]), s, re.M)
    if not m:
        continue
    nxt = re.search(r"^class |^def ", s[m.end():], re.M)
    end = m.end() + (nxt.start() if nxt else len(s) - m.end())
    body = s[m.start():end]
    if "__tenant_parent__" in body:
        continue
    tn = re.search(r"^([ \t]+)__tablename__[ \t]*=[ \t]*[^\n]+\n", body, re.M)
    if not tn:
        print("NO __tablename__ in", t["model"], "- parent not declared")
        continue
    pairs = ", ".join("(%r, %r)" % (col, parent) for parent, col in t["sources"])
    line = "%s__tenant_parent__ = (%s,)\n" % (tn.group(1), pairs)
    body = body[:tn.end()] + line + body[tn.end():]
    s = s[:m.start()] + body + s[end:]
    p.write_text(s, encoding="utf-8")

# ---- 2. migration -------------------------------------------------------------------------
def tup(items):
    return "(\n" + "".join("    %r,\n" % (i,) for i in items) + ")"

tables_spec = [(t["table"], tuple(tuple(x) for x in t["sources"])) for t in spec["tables"]]
body = '''"""Tenant-fence batch: __TITLE__ (T-sec03 slice A).

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

Idempotent: a second run changes nothing. A database built fresh from the models already has the
columns, so the revision then only fences.

Revision ID: __REV__
Revises: __DOWN__
Create Date: __DATE__
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "__REV__"
down_revision = "__DOWN__"
branch_labels = None
depends_on = None

# (table, ((parent_table, fk_column), ...)): parents before children, sources tried in order.
BACKFILL = __BACKFILL__

VERIFY = __VERIFY__

HYBRID = __HYBRID__

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


def upgrade():
    bind = op.get_bind()
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
'''
body = (body.replace("__TITLE__", spec["title"]).replace("__REV__", spec["revision"])
        .replace("__DOWN__", spec["down"]).replace("__DATE__", spec["date"])
        .replace("__BACKFILL__", tup(tables_spec)).replace("__VERIFY__", tup(spec.get("verify", [])) if spec.get("verify") else "()")
        .replace("__HYBRID__", tup(spec.get("hybrid", [])) if spec.get("hybrid") else "()"))
Path("migrations/versions/%s.py" % spec["revision"]).write_text(body, encoding="utf-8")

# ---- 3. lists ------------------------------------------------------------------------------
remove = {t["table"] for t in spec["tables"]} | set(spec.get("verify", [])) | set(spec.get("hybrid", [])) | set(spec.get("shared", {}))
remove |= set(spec.get("also_remove", []))
for name in ("scripts/unfenced_tables.txt", "scripts/unfenced_tables_baseline.txt"):
    p = ROOT / name
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.split("#", 1)[0].strip() in remove:
            continue
        out.append(line)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
if spec.get("shared"):
    p = ROOT / "scripts/shared_reference_tables.txt"
    s = p.read_text(encoding="utf-8")
    if not s.endswith("\n"):
        s += "\n"
    for t, why in spec["shared"].items():
        if re.search(r"^%s\s" % t, s, re.M):
            continue
        s += "%s  # %s\n" % (t, why)
    p.write_text(s, encoding="utf-8")
print("done", len(spec["tables"]), "tables")
