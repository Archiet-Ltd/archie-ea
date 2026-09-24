"""flask seed-demo-company — create the Lantern Quay Systems demonstration.

Creates the fictional organisation "Lantern Quay Systems" with a coherent
estate of about 300 ArchiMate elements, relationships, applications,
capabilities, costs, risks, programmes, plateaus and gaps, plus a read-only
demo user whose password is read from the DEMO_USER_PASSWORD environment
variable.

Idempotent: running it twice changes nothing. All rows carry the demo
organisation's id. Every name is plainly invented.

    flask --app manage seed-demo-company
"""

from __future__ import annotations

import datetime as _dt
import os

import click
from flask.cli import with_appcontext

from app import db

# ── organisation ───────────────────────────────────────────────────────────

_ORG_NAME = "Lantern Quay Systems"
_ORG_SLUG = "lantern-quay"

# ── demo user ──────────────────────────────────────────────────────────────

_DEMO_USER_EMAIL = "demo@lantern-quay.example.com"
_DEMO_USER_FIRST = "Demo"
_DEMO_USER_LAST = "User"

# ── five organisational units ──────────────────────────────────────────────

_UNITS = [
    "Fabrication",
    "Fulfilment",
    "Field Service",
    "Circular Recovery",
    "Shared Services",
]

# ── ArchiMate elements ─────────────────────────────────────────────────────
# Each entry: (name, type, layer).  About 300 elements across all layers.

