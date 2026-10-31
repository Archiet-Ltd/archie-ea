"""

Application Architecture Mapper Service

Comprehensive service for mapping applications to:
1. Business Capabilities
2. APQC PCF Processes
3. ArchiMate 3.2 Elements (Application Layer)
4. Vendor ArchiMate Elements (cloned from vendor templates)

Uses AI analysis of application data including:
- Name, description, vendor, technology stack
- Imported capabilities/functionality text
- Imported APQC process codes
- Business functions and context
"""

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, text

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
from app.models.archimate_core import ArchiMateElement, ArchitectureModel
from app.models.unified_capability import UnifiedCapability
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

# ============================================================================
# INTELLIGENT VENDOR & APQC MAPPING DICTIONARIES
# ============================================================================

# Product name patterns that map to vendor organizations
PRODUCT_TO_VENDOR_MAP = {
    # Microsoft products
    "microsoft": "Microsoft",
    "navision": "Microsoft",
    "dynamics": "Microsoft",
    "azure": "Microsoft",
    "office 365": "Microsoft",
    "o365": "Microsoft",
    "sharepoint": "Microsoft",
    "power bi": "Microsoft",
    "powerbi": "Microsoft",
    "teams": "Microsoft",
    "outlook": "Microsoft",
    "excel": "Microsoft",
    "onedrive": "Microsoft",
    "intune": "Microsoft",
    "active directory": "Microsoft",
    "sql server": "Microsoft",
    ".net": "Microsoft",
    "visual studio": "Microsoft",
    "windows": "Microsoft",
    # SAP products
    "sap": "SAP",
    "successfactors": "SAP",
    "ariba": "SAP",
    "s/4hana": "SAP",
    "concur": "SAP",
    "hybris": "SAP",
    "businessobjects": "SAP",
    "hana": "SAP",
    # Oracle products
    "oracle": "Oracle",
    "peoplesoft": "Oracle",
    "jd edwards": "Oracle",
    "siebel": "Oracle",
    "hyperion": "Oracle",
    "netsuite": "Oracle",
    "primavera": "Oracle",
    # Salesforce products
    "salesforce": "Salesforce",
    "sfdc": "Salesforce",
    "pardot": "Salesforce",
    "heroku": "Salesforce",
    "mulesoft": "Salesforce",
    "tableau": "Salesforce",
    # ServiceNow products
    "servicenow": "ServiceNow",
    "snow": "ServiceNow",
    # Workday products
    "workday": "Workday",
    # Google products
    "google": "Google Cloud",
    "gcp": "Google Cloud",
    "bigquery": "Google Cloud",
    "firebase": "Google Cloud",
    "google cloud": "Google Cloud",
    # AWS products
    "aws": "Amazon Web Services",
    "amazon": "Amazon Web Services",
    "ec2": "Amazon Web Services",
    "s3": "Amazon Web Services",
    "lambda": "Amazon Web Services",
    "cloudwatch": "Amazon Web Services",
    # Atlassian products
    "jira": "Atlassian",
    "confluence": "Atlassian",
    "bitbucket": "Atlassian",
    "trello": "Atlassian",
    "atlassian": "Atlassian",
    # IBM products
    "ibm": "IBM",
    "cognos": "IBM",
    "websphere": "IBM",
    "maximo": "IBM",
    "db2": "IBM",
    # VMware products
    "vmware": "VMware",
    "vsphere": "VMware",
    "esxi": "VMware",
    "vcenter": "VMware",
    # Cisco products
    "cisco": "Cisco",
    "webex": "Cisco",
    "meraki": "Cisco",
    # Adobe products
    "adobe": "Adobe",
    "photoshop": "Adobe",
    "acrobat": "Adobe",
    "creative cloud": "Adobe",
    "marketo": "Adobe",
    "magento": "Adobe",
    "experience manager": "Adobe",
    # Zoom products
    "zoom": "Zoom",
    # Slack products
    "slack": "Slack",
    # Splunk products
    "splunk": "Splunk",
    # Snowflake
    "snowflake": "Snowflake",
    # Anaplan
    "anaplan": "Anaplan",
    # Red Hat
    "red hat": "Red Hat",
    "redhat": "Red Hat",
    "ansible": "Red Hat",
    "openshift": "Red Hat",
    # Dell
    "dell": "Dell",
    "emc": "Dell",
    # HPE
    "hpe": "HPE",
    "hewlett": "HPE",
    # NetApp
    "netapp": "NetApp",
    # Citrix
    "citrix": "Citrix",
    "xenapp": "Citrix",
    "xendesktop": "Citrix",
    # Palo Alto
    "palo alto": "Palo Alto Networks",
    "prisma": "Palo Alto Networks",
    # CrowdStrike
    "crowdstrike": "CrowdStrike",
    "falcon": "CrowdStrike",
    # Datadog
    "datadog": "Datadog",
    # Dynatrace
    "dynatrace": "Dynatrace",
    # GitLab
    "gitlab": "GitLab",
    # GitHub
    "github": "GitHub",
    # Zoho
    "zoho": "Zoho",
    # Freshworks
    "freshworks": "Freshworks",
    "freshdesk": "Freshworks",
    "freshservice": "Freshworks",
    # Zendesk
    "zendesk": "Zendesk",
    # HubSpot
    "hubspot": "HubSpot",
    # Monday.com
    "monday": "Monday.com",
    # Asana
    "asana": "Asana",
    # Box
    "box": "Box",
    # Dropbox
    "dropbox": "Dropbox",
    # DocuSign
    "docusign": "DocuSign",
    # Okta
    "okta": "Okta",
    # Ping Identity
    "ping": "Ping Identity",
    "pingidentity": "Ping Identity",
    # SailPoint
    "sailpoint": "SailPoint",
    # CyberArk
    "cyberark": "CyberArk",
    # Qualys
    "qualys": "Qualys",
    # Tenable
    "tenable": "Tenable",
    "nessus": "Tenable",
    # Rapid7
    "rapid7": "Rapid7",
    # Fortinet
    "fortinet": "Fortinet",
    "fortigate": "Fortinet",
    # SolarWinds
    "solarwinds": "SolarWinds",
    # Acronis
    "acronis": "Acronis",
    # Veeam
    "veeam": "Veeam",
    # Commvault
    "commvault": "Commvault",
    # Veritas
    "veritas": "Veritas",
    # Informatica
    "informatica": "Informatica",
    # Talend
    "talend": "Talend",
    # Boomi
    "boomi": "Boomi",
    # MicroStrategy
    "microstrategy": "MicroStrategy",
    # Qlik
    "qlik": "Qlik",
    # SAS
    "sas": "SAS",
    # TIBCO
    "tibco": "TIBCO",
    # Infor
    "infor": "Infor",
    # Epicor
    "epicor": "Epicor",
    # Sage
    "sage": "Sage",
    # Coupa
    "coupa": "Coupa",
    # Planview
    "planview": "Planview",
    # ServiceMax
    "servicemax": "ServiceMax",
    # Veeva
    "veeva": "Veeva",
    # Cornerstone
    "cornerstone": "Cornerstone OnDemand",
    # ADP
    "adp": "ADP",
    # UKG
    "ukg": "UKG",
    "kronos": "UKG",
    "ultimate software": "UKG",
    # Paylocity
    "paylocity": "Paylocity",
    # Paycom
    "paycom": "Paycom",
    # Ceridian
    "ceridian": "Ceridian",
    "dayforce": "Ceridian",
    # Financial/ERP - Additional
    "quickbooks": "Intuit",
    "intuit": "Intuit",
    "turbotax": "Intuit",
    "xero": "Xero",
    "freshbooks": "FreshBooks",
    "blackline": "BlackLine",
    "onestream": "OneStream",
    "planful": "Planful",
    "adaptive insights": "Workday",
    "blackrock": "BlackRock",
    "aladdin": "BlackRock",
    "bloomberg": "Bloomberg",
    "refinitiv": "Refinitiv",
    "factset": "FactSet",
    "morningstar": "Morningstar",
    # Legal/Compliance
    "lexisnexis": "LexisNexis",
    "westlaw": "Thomson Reuters",
    "thomson reuters": "Thomson Reuters",
    "relativity": "Relativity",
    "clio": "Clio",
    "contractpodai": "ContractPodAi",
    # Project Management
    "smartsheet": "Smartsheet",
    "basecamp": "Basecamp",
    "wrike": "Wrike",
    "clickup": "ClickUp",
    "notion": "Notion",
    "airtable": "Airtable",
    "teamwork": "Teamwork",
    # Communication/Collaboration
    "twilio": "Twilio",
    "sendgrid": "Twilio",
    "intercom": "Intercom",
    "drift": "Drift",
    "ringcentral": "RingCentral",
    "vonage": "Vonage",
    "mattermost": "Mattermost",
    "discord": "Discord",
    # Security - Additional
    "zscaler": "Zscaler",
    "netskope": "Netskope",
    "cloudflare": "Cloudflare",
    "akamai": "Akamai",
    "symantec": "Broadcom",
    "mcafee": "Trellix",
    "carbon black": "VMware",
    "sentinelone": "SentinelOne",
    "snyk": "Snyk",
    "veracode": "Veracode",
    "checkmarx": "Checkmarx",
    "beyond trust": "BeyondTrust",
    "beyondtrust": "BeyondTrust",
    "thycotic": "Delinea",
    "delinea": "Delinea",
    # Data/Analytics - Additional
    "looker": "Google Cloud",
    "domo": "Domo",
    "alteryx": "Alteryx",
    "thoughtspot": "ThoughtSpot",
    "fivetran": "Fivetran",
    "dbt": "dbt Labs",
    "databricks": "Databricks",
    "palantir": "Palantir",
    "elastic": "Elastic",
    "elasticsearch": "Elastic",
    "mongo": "MongoDB",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "confluent": "Confluent",
    "kafka": "Confluent",
    # Healthcare-specific
    "epic": "Epic Systems",
    "cerner": "Oracle",
    "meditech": "MEDITECH",
    "allscripts": "Veradigm",
    "athenahealth": "athenahealth",
    "eclinicalworks": "eClinicalWorks",
    "nextgen": "NextGen Healthcare",
    # Manufacturing/Industrial
    "siemens": "Siemens",
    "rockwell": "Rockwell Automation",
    "honeywell": "Honeywell",
    "ge digital": "GE Digital",
    "ptc": "PTC",
    "windchill": "PTC",
    "dassault": "Dassault Systemes",
    "autodesk": "Autodesk",
    "solidworks": "Dassault Systemes",
    # Document/Content Management
    "opentext": "OpenText",
    "hyland": "Hyland",
    "alfresco": "Hyland",
    "m-files": "M-Files",
    # DevOps/Infrastructure
    "hashicorp": "HashiCorp",
    "terraform": "HashiCorp",
    "vault": "HashiCorp",
    "jenkins": "Jenkins",
    "circleci": "CircleCI",
    "puppet": "Puppet",
    "chef": "Progress",
    "new relic": "New Relic",
    "newrelic": "New Relic",
    "pagerduty": "PagerDuty",
    "opsgenie": "Atlassian",
}

