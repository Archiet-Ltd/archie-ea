"""Let a technical founder's assistant make the changes a one-person workspace makes.

The self-serve sign-up gives a CTO the cto_founder charter, and its first default forbade every
write, so a founder alone in a workspace could not get a change made through the chat. The default
now permits the founder's own model-keeping changes (application status, ArchiMate elements and
relationships, solutions, capability links and maturity, rationalisation scores) and still
forbids what needs a board or another person (submitting to the ARB).

For each organisation whose latest cto_founder charter is still the old default (the same purpose
and readable list, no tools proposed, every one of those changes forbidden, and none of the rules a
charter written through the agent registry always carries), the current default is appended as the
next version. A charter an administrator amended is left as it is. Old versions are never deleted or
rewritten. Idempotent: a second run adds nothing. The default comes from
app.commands.seed_agent_charters (one copy).

Revision ID: 20261110_founder_charter
Revises: 20261110_change_review_columns
Create Date: 2026-11-10
"""
import json
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "20261110_founder_charter"
down_revision = "20261110_change_review_columns"
branch_labels = None
depends_on = None

_PERSONA = "cto_founder"
_OLD_PURPOSE = "Technical founder's view — what breaks, who owns it, what it costs, what to fix first."
_OLD_READABLE = ["application", "capability", "risk"]
_AUTHORED_MARKERS = ("no_fabrication", "cite_source", "propose_dont_dispose", "governance_wins")


def _as_list(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value


def _still_the_old_default(row, founder_tools):
    if row["purpose"] != _OLD_PURPOSE:
        return False
    if sorted(_as_list(row["readable_entities"]) or []) != sorted(_OLD_READABLE):
        return False
    if _as_list(row["proposable_actions"]):
        return False
    forbidden = _as_list(row["forbidden_actions"]) or []
    if any(marker in forbidden for marker in _AUTHORED_MARKERS):
        return False
    return all(tool in forbidden for tool in founder_tools)


def upgrade():
    from app.commands.seed_agent_charters import FOUNDER_CHANGE_TOOLS, default_charter_fields

    fields = default_charter_fields(_PERSONA)
    bind = op.get_bind()
    held = defaultdict(list)
    for r in bind.execute(sa.text(
        "SELECT organization_id, version, purpose, readable_entities, proposable_actions, forbidden_actions "
        "FROM agent_charters WHERE persona = :persona"
    ), {"persona": _PERSONA}):
        held[r[0]].append({
            "version": r[1], "purpose": r[2], "readable_entities": r[3],
            "proposable_actions": r[4], "forbidden_actions": r[5],
        })
    # Remember which rows this revision appends, so the downgrade removes those and no others.
    bind.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS founder_charter_added (charter_id INTEGER PRIMARY KEY)"
    ))
    insert = sa.text(
        "INSERT INTO agent_charters (organization_id, persona, version, purpose, readable_entities, "
        "proposable_actions, forbidden_actions, charter_text, created_at, updated_at) "
        "VALUES (:org, :persona, :version, :purpose, CAST(:readable AS JSON), CAST(:proposable AS JSON), "
        "CAST(:forbidden AS JSON), :text, NOW(), NOW()) "
        "ON CONFLICT (organization_id, persona, version) DO NOTHING RETURNING id"
    )
    for org, rows in held.items():
        latest = max(rows, key=lambda r: r["version"])
        if not _still_the_old_default(latest, FOUNDER_CHANGE_TOOLS):
            continue
        added = bind.execute(insert, {
            "org": org, "persona": _PERSONA, "version": latest["version"] + 1, "purpose": fields["purpose"],
            "readable": json.dumps(fields["readable_entities"]),
            "proposable": json.dumps(fields["proposable_actions"]),
            "forbidden": json.dumps(fields["forbidden_actions"]),
            "text": fields["charter_text"],
        }).scalar()
        if added is not None:
            bind.execute(sa.text(
                "INSERT INTO founder_charter_added (charter_id) VALUES (:id) ON CONFLICT DO NOTHING"
            ), {"id": added})


def downgrade():
    # Delete exactly the rows the upgrade appended, recorded by id. A charter an administrator
    # amended later is a row this revision did not add and is never deleted.
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("founder_charter_added"):
        return
    bind.execute(sa.text(
        "DELETE FROM agent_charters WHERE id IN (SELECT charter_id FROM founder_charter_added)"
    ))
    bind.execute(sa.text("DROP TABLE founder_charter_added"))
