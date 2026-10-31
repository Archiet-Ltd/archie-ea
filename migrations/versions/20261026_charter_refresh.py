"""Give every organisation the current default charter of every persona (QA-048).

Charters enforce readable_entities for read tools, but stored charters predate those lists
and 97 organisations hold none (and none holds a cto_founder charter), so their chat refused
tools it used to run. This appends the current default charter:
  - a persona with no charter gets the default as version 1;
  - a persona whose latest charter is a seeded version 1 that differs from today's default
    gets the default as version 2;
  - a charter an organisation edited or authored (version 2 or later, or carrying the registry's
    non-removable rules) is left alone.
Old versions are never deleted or rewritten. Idempotent: a second run adds nothing. The
defaults and the decision come from app.commands.seed_agent_charters (one copy).

Revision ID: 20261026_charter_refresh
Revises: 20261025_vendor_catalogue
Create Date: 2026-10-26
"""
import json
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "20261026_charter_refresh"
down_revision = "20261025_vendor_catalogue"
branch_labels = None
depends_on = None


def upgrade():
    from app.commands.seed_agent_charters import plan_charter_row
    from app.modules.ai_chat.services.architect_persona_charters import ARCHITECT_PERSONAS

    bind = op.get_bind()
    orgs = [r[0] for r in bind.execute(sa.text("SELECT id FROM organizations ORDER BY id"))]
    held = defaultdict(list)
    for r in bind.execute(sa.text(
        "SELECT organization_id, persona, version, purpose, readable_entities, proposable_actions, "
        "forbidden_actions, charter_text FROM agent_charters"
    )):
        held[(r[0], r[1])].append({
            "version": r[2], "purpose": r[3], "readable_entities": r[4],
            "proposable_actions": r[5], "forbidden_actions": r[6], "charter_text": r[7],
        })
    insert = sa.text(
        "INSERT INTO agent_charters (organization_id, persona, version, purpose, readable_entities, "
        "proposable_actions, forbidden_actions, charter_text, created_at, updated_at) "
        "VALUES (:org, :persona, :version, :purpose, CAST(:readable AS JSON), CAST(:proposable AS JSON), "
        "CAST(:forbidden AS JSON), :text, NOW(), NOW()) "
        "ON CONFLICT (organization_id, persona, version) DO NOTHING"
    )
    for org in orgs:
        for persona in ARCHITECT_PERSONAS:
            plan = plan_charter_row(persona, held.get((org, persona), []))
            if plan is None:
                continue
            version, f = plan
            bind.execute(insert, {
                "org": org, "persona": persona, "version": version, "purpose": f["purpose"],
                "readable": json.dumps(f["readable_entities"]),
                "proposable": json.dumps(f["proposable_actions"]),
                "forbidden": json.dumps(f["forbidden_actions"]),
                "text": f["charter_text"],
            })


def downgrade():
    # Append-only data migration: the added versions are kept; nothing is deleted.
    pass
