"""
ArchiMate Element Field Configurations

This module defines the field schemas for all ArchiMate element types.
It provides a data-driven approach to form generation, replacing template conditionals.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from app.models.archimate_element_types import StandardTypeTable


@dataclass
class FieldOption:
    """Represents a select field option."""
    value: str
    label: str


@dataclass
class ElementField:
    """Represents a single form field configuration."""
    name: str
    label: str
    field_type: str  # 'text', 'select', 'textarea'
    required: bool = False
    placeholder: str = ""
    options: List[FieldOption] = field(default_factory=list)
    grid_column: int = 1  # 1 or 2 for grid layout


@dataclass
class ElementTypeConfig:
    """Complete configuration for an ArchiMate element type."""
    element_type: str
    layer: str
    display_name: str
    description: str
    fields: List[ElementField]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for template rendering."""
        fields = [
            {
                'name': f.name,
                'label': f.label,
                'type': f.field_type,
                'required': f.required,
                'placeholder': f.placeholder,
                'options': [{'value': opt.value, 'label': opt.label} for opt in f.options],
                'grid_column': f.grid_column
            }
            for f in self.fields
        ]
        # Architecture state (as-is / to-be) is shared by EVERY element type — it
        # is what lets a transformation programme keep a baseline and a target
        # architecture as distinct states of the model. Appended here once rather
        # than declared on 30+ type configs; the handler maps it to
        # ArchiMateElement.plateau. Baseline = As-Is, Target = To-Be.
        fields.append({
            'name': 'architecture_state',
            'label': 'Architecture state',
            'type': 'select',
            'required': False,
            'placeholder': '',
            'options': [
                {'value': '', 'label': 'Unspecified'},
                {'value': 'Baseline', 'label': 'As-Is (baseline)'},
                {'value': 'Target', 'label': 'To-Be (target)'},
                {'value': 'Transition', 'label': 'Transition'},
            ],
            'grid_column': 1,
        })
        return {
            'element_type': self.element_type,
            'layer': self.layer,
            'display_name': self.display_name,
            'description': self.description,
            'fields': fields,
        }


# Field option sets for reuse
DRIVER_TYPES = [
    FieldOption('regulatory', 'Regulatory'),
    FieldOption('competitive', 'Competitive'),
    FieldOption('customer', 'Customer'),
    FieldOption('technology', 'Technology'),
    FieldOption('financial', 'Financial'),
    FieldOption('operational', 'Operational'),
]

DRIVER_SOURCES = [
    FieldOption('external', 'External'),
    FieldOption('internal', 'Internal'),
]

GOAL_TYPES = [
    FieldOption('strategic', 'Strategic'),
    FieldOption('operational', 'Operational'),
    FieldOption('tactical', 'Tactical'),
]

GOAL_CATEGORIES = [
    FieldOption('growth', 'Growth'),
    FieldOption('efficiency', 'Efficiency'),
    FieldOption('innovation', 'Innovation'),
    FieldOption('compliance', 'Compliance'),
    FieldOption('quality', 'Quality'),
]

ACTOR_TYPES = [
    FieldOption('Department', 'Department'),
    FieldOption('Team', 'Team'),
    FieldOption('Business Unit', 'Business Unit'),
    FieldOption('External Partner', 'External Partner'),
    FieldOption('Individual', 'Individual'),
]

STAKEHOLDER_TYPES = [
    FieldOption('internal', 'Internal'),
    FieldOption('external', 'External'),
    FieldOption('executive', 'Executive'),
    FieldOption('operational', 'Operational'),
    FieldOption('regulatory', 'Regulatory'),
]