_ELEMENTS = [
    # ── Business layer: actors, roles, processes, functions ────────────────
    ("Fabrication Manager", "BusinessActor", "business"),
    ("Fulfilment Coordinator", "BusinessActor", "business"),
    ("Field Service Engineer", "BusinessActor", "business"),
    ("Recovery Technician", "BusinessActor", "business"),
    ("Shared Services Director", "BusinessActor", "business"),
    ("Ivo Reed", "BusinessActor", "business"),
    ("Production Supervisor", "BusinessRole", "business"),
    ("Quality Assurance Lead", "BusinessRole", "business"),
    ("Logistics Planner", "BusinessRole", "business"),
    ("Service Desk Operator", "BusinessRole", "business"),
    ("Release calibrated batch", "BusinessProcess", "business"),
    ("Pack calibrated batch", "BusinessProcess", "business"),
    ("Dispatch calibrated batch", "BusinessProcess", "business"),
    ("Receive recovered assembly", "BusinessProcess", "business"),
    ("Inspect recovered assembly", "BusinessProcess", "business"),
    ("Recertify recovered assembly", "BusinessProcess", "business"),
    ("Reserve replacement stock", "BusinessProcess", "business"),
    ("Assemble sensor module", "BusinessProcess", "business"),
    ("Test sensor calibration", "BusinessProcess", "business"),
    ("Package finished unit", "BusinessProcess", "business"),
    ("Ship customer order", "BusinessProcess", "business"),
    ("Process return authorisation", "BusinessProcess", "business"),
    ("Decontaminate returned unit", "BusinessProcess", "business"),
    ("Replenish component stock", "BusinessProcess", "business"),
    ("Generate compliance report", "BusinessProcess", "business"),
    ("Approve capital expenditure", "BusinessProcess", "business"),
    ("Onboard new supplier", "BusinessProcess", "business"),
    ("Conduct safety audit", "BusinessProcess", "business"),
    ("Manage workforce schedule", "BusinessProcess", "business"),
    ("Reconcile inventory counts", "BusinessProcess", "business"),
    ("Water quality sampling", "BusinessProcess", "business"),
    ("Sensor drift analysis", "BusinessProcess", "business"),
    ("Field calibration run", "BusinessProcess", "business"),
    ("Remote diagnostics session", "BusinessProcess", "business"),
    ("Preventive maintenance visit", "BusinessProcess", "business"),
    ("Firmware update rollout", "BusinessProcess", "business"),
    ("Evaluate calibration", "BusinessFunction", "business"),
    ("Validate measurement accuracy", "BusinessFunction", "business"),
    ("Compute drift trend", "BusinessFunction", "business"),
    ("Generate calibration certificate", "BusinessFunction", "business"),
    ("Authorise batch release", "BusinessFunction", "business"),
    ("Assess return condition", "BusinessFunction", "business"),
    ("Determine repair scope", "BusinessFunction", "business"),
    ("Calculate stock reorder point", "BusinessFunction", "business"),
    ("Forecast demand", "BusinessFunction", "business"),
    ("Score supplier performance", "BusinessFunction", "business"),
    ("Monitor water turbidity", "BusinessFunction", "business"),
    ("Detect anomaly in readings", "BusinessFunction", "business"),
    ("Classify incident severity", "BusinessFunction", "business"),
    ("Escalate unresolved alert", "BusinessFunction", "business"),
    # ── Application layer: components, services, functions, data ───────────
    ("Event Relay", "ApplicationComponent", "application"),
    ("Calibration Ledger", "ApplicationComponent", "application"),
    ("Dispatch Planner", "ApplicationComponent", "application"),
    ("Inventory Manager", "ApplicationComponent", "application"),
    ("Field Service Scheduler", "ApplicationComponent", "application"),
    ("Compliance Reporter", "ApplicationComponent", "application"),
    ("Supplier Portal", "ApplicationComponent", "application"),
    ("Workforce Planner", "ApplicationComponent", "application"),
    ("Quality Monitor", "ApplicationComponent", "application"),
    ("Sensor Data Hub", "ApplicationComponent", "application"),
    ("Firmware Distribution Service", "ApplicationComponent", "application"),
    ("Customer Portal", "ApplicationComponent", "application"),
    ("Finance System", "ApplicationComponent", "application"),
    ("Document Management", "ApplicationComponent", "application"),
    ("Identity Provider", "ApplicationComponent", "application"),
    ("Monitoring Dashboard", "ApplicationComponent", "application"),
    ("Alert Manager", "ApplicationComponent", "application"),
    ("Reporting Engine", "ApplicationComponent", "application"),
    ("Integration Bus", "ApplicationComponent", "application"),
    ("Data Warehouse", "ApplicationComponent", "application"),
    ("Event Delivery", "ApplicationService", "application"),
    ("Stock Promise", "ApplicationService", "application"),
    ("Calibration Lookup", "ApplicationService", "application"),
    ("Inventory Reservation", "ApplicationService", "application"),
    ("Dispatch Scheduling", "ApplicationService", "application"),
    ("Field Assignment", "ApplicationService", "application"),
    ("Compliance Export", "ApplicationService", "application"),
    ("Supplier Scorecard", "ApplicationService", "application"),
    ("Shift Roster", "ApplicationService", "application"),
    ("Quality Threshold Check", "ApplicationService", "application"),
    ("Sensor Ingestion", "ApplicationService", "application"),
    ("Firmware Bundle", "ApplicationService", "application"),
    ("Customer Order Status", "ApplicationService", "application"),
    ("Invoice Generation", "ApplicationService", "application"),
    ("Document Search", "ApplicationService", "application"),
    ("Single Sign-On", "ApplicationService", "application"),
    ("Dashboard Query", "ApplicationService", "application"),
    ("Alert Dispatch", "ApplicationService", "application"),
    ("Report Compilation", "ApplicationService", "application"),
    ("Message Routing", "ApplicationService", "application"),
    ("Analytics Query", "ApplicationService", "application"),
    ("Calibration result", "DataObject", "application"),
    ("Sensor reading", "DataObject", "application"),
    ("Batch release record", "DataObject", "application"),
    ("Inventory snapshot", "DataObject", "application"),
    ("Dispatch manifest", "DataObject", "application"),
    ("Field service report", "DataObject", "application"),
    ("Compliance submission", "DataObject", "application"),
    ("Supplier evaluation", "DataObject", "application"),
    ("Shift assignment", "DataObject", "application"),
    ("Quality incident", "DataObject", "application"),
    ("Firmware image", "DataObject", "application"),
    ("Customer order", "DataObject", "application"),
    ("Invoice", "DataObject", "application"),
    ("Audit log entry", "DataObject", "application"),
    ("Water quality sample", "DataObject", "application"),
    ("Anomaly record", "DataObject", "application"),
    ("Incident ticket", "DataObject", "application"),
    ("Maintenance schedule", "DataObject", "application"),
    ("Calibration certificate", "DataObject", "application"),
    ("Return authorisation", "DataObject", "application"),
    # ── Technology layer: nodes, devices, services, software ───────────────
    ("Quay Compute Pool", "Node", "technology"),
    ("Fabrication Control Network", "Node", "technology"),
    ("Fulfilment Centre Network", "Node", "technology"),
    ("Field Service Edge", "Node", "technology"),
    ("Recovery Lab Network", "Node", "technology"),
    ("Shared Services Cloud", "Node", "technology"),
    ("Sensor Gateway", "Device", "technology"),
    ("Calibration Rig Controller", "Device", "technology"),
    ("Handheld Field Tester", "Device", "technology"),
    ("Barcode Scanner Array", "Device", "technology"),
    ("Environmental Monitor", "Device", "technology"),
    ("Label Printer Station", "Device", "technology"),
    ("Event Transport", "TechnologyService", "technology"),
    ("Message Queue", "TechnologyService", "technology"),
    ("Object Storage", "TechnologyService", "technology"),
    ("Container Orchestrator", "TechnologyService", "technology"),
    ("Relational Database", "TechnologyService", "technology"),
    ("Time-Series Database", "TechnologyService", "technology"),
    ("Search Index", "TechnologyService", "technology"),
    ("API Gateway", "TechnologyService", "technology"),
    ("Secret Store", "TechnologyService", "technology"),
    ("Log Aggregator", "TechnologyService", "technology"),
    ("Metric Collector", "TechnologyService", "technology"),
    ("Notification Service", "TechnologyService", "technology"),
    ("DNS Resolver", "TechnologyService", "technology"),
    ("Load Balancer", "TechnologyService", "technology"),
    ("Certificate Authority", "TechnologyService", "technology"),
    ("Container Runtime", "SystemSoftware", "technology"),
    ("Operating System Image", "SystemSoftware", "technology"),
    ("Database Engine", "SystemSoftware", "technology"),
    ("Message Broker", "SystemSoftware", "technology"),
    ("Web Server", "SystemSoftware", "technology"),
    ("Monitoring Agent", "SystemSoftware", "technology"),
    ("Backup Agent", "SystemSoftware", "technology"),
    ("Log Shipper", "SystemSoftware", "technology"),
    # ── Motivation layer: stakeholders, drivers, assessments, goals ────────
    ("Regulatory Compliance Officer", "Stakeholder", "motivation"),
    ("Chief Operations Officer", "Stakeholder", "motivation"),
    ("Head of Engineering", "Stakeholder", "motivation"),
    ("Water Authority Inspector", "Stakeholder", "motivation"),
    ("Environmental Regulator", "Stakeholder", "motivation"),
    ("Increasing regulatory scrutiny", "Driver", "motivation"),
    ("Ageing field equipment", "Driver", "motivation"),
    ("Customer demand for real-time data", "Driver", "motivation"),
    ("Cost pressure on field operations", "Driver", "motivation"),
    ("Supply chain volatility", "Driver", "motivation"),
    ("Regulatory compliance gap", "Assessment", "motivation"),
    ("Field service efficiency below target", "Assessment", "motivation"),
    ("Calibration drift exceeds threshold", "Assessment", "motivation"),
    ("Inventory accuracy below benchmark", "Assessment", "motivation"),
    ("Supplier performance degradation", "Assessment", "motivation"),
    ("Achieve ISO 17025 accreditation", "Goal", "motivation"),
    ("Reduce field service cost per visit", "Goal", "motivation"),
    ("Improve calibration throughput", "Goal", "motivation"),
    ("Achieve 99.5% inventory accuracy", "Goal", "motivation"),
    ("Reduce supplier lead time by 20%", "Goal", "motivation"),
    ("All measurements traceable to national standards", "Principle", "motivation"),
    ("Data retained for seven years minimum", "Principle", "motivation"),
    ("Every batch release authorised by two qualified persons", "Principle", "motivation"),
    ("Field devices must operate offline for 72 hours", "Requirement", "motivation"),
    ("Calibration records must be immutable", "Requirement", "motivation"),
    ("Customer data must be encrypted at rest", "Requirement", "motivation"),
    ("System must support 500 concurrent field devices", "Requirement", "motivation"),
    ("Recovery time objective of four hours", "Requirement", "motivation"),
    ("Must not exceed licensed sensor count", "Constraint", "motivation"),
    ("Must operate within existing network segments", "Constraint", "motivation"),
    ("Budget capped at approved annual allocation", "Constraint", "motivation"),
    # ── Strategy layer: capabilities, resources, courses of action ─────────
    ("Sensor Calibration", "Capability", "strategy"),
    ("Water Quality Monitoring", "Capability", "strategy"),
    ("Field Service Management", "Capability", "strategy"),
    ("Inventory Control", "Capability", "strategy"),
    ("Order Fulfilment", "Capability", "strategy"),
    ("Supplier Management", "Capability", "strategy"),
    ("Regulatory Compliance", "Capability", "strategy"),
    ("Workforce Planning", "Capability", "strategy"),
    ("Asset Lifecycle Management", "Capability", "strategy"),
    ("Incident Response", "Capability", "strategy"),
    ("Demand Forecasting", "Capability", "strategy"),
    ("Quality Assurance", "Capability", "strategy"),
    ("Calibration Rig Pool", "Resource", "strategy"),
    ("Field Engineer Pool", "Resource", "strategy"),
    ("Reference Standard Set", "Resource", "strategy"),
    ("Spare Parts Inventory", "Resource", "strategy"),
    ("Deploy automated calibration rigs", "CourseOfAction", "strategy"),
    ("Roll out handheld field testers", "CourseOfAction", "strategy"),
    ("Consolidate supplier base", "CourseOfAction", "strategy"),
    ("Migrate to cloud-hosted event backbone", "CourseOfAction", "strategy"),
    ("Implement predictive maintenance", "CourseOfAction", "strategy"),
    # ── Implementation layer: work packages, deliverables, plateaus, gaps ──
    ("Calibration Automation Phase 1", "WorkPackage", "implementation"),
    ("Calibration Automation Phase 2", "WorkPackage", "implementation"),
    ("Field Tester Rollout", "WorkPackage", "implementation"),
    ("Event Backbone Migration", "WorkPackage", "implementation"),
    ("Supplier Consolidation Programme", "WorkPackage", "implementation"),
    ("Predictive Maintenance Pilot", "WorkPackage", "implementation"),
    ("Compliance System Upgrade", "WorkPackage", "implementation"),
    ("Inventory Accuracy Drive", "WorkPackage", "implementation"),
    ("Automated Calibration Rig", "Deliverable", "implementation"),
    ("Deployed Field Tester Fleet", "Deliverable", "implementation"),
    ("Cloud Event Backbone", "Deliverable", "implementation"),
    ("Consolidated Supplier Panel", "Deliverable", "implementation"),
    ("Predictive Model", "Deliverable", "implementation"),
    ("Upgraded Compliance Module", "Deliverable", "implementation"),
    ("Inventory Reconciliation Tool", "Deliverable", "implementation"),
    ("Baseline Architecture", "Plateau", "implementation"),
    ("Transition State 1", "Plateau", "implementation"),
    ("Target Architecture", "Plateau", "implementation"),
    ("Calibration throughput gap", "Gap", "implementation"),
    ("Field service coverage gap", "Gap", "implementation"),
    ("Inventory visibility gap", "Gap", "implementation"),
    ("Supplier performance gap", "Gap", "implementation"),
    ("Event backbone resilience gap", "Gap", "implementation"),
    # ── Additional elements to reach ~300 ──────────────────────────────────
    ("Fabrication Workstation", "Device", "technology"),
    ("Assembly Line Controller", "Device", "technology"),
    ("Test Chamber Monitor", "Device", "technology"),
    ("Shipping Scale", "Device", "technology"),
    ("Receiving Dock Scanner", "Device", "technology"),
    ("Cold Storage Monitor", "Device", "technology"),
    ("Network Firewall", "SystemSoftware", "technology"),
    ("Intrusion Detection System", "SystemSoftware", "technology"),
    ("VPN Gateway", "SystemSoftware", "technology"),
    ("Configuration Management Database", "SystemSoftware", "technology"),
    ("Service Mesh", "TechnologyService", "technology"),
    ("Distributed Cache", "TechnologyService", "technology"),
    ("Feature Flag Service", "TechnologyService", "technology"),
    ("Circuit Breaker", "TechnologyService", "technology"),
    ("Rate Limiter", "TechnologyService", "technology"),
    ("Audit Trail", "TechnologyService", "technology"),
    ("Data Replication Service", "TechnologyService", "technology"),
    ("Schema Registry", "TechnologyService", "technology"),
    ("Workflow Engine", "TechnologyService", "technology"),
    ("Rules Engine", "TechnologyService", "technology"),
    ("Notification Preferences", "DataObject", "application"),
    ("Calibration history", "DataObject", "application"),
    ("Device telemetry", "DataObject", "application"),
    ("Usage report", "DataObject", "application"),
    ("Capacity forecast", "DataObject", "application"),
    ("Cost allocation", "DataObject", "application"),
    ("Service level agreement", "DataObject", "application"),
    ("Training record", "DataObject", "application"),
    ("Safety incident", "DataObject", "application"),
    ("Environmental reading", "DataObject", "application"),
    ("Validate sensor identity", "BusinessFunction", "business"),
    ("Compute measurement uncertainty", "BusinessFunction", "business"),
    ("Log calibration event", "BusinessFunction", "business"),
    ("Flag out-of-tolerance result", "BusinessFunction", "business"),
    ("Schedule recalibration", "BusinessFunction", "business"),
    ("Approve instrument for use", "BusinessFunction", "business"),
    ("Quarantine failed instrument", "BusinessFunction", "business"),
    ("Generate non-conformance report", "BusinessFunction", "business"),
    ("Review trend data", "BusinessFunction", "business"),
    ("Adjust control limits", "BusinessFunction", "business"),
    ("Record instrument history", "BusinessFunction", "business"),
    ("Verify reference standard", "BusinessFunction", "business"),
    ("Manage calibration schedule", "BusinessFunction", "business"),
    ("Allocate calibration resource", "BusinessFunction", "business"),
    ("Close calibration work order", "BusinessFunction", "business"),
    ("Receive raw materials", "BusinessProcess", "business"),
    ("Inspect incoming components", "BusinessProcess", "business"),
    ("Store quarantined materials", "BusinessProcess", "business"),
    ("Release materials to production", "BusinessProcess", "business"),
    ("Record batch traceability", "BusinessProcess", "business"),
    ("Conduct end-of-line test", "BusinessProcess", "business"),
    ("Apply calibration label", "BusinessProcess", "business"),
    ("Load dispatch vehicle", "BusinessProcess", "business"),
    ("Confirm delivery receipt", "BusinessProcess", "business"),
    ("Handle customer complaint", "BusinessProcess", "business"),
    ("Investigate root cause", "BusinessProcess", "business"),
    ("Issue corrective action", "BusinessProcess", "business"),
    ("Verify corrective action", "BusinessProcess", "business"),
    ("Close quality case", "BusinessProcess", "business"),
    ("Provision field device", "BusinessProcess", "business"),
    ("Decommission field device", "BusinessProcess", "business"),
    ("Audit calibration records", "BusinessProcess", "business"),
    ("Prepare regulatory submission", "BusinessProcess", "business"),
    ("Review supplier contract", "BusinessProcess", "business"),
    ("Negotiate pricing terms", "BusinessProcess", "business"),
    ("Approve purchase order", "BusinessProcess", "business"),
    ("Receive supplier shipment", "BusinessProcess", "business"),
    ("Inspect supplier delivery", "BusinessProcess", "business"),
    ("Record supplier non-conformance", "BusinessProcess", "business"),
    ("Rate supplier performance", "BusinessProcess", "business"),
    ("Terminate supplier relationship", "BusinessProcess", "business"),
    ("Fabrication Technician", "BusinessActor", "business"),
    ("Fulfilment Operator", "BusinessActor", "business"),
    ("Quality Inspector", "BusinessActor", "business"),
    ("Calibration Specialist", "BusinessActor", "business"),
    ("Inventory Controller", "BusinessActor", "business"),
    ("Procurement Officer", "BusinessActor", "business"),
    ("IT Operations Lead", "BusinessActor", "business"),
    ("Data Analyst", "BusinessActor", "business"),
    ("Customer Support Agent", "BusinessActor", "business"),
    ("Field Technician", "BusinessActor", "business"),
    ("Production Planner", "BusinessRole", "business"),
    ("Shift Supervisor", "BusinessRole", "business"),
    ("Calibration Approver", "BusinessRole", "business"),
    ("Inventory Auditor", "BusinessRole", "business"),
    ("Procurement Reviewer", "BusinessRole", "business"),
    ("Safety Officer", "BusinessRole", "business"),
    ("Data Steward", "BusinessRole", "business"),
    ("System Administrator", "BusinessRole", "business"),
    ("Network Engineer", "BusinessRole", "business"),
    ("Support Team Lead", "BusinessRole", "business"),
    ("Reduce calibration cycle time by 30%", "Goal", "motivation"),
    ("Eliminate paper-based field reports", "Goal", "motivation"),
    ("Achieve zero safety incidents", "Goal", "motivation"),
    ("Maintain 99.9% system availability", "Goal", "motivation"),
    ("Reduce energy consumption by 15%", "Goal", "motivation"),
    ("All safety-critical decisions are recorded", "Principle", "motivation"),
    ("Personal data minimised to operational need", "Principle", "motivation"),
    ("Every instrument has a single source of truth", "Principle", "motivation"),
    ("Changes gated by peer review", "Principle", "motivation"),
    ("Failures must be visible within five minutes", "Requirement", "motivation"),
    ("All APIs must support versioning", "Requirement", "motivation"),
    ("Audit logs retained for ten years", "Requirement", "motivation"),
    ("Must integrate with existing ERP suite", "Constraint", "motivation"),
    ("Must use approved cloud regions only", "Constraint", "motivation"),
    ("No single point of failure in event path", "Constraint", "motivation"),
    ("Digital Transformation Programme", "WorkPackage", "implementation"),
    ("Quality Excellence Programme", "WorkPackage", "implementation"),
    ("Operational Resilience Programme", "WorkPackage", "implementation"),
    ("Sustainability Initiative", "WorkPackage", "implementation"),
    ("Digital Twin of Calibration Lab", "Deliverable", "implementation"),
    ("Mobile Field App", "Deliverable", "implementation"),
    ("Real-time Dashboard", "Deliverable", "implementation"),
    ("Automated Test Suite", "Deliverable", "implementation"),
    ("Disaster Recovery Runbook", "Deliverable", "implementation"),
    ("Current State", "Plateau", "implementation"),
    ("Future State", "Plateau", "implementation"),
    ("Data quality gap", "Gap", "implementation"),
    ("Integration coverage gap", "Gap", "implementation"),
    ("Skills shortage gap", "Gap", "implementation"),
    ("Monitoring coverage gap", "Gap", "implementation"),
    ("Documentation currency gap", "Gap", "implementation"),
]

