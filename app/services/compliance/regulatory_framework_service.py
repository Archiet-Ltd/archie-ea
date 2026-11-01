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
from app.services.compliance.earlier_catalogue_seeds import matches_an_earlier_load
from app.models.compliance_models import (
    ComplianceControl,
    RegulatoryFramework,
)

logger = logging.getLogger(__name__)

# DORA (Regulation (EU) 2022/2554) and NIS2 (Directive (EU) 2022/2555), one row per article or measure.
# Titles are the official article headings (EUR-Lex, (c) European Union, reused with acknowledgement); the
# descriptions are written in our own words and say what an organisation has to do.
_DORA_PILLARS = (
    ("ICT Risk Management", 5, 16), ("Incident Reporting", 17, 23), ("Resilience Testing", 24, 27),
    ("Third-Party Risk", 28, 44), ("Information Sharing", 45, 45),
)
_DORA_ARTICLES = [
    ("5", "Governance and organisation", "Have your leadership approve, oversee and answer for how the firm manages ICT risk.", "critical"),
    ("6", "ICT risk management framework", "Keep a written plan for ICT risk, with the policies, tools and people to carry it out.", "critical"),
    ("7", "ICT systems, protocols and tools", "Run ICT systems that are current, reliable and big enough for the work they do.", "high"),
    ("8", "Identification", "List your ICT-supported business functions and assets, and record what depends on what.", "high"),
    ("9", "Protection and prevention", "Put in place controls that keep systems and data secure, available and accurate.", "high"),
    ("10", "Detection", "Watch for unusual activity so ICT problems are noticed early.", "high"),
    ("11", "Response and recovery", "Keep continuity and recovery plans for ICT disruption, and practise them.", "critical"),
    ("12", "Backup and restoration", "Back up data on a set policy and prove you can restore it.", "high"),
    ("13", "Learning and evolving", "Review what each incident or test taught you, and train staff to match.", "medium"),
    ("14", "Communication", "Agree who tells clients, staff and the public about ICT incidents, and how.", "medium"),
    ("15", "Further harmonisation of risk tools", "Authorities will set shared technical standards for ICT risk tools; follow them once published.", "low"),
    ("16", "Simplified framework for smaller firms", "Smaller or simpler firms may use a lighter ICT risk approach if they qualify.", "low"),
    ("17", "Incident management process", "Set up a process to find, log, handle and follow up ICT incidents.", "critical"),
    ("18", "Classification of incidents and threats", "Sort incidents by how many clients are hit, how long they last and how far they spread.", "high"),
    ("19", "Reporting of major incidents", "Report major ICT incidents to your regulator, with updates until they are resolved.", "critical"),
    ("20", "Harmonised reporting content and templates", "Use the standard forms and deadlines for incident reports once they are published.", "low"),
    ("21", "Centralised reporting", "Authorities are studying one shared EU channel for incident reports; nothing to do yet.", "low"),
    ("22", "Supervisory feedback", "Expect feedback from your regulator after you report, and use it to improve.", "low"),
    ("23", "Payment-related incidents", "If an incident disrupts payments, report it under the same rules.", "medium"),
    ("24", "General testing requirements", "Run a resilience testing programme that fits the size and risk of the firm.", "high"),
    ("25", "Testing of ICT tools and systems", "Test systems each year with exercises such as scenarios, performance checks and penetration tests.", "high"),
    ("26", "Threat-led penetration testing", "Larger firms must also have live systems tested by realistic attackers every three years.", "high"),
    ("27", "Requirements for testers", "Use testers who are skilled and independent, and manage the risks of the test itself.", "medium"),
    ("28", "ICT third-party risk and register of information", "Keep a strategy and a register of every ICT provider contract, including the subcontracting chain.", "critical"),
    ("29", "ICT concentration risk assessment", "Before relying on a provider, check you are not too dependent on that one provider.", "high"),
    ("30", "Key contractual provisions", "Make sure ICT contracts cover the terms the regulation lists, especially when subcontracting supports critical functions.", "critical"),
    ("31", "Designation of critical providers", "Authorities choose which ICT providers count as critical; a provider you use may be named.", "low"),
    ("32", "Oversight framework", "Describes how EU authorities will oversee critical providers; no action for you.", "low"),
    ("33", "Tasks of the lead overseer", "Sets what the lead overseer checks at each critical provider; no action for you.", "low"),
    ("34", "Coordination between lead overseers", "Describes how the lead overseers share oversight work; no action for you.", "low"),
    ("35", "Powers of the lead overseer", "Lets the lead overseer ask a critical provider for information and make recommendations.", "low"),
    ("36", "Powers outside the Union", "Extends oversight to critical providers based outside the EU.", "low"),
    ("37", "Requests for information", "Lets the lead overseer ask critical providers for documents and data.", "low"),
    ("38", "General investigations", "Lets the lead overseer investigate critical providers.", "low"),
    ("39", "Inspections", "Lets the lead overseer visit critical providers on site.", "low"),
    ("40", "Ongoing oversight", "Sets up joint bodies that keep watching critical providers over time.", "low"),
    ("41", "Harmonised oversight conditions", "Authorities will agree the conditions that make this oversight workable.", "low"),
    ("42", "Follow-up by authorities", "If a provider is told to fix something, authorities may ask you to act on it too.", "medium"),
    ("43", "Oversight fees", "Critical providers pay fees for oversight; that cost may reach their clients.", "low"),
    ("44", "International cooperation", "Authorities work with their counterparts outside the EU on these matters.", "low"),
    ("45", "Cyber threat information sharing", "You may join arrangements to share information about cyber threats with other firms.", "medium"),
]
_NIS2_ARTICLES = [
    ("21(2)(a)", "Risk analysis and security policies", "Write down how you assess risk, and set policies for keeping information systems secure.", "critical"),
    ("21(2)(b)", "Incident handling", "Have a way to prevent, spot and respond to security incidents.", "critical"),
    ("21(2)(c)", "Continuity, backup and crisis management", "Plan for disruption: continuity, backups, disaster recovery and crisis handling.", "critical"),
    ("21(2)(d)", "Supply chain security", "Manage the security risk that comes from your direct suppliers and service providers.", "high"),
    ("21(2)(e)", "Security in acquisition, development and maintenance", "Build security into buying, developing and maintaining systems, including fixing and disclosing vulnerabilities.", "high"),
    ("21(2)(f)", "Assessing the effectiveness of measures", "Check that your security measures actually work.", "medium"),
    ("21(2)(g)", "Cyber hygiene and training", "Practise basic cyber hygiene and train staff in cybersecurity.", "medium"),
    ("21(2)(h)", "Cryptography and encryption", "Decide when and how to use cryptography and encryption.", "high"),
    ("21(2)(i)", "Personnel security, access control and assets", "Vet and manage staff, control who can access what, and keep an inventory of assets.", "high"),
    ("21(2)(j)", "Multi-factor authentication and secure communications", "Use multi-factor sign-in and secure channels, including a backup way to communicate in a crisis.", "high"),
    ("23(4)(a)", "Early warning of a significant incident", "Send the authority a first warning within 24 hours of learning of a significant incident.", "critical"),
    ("23(4)(b)", "Incident notification", "Send an incident notification with an initial assessment within 72 hours.", "critical"),
    ("23(4)(d)", "Final incident report", "Send a final report within one month of the notification.", "high"),
]


