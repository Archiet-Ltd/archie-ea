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
    ("Calibration Certificate Portal", "WorkPackage", "implementation"),
    ("Engineer Scheduling Assistant", "WorkPackage", "implementation"),
    ("Instrument History Timeline", "WorkPackage", "implementation"),
    ("Shared Telemetry Pipeline", "WorkPackage", "implementation"),
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

# Portfolio profile for each application above: the Abacus lifecycle code the
# Applications tiles count ("2.1 strategic", "2.2 tactical", "3. sunset",
# "5. decommissioned"), the component type, the vendor of record and the one
# capability it primarily enables. Without these the Applications screen shows
# four zero tiles, "Not mapped" on every row and "0 of 20 have a vendor".
# (app name) -> (lifecycle, component_type, vendor, capability code)
_APP_PROFILE = {
    "Event Relay": ("2.1 strategic", "Integration Service", "Confluent", "LQ-CAP-EVENT-PROCESSING"),
    "Calibration Ledger": ("2.1 strategic", "Business Application", "Lantern Quay (in-house)", "LQ-CAP-CALIBRATION"),
    "Dispatch Planner": ("2.2 tactical", "Business Application", "Oracle", "LQ-CAP-FULFILMENT"),
    "Inventory Manager": ("2.2 tactical", "Business Application", "SAP", "LQ-CAP-INVENTORY"),
    "Field Service Scheduler": ("2.1 strategic", "Business Application", "ServiceNow", "LQ-CAP-FIELD-SERVICE"),
    "Compliance Reporter": ("2.1 strategic", "Business Application", "Workiva", "LQ-CAP-COMPLIANCE"),
    "Supplier Portal": ("2.1 strategic", "Business Application", "Coupa", "LQ-CAP-SUPPLIER"),
    "Workforce Planner": ("2.2 tactical", "Business Application", "Workday", "LQ-CAP-WORKFORCE"),
    "Quality Monitor": ("2.1 strategic", "Business Application", "Lantern Quay (in-house)", "LQ-CAP-QUALITY"),
    "Sensor Data Hub": ("2.1 strategic", "Data Platform", "Microsoft", "LQ-CAP-WATER-QUALITY"),
    "Firmware Distribution Service": ("2.2 tactical", "Platform Service", "Lantern Quay (in-house)", "LQ-CAP-ASSET-LIFECYCLE"),
    "Customer Portal": ("2.2 tactical", "Business Application", "Salesforce", "LQ-CAP-FULFILMENT"),
    "Finance System": ("2.2 tactical", "Business Application", "Oracle", "LQ-CAP-DEMAND"),
    "Document Management": ("3. sunset", "Business Application", "OpenText", "LQ-CAP-COMPLIANCE"),
    "Identity Provider": ("2.1 strategic", "Platform Service", "Okta", "LQ-CAP-INTEGRATION"),
    "Monitoring Dashboard": ("2.2 tactical", "Platform Service", "Datadog", "LQ-CAP-INCIDENT"),
    "Alert Manager": ("5. decommissioned", "Platform Service", "PagerDuty", "LQ-CAP-INCIDENT"),
    "Reporting Engine": ("3. sunset", "Data Platform", "Microsoft", "LQ-CAP-ANALYTICS"),
    "Integration Bus": ("2.2 tactical", "Integration Service", "MuleSoft", "LQ-CAP-INTEGRATION"),
    "Data Warehouse": ("2.1 strategic", "Data Platform", "Snowflake", "LQ-CAP-ANALYTICS"),
}

# ── architecture review board queue ────────────────────────────────────────
# (review_number, title, review_type, priority, status, days_since_submitted,
#  days_to_decision or None while undecided, decision or None)
_ARB_REVIEWS = [
    ("LQ-ARB-2026-001", "Migrate the event backbone to a managed message broker",
     "architecture_change", "high", "approved", 62, 11, "approved"),
    ("LQ-ARB-2026-002", "Adopt Okta as the single identity provider",
     "technology_selection", "high", "approved", 55, 8, "approved"),
    ("LQ-ARB-2026-003", "Consolidate supplier onboarding into Supplier Portal",
     "architecture_change", "medium", "approved_with_conditions", 48, 14, "approved_with_conditions"),
    ("LQ-ARB-2026-004", "Retire the manual inventory reconciliation spreadsheet",
     "architecture_change", "low", "rejected", 41, 6, "rejected"),
    ("LQ-ARB-2026-005", "Replace Document Management with a managed records service",
     "technology_selection", "medium", "approved", 33, 9, "approved"),
    ("LQ-ARB-2026-006", "Adopt predictive maintenance for field service",
     "architecture_change", "high", "under_review", 12, None, None),
    ("LQ-ARB-2026-007", "Move the Data Warehouse to a lakehouse pattern",
     "architecture_change", "critical", "under_review", 9, None, None),
    ("LQ-ARB-2026-008", "Standardise API gateway across customer-facing services",
     "standards_exception", "medium", "submitted", 5, None, None),
    ("LQ-ARB-2026-009", "Introduce a vendor risk score into supplier selection",
     "architecture_change", "low", "submitted", 2, None, None),
    ("LQ-ARB-2026-010", "Adopt a single customer identifier across the estate",
     "architecture_change", "high", "under_review", 18, None, None),
    ("LQ-ARB-2026-011", "Exception to the approved database standard for Field Service",
     "standards_exception", "medium", "submitted", 26, None, None),
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
    ("Bryn", "BusinessArch", "bryn.businessarch@lantern-quay.example.com"),
]

# The one demo user who can look but not change the model: a business analyst
# on the Viewer platform role, so the demonstration carries a read-only persona
# beside its sixteen architects. Matches an entry of _OWNER_USERS.
_DEMO_READER_EMAIL = "drew.dataanalyst@lantern-quay.example.com"
_DEMO_READER_PERSONA = "business_analyst"

# The one business architect in the demonstration, so what that persona does and
# does not see (Connections yes, Billing no) can be walked signed in as them.
_DEMO_BUSINESS_ARCHITECT_EMAIL = "bryn.businessarch@lantern-quay.example.com"

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

# ── application cost: the total is derived from its components ─────────────

_APP_LICENSE_ANNUAL = 12000
_APP_INFRASTRUCTURE_MONTHLY = 2500
# The one place the annual total is computed; the applications below are
# created with it and an earlier seed's blanket figure is brought back to it.
_APP_TOTAL_COST = _APP_LICENSE_ANNUAL + 12 * _APP_INFRASTRUCTURE_MONTHLY

# Each application's own annual licence (None where Lantern Quay builds it) and monthly infrastructure
# cost, so the estate's costs differ from one application to the next, and the business area it serves.
# (application) -> (licence per year, infrastructure per month, business area)
_APP_COSTS = {
    "Event Relay": (84000, 3200, "IT Operations"),
    "Calibration Ledger": (None, 2100, "Quality and Compliance"),
    "Dispatch Planner": (96000, 2600, "Operations"),
    "Inventory Manager": (132000, 3400, "Operations"),
    "Field Service Scheduler": (78000, 1200, "Field Services"),
    "Compliance Reporter": (54000, 700, "Quality and Compliance"),
    "Supplier Portal": (66000, 900, "Shared Services"),
    "Workforce Planner": (90000, 1100, "Shared Services"),
    "Quality Monitor": (None, 1500, "Quality and Compliance"),
    "Sensor Data Hub": (48000, 4800, "Field Services"),
    "Firmware Distribution Service": (None, 1900, "Field Services"),
    "Customer Portal": (72000, 1300, "Operations"),
    "Finance System": (168000, 4200, "Shared Services"),
    "Document Management": (36000, 600, "Shared Services"),
    "Identity Provider": (30000, 200, "IT Operations"),
    "Monitoring Dashboard": (42000, 800, "IT Operations"),
    "Alert Manager": (18000, 100, "IT Operations"),
    "Reporting Engine": (24000, 700, "Shared Services"),
    "Integration Bus": (102000, 2400, "IT Operations"),
    "Data Warehouse": (120000, 6100, "IT Operations"),
}


def _app_figures(app_name):
    """(licence, infrastructure per month, total per year, area) for an application."""
    licence, infra, area = _APP_COSTS.get(app_name, (_APP_LICENSE_ANNUAL, _APP_INFRASTRUCTURE_MONTHLY, None))
    return licence, infra, (licence or 0) + 12 * infra, area


# Organisation units that own the applications (one per owner role).
_OWNER_UNITS = [
    # (code, name)
    ("LQ-OU-OPS", "Operations"),
    ("LQ-OU-FIELD", "Field Services"),
    ("LQ-OU-QC", "Quality and Compliance"),
    ("LQ-OU-IT", "IT Operations"),
    ("LQ-OU-SHARED", "Shared Services"),
]
_OWNER_UNIT_OF = {
    "Ivo Reed": "LQ-OU-SHARED",
    "Calibration Specialist": "LQ-OU-OPS",
    "Logistics Planner": "LQ-OU-OPS",
    "Inventory Controller": "LQ-OU-OPS",
    "Field Service Engineer": "LQ-OU-FIELD",
    "Regulatory Compliance Officer": "LQ-OU-QC",
    "Procurement Officer": "LQ-OU-SHARED",
    "Production Supervisor": "LQ-OU-OPS",
    "Quality Assurance Lead": "LQ-OU-QC",
    "Data Analyst": "LQ-OU-IT",
    "IT Operations Lead": "LQ-OU-IT",
    "Support Team Lead": "LQ-OU-FIELD",
    "Shared Services Director": "LQ-OU-SHARED",
    "Data Steward": "LQ-OU-IT",
    "System Administrator": "LQ-OU-IT",
    "Network Engineer": "LQ-OU-IT",
}

# ── capability owners (roles already named as application owners) ──────────

_CAPABILITY_OWNERS = {
    "LQ-CAP-CALIBRATION": "Calibration Specialist",
    "LQ-CAP-WATER-QUALITY": "Quality Assurance Lead",
    "LQ-CAP-FIELD-SERVICE": "Field Service Engineer",
    "LQ-CAP-INVENTORY": "Inventory Controller",
    "LQ-CAP-FULFILMENT": "Logistics Planner",
    "LQ-CAP-SUPPLIER": "Procurement Officer",
    "LQ-CAP-COMPLIANCE": "Regulatory Compliance Officer",
    "LQ-CAP-WORKFORCE": "Production Supervisor",
    "LQ-CAP-ASSET-LIFECYCLE": "Ivo Reed",
    "LQ-CAP-INCIDENT": "Support Team Lead",
    "LQ-CAP-DEMAND": "Logistics Planner",
    "LQ-CAP-QUALITY": "Quality Assurance Lead",
    "LQ-CAP-EVENT-PROCESSING": "IT Operations Lead",
    "LQ-CAP-ANALYTICS": "Data Analyst",
    "LQ-CAP-INTEGRATION": "Network Engineer",
    "LQ-CAP-IDENTITY": "System Administrator",
    "LQ-CAP-DOCUMENT": "Data Steward",
    "LQ-CAP-MONITORING": "IT Operations Lead",
    "LQ-CAP-DEVICE-MGMT": "IT Operations Lead",
    "LQ-CAP-FIRMWARE": "IT Operations Lead",
    "LQ-CAP-NETWORK": "Network Engineer",
    "LQ-CAP-BACKUP": "System Administrator",
    "LQ-CAP-CAPACITY": "IT Operations Lead",
    "LQ-CAP-SECURITY": "System Administrator",
}

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

# Each demo capability sits in a business domain, so the capability heat map has
# rows to compare rather than one "No domain" strip. (code, name) per domain.
_CAPABILITY_DOMAINS = {
    "LQ-FIELD": ("Field Operations", ["CALIBRATION", "WATER-QUALITY", "FIELD-SERVICE", "ASSET-LIFECYCLE", "INCIDENT"]),
    "LQ-SUPPLY": ("Supply and Fulfilment", ["INVENTORY", "FULFILMENT", "SUPPLIER", "DEMAND", "QUALITY"]),
    "LQ-GOVERN": ("Governance and People", ["COMPLIANCE", "WORKFORCE", "DOCUMENT", "IDENTITY"]),
    "LQ-DATA": ("Data and Integration", ["EVENT-PROCESSING", "ANALYTICS", "INTEGRATION", "MONITORING"]),
    "LQ-TECH": ("Technology Operations", ["DEVICE-MGMT", "FIRMWARE", "NETWORK", "BACKUP", "CAPACITY", "SECURITY"]),
}

