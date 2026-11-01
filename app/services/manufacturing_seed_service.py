"""
Manufacturing seeding utilities

Provides a small, idempotent seeder used for local verification and testing.
This is intentionally minimal: it will create a BusinessDomain 'MFG' and a
single UnifiedCapability (if none found) and then upsert a ManufacturingCapability
linked to that unified capability.
"""
from datetime import datetime
from typing import Dict

from flask import current_app

from .. import db
from ..models.manufacturing_capability import ManufacturingCapability
from ..models.unified_capability import BusinessDomain, UnifiedCapability
from ..seed_data.manufacturing_seed_data import get_flat_capabilities


class ManufacturingSeedService:
    @staticmethod
    def seed_sample(create_unified_if_missing: bool = True) -> Dict[str, int]:
        """Seed a minimal ManufacturingCapability for verification.

        Returns a summary dict with created/updated/errors counts.
        """
        created_unified = 0
        created_mfg = 0
        updated_mfg = 0
        errors = 0

        try:
            # Ensure MFG domain exists
            domain = BusinessDomain.query.filter_by(code="MFG").first()
            if not domain:
                domain = BusinessDomain(
                    code="MFG", name="Manufacturing", description="Manufacturing domain"
                )
                db.session.add(domain)
                db.session.flush()

            # Try to find an existing unified capability that looks manufacturing-related
            unified = UnifiedCapability.query.filter(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                (UnifiedCapability.manufacturing_critical == True)
                | (UnifiedCapability.industry_domain.ilike("%manufactur%"))
                | (UnifiedCapability.code.ilike("%MFG%"))
            ).first()

            # Create a lightweight unified capability if none found and allowed
            if not unified and create_unified_if_missing:
                unified = UnifiedCapability(
                    name="Manufacturing Seed Capability",
                    code="MFG-SEED - 001",
                    level=3,
                    domain_id=domain.id,
                    industry_domain="Manufacturing",
                    manufacturing_critical=True,
                    business_owner="Manufacturing Lead",
                    strategic_importance="medium",
                )
                db.session.add(unified)
                db.session.flush()
                created_unified += 1

            if not unified:
                current_app.logger.warning(
                    "No unified capability found or created; aborting manufacturing seed."
                )
                return {
                    "created_unified": created_unified,
                    "created_manufacturing": created_mfg,
                    "updated_manufacturing": updated_mfg,
                    "errors": errors,
                }

            # Upsert manufacturing capability linked to the unified capability
            existing = ManufacturingCapability.query.filter_by(
                unified_capability_id=unified.id
            ).first()

            if existing:
                existing.manufacturing_domain = existing.manufacturing_domain or "production"
                existing.manufacturing_process_type = (
                    existing.manufacturing_process_type or "make_to_stock"
                )
                existing.industry_subsector = existing.industry_subsector or "industrial"
                existing.lean_maturity = existing.lean_maturity or 2
                existing.assessment_notes = (
                    existing.assessment_notes or "Seed updated for verification"
                )
                existing.updated_at = datetime.utcnow()
                updated_mfg += 1
            else:
                new_mfg = ManufacturingCapability(
                    unified_capability_id=unified.id,
                    manufacturing_domain="production",
                    manufacturing_process_type="make_to_stock",
                    industry_subsector="industrial",
                    oee_target=80.0,
                    oee_current=60.0,
                    lean_maturity=2,
                    assessment_notes="Seeded sample manufacturing capability for UI verification",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.session.add(new_mfg)
                db.session.flush()
                created_mfg += 1

            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error while seeding manufacturing capability")
            errors += 1

        return {
            "created_unified": created_unified,
            "created_manufacturing": created_mfg,
            "updated_manufacturing": updated_mfg,
            "errors": errors,
        }

    @staticmethod
    def seed_capabilities(create_domain_if_missing: bool = True) -> Dict[str, int]:
        """Two-pass idempotent seeder for a manufacturing capability taxonomy.

        First pass: create or update UnifiedCapability records by code (no parent links).
        Second pass: set parent_capability_id using parent_code -> id mapping.
        """
        created = 0
        updated = 0
        errors = 0
        caps = []

        try:
            # Ensure MFG domain exists
            domain = BusinessDomain.query.filter_by(code="MFG").first()
            if not domain and create_domain_if_missing:
                domain = BusinessDomain(
                    code="MFG", name="Manufacturing", description="Manufacturing domain"
                )
                db.session.add(domain)
                db.session.flush()

            caps = get_flat_capabilities()
            code_to_id = {}

            # First pass: create/update capabilities without parent links
            for cap in caps:
                existing = UnifiedCapability.query.filter_by(code=cap["code"]).first()  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                if existing:
                    existing.name = cap.get("name", existing.name)
                    existing.description = cap.get("description", existing.description)
                    existing.level = cap.get("level", existing.level)
                    existing.domain_id = domain.id
                    existing.industry_domain = cap.get("industry_domain", existing.industry_domain)
                    existing.manufacturing_critical = cap.get(
                        "manufacturing_critical", existing.manufacturing_critical
                    )
                    existing.business_owner = cap.get("business_owner", existing.business_owner)
                    existing.strategic_importance = cap.get(
                        "strategic_importance", existing.strategic_importance
                    )
                    existing.category = cap.get("category", existing.category)
                    existing.updated_at = datetime.utcnow()
                    db.session.flush()
                    updated += 1
                    code_to_id[existing.code] = existing.id
                else:
                    new_cap = UnifiedCapability(
                        name=cap["name"],
                        code=cap["code"],
                        description=cap.get("description"),
                        level=cap.get("level", 1),
                        domain_id=domain.id,
                        industry_domain=cap.get("industry_domain", "Manufacturing"),
                        manufacturing_critical=cap.get("manufacturing_critical", True),
                        business_owner=cap.get("business_owner", None),
                        strategic_importance=cap.get("strategic_importance", "medium"),
                        category=cap.get("category", "supporting"),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    db.session.add(new_cap)
                    db.session.flush()
                    created += 1
                    code_to_id[new_cap.code] = new_cap.id

            db.session.commit()

            # Second pass: set parent relationships
            for cap in caps:
                parent_code = cap.get("parent_code")
                if parent_code:
                    child = UnifiedCapability.query.filter_by(code=cap["code"]).first()  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                    parent_id = code_to_id.get(parent_code)
                    if child and parent_id:
                        child.parent_capability_id = parent_id

            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error while seeding manufacturing capabilities")
            errors += 1

        return {"created": created, "updated": updated, "total": len(caps), "errors": errors}
