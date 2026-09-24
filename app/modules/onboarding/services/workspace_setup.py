"""Turn a saved Tell-us-more section into the organisation's own records.

The founder's question, answered here: these are not preferences to store and
forget, they are facts that should set up the organisation's workspace. Every
answer below becomes a real, tenant-scoped row, not a second copy of the
answer sitting only in ``Organization.settings``.

Reuse check (CLAUDE.md §12): before writing anything, this module was checked
against the model the intelligence module's Ask lenses actually read (ground
truth read from ``app/modules/intelligence/services/query_service.py``, not
assumed from a design document):

- **Compliance standards and their maturity** become a ``Risk``
  (``app/models/risk.py``, tenant-scoped) — the exact model
  ``IntelligenceQueryService.risk_for_element`` (Ask's Risk lens) reads by
  ``archimate_element_id``. ``ComplianceRequirement``/``ComplianceControl``
  (``app/models/compliance_models.py``) were checked first and ruled out:
  neither carries an ``organization_id``, and no Ask lens reads either one.
- **Frameworks, the implementation answers, and each transformation
  template's own category** become a ``UnifiedCapability``
  (``app/models/unified_capability.py``) — the one capability store
  (``docs/reuse-register.yml``'s ``capability-record``/``health-and-maturity``
  concepts). No numeric maturity level is ever invented here: "in use" and
  the implementation answers set a plain ``status`` (a state, not a score);
  "want" sets a ``target_maturity_level`` (a goal the organisation chose, not
  a claimed measurement) and leaves ``current_maturity_level`` unset, exactly
  the "unassessed reads as null" rule the model's own docstring states.
- **Each chosen transformation template** becomes one ``UnifiedWorkPackage``
  per phase Archiet's own template data lists (``app/models/
  unified_work_package.py``) — the model ``IntelligenceQueryService.
  programme_for_element`` (Ask's Programme lens) reads by
  ``archimate_element_id``.
- Every one of those rows is reached through an ``ArchiMateElement``,
  created with ``app/services/archimate_backbone.py``'s existing
  ``create_backbone_element``/``sync_archimate_element`` — the one place a
  motivation or implementation entity joins the ArchiMate model — never a
  second, hand-built element constructor.

The Ask "Strategy" lens (``strategy_for_element``) reads ``PortfolioInitiative``
specifically, which ``docs/reuse-register.yml``'s ``initiative-record`` concept
freezes against any new writer other than its demonstration seed. Frameworks
therefore land on the capability store, visible in the capability heatmap and
the element browser, not as a new ``PortfolioInitiative`` row — see the build
report's Deviations section.

Idempotency: every row this module creates carries a stable marker (an
``ArchiMateElement.custom_properties["onboarding_source"]`` string, or
``UnifiedCapability.source_table``/``source_id``, or a
``UnifiedWorkPackage.source_data`` JSON field) built from the section key and
the chosen option's own key. Re-saving a section looks the marker up first
and updates the existing row; it never creates a second one for the same
choice. Every created element also carries ``"source": "onboarding"`` in its
own properties, so it can be found, edited or removed like any other element.
"""
from __future__ import annotations

import json
import re

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.organization import Organization
from app.models.risk import Risk
from app.models.unified_capability import UnifiedCapability
from app.models.unified_work_package import UnifiedWorkPackage
from app.services import archimate_backbone

from . import reference_data

_MARKER_KEY = "onboarding_source"
_MAX_STACK_TOKENS = 8
_TOKEN_SPLIT = re.compile(r"[,/&+;\n]+|\s{2,}|(?<=\w)\s+(?=[A-Z])")


def apply_section(org: Organization, section_key: str, cleaned_answers: dict) -> dict:
    """Set up the organisation's workspace from one saved section's cleaned
    answers. Returns a small summary (counts by kind), mainly for tests and
    for a future "what this created" confirmation. Safe to call with an
    empty/None answers dict (a skip, or a section with nothing recordable)."""
    if not cleaned_answers:
        return {}
    handler = _HANDLERS.get(section_key)
    if handler is None:
        return {}
    summary = handler(org, cleaned_answers)
    db.session.commit()
    return summary


# ---------------------------------------------------------------------------
# ArchiMate element lookup/creation, shared by every kind of record below
# ---------------------------------------------------------------------------


def _find_element_by_marker(org_id: int, marker: str, *, layer: str | None = None) -> ArchiMateElement | None:
    query = ArchiMateElement.query.filter_by(organization_id=org_id)
    if layer is not None:
        query = query.filter_by(layer=layer)
    for element in query.all():
        if (element.custom_properties or {}).get(_MARKER_KEY) == marker:
            return element
    return None


# ---------------------------------------------------------------------------
# Compliance standards -> Risk (the Risk lens's own model)
# ---------------------------------------------------------------------------


def _apply_compliance(org: Organization, answers: dict) -> dict:
    standards = answers.get("standards") or {}
    created, updated = 0, 0
    for standard_key, status_key in standards.items():
        standard = reference_data.compliance_standard(standard_key)
        if standard is None:
            continue
        _, was_created = _upsert_compliance_risk(org, standard, status_key)
        created += was_created
        updated += 0 if was_created else 1
    return {"risks_created": created, "risks_updated": updated}