# ── relationships ──────────────────────────────────────────────────────────
# Each entry: (source_name, target_name, type)

_RELATIONSHIPS = [
    # Core chain from the design document
    ("Quay Compute Pool", "Event Transport", "Realization"),
    ("Event Transport", "Event Relay", "Serving"),
    ("Event Relay", "Event Delivery", "Realization"),
    ("Event Delivery", "Release calibrated batch", "Serving"),
    ("Release calibrated batch", "Pack calibrated batch", "Triggering"),
    ("Pack calibrated batch", "Dispatch calibrated batch", "Triggering"),
    ("Calibration Ledger", "Evaluate calibration", "Assignment"),
    ("Evaluate calibration", "Calibration result", "Access"),
    ("Receive recovered assembly", "Inspect recovered assembly", "Triggering"),
    ("Inspect recovered assembly", "Recertify recovered assembly", "Triggering"),
    ("Recertify recovered assembly", "Receive recovered assembly", "Triggering"),
    ("Dispatch Planner", "Stock Promise", "Realization"),
    ("Stock Promise", "Reserve replacement stock", "Serving"),
    ("Dispatch Planner", "Reserve replacement stock", "Association"),
    # Business process flows
    ("Assemble sensor module", "Test sensor calibration", "Triggering"),
    ("Test sensor calibration", "Package finished unit", "Triggering"),
    ("Package finished unit", "Ship customer order", "Triggering"),
    ("Process return authorisation", "Decontaminate returned unit", "Triggering"),
    ("Decontaminate returned unit", "Inspect recovered assembly", "Triggering"),
    ("Replenish component stock", "Assemble sensor module", "Serving"),
    ("Generate compliance report", "Prepare regulatory submission", "Triggering"),
    ("Approve capital expenditure", "Onboard new supplier", "Triggering"),
    ("Conduct safety audit", "Generate compliance report", "Triggering"),
    ("Manage workforce schedule", "Reconcile inventory counts", "Serving"),
    ("Water quality sampling", "Sensor drift analysis", "Triggering"),
    ("Sensor drift analysis", "Field calibration run", "Triggering"),
    ("Field calibration run", "Remote diagnostics session", "Triggering"),
    ("Remote diagnostics session", "Preventive maintenance visit", "Triggering"),
    ("Firmware update rollout", "Remote diagnostics session", "Serving"),
    # Application assignments
    ("Event Relay", "Message Routing", "Realization"),
    ("Calibration Ledger", "Calibration Lookup", "Realization"),
    ("Dispatch Planner", "Dispatch Scheduling", "Realization"),
    ("Inventory Manager", "Inventory Reservation", "Realization"),
    ("Field Service Scheduler", "Field Assignment", "Realization"),
    ("Compliance Reporter", "Compliance Export", "Realization"),
    ("Supplier Portal", "Supplier Scorecard", "Realization"),
    ("Workforce Planner", "Shift Roster", "Realization"),
    ("Quality Monitor", "Quality Threshold Check", "Realization"),
    ("Sensor Data Hub", "Sensor Ingestion", "Realization"),
    ("Firmware Distribution Service", "Firmware Bundle", "Realization"),
    ("Customer Portal", "Customer Order Status", "Realization"),
    ("Finance System", "Invoice Generation", "Realization"),
    ("Document Management", "Document Search", "Realization"),
    ("Identity Provider", "Single Sign-On", "Realization"),
    ("Monitoring Dashboard", "Dashboard Query", "Realization"),
    ("Alert Manager", "Alert Dispatch", "Realization"),
    ("Reporting Engine", "Report Compilation", "Realization"),
    ("Integration Bus", "Message Routing", "Realization"),
    ("Data Warehouse", "Analytics Query", "Realization"),
    # Application serving business
    ("Calibration Ledger", "Evaluate calibration", "Serving"),
    ("Calibration Ledger", "Validate measurement accuracy", "Serving"),
    ("Calibration Ledger", "Compute drift trend", "Serving"),
    ("Calibration Ledger", "Generate calibration certificate", "Serving"),
    ("Dispatch Planner", "Ship customer order", "Serving"),
    ("Dispatch Planner", "Load dispatch vehicle", "Serving"),
    ("Inventory Manager", "Replenish component stock", "Serving"),
    ("Inventory Manager", "Reconcile inventory counts", "Serving"),
    ("Field Service Scheduler", "Field calibration run", "Serving"),
    ("Field Service Scheduler", "Preventive maintenance visit", "Serving"),
    ("Compliance Reporter", "Generate compliance report", "Serving"),
    ("Compliance Reporter", "Prepare regulatory submission", "Serving"),
    ("Supplier Portal", "Onboard new supplier", "Serving"),
    ("Supplier Portal", "Score supplier performance", "Serving"),
    ("Workforce Planner", "Manage workforce schedule", "Serving"),
    ("Quality Monitor", "Test sensor calibration", "Serving"),
    ("Quality Monitor", "Conduct end-of-line test", "Serving"),
    ("Sensor Data Hub", "Water quality sampling", "Serving"),
    ("Sensor Data Hub", "Sensor drift analysis", "Serving"),
    ("Firmware Distribution Service", "Firmware update rollout", "Serving"),
    ("Customer Portal", "Handle customer complaint", "Serving"),
    ("Alert Manager", "Classify incident severity", "Serving"),
    ("Alert Manager", "Escalate unresolved alert", "Serving"),
    # Technology serving application
    ("Event Transport", "Event Relay", "Serving"),
    ("Message Queue", "Event Relay", "Serving"),
    ("Object Storage", "Document Management", "Serving"),
    ("Container Orchestrator", "Event Relay", "Serving"),
    ("Relational Database", "Calibration Ledger", "Serving"),
    ("Relational Database", "Inventory Manager", "Serving"),
    ("Time-Series Database", "Sensor Data Hub", "Serving"),
    ("Search Index", "Document Management", "Serving"),
    ("API Gateway", "Integration Bus", "Serving"),
    ("Secret Store", "Identity Provider", "Serving"),
    ("Log Aggregator", "Monitoring Dashboard", "Serving"),
    ("Metric Collector", "Monitoring Dashboard", "Serving"),
    ("Notification Service", "Alert Manager", "Serving"),
    ("Load Balancer", "API Gateway", "Serving"),
    # Technology realizations
    ("Quay Compute Pool", "Container Orchestrator", "Realization"),
    ("Quay Compute Pool", "Relational Database", "Realization"),
    ("Quay Compute Pool", "Time-Series Database", "Realization"),
    ("Quay Compute Pool", "Message Queue", "Realization"),
    ("Quay Compute Pool", "Object Storage", "Realization"),
    ("Quay Compute Pool", "Search Index", "Realization"),
    ("Fabrication Control Network", "Calibration Rig Controller", "Realization"),
    ("Fulfilment Centre Network", "Barcode Scanner Array", "Realization"),
    ("Field Service Edge", "Handheld Field Tester", "Realization"),
    ("Recovery Lab Network", "Environmental Monitor", "Realization"),
    ("Shared Services Cloud", "API Gateway", "Realization"),
    # System software assignments
    ("Container Runtime", "Container Orchestrator", "Assignment"),
    ("Operating System Image", "Container Runtime", "Assignment"),
    ("Database Engine", "Relational Database", "Assignment"),
    ("Database Engine", "Time-Series Database", "Assignment"),
    ("Message Broker", "Message Queue", "Assignment"),
    ("Web Server", "API Gateway", "Assignment"),
    ("Monitoring Agent", "Metric Collector", "Assignment"),
    ("Backup Agent", "Object Storage", "Assignment"),
    ("Log Shipper", "Log Aggregator", "Assignment"),
    # Motivation associations
    ("Increasing regulatory scrutiny", "Regulatory compliance gap", "Association"),
    ("Ageing field equipment", "Field service efficiency below target", "Association"),
    ("Customer demand for real-time data", "Calibration drift exceeds threshold", "Association"),
    ("Cost pressure on field operations", "Field service efficiency below target", "Association"),
    ("Supply chain volatility", "Inventory accuracy below benchmark", "Association"),
    ("Regulatory compliance gap", "Achieve ISO 17025 accreditation", "Association"),
    ("Field service efficiency below target", "Reduce field service cost per visit", "Association"),
    ("Calibration drift exceeds threshold", "Improve calibration throughput", "Association"),
    ("Inventory accuracy below benchmark", "Achieve 99.5% inventory accuracy", "Association"),
    ("Supplier performance degradation", "Reduce supplier lead time by 20%", "Association"),
    # Strategy realizations
    ("Deploy automated calibration rigs", "Sensor Calibration", "Realization"),
    ("Roll out handheld field testers", "Field Service Management", "Realization"),
    ("Consolidate supplier base", "Supplier Management", "Realization"),
    ("Migrate to cloud-hosted event backbone", "Incident Response", "Realization"),
    ("Implement predictive maintenance", "Asset Lifecycle Management", "Realization"),
    # Implementation compositions
    ("Calibration Automation Phase 1", "Automated Calibration Rig", "Composition"),
    ("Calibration Automation Phase 2", "Digital Twin of Calibration Lab", "Composition"),
    ("Field Tester Rollout", "Deployed Field Tester Fleet", "Composition"),
    ("Field Tester Rollout", "Mobile Field App", "Composition"),
    ("Event Backbone Migration", "Cloud Event Backbone", "Composition"),
    ("Supplier Consolidation Programme", "Consolidated Supplier Panel", "Composition"),
    ("Predictive Maintenance Pilot", "Predictive Model", "Composition"),
    ("Compliance System Upgrade", "Upgraded Compliance Module", "Composition"),
    ("Inventory Accuracy Drive", "Inventory Reconciliation Tool", "Composition"),
    ("Digital Transformation Programme", "Real-time Dashboard", "Composition"),
    ("Quality Excellence Programme", "Automated Test Suite", "Composition"),
    ("Operational Resilience Programme", "Disaster Recovery Runbook", "Composition"),
    # Plateau transitions
    ("Baseline Architecture", "Transition State 1", "Triggering"),
    ("Transition State 1", "Target Architecture", "Triggering"),
    ("Current State", "Future State", "Triggering"),
    # Gap associations
    ("Calibration throughput gap", "Calibration Automation Phase 1", "Association"),
    ("Field service coverage gap", "Field Tester Rollout", "Association"),
    ("Inventory visibility gap", "Inventory Accuracy Drive", "Association"),
    ("Supplier performance gap", "Supplier Consolidation Programme", "Association"),
    ("Event backbone resilience gap", "Event Backbone Migration", "Association"),
    ("Data quality gap", "Digital Transformation Programme", "Association"),
    ("Integration coverage gap", "Digital Transformation Programme", "Association"),
    ("Skills shortage gap", "Quality Excellence Programme", "Association"),
    ("Monitoring coverage gap", "Operational Resilience Programme", "Association"),
    ("Documentation currency gap", "Quality Excellence Programme", "Association"),
    # Additional cross-layer relationships
    ("Sensor Gateway", "Sensor Ingestion", "Serving"),
    ("Calibration Rig Controller", "Evaluate calibration", "Serving"),
    ("Handheld Field Tester", "Field calibration run", "Serving"),
    ("Barcode Scanner Array", "Inventory Reservation", "Serving"),
    ("Environmental Monitor", "Water quality sampling", "Serving"),
    ("Label Printer Station", "Apply calibration label", "Serving"),
    ("Fabrication Workstation", "Assemble sensor module", "Serving"),
    ("Assembly Line Controller", "Test sensor calibration", "Serving"),
    ("Test Chamber Monitor", "Conduct end-of-line test", "Serving"),
    ("Shipping Scale", "Load dispatch vehicle", "Serving"),
    ("Receiving Dock Scanner", "Receive supplier shipment", "Serving"),
    ("Cold Storage Monitor", "Store quarantined materials", "Serving"),
    # Actor assignments
    ("Fabrication Manager", "Fabrication Technician", "Assignment"),
    ("Fulfilment Coordinator", "Fulfilment Operator", "Assignment"),
    ("Field Service Engineer", "Field Technician", "Assignment"),
    ("Recovery Technician", "Quality Inspector", "Assignment"),
    ("Shared Services Director", "IT Operations Lead", "Assignment"),
    ("Ivo Reed", "IT Operations Lead", "Assignment"),
    ("Production Supervisor", "Fabrication Technician", "Assignment"),
    ("Quality Assurance Lead", "Quality Inspector", "Assignment"),
    ("Logistics Planner", "Fulfilment Operator", "Assignment"),
    ("Service Desk Operator", "Customer Support Agent", "Assignment"),
    # Additional business function assignments
    ("Validate measurement accuracy", "Calibration result", "Access"),
    ("Compute drift trend", "Sensor reading", "Access"),
    ("Generate calibration certificate", "Calibration certificate", "Access"),
    ("Authorise batch release", "Batch release record", "Access"),
    ("Assess return condition", "Return authorisation", "Access"),
    ("Determine repair scope", "Field service report", "Access"),
    ("Calculate stock reorder point", "Inventory snapshot", "Access"),
    ("Forecast demand", "Capacity forecast", "Access"),
    ("Score supplier performance", "Supplier evaluation", "Access"),
    ("Monitor water turbidity", "Water quality sample", "Access"),
    ("Detect anomaly in readings", "Anomaly record", "Access"),
    ("Classify incident severity", "Incident ticket", "Access"),
    ("Escalate unresolved alert", "Incident ticket", "Access"),
    # Additional data access
    ("Calibration Lookup", "Calibration history", "Access"),
    ("Sensor Ingestion", "Device telemetry", "Access"),
    ("Inventory Reservation", "Inventory snapshot", "Access"),
    ("Dispatch Scheduling", "Dispatch manifest", "Access"),
    ("Field Assignment", "Maintenance schedule", "Access"),
    ("Compliance Export", "Compliance submission", "Access"),
    ("Supplier Scorecard", "Supplier evaluation", "Access"),
    ("Shift Roster", "Shift assignment", "Access"),
    ("Quality Threshold Check", "Quality incident", "Access"),
    ("Firmware Bundle", "Firmware image", "Access"),
    ("Customer Order Status", "Customer order", "Access"),
    ("Invoice Generation", "Invoice", "Access"),
    ("Dashboard Query", "Usage report", "Access"),
    ("Alert Dispatch", "Incident ticket", "Access"),
    ("Report Compilation", "Cost allocation", "Access"),
    ("Analytics Query", "Capacity forecast", "Access"),
    # Network and security
    ("Network Firewall", "API Gateway", "Serving"),
    ("Intrusion Detection System", "Network Firewall", "Serving"),
    ("VPN Gateway", "Field Service Edge", "Serving"),
    ("Configuration Management Database", "Container Orchestrator", "Serving"),
    ("Service Mesh", "Container Orchestrator", "Serving"),
    ("Distributed Cache", "API Gateway", "Serving"),
    ("Feature Flag Service", "Container Orchestrator", "Serving"),
    ("Circuit Breaker", "API Gateway", "Serving"),
    ("Rate Limiter", "API Gateway", "Serving"),
    ("Audit Trail", "Log Aggregator", "Serving"),
    ("Data Replication Service", "Relational Database", "Serving"),
    ("Schema Registry", "Message Queue", "Serving"),
    ("Workflow Engine", "Integration Bus", "Serving"),
    ("Rules Engine", "Quality Monitor", "Serving"),
    # Additional business process chains
    ("Receive raw materials", "Inspect incoming components", "Triggering"),
    ("Inspect incoming components", "Store quarantined materials", "Triggering"),
    ("Store quarantined materials", "Release materials to production", "Triggering"),
    ("Release materials to production", "Record batch traceability", "Triggering"),
    ("Conduct end-of-line test", "Apply calibration label", "Triggering"),
    ("Apply calibration label", "Load dispatch vehicle", "Triggering"),
    ("Load dispatch vehicle", "Confirm delivery receipt", "Triggering"),
    ("Handle customer complaint", "Investigate root cause", "Triggering"),
    ("Investigate root cause", "Issue corrective action", "Triggering"),
    ("Issue corrective action", "Verify corrective action", "Triggering"),
    ("Verify corrective action", "Close quality case", "Triggering"),
    ("Provision field device", "Firmware update rollout", "Triggering"),
    ("Decommission field device", "Audit calibration records", "Triggering"),
    ("Audit calibration records", "Prepare regulatory submission", "Triggering"),
    ("Review supplier contract", "Negotiate pricing terms", "Triggering"),
    ("Negotiate pricing terms", "Approve purchase order", "Triggering"),
    ("Approve purchase order", "Receive supplier shipment", "Triggering"),
    ("Receive supplier shipment", "Inspect supplier delivery", "Triggering"),
    ("Inspect supplier delivery", "Record supplier non-conformance", "Triggering"),
    ("Record supplier non-conformance", "Rate supplier performance", "Triggering"),
    ("Rate supplier performance", "Terminate supplier relationship", "Triggering"),
    # Additional function assignments
    ("Validate sensor identity", "Sensor reading", "Access"),
    ("Compute measurement uncertainty", "Calibration result", "Access"),
    ("Log calibration event", "Calibration history", "Access"),
    ("Flag out-of-tolerance result", "Quality incident", "Access"),
    ("Schedule recalibration", "Maintenance schedule", "Access"),
    ("Approve instrument for use", "Batch release record", "Access"),
    ("Quarantine failed instrument", "Quality incident", "Access"),
    ("Generate non-conformance report", "Compliance submission", "Access"),
    ("Review trend data", "Calibration history", "Access"),
    ("Adjust control limits", "Calibration result", "Access"),
    ("Record instrument history", "Calibration history", "Access"),
    ("Verify reference standard", "Calibration certificate", "Access"),
    ("Manage calibration schedule", "Maintenance schedule", "Access"),
    ("Allocate calibration resource", "Shift assignment", "Access"),
    ("Close calibration work order", "Field service report", "Access"),
    # Additional actor assignments
    ("Calibration Specialist", "Calibration Approver", "Assignment"),
    ("Inventory Controller", "Inventory Auditor", "Assignment"),
    ("Procurement Officer", "Procurement Reviewer", "Assignment"),
    ("Quality Inspector", "Safety Officer", "Assignment"),
    ("Data Analyst", "Data Steward", "Assignment"),
    ("IT Operations Lead", "System Administrator", "Assignment"),
    ("IT Operations Lead", "Network Engineer", "Assignment"),
    ("Customer Support Agent", "Support Team Lead", "Assignment"),
    ("Production Planner", "Shift Supervisor", "Assignment"),
]

