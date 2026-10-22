"""
AI-Powered Technology Layer Service for ArchiMate 3.2

This service provides comprehensive Technology Layer modeling:
- Infrastructure node identification (servers, containers, VMs)
- Technology service mapping (databases, middleware, OS services)
- Device modeling (physical hardware)
- System software identification
- Deployment architecture mapping
- Technology dependency analysis

ArchiMate 3.2 Technology Layer Elements:
- Node: Computational or physical resource (server, VM, container, cluster)
- Device: Physical IT resource (laptop, router, sensor, mobile device)
- SystemSoftware: Software environment for specific types of components (OS, database engine, middleware)
- TechnologyService: Explicitly defined exposed technology behavior
- TechnologyInterface: Point of access where technology services are made available
- Path: Link between two or more nodes (network connection)
- CommunicationNetwork: Set of structures connecting nodes (LAN, WAN, Internet)
- Artifact: Physical piece of data (database table, file, container image)
- TechnologyEvent: Technology state change
- TechnologyFunction: Collection of technology behavior
- TechnologyProcess: Sequence of technology behaviors
- TechnologyInteraction: Behavior by 2+ nodes
"""

import json
from datetime import datetime
from typing import Dict, List, Optional

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.services.llm_service import LLMService
from app.models.archimate_element_types import is_application_kind