# The judgment for each demo capability: strategic differentiates the company,
# operational runs it day to day, supporting is what every company needs.
_CAPABILITY_TYPES = {
    "LQ-CAP-CALIBRATION": "strategic",
    "LQ-CAP-WATER-QUALITY": "strategic",
    "LQ-CAP-FIELD-SERVICE": "strategic",
    "LQ-CAP-DEMAND": "strategic",
    "LQ-CAP-ANALYTICS": "strategic",
    "LQ-CAP-EVENT-PROCESSING": "strategic",
    "LQ-CAP-INVENTORY": "operational",
    "LQ-CAP-FULFILMENT": "operational",
    "LQ-CAP-SUPPLIER": "operational",
    "LQ-CAP-ASSET-LIFECYCLE": "operational",
    "LQ-CAP-INCIDENT": "operational",
    "LQ-CAP-QUALITY": "operational",
    "LQ-CAP-INTEGRATION": "operational",
    "LQ-CAP-MONITORING": "operational",
    "LQ-CAP-DEVICE-MGMT": "operational",
    "LQ-CAP-FIRMWARE": "operational",
    "LQ-CAP-NETWORK": "operational",
    "LQ-CAP-COMPLIANCE": "supporting",
    "LQ-CAP-WORKFORCE": "supporting",
    "LQ-CAP-IDENTITY": "supporting",
    "LQ-CAP-DOCUMENT": "supporting",
    "LQ-CAP-BACKUP": "supporting",
    "LQ-CAP-CAPACITY": "supporting",
    "LQ-CAP-SECURITY": "supporting",
}

# ── risks ──────────────────────────────────────────────────────────────────

# (risk title, earlier residual (likelihood, impact), residual now)
_RISING_RISKS = [
    ("Event Relay single point of failure", (3, 4), (4, 5)),
    ("Calibration Ledger data corruption", (2, 4), (4, 4)),
]

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


# ── portfolio initiatives (the Portfolio screen) ───────────────────────────
# The Portfolio screen lists EnterpriseInitiative rows. Invented figures for a
# fictional company.

_INITIATIVES = [
    # (name, code, phase, status, health, budget, spent, forecast, complete %,
    #  sponsor, manager, type, named elements the work packages sit under)
    ("Calibration Modernisation", "LQ-INI-CALIBRATION", "implementation", "active", "yellow",
     750000, 485000, 790000, 55, "Chief Operations Officer", "Ivo Reed", "modernization",
     ("Calibration Automation Phase 1", "Calibration Automation Phase 2")),
    ("Field Service Transformation", "LQ-INI-FIELD-SVC", "implementation", "active", "green",
     420000, 210000, 415000, 50, "Chief Operations Officer", "Jordan FieldEng", "transformation",
     ("Field Tester Rollout",)),
    ("Event Backbone Hardening", "LQ-INI-EVENT-BACKBONE", "design", "active", "red",
     600000, 540000, 720000, 30, "Chief Technology Officer", "Sage ITOps", "migration",
     ("Event Backbone Migration",)),
    ("Supplier Optimisation", "LQ-INI-SUPPLIER", "planning", "planning", "green",
     240000, 30000, 240000, 10, "Chief Financial Officer", "Taylor Procurement", "consolidation",
     ("Supplier Consolidation Programme",)),
    ("Predictive Operations", "LQ-INI-PREDICTIVE", "planning", "planning", "yellow",
     310000, 45000, 330000, 8, "Chief Technology Officer", "Drew DataAnalyst", "transformation",
     ("Predictive Maintenance Pilot",)),
    ("Compliance System Upgrade", "LQ-INI-COMPLIANCE", "testing", "active", "green",
     180000, 150000, 175000, 80, "Chief Operations Officer", "Avery Compliance", "upgrade",
     ("Compliance System Upgrade",)),
    ("Inventory Accuracy Drive", "LQ-INI-INVENTORY", "deployment", "active", "green",
     95000, 88000, 95000, 90, "Chief Operations Officer", "Casey Inventory", "upgrade",
     ("Inventory Accuracy Drive",)),
]

# ── the business model canvas (the Business Model Canvas screen) ───────────