# ── application components (linked to ArchiMate elements) ──────────────────

_APPLICATIONS = [
    # (archimate_element_name, component_name, lifecycle_status, health_status)
    ("Event Relay", "Event Relay", "active", "healthy"),
    ("Calibration Ledger", "Calibration Ledger", "active", "healthy"),
    ("Dispatch Planner", "Dispatch Planner", "active", "healthy"),
    ("Inventory Manager", "Inventory Manager", "active", "healthy"),
    ("Field Service Scheduler", "Field Service Scheduler", "active", "healthy"),
    ("Compliance Reporter", "Compliance Reporter", "active", "healthy"),
    ("Supplier Portal", "Supplier Portal", "active", "healthy"),
    ("Workforce Planner", "Workforce Planner", "active", "healthy"),
    ("Quality Monitor", "Quality Monitor", "active", "healthy"),
    ("Sensor Data Hub", "Sensor Data Hub", "active", "healthy"),
    ("Firmware Distribution Service", "Firmware Distribution Service", "active", "healthy"),
    ("Customer Portal", "Customer Portal", "active", "healthy"),
    ("Finance System", "Finance System", "active", "healthy"),
    ("Document Management", "Document Management", "active", "healthy"),
    ("Identity Provider", "Identity Provider", "active", "healthy"),
    ("Monitoring Dashboard", "Monitoring Dashboard", "active", "healthy"),
    ("Alert Manager", "Alert Manager", "active", "healthy"),
    ("Reporting Engine", "Reporting Engine", "active", "healthy"),
    ("Integration Bus", "Integration Bus", "active", "healthy"),
    ("Data Warehouse", "Data Warehouse", "active", "healthy"),
]

