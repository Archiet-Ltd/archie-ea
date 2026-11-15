"""
Entity resolver: converts human-readable names to database IDs.

The LLM always passes names (never IDs) to avoid hallucinated integers.
Resolver returns:
  {"resolved": True,  "id": 42, "name": "Salesforce CRM"}    # unambiguous
  {"resolved": False, "candidates": [{id, name}, ...]}        # ambiguous
  {"resolved": False, "candidates": []}                       # not found
"""

import logging

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 6


class EntityResolver:

    # ------------------------------------------------------------------ #
    # Applications                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_application(name: str) -> dict:
        try:
            from app.models.application_component_fast import ApplicationComponent
            exact = ApplicationComponent.query.filter(
                ApplicationComponent.name.ilike(name)
            ).first()
            if exact:
                return {"resolved": True, "id": exact.id, "name": exact.name}
            candidates = ApplicationComponent.query.filter(
                ApplicationComponent.name.ilike(f"%{name}%")
            ).limit(MAX_CANDIDATES).all()
            if len(candidates) == 1:
                return {"resolved": True, "id": candidates[0].id, "name": candidates[0].name}
            return {
                "resolved": False,
                "candidates": [{"id": a.id, "name": a.name} for a in candidates],
            }
        except Exception as e:
            logger.warning("resolve_application error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    # ------------------------------------------------------------------ #
    # Capabilities                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_capability(name: str) -> dict:
        try:
            from app.models.business_capabilities import BusinessCapability
            exact = BusinessCapability.query.filter(
                BusinessCapability.name.ilike(name)
            ).first()
            if exact:
                return {"resolved": True, "id": exact.id, "name": exact.name}
            candidates = BusinessCapability.query.filter(
                BusinessCapability.name.ilike(f"%{name}%")
            ).limit(MAX_CANDIDATES).all()
            if len(candidates) == 1:
                return {"resolved": True, "id": candidates[0].id, "name": candidates[0].name}
            return {
                "resolved": False,
                "candidates": [{"id": c.id, "name": c.name} for c in candidates],
            }
        except Exception as e:
            logger.warning("resolve_capability error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    # ------------------------------------------------------------------ #
    # Solutions                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_solution(name: str) -> dict:
        try:
            from app.models.solution_models import Solution
            exact = Solution.query.filter(
                Solution.name.ilike(name)
            ).first()
            if exact:
                return {"resolved": True, "id": exact.id, "name": exact.name}
            candidates = Solution.query.filter(
                Solution.name.ilike(f"%{name}%")
            ).limit(MAX_CANDIDATES).all()
            if len(candidates) == 1:
                return {"resolved": True, "id": candidates[0].id, "name": candidates[0].name}
            return {
                "resolved": False,
                "candidates": [{"id": s.id, "name": s.name} for s in candidates],
            }
        except Exception as e:
            logger.warning("resolve_solution error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    # ------------------------------------------------------------------ #
    # ArchiMate Elements                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_archimate_element(name: str) -> dict:
        try:
            try:
                from app.models.archimate_core import ArchiMateElement
            except ImportError:
                from app.models.models import ArchiMateElement
            from app.middleware.tenant_context import current_org_id
            from app.modules.dashboard.services.overview_figures import element_named

            org_id = current_org_id()
            if org_id is None:
                return {"resolved": False, "candidates": [], "error": "No organisation context"}

            # First, try the standard ArchiMateElement lookup
            meant = element_named(org_id, name)
            exact = ArchiMateElement.query.filter_by(id=meant, organization_id=org_id).first() if meant else None
            if exact:
                return {"resolved": True, "id": exact.id, "name": exact.name, "type": exact.type, "layer": exact.layer}

            # Also search motivation element tables that have archimate_element_id
            # These tables store the "journey layer" motivation entities which mirror to ArchiMateElement
            from app.models.solution_architect_models import (
                SolutionConstraint, SolutionDriver, SolutionGoal, SolutionRequirement,
            )
            from app.models.solution_lifecycle_models import SolutionRisk

            # (model, the column holding its name)
            motivation_models = [
                (SolutionDriver, "name"),
                (SolutionGoal, "name"),
                (SolutionConstraint, "name"),
                (SolutionRequirement, "name"),
                (SolutionRisk, "risk_name"),
            ]

            for model_class, name_column in motivation_models:
                # Search by name in the motivation table
                candidate = model_class.query.filter(
                    getattr(model_class, name_column).ilike(name),
                    model_class.organization_id == org_id,
                    model_class.archimate_element_id.isnot(None)
                ).first()
                if candidate:
                    # Return the linked ArchiMateElement
                    ae = ArchiMateElement.query.filter_by(
                        id=candidate.archimate_element_id,
                        organization_id=org_id
                    ).first()
                    if ae:
                        return {"resolved": True, "id": ae.id, "name": ae.name, "type": ae.type, "layer": ae.layer}

            # Fallback: fuzzy search on ArchiMateElement table
            candidates = ArchiMateElement.query.filter(
                ArchiMateElement.name.ilike(f"%{name}%"),
                ArchiMateElement.organization_id == org_id
            ).limit(MAX_CANDIDATES).all()
            if len(candidates) == 1:
                c = candidates[0]
                return {"resolved": True, "id": c.id, "name": c.name, "type": c.type, "layer": c.layer}

            # Also collect candidates from motivation tables for ambiguity reporting
            motivation_candidates = []
            for model_class, name_column in motivation_models:
                cands = model_class.query.filter(
                    getattr(model_class, name_column).ilike(f"%{name}%"),
                    model_class.organization_id == org_id,
                    model_class.archimate_element_id.isnot(None)
                ).limit(MAX_CANDIDATES).all()
                for c in cands:
                    ae = ArchiMateElement.query.filter_by(
                        id=c.archimate_element_id,
                        organization_id=org_id
                    ).first()
                    if ae:
                        motivation_candidates.append({"id": ae.id, "name": ae.name, "type": ae.type})

            all_candidates = [{"id": e.id, "name": e.name, "type": e.type} for e in candidates] + motivation_candidates
            # Deduplicate by id
            seen = set()
            unique_candidates = []
            for c in all_candidates:
                if c["id"] not in seen:
                    seen.add(c["id"])
                    unique_candidates.append(c)

            return {
                "resolved": False,
                "candidates": unique_candidates[:MAX_CANDIDATES],
            }
        except SQLAlchemyError as e:
            logger.exception("resolve_archimate_element database error")
            return {"resolved": False, "candidates": [], "error": str(e)}

    # ------------------------------------------------------------------ #
    # Vendor Products                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_vendor_product(name: str) -> dict:
        try:
            from app.models.vendor.vendor_organization import VendorProduct
            exact = VendorProduct.query.filter(
                VendorProduct.name.ilike(name)
            ).first()
            if exact:
                return {"resolved": True, "id": exact.id, "name": exact.name}
            candidates = VendorProduct.query.filter(
                VendorProduct.name.ilike(f"%{name}%")
            ).limit(MAX_CANDIDATES).all()
            if len(candidates) == 1:
                return {"resolved": True, "id": candidates[0].id, "name": candidates[0].name}
            return {
                "resolved": False,
                "candidates": [{"id": v.id, "name": v.name} for v in candidates],
            }
        except Exception as e:
            logger.warning("resolve_vendor_product error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}
