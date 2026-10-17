"""ArchiMate element addition routes for the Applications module.

Extracted from app/routes/unified_applications_routes.py lines 7619-8380.
16 POST-only routes that link ArchiMate elements (requirements, business processes,
business actors, services, roles, interfaces, data objects, technology nodes/devices,
system software, goals, drivers, work packages, deliverables, plateaus) to an
ApplicationComponent.
"""

import logging

from flask import current_app, flash, redirect, request, url_for
from flask_login import login_required
from app.decorators import WORK_PACKAGE_WRITE_ROLES, require_roles
from app.services import work_package_service

from app import db
from app.decorators import audit_log
from app.models.application_layer import (
    ApplicationInterface,
    ApplicationService,
    DataObject,
)
from app.models.application_portfolio import ApplicationComponent
from app.models.business_layer import (
    BusinessActor,
    BusinessRole,
    BusinessService,
)
from app.models.implementation_migration import (
    Plateau,
    WorkPackage,
)
from app.models.models import Requirement
from app.models.motivation import Driver, Goal
from app.models.process_data import BusinessProcess
from app.models.relationship_tables import (
    ApplicationBusinessActorMapping,
    ApplicationProcessSupport,
    DataObjectStorage,
)
from app.models.technology_layer import Device, Node, SystemSoftware

from . import unified_applications_bp

logger = logging.getLogger(__name__)