# ── application owners ─────────────────────────────────────────────────────

# Owner users: create a User row for each person named as an owner so the
# ApplicationOwner junction table has valid user_id FKs.
_OWNER_USERS = [
    ("Ivo", "Reed", "ivo.reed@lantern-quay.example.com"),
    ("Morgan", "Calibrator", "morgan.calibrator@lantern-quay.example.com"),
    ("Riley", "Logistics", "riley.logistics@lantern-quay.example.com"),
    ("Casey", "Inventory", "casey.inventory@lantern-quay.example.com"),
    ("Jordan", "FieldEng", "jordan.fieldeng@lantern-quay.example.com"),
    ("Avery", "Compliance", "avery.compliance@lantern-quay.example.com"),
    ("Taylor", "Procurement", "taylor.procurement@lantern-quay.example.com"),
    ("Quinn", "Production", "quinn.production@lantern-quay.example.com"),
    ("Blake", "Quality", "blake.quality@lantern-quay.example.com"),
    ("Drew", "DataAnalyst", "drew.dataanalyst@lantern-quay.example.com"),
    ("Sage", "ITOps", "sage.itops@lantern-quay.example.com"),
    ("Finley", "Support", "finley.support@lantern-quay.example.com"),
    ("Rowan", "SharedSvc", "rowan.sharedsvc@lantern-quay.example.com"),
    ("Ellis", "DataSteward", "ellis.datasteward@lantern-quay.example.com"),
    ("Cameron", "SysAdmin", "cameron.sysadmin@lantern-quay.example.com"),
    ("Ainsley", "NetEng", "ainsley.neteng@lantern-quay.example.com"),
]

