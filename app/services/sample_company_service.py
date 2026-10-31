"""The sample company's working data, and its removal.

The OEF file loaded by ``/architecture/sample-company/load`` carries the
ArchiMate elements. A first screen built from those alone counts no
applications and cannot score health, so this adds what a real estate carries:
an application inventory with an owner, a lifecycle stage and a recorded annual
cost, the capabilities those applications support, a few open risks and two solutions in progress.

Rows are written the way the demonstration seed writes them
(``app/commands/seed_demo_company.py``): the same application, owner, cost and
capability-mapping columns, and the cost facts through the one cost accessor.
Every row added is recorded on the import's restore point, so removal undoes
the elements and these rows together, in this organisation only.
"""

from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from sqlalchemy.orm.attributes import flag_modified

from app import db

SAMPLE_MODEL_NAME = "Sample company"
# Written on every relationship and element the file brings in, so the first answer cites it.
SOURCE_LABEL = "Sample company"
_ROWS_KEY = "sample_rows"
_LOCK_KEY = 74510  # first half of the advisory-lock pair; the organisation id is the second

# (element name in the file or None, application name, lifecycle code,
#  component type, vendor, annual licence, monthly infrastructure,
#  capability name)
# Lifecycle codes, component types and vendor shapes follow the demo seed's
# application profile.
_APPLICATIONS = [
    ("Payments service", "Payments service", "2.1 strategic", "Business Application",
     "Stripe", 36000, 3200, "Process online payments"),
    ("Customer web app", "Customer web app", "2.1 strategic", "Business Application",
     "In-house", 12000, 4100, "Sell to customers online"),
    ("Order management system", "Order management system", "2.2 tactical", "Business Application",
     "SAP", 48000, 2800, "Track customer orders"),
    ("Inventory system", "Inventory system", "2.2 tactical", "Business Application",
     "Oracle", 30000, 1900, "Manage stock"),
    ("Notification service", "Notification service", "2.2 tactical", "Platform Service",
     "Twilio", 9000, 600, "Track customer orders"),
    (None, "Identity provider", "2.1 strategic", "Platform Service",
     "Okta", 18000, 400, "Sell to customers online"),
    (None, "Finance system", "2.2 tactical", "Business Application",
     "Oracle", 60000, 2400, "Process online payments"),
    (None, "Data warehouse", "2.1 strategic", "Data Platform",
     "Snowflake", 42000, 5200, "Manage stock"),
    (None, "Document management", "3. sunset", "Business Application",
     "OpenText", 15000, 900, None),
    (None, "Monitoring dashboard", "2.2 tactical", "Platform Service",
     "Datadog", 7200, 500, "Track customer orders"),
]

# The business area each sample application serves, as the Overview groups them.
_AREAS = {
    "Payments service": "Finance", "Customer web app": "Sales", "Order management system": "Operations",
    "Inventory system": "Operations", "Notification service": "Customer service", "Identity provider": "IT",
    "Finance system": "Finance", "Data warehouse": "IT", "Document management": "Corporate",
    "Monitoring dashboard": "IT",
}
# Vendor of an application the company builds itself: it has no contract with anyone.
_IN_HOUSE = "In-house"

# (name, the file's capability element or None, current maturity, target maturity)
_CAPABILITIES = [
    ("Process online payments", "Process online payments", 3, 4),
    ("Track customer orders", "Track customer orders", 3, 4),
    ("Sell to customers online", None, 4, 5),
    ("Manage stock", None, 2, 4),
    ("Run customer support", None, 2, 3),
]

# What kind of capability each is: selling differentiates this shop, taking payments and
# fulfilling orders run it, and support is what every shop needs.
_CAPABILITY_TYPES = {
    "Sell to customers online": "strategic",
    "Process online payments": "operational",
    "Track customer orders": "operational",
    "Manage stock": "operational",
    "Run customer support": "supporting",
}

# (title, likelihood, impact, owner role, mitigation, element name)
_RISKS = [
    ("Payments provider outage stops checkout", 3, 5,
     "Head of Engineering", "Add a second payment gateway and retry queued payments.",
     "Payments service"),
    ("Stock counts drift from the warehouse", 3, 3,
     "Operations Lead", "Reconcile stock against the warehouse count every night.",
     "Inventory system"),
    ("Order records held in one database", 2, 4,
     "Head of Engineering", "Take hourly backups and rehearse a restore each quarter.",
     "Orders database"),
    ("Customer notices sent twice after a retry", 2, 2,
     "Product Owner", "Record a send key per notice so a retry is ignored.",
     "Notification service"),
]


