"""Add seed data tracking columns to VendorOrganization

Revision ID: 20260205_add_vendor_seed_tracking
Revises: 20260101_previous_migration
Create Date: 2026-02-05 01:20:00.000000

This migration adds comprehensive seed data tracking to the VendorOrganization model
to support the UnifiedVendorSeeder system. It runs in 3 phases:

Phase 1: Add new columns (all nullable initially - non-breaking)
Phase 2: Backfill existing vendors with generated code and seed_source_id
Phase 3: Add NOT NULL constraints and unique constraints
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '20260205_add_vendor_seed_tracking'
down_revision = 'fac924608f6e'
downgrade_revision = None  # Will be set to previous migration ID


def upgrade():
    """Phase 1 & 2 & 3: Add seed tracking columns with backfill."""
    
    # =========================================================================
    # PHASE 1: Add new columns (all nullable initially - non-breaking)
    # =========================================================================
    op.add_column(
        'vendor_organizations',
        sa.Column('code', sa.String(50), nullable=True, unique=False, index=True)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('seed_source_id', sa.String(100), nullable=True, unique=False, index=True)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('seed_version', sa.String(50), nullable=True, index=True)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('is_seed_data', sa.Boolean, nullable=True, default=False)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('seeded_at', sa.DateTime, nullable=True)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('seeded_by', sa.String(100), nullable=True)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('seed_checksum', sa.String(64), nullable=True)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('last_manual_edit_at', sa.DateTime, nullable=True)
    )
    op.add_column(
        'vendor_organizations',
        sa.Column('last_manual_edit_by', sa.String(100), nullable=True)
    )
    
    # =========================================================================
    # PHASE 2: Backfill existing vendors (generate code and seed_source_id)
    # =========================================================================
    # For each existing vendor, generate:
    # - code: Derived from name (e.g., "SAP SE" → "VEND-SAP")
    # - seed_source_id: Unique ID for this vendor (e.g., "legacy-vendor-{id}")
    # - is_seed_data: FALSE (these are legacy/manual vendors, not from seed)
    # - seeded_at: NULL (these weren't loaded from seed)
    # - seeded_by: "manual" (legacy vendor created manually)
    
    connection = op.get_bind()
    
    # Get all existing vendors
    result = connection.execute(sa.text(
        "SELECT id, name FROM vendor_organizations WHERE code IS NULL"
    ))
    
    for vendor_id, vendor_name in result:
        # Generate code from name: "SAP SE" → "VEND-SAP"
        code_prefix = vendor_name.split()[0].upper()  # First word
        code = f"VEND-{code_prefix}"
        
        # Generate seed_source_id: "legacy-vendor-{id}"
        seed_source_id = f"legacy-vendor-{vendor_id}"
        
        # Update vendor with generated values
        connection.execute(sa.text(
            """
            UPDATE vendor_organizations
            SET 
                code = :code,
                seed_source_id = :seed_source_id,
                is_seed_data = FALSE,
                seeded_by = 'manual'
            WHERE id = :vendor_id
            """
        ), {
            'code': code,
            'seed_source_id': seed_source_id,
            'vendor_id': vendor_id
        })
    
    connection.commit()
    
    # =========================================================================
    # PHASE 3: Add NOT NULL and UNIQUE constraints
    # =========================================================================
    # Now that all vendors have values, make code/seed_source_id required and unique
    
    op.alter_column(
        'vendor_organizations',
        'code',
        existing_type=sa.String(50),
        nullable=False,
        existing_nullable=True
    )
    
    op.alter_column(
        'vendor_organizations',
        'seed_source_id',
        existing_type=sa.String(100),
        nullable=False,
        existing_nullable=True
    )
    
    op.alter_column(
        'vendor_organizations',
        'is_seed_data',
        existing_type=sa.Boolean,
        nullable=False,
        existing_default=False,
        existing_nullable=True
    )
    
    op.alter_column(
        'vendor_organizations',
        'seeded_by',
        existing_type=sa.String(100),
        nullable=False,
        existing_default='manual',
        existing_nullable=True
    )
    
    # Add unique constraints
    op.create_unique_constraint(
        'uq_vendor_organizations_code',
        'vendor_organizations',
        ['code']
    )
    
    op.create_unique_constraint(
        'uq_vendor_organizations_seed_source_id',
        'vendor_organizations',
        ['seed_source_id']
    )


def downgrade():
    """Remove all seed tracking columns (rollback migration)."""
    
    # Drop unique constraints
    op.drop_constraint(
        'uq_vendor_organizations_code',
        'vendor_organizations',
        type_='unique'
    )
    
    op.drop_constraint(
        'uq_vendor_organizations_seed_source_id',
        'vendor_organizations',
        type_='unique'
    )
    
    # Drop all columns
    op.drop_column('vendor_organizations', 'code')
    op.drop_column('vendor_organizations', 'seed_source_id')
    op.drop_column('vendor_organizations', 'seed_version')
    op.drop_column('vendor_organizations', 'is_seed_data')
    op.drop_column('vendor_organizations', 'seeded_at')
    op.drop_column('vendor_organizations', 'seeded_by')
    op.drop_column('vendor_organizations', 'seed_checksum')
    op.drop_column('vendor_organizations', 'last_manual_edit_at')
    op.drop_column('vendor_organizations', 'last_manual_edit_by')