_CANVAS_NAME = "Lantern Quay Systems"
_CANVAS_DESCRIPTION = (
    "How Lantern Quay Systems makes, calibrates, ships and recovers water-quality sensors."
)
_CANVAS_OPERATING_MODEL = "coordination"
# Each box of the canvas lists elements of the model: (name, type, layer). An
# entry that already exists in the seeded model (a process, a resource pool,
# a capability) is found by name and reused, never created twice.
_CANVAS_VISION = ("Recover every sensor assembly for reuse", "Goal", "motivation")
_CANVAS_ENTRIES = {
    "customer_segments": [
        ("Municipal water utilities", "BusinessActor", "business"),
        ("Industrial process-water operators", "BusinessActor", "business"),
        ("Environmental survey firms", "BusinessActor", "business"),
    ],
    "value_propositions": [
        ("Calibrated sensors with a certificate for every batch", "BusinessService", "business"),
        ("Recovered assemblies recertified instead of scrapped", "BusinessService", "business"),
        ("Remote diagnostics that cut site visits", "BusinessService", "business"),
        ("Operators know their readings can be trusted", "Value", "motivation"),
    ],
    "channels": [
        ("Direct account managers", "BusinessInterface", "business"),
        ("Customer portal for orders and returns", "BusinessInterface", "business"),
        ("Field service engineers on site", "BusinessInterface", "business"),
    ],
    "customer_relationships": [
        ("Named service engineer per account", "BusinessService", "business"),
        ("Scheduled preventive maintenance visits", "BusinessService", "business"),
        ("Quarterly drift and compliance reports", "BusinessService", "business"),
    ],
    "key_activities": [
        ("Assemble sensor module", "BusinessProcess", "business"),
        ("Test sensor calibration", "BusinessProcess", "business"),
        ("Recertify recovered assembly", "BusinessProcess", "business"),
    ],
    "key_resources": [
        ("Calibration Rig Pool", "Resource", "strategy"),
        ("Field Engineer Pool", "Resource", "strategy"),
        ("Component stock and supplier panel", "Resource", "strategy"),
    ],
    "key_partners": [
        ("Component suppliers", "BusinessActor", "business"),
        ("Courier and logistics partners", "BusinessActor", "business"),
        ("Regional testing laboratories", "BusinessActor", "business"),
    ],
}
# (service it prices, plan name, price, currency, billing period, mechanism)
_CANVAS_PRICES = [
    ("Calibrated sensors with a certificate for every batch", "Sensor sale", "1850.00", "GBP", "use", "sale"),
    ("Remote diagnostics that cut site visits", "Annual calibration and maintenance", "24000.00", "GBP", "year", "subscription"),
    ("Recovered assemblies recertified instead of scrapped", "Recertified assembly resale", "980.00", "GBP", "use", "sale"),
]
# (source, target, relationship type): what serves, realises or is assigned to what.
_CANVAS_LINKS = [
    ("Assemble sensor module", "Sensor Calibration", "Realization"),
    ("Test sensor calibration", "Sensor Calibration", "Realization"),
    ("Recertify recovered assembly", "Asset Lifecycle Management", "Realization"),
    ("Calibration Rig Pool", "Sensor Calibration", "Assignment"),
    ("Field Engineer Pool", "Field Service Management", "Assignment"),
    ("Calibrated sensors with a certificate for every batch", "Municipal water utilities", "Serving"),
    ("Remote diagnostics that cut site visits", "Industrial process-water operators", "Serving"),
    ("Recovered assemblies recertified instead of scrapped", "Environmental survey firms", "Serving"),
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
    # (name, sequence_order, archimate_element_name, target_date)
    ("Baseline Architecture", 1, "Baseline Architecture", _dt.date(2025, 3, 31)),
    ("Transition State 1", 2, "Transition State 1", _dt.date(2026, 6, 30)),
    ("Target Architecture", 3, "Target Architecture", _dt.date(2027, 3, 31)),
    ("Current State", 1, "Current State", _dt.date(2025, 3, 31)),
    ("Future State", 2, "Future State", _dt.date(2027, 3, 31)),
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

# Where each gap sits and the work package that closes it:
# gap name -> (originating plateau, target plateau, work package name).
_GAP_PLACEMENT = {
    "Calibration throughput gap": ("Baseline Architecture", "Transition State 1", "Deploy automated calibration rigs"),
    "Field service coverage gap": ("Baseline Architecture", "Transition State 1", "Deploy mobile field app"),
    "Inventory visibility gap": ("Baseline Architecture", "Transition State 1", "Deploy inventory reconciliation tool"),
    "Supplier performance gap": ("Baseline Architecture", "Target Architecture", "Implement supplier scorecard"),
    "Event backbone resilience gap": ("Baseline Architecture", "Transition State 1", "Establish redundant event relay"),
    "Data quality gap": ("Current State", "Future State", "Build predictive maintenance model"),
    "Integration coverage gap": ("Current State", "Future State", "Integrate calibration ledger with rigs"),
    "Skills shortage gap": ("Current State", "Future State", "Build digital twin of calibration lab"),
    "Monitoring coverage gap": ("Current State", "Future State", "Migrate event backbone to cloud"),
    "Documentation currency gap": ("Current State", "Future State", "Upgrade compliance reporting module"),
}


# ═════════════════════════════════════════════════════════════════════════════
#  seed function
# ═════════════════════════════════════════════════════════════════════════════

def _seed_canvas_model(org_id, canvas):
    """Fill the canvas from the organisation's model: elements per box, links
    to the seeded capabilities, price plans, and the vision as a Goal.
    Idempotent: every element is found by name and type before one is made."""
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.price_plan import PricePlan
    from app.modules.business_model_canvas import model_service
    from app.services.archimate_backbone import create_backbone_element

    created = 0
    by_key = {
        (e.name, e.type): e
        for e in db.session.query(ArchiMateElement).filter(ArchiMateElement.organization_id == org_id)
    }
    by_name = {}
    for (name, _type), e in by_key.items():
        by_name.setdefault(name, e)

    def element(name, type_, layer):
        nonlocal created
        el = by_key.get((name, type_))
        if el is None:
            el = create_backbone_element(
                element_type=type_, layer=layer, name=name, organization_id=org_id,
                provenance={"created_from": "demo company seed"},
            )
            by_key[(name, type_)] = el
            by_name.setdefault(name, el)
            created += 1
        return el

    model_service.ensure_view(canvas)
    for box, entries in _CANVAS_ENTRIES.items():
        for name, type_, layer in entries:
            el = element(name, type_, layer)
            model_service.place(canvas, box, el)
    vision = element(*_CANVAS_VISION)
    canvas.vision_element_id = vision.id
    db.session.flush()

    for source, target, rel_type in _CANVAS_LINKS:
        src, tgt = by_name.get(source), by_name.get(target)
        if src is None or tgt is None:
            continue
        if not db.session.query(ArchiMateRelationship.id).filter_by(
            source_id=src.id, target_id=tgt.id, type=rel_type, organization_id=org_id
        ).first():
            db.session.add(ArchiMateRelationship(
                source_id=src.id, target_id=tgt.id, type=rel_type, organization_id=org_id,
            ))
            created += 1
    for service_name, plan_name, price, currency, period, mechanism in _CANVAS_PRICES:
        service = by_key.get((service_name, "BusinessService"))
        if service is None or db.session.query(PricePlan.id).filter_by(
            organization_id=org_id, element_id=service.id, name=plan_name
        ).first():
            continue
        db.session.add(PricePlan(
            organization_id=org_id, element_id=service.id, name=plan_name, price=price,
            currency=currency, billing_period=period, pricing_mechanism=mechanism,
        ))
        created += 1
    db.session.flush()
    return created


def _resolve_elements(org_id):
    """Return {name: ArchiMateElement} for all elements in this org; where a work package's own
    element shares a seeded element's name, the first-created (seeded) element wins."""
    from app.models import ArchiMateElement

    rows = (
        db.session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org_id)
        .order_by(ArchiMateElement.id.desc())
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


# ── SAFe kinds for the portfolio Kanban and the Kind filter ────────────────
# (work package element, kanban state, business value, time criticality,
#  risk reduction, job size). Twelve existing work packages become Epics spread
# over the first four columns; Analyzing sits at its limit of three so the
# limit shows. Scores differ so each column has a visible order.
_SAFE_EPICS = [
    ("Digital Transformation Programme", "Funnel", 8, 5, 5, 13),
    ("Sustainability Initiative", "Funnel", 5, 3, 2, 8),
    ("Inventory Accuracy Drive", "Funnel", 3, 5, 5, 5),
    ("Quality Excellence Programme", "Reviewing", 8, 8, 5, 8),
    ("Compliance System Upgrade", "Reviewing", 5, 13, 8, 5),
    ("Supplier Consolidation Programme", "Reviewing", 5, 3, 3, 13),
    ("Operational Resilience Programme", "Analyzing", 13, 8, 13, 13),
    ("Predictive Maintenance Pilot", "Analyzing", 8, 5, 8, 8),
    ("Field Tester Rollout", "Analyzing", 8, 8, 3, 5),
    ("Event Backbone Migration", "Portfolio Backlog", 13, 8, 13, 8),
    ("Calibration Automation Phase 1", "Portfolio Backlog", 13, 13, 5, 8),
    ("Calibration Automation Phase 2", "Portfolio Backlog", 8, 5, 3, 13),
]

# (work package element, kind, business value, time criticality, risk
#  reduction, job size)
_SAFE_OTHER_KINDS = [
    ("Calibration Certificate Portal", "Feature", 8, 5, 3, 5),
    ("Engineer Scheduling Assistant", "Feature", 5, 3, 3, 8),
    ("Instrument History Timeline", "Feature", 3, 2, 2, 3),
    ("Shared Telemetry Pipeline", "Enabler", 5, 3, 8, 8),
]

# ── solutions on the ADM board ─────────────────────────────────────────────
# (name, description, ADM phase, governance status, deployment status,
#  deliverables as (name, delivery status, type))
_SOLUTIONS = [
    (
        "Field Calibration Platform",
        "One platform for scheduling, performing and certifying field calibrations.",
        "B", "proposed", None,
        [
            ("Calibration process model", "in_progress", "business_architecture", "Business Architecture document"),
            ("Stakeholder impact assessment", "planned", "assessment", None),
        ],
    ),
    (
        "Supplier Collaboration Hub",
        "A shared portal where suppliers confirm orders, deliveries and certificates.",
        "D", "in_progress", None,
        [
            ("Integration architecture", "in_progress", "technology_architecture", "Technology Architecture"),
            ("Supplier onboarding runbook", "planned", "runbook", None),
            ("Security review", "planned", "assessment", None),
        ],
    ),
    (
        "Event-Driven Operations Backbone",
        "Replaces point-to-point feeds with one governed event backbone.",
        "F", "approved", "development",
        [
            ("Migration roadmap", "completed", "implementation_plan", "Finalized Implementation Plan"),
            ("Cut-over plan", "in_progress", "implementation_plan", None),
        ],
    ),
]

# ── one value stream, four stages, each supported by capabilities (by code) ─
_VALUE_STREAM = (
    "Calibration Request to Certificate", "LQ-VS-CALIBRATION",
    "From a customer's calibration request to the certificate that closes it.",
    [
        ("Receive Request", ["LQ-CAP-FULFILMENT"]),
        ("Schedule Engineer", ["LQ-CAP-FIELD-SERVICE", "LQ-CAP-WORKFORCE"]),
        ("Calibrate and Test", ["LQ-CAP-CALIBRATION", "LQ-CAP-QUALITY"]),
        ("Certify and Report", ["LQ-CAP-COMPLIANCE", "LQ-CAP-WATER-QUALITY"]),
    ],
)

# ── requirements backlog: (name, description, status, MoSCoW, ADM phase,
#    capability, solution) ──────────────────────────────────────────────────
_BACKLOG_REQUIREMENTS = [
    ("Record the as-found reading for every instrument",
     "The engineer records the reading before any adjustment is made.",
     "Draft", "MUST", "B", "Sensor Calibration", "Field Calibration Platform"),
    ("Show a customer the status of an open request",
     "A customer can see whether a request is received, scheduled or certified.",
     "Draft", "SHOULD", "B", "Order Fulfilment", "Field Calibration Platform"),
    ("Suggest the nearest qualified engineer",
     "Scheduling proposes engineers by location and certification.",
     "Refined", "SHOULD", "C", "Field Service Management", "Field Calibration Platform"),
    ("Suppliers confirm delivery dates in the portal",
     "A supplier confirms or proposes a new delivery date for each order line.",
     "Refined", "MUST", "C", "Supplier Management", "Supplier Collaboration Hub"),
    ("Reject certificates with a missing reference standard",
     "A certificate cannot be issued without the reference standard used.",
     "Approved", "MUST", "D", "Regulatory Compliance", "Supplier Collaboration Hub"),
    ("Retry a failed event delivery three times",
     "A failed delivery is retried and then raised to an operator.",
     "Approved", "COULD", "D", "Event Processing", "Event-Driven Operations Backbone"),
    ("Publish every instrument change as an event",
     "Each change to an instrument is published once to the event backbone.",
     "Implemented", "MUST", "E", "Event Processing", "Event-Driven Operations Backbone"),
    ("Export certificates as signed documents",
     "A certificate downloads as a signed document with its reference number.",
     "Implemented", "SHOULD", "E", "Regulatory Compliance", "Field Calibration Platform"),
]


# Three data domains with the entities, attributes and relationships a data
# architect would draw for a fabrication and fulfilment business. Each entity is
# a DataEntity (and so its ArchiMate data object); nothing here is a second store.
_DATA_MODEL = {
    "Customer": {
        "entities": {
            "Customer": ["customer_id:uuid:pk", "legal_name:varchar", "segment:varchar?", "created_on:date"],
            "Contact": ["contact_id:uuid:pk", "customer_id:uuid:fk", "full_name:varchar", "email:varchar?"],
            "Delivery Address": ["address_id:uuid:pk", "customer_id:uuid:fk", "street:varchar", "postcode:varchar", "country:varchar"],
            "Consent": ["consent_id:uuid:pk", "contact_id:uuid:fk", "purpose:varchar", "given_on:timestamp", "withdrawn_on:timestamp?"],
        },
        "relationships": [
            ("Customer", "Contact", "one-to-many", "has"),
            ("Customer", "Delivery Address", "one-to-many", "ships to"),
            ("Contact", "Consent", "one-to-many", "gives"),
        ],
    },
    "Orders and Fulfilment": {
        "entities": {
            "Sales Order": ["order_id:uuid:pk", "customer_id:uuid:fk", "placed_on:timestamp", "status:varchar"],
            "Order Line": ["line_id:uuid:pk", "order_id:uuid:fk", "product_id:uuid:fk", "quantity:integer", "unit_price:decimal"],
            "Product": ["product_id:uuid:pk", "sku:varchar", "name:varchar", "weight_kg:decimal?"],
            "Shipment": ["shipment_id:uuid:pk", "order_id:uuid:fk", "carrier:varchar", "shipped_on:date?"],
            "Return": ["return_id:uuid:pk", "line_id:uuid:fk", "reason:text", "received_on:date?"],
        },
        "relationships": [
            ("Sales Order", "Order Line", "one-to-many", "contains"),
            ("Product", "Order Line", "one-to-many", "is sold on"),
            ("Sales Order", "Shipment", "one-to-many", "is sent in"),
            ("Order Line", "Return", "one-to-many", "may be returned as"),
        ],
    },
    "Finance": {
        "entities": {
            "Invoice": ["invoice_id:uuid:pk", "order_id:uuid:fk", "issued_on:date", "total:decimal"],
            "Payment": ["payment_id:uuid:pk", "invoice_id:uuid:fk", "paid_on:date", "amount:decimal"],
            "Cost Centre": ["cost_centre_id:varchar:pk", "name:varchar", "owner:varchar?"],
            "Ledger Entry": ["entry_id:bigint:pk", "cost_centre_id:varchar:fk", "invoice_id:uuid:fk?", "posted_on:date", "amount:decimal"],
        },
        "relationships": [
            ("Invoice", "Payment", "one-to-many", "is settled by"),
            ("Cost Centre", "Ledger Entry", "one-to-many", "is charged by"),
            ("Invoice", "Ledger Entry", "one-to-many", "is posted as"),
        ],
    },
}

# What each demonstration entity is: (type, classification, holds personal data, system of record).
_DATA_FACTS = {
    "Customer": ("master", "confidential", True, "Customer Portal"),
    "Contact": ("master", "confidential", True, "Customer Portal"),
    "Delivery Address": ("master", "confidential", True, "Customer Portal"),
    "Consent": ("transactional", "restricted", True, "Customer Portal"),
    "Sales Order": ("transactional", "internal", False, "Dispatch Planner"),
    "Order Line": ("transactional", "internal", False, "Dispatch Planner"),
    "Product": ("master", "internal", False, "Inventory Manager"),
    "Shipment": ("transactional", "internal", False, "Dispatch Planner"),
    "Return": ("transactional", "internal", False, "Dispatch Planner"),
    "Invoice": ("transactional", "confidential", False, "Finance System"),
    "Payment": ("transactional", "confidential", False, "Finance System"),
    "Cost Centre": ("reference", "internal", False, "Finance System"),
    "Ledger Entry": ("transactional", "confidential", False, "Finance System"),
}


def _seed_data_model(org_id) -> dict:
    """Fill the demonstration organisation's data domains so each opens with a
    drawn entity relationship diagram. Idempotent: a domain or entity that
    exists by name is left as it is."""
    from app.models.process_data import DataDomain, DataEntity, DataEntityRelationship
    from app.modules.architecture.services import data_erd_service as erd

    stats = {"data_domains_created": 0, "data_entities_created": 0, "data_relationships_created": 0}
    for domain_name, spec in _DATA_MODEL.items():
        domain = DataDomain.query.filter_by(organization_id=org_id, name=domain_name).first()
        if domain is None:
            domain = DataDomain(name=domain_name, organization_id=org_id,
                                description=f"The {domain_name.lower()} data of Lantern Quay Systems")
            db.session.add(domain)
            db.session.flush()
            stats["data_domains_created"] += 1
        by_name = {}
        for entity_name, columns in spec["entities"].items():
            entity = DataEntity.query.filter_by(
                organization_id=org_id, domain_id=domain.id, name=entity_name).first()
            if entity is None:
                attributes = []
                for column in columns:
                    parts = column.split(":")
                    nullable = parts[-1].endswith("?")
                    parts[-1] = parts[-1].rstrip("?")
                    flags = parts[2:]
                    attributes.append({
                        "name": parts[0], "data_type": parts[1],
                        "is_primary_key": "pk" in flags, "is_foreign_key": "fk" in flags,
                        "is_nullable": nullable,
                    })
                entity = erd.create_entity(domain, entity_name, attributes)
                stats["data_entities_created"] += 1
            by_name[entity_name] = entity
            _fill_entity_facts(entity, org_id)
        for source, target, preset, label in spec["relationships"]:
            exists = DataEntityRelationship.query.filter_by(
                organization_id=org_id, source_entity_id=by_name[source].id,
                target_entity_id=by_name[target].id).first()
            if exists is None:
                erd.relate(by_name[source], by_name[target], preset=preset, label=label)
                stats["data_relationships_created"] += 1
    db.session.flush()
    return stats


def _fill_entity_facts(entity, org_id):
    """Record the entity's type, classification, personal-data flag and system
    of record when they are not recorded yet; never overwrite a person's entry."""
    facts = _DATA_FACTS.get(entity.name)
    if facts is None:
        return
    entity_type, classification, pii, system_name = facts
    if not entity.entity_type:
        entity.entity_type = entity_type
        entity.is_master_data = entity_type == "master"
    if not entity.data_classification:
        entity.data_classification = classification
    if pii and not entity.contains_pii:
        entity.contains_pii = True
    if entity.system_of_record_application_id is None:
        from app.models.application_portfolio import ApplicationComponent

        system = ApplicationComponent.query.filter_by(organization_id=org_id, name=system_name).first()
        if system is not None:
            entity.system_of_record_application_id = system.id
            entity.system_of_record = entity.system_of_record or system.name


# Where the demonstration organisation's customer data goes: which applications
# create, read, update or delete each entity (ArchiMate access relationships,
# the one store for that), the copy into the warehouse (a lineage row), which
# entities hold personal data, the systems of record and the domain stewards.
_DATA_STEWARDS = {
    "Customer": "Customer Operations lead",
    "Orders and Fulfilment": "Fulfilment Operations lead",
    "Finance": "Financial Controller",
    "Analytics": "Head of Data",
}
_PERSONAL_DATA = ("Customer", "Contact", "Delivery Address", "Consent", "Customer Analytics Profile")
_SYSTEMS_OF_RECORD = {"Customer": "Customer Portal", "Invoice": "Finance System", "Sales Order": "Dispatch Planner"}
_DATA_ACCESS = [
    # (application, entity, C/R/U/D letters)
    ("Customer Portal", "Customer", "CRU"),
    ("Customer Portal", "Contact", "CRUD"),
    ("Customer Portal", "Consent", "CRU"),
    ("Customer Portal", "Delivery Address", "CRUD"),
    ("Dispatch Planner", "Customer", "R"),
    ("Dispatch Planner", "Delivery Address", "R"),
    ("Dispatch Planner", "Sales Order", "CRU"),
    ("Finance System", "Customer", "R"),
    ("Finance System", "Invoice", "CRU"),
    ("Finance System", "Payment", "CRU"),
    ("Integration Bus", "Customer", "R"),
    ("Data Warehouse", "Customer Analytics Profile", "CU"),
    ("Reporting Engine", "Customer Analytics Profile", "R"),
]
# (source entity, target entity, kind of copy)
_DATA_COPIES = [("Customer", "Customer Analytics Profile", "Batch")]


def _seed_data_flows(org_id) -> dict:
    """Record where the demonstration organisation's data flows. Idempotent: an
    access relationship or lineage row that exists is left as it is."""
    from app.models.all_missing_models import DataLineage
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import DataDomain, DataEntity
    from app.modules.architecture.services import data_erd_service as erd
    from app.modules.architecture.services.data_architecture_service import record_entity_access

    stats = {"data_accesses_recorded": 0, "data_copies_recorded": 0}
    analytics = DataDomain.query.filter_by(organization_id=org_id, name="Analytics").first()
    if analytics is None:
        analytics = DataDomain(name="Analytics", organization_id=org_id,
                               description="The analytics data of Lantern Quay Systems")
        db.session.add(analytics)
        db.session.flush()
    if DataEntity.query.filter_by(organization_id=org_id, name="Customer Analytics Profile").first() is None:
        erd.create_entity(analytics, "Customer Analytics Profile", [
            {"name": "customer_id", "data_type": "uuid", "is_primary_key": True, "is_foreign_key": False, "is_nullable": False},
            {"name": "segment", "data_type": "varchar", "is_primary_key": False, "is_foreign_key": False, "is_nullable": True},
            {"name": "lifetime_value", "data_type": "decimal", "is_primary_key": False, "is_foreign_key": False, "is_nullable": True},
        ])
    for domain in DataDomain.query.filter_by(organization_id=org_id).all():
        if domain.name in _DATA_STEWARDS and not domain.data_steward:
            domain.data_steward = _DATA_STEWARDS[domain.name]
    entities = {e.name: e for e in DataEntity.query.filter_by(organization_id=org_id).all()}
    apps = {a.name: a for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()}
    for name in _PERSONAL_DATA:
        if name in entities:
            entities[name].contains_pii = True
            entities[name].data_classification = entities[name].data_classification or "confidential"
    for entity_name, app_name in _SYSTEMS_OF_RECORD.items():
        entity, app = entities.get(entity_name), apps.get(app_name)
        if entity is not None and app is not None and entity.system_of_record_application_id is None:
            entity.system_of_record_application_id = app.id
            entity.system_of_record = app.name
    db.session.flush()
    for app_name, entity_name, letters in _DATA_ACCESS:
        entity, app = entities.get(entity_name), apps.get(app_name)
        if entity is None or app is None or not entity.archimate_element_id or not app.archimate_element_id:
            continue
        record_entity_access(entity, app, list(letters))
        stats["data_accesses_recorded"] += 1
    for source_name, target_name, kind in _DATA_COPIES:
        source, target = entities.get(source_name), entities.get(target_name)
        if source is None or target is None or not source.archimate_element_id or not target.archimate_element_id:
            continue
        exists = DataLineage.query.filter_by(
            organization_id=org_id, archimate_element_id=source.archimate_element_id,
            target_archimate_element_id=target.archimate_element_id).first()
        if exists is None:
            db.session.add(DataLineage(
                name=f"{source.name} -> {target.name}", organization_id=org_id,
                archimate_element_id=source.archimate_element_id,
                target_archimate_element_id=target.archimate_element_id,
                lineage_type=kind, source_system="Customer Portal", target_system="Data Warehouse"))
            stats["data_copies_recorded"] += 1
    db.session.flush()
    return stats


def _seed_boards(org_id, demo_user_id) -> dict:
    """SAFe kinds, ADM-board solutions, a value stream and a requirements
    backlog for the demonstration organisation.

    Idempotent. A work package that already carries a kind is left alone, so
    a card an architect has moved is not put back by a re-run.
    """
    from app.models.adm_deliverable import ADMDeliverable
    from app.models.business_capabilities import BusinessCapability
    from app.models.implementation_migration import Deliverable
    from app.models.solution_architect_models import (
        RequirementType, SolutionRequirement,
    )
    from app.models.solution_models import Solution, solution_deliverables
    from app.models.unified_capability import (
        CapabilityValueStreamMapping, ValueStream, ValueStreamStage,
    )
    from app.modules.capabilities.services import value_stream_service
    from app.services.adm_deliverable_service import seed_deliverables
    from app.services.element_profile_service import (
        read_profile, set_kind, set_kind_property, set_wsjf_components,
    )

    stats = {
        "safe_kinds_set": 0,
        "solutions_created": 0,
        "solution_deliverables_created": 0,
        "value_streams_created": 0,
        "value_stream_stages_created": 0,
        "stage_capabilities_linked": 0,
        "backlog_requirements_created": 0,
    }

    # SAFe kinds
    elements = _resolve_elements(org_id)
    for name, state, bv, tc, rr, js in _SAFE_EPICS:
        el = elements.get(name)
        if el is None or read_profile(el).get("kind"):
            continue
        set_kind(el, "Epic", source="seed")
        set_kind_property(el, "kanban_state", state, source="seed")
        set_wsjf_components(el, bv, tc, rr, js, source="seed")
        stats["safe_kinds_set"] += 1
    for name, kind, bv, tc, rr, js in _SAFE_OTHER_KINDS:
        el = elements.get(name)
        if el is None or read_profile(el).get("kind"):
            continue
        set_kind(el, kind, source="seed")
        set_wsjf_components(el, bv, tc, rr, js, source="seed")
        stats["safe_kinds_set"] += 1
    db.session.flush()

    # Solutions, each with deliverables matched to the phase's TOGAF checklist
    seed_deliverables()
    templates = {
        (t.phase, t.name): t.id for t in db.session.query(ADMDeliverable).filter_by(is_template=True)
    }
    solutions = {
        s.name: s
        for s in db.session.query(Solution).filter(Solution.organization_id == org_id)
    }
    for name, desc, phase, gov, deploy, deliverables in _SOLUTIONS:
        if name in solutions:
            # Seeded before deliverables carried a checklist item: match them now.
            for d_name, _s, _t, d_template in deliverables:
                if d_template is None:
                    continue
                for existing in (
                    db.session.query(Deliverable)
                    .join(solution_deliverables,
                          solution_deliverables.c.deliverable_id == Deliverable.id)
                    .filter(solution_deliverables.c.solution_id == solutions[name].id,
                            Deliverable.name == d_name,
                            Deliverable.adm_template_id.is_(None))
                ):
                    existing.adm_template_id = templates.get((phase, d_template))
            continue
        sol = Solution(
            name=name, description=desc, organization_id=org_id,
            adm_phase=phase, governance_status=gov, deployment_status=deploy,
            complexity_level="medium", solution_owner="Ivo Reed",
            created_by_id=demo_user_id,
        )
        db.session.add(sol)
        db.session.flush()
        solutions[name] = sol
        stats["solutions_created"] += 1
        for d_name, d_status, d_type, d_template in deliverables:
            deliverable = Deliverable(
                name=d_name, organization_id=org_id,
                delivery_status=d_status, deliverable_type=d_type,
                adm_template_id=templates.get((phase, d_template)),
            )
            db.session.add(deliverable)
            db.session.flush()
            db.session.execute(
                solution_deliverables.insert().values(
                    solution_id=sol.id, deliverable_id=deliverable.id)
            )
            stats["solution_deliverables_created"] += 1
    db.session.flush()

    # Value stream with four stages mapped to capabilities
    vs_name, vs_code, vs_desc, stages = _VALUE_STREAM
    vs = (
        db.session.query(ValueStream)
        .filter(ValueStream.organization_id == org_id, ValueStream.code == vs_code)
        .first()
    )
    if vs is None:
        vs = value_stream_service.create_value_stream({
            "name": vs_name, "code": vs_code, "description": vs_desc,
            "value_stream_type": "customer_facing",
        })
        stats["value_streams_created"] += 1
    vs_id = vs.id
    unified = _resolve_caps(org_id)
    for order, (stage_name, cap_codes) in enumerate(stages, start=1):
        stage = (
            db.session.query(ValueStreamStage)
            .filter(ValueStreamStage.value_stream_id == vs_id,
                    ValueStreamStage.name == stage_name)
            .first()
        )
        if stage is None:
            stage = value_stream_service.create_stage(
                vs_id, {"name": stage_name, "stage_order": order})
            stats["value_stream_stages_created"] += 1
        mapped = {
            m.capability_id
            for m in db.session.query(CapabilityValueStreamMapping)
            .filter(CapabilityValueStreamMapping.value_stream_stage_id == stage.id)
        }
        for code in cap_codes:
            cap = unified.get(code)
            if cap is None or cap.id in mapped:
                continue
            value_stream_service.upsert_mapping_cell(
                cap.id, vs_id, stage.id,
                {"support_type": "primary", "support_level": 4, "impact_level": "high"},
            )
            stats["stage_capabilities_linked"] += 1

    # Requirements name the capability they serve by its name
    caps = {
        c.name: c
        for c in db.session.query(BusinessCapability)
        .filter(BusinessCapability.organization_id == org_id)
    }
    # Requirements backlog
    existing = {
        r.name
        for r in db.session.query(SolutionRequirement)
        .filter(SolutionRequirement.organization_id == org_id)
    }
    for name, desc, status, moscow, phase, cap_name, sol_name in _BACKLOG_REQUIREMENTS:
        if name in existing:
            continue
        cap = caps.get(cap_name)
        sol = solutions.get(sol_name)
        db.session.add(SolutionRequirement(
            name=name, description=desc, status=status, moscow_priority=moscow,
            togaf_phase=phase, requirement_type=RequirementType.FUNCTIONAL,
            organization_id=org_id, owner="Ivo Reed",
            capability_id=cap.id if cap else None,
            solution_id=sol.id if sol else None,
        ))
        stats["backlog_requirements_created"] += 1
    db.session.flush()
    stats.update(_seed_solution_blueprints(org_id, solutions))
    stats["solution_risks_created"] = _seed_solution_risks(org_id, solutions)
    return stats


# ── the blueprint of each seeded solution ─────────────────────────────────
# The fifteen blueprint sections are filled from the seeded estate, not typed
# in as figures: a narrative where the section is prose (summary, decisions),
# and links to the seeded elements, capabilities, applications, value stream
# and requirements where it is a view. The completeness, the governance state
# and the compliance figure are then read back from the blueprint's own
# scoring, so they cannot disagree with what is written.
#
# (name, type, layer, description): elements the blueprint needs that the
# estate did not already have.
_BLUEPRINT_NEW_ELEMENTS = [
    ("Certificate issued within the agreed time", "Outcome", "strategy",
     "A customer holds a signed calibration certificate within the turnaround agreed when the request was made."),
    ("Field calibration service", "BusinessService", "business",
     "The service a customer buys: an engineer visits, calibrates the instrument on site and certifies the result."),
    ("Calibration Ledger API", "ApplicationInterface", "application",
     "The interface the field tester and the scheduler use to read an instrument's history and write a new reading."),
    ("Field Service Edge Network", "CommunicationNetwork", "technology",
     "The mobile and site network that connects handheld testers to the ledger when an engineer is at a customer."),
    ("Tester to Ledger Path", "Path", "technology",
     "The encrypted path a handheld tester uses to send readings; it queues readings when the network is lost."),
    ("Calibration rig go-live", "ImplementationEvent", "implementation",
     "The date the first automated calibration rig starts certifying customer instruments."),
    ("Supplier order confirmation", "BusinessService", "business",
     "A supplier confirms, changes or rejects an order line and the buyer sees the answer the same day."),
    ("Supplier Portal API", "ApplicationInterface", "application",
     "The interface suppliers use to confirm delivery dates and upload certificates."),
    ("Event backbone cut-over", "ImplementationEvent", "implementation",
     "The weekend on which point-to-point feeds are switched off and every system publishes to the event backbone."),
]

# Plain descriptions for seeded elements the blueprints use. Only an element
# with no description of its own gets one.
_BLUEPRINT_DESCRIPTIONS = {
    "Regulatory Compliance Officer": "Signs off that every certificate meets the accreditation body's rules.",
    "Increasing regulatory scrutiny": "Inspectors now ask for the reference standard behind each reading, so records must be complete and traceable.",
    "Improve calibration throughput": "Certify more instruments per engineer per week without lowering the accuracy checks.",
    "Calibration records must be immutable": "A reading, once written, is never changed; a correction is a new, linked record.",
    "Field devices must operate offline for 72 hours": "A handheld tester keeps recording readings with no signal for three days and sends them when it reconnects.",
    "Customer data must be encrypted at rest": "Customer names, sites and instrument details are encrypted wherever they are stored.",
    "System must support 500 concurrent field devices": "Five hundred testers can send readings at the same time without delay to any of them.",
    "Must operate within existing network segments": "No new network zone is added; the platform uses the segments the security team already monitors.",
    "Field calibration run": "An engineer visits a site, checks each instrument against the reference standard and records the as-found and as-left readings.",
    "Field Service Engineer": "The person who travels to the customer and performs the calibration.",
    "Calibration Ledger": "The system of record for every instrument, every reading and every certificate issued.",
    "Calibration Lookup": "Returns an instrument's full calibration history from its serial number.",
    "Field Service Scheduler": "Proposes which engineer visits which site, using location and certification.",
    "Calibration result": "The as-found and as-left readings, the reference standard used and the pass or fail outcome.",
    "Sensor reading": "One measured value from an instrument, with the time and the device that took it.",
    "Quay Compute Pool": "The shared compute cluster that runs the ledger and the scheduler.",
    "Field Service Edge": "The edge site where handheld testers synchronise before data reaches the central cluster.",
    "Handheld Field Tester": "The rugged tablet-sized device an engineer carries to record readings on site.",
    "Container Runtime": "Runs each platform service in its own container so a fault stays inside one service.",
    "Calibration throughput gap": "Today an engineer certifies about half as many instruments a week as the target.",
    "Baseline Architecture": "Paper job sheets, a spreadsheet of instruments and certificates typed by hand.",
    "Target Architecture": "One ledger, tester readings captured on site and certificates generated automatically.",
    "Calibration Automation Phase 1": "Capture readings on the handheld tester and write them straight to the ledger.",
    "Calibration Automation Phase 2": "Generate and sign the certificate from the ledger without retyping.",
    "Automated Calibration Rig": "A bench rig that runs the standard test sequence and records every reading itself.",
    "Sensor Calibration": "Checking an instrument against a reference standard and certifying the result.",
    "Supplier Management": "Choosing, onboarding and measuring the suppliers the business relies on.",
    "Supplier Portal": "Where suppliers confirm orders, delivery dates and certificates.",
    "Procurement Officer": "Raises orders and chases suppliers for confirmation.",
    "Onboard new supplier": "Collects a new supplier's details, certificates and bank information before first order.",
    "Supplier evaluation": "A supplier's delivery and quality record for the period.",
    "Supplier performance gap": "Late and unconfirmed deliveries are found only when production stops.",
    "Consolidate supplier base": "Reduce the number of suppliers for each component to two approved sources.",
    "Supplier Consolidation Programme": "Moves suppliers onto the portal and retires the paper onboarding pack.",
    "Consolidated Supplier Panel": "The approved list of suppliers per component category.",
    "Reduce supplier lead time by 20%": "Shorten the time from order to delivery by a fifth.",
    "Supply chain volatility": "Delivery dates move often, so planners need to see confirmations as they arrive.",
    "Event Relay": "Delivers each event once to every system that subscribed to it.",
    "Event Delivery": "The service that publishes an event and guarantees every subscriber receives it.",
    "Event Transport": "The message transport that carries events between sites.",
    "Event backbone resilience gap": "The relay runs in one region, so a regional outage stops every feed.",
    "Migrate to cloud-hosted event backbone": "Move the relay to a managed service running in two regions.",
    "Event Backbone Migration": "Moves each feed from a point-to-point link onto the backbone, one system at a time.",
    "Cloud Event Backbone": "The managed event service every system publishes to.",
    "No single point of failure in event path": "Every part of the event path has a second instance in a second region.",
    "Failures must be visible within five minutes": "An operator sees a failed delivery within five minutes of it happening.",
    "Recovery time objective of four hours": "After an outage the event path is working again within four hours.",
    "Integration Bus": "The existing bus that carries the point-to-point feeds the backbone replaces.",
    "Cost pressure on field operations": "Each field visit costs more than customers will pay, so every wasted trip matters.",
    "Reduce field service cost per visit": "Cut the cost of a visit by sending the right engineer once.",
}

_FCP_SPEC = {
    "fields": [
        {"name": "instrument_serial", "type": "string", "description": "Serial number printed on the instrument."},
        {"name": "reading_value", "type": "decimal", "description": "The measured value in the instrument's unit."},
        {"name": "reference_standard", "type": "string", "description": "The standard the reading was checked against."},
        {"name": "recorded_at", "type": "timestamp", "description": "When the engineer recorded the reading."},
    ],
    "integration": {
        "protocol": "REST", "auth_method": "OAuth2",
        "retry_policy": {"attempts": 5, "backoff": "exponential"},
        "sla": {"max_latency_ms": 800},
        "description": "Handheld testers send readings to the ledger and retry when the network drops.",
    },
    "deployment": {
        "runtime": "python3.11", "framework": "fastapi", "database": "postgresql",
        "cache": "redis", "messaging": "none", "container_runtime": "kubernetes",
    },
}


def _spec_hash(value):
    import hashlib
    import json as _json
    return hashlib.sha256(_json.dumps(value, sort_keys=True).encode()).hexdigest()


# What each element type carries once its specification is confirmed: an
# application element its fields and interface, a data object its integration
# contract, a technology element its deployment; a component carries all three.
_SPEC_PARTS = {
    "ApplicationComponent": ("fields", "integration", "deployment"),
    "ApplicationService": ("fields",),
    "ApplicationInterface": ("fields",),
    "DataObject": ("integration",),
    "Node": ("deployment",),
    "Device": ("deployment",),
    "SystemSoftware": ("deployment",),
}


def _confirmed_spec(spec, element_type):
    """The confirmed specification for an element of this type, each part
    carrying a version and a hash of what was confirmed; None when the
    element type carries no specification."""
    parts = _SPEC_PARTS.get(element_type)
    if not parts:
        return None
    out = {}
    if "fields" in parts:
        out.update({
            "fields": spec["fields"], "fields_status": "confirmed",
            "contract_status": "confirmed", "fields_version": 1,
            "fields_hash": _spec_hash(spec["fields"]),
        })
    if "integration" in parts:
        out["integrations"] = {"tester-to-ledger": dict(
            spec["integration"], status="confirmed", version=1,
            contract_hash=_spec_hash(spec["integration"]),
        )}
    if "deployment" in parts:
        out.update({
            "deployment": spec["deployment"], "deployment_status": "confirmed",
            "deployment_version": 1, "deployment_hash": _spec_hash(spec["deployment"]),
        })
    return out


# Per solution: the narrative sections, the elements each view section links,
# the relationships that join them, and which linked component carries the
# confirmed specification.
_BLUEPRINTS = {
    "Field Calibration Platform": {
        "narratives": {
            "executive_summary": (
                "Lantern Quay certifies customer instruments by sending an engineer to site with paper job sheets. "
                "Readings are retyped into a spreadsheet at the depot and the certificate follows days later, so an "
                "engineer completes about half the instruments a week that the target needs, and inspectors now ask "
                "for the reference standard behind every reading. The Field Calibration Platform replaces that with "
                "one calibration ledger. The engineer records the as-found and as-left readings on a handheld tester "
                "that works offline for three days, the scheduler proposes the nearest qualified engineer, and the "
                "certificate is generated from the ledger rather than retyped. We expect throughput to rise by a "
                "third in the first year, certificates to reach customers within the agreed turnaround, and every "
                "reading to be traceable to a national standard for the inspector."
            ),
            "architecture_decisions": (
                "We chose one calibration ledger as the system of record instead of keeping a spreadsheet per depot, "
                "because inspectors need a single, unaltered history for each instrument. Readings are immutable: a "
                "correction is a new record linked to the old one, which keeps the audit trail intact. The handheld "
                "tester stores readings locally and sends them when it reconnects, rather than requiring a live "
                "connection, because engineers often work in plant rooms with no signal. We rejected buying a "
                "packaged calibration suite because it could not record the reference standard per reading and "
                "would have forced a second system for certificates. The platform runs on the existing compute pool "
                "and network segments so the security team does not take on a new zone. Each decision is recorded "
                "with its rejected options in the decision register."
            ),
        },
        "links": {
            "vision_motivation": ["Regulatory Compliance Officer", "Increasing regulatory scrutiny",
                                  "Improve calibration throughput", "Calibration records must be immutable"],
            "value_stream_map": ["Calibration Request to Certificate", "Sensor Calibration",
                                 "Certificate issued within the agreed time"],
            "business_process_view": ["Field calibration run", "Field Service Engineer", "Field calibration service"],
            "application_cooperation": ["Calibration Ledger", "Calibration Lookup", "Calibration Ledger API",
                                        "Field Service Scheduler"],
            "data_information": ["Calibration result", "Sensor reading"],
            "deployment_view": ["Quay Compute Pool", "Handheld Field Tester", "Container Runtime"],
            "network_communication": ["Field Service Edge Network", "Tester to Ledger Path", "Field Service Edge"],
            "gap_analysis": ["Calibration throughput gap", "Baseline Architecture", "Target Architecture"],
            "transition_roadmap": ["Baseline Architecture", "Target Architecture", "Calibration Automation Phase 1"],
            "work_packages": ["Calibration Automation Phase 1", "Calibration Automation Phase 2",
                              "Automated Calibration Rig", "Calibration rig go-live"],
            "security_viewpoint": ["Must operate within existing network segments",
                                   "Customer data must be encrypted at rest"],
            "nfr_satisfaction": ["System must support 500 concurrent field devices",
                                 "Field devices must operate offline for 72 hours"],
            "requirements_traceability": ["Calibration records must be immutable", "Calibration Automation Phase 1",
                                          "Calibration Automation Phase 2"],
        },
        "relationships": [
            ("Calibration Request to Certificate", "Improve calibration throughput", "realization"),
            ("Calibration Request to Certificate", "Sensor Calibration", "association"),
            ("Calibration Request to Certificate", "Certificate issued within the agreed time", "realization"),
            ("Regulatory Compliance Officer", "Increasing regulatory scrutiny", "association"),
            ("Increasing regulatory scrutiny", "Improve calibration throughput", "influence"),
            ("Calibration records must be immutable", "Improve calibration throughput", "realization"),
            ("Field Service Engineer", "Field calibration run", "assignment"),
            ("Field calibration run", "Field calibration service", "realization"),
            ("Calibration Ledger", "Calibration Lookup", "realization"),
            ("Calibration Ledger", "Calibration Ledger API", "composition"),
            ("Field Service Scheduler", "Calibration Ledger API", "serving"),
            ("Calibration Ledger", "Calibration result", "access"),
            ("Calibration Ledger", "Sensor reading", "access"),
            ("Quay Compute Pool", "Container Runtime", "composition"),
            ("Handheld Field Tester", "Tester to Ledger Path", "association"),
            ("Field Service Edge Network", "Tester to Ledger Path", "composition"),
            ("Field Service Edge", "Field Service Edge Network", "association"),
            ("Calibration throughput gap", "Baseline Architecture", "association"),
            ("Calibration throughput gap", "Target Architecture", "association"),
            ("Baseline Architecture", "Target Architecture", "triggering"),
            ("Calibration Automation Phase 1", "Target Architecture", "realization"),
            ("Calibration Automation Phase 1", "Calibration Automation Phase 2", "triggering"),
            ("Calibration Automation Phase 2", "Automated Calibration Rig", "realization"),
            ("Calibration Automation Phase 2", "Calibration rig go-live", "triggering"),
            ("Must operate within existing network segments", "Customer data must be encrypted at rest", "association"),
            ("System must support 500 concurrent field devices", "Field devices must operate offline for 72 hours", "association"),
            ("Calibration Automation Phase 1", "Calibration records must be immutable", "realization"),
        ],
        "spec": _FCP_SPEC,
        "capabilities": ["Sensor Calibration", "Field Service Management", "Order Fulfilment"],
    },
    "Supplier Collaboration Hub": {
        "narratives": {
            "executive_summary": (
                "Purchasing at Lantern Quay chases suppliers by email and phone. Delivery dates change without "
                "notice, certificates arrive as attachments that nobody files, and a late delivery is usually found "
                "only when a production line stops. The Supplier Collaboration Hub gives every supplier a portal "
                "where they confirm each order line, propose a new date and upload the certificate that goes with "
                "it. Buyers see the answer the same day, planners see which orders are unconfirmed, and quality can "
                "reject a delivery whose certificate is missing the reference standard. The first release covers "
                "the twenty suppliers who deliver sensor components. We expect confirmed delivery dates for nine "
                "orders in ten and a fifth off average lead time once the consolidated supplier panel is in place."
            ),
            "architecture_decisions": (
                "Suppliers use the existing Supplier Portal rather than a new application, because it already "
                "holds their identities and the order history, and adding a second login would have split the "
                "record. Order confirmation is exposed through an interface so a large supplier can connect its own "
                "system instead of typing into the portal. We decided that a certificate without a reference "
                "standard is rejected at upload, not at goods-in, so the problem reaches the supplier while the "
                "order is still open. We rejected emailing a daily spreadsheet because nobody owned the reply. "
                "Supplier performance is calculated from confirmed and delivered dates held in the portal, and "
                "that one calculation feeds the scorecard buyers use, so there is a single figure to argue about."
            ),
        },
        "links": {
            "vision_motivation": ["Procurement Officer", "Supply chain volatility", "Reduce supplier lead time by 20%"],
            "business_process_view": ["Onboard new supplier", "Procurement Officer", "Supplier order confirmation"],
            "application_cooperation": ["Supplier Portal", "Supplier Portal API"],
            "data_information": ["Supplier evaluation", "Supplier Portal"],
            "gap_analysis": ["Supplier performance gap", "Baseline Architecture", "Target Architecture"],
            "work_packages": ["Supplier Consolidation Programme", "Consolidated Supplier Panel"],
        },
        "capabilities": ["Supplier Management", "Regulatory Compliance"],
        "relationships": [
            ("Supply chain volatility", "Reduce supplier lead time by 20%", "influence"),
            ("Procurement Officer", "Onboard new supplier", "assignment"),
            ("Onboard new supplier", "Supplier order confirmation", "realization"),
            ("Supplier Portal", "Supplier Portal API", "composition"),
            ("Supplier Portal", "Supplier evaluation", "access"),
            ("Supplier performance gap", "Baseline Architecture", "association"),
            ("Supplier performance gap", "Target Architecture", "association"),
            ("Supplier Consolidation Programme", "Consolidated Supplier Panel", "realization"),
        ],
    },
    "Event-Driven Operations Backbone": {
        "narratives": {
            "executive_summary": (
                "Operations systems at Lantern Quay talk to each other through dozens of point-to-point feeds. "
                "When one feed breaks, nobody knows until a downstream team notices missing data, and the event "
                "relay that carries the most important events runs in a single region. The Event-Driven Operations "
                "Backbone replaces those feeds with one governed backbone: each system publishes a change once and "
                "every interested system receives it, with failed deliveries retried and then raised to an "
                "operator within five minutes. The backbone runs in two regions so one regional outage no longer "
                "stops operations, and it has been approved by the Architecture Review Board. Migration is "
                "feed by feed, so the business keeps running while the old links are switched off one at a time."
            ),
            "architecture_decisions": (
                "We moved to a managed event service rather than extending the in-house relay, because the relay "
                "was a single point of failure and the team could not staff a second region. Every event is "
                "published once and consumed by as many systems as need it, which removes the pairwise feeds and "
                "the reconciliation they required. A failed delivery is retried three times and then raised to an "
                "operator, rather than dropped silently. We kept the Integration Bus in place during migration "
                "so each feed can be moved and rolled back independently. We rejected a big-bang cut-over "
                "because a failure would have stopped dispatch and certification at the same time. The Architecture "
                "Review Board approved the managed service and asked for a recovery objective of four hours."
            ),
        },
        "links": {
            "application_cooperation": ["Event Relay", "Event Delivery"],
            "deployment_view": ["Event Transport", "Quay Compute Pool"],
            "gap_analysis": ["Event backbone resilience gap", "Baseline Architecture", "Target Architecture"],
            "work_packages": ["Event Backbone Migration", "Cloud Event Backbone", "Event backbone cut-over"],
            "security_viewpoint": ["No single point of failure in event path", "Failures must be visible within five minutes"],
            "nfr_satisfaction": ["Recovery time objective of four hours"],
        },
        "capabilities": ["Event Processing"],
        "relationships": [
            ("Event Relay", "Event Delivery", "realization"),
            ("Event Transport", "Event Relay", "serving"),
            ("Quay Compute Pool", "Event Transport", "realization"),
            ("Event backbone resilience gap", "Baseline Architecture", "association"),
            ("Event backbone resilience gap", "Target Architecture", "association"),
            ("Event Backbone Migration", "Cloud Event Backbone", "realization"),
            ("Event Backbone Migration", "Event backbone cut-over", "triggering"),
            ("No single point of failure in event path", "Failures must be visible within five minutes", "association"),
        ],
    },
}


# Risks of each seeded solution: (title, probability, impact, status, owner,
# mitigation). Spread across the grid on purpose, including a likely risk with
# a small impact and an unlikely one with a large impact.
_SOLUTION_RISKS = {
    "Field Calibration Platform": [
        ("Engineers keep using paper job sheets after go-live", 5, 5, "open", "Ivo Reed",
         "Run two depots on the tester for a month, keep a paper fallback until the depot lead signs off."),
        ("Readings are lost when a tester loses signal", 3, 5, "mitigated", "IT Operations Lead",
         "The tester stores every reading locally and sends it on reconnect; tested with a 72-hour outage."),
        ("A reading is saved without its reference standard", 4, 4, "open", "Calibration Specialist",
         "The tester will not accept a reading until a reference standard is chosen."),
        ("Historic certificates are dropped during the ledger load", 4, 4, "open", "Regulatory Compliance Officer",
         "Reconcile certificate counts per depot before and after each load."),
        ("Accreditation audit delays certificate signing", 2, 4, "accepted", "Regulatory Compliance Officer",
         "Accepted for the first quarter; the audit date is known and signing is planned around it."),
        ("A tester battery fails on a long site visit", 5, 1, "open", "Field Service Lead",
         "Issue a spare battery pack with each tester."),
    ],
    "Supplier Collaboration Hub": [
        ("Suppliers do not adopt the portal", 4, 3, "open", "Procurement Officer",
         "Onboard the twenty sensor-component suppliers first, with a named buyer for each."),
        ("The portal is unavailable at month end", 3, 4, "open", "IT Operations Lead",
         "Add a second instance before the first month-end close."),
        ("A valid certificate file is rejected on upload", 2, 2, "mitigated", "Quality Assurance Lead",
         "Accept PDF and image files; a rejected upload tells the supplier why."),
    ],
    "Event-Driven Operations Backbone": [
        ("The second region is not funded after cut-over", 2, 5, "open", "Ivo Reed",
         "Hold the second-region budget in the migration programme until both regions are live."),
        ("A feed is switched off before its replacement is proven", 3, 5, "mitigated", "IT Operations Lead",
         "Each feed runs on both paths for two weeks and is switched off only when the counts match."),
        ("An operator misses a failed-delivery alert", 1, 3, "accepted", "IT Operations Lead",
         "Alerts go to the on-call rota and are repeated after five minutes."),
    ],
}


def _seed_solution_risks(org_id, solutions) -> int:
    """Record each solution's risks in the one risk register, linked to the
    solution the way the solution's own risk list reads them."""
    from app.models.risk import Risk, RiskStatus
    from app.models.risk_entity_link import RiskEntityLink

    existing = {
        r.title: r
        for r in db.session.query(Risk).filter(Risk.organization_id == org_id)
    }
    created = 0
    for sol_name, rows in _SOLUTION_RISKS.items():
        sol = solutions.get(sol_name)
        if sol is None:
            continue
        for title, probability, impact, status, owner, mitigation in rows:
            risk = existing.get(title)
            if risk is None:
                risk = Risk(
                    title=title, description=mitigation, likelihood=probability,
                    impact=impact, status=RiskStatus(status), owner=owner,
                    mitigation_plan=mitigation, organization_id=org_id,
                    solution_id=sol.id,
                )
                db.session.add(risk)
                db.session.flush()
                created += 1
            linked = (
                db.session.query(RiskEntityLink)
                .filter_by(risk_id=risk.id, entity_type="solution", entity_id=sol.id)
                .first()
            )
            if linked is None:
                db.session.add(RiskEntityLink(
                    risk_id=risk.id, entity_type="solution", entity_id=sol.id,
                    organization_id=org_id))
    db.session.flush()
    return created


def _seed_solution_blueprints(org_id, solutions) -> dict:
    """Write each seeded solution's blueprint and read its governance state
    back from the blueprint's own scoring. Idempotent: an element keeps the
    description it has, a link or relationship already present is not doubled,
    and a narrative an architect has edited is not overwritten."""
    from app.models import ArchiMateRelationship
    from app.models.archimate_core import ArchitectureModel
    from app.services.archimate_backbone import create_backbone_element
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import SolutionArchiMateElement as SolutionElementLink
    from app.models.solution_models import SolutionCapabilityMapping
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )

    stats = {
        "blueprint_elements_created": 0,
        "blueprint_elements_described": 0,
        "blueprint_links_created": 0,
        "blueprint_relationships_created": 0,
        "blueprint_narratives_written": 0,
        "blueprint_capabilities_mapped": 0,
    }
    business_caps = {
        c.name: c
        for c in db.session.query(BusinessCapability)
        .filter(BusinessCapability.organization_id == org_id)
    }
    elements = _resolve_elements(org_id)
    for name, etype, layer, desc in _BLUEPRINT_NEW_ELEMENTS:
        if name in elements:
            continue
        elements[name] = create_backbone_element(
            element_type=etype, layer=layer, name=name, description=desc,
            organization_id=org_id,
        )
        stats["blueprint_elements_created"] += 1
    for name, desc in _BLUEPRINT_DESCRIPTIONS.items():
        el = elements.get(name)
        if el is not None and not (el.description or "").strip():
            el.description = desc
            stats["blueprint_elements_described"] += 1
    db.session.flush()

    svc = BlueprintCompletenessService()
    for sol_name, plan in _BLUEPRINTS.items():
        sol = solutions.get(sol_name)
        if sol is None:
            continue
        arch = (
            db.session.query(ArchitectureModel)
            .filter(ArchitectureModel.organization_id == org_id,
                    ArchitectureModel.solution_id == sol.id)
            .first()
        )
        if arch is None:
            arch = ArchitectureModel(
                name="%s architecture" % sol_name, version="1.0",
                organization_id=org_id, solution_id=sol.id,
            )
            db.session.add(arch)
            db.session.flush()

        narratives = dict(sol.section_narratives or {})
        for section, text in plan["narratives"].items():
            if not (narratives.get(section) or "").strip():
                narratives[section] = text
                stats["blueprint_narratives_written"] += 1
        sol.section_narratives = narratives

        linked = {
            row.element_id: row
            for row in db.session.query(SolutionElementLink)
            .filter(SolutionElementLink.solution_id == sol.id)
        }
        for names in plan["links"].values():
            for name in names:
                el = elements.get(name)
                if el is None:
                    continue
                if el.architecture_id is None:
                    el.architecture_id = arch.id
                spec = _confirmed_spec(plan["spec"], el.type) if plan.get("spec") else None
                row = linked.get(el.id)
                if row is not None:
                    if row.spec_data is None and spec is not None:
                        row.spec_data = spec
                    continue
                linked[el.id] = SolutionElementLink(
                    solution_id=sol.id, layer_type=(el.layer or "").lower() or None,
                    element_id=el.id, element_table="archimate_elements",
                    element_name=el.name, element_role="primary", spec_data=spec,
                )
                db.session.add(linked[el.id])
                stats["blueprint_links_created"] += 1
        db.session.flush()

        existing = {
            (r.source_id, r.target_id)
            for r in db.session.query(ArchiMateRelationship)
            .filter(ArchiMateRelationship.organization_id == org_id)
        }
        for src_name, tgt_name, rel_type in plan["relationships"]:
            src, tgt = elements.get(src_name), elements.get(tgt_name)
            if src is None or tgt is None or (src.id, tgt.id) in existing:
                continue
            db.session.add(ArchiMateRelationship(
                source_id=src.id, target_id=tgt.id, type=rel_type,
                architecture_id=arch.id, organization_id=org_id,
            ))
            existing.add((src.id, tgt.id))
            stats["blueprint_relationships_created"] += 1
        db.session.flush()

        sol.business_sponsor = sol.business_sponsor or "Chief Operations Officer"
        sol.technical_lead = sol.technical_lead or "Head of Engineering"
        mapped = {
            m.capability_id
            for m in db.session.query(SolutionCapabilityMapping)
            .filter(SolutionCapabilityMapping.solution_id == sol.id)
        }
        for cap_name in plan["capabilities"]:
            cap = business_caps.get(cap_name)
            if cap is not None and cap.id not in mapped:
                db.session.add(SolutionCapabilityMapping(
                    solution_id=sol.id, capability_id=cap.id, support_level="full",
                    notes="Capability this solution serves, from the demonstration estate.",
                ))
                stats["blueprint_capabilities_mapped"] += 1
        db.session.flush()

        # Score the blueprint, then let the facts decide the governance
        # state: a solution whose every section clears the ARB threshold is
        # ready to be proposed; an ARB decision (approved or rejected) and an
        # open review are human acts and are left alone.
        scores = svc.score_all(sol.id)
        if (sol.governance_status or "draft") in ("draft", "proposed"):
            sol.governance_status = (
                "proposed" if svc.check_arb_ready(sol.id, scores)["ready"] else "draft"
            )
    db.session.flush()
    return stats


