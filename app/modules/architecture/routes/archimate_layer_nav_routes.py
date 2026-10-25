"""ArchiMate Layer Navigation Routes - North Star Phase 2

Provides layer-specific navigation entry points that filter the ArchiMate
element browser (``archimate_crud``) by layer and element type. Every route
here backs one entry of the "By Layer" section of
``components/admin_sidebar_northstar_phase2.html``.

These used to redirect to ``archimate.composer_page`` — the diagram editor —
passing ``layer`` and ``element_type``. ``composer_page`` reads only
``solution_id`` and ``viewpoint``, so both filters fell into the query string
and were ignored: all ~50 links landed on the same unfiltered canvas, and the
whole section was a no-op that looked like navigation. They now redirect to the
element browser, which lists elements and honours both filters.

The ``element_type`` handed over is the ArchiMate 3.2 type name as spelled in
``archimate_crud.routes.MODEL_REGISTRY`` (``BusinessActor``, not
``business_actor``) — that registry is what the browser filters on, so a
lower-case slug would match nothing.

Routes:
- /architecture/motivation/*     → Motivation Layer elements
- /architecture/strategy/*       → Strategy Layer elements
- /architecture/business/*       → Business Layer elements
- /architecture/application/*    → Application Layer elements
- /architecture/technology/*     → Technology Layer elements
- /architecture/physical/*       → Physical Layer elements
- /architecture/implementation/* → Implementation & Migration Layer elements
"""

from flask import Blueprint, redirect, url_for
from flask_login import login_required

archimate_layer_nav_bp = Blueprint('archimate_layers', __name__, url_prefix='/architecture')


def _browse(layer, element_type):
    """Redirect to the element browser, pre-filtered to one layer and type.

    The browser re-validates both values against its own registry and drops
    anything it does not recognise, so this never has to guess.
    """
    return redirect(
        url_for('archimate_crud.dashboard', layer=layer, element_type=element_type)
    )


# ============================================================================
# MOTIVATION LAYER (9 element types)
# ============================================================================

@archimate_layer_nav_bp.route('/motivation/stakeholders')
@login_required
def motivation_stakeholders():
    """Navigate to Stakeholders (Motivation Layer)."""
    return _browse('motivation', 'Stakeholder')

@archimate_layer_nav_bp.route('/motivation/drivers')
@login_required
def motivation_drivers():
    """Navigate to Drivers (Motivation Layer)."""
    return _browse('motivation', 'Driver')

@archimate_layer_nav_bp.route('/motivation/assessments')
@login_required
def motivation_assessments():
    """Navigate to Assessments (Motivation Layer)."""
    return _browse('motivation', 'Assessment')

@archimate_layer_nav_bp.route('/motivation/goals')
@login_required
def motivation_goals():
    """Navigate to Goals (Motivation Layer)."""
    return _browse('motivation', 'Goal')

@archimate_layer_nav_bp.route('/motivation/outcomes')
@login_required
def motivation_outcomes():
    """Navigate to Outcomes (Motivation Layer)."""
    return _browse('motivation', 'Outcome')

@archimate_layer_nav_bp.route('/motivation/principles')
@login_required
def motivation_principles():
    """Navigate to Principles (Motivation Layer)."""
    return _browse('motivation', 'Principle')

@archimate_layer_nav_bp.route('/motivation/requirements')
@login_required
def motivation_requirements():
    """Navigate to Requirements (Motivation Layer)."""
    return _browse('motivation', 'Requirement')

@archimate_layer_nav_bp.route('/motivation/constraints')
@login_required
def motivation_constraints():
    """Navigate to Constraints (Motivation Layer)."""
    return _browse('motivation', 'Constraint')

@archimate_layer_nav_bp.route('/motivation/meanings')
@login_required
def motivation_meanings():
    """Navigate to Meanings (Motivation Layer)."""
    return _browse('motivation', 'Meaning')


# ============================================================================
# STRATEGY LAYER (4 element types)
# ============================================================================

@archimate_layer_nav_bp.route('/strategy/capabilities/tree')
@login_required
def strategy_capabilities_tree():
    """Navigate to Capabilities tree view - redirects to existing capability map."""
    return redirect(url_for('capability_map.index', view='tree'))

@archimate_layer_nav_bp.route('/strategy/resources')
@login_required
def strategy_resources():
    """Navigate to Resources (Strategy Layer)."""
    return _browse('strategy', 'Resource')

