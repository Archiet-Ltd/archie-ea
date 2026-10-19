"""Interface Register service (SAP S/4HANA Interface Register, Task 02).

Owns the write path for the register: every interface is a real
ArchiMateElement (type='ApplicationInterface', layer='Application'),
extended 1:1 by ApplicationInterfaceMetadata, with SystemDependency and
ArchiMateRelationship rows recording the provider/consumer coupling.

Tenancy: ApplicationInterfaceMetadata and SystemDependency are NOT
TenantMixin. Every read is rooted at ArchiMateElement (which IS TenantMixin
and is filtered automatically by do_orm_execute) and joins outward. Neither
model is ever queried as the primary entity.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from app.models.implementation_migration import TechnologyRoadmapInitiative
from app.models.integration_metadata import ApplicationInterfaceMetadata, SystemDependency
from app.services.archimate_backbone import create_backbone_element

BUSINESS_CRITICALITY_VALUES = {"Critical", "High", "Medium", "Low"}
OPERATIONAL_STATUS_VALUES = {
    "planned",
    "development",
    "testing",
    "production",
    "deprecated",
    "retired",
}


class InterfaceRegisterError(ValueError):
    """A rejected create/update — the caller renders this as an inline 4xx error."""


def resolve_initiative(initiative_id: int, organization_id: int) -> TechnologyRoadmapInitiative:
    """Resolve a TechnologyRoadmapInitiative *through* its architecture_id,
    within the caller's tenant. Raises InterfaceRegisterError (404, not a
    silent "visible to everyone") when the initiative does not resolve, or
    its architecture_id is NULL.

    TechnologyRoadmapInitiative itself carries no organization_id, so this
    is the entire isolation argument for this feature (SDD §8.2): resolve
    through ArchitectureModel, which IS TenantMixin and is auto-filtered.
    """
    initiative = TechnologyRoadmapInitiative.query.filter_by(id=initiative_id).first()
    if initiative is None or initiative.architecture_id is None:
        raise InterfaceRegisterError("Initiative not found")

    architecture = ArchitectureModel.query.filter_by(id=initiative.architecture_id).first()
    if architecture is None:
        raise InterfaceRegisterError("Initiative not found")

    return initiative


def list_initiatives_for_org(organization_id: int) -> List[TechnologyRoadmapInitiative]:
    """Initiative picker, D2 fix: every prior version of this query read
    TechnologyRoadmapInitiative directly with no tenant filter (it carries no
    organization_id column) and no exclusion of architecture_id IS NULL rows
    (guaranteed 404s in the picker via resolve_initiative). Joins through
    ArchitectureModel, which IS TenantMixin and auto-filtered by
    do_orm_execute, so the join itself is the tenant boundary -- mirrors
    resolve_initiative's isolation argument (SDD Sec.8.2).
    """
    return (
        db.session.query(TechnologyRoadmapInitiative)
        .join(ArchitectureModel, TechnologyRoadmapInitiative.architecture_id == ArchitectureModel.id)
        .filter(TechnologyRoadmapInitiative.architecture_id.isnot(None))
        .order_by(TechnologyRoadmapInitiative.name)
        .all()
    )


def _interface_rows(architecture_id: Optional[int] = None):
    """Every ArchiMateElement of type ApplicationInterface in this tenant,
    outer-joined to its metadata (which may not exist yet -- an element with no
    metadata row still lists, US-1 AC3). Same element-rooted, tenant-filtered
    query the Elements page runs (architecture_crud_routes.list_elements),
    narrowed by ArchiMateElement.architecture_id when a model is given."""
    query = (
        db.session.query(ArchiMateElement, ApplicationInterfaceMetadata)
        .outerjoin(
            ApplicationInterfaceMetadata,
            ApplicationInterfaceMetadata.archimate_element_id == ArchiMateElement.id,
        )
        .filter(ArchiMateElement.type == "ApplicationInterface")
        .filter(ArchiMateElement.layer == "Application")
    )
    if architecture_id is not None:
        query = query.filter(ArchiMateElement.architecture_id == architecture_id)
    return query.order_by(ArchiMateElement.name).all()


def list_architectures_for_org() -> List[ArchitectureModel]:
    """Architecture models of the caller's tenant (ArchitectureModel is
    TenantMixin, so do_orm_execute scopes this)."""
    return ArchitectureModel.query.order_by(ArchitectureModel.name).all()


def resolve_architecture(architecture_id: int) -> ArchitectureModel:
    """An architecture model of the caller's tenant, or InterfaceRegisterError
    (404, never "visible to everyone")."""
    architecture = ArchitectureModel.query.filter_by(id=architecture_id).first()
    if architecture is None:
        raise InterfaceRegisterError("Architecture model not found")
    return architecture


def list_org_interfaces(organization_id: int, architecture_id: Optional[int] = None) -> List[Dict]:
    """Register list for the caller's organisation, optionally narrowed to one
    architecture model. No roadmap initiative is involved."""
    if architecture_id is not None:
        resolve_architecture(architecture_id)
    return [
        {"element": element, "metadata": metadata}
        for element, metadata in _interface_rows(architecture_id)
    ]


def list_interfaces(initiative_id: int, organization_id: int) -> List[Dict]:
    """Interfaces provenance-stamped with one roadmap initiative (used by the
    initiative-scoped comparison and costing screens)."""
    resolve_initiative(initiative_id, organization_id)

    results = []
    for element, metadata in _interface_rows():
        props = element.custom_properties or {}
        if props.get("initiative_id") != initiative_id:
            continue
        results.append({"element": element, "metadata": metadata})
    return results


def get_interface(element_id: int, organization_id: int):
    """A single interface (element + metadata, metadata may be None), rooted
    at ArchiMateElement so the tenant filter applies. Never .get()."""
    element = (
        ArchiMateElement.query.filter_by(id=element_id, type="ApplicationInterface")
        .first()
    )
    if element is None:
        return None
    metadata = ApplicationInterfaceMetadata.query.filter_by(
        archimate_element_id=element_id
    ).first()
    return {"element": element, "metadata": metadata}


def _resolve_component(component_id: Optional[int], field_label: str) -> Optional[ArchiMateElement]:
    """Resolve a picked ApplicationComponent to its ArchiMateElement.

    Rejects (InterfaceRegisterError) rather than writing a NULL-sided
    SystemDependency/ArchiMateRelationship row when the component has no
    archimate_element_id yet.
    """
    if not component_id:
        return None
    component = ApplicationComponent.query.filter_by(id=component_id).first()
    if component is None:
        raise InterfaceRegisterError(f"{field_label}: application not found")
    if not component.archimate_element_id:
        raise InterfaceRegisterError(
            f"{field_label}: this application has no ArchiMate element yet"
        )
    element = ArchiMateElement.query.filter_by(id=component.archimate_element_id).first()
    if element is None:
        raise InterfaceRegisterError(
            f"{field_label}: this application has no ArchiMate element yet"
        )
    return element


def resolve_current_provider_relationship(element: ArchiMateElement) -> Optional[ArchiMateRelationship]:
    """Resolve THIS module's current provider-side relationship for `element`.

    N1 fix (round 5): an interface can legitimately have several composition/
    serving relationships touching it -- drawn via the Composer, or written by
    another feature entirely -- so an unqualified
    ``ArchiMateRelationship.query.filter_by(target_id=..., type='composition').first()``
    can return a row this module never created, and both the compare-and-
    maybe-delete decision in update_interface() and the edit-form picker used
    to act on whatever ``.first()`` happened to return.

    Instead, resolve ownership through the SystemDependency row this module
    itself writes (create_interface/update_interface:
    interface_id=element.id, target_system_id=element.id,
    dependency_type="service" for the provider side), then fetch the exact
    ArchiMateRelationship by the (source_id, target_id, type) triple derived
    from it -- never an unqualified filter on element.id alone.

    Invariant: create_interface/update_interface never accumulate more than
    one provider-side SystemDependency row per interface (update_interface
    deletes the old one before adding a new one in the same transaction).
    If more than one is nonetheless found (hand-inserted data, a bug
    elsewhere), the most recently created row (highest id) is used as a
    documented, deterministic tiebreak -- not an unordered .first().
    """
    dependency = (
        SystemDependency.query.filter_by(
            target_system_id=element.id,
            interface_id=element.id,
            dependency_type="service",
        )
        .order_by(SystemDependency.id.desc())
        .first()
    )
    if dependency is None:
        return None
    return (
        ArchiMateRelationship.query.filter_by(
            source_id=dependency.source_system_id,
            target_id=element.id,
            type="composition",
        )
        .order_by(ArchiMateRelationship.id.desc())
        .first()
    )


def resolve_current_consumer_relationship(element: ArchiMateElement) -> Optional[ArchiMateRelationship]:
    """Consumer-side mirror of resolve_current_provider_relationship() -- see
    that docstring for the full N1 rationale. Resolves via the
    SystemDependency row this module writes with source_system_id=element.id,
    interface_id=element.id, dependency_type="service", then fetches the
    exact ArchiMateRelationship by the derived (source_id, target_id, type)
    triple rather than an unqualified filter_by(source_id=..., type=...)."""
    dependency = (
        SystemDependency.query.filter_by(
            source_system_id=element.id,
            interface_id=element.id,
            dependency_type="service",
        )
        .order_by(SystemDependency.id.desc())
        .first()
    )
    if dependency is None:
        return None
    return (
        ArchiMateRelationship.query.filter_by(
            source_id=element.id,
            target_id=dependency.target_system_id,
            type="serving",
        )
        .order_by(ArchiMateRelationship.id.desc())
        .first()
    )


def _validate_create_fields(form: Dict) -> Dict:
    name = (form.get("name") or "").strip()
    interface_type = (form.get("interface_type") or "").strip()
    protocol = (form.get("protocol") or "").strip()

    if not name:
        raise InterfaceRegisterError("Name is required")
    if not interface_type:
        raise InterfaceRegisterError("Interface type is required")
    if not protocol:
        raise InterfaceRegisterError("Protocol is required")

    business_criticality = (form.get("business_criticality") or "").strip() or None
    if business_criticality and business_criticality not in BUSINESS_CRITICALITY_VALUES:
        raise InterfaceRegisterError("Invalid business criticality")

    operational_status = (form.get("operational_status") or "planned").strip()
    if operational_status not in OPERATIONAL_STATUS_VALUES:
        raise InterfaceRegisterError("Invalid operational status")

    transaction_volume_daily = form.get("transaction_volume_daily")
    if transaction_volume_daily:
        try:
            transaction_volume_daily = int(transaction_volume_daily)
        except (TypeError, ValueError):
            raise InterfaceRegisterError("Transaction volume must be a number")
    else:
        transaction_volume_daily = None

    return {
        "name": name,
        "description": (form.get("description") or "").strip() or None,
        "interface_type": interface_type,
        "protocol": protocol,
        "data_format": (form.get("data_format") or "").strip() or None,
        "message_pattern": (form.get("message_pattern") or "").strip() or None,
        "is_synchronous": form.get("is_synchronous") == "sync",
        "authentication_method": (form.get("authentication_method") or "").strip() or None,
        "business_criticality": business_criticality,
        "transaction_volume_daily": transaction_volume_daily,
        "operational_status": operational_status,
    }


def create_interface(
    initiative_id: Optional[int],
    form: Dict,
    organization_id: int,
    architecture_id: Optional[int] = None,
) -> ArchiMateElement:
    """US-2: element -> metadata -> two SystemDependency rows -> ArchiMateRelationship
    rows, all in ONE transaction. Any failure rolls back leaving zero orphan rows.

    The register is scoped per architecture model; ``initiative_id`` is optional
    provenance for the initiative-scoped comparison screens."""
    if initiative_id is not None:
        resolve_initiative(initiative_id, organization_id)
    if architecture_id is not None:
        resolve_architecture(architecture_id)
    fields = _validate_create_fields(form)

    provider_id = form.get("provider_component_id") or None
    consumer_id = form.get("consumer_component_id") or None
    provider_id = int(provider_id) if provider_id else None
    consumer_id = int(consumer_id) if consumer_id else None

    provider_element = _resolve_component(provider_id, "Provider")
    consumer_element = _resolve_component(consumer_id, "Consumer")

    try:
        element = create_backbone_element(
            element_type="ApplicationInterface",
            layer="Application",
            name=fields["name"],
            description=fields["description"],
            organization_id=organization_id,
            provenance={
                "source_model": "InterfaceRegister",
                **({"initiative_id": initiative_id} if initiative_id is not None else {}),
            },
        )
        element.architecture_id = architecture_id

        metadata = ApplicationInterfaceMetadata(
            archimate_element_id=element.id,
            interface_type=fields["interface_type"],
            protocol=fields["protocol"],
            data_format=fields["data_format"],
            message_pattern=fields["message_pattern"],
            is_synchronous=fields["is_synchronous"],
            authentication_method=fields["authentication_method"],
            business_criticality=fields["business_criticality"],
            transaction_volume_daily=fields["transaction_volume_daily"],
            operational_status=fields["operational_status"],
        )
        db.session.add(metadata)

        if provider_element is not None:
            db.session.add(
                SystemDependency(
                    source_system_id=provider_element.id,
                    target_system_id=element.id,
                    dependency_type="service",
                    interface_id=element.id,
                )
            )
            db.session.add(
                ArchiMateRelationship(
                    type="composition",
                    source_id=provider_element.id,
                    target_id=element.id,
                )
            )

        if consumer_element is not None:
            db.session.add(
                SystemDependency(
                    source_system_id=element.id,
                    target_system_id=consumer_element.id,
                    dependency_type="service",
                    interface_id=element.id,
                )
            )
            db.session.add(
                ArchiMateRelationship(
                    type="serving",
                    source_id=element.id,
                    target_id=consumer_element.id,
                )
            )

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return element


def update_interface(element_id: int, form: Dict, organization_id: int) -> ArchiMateElement:
    """US-3: edit + retire. Creates the metadata row on first edit if absent.
    Sets retirement_date when operational_status='retired'. Replaces (not
    accumulates) relationships when source/target change."""
    from datetime import date

    element = ArchiMateElement.query.filter_by(
        id=element_id, type="ApplicationInterface"
    ).first()
    if element is None:
        raise InterfaceRegisterError("Interface not found")

    fields = _validate_create_fields(form)

    provider_id = form.get("provider_component_id") or None
    consumer_id = form.get("consumer_component_id") or None
    provider_id = int(provider_id) if provider_id else None
    consumer_id = int(consumer_id) if consumer_id else None

    provider_element = _resolve_component(provider_id, "Provider")
    consumer_element = _resolve_component(consumer_id, "Consumer")

    try:
        element.name = fields["name"]
        element.description = fields["description"]
        if "architecture_id" in form:
            raw_architecture = form.get("architecture_id") or None
            try:
                new_architecture_id = int(raw_architecture) if raw_architecture else None
            except ValueError:
                raise InterfaceRegisterError("Architecture model not found")
            if new_architecture_id is not None:
                resolve_architecture(new_architecture_id)
            element.architecture_id = new_architecture_id

        metadata = ApplicationInterfaceMetadata.query.filter_by(
            archimate_element_id=element_id
        ).first()
        if metadata is None:
            metadata = ApplicationInterfaceMetadata(archimate_element_id=element_id)
            db.session.add(metadata)

        metadata.interface_type = fields["interface_type"]
        metadata.protocol = fields["protocol"]
        metadata.data_format = fields["data_format"]
        metadata.message_pattern = fields["message_pattern"]
        metadata.is_synchronous = fields["is_synchronous"]
        metadata.authentication_method = fields["authentication_method"]
        metadata.business_criticality = fields["business_criticality"]
        metadata.transaction_volume_daily = fields["transaction_volume_daily"]
        metadata.operational_status = fields["operational_status"]

        if fields["operational_status"] == "retired":
            retirement_date = form.get("retirement_date") or None
            if retirement_date:
                try:
                    metadata.retirement_date = date.fromisoformat(retirement_date)
                except ValueError:
                    raise InterfaceRegisterError("Invalid retirement date")
            elif metadata.retirement_date is None:
                metadata.retirement_date = date.today()

        # Handle provider relationship - only update if provider has changed
        current_provider_rel = resolve_current_provider_relationship(element)

        current_provider_id = current_provider_rel.source_id if current_provider_rel else None
        new_provider_id = provider_element.id if provider_element else None

        if current_provider_id != new_provider_id:
            # Remove old relationships if they exist
            if current_provider_rel:
                # Delete the specific relationship
                db.session.delete(current_provider_rel)
                # Delete the corresponding SystemDependency
                SystemDependency.query.filter_by(
                    source_system_id=current_provider_id,
                    target_system_id=element.id,
                    interface_id=element.id
                ).delete(synchronize_session=False)
            
            # Add new relationships if a provider is specified
            if provider_element is not None:
                db.session.add(
                    SystemDependency(
                        source_system_id=provider_element.id,
                        target_system_id=element.id,
                        dependency_type="service",
                        interface_id=element.id,
                    )
                )
                db.session.add(
                    ArchiMateRelationship(
                        type="composition", 
                        source_id=provider_element.id, 
                        target_id=element.id
                    )
                )

        # Handle consumer relationship - only update if consumer has changed
        current_consumer_rel = resolve_current_consumer_relationship(element)

        current_consumer_id = current_consumer_rel.target_id if current_consumer_rel else None
        new_consumer_id = consumer_element.id if consumer_element else None

        if current_consumer_id != new_consumer_id:
            # Remove old relationships if they exist
            if current_consumer_rel:
                # Delete the specific relationship
                db.session.delete(current_consumer_rel)
                # Delete the corresponding SystemDependency
                SystemDependency.query.filter_by(
                    source_system_id=element.id,
                    target_system_id=current_consumer_id,
                    interface_id=element.id
                ).delete(synchronize_session=False)
            
            # Add new relationships if a consumer is specified
            if consumer_element is not None:
                db.session.add(
                    SystemDependency(
                        source_system_id=element.id,
                        target_system_id=consumer_element.id,
                        dependency_type="service",
                        interface_id=element.id,
                    )
                )
                db.session.add(
                    ArchiMateRelationship(
                        type="serving", 
                        source_id=element.id, 
                        target_id=consumer_element.id
                    )
                )

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return element
