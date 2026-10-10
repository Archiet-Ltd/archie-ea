"""
Regulatory Framework Service

Seeds and manages regulatory frameworks applicable to manufacturing organizations.

Pre-configured frameworks:
- ISO 9001 (Quality Management)
- ISO 27001 (Information Security)
- ISO 14001 (Environmental Management)
- OSHA (Occupational Safety)
- EPA (Environmental Protection)
- FDA 21 CFR Part 11 (Electronic Records - for pharma/medical devices)
- GDPR (Data Privacy - EU)
- SOX (Financial Compliance - public companies)
- PCI-DSS (Payment Card Security)

Frameworks are available as static JSON data in app/data/compliance_frameworks.json
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List

from app import db
from app.models.compliance_models import (
    ComplianceControl,
    RegulatoryFramework,
)

logger = logging.getLogger(__name__)


class RegulatoryFrameworkService:
    """Service for managing regulatory frameworks and compliance controls"""

    # UK MANUFACTURING-SPECIFIC regulatory frameworks
    # Includes Digital Product Passport (DPP), EPD/LCA, and UK regulations
    MANUFACTURING_FRAMEWORKS = [
        {
            "code": "UK-DPP",
            "name": "UK Digital Product Passport (Product Security and Metrology)",
            "description": "UK implementation of Digital Product Passport for manufacturing traceability, circularity, and sustainability data",
            "category": "sustainability",
            "jurisdiction": "UK",
            "enforcement_level": "mandatory",
            "penalty_risk": "high",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.gov.uk/government/publications/product-security-and-metrology-act - 2022",
            "standard_version": "2024",
            "controls": [
                {
                    "control_id": "DPP - 1",
                    "title": "Product Traceability and Identification",
                    "description": "Every manufactured product must have unique digital identifier (GS1 GTIN, EPCIS) linking to passport data",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "DPP - 2",
                    "title": "Material Composition Disclosure",
                    "description": "Detailed material composition (BOM) including hazardous substances (REACH), recycled content %, virgin material sources",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "DPP - 3",
                    "title": "Carbon Footprint and Environmental Impact",
                    "description": "Product carbon footprint (cradle-to-gate), embodied energy, water usage, and EPD alignment",
                    "control_type": "technical",
                    "criticality": "high",
                },
                {
                    "control_id": "DPP - 4",
                    "title": "Repairability and End-of-Life Instructions",
                    "description": "Repair manual, spare parts availability (min 7 years), disassembly instructions, recycling codes",
                    "control_type": "administrative",
                    "criticality": "high",
                },
                {
                    "control_id": "DPP - 5",
                    "title": "Supply Chain Transparency",
                    "description": "Tier 1 - 3 supplier information, country of origin, labor compliance (Modern Slavery Act 2015)",
                    "control_type": "administrative",
                    "criticality": "high",
                },
                {
                    "control_id": "DPP - 6",
                    "title": "Digital Passport Accessibility",
                    "description": "Passport must be machine-readable (JSON/XML), accessible via QR code, blockchain or secure API",
                    "control_type": "technical",
                    "criticality": "critical",
                },
            ],
        },
        {
            "code": "UK-EPD-LCA",
            "name": "UK Environmental Product Declaration (EPD) and Life Cycle Assessment",
            "description": "ISO 14025/14040 compliant Environmental Product Declarations and Life Cycle Assessment for UK manufacturing",
            "category": "environmental",
            "jurisdiction": "UK",
            "enforcement_level": "recommended",
            "penalty_risk": "medium",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.iso.org/standard/38131.html",
            "standard_version": "ISO 14025:2006",
            "controls": [
                {
                    "control_id": "EPD - 1",
                    "title": "Cradle-to-Gate LCA (ISO 14044)",
                    "description": "Full life cycle assessment from raw material extraction to factory gate (A1 - A3 stages)",
                    "control_type": "technical",
                    "criticality": "high",
                },
                {
                    "control_id": "EPD - 2",
                    "title": "Global Warming Potential (GWP) Calculation",
                    "description": "Calculate and disclose CO2e emissions (kg CO2e per functional unit) using PEF/PCR methodology",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "EPD - 3",
                    "title": "Water Footprint and Eutrophication",
                    "description": "Measure water consumption (m³), water scarcity index, eutrophication potential (kg PO4e)",
                    "control_type": "technical",
                    "criticality": "high",
                },
                {
                    "control_id": "EPD - 4",
                    "title": "Circular Economy Indicators",
                    "description": "Recycled input rate (%), recyclability rate (%), product lifespan, durability score",
                    "control_type": "administrative",
                    "criticality": "medium",
                },
                {
                    "control_id": "EPD - 5",
                    "title": "Third-Party EPD Verification",
                    "description": "EPD must be verified by independent third-party (BRE, IBU EPD, or equivalent)",
                    "control_type": "administrative",
                    "criticality": "high",
                },
            ],
        },
        {
            "code": "UK-HSE-COSHH",
            "name": "UK HSE COSHH (Control of Substances Hazardous to Health)",
            "description": "UK Health and Safety Executive regulations for hazardous substance control in manufacturing",
            "category": "safety",
            "jurisdiction": "UK",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.hse.gov.uk/coshh/",
            "standard_version": "2002 (amended 2023)",
            "controls": [
                {
                    "control_id": "COSHH - 6",
                    "title": "Risk Assessment for Hazardous Substances",
                    "description": "Conduct COSHH risk assessment for all hazardous substances used in manufacturing",
                    "control_type": "administrative",
                    "criticality": "critical",
                },
                {
                    "control_id": "COSHH - 7",
                    "title": "Exposure Control Measures",
                    "description": "Implement engineering controls, PPE, and monitoring to prevent exposure",
                    "control_type": "physical",
                    "criticality": "critical",
                },
                {
                    "control_id": "COSHH - 12",
                    "title": "Health Surveillance",
                    "description": "Health surveillance program for workers exposed to hazardous substances",
                    "control_type": "administrative",
                    "criticality": "high",
                },
            ],
        },
        {
            "code": "UK-REACH",
            "name": "UK REACH (Registration, Evaluation, Authorisation of Chemicals)",
            "description": "UK chemical safety regulation for substances manufactured or imported >1 tonne/year",
            "category": "environmental",
            "jurisdiction": "UK",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.hse.gov.uk/reach/",
            "standard_version": "2021 (post-Brexit)",
            "controls": [
                {
                    "control_id": "REACH-UK - 1",
                    "title": "Chemical Substance Registration",
                    "description": "Register all chemical substances manufactured/imported >1 tonne/year with HSE",
                    "control_type": "administrative",
                    "criticality": "critical",
                },
                {
                    "control_id": "REACH-UK - 2",
                    "title": "SVHC (Substance of Very High Concern) Disclosure",
                    "description": "Disclose SVHC substances >0.1% w/w in articles, notify customers within 45 days",
                    "control_type": "administrative",
                    "criticality": "critical",
                },
                {
                    "control_id": "REACH-UK - 3",
                    "title": "Safety Data Sheets (SDS)",
                    "description": "Provide SDS for hazardous substances (CLP Regulation), update within 12 months",
                    "control_type": "administrative",
                    "criticality": "high",
                },
            ],
        },
        {
            "code": "UK-UKCA",
            "name": "UK Conformity Assessed (UKCA) Marking",
            "description": "UK product safety marking for manufactured goods (replaces CE marking for UK market)",
            "category": "quality",
            "jurisdiction": "UK",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.gov.uk/guidance/using-the-ukca-marking",
            "standard_version": "2023",
            "controls": [
                {
                    "control_id": "UKCA - 1",
                    "title": "Conformity Assessment",
                    "description": "Conduct conformity assessment against relevant UK regulations before placing products on market",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "UKCA - 2",
                    "title": "Technical Documentation",
                    "description": "Maintain technical file for 10 years: test reports, design specs, risk assessments",
                    "control_type": "administrative",
                    "criticality": "critical",
                },
                {
                    "control_id": "UKCA - 3",
                    "title": "Declaration of Conformity",
                    "description": "Issue UK Declaration of Conformity signed by authorized representative",
                    "control_type": "administrative",
                    "criticality": "high",
                },
            ],
        },
        {
            "code": "UK-MSA",
            "name": "UK Modern Slavery Act 2015",
            "description": "Transparency in supply chains for manufacturing organizations with £36M+ turnover",
            "category": "social",
            "jurisdiction": "UK",
            "enforcement_level": "mandatory",
            "penalty_risk": "high",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.gov.uk/government/collections/modern-slavery-act - 2015",
            "standard_version": "2015",
            "controls": [
                {
                    "control_id": "MSA - 54",
                    "title": "Slavery and Trafficking Statement",
                    "description": "Annual statement on steps taken to prevent modern slavery in supply chain",
                    "control_type": "administrative",
                    "criticality": "high",
                },
                {
                    "control_id": "MSA-SUP",
                    "title": "Supply Chain Due Diligence",
                    "description": "Conduct due diligence on Tier 1 - 3 suppliers for forced labor risks",
                    "control_type": "administrative",
                    "criticality": "high",
                },
            ],
        },
        {
            "code": "UK-ESOS",
            "name": "UK Energy Savings Opportunity Scheme (ESOS)",
            "description": "Mandatory energy audit scheme for large UK manufacturing organizations",
            "category": "environmental",
            "jurisdiction": "UK",
            "enforcement_level": "mandatory",
            "penalty_risk": "medium",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.gov.uk/guidance/energy-savings-opportunity-scheme-esos",
            "standard_version": "Phase 3 (2023 - 2027)",
            "controls": [
                {
                    "control_id": "ESOS - 1",
                    "title": "Energy Audit Compliance",
                    "description": "Conduct comprehensive energy audit every 4 years covering 90% of energy use",
                    "control_type": "technical",
                    "criticality": "high",
                },
                {
                    "control_id": "ESOS - 2",
                    "title": "Energy Savings Opportunities",
                    "description": "Identify and report cost-effective energy savings opportunities",
                    "control_type": "administrative",
                    "criticality": "medium",
                },
            ],
        },
        {
            "code": "UK-BS - 8001",
            "name": "BS 8001:2017 Circular Economy Framework",
            "description": "British Standard for implementing circular economy principles in manufacturing",
            "category": "sustainability",
            "jurisdiction": "UK",
            "enforcement_level": "recommended",
            "penalty_risk": "low",
            "applies_to_manufacturing": True,
            "applies_to_region": "UK",
            "industry_specific": "manufacturing",
            "official_url": "https://www.bsigroup.com/en-GB/bs - 8001 - circular-economy/",
            "standard_version": "2017",
            "controls": [
                {
                    "control_id": "BS8001 - 1",
                    "title": "Circular Economy Strategy",
                    "description": "Develop and implement circular economy strategy aligned with business model",
                    "control_type": "administrative",
                    "criticality": "medium",
                },
                {
                    "control_id": "BS8001 - 2",
                    "title": "Material Flow Analysis",
                    "description": "Map material flows, identify waste streams, quantify circularity metrics",
                    "control_type": "technical",
                    "criticality": "medium",
                },
            ],
        },
        {
            "code": "ISO - 9001",
            "name": "ISO 9001:2015 Quality Management Systems",
            "description": "International standard for quality management systems in manufacturing",
            "category": "quality",
            "jurisdiction": "Global",
            "enforcement_level": "recommended",
            "penalty_risk": "medium",
            "applies_to_manufacturing": True,
            "applies_to_region": "Global",
            "industry_specific": "general",
            "official_url": "https://www.iso.org/standard/62085.html",
            "standard_version": "2015",
            "controls": [
                {
                    "control_id": "4.4",
                    "title": "Quality Management System and its Processes",
                    "description": "Organization must establish, implement, maintain and continually improve QMS",
                    "control_type": "administrative",
                    "criticality": "high",
                },
                {
                    "control_id": "8.5.1",
                    "title": "Control of Production and Service Provision",
                    "description": "Control production and service provision processes",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "8.5.3",
                    "title": "Property Belonging to Customers or External Providers",
                    "description": "Care for customer or external provider property",
                    "control_type": "administrative",
                    "criticality": "medium",
                },
                {
                    "control_id": "8.6",
                    "title": "Release of Products and Services",
                    "description": "Verify product/service requirements have been met",
                    "control_type": "technical",
                    "criticality": "critical",
                },
            ],
        },
        {
            "code": "ISO - 27001",
            "name": "ISO/IEC 27001:2022 Information Security Management",
            "description": "International standard for information security management systems",
            "category": "security",
            "jurisdiction": "Global",
            "enforcement_level": "recommended",
            "penalty_risk": "high",
            "applies_to_manufacturing": True,
            "applies_to_region": "Global",
            "industry_specific": "general",
            "official_url": "https://www.iso.org/standard/27001",
            "standard_version": "2022",
            "controls": [
                {
                    "control_id": "A.8.2",
                    "title": "Access Control",
                    "description": "Ensure authorized access and prevent unauthorized access to systems",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "A.8.10",
                    "title": "Information Deletion",
                    "description": "Delete information stored in information systems",
                    "control_type": "technical",
                    "criticality": "high",
                },
                {
                    "control_id": "A.8.24",
                    "title": "Use of Cryptography",
                    "description": "Ensure proper and effective use of cryptography",
                    "control_type": "technical",
                    "criticality": "critical",
                },
            ],
        },
        {
            "code": "OSHA - 1910",
            "name": "OSHA 29 CFR 1910 - Occupational Safety and Health",
            "description": "US federal workplace safety and health regulations",
            "category": "safety",
            "jurisdiction": "US",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "US",
            "industry_specific": "general",
            "official_url": "https://www.osha.gov/laws-regs/regulations/standardnumber/1910",
            "standard_version": "Current",
            "controls": [
                {
                    "control_id": "1910.147",
                    "title": "Control of Hazardous Energy (Lockout/Tagout)",
                    "description": "Procedures for servicing and maintenance of machines",
                    "control_type": "physical",
                    "criticality": "critical",
                },
                {
                    "control_id": "1910.1200",
                    "title": "Hazard Communication Standard",
                    "description": "Chemical hazard information and training",
                    "control_type": "administrative",
                    "criticality": "high",
                },
            ],
        },
        {
            "code": "EPA-CAA",
            "name": "EPA Clean Air Act",
            "description": "US federal law regulating air emissions from stationary and mobile sources",
            "category": "environmental",
            "jurisdiction": "US",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "US",
            "industry_specific": "general",
            "official_url": "https://www.epa.gov/clean-air-act-overview",
            "standard_version": "Current",
            "controls": [
                {
                    "control_id": "Title-V",
                    "title": "Operating Permits",
                    "description": "Facility-wide operating permits for air emissions",
                    "control_type": "administrative",
                    "criticality": "critical",
                }
            ],
        },
        {
            "code": "FDA - 21CFR11",
            "name": "FDA 21 CFR Part 11 - Electronic Records",
            "description": "Electronic records and signatures in FDA-regulated industries",
            "category": "quality",
            "jurisdiction": "US",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "US",
            "industry_specific": "pharmaceutical, medical_devices, food",
            "official_url": "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part - 11 - electronic-records-electronic-signatures",
            "standard_version": "1997",
            "controls": [
                {
                    "control_id": "11.10",
                    "title": "Controls for Closed Systems",
                    "description": "Validation of systems, audit trails, user access controls",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "11.50",
                    "title": "Signature Manifestations",
                    "description": "Signed electronic records must contain signature information",
                    "control_type": "technical",
                    "criticality": "high",
                },
            ],
        },
        {
            "code": "GDPR",
            "name": "General Data Protection Regulation",
            "description": "EU regulation on data protection and privacy",
            "category": "privacy",
            "jurisdiction": "EU",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "EU",
            "industry_specific": "general",
            "official_url": "https://gdpr.eu/",
            "standard_version": "2018",
            "controls": [
                {
                    "control_id": "Article - 32",
                    "title": "Security of Processing",
                    "description": "Implement appropriate technical and organizational measures",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "Article - 33",
                    "title": "Notification of Personal Data Breach",
                    "description": "Notify supervisory authority within 72 hours of breach",
                    "control_type": "administrative",
                    "criticality": "critical",
                },
            ],
        },
        {
            "code": "SOX",
            "name": "Sarbanes-Oxley Act",
            "description": "US law for financial reporting and corporate governance",
            "category": "financial",
            "jurisdiction": "US",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "US",
            "industry_specific": "public_companies",
            "official_url": "https://www.sec.gov/spotlight/sarbanes-oxley.htm",
            "standard_version": "2002",
            "controls": [
                {
                    "control_id": "Section - 404",
                    "title": "Internal Controls Assessment",
                    "description": "Annual assessment of internal controls over financial reporting",
                    "control_type": "administrative",
                    "criticality": "critical",
                }
            ],
        },
        {
            "code": "PCI-DSS",
            "name": "Payment Card Industry Data Security Standard",
            "description": "Security standards for organizations handling credit card information",
            "category": "security",
            "jurisdiction": "Global",
            "enforcement_level": "mandatory",
            "penalty_risk": "critical",
            "applies_to_manufacturing": True,
            "applies_to_region": "Global",
            "industry_specific": "general",
            "official_url": "https://www.pcisecuritystandards.org/",
            "standard_version": "v4.0",
            "controls": [
                {
                    "control_id": "3.2.1",
                    "title": "Data Retention and Disposal",
                    "description": "Do not store sensitive authentication data after authorization",
                    "control_type": "technical",
                    "criticality": "critical",
                },
                {
                    "control_id": "8.2.1",
                    "title": "Strong Authentication",
                    "description": "Use strong authentication methods",
                    "control_type": "technical",
                    "criticality": "critical",
                },
            ],
        },
        {
            "code": "SOC-2",
            "name": "AICPA SOC 2 — Trust Services Criteria",
            "description": "Service Organisation Control 2 framework for managing customer data based on five trust service criteria: security, availability, processing integrity, confidentiality and privacy.",
            "category": "security",
            "jurisdiction": "US",
            "enforcement_level": "recommended",
            "penalty_risk": "medium",
            "applies_to_manufacturing": True,
            "applies_to_region": "Global",
            "industry_specific": "general",
            "official_url": "https://www.aicpa.org/soc",
            "standard_version": "2017",
            "controls": [
                {"control_code": "CC1.1", "title": "COSO Principle 1 — Integrity and ethical values",
                 "description": "The entity demonstrates a commitment to integrity and ethical values.",
                 "category": "Common Criteria", "priority": "high"},
                {"control_code": "CC1.2", "title": "COSO Principle 2 — Board independence and oversight",
                 "description": "The board of directors demonstrates independence from management and exercises oversight.",
                 "category": "Common Criteria", "priority": "medium"},
                {"control_code": "CC2.1", "title": "COSO Principle 13 — Use of relevant information",
                 "description": "The entity obtains or generates and uses relevant, quality information to support internal control.",
                 "category": "Common Criteria", "priority": "medium"},
                {"control_code": "CC3.1", "title": "COSO Principle 14 — Internal communication",
                 "description": "The entity internally communicates information to support internal control.",
                 "category": "Common Criteria", "priority": "medium"},
                {"control_code": "CC4.1", "title": "COSO Principle 16 — Monitoring activities",
                 "description": "The entity selects, develops and performs ongoing evaluations of controls.",
                 "category": "Common Criteria", "priority": "medium"},
                {"control_code": "CC5.1", "title": "COSO Principle 17 — Evaluation of deficiencies",
                 "description": "The entity evaluates and communicates internal control deficiencies in a timely manner.",
                 "category": "Common Criteria", "priority": "medium"},
                {"control_code": "CC6.1", "title": "Logical and physical access controls",
                 "description": "The entity implements logical and physical access controls to protect assets.",
                 "category": "Security", "priority": "critical"},
                {"control_code": "CC6.2", "title": "User access provisioning",
                 "description": "Access to system resources is provisioned based on authorised user roles.",
                 "category": "Security", "priority": "high"},
                {"control_code": "CC6.3", "title": "Security awareness and training",
                 "description": "Personnel responsible for security are provided with appropriate training.",
                 "category": "Security", "priority": "medium"},
                {"control_code": "CC6.6", "title": "External communication threats",
                 "description": "The entity implements controls to detect and mitigate threats from external communications.",
                 "category": "Security", "priority": "high"},
                {"control_code": "CC6.7", "title": "Data encryption and transmission",
                 "description": "The entity encrypts sensitive data at rest and in transit.",
                 "category": "Security", "priority": "high"},
                {"control_code": "CC7.1", "title": "Change detection and monitoring",
                 "description": "The entity detects changes to the system that may affect security.",
                 "category": "Availability", "priority": "high"},
                {"control_code": "CC7.2", "title": "Incident response",
                 "description": "The entity has procedures to respond to security incidents.",
                 "category": "Availability", "priority": "high"},
                {"control_code": "CC8.1", "title": "Change management authorisation",
                 "description": "The entity authorises, designs, develops and tests system changes.",
                 "category": "Processing Integrity", "priority": "medium"},
                {"control_code": "CC9.1", "title": "Confidential information identification",
                 "description": "The entity identifies and maintains confidential information.",
                 "category": "Confidentiality", "priority": "high"},
            ],
        },
        {
            "code": "DORA",
            "name": "EU Digital Operational Resilience Act (DORA)",
            "description": "EU regulation on digital operational resilience for the financial sector, covering ICT risk management, incident reporting, digital operational resilience testing and third-party risk.",
            "category": "security",
            "jurisdiction": "EU",
            "enforcement_level": "mandatory",
            "penalty_risk": "high",
            "applies_to_manufacturing": False,
            "applies_to_region": "EU",
            "industry_specific": "financial",
            "official_url": "https://eur-lex.europa.eu/eli/reg/2022/2554",
            "standard_version": "2022",
            "controls": [
                {"control_code": "DORA-Art.5", "title": "ICT governance and organisation",
                 "description": "The management body shall define and oversee the ICT risk management framework.",
                 "category": "ICT Risk Management", "priority": "critical"},
                {"control_code": "DORA-Art.6", "title": "ICT risk management framework",
                 "description": "A sound ICT risk management framework shall be established as part of the overall risk management system.",
                 "category": "ICT Risk Management", "priority": "critical"},
                {"control_code": "DORA-Art.7", "title": "ICT systems, protocols and tools",
                 "description": "ICT systems and tools shall be identified, classified and adequately protected.",
                 "category": "ICT Risk Management", "priority": "high"},
                {"control_code": "DORA-Art.8", "title": "Identification of ICT-related incidents",
                 "description": "Mechanisms to promptly detect anomalous activities and ICT-related incidents shall be in place.",
                 "category": "ICT Risk Management", "priority": "high"},
                {"control_code": "DORA-Art.9", "title": "Protection and prevention",
                 "description": "Measures shall be in place to protect ICT systems and prevent incidents.",
                 "category": "ICT Risk Management", "priority": "high"},
                {"control_code": "DORA-Art.10", "title": "Detection",
                 "description": "Mechanisms to promptly detect anomalous activities shall be in place.",
                 "category": "ICT Risk Management", "priority": "high"},
                {"control_code": "DORA-Art.11", "title": "Response and recovery",
                 "description": "Comprehensive ICT business continuity policies and disaster recovery plans shall be in place.",
                 "category": "ICT Risk Management", "priority": "critical"},
                {"control_code": "DORA-Art.12", "title": "Backup policies and restoration",
                 "description": "Backup policies and restoration and recovery procedures shall be established.",
                 "category": "ICT Risk Management", "priority": "high"},
                {"control_code": "DORA-Art.13", "title": "Learning and evolving",
                 "description": "Capabilities and staff shall be kept up to date with evolving ICT risk.",
                 "category": "ICT Risk Management", "priority": "medium"},
                {"control_code": "DORA-Art.15", "title": "ICT incident management process",
                 "description": "An ICT incident management process shall be established to detect, manage and notify ICT-related incidents.",
                 "category": "Incident Reporting", "priority": "critical"},
                {"control_code": "DORA-Art.17", "title": "Classification of ICT-related incidents",
                 "description": "ICT-related incidents shall be classified based on criteria including impact and duration.",
                 "category": "Incident Reporting", "priority": "high"},
                {"control_code": "DORA-Art.19", "title": "Reporting of major incidents",
                 "description": "Major ICT-related incidents shall be reported to the competent authority.",
                 "category": "Incident Reporting", "priority": "critical"},
                {"control_code": "DORA-Art.24", "title": "Digital operational resilience testing",
                 "description": "A comprehensive digital operational resilience testing programme shall be established.",
                 "category": "Resilience Testing", "priority": "high"},
                {"control_code": "DORA-Art.26", "title": "Threat-led penetration testing",
                 "description": "Advanced threat-led penetration testing shall be carried out at least every three years.",
                 "category": "Resilience Testing", "priority": "high"},
                {"control_code": "DORA-Art.28", "title": "Third-party ICT risk strategy",
                 "description": "A strategy on ICT third-party risk shall be established and regularly reviewed.",
                 "category": "Third-Party Risk", "priority": "critical"},
            ],
        },
    ]

    @staticmethod
    def load_frameworks_from_json() -> List[Dict]:
        """
        Load frameworks from static JSON file.
        Always available, no database required.

        Returns:
            List of framework dictionaries
        """
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data",
            "compliance_frameworks.json",
        )

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("frameworks", [])
        except FileNotFoundError:
            logger.warning(f"Frameworks JSON not found at {json_path}, using hardcoded data")
            return RegulatoryFrameworkService.MANUFACTURING_FRAMEWORKS
        except Exception as e:
            logger.error(f"Error loading frameworks JSON: {e}")
            return RegulatoryFrameworkService.MANUFACTURING_FRAMEWORKS

    @staticmethod
    def get_framework_by_code(code: str) -> Dict:
        """
        Get a single framework by code from JSON.

        Args:
            code: Framework code (e.g., 'UK-DPP', 'ISO - 9001')

        Returns:
            Framework dictionary or None
        """
        frameworks = RegulatoryFrameworkService.load_frameworks_from_json()
        for fw in frameworks:
            if fw.get("code") == code:
                return fw
        return None

    @staticmethod
    def filter_frameworks(
        jurisdiction: str = None,
        category: str = None,
        enforcement_level: str = None,
        industry_specific: str = None,
    ) -> List[Dict]:
        """
        Filter frameworks from JSON by criteria.

        Args:
            jurisdiction: Filter by jurisdiction (e.g., 'UK', 'US', 'EU', 'Global')
            category: Filter by category (e.g., 'sustainability', 'safety', 'quality')
            enforcement_level: Filter by enforcement (e.g., 'mandatory', 'recommended')
            industry_specific: Filter by industry (e.g., 'manufacturing', 'general')

        Returns:
            Filtered list of frameworks
        """
        frameworks = RegulatoryFrameworkService.load_frameworks_from_json()

        if jurisdiction:
            frameworks = [fw for fw in frameworks if fw.get("jurisdiction") == jurisdiction]

        if category:
            frameworks = [fw for fw in frameworks if fw.get("category") == category]

        if enforcement_level:
            frameworks = [
                fw for fw in frameworks if fw.get("enforcement_level") == enforcement_level
            ]

        if industry_specific:
            frameworks = [
                fw for fw in frameworks if fw.get("industry_specific") == industry_specific
            ]

        return frameworks

    @staticmethod
    def seed_manufacturing_frameworks():
        """
        Seed database with manufacturing-specific regulatory frameworks.
        Now loads from JSON by default.

        Returns:
            Number of frameworks seeded
        """
        logger.info("Seeding manufacturing regulatory frameworks from JSON...")

        seeded_count = 0

        # Load from JSON instead of hardcoded data
        frameworks = RegulatoryFrameworkService.load_frameworks_from_json()

        for framework_data in frameworks:
            # Check if framework already exists
            existing = RegulatoryFramework.query.filter_by(code=framework_data["code"]).first()

            if existing:
                logger.info(f"Framework {framework_data['code']} already exists, skipping")
                continue

            # Extract controls separately
            controls_data = framework_data.pop("controls", [])

            # Create framework
            framework = RegulatoryFramework(**framework_data)
            framework.last_updated = datetime.utcnow()
            framework.next_review_date = datetime.utcnow() + timedelta(days=365)

            db.session.add(framework)
            db.session.flush()  # Get framework ID

            # Create controls
            for control_data in controls_data:
                control = ComplianceControl(framework_id=framework.id, **control_data)
                db.session.add(control)

            seeded_count += 1
            logger.info(f"Seeded framework: {framework.code} with {len(controls_data)} controls")

        db.session.commit()

        logger.info(f"Seeding complete: {seeded_count} frameworks added")
        return seeded_count

    @staticmethod
    def get_applicable_frameworks(
        region: str = None, industry: str = None, category: str = None
    ) -> List[RegulatoryFramework]:
        """
        Get applicable regulatory frameworks based on filters.

        Args:
            region: Geographic region (US, EU, Global)
            industry: Industry type (pharmaceutical, automotive, food, general)
            category: Compliance category (quality, security, safety, environmental)

        Returns:
            List of applicable frameworks
        """
        query = RegulatoryFramework.query.filter_by(status="active")

        if region:
            query = query.filter(
                (RegulatoryFramework.applies_to_region == region)
                | (RegulatoryFramework.applies_to_region == "Global")
            )

        if industry:
            query = query.filter(
                (RegulatoryFramework.industry_specific == industry)
                | (RegulatoryFramework.industry_specific == "general")
            )

        if category:
            query = query.filter_by(category=category)

        return query.all()

    @staticmethod
    def get_critical_controls() -> List[ComplianceControl]:
        """Get all critical compliance controls across all frameworks"""
        return ComplianceControl.query.filter_by(priority="critical").all()

    @staticmethod
    def generate_framework_summary() -> Dict:
        """
        Generate summary of all regulatory frameworks in the system.

        Returns:
            Dictionary with framework statistics
        """
        frameworks = RegulatoryFramework.query.filter_by(status="active").all()

        summary = {
            "total_frameworks": len(frameworks),
            "by_category": {},
            "by_jurisdiction": {},
            "mandatory_count": 0,
            "critical_controls_count": 0,
        }

        for framework in frameworks:
            # Count by category
            category = framework.category or "uncategorized"
            summary["by_category"][category] = summary["by_category"].get(category, 0) + 1

            # Count by jurisdiction
            jurisdiction = framework.jurisdiction or "unknown"
            summary["by_jurisdiction"][jurisdiction] = (
                summary["by_jurisdiction"].get(jurisdiction, 0) + 1
            )

            # Count mandatory
            if framework.enforcement_level == "mandatory":
                summary["mandatory_count"] += 1

            # Count critical controls
            summary["critical_controls_count"] += framework.controls.filter_by(
                priority="critical"
            ).count()

        return summary

    @staticmethod
    def ai_powered_compliance_analysis(product_data: Dict, frameworks: List[str] = None) -> Dict:
        """
        AI-POWERED compliance gap analysis using Claude for UK manufacturing.

        Analyzes product/system against UK manufacturing frameworks including:
        - Digital Product Passport (DPP)
        - EPD/LCA requirements
        - UK REACH, COSHH, UKCA

        Args:
            product_data: Dict containing product specs, BOM, carbon data, etc.
            frameworks: List of framework codes to check (default: all UK frameworks)

        Returns:
            Dict with AI-generated compliance gaps, recommendations, and risk scores
        """
        from app.services.llm_service import LLMService

        if frameworks is None:
            frameworks = ["UK-DPP", "UK-EPD-LCA", "UK-REACH", "UK-HSE-COSHH", "UK-UKCA"]

        # Get framework details
        framework_objs = RegulatoryFramework.query.filter(
            RegulatoryFramework.code.in_(frameworks), RegulatoryFramework.status == "active"
        ).all()

        if not framework_objs:
            return {"error": "No frameworks found"}

        # Build prompt for Claude
        framework_details = []
        for fw in framework_objs:
            controls_text = "\n".join(
                [f"  - {c.control_id}: {c.title} - {c.description}" for c in fw.controls.all()]
            )
            framework_details.append(
                f"## {fw.code}: {fw.name}\n"
                f"Enforcement: {fw.enforcement_level} | Penalty Risk: {fw.penalty_risk}\n"
                f"Controls:\n{controls_text}"
            )

        prompt = f"""You are a UK manufacturing compliance expert specializing in Digital Product Passports, EPD/LCA, and UK regulations.