class TechnologyLayerService:
    """
    AI-powered service for ArchiMate 3.2 Technology Layer modeling.

    Capabilities:
    - Identify infrastructure nodes (physical/virtual servers, containers)
    - Map technology services (databases, middleware, OS services)
    - Model deployment architecture
    - Analyze technology dependencies and constraints
    - Create infrastructure-application mappings
    - Model network topology
    """

    def __init__(self):
        self.llm_service = LLMService()

    # ========================================================================
    # Node Methods (Servers, VMs, Containers)
    # ========================================================================

    def identify_infrastructure_nodes(
        self, infrastructure_description: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify infrastructure nodes from infrastructure description.

        Node: Computational or physical resource hosting applications
        (Physical server, VM, Docker container, Kubernetes pod, cluster)

        Args:
            infrastructure_description: Description of infrastructure landscape
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of Node ArchiMateElements

        Example:
            >>> infra = '''
            ... - 10 Dell PowerEdge R740 physical servers in primary datacenter
            ... - VMware vSphere cluster with 50 VMs
            ... - Kubernetes cluster (5 nodes) for microservices
            ... - AWS EC2 instances for dev/test environments
            ... '''
            >>> nodes = service.identify_infrastructure_nodes(infra, 1)
        """
        prompt = self._build_node_identification_prompt(infrastructure_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            nodes_data = json.loads(response)

            nodes = []
            for node_info in nodes_data.get("nodes", []):
                node = self._create_technology_element(node_info, architecture_id, element_type="Node")
                nodes.append(node)

            db.session.commit()
            return nodes

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Infrastructure node identification failed: {str(e)}")

    def model_deployment_architecture(
        self,
        application_component_id: int,
        node_ids: List[int],
        deployment_description: Optional[str] = None,
    ) -> List[ArchiMateRelationship]:
        """
        Map how applications deploy to infrastructure nodes.

        Args:
            application_component_id: ID of the ApplicationComponent
            node_ids: List of Node IDs where app is deployed
            deployment_description: Optional deployment details

        Returns:
            List of assignment relationships (Node assigned to ApplicationComponent)
        """
        app_component = db.session.get(ArchiMateElement, application_component_id)
        if not app_component or not is_application_kind(app_component.type):
            raise ValueError(f"ApplicationComponent {application_component_id} not found")

        relationships = []

        for node_id in node_ids:
            node = db.session.get(ArchiMateElement, node_id)
            if not node or node.type != "Node":
                continue

            # Node assigned to ApplicationComponent (node hosts the app)
            relationship = ArchiMateRelationship(
                type="assignment",
                source_id=node_id,
                target_id=application_component_id,
                architecture_id=app_component.architecture_id,
            )

            if deployment_description:
                props = {"deployment_details": deployment_description}
                relationship.properties = json.dumps(props)

            db.session.add(relationship)
            relationships.append(relationship)

        db.session.commit()
        return relationships

    # ========================================================================
    # Device Methods (Physical Hardware)
    # ========================================================================

    def identify_devices(
        self, device_inventory: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify physical devices from device inventory.

        Device: Physical IT resource (laptop, router, firewall, sensor, mobile device)

        Args:
            device_inventory: Description of physical devices
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of Device ArchiMateElements
        """
        prompt = self._build_device_identification_prompt(device_inventory)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            devices_data = json.loads(response)

            devices = []
            for device_info in devices_data.get("devices", []):
                device = self._create_technology_element(
                    device_info, architecture_id, element_type="Device"
                )
                devices.append(device)

            db.session.commit()
            return devices

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Device identification failed: {str(e)}")

    # ========================================================================
    # System Software Methods (OS, Database, Middleware)
    # ========================================================================

    def identify_system_software(
        self, software_inventory: str, architecture_id: int, node_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """
        Identify system software from software inventory.

        SystemSoftware: Software environment for components
        (Operating System, Database engine, Web server, Application server, Middleware)

        Args:
            software_inventory: Description of system software
            architecture_id: ID of the ArchitectureModel
            node_id: Optional Node ID where software runs

        Returns:
            List of SystemSoftware ArchiMateElements
        """
        prompt = self._build_system_software_prompt(software_inventory)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            software_data = json.loads(response)

            software_list = []
            for sw_info in software_data.get("system_software", []):
                sw = self._create_technology_element(
                    sw_info, architecture_id, element_type="SystemSoftware"
                )

                # If node specified, create assignment relationship
                if node_id:
                    node = db.session.get(ArchiMateElement, node_id)
                    if node and node.type == "Node":
                        # Node assigned to SystemSoftware (node runs the OS/database)
                        relationship = ArchiMateRelationship(
                            type="assignment",
                            source_id=node_id,
                            target_id=sw.id,
                            architecture_id=architecture_id,
                        )
                        db.session.add(relationship)

                software_list.append(sw)

            db.session.commit()
            return software_list

        except Exception as e:
            db.session.rollback()
            raise Exception(f"System software identification failed: {str(e)}")

    # ========================================================================
    # Technology Service Methods
    # ========================================================================

    def identify_technology_services(
        self, system_software_id: int, service_description: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """
        Identify technology services provided by system software.

        TechnologyService: Explicitly defined exposed technology behavior
        (Database service, File storage service, Authentication service, Messaging service)

        Args:
            system_software_id: ID of the SystemSoftware
            service_description: Optional service description

        Returns:
            List of TechnologyService ArchiMateElements
        """
        system_software = db.session.get(ArchiMateElement, system_software_id)
        if not system_software or system_software.type != "SystemSoftware":
            raise ValueError(f"SystemSoftware {system_software_id} not found")

        prompt = self._build_technology_service_prompt(system_software, service_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            services_data = json.loads(response)

            services = []
            for service_info in services_data.get("services", []):
                service = self._create_technology_element(
                    service_info, system_software.architecture_id, element_type="TechnologyService"
                )

                # SystemSoftware realizes TechnologyService
                relationship = ArchiMateRelationship(
                    type="realization",
                    source_id=system_software_id,
                    target_id=service.id,
                    architecture_id=system_software.architecture_id,
                )
                db.session.add(relationship)

                services.append(service)

            db.session.commit()
            return services

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Technology service identification failed: {str(e)}")

    def map_technology_to_application_service(
        self, technology_service_id: int, application_service_id: int
    ) -> ArchiMateRelationship:
        """
        Map technology service supporting application service.

        Args:
            technology_service_id: ID of the TechnologyService
            application_service_id: ID of the ApplicationService

        Returns:
            ArchiMateRelationship (serving)
        """
        tech_service = db.session.get(ArchiMateElement, technology_service_id)
        app_service = db.session.get(ArchiMateElement, application_service_id)

        if not tech_service or tech_service.type != "TechnologyService":
            raise ValueError(f"TechnologyService {technology_service_id} not found")
        if not app_service or app_service.type != "ApplicationService":
            raise ValueError(f"ApplicationService {application_service_id} not found")

        # TechnologyService serves ApplicationService
        relationship = ArchiMateRelationship(
            type="serving",
            source_id=technology_service_id,
            target_id=application_service_id,
            architecture_id=tech_service.architecture_id,
        )

        db.session.add(relationship)
        db.session.commit()

        return relationship

    # ========================================================================
    # Network & Communication Methods
    # ========================================================================

    def model_network_topology(self, network_description: str, architecture_id: int) -> Dict:
        """
        Model network topology from network description.

        Creates:
        - CommunicationNetwork elements (LAN, WAN, VPN)
        - Path relationships (network links)

        Args:
            network_description: Description of network infrastructure
            architecture_id: ID of the ArchitectureModel

        Returns:
            Dict with networks and paths
        """
        prompt = self._build_network_topology_prompt(network_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            network_data = json.loads(response)

            networks = []
            for net_info in network_data.get("networks", []):
                network = self._create_technology_element(
                    net_info, architecture_id, element_type="CommunicationNetwork"
                )
                networks.append(network)

            paths = []
            for path_info in network_data.get("paths", []):
                # Paths are relationships between nodes
                source_node = self._find_or_create_node(path_info["source"], architecture_id)
                target_node = self._find_or_create_node(path_info["target"], architecture_id)

                path_rel = ArchiMateRelationship(
                    type="flow",
                    source_id=source_node.id,
                    target_id=target_node.id,
                    architecture_id=architecture_id,
                    properties=json.dumps(path_info.get("properties", {})),
                )
                db.session.add(path_rel)
                paths.append(path_rel)

            db.session.commit()

            return {"networks": networks, "paths": paths}

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Network topology modeling failed: {str(e)}")

    # ========================================================================
    # Artifact Methods (Files, Database Tables, Container Images)
    # ========================================================================

    def identify_artifacts(
        self, artifact_description: str, architecture_id: int, node_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """
        Identify artifacts (physical data) from description.

        Artifact: Physical piece of data (database table, file, Docker image, JAR file)

        Args:
            artifact_description: Description of artifacts
            architecture_id: ID of the ArchitectureModel
            node_id: Optional Node ID where artifacts are stored

        Returns:
            List of Artifact ArchiMateElements
        """
        prompt = self._build_artifact_identification_prompt(artifact_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            artifacts_data = json.loads(response)

            artifacts = []
            for artifact_info in artifacts_data.get("artifacts", []):
                artifact = self._create_technology_element(
                    artifact_info, architecture_id, element_type="Artifact"
                )

                # If node specified, create assignment relationship
                if node_id:
                    node = db.session.get(ArchiMateElement, node_id)
                    if node and node.type == "Node":
                        # Node assigned to Artifact (node stores the artifact)
                        relationship = ArchiMateRelationship(
                            type="assignment",
                            source_id=node_id,
                            target_id=artifact.id,
                            architecture_id=architecture_id,
                        )
                        db.session.add(relationship)

                artifacts.append(artifact)

            db.session.commit()
            return artifacts

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Artifact identification failed: {str(e)}")

    # ========================================================================
    # Dependency & Constraint Analysis
    # ========================================================================

    def analyze_technology_dependencies(
        self, node_id: int, infrastructure_context: Optional[str] = None
    ) -> Dict:
        """
        Analyze technology dependencies for a node.

        Args:
            node_id: ID of the Node
            infrastructure_context: Optional infrastructure context

        Returns:
            Dict with dependency analysis:
            {
                'dependencies': [...],  # Other nodes/services this depends on
                'dependents': [...],    # Nodes/services depending on this
                'network_dependencies': [...],
                'storage_dependencies': [...]
            }
        """
        node = db.session.get(ArchiMateElement, node_id)
        if not node or node.type != "Node":
            raise ValueError(f"Node {node_id} not found")

        prompt = self._build_technology_dependency_prompt(node, infrastructure_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            dependency_data = json.loads(response)

            # Store in node properties
            props = json.loads(node.properties) if node.properties else {}
            props["dependencies"] = dependency_data
            props["analyzed_at"] = datetime.utcnow().isoformat()
            node.properties = json.dumps(props)

            db.session.commit()

            return dependency_data

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Technology dependency analysis failed: {str(e)}")

    def identify_technology_constraints(
        self, architecture_id: int, infrastructure_context: str
    ) -> Dict:
        """
        Identify technology constraints and limitations.

        Args:
            architecture_id: ID of the ArchitectureModel
            infrastructure_context: Infrastructure context

        Returns:
            Dict with constraints:
            {
                'capacity_constraints': [...],
                'performance_constraints': [...],
                'security_constraints': [...],
                'compatibility_constraints': [...]
            }
        """
        prompt = self._build_technology_constraints_prompt(infrastructure_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            constraints_data = json.loads(response)

            return constraints_data

        except Exception as e:
            raise Exception(f"Technology constraint identification failed: {str(e)}")

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_technology_element(
        self, element_info: Dict, architecture_id: int, element_type: str
    ) -> ArchiMateElement:
        """Create Technology Layer ArchiMateElement."""
        properties = element_info.get("properties", {})
        properties["created_at"] = datetime.utcnow().isoformat()

        element = ArchiMateElement(
            name=element_info["name"],
            type=element_type,
            layer="technology",
            description=element_info.get("description", ""),
            documentation=element_info.get("documentation", ""),
            properties=json.dumps(properties),
            architecture_id=architecture_id,
        )

        db.session.add(element)
        db.session.flush()
        return element

    def _find_or_create_node(self, node_name: str, architecture_id: int) -> ArchiMateElement:
        """Find existing node or create new one."""
        # Try to find existing node
        existing = ArchiMateElement.query.filter_by(
            name=node_name, type="Node", architecture_id=architecture_id
        ).first()

        if existing:
            return existing

        # Create new node
        node = ArchiMateElement(
            name=node_name, type="Node", layer="technology", architecture_id=architecture_id
        )
        db.session.add(node)
        db.session.flush()
        return node

    # ========================================================================
    # Prompt Building Methods
    # ========================================================================

    def _build_node_identification_prompt(self, infrastructure_description: str) -> str:
        """Build node identification prompt."""
        return f"""Identify INFRASTRUCTURE NODES from this infrastructure description.

Infrastructure:
{infrastructure_description}

A Node is a computational or physical resource:
- Physical servers: Dell PowerEdge, HP ProLiant, IBM Power Systems
- Virtual machines: VMware VMs, Hyper-V VMs
- Containers: Docker containers, Kubernetes pods
- Cloud instances: AWS EC2, Azure VMs, GCP Compute Engine
- Clusters: Kubernetes cluster, Database cluster, Load balancer cluster

For each node:
- name: Node name/identifier
- description: What it does/hosts
- node_type: physical_server | virtual_machine | container | cloud_instance | cluster
- environment: production | staging | development | test
- location: datacenter/region
- specifications: CPU, RAM, storage
- operating_system: OS name and version

Return JSON:
{{
  "nodes": [
    {{
      "name": "PROD-DB - 01",
      "description": "Primary PostgreSQL database server for production",
      "node_type": "physical_server",
      "environment": "production",
      "location": "Primary Datacenter",
      "specifications": "Dell PowerEdge R740, 64GB RAM, 2TB SSD RAID10",
      "operating_system": "Ubuntu Server 22.04 LTS",
      "properties": {{
        "ip_address": "10.0.1.50",
        "high_availability": true,
        "backup_node": "PROD-DB - 02"
      }}
    }},
    {{
      "name": "K8S-PROD-CLUSTER",
      "description": "Kubernetes cluster for microservices",
      "node_type": "cluster",
      "environment": "production",
      "location": "AWS us-east - 1",
      "specifications": "5 nodes, 20 vCPU each, 64GB RAM each",
      "operating_system": "Amazon Linux 2",
      "properties": {{
        "kubernetes_version": "1.28",
        "node_count": 5,
        "pod_capacity": 500
      }}
    }}
  ]
}}
"""

    def _build_device_identification_prompt(self, device_inventory: str) -> str:
        """Build device identification prompt."""
        return f"""Identify PHYSICAL DEVICES from this device inventory.

Device Inventory:
{device_inventory}

A Device is a physical IT resource:
- Network: Router, Switch, Firewall, Load balancer
- End-user: Laptop, Desktop, Mobile device, Tablet
- IoT: Sensor, Controller, Smart device
- Peripherals: Printer, Scanner, Storage appliance

For each device:
- name: Device name
- description: Device purpose
- device_type: router | switch | firewall | laptop | mobile | iot_sensor | printer | storage
- manufacturer: Vendor
- model: Model number
- location: Physical location

Return JSON:
{{
  "devices": [
    {{
      "name": "Core Router CR - 01",
      "description": "Core network router for datacenter",
      "device_type": "router",
      "manufacturer": "Cisco",
      "model": "Catalyst 9600",
      "location": "Primary Datacenter Rack A1",
      "properties": {{
        "ports": 48,
        "throughput": "10 Gbps",
        "management_ip": "192.168.1.1"
      }}
    }}
  ]
}}
"""

    def _build_system_software_prompt(self, software_inventory: str) -> str:
        """Build system software identification prompt."""
        return f"""Identify SYSTEM SOFTWARE from this software inventory.

Software Inventory:
{software_inventory}

System Software is software providing execution environment:
- Operating Systems: Windows Server, Linux (Ubuntu, RHEL), Unix
- Database engines: PostgreSQL, MySQL, Oracle, MongoDB, Redis
- Web servers: Apache, Nginx, IIS
- Application servers: Tomcat, JBoss, WebLogic, WebSphere
- Middleware: RabbitMQ, Kafka, ActiveMQ, IBM MQ
- Container runtimes: Docker Engine, containerd

For each system software:
- name: Software name
- description: What it provides
- software_category: os | database | web_server | app_server | middleware | container_runtime
- vendor: Vendor/organization
- version: Software version
- license: License type

Return JSON:
{{
  "system_software": [
    {{
      "name": "PostgreSQL 15",
      "description": "Relational database management system",
      "software_category": "database",
      "vendor": "PostgreSQL Global Development Group",
      "version": "15.4",
      "license": "PostgreSQL License (open source)",
      "properties": {{
        "port": 5432,
        "max_connections": 200,
        "shared_buffers": "8GB"
      }}
    }},
    {{
      "name": "Nginx",
      "description": "Web server and reverse proxy",
      "software_category": "web_server",
      "vendor": "NGINX Inc",
      "version": "1.24.0",
      "license": "BSD - 2 - Clause",
      "properties": {{
        "worker_processes": 4,
        "worker_connections": 1024
      }}
    }}
  ]
}}
"""

    def _build_technology_service_prompt(
        self, system_software: ArchiMateElement, service_description: Optional[str]
    ) -> str:
        """Build technology service identification prompt."""
        desc_section = (
            f"\n\nService Description:\n{service_description}" if service_description else ""
        )

        return f"""Identify TECHNOLOGY SERVICES provided by this system software.

System Software: {system_software.name}
Description: {system_software.description}
{desc_section}

A Technology Service is explicitly defined exposed technology behavior:
- Database service (CRUD operations, query service)
- File storage service (read/write/delete)
- Authentication service (LDAP, OAuth)
- Messaging service (pub/sub, queues)
- Caching service (get/set/evict)

For each service:
- name: Service name
- description: What the service does
- service_type: database | storage | messaging | caching | authentication | monitoring
- protocol: Protocol used
- port: Service port
- sla: Performance/availability SLA

Return JSON:
{{
  "services": [
    {{
      "name": "PostgreSQL Query Service",
      "description": "Relational data query and transaction service",
      "service_type": "database",
      "protocol": "PostgreSQL wire protocol",
      "port": 5432,
      "sla": "99.99% uptime, <10ms query latency p95",
      "properties": {{
        "max_query_time": "30s",
        "isolation_level": "READ COMMITTED"
      }}
    }}
  ]
}}
"""

    def _build_network_topology_prompt(self, network_description: str) -> str:
        """Build network topology modeling prompt."""
        return f"""Model NETWORK TOPOLOGY from this network description.

Network Description:
{network_description}

Identify:
1. **Communication Networks**: LAN, WAN, VPN, DMZ, Internet
2. **Paths**: Network connections between nodes (with bandwidth, latency)

Return JSON:
{{
  "networks": [
    {{
      "name": "Corporate LAN",
      "description": "Local area network for corporate offices",
      "network_type": "LAN",
      "properties": {{
        "bandwidth": "10 Gbps",
        "subnet": "10.0.0.0/16",
        "vlan_id": 100
      }}
    }}
  ],
  "paths": [
    {{
      "source": "PROD-WEB - 01",
      "target": "PROD-DB - 01",
      "properties": {{
        "bandwidth": "1 Gbps",
        "latency": "<1ms",
        "protocol": "TCP/IP"
      }}
    }}
  ]
}}
"""

    def _build_artifact_identification_prompt(self, artifact_description: str) -> str:
        """Build artifact identification prompt."""
        return f"""Identify ARTIFACTS from this description.

Artifacts:
{artifact_description}

An Artifact is a physical piece of data:
- Database tables/schemas
- Files (config files, data files, logs)
- Container images (Docker images)
- Executable files (JAR, WAR, EXE, DLL)
- Scripts (shell scripts, SQL scripts)

For each artifact:
- name: Artifact name
- description: What it contains
- artifact_type: database_table | file | container_image | executable | script
- format: File format/structure
- size: Approximate size
- storage_location: Where it's stored

Return JSON:
{{
  "artifacts": [
    {{
      "name": "customers_table",
      "description": "PostgreSQL table storing customer master data",
      "artifact_type": "database_table",
      "format": "PostgreSQL table",
      "size": "10GB (5M rows)",
      "storage_location": "PROD-DB - 01:/var/lib/postgresql/data",
      "properties": {{
        "schema": "public",
        "indexes": ["idx_customer_email", "idx_customer_id"],
        "partitioned": true
      }}
    }}
  ]
}}
"""

    def _build_technology_dependency_prompt(
        self, node: ArchiMateElement, infrastructure_context: Optional[str]
    ) -> str:
        """Build technology dependency analysis prompt."""
        context_section = (
            f"\n\nInfrastructure Context:\n{infrastructure_context}"
            if infrastructure_context
            else ""
        )

        return f"""Analyze TECHNOLOGY DEPENDENCIES for this node.

Node: {node.name}
Description: {node.description}
{context_section}

Identify:
1. **Dependencies**: Infrastructure this node depends on
2. **Dependents**: Infrastructure depending on this node
3. **Network Dependencies**: Network connectivity requirements
4. **Storage Dependencies**: Storage requirements

Return JSON:
{{
  "dependencies": [
    {{
      "node": "Primary Storage Array",
      "dependency_type": "storage",
      "criticality": "critical",
      "failure_impact": "Node cannot boot without storage access"
    }}
  ],
  "dependents": [
    {{
      "node": "Web Application Servers",
      "usage": "Database queries",
      "criticality": "high"
    }}
  ],
  "network_dependencies": [
    {{
      "network": "Corporate LAN",
      "bandwidth_required": "100 Mbps",
      "latency_requirement": "<10ms"
    }}
  ],
  "storage_dependencies": [
    {{
      "storage": "SAN Volume 1",
      "capacity_required": "2TB",
      "iops_required": 10000
    }}
  ]
}}
"""

    def _build_technology_constraints_prompt(self, infrastructure_context: str) -> str:
        """Build technology constraints identification prompt."""
        return f"""Identify TECHNOLOGY CONSTRAINTS from infrastructure context.

Infrastructure Context:
{infrastructure_context}

Identify constraints:
1. **Capacity Constraints**: CPU, memory, storage, network limitations
2. **Performance Constraints**: Latency, throughput, response time limits
3. **Security Constraints**: Network segmentation, access controls, compliance
4. **Compatibility Constraints**: OS versions, software dependencies, integration limits

Return JSON:
{{
  "capacity_constraints": [
    {{
      "resource": "Database server CPU",
      "limit": "80% utilization threshold",
      "current_usage": "65%",
      "headroom": "23% (6 months at current growth)"
    }}
  ],
  "performance_constraints": [
    {{
      "constraint": "API response time SLA",
      "requirement": "<200ms p95",
      "current_performance": "150ms p95",
      "bottleneck": "Database query optimization needed"
    }}
  ],
  "security_constraints": [
    {{
      "constraint": "PCI-DSS network segmentation",
      "requirement": "Cardholder data environment isolated",
      "implementation": "Dedicated VLAN with firewall rules"
    }}
  ],
  "compatibility_constraints": [
    {{
      "constraint": "Java version compatibility",
      "requirement": "Application requires Java 11+",
      "limitation": "Some legacy systems still on Java 8"
    }}
  ]
}}
"""
