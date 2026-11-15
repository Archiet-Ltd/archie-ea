"""``flask reset-demo-company``: retire the demonstration company, then seed a fresh one.

The demonstration company (Lantern Quay Systems) is replaced, not repaired, so every environment shows
the same history and the same figures. A demonstration seeded before the dated-history work has too few
dated create entries, and a second plain seed is refused by the organisation's slug and by the globally
unique application codes.

Nothing sealed is ever deleted or switched off. The old demonstration is retired with ordinary updates, in
one transaction: its slug becomes ``lantern-quay-retired-<UTC timestamp>``, it is marked inactive, and every
globally unique text value it holds (application codes, external ids, capability codes, the demonstration
login's email and the like) gets a short suffix, so a fresh seed does not collide with it. Its rows, its
change history and its audit chain stay exactly as they were. The fresh organisation is then seeded, and
every system is linked to its vendor.

The command acts on the demonstration slug only and refuses any other before it reads or changes a row. If
a table that holds some of the demonstration's rows is guarded against UPDATE, the transaction is rolled back
and the command stops, naming the table.
"""
from __future__ import annotations

import datetime as dt
import os

import click
from flask.cli import with_appcontext

from app import db

DEMO_SLUG = "lantern-quay"


class NotTheDemoCompany(ValueError):
    """The command was pointed at an organisation other than the demonstration company."""


# Single-column unique indexes on text columns of tables that carry an organization_id: the values that
# stop a second organisation holding the same code.
_UNIQUE_TEXT_COLUMNS = """
SELECT c.relname AS table_name, a.attname AS column_name,
       CASE WHEN a.atttypmod > 4 THEN a.atttypmod - 4 END AS max_length
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = i.indkey[0] AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND i.indisunique AND NOT i.indisprimary AND i.indnkeyatts = 1 AND i.indexprs IS NULL
  AND a.atttypid IN ('varchar'::regtype, 'text'::regtype, 'bpchar'::regtype)
  AND a.attname <> 'organization_id'
  AND EXISTS (SELECT 1 FROM pg_attribute o
              WHERE o.attrelid = c.oid AND o.attname = 'organization_id' AND NOT o.attisdropped)
  AND c.relname <> 'organizations'
GROUP BY c.relname, a.attname, a.atttypmod
ORDER BY c.relname, a.attname
"""


# Tables a trigger guards against UPDATE: sealed history, which is never touched. The values in them
# that are unique (generated event ids, receipts) are never reused by a fresh seed.
_UPDATE_GUARDED = """
SELECT DISTINCT c.relname
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal AND (t.tgtype & 16) > 0 AND n.nspname = 'public'
"""
# What the retirement cannot do without: an organisation, its systems and its login.
_REQUIRED = ("application_components", "users")


def _quote(name: str) -> str:
    return '"%s"' % name.replace('"', '""')


def _retired_slug(org_id: int, now: dt.datetime) -> str:
    slug = "%s-retired-%s" % (DEMO_SLUG, now.strftime("%Y%m%dT%H%M%SZ"))
    taken = db.session.execute(db.text("SELECT 1 FROM organizations WHERE slug = :s"), {"s": slug}).scalar()
    return "%s-%d" % (slug, org_id) if taken else slug