# Map from display name (as used in BusinessActor elements) to User email.
_OWNER_NAME_TO_EMAIL = {
    "Ivo Reed": "ivo.reed@lantern-quay.example.com",
    "Calibration Specialist": "morgan.calibrator@lantern-quay.example.com",
    "Logistics Planner": "riley.logistics@lantern-quay.example.com",
    "Inventory Controller": "casey.inventory@lantern-quay.example.com",
    "Field Service Engineer": "jordan.fieldeng@lantern-quay.example.com",
    "Regulatory Compliance Officer": "avery.compliance@lantern-quay.example.com",
    "Procurement Officer": "taylor.procurement@lantern-quay.example.com",
    "Production Supervisor": "quinn.production@lantern-quay.example.com",
    "Quality Assurance Lead": "blake.quality@lantern-quay.example.com",
    "Data Analyst": "drew.dataanalyst@lantern-quay.example.com",
    "IT Operations Lead": "sage.itops@lantern-quay.example.com",
    "Support Team Lead": "finley.support@lantern-quay.example.com",
    "Shared Services Director": "rowan.sharedsvc@lantern-quay.example.com",
    "Data Steward": "ellis.datasteward@lantern-quay.example.com",
    "System Administrator": "cameron.sysadmin@lantern-quay.example.com",
    "Network Engineer": "ainsley.neteng@lantern-quay.example.com",
}

_APP_OWNERS = [
    # (application_name, owner_display_name, ownership_type)
    ("Event Relay", "Ivo Reed", "primary"),
    ("Calibration Ledger", "Calibration Specialist", "primary"),
    ("Dispatch Planner", "Logistics Planner", "primary"),
    ("Inventory Manager", "Inventory Controller", "primary"),
    ("Field Service Scheduler", "Field Service Engineer", "primary"),
    ("Compliance Reporter", "Regulatory Compliance Officer", "primary"),
    ("Supplier Portal", "Procurement Officer", "primary"),
    ("Workforce Planner", "Production Supervisor", "primary"),
    ("Quality Monitor", "Quality Assurance Lead", "primary"),
    ("Sensor Data Hub", "Data Analyst", "primary"),
    ("Firmware Distribution Service", "IT Operations Lead", "primary"),
    ("Customer Portal", "Support Team Lead", "primary"),
    ("Finance System", "Shared Services Director", "primary"),
    ("Document Management", "Data Steward", "primary"),
    ("Identity Provider", "System Administrator", "primary"),
    ("Monitoring Dashboard", "IT Operations Lead", "primary"),
    ("Alert Manager", "Support Team Lead", "primary"),
    ("Reporting Engine", "Data Analyst", "primary"),
    ("Integration Bus", "Network Engineer", "primary"),
    ("Data Warehouse", "Data Analyst", "primary"),
    ("Event Relay", "System Administrator", "technical"),
    ("Calibration Ledger", "System Administrator", "technical"),
    ("Sensor Data Hub", "System Administrator", "technical"),
    ("Integration Bus", "System Administrator", "technical"),
    ("Data Warehouse", "System Administrator", "technical"),
]

# ── capabilities with maturity ─────────────────────────────────────────────

_CAPABILITIES = [
    # (name, code, current_maturity, target_maturity, level)
    ("Sensor Calibration", "LQ-CAP-CALIBRATION", 3, 5, 1),
    ("Water Quality Monitoring", "LQ-CAP-WATER-QUALITY", 4, 5, 1),
    ("Field Service Management", "LQ-CAP-FIELD-SERVICE", 2, 4, 1),
    ("Inventory Control", "LQ-CAP-INVENTORY", 3, 4, 1),
    ("Order Fulfilment", "LQ-CAP-FULFILMENT", 4, 5, 1),
    ("Supplier Management", "LQ-CAP-SUPPLIER", 2, 3, 1),
    ("Regulatory Compliance", "LQ-CAP-COMPLIANCE", 3, 4, 1),
    ("Workforce Planning", "LQ-CAP-WORKFORCE", 2, 3, 1),
    ("Asset Lifecycle Management", "LQ-CAP-ASSET-LIFECYCLE", 2, 4, 1),
    ("Incident Response", "LQ-CAP-INCIDENT", 3, 4, 1),
    ("Demand Forecasting", "LQ-CAP-DEMAND", 1, 3, 1),
    ("Quality Assurance", "LQ-CAP-QUALITY", 4, 5, 1),
    ("Event Processing", "LQ-CAP-EVENT-PROCESSING", 3, 4, 2),
    ("Data Analytics", "LQ-CAP-ANALYTICS", 2, 4, 2),
    ("Integration Management", "LQ-CAP-INTEGRATION", 2, 3, 2),
    ("Identity and Access", "LQ-CAP-IDENTITY", 3, 4, 2),
    ("Document Control", "LQ-CAP-DOCUMENT", 2, 3, 2),
    ("Monitoring and Alerting", "LQ-CAP-MONITORING", 3, 4, 2),
    ("Device Management", "LQ-CAP-DEVICE-MGMT", 2, 3, 3),
    ("Firmware Distribution", "LQ-CAP-FIRMWARE", 2, 3, 3),
    ("Network Operations", "LQ-CAP-NETWORK", 3, 4, 3),
    ("Backup and Recovery", "LQ-CAP-BACKUP", 3, 4, 3),
    ("Capacity Planning", "LQ-CAP-CAPACITY", 2, 3, 3),
    ("Security Operations", "LQ-CAP-SECURITY", 3, 4, 3),
]

# ── risks ──────────────────────────────────────────────────────────────────

_RISKS = [
    # (title, likelihood, impact, status, owner, mitigation, archimate_element_name)
    (
        "Event Relay single point of failure",
        4, 5, "open", "Ivo Reed",
        "Deploy redundant Event Relay instance in secondary region",
        "Event Relay",
    ),
    (
        "Calibration Ledger data corruption",
        2, 5, "mitigated", "Calibration Specialist",
        "Daily verified backups with point-in-time recovery; checksum validation on every write",
        "Calibration Ledger",
    ),
    (
        "Sensor Gateway capacity exceeded",
        3, 4, "open", "IT Operations Lead",
        "Monitor ingestion rate; provision additional gateway instances before 80% threshold",
        "Sensor Gateway",
    ),
    (
        "Regulatory non-compliance finding",
        3, 5, "mitigated", "Regulatory Compliance Officer",
        "Quarterly compliance audits; automated evidence collection",
        "Compliance Reporter",
    ),
    (
        "Supplier sole-source dependency",
        4, 3, "open", "Procurement Officer",
        "Identify and qualify alternate suppliers for critical components",
        "Supplier Portal",
    ),
    (
        "Field device firmware bricking",
        2, 4, "mitigated", "IT Operations Lead",
        "Staged rollout with canary deployment; automatic rollback on health check failure",
        "Firmware Distribution Service",
    ),
    (
        "Inventory data staleness",
        3, 3, "open", "Inventory Controller",
        "Real-time sync from barcode scanners; reconciliation job every four hours",
        "Inventory Manager",
    ),
    (
        "API Gateway overload during peak",
        3, 3, "mitigated", "Network Engineer",
        "Auto-scaling with predictive capacity model; rate limiting per tenant",
        "API Gateway",
    ),
    (
        "Database failover not tested",
        2, 5, "open", "System Administrator",
        "Quarterly failover drills; automated failover with 30-second RTO",
        "Relational Database",
    ),
    (
        "Unauthorised access to calibration records",
        2, 4, "mitigated", "System Administrator",
        "Role-based access control; all access logged to immutable audit trail",
        "Calibration Ledger",
    ),
]

# ── programmes / portfolio initiatives ─────────────────────────────────────

_PROGRAMMES = [
    # (name, code, status, priority, archimate_element_name)
    (
        "Calibration Modernisation",
        "LQ-PGM-CALIBRATION",
        "Active", "Critical",
        "Calibration Automation Phase 1",
    ),
    (
        "Field Service Transformation",
        "LQ-PGM-FIELD-SVC",
        "Active", "High",
        "Field Tester Rollout",
    ),
    (
        "Event Backbone Hardening",
        "LQ-PGM-EVENT-BACKBONE",
        "Active", "High",
        "Event Backbone Migration",
    ),
    (
        "Supplier Optimisation",
        "LQ-PGM-SUPPLIER",
        "Active", "Medium",
        "Supplier Consolidation Programme",
    ),
    (
        "Predictive Operations",
        "LQ-PGM-PREDICTIVE",
        "Proposed", "Medium",
        "Predictive Maintenance Pilot",
    ),
]