# APQC PCF process keywords for auto-mapping
APQC_KEYWORD_MAP = {
    # Finance processes (8.x)
    "finance": ["8.1", "8.2", "8.3"],
    "accounting": ["8.2.1", "8.2.2"],
    "accounts payable": ["8.3.1"],
    "accounts receivable": ["8.3.2"],
    "budgeting": ["8.4.1"],
    "treasury": ["8.5"],
    "tax": ["8.6"],
    "audit": ["8.7", "10.5.1"],
    "financial reporting": ["8.2.3"],
    "general ledger": ["8.2.1"],
    "fixed assets": ["8.3.3"],
    "cost management": ["8.4.2"],
    # HR processes (6.x)
    "hr": ["6.1", "6.2", "6.3"],
    "human resources": ["6.1", "6.2"],
    "payroll": ["6.4.1"],
    "recruiting": ["6.2.1"],
    "talent": ["6.2", "6.3"],
    "onboarding": ["6.2.3"],
    "training": ["6.3.1"],
    "performance": ["6.3.2"],
    "compensation": ["6.4"],
    "benefits": ["6.4.2"],
    "workforce": ["6.5"],
    # IT processes (7.x)
    "it service": ["7.1", "7.2"],
    "service desk": ["7.1.1"],
    "incident": ["7.1.2"],
    "problem": ["7.1.3"],
    "change management": ["7.1.4"],
    "infrastructure": ["7.2"],
    "network": ["7.2.1"],
    "security": ["7.3"],
    "cybersecurity": ["7.3.1"],
    "data center": ["7.2.2"],
    "cloud": ["7.2.3"],
    "application": ["7.4"],
    # Supply Chain (4.x)
    "supply chain": ["4.1", "4.2", "4.3"],
    "procurement": ["4.1.1"],
    "purchasing": ["4.1.2"],
    "sourcing": ["4.1.3"],
    "inventory": ["4.2.1"],
    "warehouse": ["4.2.2"],
    "logistics": ["4.3.1"],
    "distribution": ["4.3.2"],
    "transportation": ["4.3.3"],
    # Customer Service (5.x)
    "customer service": ["5.1", "5.2", "5.4"],
    "call center": ["5.1.1"],
    "support": ["5.1.2", "5.4.1"],
    "complaint": ["5.2.1"],
    "feedback": ["5.2.2", "3.4.2"],
    "satisfaction": ["5.3"],
    "maintenance": ["5.3"],
    "plant": ["5.1.2"],
    # Sales & Marketing (3.x)
    "sales": ["3.1", "3.2"],
    "crm": ["3.1.1"],
    "customer": ["3.1", "3.4"],
    "marketing": ["3.3"],
    "campaign": ["3.3.1"],
    "lead": ["3.1.2"],
    "opportunity": ["3.1.3"],
    "quote": ["3.2.1"],
    "order": ["3.2.2"],
    # Customer Service (5.x continued)
    "contact center": ["5.4.2"],
    "case management": ["5.4.3"],
    # Strategy & Planning (1.x)
    "strategy": ["1.1"],
    "planning": ["1.2"],
    "business intelligence": ["1.3"],
    "analytics": ["1.3.1"],
    "reporting": ["1.3.2"],
    # Product Development (2.x)
    "product": ["2.1", "2.2"],
    "r&d": ["2.1.1"],
    "research": ["2.1.2"],
    "design": ["2.2.1"],
    "engineering": ["2.2.2"],
    "plm": ["2.3"],
    # Governance (9.x)
    "governance": ["9.1"],
    "compliance": ["9.3"],
    "regulatory": ["9.3.1"],
    "policy": ["9.1.1"],
    "ethics": ["9.1.2"],
    # Enterprise Risk Management (10.x)
    "enterprise risk": ["10.1"],
    "risk management": ["10.2"],
    "risk": ["10.2"],
    "risk assessment": ["10.2.1"],
    "risk mitigation": ["10.2.2"],
    "risk analysis": ["10.2.3"],
    "operational risk": ["10.3"],
    "financial risk": ["10.4"],
    "market risk": ["10.4.1"],
    "credit risk": ["10.4.2"],
    "liquidity risk": ["10.4.3"],
    "internal audit": ["10.5"],
    "fraud": ["10.6"],
    "fraud detection": ["10.6.1"],
    "legal": ["10.1"],
    "contract": ["10.2"],
    "contract management": ["10.2.1"],
    "litigation": ["10.1.2"],
    "intellectual property": ["10.1.3"],
    # External Relationships (11.x)
    "external relations": ["11.1"],
    "investor relations": ["11.1.1"],
    "government relations": ["11.1.2"],
    "public relations": ["11.2"],
    "media relations": ["11.2.1"],
    "community relations": ["11.3"],
    "stakeholder": ["11.1"],
    # Knowledge Management (12.x)
    "knowledge": ["12.1", "12.2"],
    "knowledge management": ["12.1"],
    "knowledge base": ["12.1.1"],
    "document management": ["12.2"],
    "documentation": ["12.2.1"],
    "records management": ["12.3"],
    "records retention": ["12.3.1"],
    "content management": ["12.2.2"],
    "collaboration": ["12.4"],
    "team collaboration": ["12.4.1"],
    "lessons learned": ["12.5"],
    "best practices": ["12.5.1"],
    "information management": ["12.1"],
    "wiki": ["12.1.2"],
    "intranet": ["12.4.2"],
    # Emergency Management (13.x)
    "emergency": ["13.1"],
    "emergency management": ["13.1"],
    "crisis": ["13.1.1"],
    "crisis management": ["13.1.1"],
    "disaster": ["13.2"],
    "disaster recovery": ["13.2"],
    "business continuity": ["13.3"],
    "continuity": ["13.3.1"],
    "incident response": ["13.4"],
    "incident management": ["13.4.1"],
    "pandemic": ["13.1.2"],
    "evacuation": ["13.1.3"],
    "backup": ["13.2.1"],
    "recovery": ["13.2.2"],
    # Healthcare (if applicable)
    "clinical": ["5.4.4"],
    "patient": ["5.4.5"],
    "medical": ["5.4.6"],
    "pharmacy": ["5.4.7"],
    "lab": ["5.4.8"],
    "imaging": ["5.4.9"],
    "ehr": ["5.4.4"],
    "emr": ["5.4.4"],
    "health record": ["5.4.4"],
    "telehealth": ["5.4.10"],
    "telemedicine": ["5.4.10"],
    # Real Estate/Facilities
    "facilities": ["7.5"],
    "real estate": ["7.5.1"],
    "lease": ["7.5.2"],
    "space management": ["7.5.3"],
    "building": ["7.5.4"],
    "property": ["7.5.5"],
    "workplace": ["7.5.6"],
    "cafm": ["7.5"],
    "iwms": ["7.5"],
    # Environmental/Sustainability
    "environmental": ["9.4"],
    "sustainability": ["9.4.1"],
    "esg": ["9.4.2"],
    "carbon": ["9.4.3"],
    "emissions": ["9.4.4"],
    "green": ["9.4.1"],
    "renewable": ["9.4.5"],
    # Additional IT/DevOps terms
    "devops": ["7.4.1"],
    "ci/cd": ["7.4.2"],
    "deployment": ["7.4.3"],
    "monitoring": ["7.4.4"],
    "observability": ["7.4.5"],
    "api": ["7.4.6"],
    "integration": ["7.4.7"],
    "database": ["7.2.4"],
    "storage": ["7.2.5"],
    "archival": ["12.3.2"],
    "data lake": ["7.2.6"],
    "etl": ["7.4.8"],
    "data pipeline": ["7.4.8"],
    "container": ["7.2.7"],
    "kubernetes": ["7.2.7"],
    "docker": ["7.2.7"],
    # E-commerce
    "e-commerce": ["3.5"],
    "ecommerce": ["3.5"],
    "checkout": ["3.5.1"],
    "cart": ["3.5.2"],
    "payment": ["3.5.3"],
    "fulfillment": ["4.3.3"],
    "digital commerce": ["3.5"],
    "online store": ["3.5"],
    # Project/Portfolio Management
    "project management": ["1.4"],
    "portfolio management": ["1.4.1"],
    "resource management": ["1.4.2"],
    "capacity planning": ["1.4.3"],
    "time tracking": ["6.4.3"],
    "timesheet": ["6.4.3"],
    # Procurement/Spend
    "spend management": ["4.1.4"],
    "expense": ["8.3.4"],
    "travel": ["6.5.1"],
    "expense report": ["8.3.4"],
    "vendor management": ["4.1.5"],
    "supplier management": ["4.1.5"],
    # Communication/Collaboration
    "messaging": ["12.4.3"],
    "email": ["12.4.4"],
    "video conferencing": ["12.4.5"],
    "virtual meeting": ["12.4.5"],
    "chat": ["12.4.3"],
    "communication": ["12.4"],
    # Customer Experience
    "cx": ["3.4"],
    "customer experience": ["3.4"],
    "nps": ["3.4.1"],
    "survey": ["3.4.2"],
    "voice of customer": ["3.4.3"],
    "voc": ["3.4.3"],
}

# Invalid vendor names to skip (garbage data)
INVALID_VENDOR_NAMES = {
    "stable",
    "n/a",
    "na",
    "none",
    "unknown",
    "tbd",
    "tbc",
    "php",
    "react",
    "angular",
    "vue",
    "node",
    "nodejs",
    "python",
    "java",
    "javascript",
    "typescript",
    "ruby",
    "golang",
    "go",
    "drupal",
    "wordpress",
    "joomla",
    "magento",
    "shopify",
    "externally developed software",
    "custom",
    "in-house",
    "internal",
    "third party",
    "third-party",
    "vendor",
    "supplier",
    "contractor",
    "other",
    "various",
    "multiple",
    "mixed",
    "legacy",
    "deprecated",
}


