"""The tools a company runs on, captured at onboarding, written to the real tables.

A tool is an ``ApplicationComponent`` for the organisation (its ArchiMate element is
created by the model's own listener). Which capabilities it supports is a
``UnifiedApplicationCapabilityMapping`` row, the store the rest of the product reads
for application-to-capability coverage. Nothing is kept on the side.

The mapping table's ``support_level`` column defaults to "partial". Onboarding does
not ask how well a tool supports a capability, so it does not set it and never
reports coverage beyond "this tool supports this capability".

Onboarding never deletes a tool and only removes a support link the founder
explicitly cleared.
"""
from __future__ import annotations

from flask import g

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability

from . import capabilities as capability_capture
from . import stage_gaps

_MAX_NAME = 256
DEPLOYMENTS = (
    {"key": "saas", "label": "Subscription service (SaaS)"},
    {"key": "cloud", "label": "Runs in the cloud, built by us"},
    {"key": "on_premise", "label": "Runs on our own machines"},
    {"key": "hybrid", "label": "A mix"},
    {"key": "mobile", "label": "Mobile app"},
)
_DEPLOYMENT_KEYS = {d["key"] for d in DEPLOYMENTS}


def _clean_name(value) -> str | None:
    text = (str(value) if value is not None else "").strip()
    return text[:_MAX_NAME] or None


def suggestions(stage: str) -> list[str]:
    """Tools typically expected at this stage (plain labels), for quick-add."""
    return [stage_gaps.label_for(k) for k in stage_gaps.expected_for_stage(stage).get("systems", [])]


def _unified_by_label(org_id: int) -> dict[str, UnifiedCapability]:
    rows = UnifiedCapability.query.filter_by(source_table="business_capability", organization_id=org_id).all()
    return {(r.name or "").strip().lower(): r for r in rows}


def read(stage: str, size_band: str, org_id: int) -> dict:
    unified = _unified_by_label(org_id)
    recorded = [
        {"key": c["key"], "label": c["label"], "unified_id": unified[c["label"].lower()].id}
        for c in capability_capture.catalogue_for(stage, size_band)
        if c["label"].lower() in unified
    ]
    key_by_unified = {c["unified_id"]: c["key"] for c in recorded}
    apps = ApplicationComponent.query.filter_by(organization_id=org_id).order_by(ApplicationComponent.id).all()
    links: dict[int, list[str]] = {}
    if apps:
        rows = UnifiedApplicationCapabilityMapping.query.filter(
            UnifiedApplicationCapabilityMapping.application_component_id.in_([a.id for a in apps])
        ).all()
        for row in rows:
            key = key_by_unified.get(row.unified_capability_id)
            if key:
                links.setdefault(row.application_component_id, []).append(key)
    return {
        "tools": [
            {
                "id": a.id,
                "name": a.name,
                "deployment": a.deployment_model if a.deployment_model in _DEPLOYMENT_KEYS else "",
                "supports": links.get(a.id, []),
            }
            for a in apps
        ],
        "capabilities": [{"key": c["key"], "label": c["label"]} for c in recorded],
        "deployments": list(DEPLOYMENTS),
        "suggestions": suggestions(stage),
    }


def save(entries: list[dict], *, stage: str, size_band: str, org_id: int | None = None) -> dict:
    """Create or update tools and their capability support links.

    Each entry is ``{"id"?, "name", "deployment"?, "supports": [capability keys]}``.
    ``supports`` lists the links that should exist for capabilities offered to this
    company and already recorded; a listed capability the tool no longer supports is
    removed. Returns ``{"tools": n, "links": n}``."""
    org_id = org_id or getattr(g, "current_org_id", None)
    unified = _unified_by_label(org_id)
    key_to_unified = {
        c["key"]: unified[c["label"].lower()]
        for c in capability_capture.catalogue_for(stage, size_band)
        if c["label"].lower() in unified
    }
    tools = links = 0
    for entry in entries or []:
        name = _clean_name((entry or {}).get("name"))
        if not name:
            continue
        app = None
        if entry.get("id"):
            app = db.session.get(ApplicationComponent, entry["id"])
            if app is not None and app.organization_id != org_id:
                app = None
        if app is None:
            app = ApplicationComponent.query.filter_by(organization_id=org_id, name=name).first()
        if app is None:
            app = ApplicationComponent(name=name, organization_id=org_id)
            db.session.add(app)
        app.name = name
        if entry.get("deployment") in _DEPLOYMENT_KEYS:
            app.deployment_model = entry["deployment"]
        db.session.flush()
        tools += 1

        wanted = {k for k in (entry.get("supports") or []) if k in key_to_unified}
        existing = {
            m.unified_capability_id: m
            for m in UnifiedApplicationCapabilityMapping.query.filter_by(application_component_id=app.id).all()
        }
        for key, capability in key_to_unified.items():
            row = existing.get(capability.id)
            if key in wanted and row is None:
                db.session.add(
                    UnifiedApplicationCapabilityMapping(
                        unified_capability_id=capability.id,
                        application_component_id=app.id,
                        relationship_type="supports",
                    )
                )
            elif key not in wanted and row is not None and "supports" in entry:
                db.session.delete(row)
        links += len(wanted)
    db.session.commit()
    return {"tools": tools, "links": links}