@archimate_layer_nav_bp.route('/strategy/value-streams')
@login_required
def strategy_value_streams():
    """Navigate to Value Streams (Strategy Layer)."""
    return _browse('strategy', 'ValueStream')

@archimate_layer_nav_bp.route('/strategy/courses-of-action')
@login_required
def strategy_courses_of_action():
    """Navigate to Courses of Action (Strategy Layer)."""
    return _browse('strategy', 'CourseOfAction')


# ============================================================================
# BUSINESS LAYER (13 element types)
# ============================================================================

@archimate_layer_nav_bp.route('/business/actors')
@login_required
def business_actors():
    """Navigate to Business Actors."""
    return _browse('business', 'BusinessActor')

@archimate_layer_nav_bp.route('/business/roles')
@login_required
def business_roles():
    """Navigate to Business Roles."""
    return _browse('business', 'BusinessRole')

@archimate_layer_nav_bp.route('/business/collaborations')
@login_required
def business_collaborations():
    """Navigate to Business Collaborations."""
    return _browse('business', 'BusinessCollaboration')

@archimate_layer_nav_bp.route('/business/interfaces')
@login_required
def business_interfaces():
    """Navigate to Business Interfaces."""
    return _browse('business', 'BusinessInterface')

@archimate_layer_nav_bp.route('/business/processes')
@login_required
def business_processes():
    """Navigate to Business Processes: the list, with its Diagram view."""
    return redirect(url_for('business_architecture.processes'))

@archimate_layer_nav_bp.route('/business/functions')
@login_required
def business_functions():
    """Navigate to Business Functions."""
    return _browse('business', 'BusinessFunction')

@archimate_layer_nav_bp.route('/business/interactions')
@login_required
def business_interactions():
    """Navigate to Business Interactions."""
    return _browse('business', 'BusinessInteraction')

@archimate_layer_nav_bp.route('/business/events')
@login_required
def business_events():
    """Navigate to Business Events."""
    return _browse('business', 'BusinessEvent')

@archimate_layer_nav_bp.route('/business/services')
@login_required
def business_services():
    """Navigate to Business Services."""
    return _browse('business', 'BusinessService')

@archimate_layer_nav_bp.route('/business/objects')
@login_required
def business_objects():
    """Navigate to Business Objects."""
    return _browse('business', 'BusinessObject')

@archimate_layer_nav_bp.route('/business/contracts')
@login_required
def business_contracts():
    """Navigate to Contracts."""
    return _browse('business', 'Contract')

@archimate_layer_nav_bp.route('/business/representations')
@login_required
def business_representations():
    """Navigate to Representations."""
    return _browse('business', 'Representation')

@archimate_layer_nav_bp.route('/business/products')
@login_required
def business_products():
    """Navigate to Products."""
    return _browse('business', 'Product')


# ============================================================================
# APPLICATION LAYER (9 element types)
# ============================================================================

@archimate_layer_nav_bp.route('/application/components')
@login_required
def application_components():
    """Navigate to Application Components."""
    return _browse('application', 'ApplicationComponent')

@archimate_layer_nav_bp.route('/application/collaborations')
@login_required
def application_collaborations():
    """Navigate to Application Collaborations."""
    return _browse('application', 'ApplicationCollaboration')

@archimate_layer_nav_bp.route('/application/interfaces')
@login_required
def application_interfaces():
    """Navigate to Application Interfaces."""
    return _browse('application', 'ApplicationInterface')

@archimate_layer_nav_bp.route('/application/functions')
@login_required
def application_functions():
    """Navigate to Application Functions."""
    return _browse('application', 'ApplicationFunction')

@archimate_layer_nav_bp.route('/application/interactions')
@login_required
def application_interactions():
    """Navigate to Application Interactions."""
    return _browse('application', 'ApplicationInteraction')

@archimate_layer_nav_bp.route('/application/processes')
@login_required
def application_processes():
    """Navigate to Application Processes."""
    return _browse('application', 'ApplicationProcess')

@archimate_layer_nav_bp.route('/application/events')
@login_required
def application_events():
    """Navigate to Application Events."""
    return _browse('application', 'ApplicationEvent')

@archimate_layer_nav_bp.route('/application/services')
@login_required
def application_services():
    """Navigate to Application Services."""
    return _browse('application', 'ApplicationService')

