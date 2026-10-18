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

The frameworks that carry a full control catalogue (ISO/IEC 27001, SOC 2, GDPR, NIS2,
DORA, the EU AI Act) live in one place, app/services/compliance/data/default_catalogue.json.
The manufacturing list below holds the other frameworks. ``seed_manufacturing_frameworks``
(the ``seed-framework-catalogue`` command) loads both through one helper.
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
    # Includes Digital Product Passport (DPP), EPD/LCA, and UK regulations.
    # ISO 27001, SOC 2, GDPR and DORA are NOT here: they come from the default
    # catalogue file, the one source for those codes.
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

    # Keys older framework lists used for control fields, mapped onto the columns.
    _LEGACY_CONTROL_KEYS = {
        "control_id": "control_code",
        "criticality": "priority",
        "control_type": "category",
    }

    # Titles this platform itself shipped wrongly in an earlier release, keyed by
    # (framework code, control code) -> (the title it shipped, the correct title).
    # The seeder only adds missing codes, so a database that loaded the old rows
    # would otherwise keep them. A row is corrected only while its title is still
    # exactly the one that shipped: a title an administrator has since edited is
    # never touched.
    KNOWN_WRONG_CONTROL_TITLES = {
        ("ISO - 27001", "A.8.2"): ("Access Control", "Privileged access rights"),
        ("SOC-2", "CC3.1"): (
            "COSO Principle 14 \u2014 Internal communication",
            "Objectives specified clearly enough to assess risk",
        ),
        ("SOC-2", "CC5.1"): (
            "COSO Principle 17 \u2014 Evaluation of deficiencies",
            "Selection and development of control activities",
        ),
        ("SOC-2", "CC6.3"): (
            "Security awareness and training",
            "Role-based access and least privilege",
        ),
        ("DORA", "DORA-Art.15"): (
            "ICT incident management process",
            "Further harmonisation of ICT risk management tools, methods, processes and policies",
        ),
        ("DORA", "DORA-Art.17"): (
            "Classification of ICT-related incidents",
            "ICT-related incident management process",
        ),
    }

    @staticmethod
    def _columns(model) -> set:
        return {c.name for c in model.__table__.columns}

    @staticmethod
    def _control_values(control_data: Dict) -> Dict:
        """Translate one control dict to ComplianceControl column values."""
        columns = RegulatoryFrameworkService._columns(ComplianceControl)
        values = {}
        for key, value in control_data.items():
            key = RegulatoryFrameworkService._LEGACY_CONTROL_KEYS.get(key, key)
            if key in columns and key not in ("id", "framework_id"):
                values.setdefault(key, value)
        return values

    @staticmethod
    def validate_framework_list(frameworks: List[Dict], source: str) -> None:
        """Raise ValueError naming the entry when a framework list is incomplete.

        Runs on every list the seeder loads -- the shipped catalogue file and the
        manufacturing list alike -- so a control with no code fails with a message
        that names it instead of a KeyError in the middle of a seed.
        """
        seen_frameworks = set()
        for fw in frameworks:
            code = fw.get("code")
            if not code or not fw.get("name"):
                raise ValueError(f"{source}: a framework needs a code and a name: {fw.get('code')!r}")
            if code in seen_frameworks:
                raise ValueError(f"{source}: duplicate framework code {code!r}")
            seen_frameworks.add(code)
            seen = set()
            for control in fw.get("controls", []):
                values = RegulatoryFrameworkService._control_values(control)
                for field in ("control_code", "title"):
                    if not values.get(field):
                        raise ValueError(f"{source}: {code}: control {control!r} has no {field}")
                if values["control_code"] in seen:
                    raise ValueError(
                        f"{source}: {code}: duplicate control_code {values['control_code']}"
                    )
                seen.add(values["control_code"])

    @staticmethod
    def _add_to_existing_adoptions(framework, new_controls) -> int:
        """Give every organisation that already adopted ``framework`` the new controls.

        An adoption copies the framework's controls when it is made, so controls
        added to the catalogue afterwards would be missing from it and its
        evidence coverage would read complete. Each new control is added as
        "planned" -- not started -- to the adoption it belongs to, under that
        adoption's own organisation. Returns the number of rows added.
        """
        if not new_controls:
            return 0
        from app.models.application_compliance import ApplicationComplianceControl
        from app.models.regulatory_framework import FrameworkAdoption

        added = 0
        adoptions = FrameworkAdoption.query.filter(
            FrameworkAdoption.framework_id == framework.id,
            FrameworkAdoption.organization_id.isnot(None),
        ).all()
        for adoption in adoptions:
            for control in new_controls:
                db.session.add(
                    ApplicationComplianceControl(
                        organization_id=adoption.organization_id,
                        adoption_id=adoption.id,
                        control_id=control.id,
                        implementation_status="planned",
                    )
                )
                added += 1
        return added

    @staticmethod
    def upsert_framework_with_controls(framework_data: Dict, add_missing_controls: bool = True):
        """Create a framework with its controls, or top up one that already exists.

        A framework that already exists (matched on ``code``) keeps its own row.
        With ``add_missing_controls`` the controls whose ``control_code`` is absent
        are added -- and given to every organisation that already adopted the
        framework -- and a control still carrying a title this platform shipped
        wrongly (``KNOWN_WRONG_CONTROL_TITLES``) is corrected. Any other existing
        control is never edited. Does not commit.

        Returns:
            (framework, created, controls_added, titles_corrected)
        """
        data = dict(framework_data)
        controls_data = data.pop("controls", [])

        framework = RegulatoryFramework.query.filter_by(code=data["code"]).first()
        created = framework is None
        if created:
            columns = RegulatoryFrameworkService._columns(RegulatoryFramework)
            framework = RegulatoryFramework(
                **{k: v for k, v in data.items() if k in columns and k != "id"}
            )
            framework.last_updated = datetime.utcnow()
            framework.next_review_date = datetime.utcnow() + timedelta(days=365)
            db.session.add(framework)
            db.session.flush()  # Get framework ID
        elif not add_missing_controls:
            return framework, False, 0, 0

        existing = (
            {}
            if created
            else {
                control.control_code: control
                for control in ComplianceControl.query.filter_by(framework_id=framework.id)
            }
        )
        new_controls = []
        corrected = 0
        for control_data in controls_data:
            values = RegulatoryFrameworkService._control_values(control_data)
            code = values["control_code"]
            if code in existing:
                continue
            control = ComplianceControl(framework_id=framework.id, **values)
            db.session.add(control)
            existing[code] = control
            new_controls.append(control)
        # Corrections run on what the database already holds, independent of
        # whether the current catalogue still lists the control.
        for (fw_code, control_code), (wrong, right) in (
            RegulatoryFrameworkService.KNOWN_WRONG_CONTROL_TITLES.items()
        ):
            current = existing.get(control_code)
            if fw_code == framework.code and current is not None and current.title == wrong:
                current.title = right
                corrected += 1
        db.session.flush()
        if not created:
            RegulatoryFrameworkService._add_to_existing_adoptions(framework, new_controls)
            db.session.flush()
        return framework, created, len(new_controls), corrected

    @staticmethod
    def load_default_catalogue() -> Dict:
        """Load and validate the default global catalogue shipped with the app."""
        json_path = os.path.join(os.path.dirname(__file__), "data", "default_catalogue.json")
        with open(json_path, "r", encoding="utf-8") as f:
            catalogue = json.load(f)
        if not catalogue.get("catalogue_version"):
            raise ValueError("default catalogue has no catalogue_version")
        RegulatoryFrameworkService.validate_framework_list(
            catalogue.get("frameworks", []), "default catalogue"
        )
        return catalogue

    @staticmethod
    def seed_manufacturing_frameworks():
        """
        Seed the shared framework catalogue: the manufacturing list and the default
        catalogue file, both through ``upsert_framework_with_controls``.

        Safe to run repeatedly. Creates missing frameworks, adds missing controls
        to every framework (and to organisations that already adopted it), and
        corrects the titles listed in ``KNOWN_WRONG_CONTROL_TITLES``. Global master
        data: no organisation's own rows are edited.

        Returns:
            Number of frameworks added
        """
        logger.info("Seeding the shared regulatory framework catalogue...")

        manufacturing = [dict(fw) for fw in RegulatoryFrameworkService.MANUFACTURING_FRAMEWORKS]
        RegulatoryFrameworkService.validate_framework_list(manufacturing, "manufacturing list")
        catalogue = RegulatoryFrameworkService.load_default_catalogue()

        seeded_count = 0
        controls_added = 0
        titles_corrected = 0
        for framework_data in manufacturing + catalogue["frameworks"]:
            _, created, added, corrected = RegulatoryFrameworkService.upsert_framework_with_controls(
                framework_data
            )
            seeded_count += 1 if created else 0
            controls_added += added
            titles_corrected += corrected

        db.session.commit()

        logger.info(
            "Seeding complete (catalogue %s): %d frameworks added, %d controls added, %d titles corrected",
            catalogue["catalogue_version"],
            seeded_count,
            controls_added,
            titles_corrected,
        )
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