# Element type configurations
ELEMENT_TYPE_CONFIGS: Dict[str, ElementTypeConfig] = StandardTypeTable({
    'Driver': ElementTypeConfig(
        element_type='Driver',
        layer='motivation',
        display_name='Driver',
        description='An external or internal condition that motivates the organization to define goals',
        fields=[
            ElementField(
                name='driver_type',
                label='Driver Type',
                field_type='select',
                options=DRIVER_TYPES,
                grid_column=1
            ),
            ElementField(
                name='source',
                label='Source',
                field_type='select',
                options=DRIVER_SOURCES,
                grid_column=2
            ),
        ]
    ),
    
    'Goal': ElementTypeConfig(
        element_type='Goal',
        layer='motivation',
        display_name='Goal',
        description='A high-level statement of intent or direction',
        fields=[
            ElementField(
                name='goal_type',
                label='Goal Type',
                field_type='select',
                options=GOAL_TYPES,
                grid_column=1
            ),
            ElementField(
                name='category',
                label='Category',
                field_type='select',
                options=GOAL_CATEGORIES,
                grid_column=2
            ),
        ]
    ),
    
    'BusinessActor': ElementTypeConfig(
        element_type='BusinessActor',
        layer='business',
        display_name='Business Actor',
        description='An organizational entity that performs behavior',
        fields=[
            ElementField(
                name='actor_type',
                label='Actor Type',
                field_type='select',
                options=ACTOR_TYPES,
                grid_column=1
            ),
            ElementField(
                name='location',
                label='Location',
                field_type='text',
                placeholder='e.g., Headquarters, Remote',
                grid_column=2
            ),
        ]
    ),
    
    'Stakeholder': ElementTypeConfig(
        element_type='Stakeholder',
        layer='motivation',
        display_name='Stakeholder',
        description='An individual, team, or organization with interest in the architecture',
        fields=[
            ElementField(
                name='stakeholder_type',
                label='Stakeholder Type',
                field_type='select',
                options=STAKEHOLDER_TYPES,
                grid_column=1
            ),
            ElementField(
                name='role',
                label='Role',
                field_type='text',
                placeholder='e.g., CFO, Customer',
                grid_column=2
            ),
        ]
    ),

    # ── Motivation Layer ────────────────────────────────────────────────────
    'Outcome': ElementTypeConfig(
        element_type='Outcome',
        layer='motivation',
        display_name='Outcome',
        description='An end result achieved or to be achieved by a stakeholder',
        fields=[
            ElementField(name='outcome_type', label='Outcome Type', field_type='select',
                options=[FieldOption('business','Business'), FieldOption('technical','Technical'),
                         FieldOption('operational','Operational')], grid_column=1),
            ElementField(name='measurement', label='Measurement / KPI', field_type='text',
                placeholder='e.g., NPS > 50, Uptime 99.9%', grid_column=2),
        ]
    ),
    'Principle': ElementTypeConfig(
        element_type='Principle',
        layer='motivation',
        display_name='Architecture Principle',
        description='A qualitative statement of intent that should be met by the architecture',
        fields=[
            ElementField(name='category', label='Category', field_type='select',
                options=[FieldOption('business','Business'), FieldOption('data','Data'),
                         FieldOption('application','Application'), FieldOption('technology','Technology'),
                         FieldOption('security','Security')], grid_column=1),
            ElementField(name='rationale', label='Rationale', field_type='textarea', grid_column=2),
            ElementField(name='implications', label='Implications', field_type='textarea', grid_column=1),
        ]
    ),
    'Constraint': ElementTypeConfig(
        element_type='Constraint',
        layer='motivation',
        display_name='Constraint',
        description='A factor that limits the realization of goals',
        fields=[
            ElementField(name='constraint_type', label='Type', field_type='select',
                options=[FieldOption('regulatory','Regulatory'), FieldOption('financial','Financial'),
                         FieldOption('technical','Technical'), FieldOption('organizational','Organizational')],
                grid_column=1),
        ]
    ),

    # ── Business Layer ──────────────────────────────────────────────────────
    'BusinessRole': ElementTypeConfig(
        element_type='BusinessRole',
        layer='business',
        display_name='Business Role',
        description='The responsibility for performing specific behavior',
        fields=[
            ElementField(name='actor_type', label='Assigned Actor Type', field_type='select',
                options=ACTOR_TYPES, grid_column=1),
        ]
    ),
    'BusinessProcess': ElementTypeConfig(
        element_type='BusinessProcess',
        layer='business',
        display_name='Business Process',
        description='A sequence of business behaviors that achieves a specific result',
        fields=[
            ElementField(name='process_type', label='Process Type', field_type='select',
                options=[FieldOption('core','Core'), FieldOption('supporting','Supporting'),
                         FieldOption('management','Management')], grid_column=1),
            ElementField(name='apqc_code', label='APQC Code', field_type='text',
                placeholder='e.g., 1.1.1', grid_column=2),
        ]
    ),
    'BusinessFunction': ElementTypeConfig(
        element_type='BusinessFunction',
        layer='business',
        display_name='Business Function',
        description='A collection of business behavior based on a chosen set of criteria',
        fields=[
            ElementField(name='domain', label='Domain', field_type='text',
                placeholder='e.g., Finance, HR, Operations', grid_column=1),
        ]
    ),
    'BusinessService': ElementTypeConfig(
        element_type='BusinessService',
        layer='business',
        display_name='Business Service',
        description='An explicitly defined exposed business behavior',
        fields=[
            ElementField(name='service_level', label='Service Level', field_type='select',
                options=[FieldOption('standard','Standard'), FieldOption('premium','Premium'),
                         FieldOption('basic','Basic')], grid_column=1),
        ]
    ),
    'BusinessObject': ElementTypeConfig(
        element_type='BusinessObject',
        layer='business',
        display_name='Business Object',
        description='A passive element that has relevance from a business perspective',
        fields=[
            ElementField(name='lifecycle_state', label='Lifecycle State', field_type='select',
                options=[FieldOption('draft','Draft'), FieldOption('active','Active'),
                         FieldOption('archived','Archived')], grid_column=1),
        ]
    ),
    'ValueStream': ElementTypeConfig(
        element_type='ValueStream',
        layer='business',
        display_name='Value Stream',
        description='A sequence of activities that creates an overall result for a customer',
        fields=[
            ElementField(name='domain', label='Domain', field_type='text',
                placeholder='e.g., Order-to-Cash, Hire-to-Retire', grid_column=1),
        ]
    ),

    # ── Application Layer ───────────────────────────────────────────────────
    'ApplicationComponent': ElementTypeConfig(
        element_type='ApplicationComponent',
        layer='application',
        display_name='Application Component',
        description='An encapsulation of application functionality aligned to an implementation structure',
        fields=[
            ElementField(name='component_type', label='Component Type', field_type='select',
                options=[FieldOption('microservice','Microservice'), FieldOption('monolith','Monolith'),
                         FieldOption('library','Library'), FieldOption('api','API'),
                         FieldOption('frontend','Frontend'), FieldOption('batch','Batch')], grid_column=1),
            ElementField(name='technology_stack', label='Technology Stack', field_type='text',
                placeholder='e.g., Python/Flask, Java/Spring', grid_column=2),
        ]
    ),
    'ApplicationService': ElementTypeConfig(
        element_type='ApplicationService',
        layer='application',
        display_name='Application Service',
        description='An explicitly defined exposed application behavior',
        fields=[
            ElementField(name='protocol', label='Protocol', field_type='select',
                options=[FieldOption('rest','REST'), FieldOption('graphql','GraphQL'),
                         FieldOption('soap','SOAP'), FieldOption('grpc','gRPC'),
                         FieldOption('event','Event/Message')], grid_column=1),
            ElementField(name='sla_tier', label='SLA Tier', field_type='select',
                options=[FieldOption('tier1','Tier 1 – Mission Critical'),
                         FieldOption('tier2','Tier 2 – Business Critical'),
                         FieldOption('tier3','Tier 3 – Standard')], grid_column=2),
        ]
    ),
    'ApplicationInterface': ElementTypeConfig(
        element_type='ApplicationInterface',
        layer='application',
        display_name='Application Interface',
        description='A point of access where application services are made available',
        fields=[
            ElementField(name='interface_type', label='Interface Type', field_type='select',
                options=[FieldOption('ui','User Interface'), FieldOption('api','API'),
                         FieldOption('file','File'), FieldOption('database','Database'),
                         FieldOption('message','Message Queue')], grid_column=1),
        ]
    ),
    'DataObject': ElementTypeConfig(
        element_type='DataObject',
        layer='application',
        display_name='Data Object',
        description='A passive element suitable for automated processing',
        fields=[
            ElementField(name='data_classification', label='Classification', field_type='select',
                options=[FieldOption('public','Public'), FieldOption('internal','Internal'),
                         FieldOption('confidential','Confidential'), FieldOption('restricted','Restricted')],
                grid_column=1),
            ElementField(name='data_format', label='Format', field_type='text',
                placeholder='e.g., JSON, XML, CSV', grid_column=2),
        ]
    ),

    # ── Technology Layer ─────────────────────────────────────────────────────
    # Keyed as 'Node' (not 'TechnologyNode') to match the element_type string
    # MODEL_REGISTRY and LAYER_CONFIG actually use in routes.py — a mismatched
    # key here means get_element_config('Node') never finds this config.
    'Node': ElementTypeConfig(
        element_type='Node',
        layer='technology',
        display_name='Technology Node',
        description='A computational or physical resource hosting, executing, or processing artifacts',
        fields=[
            ElementField(name='node_type', label='Node Type', field_type='select',
                options=[FieldOption('server','Server'), FieldOption('vm','Virtual Machine'),
                         FieldOption('container','Container'), FieldOption('cloud','Cloud Instance'),
                         FieldOption('mainframe','Mainframe')], grid_column=1),
            ElementField(name='location', label='Location / Data Centre', field_type='text',
                placeholder='e.g., AWS us-east-1, On-Prem DC1', grid_column=2),
        ]
    ),
    # Keyed as 'Device' for the same reason — see 'Node' above.
    'Device': ElementTypeConfig(
        element_type='Device',
        layer='technology',
        display_name='Device',
        description='A physical IT resource upon which system software and artifacts can be stored or deployed',
        fields=[
            ElementField(name='device_type', label='Device Type', field_type='select',
                options=[FieldOption('workstation','Workstation'), FieldOption('mobile','Mobile'),
                         FieldOption('iot','IoT Device'), FieldOption('network','Network Device'),
                         FieldOption('storage','Storage')], grid_column=1),
        ]
    ),
    'SystemSoftware': ElementTypeConfig(
        element_type='SystemSoftware',
        layer='technology',
        display_name='System Software',
        description='Software that provides or contributes to an environment for storing and executing artifacts',
        fields=[
            ElementField(name='software_type', label='Software Type', field_type='select',
                options=[FieldOption('os','Operating System'), FieldOption('middleware','Middleware'),
                         FieldOption('database','Database'), FieldOption('runtime','Runtime'),
                         FieldOption('container_engine','Container Engine')], grid_column=1),
            ElementField(name='version', label='Version', field_type='text',
                placeholder='e.g., 22.04 LTS', grid_column=2),
        ]
    ),
    'TechnologyService': ElementTypeConfig(
        element_type='TechnologyService',
        layer='technology',
        display_name='Technology Service',
        description='An explicitly defined exposed technology behavior',
        fields=[
            ElementField(name='service_type', label='Service Type', field_type='select',
                options=[FieldOption('compute','Compute'), FieldOption('storage','Storage'),
                         FieldOption('network','Network'), FieldOption('security','Security'),
                         FieldOption('monitoring','Monitoring')], grid_column=1),
        ]
    ),
    'CommunicationNetwork': ElementTypeConfig(
        element_type='CommunicationNetwork',
        layer='technology',
        display_name='Communication Network',
        description='A set of structures that connects nodes for transmission of data',
        fields=[
            ElementField(name='network_type', label='Network Type', field_type='select',
                options=[FieldOption('lan','LAN'), FieldOption('wan','WAN'),
                         FieldOption('vpn','VPN'), FieldOption('internet','Internet'),
                         FieldOption('mpls','MPLS')], grid_column=1),
            ElementField(name='bandwidth', label='Bandwidth', field_type='text',
                placeholder='e.g., 1Gbps, 100Mbps', grid_column=2),
        ]
    ),

    # ── Implementation & Migration Layer ─────────────────────────────────────
    'WorkPackage': ElementTypeConfig(
        element_type='WorkPackage',
        layer='implementation',
        display_name='Work Package',
        description='A series of actions intended to produce a result within a specified time period',
        fields=[
            ElementField(name='status', label='Status', field_type='select',
                options=[FieldOption('planned','Planned'), FieldOption('in_progress','In Progress'),
                         FieldOption('complete','Complete'), FieldOption('cancelled','Cancelled')],
                grid_column=1),
            ElementField(name='target_date', label='Target Date', field_type='text',
                placeholder='YYYY-MM-DD', grid_column=2),
        ]
    ),
    'Deliverable': ElementTypeConfig(
        element_type='Deliverable',
        layer='implementation',
        display_name='Deliverable',
        description='A precisely-defined outcome of a work package',
        fields=[
            ElementField(name='deliverable_type', label='Type', field_type='select',
                options=[FieldOption('document','Document'), FieldOption('system','System'),
                         FieldOption('service','Service'), FieldOption('capability','Capability')],
                grid_column=1),
            ElementField(name='status', label='Status', field_type='select',
                options=[FieldOption('draft','Draft'), FieldOption('review','In Review'),
                         FieldOption('approved','Approved'), FieldOption('delivered','Delivered')],
                grid_column=2),
        ]
    ),
    'Plateau': ElementTypeConfig(
        element_type='Plateau',
        layer='implementation',
        display_name='Plateau',
        description='A relatively stable state of the architecture that exists during a limited period',
        fields=[
            ElementField(name='plateau_type', label='Plateau Type', field_type='select',
                options=[FieldOption('baseline','Baseline'), FieldOption('target','Target'),
                         FieldOption('transition','Transition')], grid_column=1),
            ElementField(name='target_date', label='Target Date', field_type='text',
                placeholder='YYYY-MM-DD', grid_column=2),
        ]
    ),

    # ── Strategy Layer ───────────────────────────────────────────────────────
    'Capability': ElementTypeConfig(
        element_type='Capability',
        layer='strategy',
        display_name='Capability',
        description='An ability that an active structure element possesses',
        fields=[
            ElementField(name='maturity_level', label='Maturity Level', field_type='select',
                options=[FieldOption('1','1 – Initial'), FieldOption('2','2 – Developing'),
                         FieldOption('3','3 – Defined'), FieldOption('4','4 – Managed'),
                         FieldOption('5','5 – Optimizing')], grid_column=1),
        ]
    ),
    'CourseOfAction': ElementTypeConfig(
        element_type='CourseOfAction',
        layer='strategy',
        display_name='Course of Action',
        description='An approach or plan for configuring a capability or resource',
        fields=[
            ElementField(name='action_type', label='Action Type', field_type='select',
                options=[FieldOption('invest','Invest'), FieldOption('transform','Transform'),
                         FieldOption('retire','Retire'), FieldOption('tolerate','Tolerate')], grid_column=1),
        ]
    ),
})


def get_element_config(element_type: str) -> Optional[ElementTypeConfig]:
    """
    Get configuration for a specific element type.
    
    Args:
        element_type: The element type name (e.g., 'Driver', 'Goal')
        
    Returns:
        ElementTypeConfig if found, None otherwise
    """
    return ELEMENT_TYPE_CONFIGS.get(element_type)


def get_all_element_types() -> List[str]:
    """Get list of all supported element types."""
    return list(ELEMENT_TYPE_CONFIGS.keys())


def get_element_field_names(element_type: str) -> List[str]:
    """
    Get list of dynamic field names for an element type.
    Used for form initialization and API handling.
    """
    config = get_element_config(element_type)
    if not config:
        return []
    return [f.name for f in config.fields]


def create_empty_form_data(element_type: str) -> Dict[str, str]:
    """
    Create empty form data structure with all fields initialized.
    Used for Alpine.js form initialization.
    """
    config = get_element_config(element_type)
    if not config:
        return {}
    
    data = {'name': '', 'description': ''}
    for item in config.fields:
        data[item.name] = ''
    return data