# ── work packages ──────────────────────────────────────────────────────────

_WORK_PACKAGES = [
    # (name, status, priority, start_offset_days, duration_days, estimated_cost,
    #  actual_cost, archimate_element_name, capability_name)
    (
        "Deploy automated calibration rigs",
        "in_progress", "critical",
        -90, 180, 450000, 320000,
        "Calibration Automation Phase 1", "Sensor Calibration",
    ),
    (
        "Integrate calibration ledger with rigs",
        "in_progress", "high",
        -60, 120, 180000, 165000,
        "Calibration Automation Phase 1", "Sensor Calibration",
    ),
    (
        "Build digital twin of calibration lab",
        "planned", "high",
        30, 150, 320000, 0,
        "Calibration Automation Phase 2", "Sensor Calibration",
    ),
    (
        "Procure handheld field testers",
        "completed", "high",
        -180, 90, 280000, 275000,
        "Field Tester Rollout", "Field Service Management",
    ),
    (
        "Deploy mobile field app",
        "in_progress", "high",
        -120, 120, 190000, 140000,
        "Field Tester Rollout", "Field Service Management",
    ),
    (
        "Migrate event backbone to cloud",
        "in_progress", "high",
        -45, 90, 210000, 95000,
        "Event Backbone Migration", "Event Processing",
    ),
    (
        "Establish redundant event relay",
        "planned", "critical",
        60, 60, 150000, 0,
        "Event Backbone Migration", "Event Processing",
    ),
    (
        "Consolidate supplier base",
        "in_progress", "medium",
        -30, 120, 95000, 62000,
        "Supplier Consolidation Programme", "Supplier Management",
    ),
    (
        "Implement supplier scorecard",
        "planned", "medium",
        60, 90, 75000, 0,
        "Supplier Consolidation Programme", "Supplier Management",
    ),
    (
        "Build predictive maintenance model",
        "planned", "medium",
        90, 120, 160000, 0,
        "Predictive Maintenance Pilot", "Asset Lifecycle Management",
    ),
    (
        "Upgrade compliance reporting module",
        "in_progress", "high",
        -15, 60, 110000, 48000,
        "Compliance System Upgrade", "Regulatory Compliance",
    ),
    (
        "Deploy inventory reconciliation tool",
        "in_progress", "medium",
        -20, 45, 65000, 38000,
        "Inventory Accuracy Drive", "Inventory Control",
    ),
]

# ── plateaus ───────────────────────────────────────────────────────────────

_PLATEAUS = [
    # (name, sequence_order, archimate_element_name)
    ("Baseline Architecture", 1, "Baseline Architecture"),
    ("Transition State 1", 2, "Transition State 1"),
    ("Target Architecture", 3, "Target Architecture"),
    ("Current State", 1, "Current State"),
    ("Future State", 2, "Future State"),
]

# ── gaps ───────────────────────────────────────────────────────────────────

_GAPS = [
    # (name, gap_kind, severity, archimate_element_name)
    ("Calibration throughput gap", "capability_shortfall", "high", "Calibration throughput gap"),
    ("Field service coverage gap", "capability_shortfall", "high", "Field service coverage gap"),
    ("Inventory visibility gap", "capability_shortfall", "medium", "Inventory visibility gap"),
    ("Supplier performance gap", "capability_shortfall", "medium", "Supplier performance gap"),
    ("Event backbone resilience gap", "capability_shortfall", "critical", "Event backbone resilience gap"),
    ("Data quality gap", "capability_shortfall", "medium", "Data quality gap"),
    ("Integration coverage gap", "capability_shortfall", "medium", "Integration coverage gap"),
    ("Skills shortage gap", "capability_shortfall", "high", "Skills shortage gap"),
    ("Monitoring coverage gap", "capability_shortfall", "medium", "Monitoring coverage gap"),
    ("Documentation currency gap", "capability_shortfall", "low", "Documentation currency gap"),
]


# ═════════════════════════════════════════════════════════════════════════════
#  seed function
# ═════════════════════════════════════════════════════════════════════════════

def _resolve_elements(org_id):
    """Return {name: ArchiMateElement} for all elements in this org."""
    from app.models import ArchiMateElement

    rows = (
        db.session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org_id)
        .all()
    )
    return {r.name: r for r in rows}


def _resolve_apps(org_id):
    """Return {name: ApplicationComponent} for all apps in this org."""
    from app.models.application_portfolio import ApplicationComponent

    rows = (
        db.session.query(ApplicationComponent)
        .filter(ApplicationComponent.organization_id == org_id)
        .all()
    )
    return {r.name: r for r in rows}


def _resolve_caps(org_id):
    """Return {code: UnifiedCapability} for all caps in this org."""
    from app.models.unified_capability import UnifiedCapability

    rows = (
        db.session.query(UnifiedCapability)
        .filter(UnifiedCapability.organization_id == org_id)
        .all()
    )
    return {r.code: r for r in rows}


def _resolve_users(org_id):
    """Return {email: User} for all users in this org."""
    from app.models.user import User

    rows = (
        db.session.query(User)
        .filter(User.organization_id == org_id)
        .all()
    )
    return {r.email: r for r in rows}