# (name, description, ADM phase, element name)
_SOLUTIONS = [
    ("Add a second payment gateway", "Take card payments through two providers so one outage does not stop checkout.",
     "C", "Payments service"),
    ("Real-time stock across channels", "Keep web and warehouse stock counts in step as orders arrive.",
     "B", "Inventory system"),
]


def _display_name(user) -> str:
    name = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p)
    return name or getattr(user, "email", "") or "Workspace owner"


def find_restore_point(org_id: int):
    """The newest un-restored import of the sample file in this organisation."""
    from app.services import import_snapshot_service

    for point in import_snapshot_service.restore_points(org_id):
        if (point.snapshot_data or {}).get("model_name") == SAMPLE_MODEL_NAME:
            return point
    return None


def _remember(log, rows: List[Dict[str, Any]], mirrors: List[int]) -> None:
    """Keep the added rows on the restore point, and the elements mirrored for them."""
    data = dict(log.snapshot_data or {})
    data[_ROWS_KEY] = rows
    data["created_element_ids"] = list(data.get("created_element_ids") or []) + mirrors
    log.snapshot_data = data
    flag_modified(log, "snapshot_data")


def _cite(org_id: int, mirrored_elements: List[int], relationships_before: set, recorded_at) -> None:
    """Give what the added rows brought into the model the file's own source.

    The mirrored elements and the links drawn from the capability mappings are
    written by the model's own sync, which records no origin; without this the
    first answer reads "Source not recorded" beside them.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    when = recorded_at or _dt.datetime.utcnow()
    db.session.flush()
    if mirrored_elements:
        for element in ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(mirrored_elements), ArchiMateElement.organization_id == org_id,
            ArchiMateElement.source_kind.is_(None),
        ):
            element.source_kind, element.source_label, element.source_recorded_at = "import", SOURCE_LABEL, when
    for rel in ArchiMateRelationship.query.filter(
        ArchiMateRelationship.organization_id == org_id, ArchiMateRelationship.source_kind.is_(None),
    ):
        if rel.id not in relationships_before:
            rel.source_kind, rel.source_label, rel.source_recorded_at = "import", SOURCE_LABEL, when


class LoadBusy(Exception):
    """Another load for this organisation did not finish within the wait."""


LOAD_WAIT_SECONDS = 30


@contextmanager
def one_load_at_a_time(org_id: int, wait_seconds: int | None = None):
    """Hold a database lock for ``org_id`` while its sample company loads.

    The lock lives on its own connection, not the request's session: the load commits
    part-way and a transaction-level lock would be let go at the first commit. Raises
    ``LoadBusy`` when the lock is not free within ``wait_seconds``.
    """
    wait_seconds = LOAD_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if db.engine.dialect.name != "postgresql":
        yield
        return
    conn = db.engine.connect()
    try:
        conn.execute(db.text("SELECT set_config('lock_timeout', :ms, true)"), {"ms": "%dms" % (wait_seconds * 1000)})
        try:
            conn.execute(db.text("SELECT pg_advisory_lock(:k, :o)"), {"k": _LOCK_KEY, "o": int(org_id)})
        except Exception as exc:
            conn.close()
            raise LoadBusy(str(exc)) from exc
        try:
            yield
        finally:
            conn.execute(db.text("SELECT pg_advisory_unlock(:k, :o)"), {"k": _LOCK_KEY, "o": int(org_id)})
    finally:
        conn.close()


def add_working_data(org_id: int, user, log) -> Dict[str, int]:
    """Add applications, owners, costs, capabilities and risks to ``org_id``.

    The route calls this once, straight after a fresh import of the file.
    Everything created is noted on ``log`` so ``remove`` can undo it.
    """

    from app import db
    from app.models.organization import Organization

    # Ensure the organisation has a reporting currency set (H-04: single currency source)
    org = db.session.get(Organization, org_id)
    if org and not org.reporting_currency:
        org.reporting_currency = "GBP"
        db.session.flush()

    from app.commands.seed_agent_charters import ensure_charters

    ensure_charters(org_id)
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_capabilities import BusinessCapability
    from app.models.risk import Risk, RiskStatus
    from app.models.solution_models import Solution
    from app.commands.project_capabilities import SOURCE_TABLE
    from app.models.unified_capability import UnifiedCapability
    from app.services.application_cost_accessor import state_contract, sync_cost_fact
    from app.services.capability_ownership_service import set_capability_owner

    elements = {
        e.name: e
        for e in ArchiMateElement.query.filter(ArchiMateElement.organization_id == org_id).all()
    }
    relationships_before = {
        rid for (rid,) in db.session.query(ArchiMateRelationship.id)
        .filter(ArchiMateRelationship.organization_id == org_id)
    }
    owner_name = _display_name(user)
    noted: List[Dict[str, Any]] = []
    mirrors: List[int] = []

    def note(table: str, row) -> None:
        noted.append({"table": table, "id": int(row.id)})

    capabilities: Dict[str, Any] = {}
    for index, (name, element_name, current, target) in enumerate(_CAPABILITIES, start=1):
        element = elements.get(element_name) if element_name else None
        if element is not None and element.type != "Capability":
            element = None
        row = BusinessCapability(
            name=name,
            code="SAMPLE-%d-CAP-%d" % (org_id, index),
            organization_id=org_id,
            archimate_element_id=element.id if element else None,
            business_owner=owner_name,
            level=1,
            current_maturity_level=current,
            target_maturity_level=target,
            maturity_assessment_date=_dt.datetime.utcnow(),
        )
        db.session.add(row)
        db.session.flush()
        capabilities[name] = row
        if element is None and row.archimate_element_id:
            mirrors.append(int(row.archimate_element_id))
        note("business_capability", row)
        # The capability's owner goes in the one ownership record, so an answer
        # that reaches this capability names the same person the capability page does.
        projected = UnifiedCapability.query.filter_by(
            organization_id=org_id, source_table=SOURCE_TABLE, source_id=str(row.id),
        ).first()
        if projected is not None:
            projected.capability_type = _CAPABILITY_TYPES[name]
            db.session.refresh(row)
            if projected.archimate_element_id is None and row.archimate_element_id:
                # The mirror element is created after the first projection; carry its id across.
                projected.archimate_element_id = row.archimate_element_id
            record = set_capability_owner(
                capability_id=projected.id, user_id=user.id, organization_id=org_id,
                assigned_by=user.id, replace_primary=True,
            )
            db.session.flush()
            note("application_owners", record)

    # One flush per kind of row, not one per row: every flush runs the model's change
    # listeners, which is where the load spent its time.
    applications = []
    unmirrored = set()
    for (element_name, name, lifecycle, component_type, vendor, licence, infra, capability) in _APPLICATIONS:
        element = elements.get(element_name) if element_name else None
        app = ApplicationComponent(
            name=name,
            organization_id=org_id,
            lifecycle_status=lifecycle,
            component_type=component_type,
            vendor_name=vendor,
            health_status="healthy",
            archimate_element_id=element.id if element else None,
            application_code="SAMPLE-%d-" % org_id + name.upper().replace(" ", "-")[:20],
            application_owner=owner_name,
            business_owner=owner_name,
            license_cost_annual=licence,
            infrastructure_cost_monthly=infra,
            total_cost_of_ownership=licence + 12 * infra,
            business_domain=_AREAS.get(name),
            business_criticality="high",
            strategic_importance="high",
        )
        db.session.add(app)
        applications.append((app, capability))
        if element is None:
            unmirrored.add(name)
    db.session.flush()

    mappings = []
    for app, capability in applications:
        if app.name in unmirrored and app.archimate_element_id:
            mirrors.append(int(app.archimate_element_id))
        note("application_components", app)
        for column in ("total_cost_of_ownership", "license_cost_annual", "infrastructure_cost_monthly"):
            sync_cost_fact(app, column)
        if app.vendor_name and app.vendor_name != _IN_HOUSE:
            # The Spend page reads contracts, so the application's cost is stated there too and the
            # run cost on the Overview, the Spend page and the canvas's cost box add up to one figure.
            note("vendor_contracts", state_contract(app, app.vendor_name, app.total_cost_of_ownership,
                                                    months_ago=3 + app.id % 8))
        if capability:
            mapping = ApplicationCapabilityMapping(
                organization_id=org_id,
                application_component_id=app.id,
                business_capability_id=capabilities[capability].id,
                support_level="full",
                coverage_percentage=80,
                is_primary_enabler=True,
                is_active=True,
            )
            db.session.add(mapping)
            mappings.append(mapping)

    # Setting the owner's name on an application records its ownership rows; add the
    # primary one only where that did not, and note every row each application has so
    # removal takes them all.
    owned = {}
    for owner in ApplicationOwner.query.filter(
        ApplicationOwner.application_id.in_([a.id for a, _ in applications])
    ).all():
        owned.setdefault(owner.application_id, []).append(owner)
    added = []
    for app, _capability in applications:
        rows = owned.setdefault(app.id, [])
        if not any(o.user_id == user.id and o.ownership_type == "primary" for o in rows):
            extra = ApplicationOwner(
                application_id=app.id, user_id=user.id, organization_id=org_id, ownership_type="primary",
            )
            db.session.add(extra)
            added.append(extra)
            rows.append(extra)
    db.session.flush()
    for mapping in mappings:
        note("application_capability_mapping", mapping)
    for app, _capability in applications:
        for owner in owned[app.id]:
            note("application_owners", owner)
    applications = [a for a, _ in applications]

    risks = []
    for title, likelihood, impact, owner_role, mitigation, element_name in _RISKS:
        element = elements.get(element_name)
        risk = Risk(
            title=title,
            description=mitigation,
            likelihood=likelihood,
            impact=impact,
            status=RiskStatus.OPEN,
            owner=owner_role,
            mitigation_plan=mitigation,
            organization_id=org_id,
            archimate_element_id=element.id if element else None,
        )
        db.session.add(risk)
        risks.append(risk)

    solutions = []
    for name, description, phase, element_name in _SOLUTIONS:
        element = elements.get(element_name)
        solution = Solution(
            name=name,
            description=description,
            adm_phase=phase,
            status="in_progress",
            solution_type="Service",
            organization_id=org_id,
            archimate_element_id=element.id if element else None,
        )
        db.session.add(solution)
        solutions.append(solution)
    db.session.flush()
    for risk in risks:
        note("risks", risk)
    for solution in solutions:
        note("solutions", solution)

    _cite(org_id, mirrors, relationships_before, log.started_at)
    _remember(log, noted, mirrors)
    return {"applications": len(applications), "capabilities": len(capabilities), "risks": len(_RISKS),
            "solutions": len(_SOLUTIONS)}


def remove(org_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Remove the sample company from ``org_id``: its added rows, then its elements.

    Returns the restore result, or ``None`` when no sample is loaded.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.business_capabilities import BusinessCapability
    from app.models.risk import Risk
    from app.models.solution_models import Solution
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.services import import_restore_service
    from app.services.application_cost_accessor import sync_cost_fact

    log = find_restore_point(org_id)
    if log is None:
        return None
    by_table: Dict[str, List[int]] = {}
    for item in (log.snapshot_data or {}).get(_ROWS_KEY, []):
        by_table.setdefault(item["table"], []).append(int(item["id"]))

    def rows(model, table):
        ids = by_table.get(table) or []
        if not ids:
            return []
        return model.query.filter(model.id.in_(ids), model.organization_id == org_id).all()

    # Each contract also stands as an element of the model; the restore removes it with the rest.
    contract_elements = [int(c.archimate_element_id) for c in rows(VendorContract, "vendor_contracts")
                         if c.archimate_element_id]
    if contract_elements:
        data = dict(log.snapshot_data or {})
        known = list(data.get("created_element_ids") or [])
        data["created_element_ids"] = known + [i for i in contract_elements if i not in known]
        log.snapshot_data = data
        flag_modified(log, "snapshot_data")
    for model, table in (
        (VendorContract, "vendor_contracts"),
        (ApplicationCapabilityMapping, "application_capability_mapping"),
        (ApplicationOwner, "application_owners"),
    ):
        for row in rows(model, table):
            db.session.delete(row)
    # The one store of "which application supports which capability" also holds a row for each
    # sample mapping, and one for every suggestion the person accepted on a sample application.
    sample_app_ids = [app.id for app in rows(ApplicationComponent, "application_components")]
    if sample_app_ids:
        UnifiedApplicationCapabilityMapping.query.filter(
            UnifiedApplicationCapabilityMapping.application_component_id.in_(sample_app_ids)
        ).delete(synchronize_session=False)
    for app in rows(ApplicationComponent, "application_components"):
        app.total_cost_of_ownership = None
        app.license_cost_annual = None
        app.infrastructure_cost_monthly = None
        for column in ("total_cost_of_ownership", "license_cost_annual", "infrastructure_cost_monthly"):
            sync_cost_fact(app, column)
    db.session.flush()
    for app in rows(ApplicationComponent, "application_components"):
        db.session.delete(app)
    for model, table in ((BusinessCapability, "business_capability"), (Risk, "risks"), (Solution, "solutions")):
        for row in rows(model, table):
            db.session.delete(row)
    db.session.flush()
    return import_restore_service.restore(org_id, user_id, log.id, [])


def work_out_connections(org_id: int) -> None:
    """Work out the indirect connections now, so the first answer is complete.

    Uses the one derivation runner; it also prunes what no longer applies, so it
    is run again after a removal.
    """
    from flask import current_app

    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    try:
        DerivationRunner().run_and_persist(org_id, trigger="on_demand")
    except Exception:
        # The answer then offers "Work them out now"; the load itself stands.
        db.session.rollback()
        current_app.logger.exception("sample company connections not worked out")
