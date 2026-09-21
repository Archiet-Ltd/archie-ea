"""

Application Factory Service

Creates ApplicationComponent instances from vendor product deployments.
Handles application metadata, configuration, and initial setup.
"""

import logging
import math
import re
from datetime import date, datetime
from decimal import Decimal

from flask import g, has_app_context
from sqlalchemy import inspect, select

from app.models.application_layer import ApplicationComponent
from app.models.application_portfolio import APPLICATION_HEALTH_STATUSES
from app.models.vendor.vendor_organization import VendorProduct

logger = logging.getLogger(__name__)

UNSET = object()
_OWNER_FIELDS = (
    "application_owner", "business_owner", "technical_owner", "technical_lead",
    "product_manager", "development_team", "support_team",
)


class ImportedApplicationAssessmentConflict(ValueError):
    """The persisted import authority or expected state no longer agrees."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise ImportedApplicationAssessmentConflict(code)


def _positive_id(value):
    return type(value) is int and value > 0


def _hex_string(value, length):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def _snapshot_value(value):
    """Encode only the scalar types present in the two mapped tables."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    if type(value) is list:
        return [_snapshot_value(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _snapshot_value(item) for key, item in value.items()}
    raise ValueError("unsupported_snapshot_value")


def _same_snapshot(left, right):
    # Python equality would otherwise accept True as 1 in IDs or JSON properties.
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(
            _same_snapshot(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _same_snapshot(a, b) for a, b in zip(left, right)
        )
    return left == right


def _complete_snapshot(value, table):
    if type(value) is not dict or set(value) != {column.name for column in table.columns}:
        raise ImportedApplicationAssessmentConflict("snapshot_conflict")
    try:
        encoded = _snapshot_value(value)
    except ValueError as exc:
        raise ImportedApplicationAssessmentConflict("snapshot_conflict") from exc
    _require(_same_snapshot(value, encoded), "snapshot_conflict")
    return value


def _row_snapshot(row, table):
    return {column.name: _snapshot_value(row[column.name]) for column in table.columns}


def _instance_snapshot(instance):
    mapper = inspect(type(instance))
    return {
        column.name: _snapshot_value(getattr(instance, mapper.get_property_by_column(column).key))
        for column in mapper.local_table.columns
    }


def _read_assessment_row(connection, table, organization_id, row_id, *, lock=False):
    query = select(*table.columns).where(
        table.c.organization_id == organization_id, table.c.id == row_id
    )
    if lock:
        query = query.with_for_update()
    return connection.execute(query).mappings().one_or_none()


def _health_value(value):
    return value is None or (type(value) is str and value in APPLICATION_HEALTH_STATUSES)


def _assessment_intent(settings, organization_id, token, key, attempt_id, element_id,
                       application_id, expected_snapshot, health, description, element_table):
    """Validate supplied operations against fresh, persisted authority only."""
    _require(type(settings) is dict, "reservation_mismatch")
    reservation = settings.get("a05_fixture")
    _require(type(reservation) is dict, "reservation_mismatch")
    _require(
        type(reservation.get("schema_version")) is int
        and reservation["schema_version"] == 1
        and _positive_id(reservation.get("organization_id"))
        and reservation["organization_id"] == organization_id
        and reservation.get("scenario") == "lantern-quay-v1"
        and reservation.get("reservation_token") == token
        and reservation.get("authorized_nonproduction") is True
        and _hex_string(reservation.get("catalog_digest"), 64)
        and _same_snapshot(reservation.get("permitted_sizes"), [300, 3000, 10000])
        and type(reservation.get("persona_accounts")) is list,
        "reservation_mismatch",
    )
    manifest = reservation.get("manifest")
    _require(type(manifest) is dict, "reservation_mismatch")
    _require(
        type(manifest.get("elements")) is dict
        and type(manifest.get("applications")) is dict
        and "health_checkpoint" in manifest
        and (manifest["health_checkpoint"] is None or type(manifest["health_checkpoint"]) is dict),
        "reservation_mismatch",
    )
    pending = manifest.get("pending")
    _require(type(pending) is dict, "intent_mismatch")
    _require(
        pending.get("phase") == "health"
        and pending.get("attempt_id") == attempt_id
        and pending.get("catalog_digest") == reservation["catalog_digest"]
        and type(pending.get("requested_size")) is int
        and pending["requested_size"] in reservation["permitted_sizes"]
        and type(pending.get("health")) is dict,
        "intent_mismatch",
    )
    element_entry = manifest["elements"].get(key)
    _require(type(element_entry) is dict, "identity_conflict")
    _require(
        _positive_id(element_entry.get("id")) and element_entry["id"] == element_id,
        "identity_conflict",
    )
    element_snapshot = _complete_snapshot(element_entry.get("snapshot"), element_table)
    _require(
        _same_snapshot(element_snapshot["id"], element_id)
        and _same_snapshot(element_snapshot["organization_id"], organization_id),
        "snapshot_conflict",
    )
    intent = pending["health"].get(key)
    _require(type(intent) is dict, "intent_mismatch")
    _require(
        {"element_id", "expected_application_id", "expected_snapshot", "desired"} <= intent.keys()
        and _same_snapshot(intent["element_id"], element_id)
        and _same_snapshot(intent["expected_application_id"], application_id),
        "intent_mismatch",
    )
    desired = intent["desired"]
    _require(type(desired) is dict and set(desired) == {"health", "description"}, "intent_mismatch")
    for field, value in (("health", health), ("description", description)):
        operation = {"operation": "preserve"} if value is UNSET else {"operation": "set", "value": value}
        _require(_same_snapshot(desired[field], operation), "intent_mismatch")
    applications = manifest["applications"]
    if application_id is None:
        _require(key not in applications, "identity_conflict")
        _require(expected_snapshot is None and intent["expected_snapshot"] is None, "snapshot_conflict")
    else:
        accepted = applications.get(key)
        _require(type(accepted) is dict, "identity_conflict")
        _require(
            _same_snapshot(accepted.get("id"), application_id)
            and _same_snapshot(accepted.get("element_id"), element_id),
            "identity_conflict",
        )
        table = ApplicationComponent.__table__
        for snapshot in (expected_snapshot, accepted.get("snapshot"), intent["expected_snapshot"]):
            _complete_snapshot(snapshot, table)
            _require(_same_snapshot(snapshot, expected_snapshot), "snapshot_conflict")
        _require(
            _same_snapshot(expected_snapshot["id"], application_id)
            and _same_snapshot(expected_snapshot["organization_id"], organization_id)
            and _same_snapshot(expected_snapshot["archimate_element_id"], element_id),
            "snapshot_conflict",
        )
    return element_snapshot


def _assessment_candidates(connection, element_table, organization_id, element_id,
                           name, source_identifier, application_id):
    from app.utils.duplicate_guard import normalize_name

    normalized = normalize_name(name)
    _require(bool(normalized), "identity_conflict")
    elements = connection.execute(select(element_table.c.id, element_table.c.name).where(
        element_table.c.organization_id == organization_id,
        element_table.c.type == "ApplicationComponent",
    ))
    _require(
        {row.id for row in elements if normalize_name(row.name) == normalized} == {element_id},
        "identity_conflict",
    )
    table = ApplicationComponent.__table__
    expected = set() if application_id is None else {application_id}
    names = connection.execute(select(table.c.id, table.c.name).where(
        table.c.organization_id == organization_id
    ))
    _require({row.id for row in names if normalize_name(row.name) == normalized} == expected,
             "identity_conflict")
    for column, value in ((table.c.source_identifier, source_identifier),
                          (table.c.archimate_element_id, element_id)):
        ids = set(connection.execute(select(table.c.id).where(
            table.c.organization_id == organization_id, column == value
        )).scalars())
        _require(ids == expected, "identity_conflict")
    # An exact-link existence audit also catches malformed foreign/NULL tenant FKs.
    other_link = select(table.c.id).where(table.c.archimate_element_id == element_id)
    if application_id is not None:
        other_link = other_link.where(table.c.id != application_id)
    _require(not connection.execute(select(other_link.exists())).scalar_one(), "identity_conflict")


def _assessment_unowned(connection, row, owner_table):
    _require(all(row[field] is None for field in _OWNER_FIELDS), "owner_conflict")
    # Ownership has no tenant mixin; any owner of this exact ID blocks mutation.
    exists = select(owner_table.c.id).where(owner_table.c.application_id == row["id"]).exists()
    _require(not connection.execute(select(exists)).scalar_one(), "owner_conflict")


class ApplicationFactory:
    """Factory for creating ApplicationComponent instances from vendor products."""

    @staticmethod
    def upsert_imported_application_assessment(
        *, organization_id, reservation_token, fixture_key, attempt_id,
        archimate_element_id, application_id=None, expected_snapshot=None,
        health_status=UNSET, description=UNSET,
    ):
        """Flush an authorized assessment; the caller owns commit and rollback.

        The caller commits import intent first, holds the refresh and recompute
        job locks outside removing tenant scopes, then commits all assessments
        and their complete manifest checkpoint together. Nonparticipating writers
        require an exclusive edit window: the imported link is not unique in SQL.
        """
        from app import db
        from app.models.application_owner import ApplicationOwner
        from app.models.archimate_core import ArchiMateElement
        from app.models.organization import Organization
        from app.utils.duplicate_guard import lock_name_for_write

        if not _positive_id(organization_id) or not _positive_id(archimate_element_id):
            raise ValueError("invalid_id")
        if application_id is not None and not _positive_id(application_id):
            raise ValueError("invalid_id")
        if not has_app_context() or not _positive_id(getattr(g, "current_org_id", None)):
            raise ValueError("invalid_tenant_context")
        if g.current_org_id != organization_id:
            raise ValueError("invalid_tenant_context")
        if not _hex_string(reservation_token, 32) or not _hex_string(attempt_id, 32):
            raise ValueError("invalid_token")
        if not isinstance(fixture_key, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,120}", fixture_key) is None:
            raise ValueError("invalid_fixture_key")
        source_identifier = f"a05:v1:{organization_id}:{reservation_token}:{fixture_key}"
        if len(source_identifier) > 255:
            raise ValueError("invalid_source_identifier")
        if health_status is not UNSET and not _health_value(health_status):
            raise ValueError("invalid_health_status")
        if description is not UNSET and (type(description) is not str or not description.strip()):
            raise ValueError("invalid_description")
        if application_id is None and (health_status is UNSET or description is UNSET):
            raise ValueError("missing_assessment_input")

        session = db.session()
        element_table = ArchiMateElement.__table__
        table = ApplicationComponent.__table__
        owner_table = ApplicationOwner.__table__
        with session.no_autoflush:
            pending_objects = set(session.new) | set(session.dirty) | set(session.deleted)
            if any(isinstance(obj, (Organization, ArchiMateElement, ApplicationComponent, ApplicationOwner))
                   for obj in pending_objects):
                raise ImportedApplicationAssessmentConflict("snapshot_conflict")
            connection = session.connection()
            if connection.dialect.name != "postgresql" or connection.get_isolation_level() != "READ COMMITTED":
                raise RuntimeError("unsupported_assessment_transaction")
            org_table = Organization.__table__
            organization = connection.execute(select(org_table.c.is_active, org_table.c.settings).where(
                org_table.c.id == organization_id
            ).with_for_update()).mappings().one_or_none()
            _require(organization is not None and organization["is_active"] is True, "reservation_mismatch")
            element_snapshot = _assessment_intent(
                organization["settings"], organization_id, reservation_token, fixture_key, attempt_id,
                archimate_element_id, application_id, expected_snapshot, health_status, description,
                element_table,
            )
            target = _read_assessment_row(connection, element_table, organization_id, archimate_element_id, lock=True)
            _require(target is not None and target["deleted_at"] is None, "target_unavailable")
            _require(target["type"] == "ApplicationComponent", "target_type")
            _require(target["application_component_id"] is None, "identity_conflict")
            properties = target["custom_properties"]
            _require(
                type(properties) is dict
                and properties.get("a05:scenario") == "lantern-quay-v1"
                and properties.get("a05:reservation") == reservation_token
                and properties.get("a05:key") == fixture_key
                and type(properties.get("archie:imported_at")) is str
                and bool(properties["archie:imported_at"]),
                "identity_conflict",
            )
            _require(_same_snapshot(_row_snapshot(target, element_table), element_snapshot), "snapshot_conflict")
            if lock_name_for_write(ApplicationComponent, target["name"], organization_id=organization_id) is not True:
                raise RuntimeError("lock_unavailable")
            _assessment_candidates(connection, element_table, organization_id, archimate_element_id,
                                   target["name"], source_identifier, application_id)
            outcome = "created"
            before = None
            if application_id is None:
                application = ApplicationComponent(
                    name=target["name"], description=description, organization_id=organization_id,
                    archimate_element_id=archimate_element_id, health_status=health_status,
                    data_source="a05_fixture", source_identifier=source_identifier,
                )
                session.add(application)
            else:
                before = _read_assessment_row(connection, table, organization_id, application_id, lock=True)
                _require(before is not None and before["deleted_at"] is None, "identity_conflict")
                _assessment_unowned(connection, before, owner_table)
                _require(
                    before["name"] == target["name"]
                    and before["archimate_element_id"] == archimate_element_id
                    and before["data_source"] == "a05_fixture"
                    and before["source_identifier"] == source_identifier,
                    "identity_conflict",
                )
                _require(_health_value(before["health_status"]), "snapshot_conflict")
                _require(_same_snapshot(_row_snapshot(before, table), expected_snapshot), "snapshot_conflict")
                application = session.execute(select(ApplicationComponent).where(
                    ApplicationComponent.organization_id == organization_id,
                    ApplicationComponent.id == application_id,
                ).execution_options(populate_existing=True)).scalar_one()
                _require(_same_snapshot(_instance_snapshot(application), expected_snapshot), "snapshot_conflict")
                outcome = "unchanged"
                for field, value in (("health_status", health_status), ("description", description)):
                    if value is not UNSET and not _same_snapshot(before[field], value):
                        setattr(application, field, value)
                        outcome = "updated"
            session.flush()
            row = _read_assessment_row(connection, table, organization_id, application.id)
            if row is None:
                raise RuntimeError("assessment_readback_mismatch")
            snapshot = _row_snapshot(row, table)
            wanted_health = before["health_status"] if health_status is UNSET else health_status
            wanted_description = before["description"] if description is UNSET else description
            invariants = {
                "id": application.id, "organization_id": organization_id,
                "archimate_element_id": archimate_element_id, "name": target["name"],
                "health_status": wanted_health, "description": wanted_description,
                "data_source": "a05_fixture", "source_identifier": source_identifier,
                "deleted_at": None,
            }
            if (not _same_snapshot(snapshot, _instance_snapshot(application))
                    or any(not _same_snapshot(snapshot[key], value) for key, value in invariants.items())):
                raise RuntimeError("assessment_readback_mismatch")
            if before is not None:
                allowed = {"health_status", "description", "updated_at", "lock_version"} if outcome == "updated" else set()
                if any(not _same_snapshot(snapshot[key], expected_snapshot[key]) for key in snapshot if key not in allowed):
                    raise RuntimeError("assessment_readback_mismatch")
            target_after = _read_assessment_row(connection, element_table, organization_id, archimate_element_id)
            if target_after is None or not _same_snapshot(_row_snapshot(target_after, element_table), element_snapshot):
                raise RuntimeError("assessment_readback_mismatch")
            try:
                _assessment_candidates(connection, element_table, organization_id, archimate_element_id,
                                       target["name"], source_identifier, application.id)
                _assessment_unowned(connection, row, owner_table)
            except ImportedApplicationAssessmentConflict as exc:
                raise RuntimeError("assessment_readback_mismatch") from exc
            return {
                "application_id": application.id, "archimate_element_id": archimate_element_id,
                "organization_id": organization_id, "fixture_key": fixture_key,
                "health_status": snapshot["health_status"], "data_source": snapshot["data_source"],
                "source_identifier": source_identifier, "outcome": outcome, "snapshot": snapshot,
            }

    @staticmethod
    def update_application_with_archimate_link(application_id, archimate_element_id):
        """
        Update application with ArchiMate element link.

        Args:
            application_id: ID of the application component
            archimate_element_id: ID of the main ArchiMate element
        """
        try:
            application = ApplicationComponent.query.get(application_id)
            if not application:
                raise ValueError(f"Application {application_id} not found")

            application.archimate_element_id = archimate_element_id
            application.updated_at = datetime.utcnow()

            logger.info(
                f"Linked application {application_id} to ArchiMate element {archimate_element_id}"
            )

        except Exception as e:
            logger.error(f"Error linking application to ArchiMate element: {str(e)}")
            raise e

    @staticmethod
    def get_deployment_summary(application_id):
        """
        Get deployment summary for an application.

        Args:
            application_id: ID of the application

        Returns:
            dict: Deployment summary information
        """
        try:
            application = ApplicationComponent.query.get(application_id)
            if not application:
                return None

            # Get vendor product information
            vendor_product = None
            if application.vendor_product_id:
                vendor_product = VendorProduct.query.get(application.vendor_product_id)

            # Count ArchiMate elements
            element_count = 0
            if application.archimate_element_id:
                from app.models.models import ArchiMateElement

                element_count = ArchiMateElement.query.filter_by(
                    application_component_id=application_id
                ).count()

            # Get deployment type from metadata
            deployment_type = "primary_system"  # Default
            if application.metadata and isinstance(application.metadata, dict):
                deployment_type = application.metadata.get("deployment_type", "primary_system")

            summary = {
                "application_id": application.id,
                "application_name": application.name,
                "deployment_status": application.deployment_status,
                "deployment_type": deployment_type,  # Get from metadata
                "criticality": application.business_criticality,
                "hosting_model": application.deployment_model,
                "business_owner": application.business_owner,
                "created_at": application.created_at.isoformat()
                if application.created_at
                else None,
                "updated_at": application.updated_at.isoformat()
                if application.updated_at
                else None,
                "archimate_elements_count": element_count,
                "has_archimate_link": bool(application.archimate_element_id),
                "vendor_product": {
                    "id": vendor_product.id if vendor_product else None,
                    "name": vendor_product.name if vendor_product else None,
                    "category": vendor_product.product_type if vendor_product else None,
                    "vendor": {
                        "id": vendor_product.vendor_organization.id if vendor_product else None,
                        "name": vendor_product.vendor_organization.name if vendor_product else None,
                    }
                    if vendor_product
                    else None,
                },
                "metadata": dict(application.metadata) if application.metadata else {},
            }

            return summary

        except Exception as e:
            logger.error(f"Error getting deployment summary: {str(e)}")
            return None


def create_vendor_deployment(vendor_product_id, deployment_config):
    """
    Complete deployment process: create application and link to vendor product.

    Args:
        vendor_product_id: ID of the vendor product to deploy
        deployment_config: Deployment configuration

    Returns:
        dict: Deployment result with application and summary
    """
    try:
        # Get vendor product
        vendor_product = VendorProduct.query.get(vendor_product_id)
        if not vendor_product:
            raise ValueError(f"Vendor product {vendor_product_id} not found")

        # Validate configuration
        is_valid, errors = ApplicationFactory.validate_deployment_config(deployment_config)
        if not is_valid:
            raise ValueError(f"Invalid deployment configuration: {', '.join(errors)}")

        # Create application
        application = ApplicationFactory.create_application_from_vendor_product(
            vendor_product, deployment_config
        )

        # Get deployment summary
        summary = ApplicationFactory.get_deployment_summary(application.id)

        logger.info(f"Successfully created deployment: {application.name}")

        return {
            "success": True,
            "application_id": application.id,
            "application_name": application.name,
            "summary": summary,
        }

    except Exception as e:
        logger.error(f"Error in vendor deployment: {str(e)}")
        raise e