def _article_rows(prefix: str, instrument: str, rows: List) -> List[Dict]:
    """Catalogue controls for a regulation: the code carries the article, the category the pillar or group,
    and ``official_reference`` the instrument and article."""
    controls = []
    for article, title, text, priority in rows:
        number = int(article) if article.isdigit() else None
        if number is not None:
            group = next(name for name, low, high in _DORA_PILLARS if low <= number <= high)
        else:
            group = "Risk-management measures" if article.startswith("21") else "Incident reporting"
        controls.append({
            "control_code": prefix + article, "title": title, "description": text,
            "category": group, "priority": priority,
            "official_reference": "%s, Article %s" % (instrument, article),
        })
    return controls



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
            "description": "EU regulation on digital operational resilience for financial entities, in five pillars: ICT risk management, incident reporting, resilience testing, ICT third-party risk and information sharing.",
            "category": "security",
            "jurisdiction": "EU",
            "enforcement_level": "mandatory",
            "penalty_risk": "high",
            "applies_to_manufacturing": False,
            "applies_to_region": "EU",
            "industry_specific": "financial",
            "official_url": "https://eur-lex.europa.eu/eli/reg/2022/2554",
            "standard_version": "2022",
            "controls": _article_rows("DORA-Art.", "Regulation (EU) 2022/2554", _DORA_ARTICLES),
        },
        {
            "code": "NIS2",
            "name": "EU Network and Information Security Directive (NIS2)",
            "description": "EU directive on cybersecurity for essential and important entities: the ten risk-management measures of Article 21(2) and the incident reporting of Article 23.",
            "category": "security",
            "jurisdiction": "EU",
            "enforcement_level": "mandatory",
            "penalty_risk": "high",
            "applies_to_manufacturing": True,
            "applies_to_region": "EU",
            "industry_specific": "general",
            "official_url": "https://eur-lex.europa.eu/eli/dir/2022/2555",
            "standard_version": "2022",
            "controls": _article_rows("NIS2-Art.", "Directive (EU) 2022/2555", _NIS2_ARTICLES),
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

        from app.services.compliance.method_frameworks import METHOD_FRAMEWORKS

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("frameworks", []) + METHOD_FRAMEWORKS
        except FileNotFoundError:
            logger.warning(f"Frameworks JSON not found at {json_path}, using hardcoded data")
            return RegulatoryFrameworkService.MANUFACTURING_FRAMEWORKS + METHOD_FRAMEWORKS
        except Exception as e:
            logger.error(f"Error loading frameworks JSON: {e}")
            return RegulatoryFrameworkService.MANUFACTURING_FRAMEWORKS + METHOD_FRAMEWORKS

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
    def _control_columns(control_data: Dict) -> Dict:
        """The control as ComplianceControl columns: most catalogue entries name the code, the type and the
        criticality ``control_id``, ``control_type`` and ``criticality``; the columns are ``control_code``,
        ``category`` and ``priority``."""
        renamed = {"control_id": "control_code", "control_type": "category", "criticality": "priority"}
        return {renamed.get(key, key): value for key, value in control_data.items()}

    @staticmethod
    def correct_unedited_controls(framework_id: int) -> int:
        """Bring the catalogue rows of one framework to the current catalogue when they still read exactly
        as an earlier release loaded them (title, heading, reference and description); a row anyone has
        edited is never touched. Returns how many rows were corrected."""
        framework = db.session.get(RegulatoryFramework, framework_id)
        if framework is None:
            return 0
        current = {}
        for framework_data in RegulatoryFrameworkService.load_frameworks_from_json():
            if framework_data["code"] == framework.code:
                for control_data in framework_data.get("controls", []):
                    columns = RegulatoryFrameworkService._control_columns(control_data)
                    current[columns["control_code"]] = columns
        corrected = 0
        for row in ComplianceControl.query.filter_by(framework_id=framework.id):
            wanted = current.get(row.control_code)
            if wanted is None or not matches_an_earlier_load(
                framework.code, row.control_code, row.title, row.category, row.official_reference, row.description
            ):
                continue
            fresh = (wanted.get("title"), wanted.get("category"), wanted.get("official_reference"), wanted.get("description"))
            if fresh != (row.title, row.category, row.official_reference, row.description):
                row.title, row.category, row.official_reference, row.description = fresh
                corrected += 1
        return corrected

    @staticmethod
    def seed_manufacturing_frameworks(codes=None):
        """
        Seed database with manufacturing-specific regulatory frameworks.
        Now loads from JSON by default. ``codes`` limits the seeding to those framework codes.

        Returns:
            Number of frameworks seeded
        """
        logger.info("Seeding manufacturing regulatory frameworks from JSON...")

        seeded_count = 0

        # Load from JSON instead of hardcoded data
        frameworks = RegulatoryFrameworkService.load_frameworks_from_json()

        for framework_data in frameworks:
            if codes is not None and framework_data["code"] not in codes:
                continue
            # Check if framework already exists
            existing = RegulatoryFramework.query.filter_by(code=framework_data["code"]).first()

            if existing:
                # A catalogue that grew since it was first loaded gets its new rows; nothing already there changes.
                held = {c.control_code for c in ComplianceControl.query.filter_by(framework_id=existing.id)}
                for control_data in framework_data.get("controls", []):
                    columns = RegulatoryFrameworkService._control_columns(control_data)
                    if columns["control_code"] not in held:
                        db.session.add(ComplianceControl(framework_id=existing.id, **columns))
                logger.info(f"Framework {framework_data['code']} already exists, topped up")
                continue

            # Extract controls separately
            # (read, not popped: the fallback list is shared by every call in the process)
            controls_data = framework_data.get("controls", [])

            # Create framework
            framework = RegulatoryFramework(**{k: v for k, v in framework_data.items() if k != "controls"})
            framework.last_updated = datetime.utcnow()
            framework.next_review_date = datetime.utcnow() + timedelta(days=365)

            db.session.add(framework)
            db.session.flush()  # Get framework ID

            # Create controls
            for control_data in controls_data:
                control = ComplianceControl(
                    framework_id=framework.id, **RegulatoryFrameworkService._control_columns(control_data)
                )
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
