"""
Entity resolver: converts human-readable names to database IDs.

The LLM always passes names (never IDs) to avoid hallucinated integers.
Resolver returns:
  {"resolved": True,  "id": 42, "name": "Salesforce CRM"}    # unambiguous
  {"resolved": False, "candidates": [{id, name}, ...]}        # ambiguous
  {"resolved": False, "candidates": []}                       # not found

Every resolver names the organisation in its statement. A caller with no request (the chat's worker thread)
passes it; one inside a request may leave it out and gets the active organisation. With neither, nothing
resolves.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 6



def _own_org(organization_id):
    if organization_id is not None:
        return organization_id
    from app.middleware.tenant_context import current_org_id

    return current_org_id()


def _by_name(model, name, organization_id, label, extra=()):
    """Resolve one row of ``model`` in the organisation by name: the exact name, else the only partial match."""
    org_id = _own_org(organization_id)
    if org_id is None:
        return {"resolved": False, "candidates": []}
    scope = [model.organization_id == org_id, *extra]
    exact = model.query.filter(model.name.ilike(_literal(name), escape="\\"), *scope).order_by(model.id).all()
    if len(exact) == 1:
        return {"resolved": True, "id": exact[0].id, "name": exact[0].name}
    if len(exact) > 1:
        return {"resolved": False,
                "candidates": [{"id": r.id, "name": r.name} for r in exact[:MAX_CANDIDATES]]}
    candidates = model.query.filter(model.name.ilike(f"%{_literal(name)}%", escape="\\"), *scope).order_by(model.id).limit(
        MAX_CANDIDATES).all()
    if len(candidates) == 1:
        return {"resolved": True, "id": candidates[0].id, "name": candidates[0].name}
    return {"resolved": False, "candidates": [{"id": r.id, "name": r.name} for r in candidates]}


def _literal(text) -> str:
    """The text with LIKE's own characters made plain, so a name containing % or _ matches only itself."""
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class EntityResolver:

    @staticmethod
    def resolve_application(name: str, organization_id=None) -> dict:
        try:
            from app.models.application_component_fast import ApplicationComponent
            return _by_name(ApplicationComponent, name, organization_id, "application")
        except Exception as e:
            logger.warning("resolve_application error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    @staticmethod
    def resolve_capability(name: str, organization_id=None) -> dict:
        try:
            from app.models.business_capabilities import BusinessCapability
            return _by_name(BusinessCapability, name, organization_id, "capability")
        except Exception as e:
            logger.warning("resolve_capability error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    @staticmethod
    def resolve_solution(name: str, organization_id=None) -> dict:
        try:
            from app.models.solution_models import Solution
            return _by_name(Solution, name, organization_id, "solution")
        except Exception as e:
            logger.warning("resolve_solution error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    @staticmethod
    def resolve_archimate_element(name: str, organization_id=None, element_type=None) -> dict:
        try:
            try:
                from app.models.archimate_core import ArchiMateElement
            except ImportError:
                from app.models.models import ArchiMateElement
            from app.modules.dashboard.services.overview_figures import element_named

            org_id = _own_org(organization_id)
            if org_id is None:
                return {"resolved": False, "candidates": []}
            of_type = [ArchiMateElement.type == element_type] if element_type else []
            meant = element_named(org_id, name) if not element_type else None
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
                    getattr(model_class, name_column).ilike(_literal(name), escape="\\"),
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
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.name.ilike(f"%{_literal(name)}%", escape="\\"),
                *of_type,
            ).order_by(ArchiMateElement.id).limit(MAX_CANDIDATES).all()
            if len(candidates) == 1:
                c = candidates[0]
                return {"resolved": True, "id": c.id, "name": c.name, "type": c.type, "layer": c.layer}

            # Also collect candidates from motivation tables for ambiguity reporting
            motivation_candidates = []
            for model_class, name_column in motivation_models:
                cands = model_class.query.filter(
                    getattr(model_class, name_column).ilike(f"%{_literal(name)}%", escape="\\"),
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

    @staticmethod
    def resolve_goal(name: str, organization_id=None) -> dict:
        """A goal of the organisation by name, as its element: the exact name (every one of that name when
        there are several), else the only goal whose name contains the words."""
        try:
            from app.models.archimate_core import ArchiMateElement

            org_id = _own_org(organization_id)
            if org_id is None:
                return {"resolved": False, "candidates": []}
            base = [ArchiMateElement.organization_id == org_id, ArchiMateElement.type == "Goal"]
            exact = ArchiMateElement.query.filter(ArchiMateElement.name.ilike(_literal(name), escape="\\"), *base).order_by(
                ArchiMateElement.id).limit(MAX_CANDIDATES).all()
            if not exact:
                exact = ArchiMateElement.query.filter(ArchiMateElement.name.ilike(f"%{_literal(name)}%", escape="\\"), *base).order_by(
                    ArchiMateElement.id).limit(MAX_CANDIDATES).all()
            if len(exact) == 1:
                return {"resolved": True, "id": exact[0].id, "name": exact[0].name}
            return {"resolved": False, "candidates": [{"id": e.id, "name": e.name} for e in exact]}
        except Exception as e:
            logger.warning("resolve_goal error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    @staticmethod
    def resolve_risk(title: str, organization_id=None) -> dict:
        """A risk of the organisation by title, the same way."""
        try:
            from app.models.risk import Risk

            org_id = _own_org(organization_id)
            if org_id is None:
                return {"resolved": False, "candidates": []}
            base = [Risk.organization_id == org_id]
            found = Risk.query.filter(Risk.title.ilike(_literal(title), escape="\\"), *base).order_by(Risk.id).limit(MAX_CANDIDATES).all()
            if not found:
                found = Risk.query.filter(Risk.title.ilike(f"%{_literal(title)}%", escape="\\"), *base).order_by(Risk.id).limit(
                    MAX_CANDIDATES).all()
            if len(found) == 1:
                return {"resolved": True, "id": found[0].id, "name": found[0].title}
            return {"resolved": False, "candidates": [{"id": r.id, "name": r.title} for r in found]}
        except Exception as e:
            logger.warning("resolve_risk error: %s", e)
            return {"resolved": False, "candidates": [], "error": str(e)}

    @staticmethod
    def resolve_vendor_product(name: str, organization_id=None) -> dict:
        # The vendor product list is one catalogue shared by every organisation: it has no organisation
        # column to filter on, so the argument is accepted for a uniform call and not used.
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