Analyze the following product/system for compliance gaps:

PRODUCT DATA:
{product_data}

APPLICABLE UK MANUFACTURING FRAMEWORKS:
{''.join(framework_details)}

ANALYSIS REQUIRED:
1. **Compliance Gaps**: Identify specific gaps for each framework control
2. **Risk Assessment**: Rate each gap (Critical/High/Medium/Low)
3. **Implementation Recommendations**: Specific, actionable steps to close gaps
4. **Digital Product Passport Readiness**: Score 0 - 100%
5. **EPD/LCA Readiness**: Score 0 - 100%
6. **Overall Compliance Score**: 0 - 100%

Return JSON format:
{{
  "overall_compliance_score": 0 - 100,
  "dpp_readiness_score": 0 - 100,
  "epd_lca_readiness_score": 0 - 100,
  "gaps": [
    {{
      "framework_code": "UK-DPP",
      "control_id": "DPP - 1",
      "gap_title": "Missing Product Traceability",
      "gap_description": "Product lacks unique digital identifier (GTIN/EPCIS)",
      "risk_level": "critical",
      "impact": "Cannot comply with DPP requirements, product cannot be sold in UK",
      "remediation_action": "Implement GS1 GTIN barcode system and EPCIS event repository",
      "estimated_effort": "4 - 6 weeks",
      "estimated_cost": 15000,
      "priority": "P0"
    }}
  ],
  "recommendations": [
    "Quick wins: Implement product traceability first (highest ROI)",
    "Partner with EPD provider (BRE, IBU) for third-party verification"
  ],
  "uk_manufacturing_insights": "Analysis of UK-specific requirements and best practices"
}}"""

        # Call Claude for analysis
        try:
            llm_service = LLMService()
            response = llm_service.generate_text(
                prompt=prompt, temperature=0.3, max_tokens=4000, response_format="json"
            )

            import json

            result = json.loads(response)
            result["ai_powered"] = True
            result["analyzed_frameworks"] = [fw.code for fw in framework_objs]
            return result

        except Exception as e:
            logger.error(f"AI compliance analysis failed: {str(e)}")
            return {
                "error": str(e),
                "fallback_message": "AI analysis unavailable. Please configure LLM provider in API Settings.",
            }

    @staticmethod
    def generate_dpp_json_template(product_name: str) -> Dict:
        """
        Generate UK Digital Product Passport JSON template.

        Args:
            product_name: Name of the product

        Returns:
            JSON-LD formatted Digital Product Passport template
        """
        from datetime import datetime

        return {
            "@context": "https://schema.org",
            "@type": "ProductPassport",
            "version": "UK-DPP - 2024",
            "productIdentifier": {
                "gtin": "01234567890128",  # GS1 GTIN - 13
                "serialNumber": "",  # Unique per unit
                "batchNumber": "",
            },
            "productInformation": {
                "name": product_name,
                "manufacturer": {
                    "name": "",
                    "address": "",
                    "registrationNumber": "",  # UK Companies House number
                },
                "manufactureDate": datetime.utcnow().isoformat(),
                "countryOfOrigin": "GB",
            },
            "materialComposition": {
                "billOfMaterials": [
                    {
                        "material": "Steel",
                        "percentage": 60.0,
                        "recycledContent": 30.0,  # % recycled
                        "source": "UK supplier",
                        "reachCompliant": True,
                        "svhcSubstances": [],  # List of SVHC if >0.1%
                    }
                ]
            },
            "environmentalImpact": {
                "carbonFootprint": {
                    "totalCO2e_kg": 0.0,  # kg CO2e (cradle-to-gate)
                    "methodology": "PEF/PCR",
                    "verifiedBy": "",  # Third-party verifier
                    "epdUrl": "",  # Link to full EPD
                },
                "waterFootprint": {"totalWaterUse_m3": 0.0, "waterScarcityIndex": 0.0},
                "energyUse": {"embodiedEnergy_MJ": 0.0},
            },
            "circularityMetrics": {
                "recycledInputRate": 0.0,  # %
                "recyclabilityRate": 0.0,  # %
                "expectedLifespan": {"value": 10, "unit": "years"},
                "repairabilityScore": 0,  # 0 - 100
                "sparePartsAvailability": "7 years",
            },
            "endOfLife": {
                "disassemblyInstructions": "",
                "recyclingCodes": [],
                "hazardousComponents": [],
            },
            "supplyChainTransparency": {
                "tier1Suppliers": [],
                "modernSlaveryCompliance": True,
                "laborStandards": "ILO compliant",
            },
            "passportAccessibility": {
                "qrCodeUrl": "",
                "apiEndpoint": "",
                "format": "JSON-LD",
                "lastUpdated": datetime.utcnow().isoformat(),
            },
        }