def _upsert_compliance_risk(org: Organization, standard: dict, status_key: str) -> tuple[Risk, bool]:
    marker = f"tell_us_more:compliance:{standard['id']}"
    label = standard["name"]
    likelihood, impact = reference_data.maturity_risk_score(status_key)
    status_label = reference_data.maturity_status_label(status_key)
    description = f"Recorded from Tell us more: {status_label}."

    element = _find_element_by_marker(org.id, marker, layer="Motivation")
    risk = None
    if element is not None:
        risk = Risk.query.filter_by(organization_id=org.id, archimate_element_id=element.id).first()

    if risk is not None:
        risk.title = f"{label} compliance"
        risk.description = description
        risk.likelihood = likelihood
        risk.impact = impact
        db.session.add(risk)
        return risk, False

    risk = Risk(
        organization_id=org.id,
        title=f"{label} compliance",
        description=description,
        likelihood=likelihood,
        impact=impact,
    )
    db.session.add(risk)
    db.session.flush()
    archimate_backbone.sync_archimate_element(
        risk,
        provenance={_MARKER_KEY: marker, "source": "onboarding", "onboarding_section": "compliance"},
    )
    db.session.add(risk)
    return risk, True


# ---------------------------------------------------------------------------
# Capability upsert, shared by frameworks, transformation categories and the
# implementation answers
# ---------------------------------------------------------------------------


def _upsert_capability(
    org: Organization,
    *,
    marker: str,
    name: str,
    status: str | None = None,
    target_maturity_level: int | None = None,
) -> tuple[UnifiedCapability, bool]:
    existing = UnifiedCapability.query.filter_by(
        organization_id=org.id, source_table="onboarding", source_id=marker
    ).first()
    if existing is not None:
        existing.name = name
        if status is not None:
            existing.status = status
        if target_maturity_level is not None:
            existing.target_maturity_level = target_maturity_level
        db.session.add(existing)
        return existing, False

    element = archimate_backbone.create_backbone_element(
        element_type="Capability",
        layer="strategy",
        name=name,
        organization_id=org.id,
        provenance={_MARKER_KEY: marker, "source": "onboarding"},
    )
    capability = UnifiedCapability(
        organization_id=org.id,
        name=name,
        level=1,
        archimate_element_id=element.id,
        archimate_layer="strategy",
        status=status or "defined",
        target_maturity_level=target_maturity_level,
        source_table="onboarding",
        source_id=marker,
        source_org_id=org.id,
        discovery_source="onboarding",
        extension_source="onboarding",
    )
    db.session.add(capability)
    db.session.flush()
    return capability, True


# ---------------------------------------------------------------------------
# Frameworks -> capability (existence/status only, never a fabricated score)
# ---------------------------------------------------------------------------


def _apply_frameworks(org: Organization, answers: dict) -> dict:
    created, updated = 0, 0
    for key in answers.get("frameworks_in_use") or []:
        framework = reference_data.framework(key)
        if framework is None:
            continue
        marker = f"tell_us_more:how_you_work:in_use:{key}"
        _, was_created = _upsert_capability(org, marker=marker, name=framework["name"], status="operational")
        created += was_created
        updated += 0 if was_created else 1
    for key in answers.get("frameworks_want") or []:
        framework = reference_data.framework(key)
        if framework is None:
            continue
        marker = f"tell_us_more:how_you_work:want:{key}"
        _, was_created = _upsert_capability(
            org, marker=marker, name=framework["name"], status="defined", target_maturity_level=3
        )
        created += was_created
        updated += 0 if was_created else 1
    return {"capabilities_created": created, "capabilities_updated": updated}


# ---------------------------------------------------------------------------
# Transformation templates -> a programme: one capability (the template's own
# category) and one UnifiedWorkPackage per phase the template data lists
# ---------------------------------------------------------------------------


def _find_work_package_by_marker(org: Organization, marker: str) -> UnifiedWorkPackage | None:
    rows = (
        UnifiedWorkPackage.query
        .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org.id, UnifiedWorkPackage.generation_method == "onboarding")
        .all()
    )
    for work_package in rows:
        try:
            source_data = json.loads(work_package.source_data or "{}")
        except (TypeError, ValueError):
            source_data = {}
        if source_data.get(_MARKER_KEY) == marker:
            return work_package
    return None


def _upsert_work_package(
    org: Organization,
    *,
    marker: str,
    name: str,
    business_capability_label: str,
    description: str | None,
    capability: UnifiedCapability | None,
) -> tuple[UnifiedWorkPackage, bool]:
    existing = _find_work_package_by_marker(org, marker)
    if existing is not None:
        existing.name = name[:255]
        existing.description = description
        db.session.add(existing)
        return existing, False

    element = archimate_backbone.create_backbone_element(
        element_type="WorkPackage",
        layer="Implementation",
        name=name,
        organization_id=org.id,
        provenance={_MARKER_KEY: marker, "source": "onboarding"},
    )
    work_package = UnifiedWorkPackage(
        name=name[:255],
        description=description,
        business_capability=business_capability_label[:100],
        archimate_element_id=element.id,
        layer="implementation",
        status="planned",
        source_type="manual",
        generation_method="onboarding",
        source_data=json.dumps({_MARKER_KEY: marker}),
        capability_ids=[capability.id] if capability is not None else None,
        capability_names=[capability.name] if capability is not None else None,
    )
    db.session.add(work_package)
    db.session.flush()
    return work_package, True


