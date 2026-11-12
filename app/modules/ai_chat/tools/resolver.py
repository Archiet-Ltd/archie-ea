"""
Entity resolver: converts human-readable names to database IDs.

The LLM always passes names (never IDs) to avoid hallucinated integers.
Resolver returns:
  {"resolved": True,  "id": 42, "name": "Salesforce CRM"}    # unambiguous
  {"resolved": False, "candidates": [{id, name}, ...]}        # ambiguous
  {"resolved": False, "candidates": []}                       # not found
"""

import logging

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
            meant = element_named(org_id, name) if org_id is not None else None
            exact = ArchiMateElement.query.filter_by(id=meant, organization_id=org_id).first() if meant else None
            if exact:
                return {"resolved": True, "id": exact.id, "name": exact.name, "type": exact.type, "layer": exact.layer}
            candidates = ArchiMateElement.query.filter(
                ArchiMateElement.name.ilike(f"%{name}%")
            ).limit(MAX_CANDIDATES).all()
            if len(candidates) == 1:
                c = candidates[0]
                return {"resolved": True, "id": c.id, "name": c.name, "type": c.type, "layer": c.layer}
            return {
                "resolved": False,
                "candidates": [{"id": e.id, "name": e.name, "type": e.type} for e in candidates],
            }
        except Exception as e:
            logger.warning("resolve_archimate_element error: %s", e)
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