def seed_demo_company() -> dict:
    """Create the Lantern Quay Systems demonstration organisation.

    Returns counts keyed by entity kind.  Idempotent: re-running creates
    nothing new and changes nothing.
    """
    from app.models.organization import Organization
    from app.models.user import User, Role
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_owner import ApplicationOwner
    from app.models.unified_capability import UnifiedCapability
    from app.models.risk import Risk, RiskStatus
    from app.models.enterprise_intelligence import PortfolioInitiative
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.implementation_migration import Plateau, Gap
    from app.jobs.tenant_safe_job import tenant_scope
    from werkzeug.security import generate_password_hash

    stats: dict[str, int] = {}

    # ── 1. organisation ─────────────────────────────────────────────────
    org = Organization.query.filter_by(slug=_ORG_SLUG).first()
    if org is None:
        org = Organization(name=_ORG_NAME, slug=_ORG_SLUG, plan="enterprise")
        db.session.add(org)
        db.session.flush()
        stats["organization_created"] = 1
    else:
        stats["organization_created"] = 0
    org_id = org.id
    # Commit the org row before tenant_scope, which calls db.session.remove()
    # and would otherwise detach the org, breaking FK validation downstream.
    db.session.commit()

    with tenant_scope(org_id):
        # ── 2. demo user ────────────────────────────────────────────────
        demo_password = os.environ.get("DEMO_USER_PASSWORD", "")
        existing_user = User.query.filter_by(
            email=_DEMO_USER_EMAIL, organization_id=org_id
        ).first()
        if existing_user is None:
            # Ensure roles are seeded (idempotent).
            Role.insert_roles()
            viewer_role = Role.query.filter_by(name="Viewer").first()
            demo_user = User(
                email=_DEMO_USER_EMAIL,
                first_name=_DEMO_USER_FIRST,
                last_name=_DEMO_USER_LAST,
                password_hash=generate_password_hash(demo_password),
                organization_id=org_id,
                confirmed=True,
                role=viewer_role,
                enterprise_role="enterprise_architect",
            )
            db.session.add(demo_user)
            db.session.flush()
            stats["demo_user_created"] = 1
        else:
            stats["demo_user_created"] = 0

        # ── 3. ArchiMate elements ───────────────────────────────────────
        existing_elements = _resolve_elements(org_id)
        elements_created = 0
        for name, type_, layer in _ELEMENTS:
            if name in existing_elements:
                continue
            el = ArchiMateElement(
                name=name, type=type_, layer=layer, organization_id=org_id,
            )
            db.session.add(el)
            elements_created += 1
        if elements_created:
            db.session.flush()
        stats["elements_created"] = elements_created

        # ── 4. relationships ────────────────────────────────────────────
        elements = _resolve_elements(org_id)
        existing_rels = set()
        for rel in (
            db.session.query(ArchiMateRelationship)
            .filter(ArchiMateRelationship.organization_id == org_id)
            .all()
        ):
            existing_rels.add((rel.source_id, rel.target_id, rel.type))

        rels_created = 0
        for src_name, tgt_name, rel_type in _RELATIONSHIPS:
            src = elements.get(src_name)
            tgt = elements.get(tgt_name)
            if src is None or tgt is None:
                continue
            key = (src.id, tgt.id, rel_type)
            if key in existing_rels:
                continue
            rel = ArchiMateRelationship(
                source_id=src.id,
                target_id=tgt.id,
                type=rel_type,
                organization_id=org_id,
            )
            db.session.add(rel)
            existing_rels.add(key)
            rels_created += 1
        if rels_created:
            db.session.flush()
        stats["relationships_created"] = rels_created

        # ── 5. application components ───────────────────────────────────
        existing_apps = _resolve_apps(org_id)
        apps_created = 0
        for el_name, app_name, lifecycle, health in _APPLICATIONS:
            if app_name in existing_apps:
                continue
            el = elements.get(el_name)
            app = ApplicationComponent(
                name=app_name,
                organization_id=org_id,
                lifecycle_status=lifecycle,
                health_status=health,
                archimate_element_id=el.id if el else None,
                application_code=f"LQ-{app_name.upper().replace(' ', '-')[:20]}",
                implementation_date=_dt.date(2022, 3, 15),
                go_live_date=_dt.date(2022, 6, 1),
                total_cost_of_ownership=85000,
                license_cost_annual=12000,
                infrastructure_cost_monthly=2500,
                business_criticality="high",
                strategic_importance="high",
            )
            db.session.add(app)
            apps_created += 1
        if apps_created:
            db.session.flush()
        stats["applications_created"] = apps_created

        # ── 6. application owners ───────────────────────────────────────
        # Create owner user accounts first.
        owner_password = os.environ.get("DEMO_USER_PASSWORD", "")
        existing_owner_emails = {
            u.email for u in User.query.filter_by(organization_id=org_id).all()
        }
        owner_users_created = 0
        for first, last, email in _OWNER_USERS:
            if email in existing_owner_emails:
                continue
            u = User(
                email=email,
                first_name=first,
                last_name=last,
                password_hash=generate_password_hash(owner_password),
                organization_id=org_id,
                confirmed=True,
                role=viewer_role,
                enterprise_role="enterprise_architect",
            )
            db.session.add(u)
            owner_users_created += 1
        if owner_users_created:
            db.session.flush()

        apps = _resolve_apps(org_id)
        users = _resolve_users(org_id)
        existing_owners = set()
        for ao in (
            db.session.query(ApplicationOwner)
            .filter(ApplicationOwner.organization_id == org_id)
            .all()
        ):
            existing_owners.add((ao.application_id, ao.user_id, ao.ownership_type))

        owners_created = 0
        for app_name, owner_display_name, owner_type in _APP_OWNERS:
            app = apps.get(app_name)
            owner_email = _OWNER_NAME_TO_EMAIL.get(owner_display_name)
            user = users.get(owner_email) if owner_email else None
            if app is None or user is None:
                continue
            key = (app.id, user.id, owner_type)
            if key in existing_owners:
                continue
            ao = ApplicationOwner(
                application_id=app.id,
                user_id=user.id,
                organization_id=org_id,
                ownership_type=owner_type,
            )
            db.session.add(ao)
            existing_owners.add(key)
            owners_created += 1
        if owners_created:
            db.session.flush()
        stats["application_owners_created"] = owners_created

        # ── 7. capabilities ─────────────────────────────────────────────
        existing_caps = _resolve_caps(org_id)
        caps_created = 0
        for name, code, current, target, level in _CAPABILITIES:
            if code in existing_caps:
                continue
            cap = UnifiedCapability(
                name=name,
                code=code,
                organization_id=org_id,
                scope="tenant",
                level=level,
                current_maturity_level=current,
                target_maturity_level=target,
            )
            db.session.add(cap)
            caps_created += 1
        if caps_created:
            db.session.flush()
        stats["capabilities_created"] = caps_created

        # ── 8. risks ────────────────────────────────────────────────────
        existing_risks = {
            r.title
            for r in db.session.query(Risk)
            .filter(Risk.organization_id == org_id)
            .all()
        }
        risks_created = 0
        for title, likelihood, impact, status, owner, mitigation, el_name in _RISKS:
            if title in existing_risks:
                continue
            el = elements.get(el_name)
            risk = Risk(
                title=title,
                description=mitigation,
                likelihood=likelihood,
                impact=impact,
                status=RiskStatus(status),
                owner=owner,
                mitigation_plan=mitigation,
                organization_id=org_id,
                archimate_element_id=el.id if el else None,
            )
            db.session.add(risk)
            risks_created += 1
        if risks_created:
            db.session.flush()
        stats["risks_created"] = risks_created

        # ── 9. programmes ───────────────────────────────────────────────
        existing_progs = {
            p.code
            for p in db.session.query(PortfolioInitiative)
            .filter(PortfolioInitiative.archimate_element_id.in_(
                db.session.query(ArchiMateElement.id).filter(
                    ArchiMateElement.organization_id == org_id
                )
            ))
            .all()
        }
        progs_created = 0
        prog_by_code = {}
        for name, code, status, priority, el_name in _PROGRAMMES:
            if code in existing_progs:
                continue
            el = elements.get(el_name)
            prog = PortfolioInitiative(
                name=name,
                code=code,
                status=status,
                priority=priority,
                archimate_element_id=el.id if el else None,
                strategic_theme="Operational Excellence",
                initiative_type="Strategic",
                start_date=_dt.date(2025, 1, 15),
                target_end_date=_dt.date(2026, 6, 30),
                total_budget=750000,
                spent_to_date=420000,
                executive_sponsor="Chief Operations Officer",
                program_manager="Ivo Reed",
                business_owner_unit="Shared Services",
                health_status="Amber",
                completion_percentage=45,
            )
            db.session.add(prog)
            db.session.flush()
            prog_by_code[code] = prog
            progs_created += 1
        if progs_created:
            db.session.flush()
        stats["programmes_created"] = progs_created

        # ── 10. work packages ───────────────────────────────────────────
        caps = _resolve_caps(org_id)
        existing_wps = {
            wp.name
            for wp in db.session.query(UnifiedWorkPackage)
            .filter(UnifiedWorkPackage.archimate_element_id.in_(
                db.session.query(ArchiMateElement.id).filter(
                    ArchiMateElement.organization_id == org_id
                )
            ))
            .all()
        }
        wps_created = 0
        today = _dt.date.today()
        for (name, status, priority, start_offset, duration,
             estimated, actual, el_name, cap_code) in _WORK_PACKAGES:
            if name in existing_wps:
                continue
            el = elements.get(el_name)
            cap = caps.get(cap_code)
            wp = UnifiedWorkPackage(
                name=name,
                status=status,
                priority=priority,
                start_date=today + _dt.timedelta(days=start_offset),
                end_date=today + _dt.timedelta(days=start_offset + duration),
                duration_days=duration,
                estimated_cost=estimated,
                actual_cost=actual,
                archimate_element_id=el.id if el else None,
                capability_id=cap.id if cap else None,
                business_capability=cap.name if cap else "",
                assigned_to="Ivo Reed",
                scope="enterprise",
            )
            db.session.add(wp)
            wps_created += 1
        if wps_created:
            db.session.flush()
        stats["work_packages_created"] = wps_created

        # ── 11. plateaus ────────────────────────────────────────────────
        existing_plateaus = {
            p.name
            for p in db.session.query(Plateau)
            .filter(Plateau.organization_id == org_id)
            .all()
        }
        plateaus_created = 0
        for name, seq, el_name in _PLATEAUS:
            if name in existing_plateaus:
                continue
            el = elements.get(el_name)
            plateau = Plateau(
                name=name,
                sequence_order=seq,
                organization_id=org_id,
                archimate_element_id=el.id if el else None,
            )
            db.session.add(plateau)
            plateaus_created += 1
        if plateaus_created:
            db.session.flush()
        stats["plateaus_created"] = plateaus_created

        # ── 12. gaps ────────────────────────────────────────────────────
        existing_gaps = {
            g.name
            for g in db.session.query(Gap)
            .filter(Gap.organization_id == org_id)
            .all()
        }
        gaps_created = 0
        for name, kind, severity, el_name in _GAPS:
            if name in existing_gaps:
                continue
            el = elements.get(el_name)
            gap = Gap(
                name=name,
                gap_kind=kind,
                severity=severity,
                organization_id=org_id,
                archimate_element_id=el.id if el else None,
            )
            db.session.add(gap)
            gaps_created += 1
        if gaps_created:
            db.session.flush()
        stats["gaps_created"] = gaps_created

        db.session.commit()

    return stats


# ═════════════════════════════════════════════════════════════════════════════
#  CLI command
# ═════════════════════════════════════════════════════════════════════════════

@click.command("seed-demo-company")
@with_appcontext
def seed_demo_company_command():
    """Create the Lantern Quay Systems demonstration organisation."""
    demo_password = os.environ.get("DEMO_USER_PASSWORD")
    if not demo_password:
        raise click.ClickException(
            "DEMO_USER_PASSWORD environment variable is not set. "
            "Set it to a non-empty value before running this command."
        )

    click.echo(f"  organisation {_ORG_NAME}")

    stats = seed_demo_company()

    for key, value in sorted(stats.items()):
        label = key.replace("_", " ")
        if value:
            click.echo(f"  {label}: {value}")
        else:
            click.echo(f"  {label}: already present")

    total = sum(stats.values())
    click.echo(f"  total rows created: {total}")


def init_app(app):
    app.cli.add_command(seed_demo_company_command)