def retire_organisation(org_id: int, now: dt.datetime | None = None) -> dict:
    """Retire one organisation with ordinary updates, in one transaction. Returns what was changed.

    ``{"slug": <new slug>, "values": {"table.column": rows}, "sealed": [tables left as they were]}``.
    Nothing is committed unless every statement succeeds.
    """
    now = now or dt.datetime.utcnow()
    changed: dict = {}
    sealed: list = []
    try:
        connection = db.session.connection()
        connection.execute(db.text("SELECT set_config('archie.organization_id', :org, true)"),
                           {"org": str(org_id)})
        slug = _retired_slug(org_id, now)
        suffix = "~r%d" % org_id  # unique by construction: the retired organisation's own id
        guarded = {row[0] for row in connection.execute(db.text(_UPDATE_GUARDED))}
        for table in _REQUIRED + ("organizations",):
            if table in guarded:
                raise click.ClickException(
                    "%s is guarded against UPDATE, so the demonstration company cannot be retired. "
                    "Nothing was changed." % table)
        for column in connection.execute(db.text(_UNIQUE_TEXT_COLUMNS)).all():
            table, name, max_length = column.table_name, column.column_name, column.max_length
            held = connection.execute(db.text(  # tenancy-ok: counts the retired organisation's own rows
                "SELECT count(*) FROM %s WHERE organization_id = :org AND %s IS NOT NULL"
                % (_quote(table), _quote(name))), {"org": org_id}).scalar()
            if not held:
                continue
            keep = "left(%s, %d)" % (_quote(name), max_length - len(suffix)) if max_length else _quote(name)
            statement = db.text(  # tenancy-ok: limited to the retired organisation's rows
                "UPDATE %s SET %s = %s || :suffix WHERE organization_id = :org AND %s IS NOT NULL"
                % (_quote(table), _quote(name), keep, _quote(name)))
            if table in guarded:
                # a guard may allow this update (it protects certain columns of certain rows) or refuse it:
                # try it in a savepoint, and leave a refused table exactly as it was
                try:
                    with connection.begin_nested():
                        result = connection.execute(statement, {"org": org_id, "suffix": suffix})
                except Exception:
                    if table not in sealed:
                        sealed.append(table)
                    continue
            else:
                result = connection.execute(statement, {"org": org_id, "suffix": suffix})
            changed["%s.%s" % (table, name)] = result.rowcount
        result = connection.execute(db.text(
            "UPDATE organizations SET slug = :slug, is_active = false, "
            "name = left(name, 200 - :extra) || :label, updated_at = :now WHERE id = :org"),
            {"slug": slug, "org": org_id, "now": now,
             "label": " (retired %s)" % now.strftime("%Y-%m-%d"), "extra": len(" (retired 0000-00-00)")})
        if result.rowcount != 1:
            raise click.ClickException("the demonstration organisation could not be retired")
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return {"slug": slug, "values": changed, "sealed": sorted(sealed)}


def reset_demo_company(slug: str = DEMO_SLUG) -> dict:
    """Retire the demonstration company and seed a fresh one.

    Returns ``{"retired": {...} or None, "created": {...}}``.
    """
    if slug != DEMO_SLUG:
        raise NotTheDemoCompany(
            "reset-demo-company acts only on the demonstration company (%s), not on %r" % (DEMO_SLUG, slug))
    if not os.environ.get("DEMO_USER_PASSWORD"):
        # checked before anything is changed: the seed needs it
        raise click.ClickException(
            "DEMO_USER_PASSWORD environment variable is not set. Set it to a non-empty value first.")
    from app.commands.seed_demo_company import seed_demo_company
    from app.services import vendor_register

    org_id = db.session.execute(
        db.text("SELECT id FROM organizations WHERE slug = :slug"), {"slug": DEMO_SLUG}).scalar()
    retired = retire_organisation(org_id) if org_id is not None else None
    created = dict(seed_demo_company())
    new_id = db.session.execute(
        db.text("SELECT id FROM organizations WHERE slug = :slug"), {"slug": DEMO_SLUG}).scalar()
    # every system names the vendor that supplies it, and the link is recorded with it
    links = vendor_register.backfill_links(new_id)
    created["systems_linked_to_their_vendor_afterwards"] = links["systems_linked"]
    return {"retired": retired, "created": created}


@click.command("reset-demo-company")
@click.option("--slug", default=DEMO_SLUG, show_default=True,
              help="The organisation to reset. Only the demonstration company is accepted.")
@with_appcontext
def reset_demo_company_command(slug):
    """Retire the demonstration company (Lantern Quay Systems) and seed a fresh one."""
    try:
        result = reset_demo_company(slug)
    except NotTheDemoCompany as error:
        raise click.ClickException(str(error)) from error
    retired = result["retired"]
    if retired is None:
        click.echo("retired: no demonstration company was present")
    else:
        click.echo("retired: the old demonstration company is now %s (inactive; nothing deleted)" % retired["slug"])
        for key, rows in sorted(retired["values"].items()):
            click.echo("  %s: %d value(s) renamed" % (key, rows))
        click.echo("  total values renamed: %d" % sum(retired["values"].values()))
        if retired["sealed"]:
            click.echo("  left exactly as sealed: %s" % ", ".join(retired["sealed"]))
    click.echo("created:")
    for key, value in sorted(result["created"].items()):
        if value:
            click.echo("  %s: %s" % (key.replace("_", " "), value))
    click.echo("  total rows created: %d" % sum(result["created"].values()))


def init_app(app):
    app.cli.add_command(reset_demo_company_command)