def seed_demo_company() -> dict:
    """Create the Lantern Quay Systems demonstration organisation.

    Returns counts keyed by entity kind.  Idempotent: re-running creates
    nothing new and changes nothing. The records are written with their
    dates spread over the last 90 days, so their history is sealed once.
    """
    from app.services.history_spread import HAND_LABEL, dated_history

    with dated_history(rest=("manual", HAND_LABEL)):
        return _seed_demo_company()


def _seed_demo_company() -> dict:
    from app.models.organization import Organization
    from app.models.user import User, Role
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_owner import ApplicationOwner
    from app.models.risk import Risk, RiskStatus
    from app.models.enterprise_intelligence import PortfolioInitiative
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.business_model import BusinessModelCanvas
    from app.models.vendor.vendor_organization import EnterpriseInitiative
    from app.services import work_package_service
    from app.models.implementation_migration import Plateau, Gap
    from app.jobs.tenant_safe_job import tenant_scope
    from werkzeug.security import generate_password_hash

    from app.models.user import PASSWORD_HASH_METHOD

    stats: dict[str, int] = {}

    # ── 1. organisation ─────────────────────────────────────────────────
    org = Organization.query.filter_by(slug=_ORG_SLUG).first()
    if org is None:
        from app.services.billing_plans import set_contract_plan

        org = Organization(name=_ORG_NAME, slug=_ORG_SLUG, settings={"currency_code": "GBP"}, reporting_currency="GBP")
        db.session.add(org)
        db.session.flush()
        # Enterprise has no people limit, so the demo cast below fits.
        set_contract_plan(org, "enterprise", None)
        stats["organization_created"] = 1
    else:
        # Ensure the demo organisation has its currency set.
        changed = False
        if not (org.settings or {}).get("currency_code"):
            org.settings = {**(org.settings or {}), "currency_code": "GBP"}
            changed = True
        if not org.reporting_currency:
            org.reporting_currency = "GBP"
            changed = True
        if changed:
            db.session.flush()
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
                password_hash=generate_password_hash(demo_password, method=PASSWORD_HASH_METHOD),
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
            profile = _APP_PROFILE.get(app_name)
            licence, infra, total, area = _app_figures(app_name)
            app = ApplicationComponent(
                name=app_name,
                organization_id=org_id,
                lifecycle_status=profile[0] if profile else lifecycle,
                component_type=profile[1] if profile else None,
                vendor_name=profile[2] if profile else None,
                health_status=health,
                archimate_element_id=el.id if el else None,
                application_code=f"LQ-{app_name.upper().replace(' ', '-')[:20]}",
                implementation_date=_dt.date(2022, 3, 15),
                go_live_date=_dt.date(2022, 6, 1),
                total_cost_of_ownership=total,
                license_cost_annual=licence,
                infrastructure_cost_monthly=infra,
                business_domain=area,
                business_criticality="high",
                strategic_importance="high",
            )
            db.session.add(app)
            apps_created += 1
        if apps_created:
            db.session.flush()
        stats["applications_created"] = apps_created

        # Backfill the portfolio profile onto applications seeded before it
        # existed (re-running the command on an existing demo database must
        # fix the tiles, not leave them at zero). Only fills what is unset or
        # still the old blanket "active" default.
        profile_updated = 0
        for app_name, app in _resolve_apps(org_id).items():
            profile = _APP_PROFILE.get(app_name)
            if profile is None:
                continue
            changed = False
            if app.lifecycle_status == "active":
                app.lifecycle_status = profile[0]
                changed = True
            if not app.component_type:
                app.component_type = profile[1]
                changed = True
            if not app.vendor_name:
                app.vendor_name = profile[2]
                changed = True
            profile_updated += changed
        if profile_updated:
            db.session.flush()
        stats["applications_profiled"] = profile_updated

        # ── 6. application owners ───────────────────────────────────────
        # Create owner user accounts first.
        owner_password = os.environ.get("DEMO_USER_PASSWORD", "")
        existing_owner_emails = {
            u.email for u in User.query.filter_by(organization_id=org_id).all()
        }
        viewer_role = Role.query.filter_by(name="Viewer").first()
        owner_users_created = 0
        for first, last, email in _OWNER_USERS:
            if email in existing_owner_emails:
                continue
            u = User(
                email=email,
                first_name=first,
                last_name=last,
                password_hash=generate_password_hash(owner_password, method=PASSWORD_HASH_METHOD),
                organization_id=org_id,
                confirmed=True,
                role=viewer_role,
                enterprise_role=(
                    "business_architect"
                    if email == _DEMO_BUSINESS_ARCHITECT_EMAIL
                    else "enterprise_architect"
                ),
            )
            db.session.add(u)
            owner_users_created += 1
        if owner_users_created:
            db.session.flush()
        reader = User.query.filter_by(
            email=_DEMO_READER_EMAIL, organization_id=org_id
        ).first()
        if reader is not None and reader.enterprise_role != _DEMO_READER_PERSONA:
            reader.enterprise_role = _DEMO_READER_PERSONA
            db.session.flush()

        architect = User.query.filter_by(
            email=_DEMO_BUSINESS_ARCHITECT_EMAIL, organization_id=org_id
        ).first()
        if architect is not None and architect.enterprise_role != "business_architect":
            architect.enterprise_role = "business_architect"
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

        # ── 6b. owner text, owning units, and recorded annual cost ──────
        # Every live store that answers "which applications have an owner" and
        # "which have a recorded annual cost" is written from the same facts,
        # so the screens agree. The retired application_costs table is not
        # written. Idempotent: only fills or corrects.
        from app.models.enterprise_intelligence import (
            ApplicationOwnership, OrganizationUnit,
        )
        from app.services.application_cost_accessor import state_contract, sync_cost_fact

        units = {
            u.code: u
            for u in db.session.query(OrganizationUnit)
            .filter(OrganizationUnit.organization_id == org_id)
            .all()
        }
        for code, unit_name in _OWNER_UNITS:
            if code not in units:
                units[code] = OrganizationUnit(
                    organization_id=org_id, name=unit_name, code=code,
                    unit_type="Department", status="Active",
                )
                db.session.add(units[code])
        db.session.flush()

        owner_text = {}
        technical_text = {}
        for app_name, owner_display_name, owner_type in _APP_OWNERS:
            if owner_type == "primary":
                owner_text.setdefault(app_name, owner_display_name)
            else:
                technical_text.setdefault(app_name, owner_display_name)

        held = {
            (o.application_id, o.organization_unit_id, o.ownership_type)
            for o in db.session.query(ApplicationOwnership)
            .filter(ApplicationOwnership.organization_id == org_id)
            .all()
        }
        agreed = 0
        for app_name, app in _resolve_apps(org_id).items():
            changed = False
            if app_name in owner_text and not app.application_owner:
                app.application_owner = owner_text[app_name]
                app.business_owner = owner_text[app_name]
                changed = True
            if app_name in technical_text and not app.technical_owner:
                app.technical_owner = technical_text[app_name]
                changed = True
            unit = units.get(_OWNER_UNIT_OF.get(owner_text.get(app_name)))
            if unit is not None and (app.id, unit.id, "Business Owner") not in held:
                db.session.add(ApplicationOwnership(
                    organization_id=org_id, application_id=app.id,
                    organization_unit_id=unit.id, ownership_type="Business Owner",
                    ownership_percentage=100,
                    primary_contact=owner_text[app_name],
                ))
                held.add((app.id, unit.id, "Business Owner"))
                changed = True
            # The figures are the application's own, whatever an earlier seed wrote.
            licence, infra, total, area = _app_figures(app_name)
            if (app.license_cost_annual, app.infrastructure_cost_monthly, app.total_cost_of_ownership,
                    app.business_domain) != (licence, infra, total, area):
                app.license_cost_annual, app.infrastructure_cost_monthly = licence, infra
                app.total_cost_of_ownership, app.business_domain = total, area
                changed = True
            agreed += changed
        db.session.flush()
        for app in _resolve_apps(org_id).values():
            for column in ("total_cost_of_ownership", "license_cost_annual",
                           "infrastructure_cost_monthly"):
                sync_cost_fact(app, column)
            # The Spend page reads contracts, so a vendor-supplied application states its cost there
            # too: the run cost on the Overview, the Spend page and the canvas's cost box then agree.
            profile = _APP_PROFILE.get(app.name)
            if (profile and "in-house" not in profile[2].lower() and not profile[0].startswith("5.")
                    and app.total_cost_of_ownership):
                state_contract(app, profile[2], app.total_cost_of_ownership, months_ago=3 + app.id % 8)
        db.session.flush()
        stats["application_figures_agreed"] = agreed

        # ── 7. capabilities ─────────────────────────────────────────────
        # The application-to-capability mapping table (what the Applications
        # screen's Capability column reads) points at business_capability, and
        # ADR 0008's write-time projection copies each business_capability row
        # into unified_capabilities with its provenance. So the capability is
        # written once, as a business_capability row, and the unified row is
        # the projection of it -- not a second hand-written copy. A database
        # seeded before this existed already holds unlinked unified rows with
        # the same codes; those are left as they are (the (organisation, code)
        # index would refuse a second row) and only a fresh seed gets mappings.
        from app.models.business_capabilities import BusinessCapability

        existing_caps = _resolve_caps(org_id)
        existing_bcs = {
            b.code: b
            for b in db.session.query(BusinessCapability)
            .filter(BusinessCapability.organization_id == org_id)
            .all()
        }
        caps_created = 0
        for name, code, current, target, level in _CAPABILITIES:
            if code in existing_caps or code in existing_bcs:
                continue
            seeded_element = elements.get(name)
            if seeded_element is not None and seeded_element.type != "Capability":
                seeded_element = None
            bc = BusinessCapability(
                name=name,
                code=code,
                organization_id=org_id,
                # The seeded element of the same name is this capability's
                # element, so it is not mirrored a second time.
                archimate_element_id=seeded_element.id if seeded_element else None,
                business_owner=_CAPABILITY_OWNERS.get(code),
                level=level,
                current_maturity_level=current,
                target_maturity_level=target,
                maturity_assessment_date=_dt.datetime.utcnow(),
            )
            db.session.add(bc)
            existing_bcs[code] = bc
            caps_created += 1
        if caps_created:
            db.session.flush()
        stats["capabilities_created"] = caps_created

        # Owners for capabilities seeded before they carried one.
        from app.models.unified_capability import UnifiedCapability

        for model in (BusinessCapability, UnifiedCapability):
            for cap in db.session.query(model).filter(model.organization_id == org_id):
                if not cap.business_owner and cap.code in _CAPABILITY_OWNERS:
                    cap.business_owner = _CAPABILITY_OWNERS[cap.code]
        db.session.flush()

        # Business domains for the heat map (also for a database seeded before
        # capabilities carried one: a domain an editor has set is left alone).
        from app.models.unified_capability import BusinessDomain

        domain_of = {}
        for dcode, (dname, members) in _CAPABILITY_DOMAINS.items():
            domain = BusinessDomain.query.filter_by(code=dcode).first()
            if domain is None:
                domain = BusinessDomain(code=dcode, name=dname,
                                        description="%s capabilities of Lantern Quay Systems" % dname)
                db.session.add(domain)
                db.session.flush()
            for member in members:
                domain_of["LQ-CAP-" + member] = (domain.id, dname)
        for cap in db.session.query(UnifiedCapability).filter(UnifiedCapability.organization_id == org_id):
            if cap.domain_id is None and cap.code in domain_of:
                cap.domain_id = domain_of[cap.code][0]
        for cap in db.session.query(BusinessCapability).filter(BusinessCapability.organization_id == org_id):
            if not cap.business_domain and cap.code in domain_of:
                cap.business_domain = domain_of[cap.code][1]
        db.session.flush()

        # The judgment replaces the starting value, but only while the capability still
        # carries the starting value: a type an editor has changed is theirs.
        from app.services.capability_type import default_capability_type

        for cap in db.session.query(UnifiedCapability).filter(
            UnifiedCapability.organization_id == org_id
        ):
            chosen = _CAPABILITY_TYPES.get(cap.code)
            if chosen and cap.capability_type in (
                None, default_capability_type(cap.category, cap.level),
            ):
                cap.capability_type = chosen
        db.session.flush()

        # ── 7a. every capability has its model element and its canonical record ──
        # The coverage page, the genome slice and the chat read the unified store
        # with a model element on both ends; a capability without either reads as
        # unsupported however many systems run it.
        from app.models.application_portfolio import ApplicationComponent
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )
        from app.modules.capabilities.services.capability_service import ensure_capability_record
        from app.services.archimate_backbone import create_backbone_element

        from app.models.archimate_core import ArchiMateElement

        unified_by_code = {u.code: u for u in _resolve_caps(org_id).values()}
        # Re-read: writing a capability record can already have mirrored its element.
        elements = _resolve_elements(org_id)
        elements_made = 0
        for name, code, _cur, _tgt, level in _CAPABILITIES:
            bc_now = existing_bcs.get(code)
            element = (
                db.session.get(ArchiMateElement, bc_now.archimate_element_id)
                if bc_now is not None and bc_now.archimate_element_id is not None else None
            )
            if element is None:
                element = elements.get(name)
            if element is None or element.type != "Capability":
                element = create_backbone_element(
                    element_type="Capability", layer="strategy", name=name,
                    organization_id=org_id,
                )
                elements[name] = element
                elements_made += 1
            bc = existing_bcs.get(code)
            if bc is not None and bc.archimate_element_id is None:
                bc.archimate_element_id = element.id
            ucap = unified_by_code.get(code)
            if ucap is None:
                ucap, _created = ensure_capability_record(
                    name=name, code=code, level=level, organization_id=org_id,
                    archimate_element_id=element.id,
                    domain_id=(domain_of.get(code) or (None,))[0],
                )
                unified_by_code[code] = ucap
            elif ucap.archimate_element_id is None:
                ucap.archimate_element_id = element.id
        for demo_app in db.session.query(ApplicationComponent).filter(
            ApplicationComponent.organization_id == org_id
        ):
            if demo_app.archimate_element_id is None:
                match = elements.get(demo_app.name)
                if match is not None and match.type == "ApplicationComponent":
                    demo_app.archimate_element_id = match.id
        db.session.flush()
        stats["capability_elements_created"] = elements_made

        # Link each business capability to its unified twin (ADR 0008's FK),
        # so vendor discovery's capability bridge (vendor_analysis_routes.py)
        # resolves a selection through the FK instead of resting on name
        # equality alone. The pair created or confirmed just above shares a
        # code, which is the stable join key (two capabilities can share a
        # display name; they cannot share a code within this seed).
        for code, ucap in unified_by_code.items():
            bc = existing_bcs.get(code)
            if bc is not None and bc.deprecated_in_favor_of_id is None:
                bc.deprecated_in_favor_of_id = ucap.id
        db.session.flush()

        # ── 7b. application-to-capability mappings ──────────────────────
        from app.models.application_capability import ApplicationCapabilityMapping

        mapped = {
            (m.application_component_id, m.business_capability_id)
            for m in db.session.query(ApplicationCapabilityMapping)
            .filter(ApplicationCapabilityMapping.organization_id == org_id)
            .all()
        }
        unified_mapped = {
            (m.application_component_id, m.unified_capability_id)
            for m in db.session.query(UnifiedApplicationCapabilityMapping)
            .join(ApplicationComponent,
                  ApplicationComponent.id == UnifiedApplicationCapabilityMapping.application_component_id)
            .filter(ApplicationComponent.organization_id == org_id)
            .all()
        }
        unified_created = 0
        mappings_created = 0
        for app_name, app in _resolve_apps(org_id).items():
            profile = _APP_PROFILE.get(app_name)
            ucap = unified_by_code.get(profile[3]) if profile else None
            if ucap is not None and (app.id, ucap.id) not in unified_mapped:
                db.session.add(UnifiedApplicationCapabilityMapping(
                    unified_capability_id=ucap.id,
                    application_component_id=app.id,
                    support_level="full",
                    coverage_percentage=80,
                    relationship_type="supports",
                    is_active=True,
                ))
                unified_mapped.add((app.id, ucap.id))
                unified_created += 1
            bc = existing_bcs.get(profile[3]) if profile else None
            if bc is None or (app.id, bc.id) in mapped:
                continue
            db.session.add(ApplicationCapabilityMapping(
                organization_id=org_id,
                application_component_id=app.id,
                business_capability_id=bc.id,
                support_level="full",
                coverage_percentage=80,
                is_primary_enabler=True,
                is_active=True,
            ))
            mappings_created += 1
        if mappings_created or unified_created:
            db.session.flush()
        stats["capability_mappings_created"] = mappings_created
        stats["unified_capability_mappings_created"] = unified_created

        # ── 7c. architecture review board queue ─────────────────────────
        from app.models.architecture_review_board import ARBReviewItem

        users = _resolve_users(org_id)
        submitter = users.get(_DEMO_USER_EMAIL)
        reviewer = users.get("ivo.reed@lantern-quay.example.com") or submitter
        existing_reviews = {
            r.review_number
            for r in db.session.query(ARBReviewItem)
            .filter(ARBReviewItem.review_number.like("LQ-ARB-%"))
            .all()
        }
        now = _dt.datetime.utcnow()
        reviews_created = 0
        for number, title, rtype, priority, status, age, took, decision in _ARB_REVIEWS:
            if number in existing_reviews or submitter is None:
                continue
            submitted = now - _dt.timedelta(days=age)
            item = ARBReviewItem(
                review_number=number,
                title=title,
                review_type=rtype,
                priority=priority,
                status=status,
                organization_id=org_id,
                submitter_id=submitter.id,
                submitted_at=submitted,
                reviewer_id=reviewer.id,
                review_started_at=submitted + _dt.timedelta(days=1),
            )
            if decision:
                decided = submitted + _dt.timedelta(days=took)
                item.decision = decision
                item.decision_date = decided
                item.decided_by_id = reviewer.id
                item.review_completed_at = decided
            db.session.add(item)
            reviews_created += 1
        if reviews_created:
            db.session.flush()
        stats["arb_reviews_created"] = reviews_created

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

        # Two risks whose score has risen in the last week, so the weekly
        # briefing has movement to report. Skipped once a risk has history.
        from app.models.risk_score_history import RiskScoreHistory

        rising_created = 0
        for title, was, now_score in _RISING_RISKS:
            risk = (
                db.session.query(Risk)
                .filter(Risk.organization_id == org_id, Risk.title == title)
                .first()
            )
            if risk is None or db.session.query(RiskScoreHistory).filter(
                RiskScoreHistory.risk_id == risk.id
            ).first():
                continue
            risk.residual_likelihood, risk.residual_impact = now_score
            for (likelihood, impact), days_ago in ((was, 21), (now_score, 2)):
                db.session.add(RiskScoreHistory(
                    risk_id=risk.id, score_kind="residual",
                    likelihood=likelihood, impact=impact,
                    recorded_at=_dt.datetime.utcnow() - _dt.timedelta(days=days_ago),
                    organization_id=org_id,
                ))
            rising_created += 1
        if rising_created:
            db.session.flush()
        stats["rising_risks_created"] = rising_created

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
                organization_id=org_id,
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
            cap = caps.get(cap_code)
            work_package_service.create_work_package(
                organization_id=org_id,
                name=name,
                status=status,
                priority=priority,
                start_date=today + _dt.timedelta(days=start_offset),
                end_date=today + _dt.timedelta(days=start_offset + duration),
                estimated_cost=estimated,
                actual_cost=actual,
                capability_id=cap.id if cap else None,
                business_capability=cap.name if cap else "",
                assigned_to="Ivo Reed",
            )
            wps_created += 1
        if wps_created:
            db.session.flush()
        stats["work_packages_created"] = wps_created


        # ── 10b. portfolio initiatives ──────────────────────────────────
        # The Portfolio screen reads EnterpriseInitiative, not the
        # PortfolioInitiative rows above, so it showed no initiatives.
        existing_codes = {
            i.code
            for i in db.session.query(EnterpriseInitiative)
            .filter(EnterpriseInitiative.organization_id == org_id)
            .all()
        }
        initiatives_created = 0
        for (name, code, phase, status, health, budget, spent, forecast,
             complete, sponsor, manager, kind, _elements) in _INITIATIVES:
            if code in existing_codes:
                continue
            db.session.add(EnterpriseInitiative(
                organization_id=org_id,
                name=name,
                code=code,
                initiative_type=kind,
                program_type="strategic",
                current_phase=phase,
                status=status,
                health_status=health,
                approved_budget=budget,
                spent_to_date=spent,
                forecast_cost=forecast,
                completion_percentage=complete,
                executive_sponsor=sponsor,
                program_manager=manager,
                planned_start_date=_dt.datetime(2026, 1, 15),
                planned_end_date=_dt.datetime(2027, 3, 31),
            ))
            initiatives_created += 1
        if initiatives_created:
            db.session.flush()
        stats["initiatives_created"] = initiatives_created

        # ── 10c. work packages: owner, initiative and connections ───────
        # Idempotent: only fills what is unset, so a database seeded before
        # this step is repaired by a re-run.
        from app.models import ArchiMateRelationship

        initiative_by_element = {}
        for row in db.session.query(EnterpriseInitiative).filter(
            EnterpriseInitiative.organization_id == org_id
        ):
            for spec in _INITIATIVES:
                if spec[1] == row.code:
                    for element_name in spec[12]:
                        initiative_by_element[element_name] = row
        owner = _resolve_users(org_id).get("ivo.reed@lantern-quay.example.com")
        wps_by_name = {
            wp.name: wp
            for wp in db.session.query(UnifiedWorkPackage)
            .filter(UnifiedWorkPackage.organization_id == org_id)
            .all()
        }
        linked = 0
        for spec in _WORK_PACKAGES:
            wp = wps_by_name.get(spec[0])
            if wp is None:
                continue
            changed = False
            if owner is not None and wp.owner_id is None:
                wp.owner_id = owner.id
                changed = True
            initiative = initiative_by_element.get(spec[7])
            if initiative is not None and wp.enterprise_initiative_id is None:
                wp.enterprise_initiative_id = initiative.id
                changed = True
            target = elements.get(spec[7])
            if target is not None and wp.archimate_element_id:
                has_edge = (
                    db.session.query(ArchiMateRelationship.id)
                    .filter(
                        ArchiMateRelationship.organization_id == org_id,
                        ArchiMateRelationship.source_id == wp.archimate_element_id,
                        ArchiMateRelationship.target_id == target.id,
                    )
                    .first()
                )
                if has_edge is None:
                    db.session.add(ArchiMateRelationship(
                        source_id=wp.archimate_element_id,
                        target_id=target.id,
                        type="Association",
                        organization_id=org_id,
                    ))
                    changed = True
            linked += changed
        if linked:
            db.session.flush()
        stats["work_packages_linked"] = linked

        # ── 10d. business model canvas ──────────────────────────────────
        canvas_exists = (
            db.session.query(BusinessModelCanvas.id)
            .filter(
                BusinessModelCanvas.organization_id == org_id,
                BusinessModelCanvas.name == _CANVAS_NAME,
            )
            .first()
        )
        if canvas_exists is None:
            canvas = BusinessModelCanvas(
                organization_id=org_id,
                name=_CANVAS_NAME,
                description=_CANVAS_DESCRIPTION,
                operating_model_type=_CANVAS_OPERATING_MODEL,
            )
            db.session.add(canvas)
            db.session.flush()
        else:
            canvas = BusinessModelCanvas.query.filter_by(
                id=canvas_exists[0], organization_id=org_id
            ).first()
        stats["canvas_entries_created"] = _seed_canvas_model(org_id, canvas)
        stats["business_model_canvases_created"] = 0 if canvas_exists else 1
        from app.commands import seed_framework_canvases

        stats["framework_canvases_created"], stats["framework_canvas_rows_created"] = seed_framework_canvases.seed(org_id)

        # ── 11. plateaus ────────────────────────────────────────────────
        existing_plateaus = {
            p.name
            for p in db.session.query(Plateau)
            .filter(Plateau.organization_id == org_id)
            .all()
        }
        plateaus_created = 0
        for name, seq, el_name, target_date in _PLATEAUS:
            if name in existing_plateaus:
                continue
            el = elements.get(el_name)
            plateau = Plateau(
                name=name,
                sequence_order=seq,
                organization_id=org_id,
                archimate_element_id=el.id if el else None,
                target_date=target_date,
            )
            db.session.add(plateau)
            plateaus_created += 1
        if plateaus_created:
            db.session.flush()
        for plateau in db.session.query(Plateau).filter(Plateau.organization_id == org_id):
            for name, _seq, _el, target_date in _PLATEAUS:
                if plateau.name == name and plateau.target_date is None:
                    plateau.target_date = target_date
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

        # Each gap sits between two plateaus and has the work package that
        # closes it. Idempotent: existing links are kept, none is doubled.
        plateau_by_name = {
            p.name: p
            for p in db.session.query(Plateau).filter(Plateau.organization_id == org_id)
        }
        wp_by_name = {
            wp.name: wp
            for wp in db.session.query(UnifiedWorkPackage)
            .filter(UnifiedWorkPackage.organization_id == org_id)
        }
        for gap in db.session.query(Gap).filter(Gap.organization_id == org_id):
            placement = _GAP_PLACEMENT.get(gap.name)
            if placement is None:
                continue
            origin, target, wp_name = placement
            if gap.originating_plateau_id is None and origin in plateau_by_name:
                gap.originating_plateau_id = plateau_by_name[origin].id
            if gap.target_plateau_id is None and target in plateau_by_name:
                gap.target_plateau_id = plateau_by_name[target].id
            if target in plateau_by_name and plateau_by_name[target] not in gap.plateaus:
                gap.plateaus.append(plateau_by_name[target])
            if wp_name in wp_by_name:
                work_package_service.link_gap(
                    wp_by_name[wp_name].id, gap.id, organization_id=org_id)
        db.session.flush()

        demo_user = User.query.filter_by(
            email=_DEMO_USER_EMAIL, organization_id=org_id).first()
        stats.update(_seed_boards(org_id, demo_user.id if demo_user else None))
        stats.update(_seed_data_model(org_id))
        stats.update(_seed_data_flows(org_id))

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