@unified_applications_bp.route("/<int:id>/requirements/add", methods=["POST"])
@login_required
@audit_log("element_link")
def application_requirement_add(id):
    """Link existing Requirement to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    requirement_id = request.form.get("element_id")

    if not requirement_id:
        flash("No requirement selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    requirement = Requirement.query.get(requirement_id)
    if not requirement:
        flash("Requirement not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if (
        requirement.application_component_id
        and requirement.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(requirement.application_component_id)
        flash(
            f'Requirement "{requirement.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif requirement.application_component_id == app.id:
        flash(
            f'Requirement "{requirement.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            requirement.application_component_id = app.id
            db.session.commit()
            flash(f'Requirement "{requirement.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking requirement {requirement_id} to app {id}: {e}"
            )
            flash("Error linking requirement. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#motivation"
    )


@unified_applications_bp.route("/<int:id>/business-processes/add", methods=["POST"])
@login_required
@audit_log("element_link")
def application_business_process_add(id):
    """Link existing Business Process to Application (many-to-many)"""
    app = ApplicationComponent.query.get_or_404(id)
    process_id = request.form.get("element_id")

    if not process_id:
        flash("No business process selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    process = BusinessProcess.query.get(process_id)
    if not process:
        flash("Business process not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if relationship already exists
    existing = ApplicationProcessSupport.query.filter_by(
        application_component_id=app.id, business_process_id=process.id
    ).first()

    if existing:
        flash(
            f'Business process "{process.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Create many-to-many relationship
            link = ApplicationProcessSupport(
                application_component_id=app.id,
                business_process_id=process.id,
                support_type="primary_execution",
                criticality="medium",
                is_active=True,
            )
            db.session.add(link)
            db.session.commit()
            flash(f'Business process "{process.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business process {process_id} to app {id}: {e}"
            )
            flash("Error linking business process. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#business"
    )


@unified_applications_bp.route("/<int:id>/business-actors/add", methods=["POST"])
@login_required
@audit_log("element_link")
def application_business_actor_add(id):
    """Link existing Business Actor to Application (many-to-many)"""
    app = ApplicationComponent.query.get_or_404(id)
    actor_id = request.form.get("element_id")

    if not actor_id:
        flash("No business actor selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    actor = BusinessActor.query.get(actor_id)
    if not actor:
        flash("Business actor not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if relationship already exists
    existing = ApplicationBusinessActorMapping.query.filter_by(
        application_component_id=app.id, business_actor_id=actor.id
    ).first()

    if existing:
        flash(
            f'Business actor "{actor.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Create many-to-many relationship
            link = ApplicationBusinessActorMapping(
                application_component_id=app.id,
                business_actor_id=actor.id,
                relationship_type="Primary User",
                usage_frequency="Daily",
            )
            db.session.add(link)
            db.session.commit()
            flash(f'Business actor "{actor.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business actor {actor_id} to app {id}: {e}"
            )
            flash("Error linking business actor. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#business"
    )


@unified_applications_bp.route("/<int:id>/business-services/add", methods=["POST"])
@login_required
@audit_log("element_link")
def application_business_service_add(id):
    """Link existing Business Service to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    service_id = request.form.get("element_id")

    if not service_id:
        flash("No business service selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    service = BusinessService.query.get(service_id)
    if not service:
        flash("Business service not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if service.application_component_id and service.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(service.application_component_id)
        flash(
            f'Business service "{service.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif service.application_component_id == app.id:
        flash(
            f'Business service "{service.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            service.application_component_id = app.id
            db.session.commit()
            flash(f'Business service "{service.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business service {service_id} to app {id}: {e}"
            )
            flash("Error linking business service. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#business"
    )


@unified_applications_bp.route("/<int:id>/business-roles/add", methods=["POST"])
@login_required
@audit_log("element_link")
def application_business_role_add(id):
    """Link existing Business Role to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    role_id = request.form.get("element_id")

    if not role_id:
        flash("No business role selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    role = BusinessRole.query.get(role_id)
    if not role:
        flash("Business role not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if role.application_component_id and role.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(role.application_component_id)
        flash(
            f'Business role "{role.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif role.application_component_id == app.id:
        flash(
            f'Business role "{role.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            role.application_component_id = app.id
            db.session.commit()
            flash(f'Business role "{role.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business role {role_id} to app {id}: {e}"
            )
            flash("Error linking business role. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#business"
    )


@unified_applications_bp.route("/<int:id>/interfaces/add", methods=["POST"])
@login_required
@audit_log("element_link")
def application_interface_add(id):
    """Link existing Application Interface to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    interface_id = request.form.get("element_id")

    if not interface_id:
        flash("No application interface selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    interface = ApplicationInterface.query.get(interface_id)
    if not interface:
        flash("Application interface not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if (
        interface.application_component_id
        and interface.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(interface.application_component_id)
        flash(
            f'Application interface "{interface.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif interface.application_component_id == app.id:
        flash(
            f'Application interface "{interface.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            interface.application_component_id = app.id
            db.session.commit()
            flash(
                f'Application interface "{interface.name}" linked successfully!',
                "success",
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking application interface {interface_id} to app {id}: {e}"
            )
            flash("Error linking application interface. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )


@unified_applications_bp.route("/<int:id>/services/add", methods=["POST"])
@login_required
@audit_log("element_link")
def application_service_add(id):
    """Link existing Application Service to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    service_id = request.form.get("element_id")

    if not service_id:
        flash("No application service selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    service = ApplicationService.query.get(service_id)
    if not service:
        flash("Application service not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if service.application_component_id and service.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(service.application_component_id)
        flash(
            f'Application service "{service.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif service.application_component_id == app.id:
        flash(
            f'Application service "{service.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            service.application_component_id = app.id
            db.session.commit()
            flash(
                f'Application service "{service.name}" linked successfully!', "success"
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking application service {service_id} to app {id}: {e}"
            )
            flash("Error linking application service. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )


@unified_applications_bp.route("/<int:id>/data-objects/add", methods=["POST"])
@login_required
@audit_log("element_link")
def data_object_add(id):
    """Link existing Data Object to Application (many-to-many)"""
    app = ApplicationComponent.query.get_or_404(id)
    object_id = request.form.get("element_id")

    if not object_id:
        flash("No data object selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    data_obj = DataObject.query.get(object_id)
    if not data_obj:
        flash("Data object not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if relationship already exists
    existing = DataObjectStorage.query.filter_by(
        business_object_id=data_obj.id, application_component_id=app.id
    ).first()

    if existing:
        flash(
            f'Data object "{data_obj.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Create many-to-many relationship
            link = DataObjectStorage(
                business_object_id=data_obj.id,
                application_component_id=app.id,
                is_master_source=False,
                storage_type="Database",
            )
            db.session.add(link)
            db.session.commit()
            flash(f'Data object "{data_obj.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking data object {object_id} to app {id}: {e}"
            )
            flash("Error linking data object. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )


@unified_applications_bp.route("/<int:id>/technology-nodes/add", methods=["POST"])
@login_required
@audit_log("element_link")
def technology_node_add(id):
    """Link existing Technology Node to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    node_id = request.form.get("element_id")

    if not node_id:
        flash("No technology node selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    node = Node.query.get(node_id)
    if not node:
        flash("Technology node not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if node.application_component_id and node.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(node.application_component_id)
        flash(
            f'Technology node "{node.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif node.application_component_id == app.id:
        flash(
            f'Technology node "{node.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            node.application_component_id = app.id
            db.session.commit()
            flash(f'Technology node "{node.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking technology node {node_id} to app {id}: {e}"
            )
            flash("Error linking technology node. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#technology"
    )


@unified_applications_bp.route("/<int:id>/technology-devices/add", methods=["POST"])
@login_required
@audit_log("element_link")
def technology_device_add(id):
    """Link existing Technology Device to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    device_id = request.form.get("element_id")

    if not device_id:
        flash("No technology device selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    device = Device.query.get(device_id)
    if not device:
        flash("Technology device not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if device.application_component_id and device.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(device.application_component_id)
        flash(
            f'Technology device "{device.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif device.application_component_id == app.id:
        flash(
            f'Technology device "{device.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            device.application_component_id = app.id
            db.session.commit()
            flash(f'Technology device "{device.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking technology device {device_id} to app {id}: {e}"
            )
            flash("Error linking technology device. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#technology"
    )


@unified_applications_bp.route("/<int:id>/system-software/add", methods=["POST"])
@login_required
@audit_log("element_link")
def system_software_add(id):
    """Link existing System Software to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    software_id = request.form.get("element_id")

    if not software_id:
        flash("No system software selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    software = SystemSoftware.query.get(software_id)
    if not software:
        flash("System software not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if (
        software.application_component_id
        and software.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(software.application_component_id)
        flash(
            f'System software "{software.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif software.application_component_id == app.id:
        flash(
            f'System software "{software.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            software.application_component_id = app.id
            db.session.commit()
            flash(f'System software "{software.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking system software {software_id} to app {id}: {e}"
            )
            flash("Error linking system software. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#technology"
    )


@unified_applications_bp.route("/<int:id>/goals/add", methods=["POST"])
@login_required
@audit_log("element_link")
def goal_add(id):
    """Link existing Goal to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    goal_id = request.form.get("element_id")

    if not goal_id:
        flash("No goal selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    goal = Goal.query.get(goal_id)
    if not goal:
        flash("Goal not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if goal.application_component_id and goal.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(goal.application_component_id)
        flash(
            f'Goal "{goal.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif goal.application_component_id == app.id:
        flash(f'Goal "{goal.name}" is already linked to this application', "warning")
    else:
        try:
            # Update direct FK
            goal.application_component_id = app.id
            db.session.commit()
            flash(f'Goal "{goal.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error linking goal {goal_id} to app {id}: {e}")
            flash("Error linking goal. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#strategy"
    )


@unified_applications_bp.route("/<int:id>/drivers/add", methods=["POST"])
@login_required
@audit_log("element_link")
def driver_add(id):
    """Link existing Driver to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    driver_id = request.form.get("element_id")

    if not driver_id:
        flash("No driver selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    driver = Driver.query.get(driver_id)
    if not driver:
        flash("Driver not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if driver.application_component_id and driver.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(driver.application_component_id)
        flash(
            f'Driver "{driver.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif driver.application_component_id == app.id:
        flash(
            f'Driver "{driver.name}" is already linked to this application', "warning"
        )
    else:
        try:
            # Update direct FK
            driver.application_component_id = app.id
            db.session.commit()
            flash(f'Driver "{driver.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking driver {driver_id} to app {id}: {e}"
            )
            flash("Error linking driver. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#strategy"
    )


@unified_applications_bp.route("/<int:id>/work-packages/add", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("element_link")
def work_package_add(id):
    """Link existing Work Package to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    work_package_id = request.form.get("element_id")

    if not work_package_id:
        flash("No work package selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    work_package = work_package_service.own_or_404(WorkPackage, work_package_id)

    # Check if already linked to another application
    if (
        work_package.application_component_id
        and work_package.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(
            work_package.application_component_id
        )
        flash(
            f'Work package "{work_package.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif work_package.application_component_id == app.id:
        flash(
            f'Work package "{work_package.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            work_package.application_component_id = app.id
            db.session.commit()
            flash(f'Work package "{work_package.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking work package {work_package_id} to app {id}: {e}"
            )
            flash("Error linking work package. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )


@unified_applications_bp.route("/<int:id>/deliverables/add", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("element_link")
def deliverable_add(id):
    """Link existing Deliverable to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    deliverable_id = request.form.get("element_id")

    if not deliverable_id:
        flash("No deliverable selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    deliverable = work_package_service.own_deliverable_or_404(deliverable_id)

    # Check if already linked to another application
    if (
        deliverable.application_component_id
        and deliverable.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(deliverable.application_component_id)
        flash(
            f'Deliverable "{deliverable.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif deliverable.application_component_id == app.id:
        flash(
            f'Deliverable "{deliverable.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            deliverable.application_component_id = app.id
            db.session.commit()
            flash(f'Deliverable "{deliverable.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking deliverable {deliverable_id} to app {id}: {e}"
            )
            flash("Error linking deliverable. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )


@unified_applications_bp.route("/<int:id>/plateaus/add", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("element_link")
def plateau_add(id):
    """Link existing Plateau to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    plateau_id = request.form.get("element_id")

    if not plateau_id:
        flash("No plateau selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    plateau = work_package_service.own_or_404(Plateau, plateau_id)

    # Check if already linked to another application
    if plateau.application_component_id and plateau.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(plateau.application_component_id)
        flash(
            f'Plateau "{plateau.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif plateau.application_component_id == app.id:
        flash(
            f'Plateau "{plateau.name}" is already linked to this application', "warning"
        )
    else:
        try:
            # Update direct FK
            plateau.application_component_id = app.id
            db.session.commit()
            flash(f'Plateau "{plateau.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking plateau {plateau_id} to app {id}: {e}"
            )
            flash("Error linking plateau. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )
