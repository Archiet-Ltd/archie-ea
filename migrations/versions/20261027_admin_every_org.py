"""Every organisation that has members has an Administrator.

An organisation whose only members hold a non-administrator Role cannot open its
own settings or members pages. This data migration finds each organisation that
has members and no Administrator and makes its earliest member the
Administrator, in batches. Each repair is printed and is written to the audit
trail through the sealed audit writer (the append-only triggers stay on).

Idempotent: an organisation that has an Administrator is never selected, so a
second run changes nothing. Platform administration is never granted here.
The down step does nothing: a repaired organisation keeps its Administrator.

Revision ID: 20261027_admin_every_org
Revises: 20261025_vendor_catalogue
Create Date: 2026-10-27
"""
from __future__ import annotations

from alembic import op

revision = "20261027_admin_every_org"
down_revision = "20261026_charter_refresh"
branch_labels = None
depends_on = None


def upgrade():
    from app.services.administrator_guard import repair_missing_administrators, report_line

    repaired = repair_missing_administrators(op.get_bind())
    for item in repaired:
        print(report_line(item))
    print(f"administrators: {len(repaired)} organisation(s) repaired")


def downgrade():
    pass
