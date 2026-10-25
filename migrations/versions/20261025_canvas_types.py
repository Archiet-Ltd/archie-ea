"""The canvas type, and the box an element sits in on a canvas view.

Adds two nullable columns: ``business_model_canvases.canvas_type`` (which of the
framework canvases a record draws; NULL stays the Business Model Canvas) and
``saved_diagram_elements.canvas_box`` (the box the element sits in on that view).
Imports nothing from ``app``.

Idempotent: each column is added only when absent.

Revision ID: 20261025_canvas_types
Revises: 20261025_price_plans_canvas
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261025_canvas_types"
down_revision = "20261025_price_plans_canvas"
branch_labels = None
depends_on = None

COLUMNS = (("business_model_canvases", "canvas_type"), ("saved_diagram_elements", "canvas_box"))


def upgrade():
    inspector = inspect(op.get_bind())
    for table, column in COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column(column, sa.String(64), nullable=True))


def downgrade():
    inspector = inspect(op.get_bind())
    for table, column in COLUMNS:
        if column in {c["name"] for c in inspector.get_columns(table)}:
            op.drop_column(table, column)