def _apply_transformation(org: Organization, answers: dict) -> dict:
    work_packages_created, work_packages_updated = 0, 0
    capabilities_created = 0
    for key in answers.get("transformation_templates") or []:
        template = reference_data.transformation_template(key)
        if template is None:
            continue
        category = template.get("category") or template["name"]
        capability_marker = f"tell_us_more:whats_changing:capability:{key}"
        capability, cap_created = _upsert_capability(org, marker=capability_marker, name=category, status="defined")
        capabilities_created += cap_created

        phases = template.get("phases") or [template["name"]]
        for index, phase in enumerate(phases):
            marker = f"tell_us_more:whats_changing:{key}:{index}"
            name = f"{template['name']} — {phase}"
            _, wp_created = _upsert_work_package(
                org,
                marker=marker,
                name=name,
                business_capability_label=category,
                description=template.get("description"),
                capability=capability,
            )
            work_packages_created += wp_created
            work_packages_updated += 0 if wp_created else 1

    other_text = answers.get("transformation_other")
    if other_text:
        marker = "tell_us_more:whats_changing:other"
        _, wp_created = _upsert_work_package(
            org,
            marker=marker,
            name=other_text[:255],
            business_capability_label="Other transformation",
            description=other_text,
            capability=None,
        )
        work_packages_created += wp_created
        work_packages_updated += 0 if wp_created else 1

    return {
        "capabilities_created": capabilities_created,
        "work_packages_created": work_packages_created,
        "work_packages_updated": work_packages_updated,
    }


# ---------------------------------------------------------------------------
# Implementation answers -> expected engineering capabilities and technology
# entries
# ---------------------------------------------------------------------------


def _split_stack_tokens(raw: str) -> list[str]:
    tokens = [t.strip(" .") for t in _TOKEN_SPLIT.split(raw) if t.strip(" .")]
    seen: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token.casefold() not in {t.casefold() for t in seen}:
            seen.append(token)
        if len(seen) >= _MAX_STACK_TOKENS:
            break
    return seen


def _upsert_technology_element(org: Organization, *, marker: str, name: str, element_type: str) -> tuple[ArchiMateElement, bool]:
    existing = _find_element_by_marker(org.id, marker, layer="technology")
    if existing is not None:
        existing.name = name[:100]
        db.session.add(existing)
        return existing, False
    element = archimate_backbone.create_backbone_element(
        element_type=element_type,
        layer="technology",
        name=name,
        organization_id=org.id,
        provenance={_MARKER_KEY: marker, "source": "onboarding"},
    )
    return element, True


def _apply_implementation(org: Organization, answers: dict) -> dict:
    capabilities_created = 0
    technology_created = 0

    implementation_type_key = answers.get("implementation_type")
    if implementation_type_key:
        impl_type = reference_data.implementation_type(implementation_type_key)
        if impl_type is not None:
            marker = f"tell_us_more:how_you_build:implementation_type:{implementation_type_key}"
            _, created = _upsert_capability(org, marker=marker, name=impl_type["title"], status="operational")
            capabilities_created += created

    if answers.get("has_dev_team") is True:
        marker = "tell_us_more:how_you_build:dev_team"
        _, created = _upsert_capability(org, marker=marker, name="Software engineering", status="operational")
        capabilities_created += created

    deployment_target_key = answers.get("deployment_target")
    if deployment_target_key:
        target = next(
            (t for t in reference_data.DEPLOYMENT_TARGETS if t["key"] == deployment_target_key), None
        )
        if target is not None:
            marker = f"tell_us_more:how_you_build:deployment:{deployment_target_key}"
            _, created = _upsert_capability(
                org, marker=marker, name=f"{target['label']} operations", status="operational"
            )
            capabilities_created += created

    for token in _split_stack_tokens(answers.get("stack") or ""):
        marker = f"tell_us_more:how_you_build:stack:{token.casefold()}"
        _, created = _upsert_technology_element(org, marker=marker, name=token, element_type="Node")
        technology_created += created

    for token in _split_stack_tokens(answers.get("integrations") or ""):
        marker = f"tell_us_more:how_you_build:integrations:{token.casefold()}"
        _, created = _upsert_technology_element(org, marker=marker, name=token, element_type="TechnologyService")
        technology_created += created

    return {"capabilities_created": capabilities_created, "technology_elements_created": technology_created}


_HANDLERS = {
    "compliance": _apply_compliance,
    "how_you_work": _apply_frameworks,
    "whats_changing": _apply_transformation,
    "how_you_build": _apply_implementation,
}