@archimate_layer_nav_bp.route('/application/data-objects')
@login_required
def application_data_objects():
    """Navigate to Data Objects."""
    return _browse('application', 'DataObject')


# ============================================================================
# TECHNOLOGY LAYER (13 element types)
# ============================================================================

@archimate_layer_nav_bp.route('/technology/nodes')
@login_required
def technology_nodes():
    """Navigate to Technology Nodes."""
    return _browse('technology', 'Node')

@archimate_layer_nav_bp.route('/technology/devices')
@login_required
def technology_devices():
    """Navigate to Devices."""
    return _browse('technology', 'Device')

@archimate_layer_nav_bp.route('/technology/system-software')
@login_required
def technology_system_software():
    """Navigate to System Software."""
    return _browse('technology', 'SystemSoftware')

@archimate_layer_nav_bp.route('/technology/collaborations')
@login_required
def technology_collaborations():
    """Navigate to Technology Collaborations."""
    return _browse('technology', 'TechnologyCollaboration')

@archimate_layer_nav_bp.route('/technology/interfaces')
@login_required
def technology_interfaces():
    """Navigate to Technology Interfaces."""
    return _browse('technology', 'TechnologyInterface')

@archimate_layer_nav_bp.route('/technology/paths')
@login_required
def technology_paths():
    """Navigate to Paths."""
    return _browse('technology', 'Path')

@archimate_layer_nav_bp.route('/technology/networks')
@login_required
def technology_networks():
    """Navigate to Communication Networks."""
    return _browse('technology', 'CommunicationNetwork')

@archimate_layer_nav_bp.route('/technology/functions')
@login_required
def technology_functions():
    """Navigate to Technology Functions."""
    return _browse('technology', 'TechnologyFunction')

@archimate_layer_nav_bp.route('/technology/processes')
@login_required
def technology_processes():
    """Navigate to Technology Processes."""
    return _browse('technology', 'TechnologyProcess')

@archimate_layer_nav_bp.route('/technology/interactions')
@login_required
def technology_interactions():
    """Navigate to Technology Interactions."""
    return _browse('technology', 'TechnologyInteraction')

@archimate_layer_nav_bp.route('/technology/events')
@login_required
def technology_events():
    """Navigate to Technology Events."""
    return _browse('technology', 'TechnologyEvent')

@archimate_layer_nav_bp.route('/technology/services')
@login_required
def technology_services():
    """Navigate to Technology Services."""
    return _browse('technology', 'TechnologyService')

@archimate_layer_nav_bp.route('/technology/artifacts')
@login_required
def technology_artifacts():
    """Navigate to Artifacts."""
    return _browse('technology', 'Artifact')


# ============================================================================
# PHYSICAL LAYER (4 element types)
# ============================================================================

@archimate_layer_nav_bp.route('/physical/equipment')
@login_required
def physical_equipment():
    """Navigate to Equipment."""
    return _browse('physical', 'Equipment')

@archimate_layer_nav_bp.route('/physical/facilities')
@login_required
def physical_facilities():
    """Navigate to Facilities."""
    return _browse('physical', 'Facility')

@archimate_layer_nav_bp.route('/physical/distribution-networks')
@login_required
def physical_distribution_networks():
    """Navigate to Distribution Networks."""
    return _browse('physical', 'DistributionNetwork')

@archimate_layer_nav_bp.route('/physical/materials')
@login_required
def physical_materials():
    """Navigate to Materials."""
    return _browse('physical', 'Material')


# ============================================================================
# IMPLEMENTATION & MIGRATION LAYER (4 element types)
# ============================================================================

@archimate_layer_nav_bp.route('/implementation/work-packages')
@login_required
def implementation_work_packages():
    """Navigate to Work Packages."""
    return _browse('implementation', 'WorkPackage')

@archimate_layer_nav_bp.route('/implementation/deliverables')
@login_required
def implementation_deliverables():
    """Navigate to Deliverables."""
    return _browse('implementation', 'Deliverable')

@archimate_layer_nav_bp.route('/implementation/events')
@login_required
def implementation_events():
    """Navigate to Implementation Events."""
    return _browse('implementation', 'ImplementationEvent')

@archimate_layer_nav_bp.route('/implementation/plateaus')
@login_required
def implementation_plateaus():
    """Navigate to Plateaus."""
    return _browse('implementation', 'Plateau')