class ApplicationArchitectureMapperService:
    """
    Comprehensive AI service for mapping applications to capabilities,
    APQC processes, and generating ArchiMate elements.
    """

    @staticmethod
    def extract_vendor_from_application(app) -> Optional[str]:
        """
        Intelligently extract vendor name from application data.

        Uses multiple sources:
        1. Product name patterns in app.name (e.g., "Navision" -> Microsoft)
        2. Product name patterns in app.description
        3. vendor_name field (only if it's a valid vendor, not garbage like "Stable")

        Returns:
            Vendor organization name or None
        """
        # Combine searchable text
        searchable_text = " ".join(
            [
                app.name or "",
                app.description or "",
                (app.vendor_name or ""),
                (app.technology_stack or ""),
            ]
        ).lower()

        # Sort product patterns by length (longer matches first for accuracy)
        sorted_patterns = sorted(PRODUCT_TO_VENDOR_MAP.keys(), key=len, reverse=True)

        # Check for product name patterns using word-boundary matching
        # Prevents false positives like "teams" matching in "development teams"
        for pattern in sorted_patterns:
            # Patterns starting with non-alphanumeric (e.g., ".net") can't use \b
            escaped = re.escape(pattern)
            if pattern[0].isalnum():
                regex = r'\b' + escaped
            else:
                regex = r'(?:^|\s)' + escaped
            if pattern[-1].isalnum():
                regex += r'\b'
            else:
                regex += r'(?:\s|$)'
            if re.search(regex, searchable_text):
                vendor = PRODUCT_TO_VENDOR_MAP[pattern]
                logger.debug(
                    f"Extracted vendor '{vendor}' from pattern '{pattern}' in app '{app.name}'"
                )
                return vendor

        # Fall back to vendor_name field if it's a valid vendor
        vendor_name = app.vendor_name
        if vendor_name:
            normalized = vendor_name.strip().lower()
            # Skip if it's known garbage data
            if normalized not in INVALID_VENDOR_NAMES:
                # Check if it matches a known vendor organization name
                from app.models.vendor.vendor_organization import VendorOrganization

                vendor_org = VendorOrganization.query.filter(
                    func.lower(VendorOrganization.name).contains(normalized)
                ).first()
                if vendor_org:
                    logger.debug(f"Using vendor_name field '{vendor_name}' for app '{app.name}'")
                    return vendor_org.name

        return None

    @staticmethod
    def extract_apqc_processes_from_application(app) -> List[str]:
        """
        Intelligently extract APQC process codes from application data.

        Analyzes:
        1. imported_capabilities - functionality text from import
        2. description - application description
        3. application_services - ArchiMate services from import
        4. business_domain - business context
        5. application_functions_text - function descriptions

        Returns:
            List of APQC process codes (e.g., ['8.1', '8.2.1', '6.4.1'])
        """
        matched_codes = set()

        # Build comprehensive text from all relevant fields
        text_sources = [
            app.name or "",
            app.description or "",
            (app.imported_capabilities or ""),
            (app.business_domain or ""),
            (app.business_purpose or ""),
            (app.business_functions or ""),
            (app.application_functions_text or ""),
        ]

        # Handle JSON fields
        app_services = app.application_services
        if app_services:
            try:
                if isinstance(app_services, str):
                    services = json.loads(app_services)
                else:
                    services = app_services
                if isinstance(services, list):
                    for svc in services:
                        if isinstance(svc, dict):
                            text_sources.append(svc.get("name", ""))
                            text_sources.append(svc.get("description", ""))
            except Exception:
                text_sources.append(str(app_services))

        # Combine and normalize text
        combined_text = " ".join(text_sources).lower()

        # Sort keywords by length (longer matches first for accuracy)
        sorted_keywords = sorted(APQC_KEYWORD_MAP.keys(), key=len, reverse=True)

        # Match keywords to APQC codes using word-boundary matching
        # This prevents "application" matching every app or "order" matching "in order to"
        for keyword in sorted_keywords:
            # Use word boundaries (\b) to avoid substring false positives
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, combined_text):
                codes = APQC_KEYWORD_MAP[keyword]
                matched_codes.update(codes)
                logger.debug(
                    f"Matched APQC keyword '{keyword}' -> codes {codes} for app '{app.name}'"
                )

        return list(matched_codes)

    @staticmethod
    def map_apqc_from_analysis(application_id: int, created_by: Optional[str] = None) -> List:
        """
        Map APQC processes to application by analyzing its imported data.

        Uses intelligent extraction from:
        - Functionality (imported_capabilities)
        - Application Capability (application_services)
        - Description

        Args:
            application_id: Application to map
            created_by: User creating the mappings

        Returns:
            List of created ProcessApplicationMapping objects
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return []

        # Extract APQC codes from application data
        apqc_codes = ApplicationArchitectureMapperService.extract_apqc_processes_from_application(
            app
        )

        if not apqc_codes:
            logger.debug(f"No APQC codes extracted for app {app.name}")
            return []

        created_mappings = []

        # Batch prefetch all APQC processes to avoid N+1 queries in loop
        all_apqc_processes = APQCProcess.query.all() if apqc_codes else []
        # Build lookup: exact code -> process, and prefix matches
        apqc_exact_map = {p.process_code: p for p in all_apqc_processes}

        # Batch prefetch existing mappings for this application
        existing_apqc_mappings = ProcessApplicationMapping.query.filter_by(
            application_id=application_id
        ).all() if apqc_codes else []
        existing_apqc_process_ids = {m.apqc_process_id for m in existing_apqc_mappings}

        for code in apqc_codes:
            # Find matching APQC process (try exact, then prefix)
            process = apqc_exact_map.get(code)
            if not process:
                # Try prefix match from prefetched data
                for p in all_apqc_processes:
                    if p.process_code and p.process_code.startswith(code):
                        process = p
                        break

            if process:
                # Check if mapping already exists (using prefetched set)
                if process.id not in existing_apqc_process_ids:
                    mapping = ProcessApplicationMapping(
                        application_id=application_id,
                        apqc_process_id=process.id,
                        support_level="partial",
                        process_coverage=80,
                        application_role="supporting",
                    )
                    db.session.add(mapping)
                    created_mappings.append(mapping)
                    existing_apqc_process_ids.add(process.id)
                    logger.info(
                        f"Created APQC mapping: App {app.name} -> {process.process_code} ({process.process_name})"
                    )

        if created_mappings:
            db.session.commit()
            logger.info(f"Created {len(created_mappings)} APQC mappings for app {app.name}")

        return created_mappings

    @staticmethod
    def analyze_application_comprehensive(
        application_id: int,
        map_capabilities: bool = True,
        map_processes: bool = True,
        generate_archimate: bool = True,
    ) -> Dict:
        """
        Perform comprehensive AI analysis of an application.

        Returns suggestions for:
        - Business capability mappings
        - APQC process mappings
        - ArchiMate elements to generate
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            raise ValueError(f"Application {application_id} not found")

        # Build comprehensive application context
        app_context = ApplicationArchitectureMapperService._build_app_context(app)

        results = {
            "application_id": application_id,
            "application_name": app.name,
            "capability_suggestions": [],
            "process_suggestions": [],
            "archimate_suggestions": [],
        }

        # Get all capabilities and APQC processes for context
        capabilities = UnifiedCapability.query.filter_by(status="defined").limit(100).all()
        apqc_processes = APQCProcess.query.limit(200).all()

        # Build context strings
        capability_context = "\n".join(
            [
                f"- CAP-{cap.id}: {cap.name} | {cap.description[:80] if cap.description else 'No desc'}..."
                for cap in capabilities[:50]
            ]
        )

        process_context = "\n".join(
            [
                f"- {proc.process_code}: {proc.process_name} (Level {proc.level})"
                for proc in apqc_processes[:100]
            ]
        )

        # Build comprehensive prompt
        prompt = f"""Analyze this application and suggest mappings:

APPLICATION DETAILS:
{json.dumps(app_context, indent=2)}

TASK 1 - CAPABILITY MAPPING:
From these business capabilities, identify which ones this application supports:
{capability_context}

TASK 2 - APQC PROCESS MAPPING:
From these APQC PCF processes, identify which business processes this application supports:
{process_context}

TASK 3 - ARCHIMATE ELEMENTS:
Suggest ArchiMate 3.2 Application Layer elements to create:
- ApplicationComponent (the app itself)
- ApplicationService (services it provides)
- ApplicationFunction (functions it performs)
- ApplicationInterface (interfaces it exposes)
- DataObject (data it manages)

For each suggestion provide:
1. confidence_score (0.0 - 1.0)
2. reasoning

RESPOND WITH VALID JSON:
{{
    "capability_mappings": [
        {{"capability_id": 123, "capability_name": "...", "confidence_score": 0.9, "support_level": "primary", "reasoning": "..."}}
    ],
    "process_mappings": [
        {{"process_code": "4.1.1", "process_name": "...", "confidence_score": 0.85, "support_type": "primary", "reasoning": "..."}}
    ],
    "archimate_elements": [
        {{"type": "ApplicationService", "name": "...", "description": "...", "reasoning": "..."}}
    ]
}}"""

        try:
            # Get best available provider (respects user preference + intelligent selection)
            provider, model = LLMService._get_configured_provider()
            # Comprehensive analysis returns a large JSON object (capability +
            # process + archimate suggestions); without a generous max_tokens the
            # response truncated mid-string ("Unterminated string" at ~20k chars)
            # and the whole analysis silently failed (route still returned 200).
            max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=8192)
            response, _interaction = LLMService._call_llm_with_failover(
                prompt=prompt,
                model=model,
                provider=provider,
                max_tokens=max_tokens,
            )

            # Parse response
            cleaned = response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]

            parsed = json.loads(cleaned)

            results["capability_suggestions"] = parsed.get("capability_mappings", [])
            results["process_suggestions"] = parsed.get("process_mappings", [])
            results["archimate_suggestions"] = parsed.get("archimate_elements", [])

        except Exception as e:
            logger.error(f"Error in comprehensive analysis: {e}", exc_info=True)
            results["error"] = str(e)

        return results

    @staticmethod
    def _build_app_context(app: ApplicationComponent) -> Dict:
        """Build comprehensive context dictionary from application."""
        return {
            "name": app.name,
            "description": app.description or "No description",
            "component_type": app.component_type,
            "application_category": app.application_category,
            "business_domain": app.business_domain,
            "business_purpose": app.business_purpose,
            "business_functions": app.business_functions,
            "business_criticality": app.business_criticality,
            "vendor_name": app.vendor_name,
            "vendor_type": app.vendor_type,
            "technology_stack": app.technology_stack,
            "programming_languages": app.programming_languages,
            "integration_methods": app.integration_methods,
            "lifecycle_status": app.lifecycle_status,
            # Critical imported fields
            "imported_capabilities": app.imported_capabilities,
            "imported_processes": None,  # model-safety-ok: optional field (not on ApplicationComponent schema)
            "imported_apqc_codes": app.imported_apqc_codes,
            "application_services": app.application_services,
            "application_functions_text": app.application_functions_text,
            "notes": app.notes,
        }

    @staticmethod
    def map_apqc_from_codes(
        application_id: int, apqc_codes: str, created_by: Optional[str] = None
    ) -> List[ProcessApplicationMapping]:
        """
        Create APQC process mappings from imported process codes.

        Args:
            application_id: Application to map
            apqc_codes: Comma-separated APQC codes like "4.1.1, 4.2.3, 5.1"
            created_by: User creating the mappings

        Returns:
            List of created ProcessApplicationMapping objects
        """
        if not apqc_codes:
            return []

        # Parse codes
        codes = [c.strip() for c in apqc_codes.split(",") if c.strip()]
        created_mappings = []

        # Batch prefetch all APQC processes to avoid N+1 queries in loop
        all_apqc_processes = APQCProcess.query.all() if codes else []
        apqc_exact_map = {p.process_code: p for p in all_apqc_processes}

        # Batch prefetch existing mappings for this application
        existing_import_mappings = ProcessApplicationMapping.query.filter_by(
            application_id=application_id
        ).all() if codes else []
        existing_import_process_ids = {m.apqc_process_id for m in existing_import_mappings}

        for code in codes:
            # Find matching APQC process (exact match first)
            process = apqc_exact_map.get(code)

            if not process:
                # Try partial match (e.g., "4.1" matches "4.1.1") from prefetched data
                for p in all_apqc_processes:
                    if p.process_code and p.process_code.startswith(code):
                        process = p
                        break

            if process:
                # Check if mapping already exists (using prefetched set)
                if process.id not in existing_import_process_ids:
                    mapping = ProcessApplicationMapping(
                        application_id=application_id,
                        apqc_process_id=process.id,
                        support_level="full",
                        process_coverage=100,
                        application_role="primary",
                    )
                    db.session.add(mapping)
                    created_mappings.append(mapping)
                    existing_import_process_ids.add(process.id)
                    logger.info(f"Created APQC mapping: App {application_id} → {code}")
            else:
                logger.warning(f"APQC process not found for code: {code}")

        if created_mappings:
            db.session.commit()

        return created_mappings

    @staticmethod
    def generate_archimate_for_application(
        application_id: int, architecture_id: Optional[int] = None, created_by: Optional[str] = None
    ) -> Dict:
        """
        Generate ArchiMate 3.2 Application Layer elements for an application.

        Creates:
        - ApplicationComponent element
        - ApplicationService elements (from services)
        - ApplicationFunction elements (from functions)
        - Relationships between them
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            raise ValueError(f"Application {application_id} not found")

        # Get or create architecture model (idempotent)
        if architecture_id:
            arch_model = db.session.get(ArchitectureModel, architecture_id)
        else:
            arch_model = ArchitectureModel.query.filter_by(
                name=f"{app.name} Application Architecture"
            ).first()
            if not arch_model:
                arch_model = ArchitectureModel(
                    name=f"{app.name} Application Architecture",
                    model_data=json.dumps(
                        {
                            "generated_for": app.name,
                            "generated_at": datetime.utcnow().isoformat(),
                            "description": f"Auto-generated architecture for {app.name}",
                            "source": "ApplicationArchitectureMapperService",
                        }
                    ),
                )
                db.session.add(arch_model)
                db.session.flush()

        created_elements = []
        created_relationships = []

        def _get_or_create_element(name, element_type, layer, description, properties_dict):
            """Idempotent element creation — skip if name+type+arch already exists."""
            existing = ArchiMateElement.query.filter_by(
                name=name, type=element_type, architecture_id=arch_model.id
            ).first()
            if existing:
                return existing, False
            element = ArchiMateElement(
                name=name,
                type=element_type,
                layer=layer,
                description=description,
                architecture_id=arch_model.id,
                properties=json.dumps(properties_dict),
            )
            db.session.add(element)
            return element, True

        # Create ApplicationComponent element
        app_element, was_created = _get_or_create_element(
            name=app.name,
            element_type="ApplicationComponent",
            layer="application",
            description=f"Application Component: {app.name} - {app.description or 'No description'}",
            properties_dict={
                "source_application_id": application_id,
                "component_type": app.component_type,
                "deployment_status": app.deployment_status,
                "business_criticality": app.business_criticality,
                "source": "application_import",
            },
        )
        if was_created:
            created_elements.append(app_element)

        # Create ApplicationService elements from imported services (idempotent)
        if app.application_services:
            try:
                services = (
                    json.loads(app.application_services)
                    if isinstance(app.application_services, str)
                    else app.application_services
                )
                if isinstance(services, list):
                    for service in services:
                        svc_name = service.get("name", f"Service: {service.get('description', '')}")
                        svc_el, was_new = _get_or_create_element(
                            name=svc_name,
                            element_type="ApplicationService",
                            layer="application",
                            description=f"Application Service: {service.get('description', '')}",
                            properties_dict={
                                "source_application_id": application_id,
                                "service_type": service.get("type", "business"),
                                "service_description": service.get("description", ""),
                                "source": "application_import",
                            },
                        )
                        if was_new:
                            created_elements.append(svc_el)
            except Exception as e:
                logger.warning(f"Error parsing application services: {e}")

        # Create ApplicationFunction elements from imported functions (idempotent)
        if app.application_functions_text:
            functions = [
                f.strip() for f in app.application_functions_text.split(",") if f.strip()
            ]
            for func_name in functions:
                func_el, was_new = _get_or_create_element(
                    name=func_name,
                    element_type="ApplicationFunction",
                    layer="application",
                    description=f"Application Function: {func_name}",
                    properties_dict={
                        "source_application_id": application_id,
                        "function_name": func_name,
                        "source": "application_import",
                    },
                )
                if was_new:
                    created_elements.append(func_el)

        # Create Capability elements from imported capabilities (idempotent)
        # ArchiMate 3.2: Capability is a Strategy Layer element, NOT BusinessProcess
        if app.imported_capabilities:
            try:
                capabilities = (
                    json.loads(app.imported_capabilities)
                    if isinstance(app.imported_capabilities, str)
                    else app.imported_capabilities
                )
                if isinstance(capabilities, list):
                    for capability in capabilities:
                        cap_name = capability.get(
                            "name", f"Capability: {capability.get('description', '')}"
                        )
                        cap_el, was_new = _get_or_create_element(
                            name=cap_name,
                            element_type="Capability",
                            layer="strategy",
                            description=f"Business Capability: {capability.get('description', '')}",
                            properties_dict={
                                "source_application_id": application_id,
                                "capability_name": capability.get("name", ""),
                                "capability_description": capability.get("description", ""),
                                "capability_level": capability.get("level", ""),
                                "source": "capability_import",
                            },
                        )
                        if was_new:
                            created_elements.append(cap_el)
            except Exception as e:
                logger.warning(f"Error parsing imported capabilities: {e}")

        # P1-5: Keyword-derived BusinessProcess generation from descriptions REMOVED.
        # Matching keywords like "manage", "process", "support" in descriptions produced
        # meaningless elements like "SAP - Manage". Removed per adversarial review.

        # Create BusinessProcess elements from mapped APQC PCF processes (idempotent)

        from app.utils.tenant_sql import org_scope
        _org_clause, _org_params = org_scope(prefix="pam.")
        process_mappings = db.session.execute(
            text(
                """
            SELECT ap.id, ap.process_name, ap.process_code, ap.category_level_1, ap.category_level_2
            FROM apqc_process ap
            JOIN process_application_mapping pam ON ap.id = pam.apqc_process_id
            WHERE pam.application_id = :app_id
        """
                + _org_clause
                + """
            ORDER BY ap.process_code
        """
            ),
            {"app_id": application_id, **_org_params},
        ).fetchall()

        logger.info(
            f"Found {len(process_mappings)} PCF process mappings for application {application_id}"
        )
        for process in process_mappings:
            # Create BusinessProcess ArchiMate element for each mapped PCF process (idempotent)
            proc_el, was_new = _get_or_create_element(
                name=process.process_name,
                element_type="BusinessProcess",
                layer="business",
                description=f"PCF Process {process.process_code}: {process.process_name} - {process.category_level_1} > {process.category_level_2}",
                properties_dict={
                    "source_application_id": application_id,
                    "pcf_process_code": process.process_code,
                    "pcf_level_1": process.category_level_1,
                    "pcf_level_2": process.category_level_2,
                    "source": "apqc_pcf_mapping",
                },
            )
            if was_new:
                created_elements.append(proc_el)
                logger.info(f"Created BusinessProcess element: {proc_el.name}")

        # Note: Relationship creation disabled temporarily due to database schema mismatch
        # The archimate_relationships table has source_element_type column not in ORM model
        # Blocked: ArchiMateRelationship ORM model missing source_element_type, target_element_type columns
        if process_mappings:
            logger.debug(f"Skipping {len(process_mappings)} relationships - schema mismatch")

        db.session.commit()

        return {
            "success": True,
            "architecture_id": arch_model.id,
            "architecture_name": arch_model.name,
            "elements_created": len(created_elements),
            "relationships_created": len(created_relationships),
            "elements": [{"id": e.id, "name": e.name, "type": e.type} for e in created_elements],
            "relationships": [
                {"source": r.source_id, "target": r.target_id, "type": r.type}
                for r in created_relationships
            ],
        }

    @staticmethod
    def bulk_auto_map(
        max_applications: int = 50,
        map_capabilities: bool = True,
        map_processes: bool = True,
        generate_archimate: bool = False,
        clone_vendor_archimate: bool = False,
        confidence_threshold: float = 0.7,
        auto_create: bool = False,
        created_by: Optional[str] = None,
    ) -> Dict:
        """
        Bulk auto-map applications to capabilities, processes, and ArchiMate elements.

        Args:
            max_applications: Maximum number of applications to process
            map_capabilities: Map to business capabilities
            map_processes: Map to APQC processes from imported codes
            generate_archimate: Generate ArchiMate elements from imported data
            clone_vendor_archimate: Clone ArchiMate elements from vendor templates
            confidence_threshold: Minimum confidence for AI suggestions
            auto_create: Automatically create mappings (vs. just suggestions)
            created_by: User performing the action
        """
        # Find applications needing mapping
        apps_query = ApplicationComponent.query

        # Prioritize apps with imported capability/process data
        apps = (
            apps_query.order_by(
                ApplicationComponent.imported_capabilities.isnot(None).desc(),
                ApplicationComponent.imported_apqc_codes.isnot(None).desc(),
            )
            .limit(max_applications)
            .all()
        )

        results = {
            "total_analyzed": 0,
            "capability_mappings_suggested": 0,
            "capability_mappings_created": 0,
            "process_mappings_suggested": 0,
            "process_mappings_created": 0,
            "archimate_elements_created": 0,
            "vendor_archimate_cloned": 0,
            "vendor_matches_found": 0,
            "applications": [],
        }

        for app in apps:
            try:
                app_result = {
                    "application_id": app.id,
                    "application_name": app.name,
                    "capabilities": [],
                    "processes": [],
                    "archimate": [],
                }

                # Auto-map to capabilities using AI
                if map_capabilities:
                    try:
                        from app.services.application_capability_mapper import (
                            ApplicationCapabilityMapperService,
                        )

                        # Get AI suggestions for this application
                        suggestions = (
                            ApplicationCapabilityMapperService.suggest_capabilities_for_application(
                                application_id=app.id, top_n=3
                            )
                        )

                        # Filter by confidence threshold
                        high_confidence = [
                            s
                            for s in suggestions
                            if s.get("confidence_score", 0) >= confidence_threshold
                        ]

                        if high_confidence:
                            results["capability_mappings_suggested"] += len(high_confidence)
                            app_result["capabilities"] = high_confidence

                            # Auto-create mappings if enabled
                            if auto_create:
                                for suggestion in high_confidence:
                                    try:
                                        ApplicationCapabilityMapperService.create_mapping_from_suggestion(
                                            application_id=app.id,
                                            suggestion=suggestion,
                                            created_by=created_by,
                                        )
                                        results["capability_mappings_created"] += 1
                                    except Exception as cap_err:
                                        logger.error(
                                            f"Failed to create capability mapping: {cap_err}"
                                        )
                    except Exception as cap_error:
                        logger.error(f"Capability mapping error for app {app.id}: {cap_error}")

                # Auto-map APQC processes using INTELLIGENT ANALYSIS
                # Uses: imported_capabilities, description, application_services, business_domain
                if map_processes:
                    # First try imported APQC codes if available (most accurate)
                    if app.imported_apqc_codes:
                        mappings = ApplicationArchitectureMapperService.map_apqc_from_codes(
                            application_id=app.id,
                            apqc_codes=app.imported_apqc_codes,
                            created_by=created_by,
                        )
                        results["process_mappings_created"] += len(mappings)
                        app_result["processes"] = [
                            {
                                "code": m.apqc_process.process_code,
                                "name": m.apqc_process.process_name,
                            }
                            for m in mappings
                        ]

                    # Always also run intelligent analysis on app data (Functionality, Description, etc.)
                    analysis_mappings = ApplicationArchitectureMapperService.map_apqc_from_analysis(
                        application_id=app.id, created_by=created_by
                    )
                    if analysis_mappings:
                        results["process_mappings_created"] += len(analysis_mappings)
                        for m in analysis_mappings:
                            app_result["processes"].append(
                                {
                                    "code": m.apqc_process.process_code,
                                    "name": m.apqc_process.process_name,
                                    "source": "intelligent_analysis",
                                }
                            )

                # Generate ArchiMate elements from imported data and APQC mappings
                # More permissive: generate if app has any content
                has_content = bool(
                    app.application_services
                    or app.application_functions_text
                    or app.description
                    or app.imported_capabilities
                )
                has_mapped_processes = bool(app_result.get("processes"))

                if generate_archimate and (has_content or has_mapped_processes):
                    arch_result = (
                        ApplicationArchitectureMapperService.generate_archimate_for_application(
                            application_id=app.id, created_by=created_by
                        )
                    )
                    results["archimate_elements_created"] += arch_result.get("elements_created", 0)
                    app_result["archimate"] = arch_result.get("elements", [])

                # Clone vendor ArchiMate elements using INTELLIGENT VENDOR EXTRACTION
                # Extracts vendor from: app name, description, technology_stack (not just vendor_name field)
                if clone_vendor_archimate:
                    # Use intelligent vendor extraction (handles garbage vendor_name values)
                    extracted_vendor = (
                        ApplicationArchitectureMapperService.extract_vendor_from_application(app)
                    )

                    if extracted_vendor:
                        logger.info(f"Extracted vendor '{extracted_vendor}' for app '{app.name}'")
                        vendor_clone_result = ApplicationArchitectureMapperService.clone_vendor_archimate_to_application(
                            application_id=app.id, created_by=created_by
                        )
                        app_result["vendor_clone"] = {
                            "success": vendor_clone_result.get("success", False),
                            "elements_cloned": vendor_clone_result.get("elements_cloned", 0),
                            "extracted_vendor": extracted_vendor,
                            "message": vendor_clone_result.get("message", ""),
                        }
                        if vendor_clone_result.get("vendor_matched"):
                            results["vendor_matches_found"] += 1
                        if vendor_clone_result.get("success"):
                            results["vendor_archimate_cloned"] += vendor_clone_result.get(
                                "elements_cloned", 0
                            )
                    else:
                        app_result["vendor_clone"] = {
                            "success": False,
                            "message": "No vendor pattern found in application name/description",
                        }

                # Generate APQC-VENDOR ENRICHED ArchiMate elements (NEW)
                # Combines APQC process mappings with vendor capabilities for intelligent generation
                if app_result.get("processes"):  # Only if APQC processes were mapped
                    try:
                        from app.services.apqc_vendor_archimate_service import (
                            APQCVendorArchiMateService,
                        )

                        enriched_result = (
                            APQCVendorArchiMateService.generate_apqc_vendor_enriched_archimate(
                                application_id=app.id, created_by=created_by
                            )
                        )

                        if enriched_result.get("success"):
                            results["archimate_elements_created"] += enriched_result.get(
                                "elements_created", 0
                            )
                            app_result["apqc_vendor_enriched"] = {
                                "success": True,
                                "elements_created": enriched_result.get("elements_created", 0),
                                "vendor_elements_created": enriched_result.get(
                                    "vendor_elements_created", 0
                                ),
                                "vendor_products_found": enriched_result.get(
                                    "vendor_products_found", 0
                                ),
                                "gap_analysis": enriched_result.get("gap_analysis", {}),
                                "message": f"Generated {enriched_result.get('elements_created', 0)} enriched elements",
                            }
                            logger.info(
                                f"Generated {enriched_result.get('elements_created', 0)} APQC-vendor enriched elements for {app.name}"
                            )
                        else:
                            app_result["apqc_vendor_enriched"] = {
                                "success": False,
                                "message": enriched_result.get("message", "No APQC mappings found"),
                            }
                    except Exception as enriched_error:
                        logger.error(
                            f"APQC-vendor enrichment error for app {app.id}: {enriched_error}"
                        )
                        app_result["apqc_vendor_enriched"] = {
                            "success": False,
                            "error": str(enriched_error),
                        }

                results["total_analyzed"] += 1
                results["applications"].append(app_result)

                if results["total_analyzed"] % 10 == 0:
                    logger.info(f"Progress: {results['total_analyzed']}/{len(apps)}")

            except Exception as e:
                logger.error(f"Error processing app {app.id}: {e}")
                continue

        return results

    @staticmethod
    def find_matching_vendor_product(vendor_name: str) -> Optional[Dict]:
        """
        Find a VendorProduct or VendorStackTemplate matching the application's vendor name.

        Checks in order:
        1. VendorOrganization -> VendorProduct WITH ArchiMate elements
        2. VendorStackTemplate (pre-configured technology templates with JSON data)
        3. VendorOrganization -> VendorProduct WITHOUT ArchiMate elements (fallback)

        Args:
            vendor_name: The vendor name from the application

        Returns:
            Dict with vendor info or None if not found.
            Includes 'source_type': 'vendor_product' or 'vendor_template'
        """
        if not vendor_name:
            return None

        try:
            from sqlalchemy import text

            from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
            from app.models.vendor_stack_template import VendorStackTemplate

            # Normalize vendor name for matching
            normalized_name = vendor_name.strip().lower()

            # Helper to check if a VendorProduct has ArchiMate elements.
            # Deliberately unguarded: a query failure must not be reported as a
            # count of 0, which would demote a fully mapped product to the
            # "no ArchiMate elements" fallback and publish
            # archimate_element_count: 0 as a measured figure. The caller's
            # handler below logs the failure and returns "no match".
            def get_product_element_count(product_id: int) -> int:
                result = db.session.execute(  # tenant-filtered: scoped via parent FK (vendor_product_id)
                    text(
                        """
                    SELECT COUNT(*) FROM application_vendor_products
                    WHERE vendor_product_id = :product_id
                """
                    ),
                    {"product_id": product_id},
                )
                row = result.fetchone()
                return row[0] if row else 0

            # ========== FIRST: Try VendorOrganization -> VendorProduct WITH elements ==========
            vendor_org = VendorOrganization.query.filter(
                func.lower(VendorOrganization.name) == normalized_name
            ).first()

            if not vendor_org:
                vendor_org = VendorOrganization.query.filter(
                    func.lower(VendorOrganization.name).contains(normalized_name)
                ).first()

            if not vendor_org:
                vendor_org = VendorOrganization.query.filter(
                    func.lower(VendorOrganization.display_name).contains(normalized_name)
                ).first()

            vendor_product_fallback = None  # Store product match for fallback

            if vendor_org:
                # Get products and check for ArchiMate elements
                products = VendorProduct.query.filter_by(
                    vendor_organization_id=vendor_org.id, status="active"
                ).all()

                for product in products:
                    element_count = get_product_element_count(product.id)
                    if element_count > 0:
                        return {
                            "source_type": "vendor_product",
                            "vendor_organization_id": vendor_org.id,
                            "vendor_organization_name": vendor_org.name,
                            "vendor_product_id": product.id,
                            "vendor_product_name": product.name,
                            "product_family": product.product_family.family_name
                            if product.product_family
                            else None,
                            "archimate_element_count": element_count,
                        }
                    elif not vendor_product_fallback and products:
                        # Store first product as fallback
                        vendor_product_fallback = {
                            "source_type": "vendor_product",
                            "vendor_organization_id": vendor_org.id,
                            "vendor_organization_name": vendor_org.name,
                            "vendor_product_id": products[0].id,
                            "vendor_product_name": products[0].name,
                            "product_family": products[0].product_family,
                            "archimate_element_count": 0,
                        }

            # ========== SECOND: Try VendorStackTemplate ==========
            template = VendorStackTemplate.query.filter(
                func.lower(VendorStackTemplate.vendor_name) == normalized_name
            ).first()

            if not template:
                template = VendorStackTemplate.query.filter(
                    func.lower(VendorStackTemplate.vendor_name).contains(normalized_name)
                ).first()

            if not template:
                template = VendorStackTemplate.query.filter(
                    func.lower(VendorStackTemplate.name).contains(normalized_name)
                ).first()

            if not template:
                template = VendorStackTemplate.query.filter(
                    func.lower(VendorStackTemplate.vendor_company_name).contains(normalized_name)
                ).first()

            if template:
                # Check if template has meaningful data
                has_data = any(
                    [
                        template.capabilities_enabled,
                        template.business_services,
                        template.business_processes,
                        template.application_components,
                        template.application_services,
                    ]
                )
                if has_data:
                    return {
                        "source_type": "vendor_template",
                        "vendor_template_id": template.id,
                        "vendor_name": template.vendor_name,
                        "template_name": template.name,
                        "vendor_company_name": template.vendor_company_name,
                    }

            # ========== THIRD: Return VendorProduct fallback (no elements) ==========
            if vendor_product_fallback:
                return vendor_product_fallback

            return None

        except Exception as e:
            logger.error(f"Error finding vendor product for '{vendor_name}': {e}")
            return None

    @staticmethod
    def clone_vendor_archimate_to_application(
        application_id: int,
        vendor_product_id: Optional[int] = None,
        vendor_template_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> Dict:
        """
        Clone ArchiMate elements from vendor product or template to application.

        Supports two sources:
        1. VendorProduct - clones existing ArchiMate elements via ArchiMateElementCloner
        2. VendorStackTemplate - creates new ArchiMate elements from JSON data

        Args:
            application_id: Application to clone elements to
            vendor_product_id: Optional specific vendor product ID
            vendor_template_id: Optional specific vendor template ID
            created_by: User performing the action

        Returns:
            Dict with cloning results
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            raise ValueError(f"Application {application_id} not found")

        result = {
            "success": False,
            "application_id": application_id,
            "application_name": app.name,
            "vendor_matched": False,
            "source_type": None,
            "elements_cloned": 0,
            "relationships_cloned": 0,
            "message": "",
        }

        # Find vendor source if not specified
        if not vendor_product_id and not vendor_template_id:
            # Use INTELLIGENT vendor extraction (from app name, description, not just vendor_name field)
            vendor_name = ApplicationArchitectureMapperService.extract_vendor_from_application(app)

            if not vendor_name:
                # Fall back to raw vendor_name field if intelligent extraction fails
                vendor_name = app.vendor_name
                if not vendor_name or vendor_name.strip().lower() in INVALID_VENDOR_NAMES:
                    result[
                        "message"
                    ] = f'No vendor pattern found in application name/description for "{app.name}"'
                    return result

            logger.info(f"Using vendor '{vendor_name}' for application '{app.name}'")
            match = ApplicationArchitectureMapperService.find_matching_vendor_product(vendor_name)
            if not match:
                result[
                    "message"
                ] = f'No matching vendor product or template found for "{vendor_name}"'
                return result

            result["vendor_matched"] = True
            result["vendor_match"] = match
            result["source_type"] = match.get("source_type")

            if match.get("source_type") == "vendor_product":
                vendor_product_id = match["vendor_product_id"]
            elif match.get("source_type") == "vendor_template":
                vendor_template_id = match["vendor_template_id"]

        try:
            if vendor_product_id:
                # Use the ArchiMate Element Cloner service for VendorProduct
                from app.services.archimate_element_cloner import (
                    clone_vendor_archimate_to_application as clone_from_product,
                )

                clone_result = clone_from_product(
                    vendor_product_id=vendor_product_id, application_component_id=application_id
                )

                cloned_count = clone_result.get("elements_cloned", 0)

                if cloned_count > 0:
                    # Successfully cloned existing elements
                    result["success"] = clone_result.get("success", False)
                    result["elements_cloned"] = cloned_count
                    result["relationships_cloned"] = clone_result.get("relationships_cloned", 0)
                    result["element_ids"] = clone_result.get("element_ids", [])
                    result["source_type"] = "vendor_product"
                    result[
                        "message"
                    ] = f"Cloned {cloned_count} ArchiMate elements from VendorProduct"
                else:
                    # No existing elements - auto-generate from VendorProduct metadata
                    logger.info(
                        f"No existing elements for VendorProduct {vendor_product_id}, auto-generating..."
                    )
                    auto_result = ApplicationArchitectureMapperService._auto_generate_archimate_from_vendor_product(
                        application_id=application_id,
                        vendor_product_id=vendor_product_id,
                        created_by=created_by,
                    )

                    result["success"] = auto_result.get("success", False)
                    result["elements_cloned"] = auto_result.get("elements_created", 0)
                    result["element_ids"] = auto_result.get("element_ids", [])
                    result["source_type"] = "vendor_product_auto_generated"
                    result["vendor_name"] = auto_result.get("vendor_name")
                    result["product_name"] = auto_result.get("product_name")
                    result[
                        "message"
                    ] = f"Auto-generated {result['elements_cloned']} ArchiMate elements from VendorProduct metadata"

            elif vendor_template_id:
                # Create ArchiMate elements from VendorStackTemplate JSON data
                template_result = (
                    ApplicationArchitectureMapperService._create_archimate_from_template(
                        application_id=application_id,
                        vendor_template_id=vendor_template_id,
                        created_by=created_by,
                    )
                )

                result["success"] = template_result.get("success", False)
                result["elements_cloned"] = template_result.get("elements_created", 0)
                result["element_ids"] = template_result.get("element_ids", [])
                result["source_type"] = "vendor_template"
                result[
                    "message"
                ] = f"Created {result['elements_cloned']} ArchiMate elements from VendorStackTemplate"

            logger.info(
                f"Cloned {result['elements_cloned']} vendor ArchiMate elements to app {application_id}"
            )

        except Exception as e:
            logger.error(f"Error cloning vendor ArchiMate elements: {e}", exc_info=True)
            result["message"] = f"Error during cloning: {str(e)}"

        return result

    @staticmethod
    def _create_archimate_from_template(
        application_id: int, vendor_template_id: int, created_by: Optional[str] = None
    ) -> Dict:
        """
        Create ArchiMate elements from VendorStackTemplate JSON data.

        Creates elements from:
        - capabilities_enabled -> BusinessCapability type or Capability archimate
        - business_services -> ApplicationService type
        - business_processes -> BusinessProcess type
        - application_components -> ApplicationComponent type
        - application_services -> ApplicationService type
        - application_functions -> ApplicationFunction type
        - application_interfaces -> ApplicationInterface type
        - data_objects -> DataObject type

        Args:
            application_id: Application to create elements for
            vendor_template_id: VendorStackTemplate ID
            created_by: User creating the elements

        Returns:
            Dict with creation results
        """
        from app.models.vendor_stack_template import VendorStackTemplate

        template = db.session.get(VendorStackTemplate, vendor_template_id)
        if not template:
            return {"success": False, "error": f"Template {vendor_template_id} not found"}

        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return {"success": False, "error": f"Application {application_id} not found"}

        # Get or create architecture model for this application
        arch_model = ArchitectureModel.query.filter_by(
            name=f"{app.name} Vendor Architecture"
        ).first()

        if not arch_model:
            arch_model = ArchitectureModel(
                name=f"{app.name} Vendor Architecture",
                model_data=json.dumps(
                    {
                        "source": "VendorStackTemplate",
                        "vendor_template_id": vendor_template_id,
                        "vendor_name": template.vendor_name,
                        "generated_at": datetime.utcnow().isoformat(),
                    }
                ),
            )
            db.session.add(arch_model)
            db.session.flush()

        created_elements = []

        def create_element(element_type: str, layer: str, name: str, description: str = None):
            """Helper to create an ArchiMate element."""
            element = ArchiMateElement(
                name=f"{template.vendor_name}: {name}"
                if not name.startswith(template.vendor_name)
                else name,
                type=element_type,
                layer=layer,
                description=description or f"From {template.vendor_name} template",
                architecture_id=arch_model.id,
                properties=json.dumps(
                    {
                        "source": "vendor_stack_template",
                        "vendor_template_id": vendor_template_id,
                        "vendor_name": template.vendor_name,
                        "source_application_id": application_id,
                    }
                ),
            )
            db.session.add(element)
            created_elements.append(element)
            return element

        # Parse and create elements from each layer

        # 1. Strategy Layer - Capabilities
        if template.capabilities_enabled:
            try:
                capabilities = (
                    json.loads(template.capabilities_enabled)
                    if isinstance(template.capabilities_enabled, str)
                    else template.capabilities_enabled
                )
                for cap in capabilities[:20]:  # Limit to 20
                    if isinstance(cap, dict):
                        create_element(
                            "Capability",
                            "strategy",
                            cap.get("name", "Unknown"),
                            cap.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing capabilities: {e}")

        # 2. Business Layer - Services
        if template.business_services:
            try:
                services = (
                    json.loads(template.business_services)
                    if isinstance(template.business_services, str)
                    else template.business_services
                )
                for svc in services[:15]:
                    if isinstance(svc, dict):
                        create_element(
                            "BusinessService",
                            "business",
                            svc.get("name", "Unknown"),
                            svc.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing business_services: {e}")

        # 3. Business Layer - Processes
        if template.business_processes:
            try:
                processes = (
                    json.loads(template.business_processes)
                    if isinstance(template.business_processes, str)
                    else template.business_processes
                )
                for proc in processes[:15]:
                    if isinstance(proc, dict):
                        create_element(
                            "BusinessProcess",
                            "business",
                            proc.get("name", "Unknown"),
                            proc.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing business_processes: {e}")

        # 4. Application Layer - Components
        if template.application_components:
            try:
                components = (
                    json.loads(template.application_components)
                    if isinstance(template.application_components, str)
                    else template.application_components
                )
                for comp in components[:20]:
                    if isinstance(comp, dict):
                        create_element(
                            "ApplicationComponent",
                            "application",
                            comp.get("name", "Unknown"),
                            comp.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing application_components: {e}")

        # 5. Application Layer - Services
        if template.application_services:
            try:
                app_services = (
                    json.loads(template.application_services)
                    if isinstance(template.application_services, str)
                    else template.application_services
                )
                for svc in app_services[:15]:
                    if isinstance(svc, dict):
                        create_element(
                            "ApplicationService",
                            "application",
                            svc.get("name", "Unknown"),
                            svc.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing application_services: {e}")

        # 6. Application Layer - Functions
        if template.application_functions:
            try:
                functions = (
                    json.loads(template.application_functions)
                    if isinstance(template.application_functions, str)
                    else template.application_functions
                )
                for func in functions[:15]:
                    if isinstance(func, dict):
                        create_element(
                            "ApplicationFunction",
                            "application",
                            func.get("name", "Unknown"),
                            func.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing application_functions: {e}")

        # 7. Application Layer - Interfaces
        if template.application_interfaces:
            try:
                interfaces = (
                    json.loads(template.application_interfaces)
                    if isinstance(template.application_interfaces, str)
                    else template.application_interfaces
                )
                for iface in interfaces[:10]:
                    if isinstance(iface, dict):
                        create_element(
                            "ApplicationInterface",
                            "application",
                            iface.get("name", "Unknown"),
                            iface.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing application_interfaces: {e}")

        # 8. Application Layer - Data Objects
        if template.data_objects:
            try:
                data_objs = (
                    json.loads(template.data_objects)
                    if isinstance(template.data_objects, str)
                    else template.data_objects
                )
                for obj in data_objs[:15]:
                    if isinstance(obj, dict):
                        create_element(
                            "DataObject",
                            "application",
                            obj.get("name", "Unknown"),
                            obj.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing data_objects: {e}")

        # 9. Technology Layer - Nodes
        if template.nodes:
            try:
                nodes = (
                    json.loads(template.nodes)
                    if isinstance(template.nodes, str)
                    else template.nodes
                )
                for node in nodes[:10]:
                    if isinstance(node, dict):
                        create_element(
                            "Node",
                            "technology",
                            node.get("name", "Unknown"),
                            node.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing nodes: {e}")

        # 10. Technology Layer - System Software
        if template.system_software:
            try:
                software = (
                    json.loads(template.system_software)
                    if isinstance(template.system_software, str)
                    else template.system_software
                )
                for sw in software[:10]:
                    if isinstance(sw, dict):
                        create_element(
                            "SystemSoftware",
                            "technology",
                            sw.get("name", "Unknown"),
                            sw.get("description"),
                        )
            except Exception as e:
                logger.warning(f"Error parsing system_software: {e}")

        # Commit all elements
        db.session.commit()

        logger.info(
            f"Created {len(created_elements)} ArchiMate elements from template {template.vendor_name}"
        )

        return {
            "success": True,
            "elements_created": len(created_elements),
            "element_ids": [e.id for e in created_elements],
            "architecture_id": arch_model.id,
            "vendor_template_id": vendor_template_id,
            "vendor_name": template.vendor_name,
        }

    @staticmethod
    def _auto_generate_archimate_from_vendor_product(
        application_id: int, vendor_product_id: int, created_by: Optional[str] = None
    ) -> Dict:
        """
        Auto-generate ArchiMate elements from VendorProduct data when no existing elements exist.

        This provides a fallback for all 200+ vendors that don't have manually seeded elements.
        Creates elements based on VendorProduct fields:
        - name -> ApplicationComponent
        - product_family -> ApplicationService (based on family type)
        - key_features -> ApplicationFunction (if available)
        - api_availability -> ApplicationInterface (REST API)
        - primary_technology -> TechnologyService

        Args:
            application_id: Application to create elements for
            vendor_product_id: VendorProduct ID to generate from
            created_by: User creating the elements

        Returns:
            Dict with generation results
        """
        from sqlalchemy import text

        from app.models.vendor.vendor_organization import VendorProduct

        product = db.session.get(VendorProduct, vendor_product_id)
        if not product:
            return {"success": False, "error": f"VendorProduct {vendor_product_id} not found"}

        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return {"success": False, "error": f"Application {application_id} not found"}

        vendor_org = product.vendor_organization
        vendor_name = vendor_org.name if vendor_org else "Unknown Vendor"

        # Get or create architecture model for this application
        arch_model = ArchitectureModel.query.filter_by(
            name=f"{app.name} Vendor Architecture"
        ).first()

        if not arch_model:
            arch_model = ArchitectureModel(
                name=f"{app.name} Vendor Architecture",
                model_data=json.dumps(
                    {
                        "source": "VendorProduct",
                        "vendor_product_id": vendor_product_id,
                        "vendor_name": vendor_name,
                        "product_name": product.name,
                        "generated_at": datetime.utcnow().isoformat(),
                    }
                ),
            )
            db.session.add(arch_model)
            db.session.flush()

        created_elements = []

        def create_element(element_type: str, layer: str, name: str, description: str = None):
            """Helper to create an ArchiMate element and link it to VendorProduct."""
            element = ArchiMateElement(
                name=name,
                type=element_type,
                layer=layer,
                description=description or f"Auto-generated from {vendor_name} {product.name}",
                architecture_id=arch_model.id,
                properties=json.dumps(
                    {
                        "source": "vendor_product_auto_generated",
                        "vendor_product_id": vendor_product_id,
                        "vendor_name": vendor_name,
                        "product_name": product.name,
                        "source_application_id": application_id,
                    }
                ),
            )
            db.session.add(element)
            db.session.flush()
            created_elements.append(element)

            # Link element to VendorProduct via junction table
            try:
                db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
                    text(
                        """
                    INSERT INTO application_vendor_products
                    (archimate_element_id, vendor_product_id, deployment_type, criticality, hosting_model, created_at)
                    VALUES (:element_id, :vendor_product_id, 'primary_system', 'business_critical', :hosting, :created_at)
                """
                    ),
                    {
                        "element_id": element.id,
                        "vendor_product_id": vendor_product_id,
                        "hosting": product.deployment_model or "cloud",
                        "created_at": datetime.utcnow(),
                    },
                )
            except Exception as e:
                logger.warning(f"Could not create junction record: {e}")

            return element

        # 1. Create main ApplicationComponent for the product
        create_element(
            "ApplicationComponent",
            "application",
            f"{vendor_name} - {product.name}",
            product.functional_scope or f"{product.name} from {vendor_name}",
        )

        # 2. Create ApplicationService based on product_family
        family_services = {
            "ERP": [
                "Enterprise Resource Planning Service",
                "Financial Management Service",
                "Supply Chain Service",
            ],
            "CRM": [
                "Customer Relationship Service",
                "Sales Management Service",
                "Marketing Automation Service",
            ],
            "HCM": [
                "Human Capital Management Service",
                "Payroll Service",
                "Talent Management Service",
            ],
            "Analytics": [
                "Business Intelligence Service",
                "Data Analytics Service",
                "Reporting Service",
            ],
            "SCM": ["Supply Chain Management Service", "Inventory Service", "Logistics Service"],
            "PLM": ["Product Lifecycle Service", "Engineering Service", "Product Data Service"],
            "ITSM": [
                "IT Service Management Service",
                "Incident Management Service",
                "Change Management Service",
            ],
            "BPM": ["Business Process Service", "Workflow Automation Service"],
            "Integration": [
                "Integration Service",
                "API Gateway Service",
                "Data Integration Service",
            ],
            "Security": [
                "Security Service",
                "Identity Management Service",
                "Access Control Service",
            ],
            "Collaboration": [
                "Collaboration Service",
                "Communication Service",
                "Document Management Service",
            ],
        }

        product_family = product.product_family.family_name if product.product_family else "General"
        services = family_services.get(product_family, [f"{product_family} Service"])

        for service_name in services[:3]:  # Limit to 3 services per family
            create_element(
                "ApplicationService",
                "application",
                f"{vendor_name}: {service_name}",
                f"{service_name} provided by {product.name}",
            )

        # 3. Create ApplicationFunction based on key_features (if available)
        if product.key_features:
            try:
                features = (
                    json.loads(product.key_features)
                    if isinstance(product.key_features, str)
                    else product.key_features
                )
                if isinstance(features, list):
                    for feature in features[:5]:  # Limit to 5 functions
                        feature_name = (
                            feature
                            if isinstance(feature, str)
                            else feature.get("name", str(feature))
                        )
                        create_element(
                            "ApplicationFunction",
                            "application",
                            f"{vendor_name}: {feature_name}",
                            f"Function: {feature_name}",
                        )
            except Exception as e:
                logger.warning(f"Error parsing key_features: {e}")

        # 4. Create ApplicationInterface if API is available
        if product.api_availability:
            create_element(
                "ApplicationInterface",
                "application",
                f"{vendor_name} {product.name} REST API",
                f"RESTful API for {product.name}",
            )

            # Check for specific integration methods
            if product.integration_methods:
                try:
                    methods = (
                        json.loads(product.integration_methods)
                        if isinstance(product.integration_methods, str)
                        else product.integration_methods
                    )
                    if isinstance(methods, list):
                        for method in methods[:3]:
                            if method.upper() not in ["REST", "REST API"]:
                                create_element(
                                    "ApplicationInterface",
                                    "application",
                                    f"{vendor_name} {product.name} {method}",
                                    f"{method} interface for {product.name}",
                                )
                except Exception:
                    logger.debug("Failed to parse integration methods for product %s", product.name, exc_info=True)

        # 5. Create TechnologyService based on primary_technology
        if product.primary_technology:
            create_element(
                "TechnologyService",
                "technology",
                f"{vendor_name} {product.primary_technology} Stack",
                f"Technology stack: {product.primary_technology}",
            )

        # 6. Create Node for deployment model
        if product.deployment_model:
            deployment_names = {
                "cloud": "Cloud Infrastructure",
                "on_premise": "On-Premise Infrastructure",
                "hybrid": "Hybrid Infrastructure",
                "saas": "SaaS Platform",
            }
            node_name = deployment_names.get(
                product.deployment_model, f"{product.deployment_model} Infrastructure"
            )
            create_element(
                "Node",
                "technology",
                f"{vendor_name} {node_name}",
                f"Deployment: {product.deployment_model} for {product.name}",
            )

        db.session.commit()

        logger.info(
            f"Auto-generated {len(created_elements)} ArchiMate elements from VendorProduct {product.name}"
        )

        return {
            "success": True,
            "elements_created": len(created_elements),
            "element_ids": [e.id for e in created_elements],
            "architecture_id": arch_model.id,
            "vendor_product_id": vendor_product_id,
            "vendor_name": vendor_name,
            "product_name": product.name,
        }

    @staticmethod
    def bulk_clone_vendor_archimate(
        max_applications: int = 50, only_with_vendor: bool = True, created_by: Optional[str] = None
    ) -> Dict:
        """
        Bulk clone vendor ArchiMate elements to applications.

        Args:
            max_applications: Maximum applications to process
            only_with_vendor: Only process apps that have a vendor_name set
            created_by: User performing the action

        Returns:
            Dict with bulk cloning results
        """
        results = {
            "total_processed": 0,
            "successful_clones": 0,
            "vendor_matches_found": 0,
            "elements_cloned": 0,
            "applications": [],
        }

        # Build query for applications
        apps_query = ApplicationComponent.query

        if only_with_vendor:
            apps_query = apps_query.filter(
                ApplicationComponent.vendor_name.isnot(None), ApplicationComponent.vendor_name != ""
            )

        apps = apps_query.limit(max_applications).all()

        for app in apps:
            app_result = {
                "application_id": app.id,
                "application_name": app.name,
                "vendor_name": app.vendor_name,
                "success": False,
                "message": "",
            }

            try:
                clone_result = (
                    ApplicationArchitectureMapperService.clone_vendor_archimate_to_application(
                        application_id=app.id, created_by=created_by
                    )
                )

                app_result["success"] = clone_result.get("success", False)
                app_result["message"] = clone_result.get("message", "")
                app_result["elements_cloned"] = clone_result.get("elements_cloned", 0)

                if clone_result.get("vendor_matched"):
                    results["vendor_matches_found"] += 1

                if clone_result.get("success"):
                    results["successful_clones"] += 1
                    results["elements_cloned"] += clone_result.get("elements_cloned", 0)

            except Exception as e:
                app_result["message"] = str(e)
                logger.error(f"Error cloning for app {app.id}: {e}")

            results["total_processed"] += 1
            results["applications"].append(app_result)

            if results["total_processed"] % 10 == 0:
                logger.info(f"Vendor clone progress: {results['total_processed']}/{len(apps)}")

        return results

    # ========================================================================
    # GAP MEASUREMENT & LLM FALLBACK METHODS
    # ========================================================================

    @staticmethod
    def llm_extract_mappings(app) -> Dict:
        """
        Use LLM to extract vendor and APQC processes when heuristics fail.

        This is the fallback when PRODUCT_TO_VENDOR_MAP and APQC_KEYWORD_MAP
        don't find matches.

        Returns:
            {
                'vendor': str or None,
                'apqc_codes': List[str],
                'confidence': float,
                'reasoning': str
            }
        """
        # Build context from app data
        context = f"""
Application Name: {app.name}
Description: {app.description or 'None'}
Vendor (imported): {app.vendor_name or 'None'}
Technology Stack: {app.technology_stack or 'None'}
Business Domain: {app.business_domain or 'None'}
Business Functions: {app.business_functions or 'None'}
Imported Capabilities: {(app.imported_capabilities or '')[:500]}
"""

        prompt = f"""Analyze this enterprise application and extract:
1. The VENDOR organization (company that makes/sells this software)
2. APQC PCF process codes this application supports (use format like 8.1, 6.4.1, etc.)

{context}

Common vendors include: Microsoft, SAP, Oracle, Salesforce, ServiceNow, Workday, etc.

APQC Process Framework categories:
- 1.x: Strategy & Planning
- 2.x: Product Development
- 3.x: Sales & Marketing
- 4.x: Supply Chain
- 5.x: Manufacturing/Customer Service
- 6.x: Human Resources
- 7.x: Information Technology
- 8.x: Finance
- 9.x: Governance
- 10.x: Risk Management
- 11.x: External Relations
- 12.x: Knowledge Management
- 13.x: Emergency Management

Respond ONLY with valid JSON:
{{
    "vendor": "Company Name or null if unknown",
    "apqc_codes": ["8.1", "8.2.1"],
    "confidence": 0.85,
    "reasoning": "Brief explanation"
}}
"""

        try:
            llm_service = LLMService()
            response = llm_service.query(prompt, max_tokens=300)

            # Parse JSON response
            import re

            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                logger.info(
                    f"LLM extracted for {app.name}: vendor={result.get('vendor')}, "
                    f"apqc={result.get('apqc_codes')}, confidence={result.get('confidence')}"
                )
                return result
        except Exception as e:
            logger.warning(f"LLM extraction failed for {app.name}: {e}")

        return {
            "vendor": None,
            "apqc_codes": [],
            "confidence": 0,
            "reasoning": "LLM extraction failed",
        }

    @staticmethod
    def extract_vendor_with_metrics(
        app, use_llm_fallback: bool = False, log_metrics: bool = True
    ) -> Tuple[Optional[str], str]:
        """
        Extract vendor from application with gap metrics logging.

        Args:
            app: ApplicationComponent instance
            use_llm_fallback: Whether to use LLM when heuristics fail
            log_metrics: Whether to log metrics to MappingMetric table

        Returns:
            Tuple of (vendor_name, source) where source is 'heuristic', 'llm', or 'none'
        """
        from app.models.mapping_metrics import MappingMetric

        # First try heuristic extraction
        vendor = ApplicationArchitectureMapperService.extract_vendor_from_application(app)

        if vendor:
            # Success with heuristics
            if log_metrics:
                MappingMetric.log_result(
                    application_id=app.id,
                    mapping_type="vendor",
                    source="heuristic",
                    success=True,
                    matched_value=vendor,
                    confidence=1.0,
                    app_name=app.name,
                    app_description=app.description,
                )
            return vendor, "heuristic"

        # Try LLM fallback if enabled
        if use_llm_fallback:
            llm_result = ApplicationArchitectureMapperService.llm_extract_mappings(app)
            if llm_result.get("vendor") and llm_result.get("confidence", 0) >= 0.7:
                vendor = llm_result["vendor"]
                if log_metrics:
                    MappingMetric.log_result(
                        application_id=app.id,
                        mapping_type="vendor",
                        source="llm",
                        success=True,
                        matched_value=vendor,
                        confidence=llm_result.get("confidence"),
                        app_name=app.name,
                        app_description=app.description,
                    )
                return vendor, "llm"

        # Failed to extract vendor
        if log_metrics:
            MappingMetric.log_result(
                application_id=app.id,
                mapping_type="vendor",
                source="heuristic" if not use_llm_fallback else "llm",
                success=False,
                failure_reason="No vendor pattern matched in name/description",
                app_name=app.name,
                app_description=app.description,
            )
        return None, "none"

    @staticmethod
    def extract_apqc_with_metrics(
        app, use_llm_fallback: bool = False, log_metrics: bool = True
    ) -> Tuple[List[str], str]:
        """
        Extract APQC codes from application with gap metrics logging.

        Args:
            app: ApplicationComponent instance
            use_llm_fallback: Whether to use LLM when heuristics fail
            log_metrics: Whether to log metrics to MappingMetric table

        Returns:
            Tuple of (apqc_codes, source) where source is 'heuristic', 'llm', or 'none'
        """
        from app.models.mapping_metrics import MappingMetric

        # First try heuristic extraction
        codes = ApplicationArchitectureMapperService.extract_apqc_processes_from_application(app)

        if codes:
            # Success with heuristics
            if log_metrics:
                MappingMetric.log_result(
                    application_id=app.id,
                    mapping_type="apqc",
                    source="heuristic",
                    success=True,
                    matched_value=",".join(codes),
                    confidence=1.0,
                    app_name=app.name,
                    app_description=app.description,
                )
            return codes, "heuristic"

        # Try LLM fallback if enabled
        if use_llm_fallback:
            llm_result = ApplicationArchitectureMapperService.llm_extract_mappings(app)
            if llm_result.get("apqc_codes") and llm_result.get("confidence", 0) >= 0.6:
                codes = llm_result["apqc_codes"]
                if log_metrics:
                    MappingMetric.log_result(
                        application_id=app.id,
                        mapping_type="apqc",
                        source="llm",
                        success=True,
                        matched_value=",".join(codes),
                        confidence=llm_result.get("confidence"),
                        app_name=app.name,
                        app_description=app.description,
                    )
                return codes, "llm"

        # Failed to extract APQC codes
        if log_metrics:
            MappingMetric.log_result(
                application_id=app.id,
                mapping_type="apqc",
                source="heuristic" if not use_llm_fallback else "llm",
                success=False,
                failure_reason="No APQC keywords matched in description/capabilities",
                app_name=app.name,
                app_description=app.description,
            )
        return [], "none"

    @staticmethod
    def get_mapping_gap_summary() -> Dict:
        """
        Get summary of mapping gaps for analysis.

        Returns statistics on vendor and APQC mapping success rates.
        """
        from app.models.mapping_metrics import MappingMetric

        return {
            "success_rates": MappingMetric.get_success_rates(),
            "common_failures": MappingMetric.get_common_failures(20),
            "unmatched_vendor_apps": [
                {
                    "app_name": r.app_name,
                    "description": r.app_description[:100] if r.app_description else None,
                }
                for r in MappingMetric.get_unmatched_apps("vendor", 30)
            ],
            "unmatched_apqc_apps": [
                {
                    "app_name": r.app_name,
                    "description": r.app_description[:100] if r.app_description else None,
                }
                for r in MappingMetric.get_unmatched_apps("apqc", 30)
            ],
        }


# Backwards-compatible alias: some tests expect `ApplicationArchitectureMapper`
# Export the historical name so fixtures/imports keep working.
try:
    ApplicationArchitectureMapper = globals().get("ApplicationArchitectureMapperService")
except Exception:
    ApplicationArchitectureMapper = None
