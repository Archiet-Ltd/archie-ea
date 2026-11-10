"""The estate the figures gold set runs on: organisation A, and organisation B beside it.

Both organisations carry records with the same names (Orders Hub, Billing, Field Service
Management, ERP Upgrade, Phase 21 Rollout) and different figures, so a figure that crosses
from one to the other is always wrong. ``build_estate(org_id, variant)`` writes the records
for one organisation; ``EXPECTED`` holds the figures that estate yields, frozen: a change to
the records that moves a value fails ``test_gold_values_are_the_estate_values`` loudly.
"""

from __future__ import annotations

from typing import Any, Dict

# Per variant: dependants of each element, the applications, capabilities and the investments
# the scorer ranks. (A: Orders Hub has 13 dependants. B: 21.)
VARIANTS: Dict[str, Dict[str, Any]] = {
    "A": {
        "dependants": {"Orders Hub": 13, "Billing": 4, "Phase 21 Rollout": 6},
        "costs": {"ERP Upgrade": 470000, "Data Lake": 250000, "Field Service Management": 110000,
                  "Orders Hub": 90000},
        "owned_fillers": 8, "unowned_fillers": 5, "retired_fillers": 2,
        "capabilities": 45, "mapped": 18,
        "investments": [("ERP Upgrade", 80), ("Field Service Management", 72), ("Data Lake", 71.5),
                        ("Orders Hub", 66), ("Billing", 64), ("Customer Portal", 61), ("Payments Gateway", 58),
                        ("Warehouse Control", 55), ("Fleet Tracking", 52), ("Identity Platform", 49),
                        ("Reporting Hub", 44), ("Archive Store", 38), ("Legacy Mainframe", 31)],
    },
    "B": {
        "dependants": {"Orders Hub": 21, "Billing": 9, "Phase 21 Rollout": 3},
        "costs": {"ERP Upgrade": 380000, "Data Lake": 310000, "Field Service Management": 95000,
                  "Orders Hub": 120000},
        "owned_fillers": 6, "unowned_fillers": 2, "retired_fillers": 4,
        "capabilities": 30, "mapped": 20,
        "investments": [("Data Lake", 90), ("ERP Upgrade", 77), ("Orders Hub", 70), ("Billing", 62),
                        ("Field Service Management", 59), ("Customer Portal", 57), ("Payments Gateway", 54),
                        ("Warehouse Control", 50), ("Fleet Tracking", 47), ("Identity Platform", 45),
                        ("Reporting Hub", 41), ("Archive Store", 35), ("Legacy Mainframe", 29)],
    },
}
NAMED_APPLICATIONS = ("Orders Hub", "Billing", "Field Service Management", "ERP Upgrade", "Data Lake")
OWNERS = {"Billing": "Priya Shah"}

# What each estate yields, frozen. Read from the records when this file was built.
EXPECTED: Dict[str, Dict[str, Any]] = {
    "A": {"applications": 20, "unowned": 5, "retired": 2, "capabilities": 45, "mapped": 18, "gaps": 27,
          "coverage_percent": 40.0, "orders_hub": 13, "billing": 4, "phase_21": 6,
          "cost": {"ERP Upgrade": 470000, "Data Lake": 250000, "Field Service Management": 110000,
                   "Orders Hub": 90000, "Billing": None}},
    "B": {"applications": 17, "unowned": 2, "retired": 4, "capabilities": 30, "mapped": 20, "gaps": 10,
          "coverage_percent": 66.7, "orders_hub": 21, "billing": 9, "phase_21": 3,
          "cost": {"ERP Upgrade": 380000, "Data Lake": 310000, "Field Service Management": 95000,
                   "Orders Hub": 120000, "Billing": None}},
}


def build_estate(org_id: int, variant: str) -> Dict[str, Any]:
    """Write the estate of *variant* into organisation *org_id*; return the ids by name.

    Run inside an application context with the session free (the caller commits)."""
    from app.extensions import db
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization

    spec = VARIANTS[variant]
    organisation = db.session.get(Organization, org_id)
    organisation.reporting_currency = "GBP"
    ids: Dict[str, Any] = {"elements": {}, "applications": {}}

    for name, dependants in spec["dependants"].items():
        root = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
        db.session.add(root)
        db.session.flush()
        ids["elements"][name] = root.id
        for index in range(dependants):
            leaf = ArchiMateElement(name="%s Client %02d" % (name, index + 1), type="ApplicationComponent",
                                    layer="application", organization_id=org_id)
            db.session.add(leaf)
            db.session.flush()
            db.session.add(ArchiMateRelationship(source_id=root.id, target_id=leaf.id, type="Serving",
                                                 organization_id=org_id))

    applications = []
    for name in NAMED_APPLICATIONS:
        applications.append(ApplicationComponent(
            name=name, organization_id=org_id, total_cost_of_ownership=spec["costs"].get(name),
            legacy_business_owner=OWNERS.get(name, "Alex Quinn"),
        ))
    filler_total = spec["owned_fillers"] + spec["unowned_fillers"] + spec["retired_fillers"]
    for index in range(filler_total):
        owned = index < spec["owned_fillers"] + spec["retired_fillers"]
        retired = index < spec["retired_fillers"]
        applications.append(ApplicationComponent(
            name="Gold Filler %02d" % (index + 1), organization_id=org_id,
            legacy_business_owner="Alex Quinn" if owned else None,
            lifecycle_status="retired" if retired else "operational",
        ))
    db.session.add_all(applications)
    db.session.flush()
    for application in applications:
        ids["applications"][application.name] = application.id

    capabilities = []
    for index in range(spec["capabilities"]):
        capabilities.append(BusinessCapability(name="Gold Capability %02d" % (index + 1), organization_id=org_id))
    db.session.add_all(capabilities)
    db.session.flush()
    anchor = applications[0]
    for capability in capabilities[: spec["mapped"]]:
        db.session.add(ApplicationCapabilityMapping(
            organization_id=org_id, application_component_id=anchor.id, business_capability_id=capability.id,
        ))
    db.session.flush()
    return ids
