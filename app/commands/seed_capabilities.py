"""
CLI Commands for Seeding Capabilities Data

Provides comprehensive capability seed data covering:
- Business Capabilities (L0 - L3 hierarchy)
- Technical Capabilities (ACM domains)
- Unified Capabilities (combined view)

Usage:
    flask seed-capabilities all
    flask seed-capabilities business
    flask seed-capabilities technical
    flask seed-capabilities unified
"""

from datetime import datetime

import click
from flask.cli import with_appcontext

from app import db

# =============================================================================
# BUSINESS CAPABILITIES SEED DATA
# Based on common enterprise capability frameworks
# =============================================================================
BUSINESS_CAPABILITIES = [
    # =========================================================================
    # Level 0: Enterprise Capabilities (8 domains)
    # Level 1: Business Capabilities (~33 items)
    # Level 2: Sub-Capabilities (~132 items)
    # =========================================================================
    {
        "name": "Enterprise Management",
        "level": 0,
        "description": "Strategic management of the enterprise",
        "children": [
            {
                "name": "Strategic Planning",
                "level": 1,
                "description": "Define and execute corporate strategy",
                "children": [
                    {"name": "Corporate Strategy", "level": 2, "description": "Define long-term enterprise direction and priorities"},
                    {"name": "Strategic Goal Setting", "level": 2, "description": "Establish measurable strategic objectives and OKRs"},
                    {"name": "Portfolio Strategy", "level": 2, "description": "Manage strategic investment portfolio across business units"},
                    {"name": "Competitive Analysis", "level": 2, "description": "Analyze competitive landscape and market positioning"},
                ],
            },
            {
                "name": "Performance Management",
                "level": 1,
                "description": "Monitor and improve organizational performance",
                "children": [
                    {"name": "KPI Management", "level": 2, "description": "Define, track, and report key performance indicators"},
                    {"name": "Balanced Scorecard", "level": 2, "description": "Manage multi-perspective performance framework"},
                    {"name": "Benchmarking", "level": 2, "description": "Compare performance against industry standards and peers"},
                    {"name": "Performance Reporting", "level": 2, "description": "Generate and distribute performance reports to stakeholders"},
                ],
            },
            {
                "name": "Risk Management",
                "level": 1,
                "description": "Identify, assess, and mitigate business risks",
                "children": [
                    {"name": "Enterprise Risk Assessment", "level": 2, "description": "Identify and quantify enterprise-wide risk exposure"},
                    {"name": "Operational Risk", "level": 2, "description": "Manage risks arising from business operations and processes"},
                    {"name": "Financial Risk", "level": 2, "description": "Manage credit, market, and liquidity risk exposure"},
                    {"name": "Third-Party Risk", "level": 2, "description": "Assess and monitor risks from vendors and partners"},
                ],
            },
            {
                "name": "Compliance Management",
                "level": 1,
                "description": "Ensure regulatory and policy compliance",
                "children": [
                    {"name": "Regulatory Compliance", "level": 2, "description": "Monitor and adhere to industry regulations and laws"},
                    {"name": "Policy Management", "level": 2, "description": "Create, distribute, and enforce organizational policies"},
                    {"name": "Audit Management", "level": 2, "description": "Plan, execute, and track internal and external audits"},
                    {"name": "Ethics & Conduct", "level": 2, "description": "Manage code of conduct and ethical standards"},
                ],
            },
        ],
    },
    {
        "name": "Customer Management",
        "level": 0,
        "description": "Manage customer relationships and experiences",
        "children": [
            {
                "name": "Customer Acquisition",
                "level": 1,
                "description": "Attract and onboard new customers",
                "children": [
                    {"name": "Lead Management", "level": 2, "description": "Capture, qualify, and nurture sales leads"},
                    {"name": "Campaign Management", "level": 2, "description": "Plan, execute, and measure marketing campaigns"},
                    {"name": "Channel Management", "level": 2, "description": "Manage customer acquisition across sales channels"},
                    {"name": "Prospect Qualification", "level": 2, "description": "Evaluate and score prospect readiness to buy"},
                ],
            },
            {
                "name": "Customer Service",
                "level": 1,
                "description": "Provide support and assistance to customers",
                "children": [
                    {"name": "Service Request Management", "level": 2, "description": "Handle customer service requests and tickets"},
                    {"name": "Complaint Management", "level": 2, "description": "Receive, investigate, and resolve customer complaints"},
                    {"name": "Knowledge Management", "level": 2, "description": "Maintain self-service knowledge base and FAQs"},
                    {"name": "SLA Management", "level": 2, "description": "Define and monitor service level agreements"},
                ],
            },
            {
                "name": "Customer Retention",
                "level": 1,
                "description": "Maintain and grow customer relationships",
                "children": [
                    {"name": "Loyalty Management", "level": 2, "description": "Design and operate customer loyalty programs"},
                    {"name": "Churn Prevention", "level": 2, "description": "Identify at-risk customers and execute retention actions"},
                    {"name": "Account Management", "level": 2, "description": "Manage ongoing customer account relationships"},
                    {"name": "Customer Feedback", "level": 2, "description": "Collect, analyze, and act on customer feedback"},
                ],
            },
            {
                "name": "Customer Analytics",
                "level": 1,
                "description": "Analyze customer behavior and preferences",
                "children": [
                    {"name": "Customer Segmentation", "level": 2, "description": "Classify customers into behavioral and demographic segments"},
                    {"name": "Lifetime Value Analysis", "level": 2, "description": "Calculate and forecast customer lifetime value"},
                    {"name": "Behavioral Analytics", "level": 2, "description": "Analyze customer interaction patterns and journeys"},
                    {"name": "Satisfaction Measurement", "level": 2, "description": "Measure customer satisfaction via NPS, CSAT, and CES"},
                ],
            },
        ],
    },
    {
        "name": "Product Management",
        "level": 0,
        "description": "Manage product lifecycle and development",
        "children": [
            {
                "name": "Product Development",
                "level": 1,
                "description": "Design and develop new products",
                "children": [
                    {"name": "Requirements Management", "level": 2, "description": "Capture, prioritize, and trace product requirements"},
                    {"name": "Design Management", "level": 2, "description": "Manage product design from concept through specification"},
                    {"name": "Prototype Management", "level": 2, "description": "Build and validate product prototypes"},
                    {"name": "Testing & Validation", "level": 2, "description": "Verify product meets requirements and quality standards"},
                ],
            },
            {
                "name": "Product Lifecycle",
                "level": 1,
                "description": "Manage products through their lifecycle",
                "children": [
                    {"name": "Launch Management", "level": 2, "description": "Plan and execute product market launch"},
                    {"name": "Growth Management", "level": 2, "description": "Drive product adoption and market expansion"},
                    {"name": "Maturity Management", "level": 2, "description": "Optimize established products for sustained value"},
                    {"name": "End-of-Life Management", "level": 2, "description": "Plan and execute product sunset and migration"},
                ],
            },
            {
                "name": "Product Portfolio",
                "level": 1,
                "description": "Manage the product portfolio",
                "children": [
                    {"name": "Portfolio Analysis", "level": 2, "description": "Evaluate product portfolio health and balance"},
                    {"name": "Product Prioritization", "level": 2, "description": "Rank products by strategic value and investment return"},
                    {"name": "Product Roadmapping", "level": 2, "description": "Plan and communicate product evolution timelines"},
                    {"name": "Resource Allocation", "level": 2, "description": "Allocate development resources across product portfolio"},
                ],
            },
            {
                "name": "Product Innovation",
                "level": 1,
                "description": "Drive product innovation initiatives",
                "children": [
                    {"name": "Innovation Pipeline", "level": 2, "description": "Manage funnel of innovation ideas from ideation to launch"},
                    {"name": "Market Research", "level": 2, "description": "Research market needs, trends, and opportunities"},
                    {"name": "Concept Development", "level": 2, "description": "Develop and evaluate new product concepts"},
                    {"name": "Technology Scouting", "level": 2, "description": "Identify emerging technologies for product advantage"},
                ],
            },
        ],
    },
    {
        "name": "Operations Management",
        "level": 0,
        "description": "Manage day-to-day business operations",
        "children": [
            {
                "name": "Process Management",
                "level": 1,
                "description": "Design and optimize business processes",
                "children": [
                    {"name": "Process Design", "level": 2, "description": "Model and design end-to-end business processes"},
                    {"name": "Process Monitoring", "level": 2, "description": "Track process execution and identify bottlenecks"},
                    {"name": "Process Optimization", "level": 2, "description": "Analyze and improve process efficiency and effectiveness"},
                    {"name": "Process Automation", "level": 2, "description": "Automate repetitive manual process steps"},
                ],
            },
            {
                "name": "Quality Management",
                "level": 1,
                "description": "Ensure quality standards are met",
                "children": [
                    {"name": "Quality Planning", "level": 2, "description": "Define quality objectives, standards, and control plans"},
                    {"name": "Quality Control", "level": 2, "description": "Inspect and test outputs against quality standards"},
                    {"name": "Quality Assurance", "level": 2, "description": "Establish processes to prevent quality defects"},
                    {"name": "Continuous Improvement", "level": 2, "description": "Drive ongoing improvement through Lean and Six Sigma"},
                ],
            },
            {
                "name": "Supply Chain Management",
                "level": 1,
                "description": "Manage supply chain operations",
                "children": [
                    {"name": "Demand Planning", "level": 2, "description": "Forecast demand and align supply chain capacity"},
                    {"name": "Procurement Management", "level": 2, "description": "Source, evaluate, and purchase goods and services"},
                    {"name": "Logistics Management", "level": 2, "description": "Manage transportation, warehousing, and distribution"},
                    {"name": "Supplier Management", "level": 2, "description": "Evaluate, onboard, and manage supplier relationships"},
                ],
            },
            {
                "name": "Inventory Management",
                "level": 1,
                "description": "Manage inventory levels and logistics",
                "children": [
                    {"name": "Stock Control", "level": 2, "description": "Monitor and maintain optimal inventory levels"},
                    {"name": "Warehouse Management", "level": 2, "description": "Manage warehouse operations, storage, and picking"},
                    {"name": "Demand Forecasting", "level": 2, "description": "Predict future inventory demand from historical data"},
                    {"name": "Replenishment Planning", "level": 2, "description": "Plan and trigger inventory replenishment orders"},
                ],
            },
        ],
    },
    {
        "name": "Financial Management",
        "level": 0,
        "description": "Manage financial resources and reporting",
        "children": [
            {
                "name": "Financial Planning",
                "level": 1,
                "description": "Plan and forecast financial performance",
                "children": [
                    {"name": "Budgeting", "level": 2, "description": "Create and manage organizational budgets"},
                    {"name": "Financial Forecasting", "level": 2, "description": "Project future financial outcomes and scenarios"},
                    {"name": "Cost Management", "level": 2, "description": "Track, allocate, and optimize organizational costs"},
                    {"name": "Capital Planning", "level": 2, "description": "Plan and prioritize capital investments"},
                ],
            },
            {
                "name": "Accounting",
                "level": 1,
                "description": "Record and report financial transactions",
                "children": [
                    {"name": "General Ledger", "level": 2, "description": "Maintain chart of accounts and journal entries"},
                    {"name": "Accounts Payable", "level": 2, "description": "Process vendor invoices and manage payments"},
                    {"name": "Accounts Receivable", "level": 2, "description": "Manage customer invoicing and collections"},
                    {"name": "Fixed Asset Management", "level": 2, "description": "Track and depreciate organizational fixed assets"},
                ],
            },
            {
                "name": "Treasury Management",
                "level": 1,
                "description": "Manage cash and investments",
                "children": [
                    {"name": "Cash Management", "level": 2, "description": "Manage cash positions, flows, and bank relationships"},
                    {"name": "Investment Management", "level": 2, "description": "Manage investment portfolios and returns"},
                    {"name": "Debt Management", "level": 2, "description": "Manage borrowings, covenants, and debt service"},
                    {"name": "Foreign Exchange", "level": 2, "description": "Manage currency exposures and hedging strategies"},
                ],
            },
            {
                "name": "Tax Management",
                "level": 1,
                "description": "Manage tax obligations and planning",
                "children": [
                    {"name": "Tax Planning", "level": 2, "description": "Develop tax-efficient structures and strategies"},
                    {"name": "Tax Compliance", "level": 2, "description": "Ensure timely and accurate tax filings"},
                    {"name": "Tax Reporting", "level": 2, "description": "Prepare statutory and management tax reports"},
                    {"name": "Transfer Pricing", "level": 2, "description": "Manage intercompany pricing and documentation"},
                ],
            },
        ],
    },
    {
        "name": "Human Capital Management",
        "level": 0,
        "description": "Manage workforce and talent",
        "children": [
            {
                "name": "Talent Acquisition",
                "level": 1,
                "description": "Recruit and hire talent",
                "children": [
                    {"name": "Job Requisition Management", "level": 2, "description": "Create, approve, and track open position requisitions"},
                    {"name": "Candidate Sourcing", "level": 2, "description": "Identify and attract qualified candidates"},
                    {"name": "Selection & Assessment", "level": 2, "description": "Screen, interview, and evaluate candidates"},
                    {"name": "Onboarding", "level": 2, "description": "Integrate new hires into the organization"},
                ],
            },
            {
                "name": "Talent Development",
                "level": 1,
                "description": "Develop employee skills and careers",
                "children": [
                    {"name": "Learning Management", "level": 2, "description": "Deliver and track employee training and certifications"},
                    {"name": "Career Development", "level": 2, "description": "Plan and support employee career progression"},
                    {"name": "Succession Planning", "level": 2, "description": "Identify and develop future leaders for key roles"},
                    {"name": "Mentoring & Coaching", "level": 2, "description": "Facilitate mentoring relationships and coaching programs"},
                ],
            },
            {
                "name": "Workforce Planning",
                "level": 1,
                "description": "Plan workforce capacity and skills",
                "children": [
                    {"name": "Headcount Planning", "level": 2, "description": "Forecast and plan workforce size by function"},
                    {"name": "Skills Gap Analysis", "level": 2, "description": "Identify gaps between current and needed skills"},
                    {"name": "Workforce Analytics", "level": 2, "description": "Analyze workforce trends, turnover, and productivity"},
                    {"name": "Organizational Design", "level": 2, "description": "Design organizational structure and reporting lines"},
                ],
            },
            {
                "name": "Employee Experience",
                "level": 1,
                "description": "Manage employee engagement and satisfaction",
                "children": [
                    {"name": "Engagement Management", "level": 2, "description": "Measure and improve employee engagement levels"},
                    {"name": "Wellness Programs", "level": 2, "description": "Provide health, wellness, and benefits programs"},
                    {"name": "Internal Communications", "level": 2, "description": "Manage employee communications and town halls"},
                    {"name": "Recognition & Rewards", "level": 2, "description": "Operate recognition programs and reward systems"},
                ],
            },
        ],
    },
    {
        "name": "Technology Management",
        "level": 0,
        "description": "Manage IT and technology resources",
        "children": [
            {
                "name": "IT Strategy",
                "level": 1,
                "description": "Define and execute IT strategy",
                "children": [
                    {"name": "Technology Roadmap", "level": 2, "description": "Plan technology evolution and investment timeline"},
                    {"name": "Enterprise Architecture", "level": 2, "description": "Define and govern enterprise architecture standards"},
                    {"name": "IT Innovation", "level": 2, "description": "Evaluate and pilot emerging technologies"},
                    {"name": "IT Governance", "level": 2, "description": "Establish IT decision rights and accountability"},
                ],
            },
            {
                "name": "Application Management",
                "level": 1,
                "description": "Manage application portfolio",
                "children": [
                    {"name": "Application Portfolio", "level": 2, "description": "Inventory and rationalize the application landscape"},
                    {"name": "Application Lifecycle", "level": 2, "description": "Manage applications from deployment through retirement"},
                    {"name": "Application Integration", "level": 2, "description": "Connect applications via APIs and middleware"},
                    {"name": "Application Support", "level": 2, "description": "Provide L2/L3 application support and maintenance"},
                ],
            },
            {
                "name": "Infrastructure Management",
                "level": 1,
                "description": "Manage IT infrastructure",
                "children": [
                    {"name": "Compute Management", "level": 2, "description": "Manage servers, virtual machines, and compute resources"},
                    {"name": "Network Management", "level": 2, "description": "Manage network connectivity, routing, and bandwidth"},
                    {"name": "Storage Management", "level": 2, "description": "Manage data storage systems and capacity"},
                    {"name": "Cloud Management", "level": 2, "description": "Manage cloud services, costs, and governance"},
                ],
            },
            {
                "name": "Data Management",
                "level": 1,
                "description": "Manage enterprise data assets",
                "children": [
                    {"name": "Data Governance", "level": 2, "description": "Establish data ownership, stewardship, and policies"},
                    {"name": "Data Quality Management", "level": 2, "description": "Monitor and improve data accuracy and completeness"},
                    {"name": "Data Architecture", "level": 2, "description": "Design data models, flows, and integration patterns"},
                    {"name": "Data Privacy", "level": 2, "description": "Manage data privacy compliance and consent"},
                ],
            },
            {
                "name": "Security Management",
                "level": 1,
                "description": "Manage information security",
                "children": [
                    {"name": "Threat Management", "level": 2, "description": "Detect, analyze, and respond to security threats"},
                    {"name": "Identity & Access Management", "level": 2, "description": "Manage user identities, roles, and access rights"},
                    {"name": "Security Operations", "level": 2, "description": "Operate security monitoring and incident response"},
                    {"name": "Security Compliance", "level": 2, "description": "Ensure compliance with security standards and regulations"},
                ],
            },
        ],
    },
    {
        "name": "Partner Management",
        "level": 0,
        "description": "Manage external partnerships and vendors",
        "children": [
            {
                "name": "Vendor Management",
                "level": 1,
                "description": "Manage vendor relationships",
                "children": [
                    {"name": "Vendor Selection", "level": 2, "description": "Evaluate and select vendors through RFP/RFI processes"},
                    {"name": "Vendor Performance", "level": 2, "description": "Monitor and score vendor delivery and quality"},
                    {"name": "Vendor Risk Assessment", "level": 2, "description": "Assess financial, operational, and compliance risks of vendors"},
                    {"name": "Vendor Relationship", "level": 2, "description": "Manage strategic vendor relationships and escalations"},
                ],
            },
            {
                "name": "Partner Ecosystem",
                "level": 1,
                "description": "Develop partner ecosystems",
                "children": [
                    {"name": "Alliance Management", "level": 2, "description": "Manage strategic alliance partnerships and joint ventures"},
                    {"name": "Channel Partners", "level": 2, "description": "Manage reseller, distributor, and channel networks"},
                    {"name": "Technology Partners", "level": 2, "description": "Manage technology and integration partnerships"},
                    {"name": "Co-Innovation", "level": 2, "description": "Collaborate with partners on joint innovation initiatives"},
                ],
            },
            {
                "name": "Contract Management",
                "level": 1,
                "description": "Manage contracts and agreements",
                "children": [
                    {"name": "Contract Creation", "level": 2, "description": "Draft, negotiate, and execute contracts"},
                    {"name": "Contract Compliance", "level": 2, "description": "Monitor adherence to contract terms and conditions"},
                    {"name": "Contract Renewal", "level": 2, "description": "Manage contract renewal and renegotiation cycles"},
                    {"name": "Contract Analytics", "level": 2, "description": "Analyze contract portfolio for value and risk"},
                ],
            },
            {
                "name": "Procurement",
                "level": 1,
                "description": "Procure goods and services",
                "children": [
                    {"name": "Strategic Sourcing", "level": 2, "description": "Develop category strategies and negotiate enterprise deals"},
                    {"name": "Purchase Management", "level": 2, "description": "Manage purchase orders and requisition workflows"},
                    {"name": "Supplier Qualification", "level": 2, "description": "Qualify and certify suppliers for approved vendor lists"},
                    {"name": "Spend Analytics", "level": 2, "description": "Analyze spend patterns and identify savings opportunities"},
                ],
            },
        ],
    },
]

# =============================================================================
# TECHNICAL CAPABILITIES SEED DATA
# Based on ACM (Application Capability Model) domains
# =============================================================================
TECHNICAL_CAPABILITIES = [
    # Domain 1: User Experience (UX)
    {
        "domain": "USER-EXPERIENCE",
        "code": "UX",
        "capabilities": [
            {
                "name": "Web User Interface",
                "code": "UX - 01",
                "description": "Browser-based user interfaces",
            },
            {
                "name": "Mobile User Interface",
                "code": "UX - 02",
                "description": "Native and hybrid mobile apps",
            },
            {
                "name": "Desktop User Interface",
                "code": "UX - 03",
                "description": "Desktop application interfaces",
            },
            {
                "name": "Voice User Interface",
                "code": "UX - 04",
                "description": "Voice-activated interactions",
            },
            {
                "name": "Conversational UI",
                "code": "UX - 05",
                "description": "Chatbots and conversational interfaces",
            },
            {
                "name": "Accessibility",
                "code": "UX - 06",
                "description": "Accessible user experiences",
            },
        ],
    },
    # Domain 2: Application Services
    {
        "domain": "APPLICATION-SERVICES",
        "code": "AS",
        "capabilities": [
            {
                "name": "Workflow Automation",
                "code": "AS - 05",
                "description": "Automated workflow orchestration",
            },
            {
                "name": "Business Rules Engine",
                "code": "AS - 06",
                "description": "Rule-based decision automation",
            },
            {
                "name": "Robotic Process Automation",
                "code": "AS - 07",
                "description": "RPA for repetitive tasks",
            },
            {
                "name": "Document Processing",
                "code": "AS - 08",
                "description": "Intelligent document processing",
            },
            {
                "name": "Event Processing",
                "code": "AS - 09",
                "description": "Event-driven process automation",
            },
        ],
    },
    # Domain 3: Communication
    {
        "domain": "COMMUNICATION",
        "code": "CM",
        "capabilities": [
            {
                "name": "API Management",
                "code": "CM - 05",
                "description": "API gateway and lifecycle management",
            },
            {
                "name": "Message Queuing",
                "code": "CM - 06",
                "description": "Asynchronous message processing",
            },
            {
                "name": "Event Streaming",
                "code": "CM - 07",
                "description": "Real-time event streaming",
            },
            {
                "name": "ETL/Data Integration",
                "code": "CM - 08",
                "description": "Data extraction, transformation, loading",
            },
            {
                "name": "B2B Integration",
                "code": "CM - 09",
                "description": "Partner and B2B integrations",
            },
            {
                "name": "Service Orchestration",
                "code": "CM - 10",
                "description": "Service composition and orchestration",
            },
        ],
    },
    # Domain 4: AI & Analytics
    {
        "domain": "AI-ANALYTICS",
        "code": "AA",
        "capabilities": [
            {
                "name": "Data Warehousing",
                "code": "AA - 05",
                "description": "Centralized data storage and management",
            },
            {
                "name": "Business Intelligence",
                "code": "AA - 06",
                "description": "Reporting and dashboards",
            },
            {
                "name": "Advanced Analytics",
                "code": "AA - 07",
                "description": "Predictive and prescriptive analytics",
            },
            {
                "name": "Machine Learning",
                "code": "AA - 08",
                "description": "ML model development and deployment",
            },
            {
                "name": "Data Visualization",
                "code": "AA - 09",
                "description": "Visual data exploration",
            },
            {
                "name": "Master Data Management",
                "code": "AA - 10",
                "description": "Single source of truth for master data",
            },
            {
                "name": "Data Quality",
                "code": "AA - 11",
                "description": "Data quality monitoring and improvement",
            },
        ],
    },
    # Domain 5: Security & Identity
    {
        "domain": "SECURITY-IDENTITY",
        "code": "SI",
        "capabilities": [
            {
                "name": "Identity Management",
                "code": "SI - 05",
                "description": "User identity lifecycle management",
            },
            {
                "name": "Access Management",
                "code": "SI - 06",
                "description": "Authentication and authorization",
            },
            {
                "name": "Data Protection",
                "code": "SI - 07",
                "description": "Encryption and data masking",
            },
            {
                "name": "Threat Detection",
                "code": "SI - 08",
                "description": "Security monitoring and threat detection",
            },
            {
                "name": "Vulnerability Management",
                "code": "SI - 09",
                "description": "Vulnerability assessment and remediation",
            },
            {
                "name": "Security Compliance",
                "code": "SI - 10",
                "description": "Security policy enforcement",
            },
        ],
    },
    # Domain 6: Data & Storage
    {
        "domain": "DATA-STORAGE",
        "code": "DS",
        "capabilities": [
            {"name": "Compute", "code": "DS - 05", "description": "Server and compute resources"},
            {"name": "Storage", "code": "DS - 06", "description": "Data storage systems"},
            {"name": "Networking", "code": "DS - 07", "description": "Network infrastructure"},
            {
                "name": "Containerization",
                "code": "DS - 08",
                "description": "Container orchestration (Kubernetes)",
            },
            {
                "name": "Cloud Services",
                "code": "DS - 09",
                "description": "Public/private cloud infrastructure",
            },
            {
                "name": "Edge Computing",
                "code": "DS - 10",
                "description": "Edge and IoT infrastructure",
            },
        ],
    },
    # Domain 7: DevOps & Platform
    {
        "domain": "DEVOPS-PLATFORM",
        "code": "DP",
        "capabilities": [
            {
                "name": "Source Control",
                "code": "DP - 05",
                "description": "Version control and code management",
            },
            {
                "name": "CI/CD Pipeline",
                "code": "DP - 06",
                "description": "Continuous integration and deployment",
            },
            {
                "name": "Infrastructure as Code",
                "code": "DP - 07",
                "description": "Automated infrastructure provisioning",
            },
            {
                "name": "Monitoring & Observability",
                "code": "DP - 08",
                "description": "Application and infrastructure monitoring",
            },
            {
                "name": "Logging & Tracing",
                "code": "DP - 09",
                "description": "Centralized logging and distributed tracing",
            },
            {
                "name": "Release Management",
                "code": "DP - 10",
                "description": "Release planning and deployment",
            },
        ],
    },
]

# =============================================================================
# UNIFIED CAPABILITIES SEED DATA
# Real domain-aligned capabilities with L1/L2 hierarchy
# =============================================================================
UNIFIED_CAPABILITY_DOMAINS = [
    {"code": "CUST", "name": "Customer Management", "description": "Customer lifecycle, engagement, and experience management", "domain_type": "primary", "strategic_focus": "Customer experience", "domain_owner": "Chief Customer Officer"},
    {"code": "PROD", "name": "Product Management", "description": "Product lifecycle, innovation, and portfolio management", "domain_type": "primary", "strategic_focus": "Product leadership", "domain_owner": "Chief Product Officer"},
    {"code": "OPER", "name": "Operations Management", "description": "Business process execution, quality, and supply chain", "domain_type": "primary", "strategic_focus": "Operational excellence", "domain_owner": "Chief Operating Officer"},
    {"code": "FIN", "name": "Financial Management", "description": "Financial planning, accounting, treasury, and tax", "domain_type": "supporting", "strategic_focus": "Financial stewardship", "domain_owner": "Chief Financial Officer"},
    {"code": "RISK", "name": "Risk & Compliance", "description": "Enterprise risk, regulatory compliance, and audit", "domain_type": "supporting", "strategic_focus": "Risk mitigation", "domain_owner": "Chief Risk Officer"},
    {"code": "DATA", "name": "Data & Analytics", "description": "Data governance, BI, and advanced analytics", "domain_type": "enabling", "strategic_focus": "Data-driven decisions", "domain_owner": "Chief Data Officer"},
    {"code": "PART", "name": "Partner & Supplier Management", "description": "Vendor management, procurement, and ecosystem partnerships", "domain_type": "supporting", "strategic_focus": "Ecosystem leverage", "domain_owner": "Chief Procurement Officer"},
    {"code": "WORK", "name": "Workforce Management", "description": "Talent acquisition, development, and employee experience", "domain_type": "supporting", "strategic_focus": "Talent excellence", "domain_owner": "Chief Human Resources Officer"},
    {"code": "TECH", "name": "Technology Enablement", "description": "IT strategy, infrastructure, security, and DevOps", "domain_type": "enabling", "strategic_focus": "Technology modernization", "domain_owner": "Chief Technology Officer"},
]

UNIFIED_CAPABILITIES = {
    "CUST": [
        {"code": "CUST-ACQ", "name": "Customer Acquisition", "category": "core", "children": [
            {"code": "CUST-ACQ-LEAD", "name": "Lead Management", "children": [
                {"code": "CUST-ACQ-LEAD-SCR", "name": "Lead Scoring & Ranking"},
                {"code": "CUST-ACQ-LEAD-RTE", "name": "Lead Assignment & Routing"},
                {"code": "CUST-ACQ-LEAD-PPL", "name": "Pipeline Stage Management"},
            ]},
            {"code": "CUST-ACQ-CAMP", "name": "Campaign Execution", "children": [
                {"code": "CUST-ACQ-CAMP-PLAN", "name": "Campaign Planning & Budgeting"},
                {"code": "CUST-ACQ-CAMP-EXEC", "name": "Multi-Channel Campaign Execution"},
                {"code": "CUST-ACQ-CAMP-PERF", "name": "Campaign Performance Analytics"},
            ]},
            {"code": "CUST-ACQ-QUAL", "name": "Prospect Qualification", "children": [
                {"code": "CUST-ACQ-QUAL-FRM", "name": "Qualification Framework Management"},
                {"code": "CUST-ACQ-QUAL-DIS", "name": "Needs Discovery Process"},
                {"code": "CUST-ACQ-QUAL-OPP", "name": "Opportunity Assessment & Staging"},
            ]},
        ]},
        {"code": "CUST-SVC", "name": "Customer Service", "category": "core", "children": [
            {"code": "CUST-SVC-REQ", "name": "Service Request Management", "children": [
                {"code": "CUST-SVC-REQ-INT", "name": "Request Intake & Logging"},
                {"code": "CUST-SVC-REQ-RTE", "name": "Work Order Assignment & Routing"},
                {"code": "CUST-SVC-REQ-CLO", "name": "Resolution & Closure Tracking"},
            ]},
            {"code": "CUST-SVC-COMP", "name": "Complaint Resolution", "children": [
                {"code": "CUST-SVC-COMP-CAP", "name": "Complaint Capture & Classification"},
                {"code": "CUST-SVC-COMP-INV", "name": "Root Cause Investigation"},
                {"code": "CUST-SVC-COMP-REM", "name": "Remedy & Customer Communication"},
            ]},
            {"code": "CUST-SVC-SLA", "name": "SLA Management", "children": [
                {"code": "CUST-SVC-SLA-DEF", "name": "SLA Policy Definition"},
                {"code": "CUST-SVC-SLA-MON", "name": "Real-Time SLA Monitoring"},
                {"code": "CUST-SVC-SLA-BRE", "name": "Breach Escalation Management"},
            ]},
        ]},
        {"code": "CUST-RET", "name": "Customer Retention", "category": "differentiating", "children": [
            {"code": "CUST-RET-LOY", "name": "Loyalty Program Management", "children": [
                {"code": "CUST-RET-LOY-DES", "name": "Loyalty Scheme Design"},
                {"code": "CUST-RET-LOY-ENR", "name": "Member Enrolment & Tier Management"},
                {"code": "CUST-RET-LOY-RWD", "name": "Rewards Catalogue & Redemption"},
            ]},
            {"code": "CUST-RET-CHR", "name": "Churn Prevention", "children": [
                {"code": "CUST-RET-CHR-SCR", "name": "Churn Risk Scoring"},
                {"code": "CUST-RET-CHR-INT", "name": "Proactive Retention Outreach"},
                {"code": "CUST-RET-CHR-WBK", "name": "Win-Back Programme Management"},
            ]},
        ]},
        {"code": "CUST-ANA", "name": "Customer Analytics", "category": "differentiating", "children": [
            {"code": "CUST-ANA-SEG", "name": "Customer Segmentation", "children": [
                {"code": "CUST-ANA-SEG-DEF", "name": "Segmentation Model Design"},
                {"code": "CUST-ANA-SEG-CLU", "name": "Behavioural Clustering"},
                {"code": "CUST-ANA-SEG-DYN", "name": "Dynamic Segment Refresh"},
            ]},
            {"code": "CUST-ANA-LTV", "name": "Lifetime Value Analysis", "children": [
                {"code": "CUST-ANA-LTV-MOD", "name": "LTV Model Development"},
                {"code": "CUST-ANA-LTV-COH", "name": "Cohort Revenue Analysis"},
                {"code": "CUST-ANA-LTV-PRED", "name": "Predictive LTV Scoring"},
            ]},
        ]},
    ],
    "PROD": [
        {"code": "PROD-DEV", "name": "Product Development", "category": "core", "children": [
            {"code": "PROD-DEV-REQ", "name": "Requirements Management", "children": [
                {"code": "PROD-DEV-REQ-ELI", "name": "Requirements Elicitation"},
                {"code": "PROD-DEV-REQ-PRI", "name": "Requirement Prioritisation"},
                {"code": "PROD-DEV-REQ-BCK", "name": "Backlog Management"},
            ]},
            {"code": "PROD-DEV-DES", "name": "Product Design", "children": [
                {"code": "PROD-DEV-DES-UX", "name": "UX & Interaction Design"},
                {"code": "PROD-DEV-DES-PRO", "name": "Prototyping & Wireframing"},
                {"code": "PROD-DEV-DES-REV", "name": "Design Review & Approval"},
            ]},
            {"code": "PROD-DEV-TEST", "name": "Testing & Validation", "children": [
                {"code": "PROD-DEV-TEST-CAS", "name": "Test Case Design"},
                {"code": "PROD-DEV-TEST-UAT", "name": "User Acceptance Testing"},
                {"code": "PROD-DEV-TEST-DEF", "name": "Defect Management"},
            ]},
        ]},
        {"code": "PROD-LCM", "name": "Product Lifecycle", "category": "core", "children": [
            {"code": "PROD-LCM-LNCH", "name": "Launch Management", "children": [
                {"code": "PROD-LCM-LNCH-GTM", "name": "Go-to-Market Planning"},
                {"code": "PROD-LCM-LNCH-RDY", "name": "Launch Readiness Review"},
                {"code": "PROD-LCM-LNCH-MON", "name": "Post-Launch Monitoring"},
            ]},
            {"code": "PROD-LCM-EOL", "name": "End-of-Life Management", "children": [
                {"code": "PROD-LCM-EOL-SUN", "name": "Sunset Planning"},
                {"code": "PROD-LCM-EOL-MIG", "name": "Customer Migration"},
                {"code": "PROD-LCM-EOL-DCM", "name": "Product Decommission"},
            ]},
        ]},
        {"code": "PROD-PORT", "name": "Product Portfolio", "category": "supporting", "children": [
            {"code": "PROD-PORT-PRI", "name": "Product Prioritization", "children": [
                {"code": "PROD-PORT-PRI-BUS", "name": "Business Case Development"},
                {"code": "PROD-PORT-PRI-SCR", "name": "Prioritisation Scoring"},
                {"code": "PROD-PORT-PRI-INV", "name": "Investment Committee Review"},
            ]},
            {"code": "PROD-PORT-ROAD", "name": "Product Roadmapping", "children": [
                {"code": "PROD-PORT-ROAD-DEV", "name": "Roadmap Development"},
                {"code": "PROD-PORT-ROAD-MLT", "name": "Milestone Tracking"},
                {"code": "PROD-PORT-ROAD-COM", "name": "Stakeholder Communication"},
            ]},
        ]},
        {"code": "PROD-INN", "name": "Product Innovation", "category": "differentiating", "children": [
            {"code": "PROD-INN-PIPE", "name": "Innovation Pipeline", "children": [
                {"code": "PROD-INN-PIPE-IDE", "name": "Idea Collection & Screening"},
                {"code": "PROD-INN-PIPE-CON", "name": "Concept Development"},
                {"code": "PROD-INN-PIPE-GAT", "name": "Innovation Gate Review"},
            ]},
            {"code": "PROD-INN-RES", "name": "Market Research", "children": [
                {"code": "PROD-INN-RES-DES", "name": "Research Design"},
                {"code": "PROD-INN-RES-COM", "name": "Competitive Intelligence"},
                {"code": "PROD-INN-RES-CUS", "name": "Customer Insight Synthesis"},
            ]},
        ]},
    ],
    "OPER": [
        {"code": "OPER-PROC", "name": "Process Management", "category": "core", "children": [
            {"code": "OPER-PROC-DES", "name": "Process Design", "children": [
                {"code": "OPER-PROC-DES-CUR", "name": "As-Is Process Mapping"},
                {"code": "OPER-PROC-DES-FUT", "name": "To-Be Process Design"},
                {"code": "OPER-PROC-DES-DOC", "name": "Process Documentation"},
            ]},
            {"code": "OPER-PROC-OPT", "name": "Process Optimization", "children": [
                {"code": "OPER-PROC-OPT-ANA", "name": "Process Performance Analysis"},
                {"code": "OPER-PROC-OPT-LSS", "name": "Lean / Six Sigma Application"},
                {"code": "OPER-PROC-OPT-BNK", "name": "Benchmarking"},
            ]},
            {"code": "OPER-PROC-AUT", "name": "Process Automation", "children": [
                {"code": "OPER-PROC-AUT-ASS", "name": "Automation Opportunity Assessment"},
                {"code": "OPER-PROC-AUT-IMP", "name": "RPA Implementation"},
                {"code": "OPER-PROC-AUT-MON", "name": "Automation Monitoring"},
            ]},
        ]},
        {"code": "OPER-QUAL", "name": "Quality Management", "category": "core", "children": [
            {"code": "OPER-QUAL-PLAN", "name": "Quality Planning", "children": [
                {"code": "OPER-QUAL-PLAN-STD", "name": "Quality Standards Definition"},
                {"code": "OPER-QUAL-PLAN-PLN", "name": "Quality Plan Development"},
                {"code": "OPER-QUAL-PLAN-INS", "name": "Inspection Planning"},
            ]},
            {"code": "OPER-QUAL-CTL", "name": "Quality Control", "children": [
                {"code": "OPER-QUAL-CTL-SPC", "name": "Statistical Quality Control"},
                {"code": "OPER-QUAL-CTL-NCR", "name": "Non-Conformance Management"},
                {"code": "OPER-QUAL-CTL-CAP", "name": "Corrective & Preventive Action"},
            ]},
            {"code": "OPER-QUAL-CI", "name": "Continuous Improvement", "children": [
                {"code": "OPER-QUAL-CI-OPP", "name": "Improvement Opportunity Identification"},
                {"code": "OPER-QUAL-CI-KAI", "name": "Kaizen Events"},
                {"code": "OPER-QUAL-CI-TRK", "name": "Improvement Tracking"},
            ]},
        ]},
        {"code": "OPER-SCM", "name": "Supply Chain Management", "category": "core", "children": [
            {"code": "OPER-SCM-DEM", "name": "Demand Planning", "children": [
                {"code": "OPER-SCM-DEM-SEN", "name": "Demand Sensing"},
                {"code": "OPER-SCM-DEM-FCT", "name": "Forecast Modelling"},
                {"code": "OPER-SCM-DEM-CON", "name": "Consensus Planning"},
            ]},
            {"code": "OPER-SCM-LOG", "name": "Logistics Management", "children": [
                {"code": "OPER-SCM-LOG-TRP", "name": "Transportation Planning"},
                {"code": "OPER-SCM-LOG-LMD", "name": "Last-Mile Delivery"},
                {"code": "OPER-SCM-LOG-CAR", "name": "Carrier Management"},
            ]},
        ]},
        {"code": "OPER-INV", "name": "Inventory Management", "category": "supporting", "children": [
            {"code": "OPER-INV-STK", "name": "Stock Control", "children": [
                {"code": "OPER-INV-STK-OPT", "name": "Stock Level Optimisation"},
                {"code": "OPER-INV-STK-REP", "name": "Replenishment Management"},
                {"code": "OPER-INV-STK-CYC", "name": "Cycle Counting"},
            ]},
            {"code": "OPER-INV-WMS", "name": "Warehouse Management", "children": [
                {"code": "OPER-INV-WMS-RCV", "name": "Receiving & Put-Away"},
                {"code": "OPER-INV-WMS-PCK", "name": "Pick & Pack Operations"},
                {"code": "OPER-INV-WMS-RCN", "name": "Inventory Reconciliation"},
            ]},
        ]},
    ],
    "FIN": [
        {"code": "FIN-PLAN", "name": "Financial Planning", "category": "core", "children": [
            {"code": "FIN-PLAN-BUD", "name": "Budgeting", "children": [
                {"code": "FIN-PLAN-BUD-PRE", "name": "Budget Preparation"},
                {"code": "FIN-PLAN-BUD-APR", "name": "Budget Approval & Allocation"},
                {"code": "FIN-PLAN-BUD-VAR", "name": "Budget Variance Management"},
            ]},
            {"code": "FIN-PLAN-FCST", "name": "Financial Forecasting", "children": [
                {"code": "FIN-PLAN-FCST-ROL", "name": "Rolling Forecast Management"},
                {"code": "FIN-PLAN-FCST-DRV", "name": "Driver-Based Planning"},
                {"code": "FIN-PLAN-FCST-SCN", "name": "Scenario Analysis"},
            ]},
            {"code": "FIN-PLAN-CAP", "name": "Capital Planning", "children": [
                {"code": "FIN-PLAN-CAP-EVL", "name": "Capital Budget Evaluation"},
                {"code": "FIN-PLAN-CAP-AUT", "name": "CAPEX Authorisation"},
                {"code": "FIN-PLAN-CAP-TRK", "name": "Capital Tracking"},
            ]},
        ]},
        {"code": "FIN-ACCT", "name": "Accounting", "category": "core", "children": [
            {"code": "FIN-ACCT-GL", "name": "General Ledger", "children": [
                {"code": "FIN-ACCT-GL-COA", "name": "Chart of Accounts Management"},
                {"code": "FIN-ACCT-GL-JNL", "name": "Journal Entry Processing"},
                {"code": "FIN-ACCT-GL-CLO", "name": "Period-End Close"},
            ]},
            {"code": "FIN-ACCT-AP", "name": "Accounts Payable", "children": [
                {"code": "FIN-ACCT-AP-INV", "name": "Invoice Processing"},
                {"code": "FIN-ACCT-AP-PAY", "name": "Payment Run Management"},
                {"code": "FIN-ACCT-AP-RCN", "name": "Supplier Reconciliation"},
            ]},
            {"code": "FIN-ACCT-AR", "name": "Accounts Receivable", "children": [
                {"code": "FIN-ACCT-AR-INV", "name": "Invoice Generation"},
                {"code": "FIN-ACCT-AR-COL", "name": "Collections Management"},
                {"code": "FIN-ACCT-AR-CSH", "name": "Cash Application"},
            ]},
        ]},
        {"code": "FIN-TREAS", "name": "Treasury Management", "category": "supporting", "children": [
            {"code": "FIN-TREAS-CASH", "name": "Cash Management", "children": [
                {"code": "FIN-TREAS-CASH-POS", "name": "Cash Positioning"},
                {"code": "FIN-TREAS-CASH-FCT", "name": "Cash Flow Forecasting"},
                {"code": "FIN-TREAS-CASH-BNK", "name": "Bank Account Management"},
            ]},
            {"code": "FIN-TREAS-INV", "name": "Investment Management", "children": [
                {"code": "FIN-TREAS-INV-POL", "name": "Investment Policy Management"},
                {"code": "FIN-TREAS-INV-EXE", "name": "Portfolio Execution"},
                {"code": "FIN-TREAS-INV-RPT", "name": "Investment Reporting"},
            ]},
        ]},
        {"code": "FIN-TAX", "name": "Tax Management", "category": "supporting", "children": [
            {"code": "FIN-TAX-COMP", "name": "Tax Compliance", "children": [
                {"code": "FIN-TAX-COMP-CAL", "name": "Tax Calendar Management"},
                {"code": "FIN-TAX-COMP-RET", "name": "Tax Return Preparation"},
                {"code": "FIN-TAX-COMP-PAY", "name": "Tax Payment"},
            ]},
            {"code": "FIN-TAX-REP", "name": "Tax Reporting", "children": [
                {"code": "FIN-TAX-REP-PRV", "name": "Tax Provision Calculation"},
                {"code": "FIN-TAX-REP-DEF", "name": "Deferred Tax Management"},
                {"code": "FIN-TAX-REP-DIS", "name": "Tax Disclosure"},
            ]},
        ]},
    ],
    "RISK": [
        {"code": "RISK-ERM", "name": "Enterprise Risk Management", "category": "core", "children": [
            {"code": "RISK-ERM-ASS", "name": "Risk Assessment", "children": [
                {"code": "RISK-ERM-ASS-IDE", "name": "Risk Identification"},
                {"code": "RISK-ERM-ASS-QNT", "name": "Risk Quantification"},
                {"code": "RISK-ERM-ASS-REG", "name": "Risk Register Management"},
            ]},
            {"code": "RISK-ERM-MIT", "name": "Risk Mitigation", "children": [
                {"code": "RISK-ERM-MIT-CTL", "name": "Control Design"},
                {"code": "RISK-ERM-MIT-IMP", "name": "Control Implementation"},
                {"code": "RISK-ERM-MIT-ACC", "name": "Risk Acceptance"},
            ]},
            {"code": "RISK-ERM-MON", "name": "Risk Monitoring", "children": [
                {"code": "RISK-ERM-MON-KRI", "name": "KRI Monitoring"},
                {"code": "RISK-ERM-MON-RPT", "name": "Risk Reporting"},
                {"code": "RISK-ERM-MON-EMR", "name": "Emerging Risk Scanning"},
            ]},
        ]},
        {"code": "RISK-REG", "name": "Regulatory Compliance", "category": "core", "children": [
            {"code": "RISK-REG-POL", "name": "Policy Management", "children": [
                {"code": "RISK-REG-POL-AUT", "name": "Policy Authoring"},
                {"code": "RISK-REG-POL-REV", "name": "Policy Review & Approval"},
                {"code": "RISK-REG-POL-DIS", "name": "Policy Distribution"},
            ]},
            {"code": "RISK-REG-AUD", "name": "Audit Management", "children": [
                {"code": "RISK-REG-AUD-PLN", "name": "Audit Programme Planning"},
                {"code": "RISK-REG-AUD-FWK", "name": "Fieldwork Execution"},
                {"code": "RISK-REG-AUD-RPT", "name": "Audit Reporting"},
            ]},
        ]},
        {"code": "RISK-BCM", "name": "Business Continuity", "category": "supporting", "children": [
            {"code": "RISK-BCM-DRP", "name": "Disaster Recovery Planning", "children": [
                {"code": "RISK-BCM-DRP-STR", "name": "Recovery Strategy Development"},
                {"code": "RISK-BCM-DRP-TST", "name": "DR Testing"},
                {"code": "RISK-BCM-DRP-FLO", "name": "Failover Management"},
            ]},
            {"code": "RISK-BCM-BIA", "name": "Business Impact Analysis", "children": [
                {"code": "RISK-BCM-BIA-DEP", "name": "Dependency Mapping"},
                {"code": "RISK-BCM-BIA-RTO", "name": "RTO / RPO Setting"},
                {"code": "RISK-BCM-BIA-SCN", "name": "Impact Scenario Analysis"},
            ]},
        ]},
    ],
    "DATA": [
        {"code": "DATA-GOV", "name": "Data Governance", "category": "core", "children": [
            {"code": "DATA-GOV-QUAL", "name": "Data Quality Management", "children": [
                {"code": "DATA-GOV-QUAL-PRF", "name": "Data Profiling"},
                {"code": "DATA-GOV-QUAL-RUL", "name": "Data Quality Rules"},
                {"code": "DATA-GOV-QUAL-REM", "name": "Remediation Workflow"},
            ]},
            {"code": "DATA-GOV-CAT", "name": "Data Cataloging", "children": [
                {"code": "DATA-GOV-CAT-MET", "name": "Metadata Management"},
                {"code": "DATA-GOV-CAT-GLO", "name": "Business Glossary"},
                {"code": "DATA-GOV-CAT-LIN", "name": "Data Lineage Tracking"},
            ]},
            {"code": "DATA-GOV-PRIV", "name": "Data Privacy", "children": [
                {"code": "DATA-GOV-PRIV-CLS", "name": "Data Classification"},
                {"code": "DATA-GOV-PRIV-CON", "name": "Consent Management"},
                {"code": "DATA-GOV-PRIV-PIA", "name": "Privacy Impact Assessment"},
            ]},
        ]},
        {"code": "DATA-BI", "name": "Business Intelligence", "category": "core", "children": [
            {"code": "DATA-BI-RPT", "name": "Reporting & Dashboards", "children": [
                {"code": "DATA-BI-RPT-DES", "name": "Report Design & Publishing"},
                {"code": "DATA-BI-RPT-SCH", "name": "Report Scheduling"},
                {"code": "DATA-BI-RPT-SSR", "name": "Self-Service Reporting"},
            ]},
            {"code": "DATA-BI-VIZ", "name": "Data Visualization", "children": [
                {"code": "DATA-BI-VIZ-DAS", "name": "Dashboard Design"},
                {"code": "DATA-BI-VIZ-EXP", "name": "Interactive Exploration"},
                {"code": "DATA-BI-VIZ-EMB", "name": "Embedded Analytics"},
            ]},
        ]},
        {"code": "DATA-ADV", "name": "Advanced Analytics", "category": "differentiating", "children": [
            {"code": "DATA-ADV-PRED", "name": "Predictive Analytics", "children": [
                {"code": "DATA-ADV-PRED-FEA", "name": "Feature Engineering"},
                {"code": "DATA-ADV-PRED-MOD", "name": "Model Training & Validation"},
                {"code": "DATA-ADV-PRED-SRV", "name": "Prediction Serving"},
            ]},
            {"code": "DATA-ADV-ML", "name": "Machine Learning", "children": [
                {"code": "DATA-ADV-ML-EXP", "name": "Model Experimentation"},
                {"code": "DATA-ADV-ML-OPS", "name": "MLOps Pipeline"},
                {"code": "DATA-ADV-ML-MON", "name": "Model Performance Monitoring"},
            ]},
        ]},
    ],
    "PART": [
        {"code": "PART-VEND", "name": "Vendor Management", "category": "core", "children": [
            {"code": "PART-VEND-SEL", "name": "Vendor Selection", "children": [
                {"code": "PART-VEND-SEL-SHL", "name": "Vendor Shortlisting"},
                {"code": "PART-VEND-SEL-RFP", "name": "RFP / RFQ Management"},
                {"code": "PART-VEND-SEL-AWD", "name": "Vendor Evaluation & Award"},
            ]},
            {"code": "PART-VEND-PERF", "name": "Vendor Performance", "children": [
                {"code": "PART-VEND-PERF-KPI", "name": "KPI Definition & Tracking"},
                {"code": "PART-VEND-PERF-SCR", "name": "Vendor Scorecard"},
                {"code": "PART-VEND-PERF-REV", "name": "Performance Reviews"},
            ]},
            {"code": "PART-VEND-RISK", "name": "Vendor Risk Assessment", "children": [
                {"code": "PART-VEND-RISK-DD", "name": "Vendor Due Diligence"},
                {"code": "PART-VEND-RISK-MON", "name": "Ongoing Risk Monitoring"},
                {"code": "PART-VEND-RISK-ESC", "name": "Vendor Risk Escalation"},
            ]},
        ]},
        {"code": "PART-ECO", "name": "Partner Ecosystem", "category": "differentiating", "children": [
            {"code": "PART-ECO-ALL", "name": "Alliance Management", "children": [
                {"code": "PART-ECO-ALL-STR", "name": "Alliance Strategy Development"},
                {"code": "PART-ECO-ALL-ONB", "name": "Partner Onboarding"},
                {"code": "PART-ECO-ALL-JBP", "name": "Joint Business Planning"},
            ]},
            {"code": "PART-ECO-CHAN", "name": "Channel Partners", "children": [
                {"code": "PART-ECO-CHAN-PGM", "name": "Channel Programme Design"},
                {"code": "PART-ECO-CHAN-ENB", "name": "Partner Enablement"},
                {"code": "PART-ECO-CHAN-REV", "name": "Channel Revenue Management"},
            ]},
        ]},
        {"code": "PART-CONT", "name": "Contract Management", "category": "supporting", "children": [
            {"code": "PART-CONT-CRE", "name": "Contract Creation", "children": [
                {"code": "PART-CONT-CRE-DFT", "name": "Contract Drafting"},
                {"code": "PART-CONT-CRE-LEG", "name": "Legal Review"},
                {"code": "PART-CONT-CRE-EXE", "name": "Contract Execution"},
            ]},
            {"code": "PART-CONT-COMP", "name": "Contract Compliance", "children": [
                {"code": "PART-CONT-COMP-OBL", "name": "Obligation Tracking"},
                {"code": "PART-CONT-COMP-MON", "name": "Compliance Monitoring"},
                {"code": "PART-CONT-COMP-RNW", "name": "Contract Renewal Management"},
            ]},
        ]},
    ],
    "WORK": [
        {"code": "WORK-ACQ", "name": "Talent Acquisition", "category": "core", "children": [
            {"code": "WORK-ACQ-REC", "name": "Recruitment Management", "children": [
                {"code": "WORK-ACQ-REC-REQ", "name": "Job Requisition Management"},
                {"code": "WORK-ACQ-REC-SRC", "name": "Candidate Sourcing"},
                {"code": "WORK-ACQ-REC-INT", "name": "Interview & Assessment"},
            ]},
            {"code": "WORK-ACQ-ONB", "name": "Onboarding", "children": [
                {"code": "WORK-ACQ-ONB-PRE", "name": "Pre-Boarding Administration"},
                {"code": "WORK-ACQ-ONB-ORI", "name": "New Hire Orientation"},
                {"code": "WORK-ACQ-ONB-INT", "name": "Role Integration"},
            ]},
        ]},
        {"code": "WORK-DEV", "name": "Talent Development", "category": "core", "children": [
            {"code": "WORK-DEV-LRN", "name": "Learning Management", "children": [
                {"code": "WORK-DEV-LRN-NNA", "name": "Learning Needs Analysis"},
                {"code": "WORK-DEV-LRN-CUR", "name": "Curriculum Design"},
                {"code": "WORK-DEV-LRN-DEL", "name": "Training Delivery & Tracking"},
            ]},
            {"code": "WORK-DEV-CAR", "name": "Career Development", "children": [
                {"code": "WORK-DEV-CAR-PTH", "name": "Career Pathway Definition"},
                {"code": "WORK-DEV-CAR-IDP", "name": "Individual Development Planning"},
                {"code": "WORK-DEV-CAR-MNT", "name": "Mentoring"},
            ]},
            {"code": "WORK-DEV-SUC", "name": "Succession Planning", "children": [
                {"code": "WORK-DEV-SUC-IDE", "name": "Successor Identification"},
                {"code": "WORK-DEV-SUC-PPL", "name": "Succession Pipeline Development"},
                {"code": "WORK-DEV-SUC-RDY", "name": "Readiness Assessment"},
            ]},
        ]},
        {"code": "WORK-EXP", "name": "Employee Experience", "category": "differentiating", "children": [
            {"code": "WORK-EXP-ENG", "name": "Engagement Management", "children": [
                {"code": "WORK-EXP-ENG-SRV", "name": "Engagement Survey Management"},
                {"code": "WORK-EXP-ENG-ACT", "name": "Action Planning"},
                {"code": "WORK-EXP-ENG-REC", "name": "Recognition Programmes"},
            ]},
            {"code": "WORK-EXP-WELL", "name": "Wellness Programs", "children": [
                {"code": "WORK-EXP-WELL-DES", "name": "Wellbeing Programme Design"},
                {"code": "WORK-EXP-WELL-EAP", "name": "EAP Management"},
                {"code": "WORK-EXP-WELL-TRK", "name": "Wellness Tracking"},
            ]},
        ]},
        {"code": "WORK-PLAN", "name": "Workforce Planning", "category": "supporting", "children": [
            {"code": "WORK-PLAN-HC", "name": "Headcount Planning", "children": [
                {"code": "WORK-PLAN-HC-MOD", "name": "Headcount Modelling"},
                {"code": "WORK-PLAN-HC-ORG", "name": "Organisational Design"},
                {"code": "WORK-PLAN-HC-SPL", "name": "Span & Layer Analysis"},
            ]},
            {"code": "WORK-PLAN-SKL", "name": "Skills Gap Analysis", "children": [
                {"code": "WORK-PLAN-SKL-INV", "name": "Skills Inventory"},
                {"code": "WORK-PLAN-SKL-GAP", "name": "Gap Analysis"},
                {"code": "WORK-PLAN-SKL-RSK", "name": "Workforce Reskilling Plan"},
            ]},
        ]},
    ],
    "TECH": [
        {"code": "TECH-STRAT", "name": "IT Strategy", "category": "core", "children": [
            {"code": "TECH-STRAT-ROAD", "name": "Technology Roadmap", "children": [
                {"code": "TECH-STRAT-ROAD-ASS", "name": "Technology Assessment"},
                {"code": "TECH-STRAT-ROAD-DEV", "name": "Roadmap Development"},
                {"code": "TECH-STRAT-ROAD-ALN", "name": "Portfolio Alignment"},
            ]},
            {"code": "TECH-STRAT-EA", "name": "Enterprise Architecture", "children": [
                {"code": "TECH-STRAT-EA-PRI", "name": "Architecture Principles"},
                {"code": "TECH-STRAT-EA-REV", "name": "Architecture Review"},
                {"code": "TECH-STRAT-EA-REP", "name": "EA Repository Management"},
            ]},
            {"code": "TECH-STRAT-GOV", "name": "IT Governance", "children": [
                {"code": "TECH-STRAT-GOV-POL", "name": "IT Policy Framework"},
                {"code": "TECH-STRAT-GOV-INV", "name": "IT Investment Governance"},
                {"code": "TECH-STRAT-GOV-CMP", "name": "Architecture Compliance"},
            ]},
        ]},
        {"code": "TECH-APP", "name": "Application Management", "category": "core", "children": [
            {"code": "TECH-APP-PORT", "name": "Application Portfolio", "children": [
                {"code": "TECH-APP-PORT-INV", "name": "Application Inventory"},
                {"code": "TECH-APP-PORT-RAT", "name": "Rationalisation Assessment"},
                {"code": "TECH-APP-PORT-HLT", "name": "Application Health Scoring"},
            ]},
            {"code": "TECH-APP-LCM", "name": "Application Lifecycle", "children": [
                {"code": "TECH-APP-LCM-ONB", "name": "Application Onboarding"},
                {"code": "TECH-APP-LCM-PAT", "name": "Patch & Version Management"},
                {"code": "TECH-APP-LCM-RET", "name": "Application Retirement"},
            ]},
        ]},
        {"code": "TECH-INF", "name": "Infrastructure Management", "category": "supporting", "children": [
            {"code": "TECH-INF-COMP", "name": "Compute & Network", "children": [
                {"code": "TECH-INF-COMP-SRV", "name": "Server Provisioning"},
                {"code": "TECH-INF-COMP-NET", "name": "Network Management"},
                {"code": "TECH-INF-COMP-CAP", "name": "Capacity Planning"},
            ]},
            {"code": "TECH-INF-CLD", "name": "Cloud Management", "children": [
                {"code": "TECH-INF-CLD-ACT", "name": "Cloud Account Management"},
                {"code": "TECH-INF-CLD-CST", "name": "Cloud Cost Optimisation"},
                {"code": "TECH-INF-CLD-GOV", "name": "Cloud Governance"},
            ]},
        ]},
        {"code": "TECH-SEC", "name": "Security Management", "category": "core", "children": [
            {"code": "TECH-SEC-IAM", "name": "Identity & Access Management", "children": [
                {"code": "TECH-SEC-IAM-PRV", "name": "User Provisioning"},
                {"code": "TECH-SEC-IAM-AUT", "name": "Authentication Management"},
                {"code": "TECH-SEC-IAM-PAM", "name": "Privileged Access Management"},
            ]},
            {"code": "TECH-SEC-OPS", "name": "Security Operations", "children": [
                {"code": "TECH-SEC-OPS-TI", "name": "Threat Intelligence"},
                {"code": "TECH-SEC-OPS-VLN", "name": "Vulnerability Management"},
                {"code": "TECH-SEC-OPS-INC", "name": "Incident Response"},
            ]},
        ]},
    ],
}

# =============================================================================
# APPLICATION CAPABILITIES — ArchiMate 3.2 Application Layer
# These represent application-layer capabilities (software systems / platforms)
# that realize Business Capabilities. Keyed by the closest business domain.
# =============================================================================
APPLICATION_CAPABILITIES = {
    "CUST": [
        {"code": "APP-CUST-CRM", "name": "Customer Relationship Management", "category": "core", "children": [
            {"code": "APP-CUST-CRM-SFA", "name": "Sales Force Automation", "children": [
                {"code": "APP-CUST-CRM-SFA-ACC", "name": "Account & Contact Management"},
                {"code": "APP-CUST-CRM-SFA-OPP", "name": "Opportunity Pipeline Management"},
                {"code": "APP-CUST-CRM-SFA-FCT", "name": "Sales Forecasting"},
            ]},
            {"code": "APP-CUST-CRM-SVC", "name": "Customer Service Management", "children": [
                {"code": "APP-CUST-CRM-SVC-CAS", "name": "Case Management"},
                {"code": "APP-CUST-CRM-SVC-KNW", "name": "Knowledge Base"},
                {"code": "APP-CUST-CRM-SVC-FSM", "name": "Field Service Management"},
            ]},
            {"code": "APP-CUST-CRM-MKT", "name": "Marketing Automation", "children": [
                {"code": "APP-CUST-CRM-MKT-EML", "name": "Email Campaign Management"},
                {"code": "APP-CUST-CRM-MKT-ATR", "name": "Marketing Attribution"},
                {"code": "APP-CUST-CRM-MKT-NRT", "name": "Lead Nurturing"},
            ]},
        ]},
        {"code": "APP-CUST-CPX", "name": "Customer Experience Platform", "category": "differentiating", "children": [
            {"code": "APP-CUST-CPX-POR", "name": "Customer Self-Service Portal", "children": [
                {"code": "APP-CUST-CPX-POR-ACC", "name": "Account Self-Service"},
                {"code": "APP-CUST-CPX-POR-ORD", "name": "Order Status Tracking"},
                {"code": "APP-CUST-CPX-POR-SUP", "name": "Online Support Portal"},
            ]},
            {"code": "APP-CUST-CPX-OMS", "name": "Omni-Channel Engagement", "children": [
                {"code": "APP-CUST-CPX-OMS-INB", "name": "Unified Inbox Management"},
                {"code": "APP-CUST-CPX-OMS-ORC", "name": "Channel Orchestration"},
                {"code": "APP-CUST-CPX-OMS-ANA", "name": "Conversation Analytics"},
            ]},
            {"code": "APP-CUST-CPX-CDP", "name": "Customer Data Platform", "children": [
                {"code": "APP-CUST-CPX-CDP-IDR", "name": "Identity Resolution"},
                {"code": "APP-CUST-CPX-CDP-UCP", "name": "Unified Customer Profile"},
                {"code": "APP-CUST-CPX-CDP-ACT", "name": "Audience Activation"},
            ]},
        ]},
        {"code": "APP-CUST-COM", "name": "Commerce Platform", "category": "core", "children": [
            {"code": "APP-CUST-COM-ECM", "name": "E-Commerce Management", "children": [
                {"code": "APP-CUST-COM-ECM-CAT", "name": "Product Catalogue Management"},
                {"code": "APP-CUST-COM-ECM-CHK", "name": "Shopping Cart & Checkout"},
                {"code": "APP-CUST-COM-ECM-ORD", "name": "Order Management"},
            ]},
            {"code": "APP-CUST-COM-CPQ", "name": "Configure Price Quote", "children": [
                {"code": "APP-CUST-COM-CPQ-CFG", "name": "Product Configuration Engine"},
                {"code": "APP-CUST-COM-CPQ-PRC", "name": "Dynamic Pricing"},
                {"code": "APP-CUST-COM-CPQ-PRO", "name": "Proposal Generation"},
            ]},
        ]},
    ],
    "PROD": [
        {"code": "APP-PROD-PLM", "name": "Product Lifecycle Management", "category": "core", "children": [
            {"code": "APP-PROD-PLM-PDM", "name": "Product Data Management", "children": [
                {"code": "APP-PROD-PLM-PDM-BOM", "name": "BOM Management"},
                {"code": "APP-PROD-PLM-PDM-DOC", "name": "Document Management"},
                {"code": "APP-PROD-PLM-PDM-ECO", "name": "Engineering Change Control"},
            ]},
            {"code": "APP-PROD-PLM-RDM", "name": "Requirements & Design Management", "children": [
                {"code": "APP-PROD-PLM-RDM-CAP", "name": "Requirements Capture"},
                {"code": "APP-PROD-PLM-RDM-TRC", "name": "Requirement Traceability"},
                {"code": "APP-PROD-PLM-RDM-APR", "name": "Approval Workflows"},
            ]},
            {"code": "APP-PROD-PLM-QMS", "name": "Quality Management System", "children": [
                {"code": "APP-PROD-PLM-QMS-INS", "name": "Inspection Plans"},
                {"code": "APP-PROD-PLM-QMS-CAP", "name": "CAPA Management"},
                {"code": "APP-PROD-PLM-QMS-AUD", "name": "Audit & Non-Conformance"},
            ]},
        ]},
        {"code": "APP-PROD-INN", "name": "Innovation Management Platform", "category": "differentiating", "children": [
            {"code": "APP-PROD-INN-IDE", "name": "Idea & Concept Management", "children": [
                {"code": "APP-PROD-INN-IDE-SUB", "name": "Idea Submission Portal"},
                {"code": "APP-PROD-INN-IDE-GAT", "name": "Innovation Stage-Gate"},
                {"code": "APP-PROD-INN-IDE-SCR", "name": "Concept Scoring"},
            ]},
            {"code": "APP-PROD-INN-RD", "name": "R&D Portfolio Management", "children": [
                {"code": "APP-PROD-INN-RD-PRJ", "name": "R&D Project Tracking"},
                {"code": "APP-PROD-INN-RD-TWC", "name": "Technology Watch"},
                {"code": "APP-PROD-INN-RD-IP", "name": "IP Management"},
            ]},
        ]},
    ],
    "OPER": [
        {"code": "APP-OPER-ERP", "name": "Enterprise Resource Planning", "category": "core", "children": [
            {"code": "APP-OPER-ERP-FIN", "name": "Finance & Accounting Module", "children": [
                {"code": "APP-OPER-ERP-FIN-GL", "name": "GL & Period Close"},
                {"code": "APP-OPER-ERP-FIN-AP", "name": "Accounts Payable"},
                {"code": "APP-OPER-ERP-FIN-AR", "name": "Accounts Receivable"},
            ]},
            {"code": "APP-OPER-ERP-SCM", "name": "Supply Chain Module", "children": [
                {"code": "APP-OPER-ERP-SCM-PO", "name": "Purchase Order Management"},
                {"code": "APP-OPER-ERP-SCM-GR", "name": "Goods Receipt"},
                {"code": "APP-OPER-ERP-SCM-INV", "name": "Supplier Invoice Matching"},
            ]},
            {"code": "APP-OPER-ERP-WFM", "name": "Workforce Management Module", "children": [
                {"code": "APP-OPER-ERP-WFM-TNA", "name": "Time & Attendance"},
                {"code": "APP-OPER-ERP-WFM-SFT", "name": "Shift Planning"},
                {"code": "APP-OPER-ERP-WFM-LAB", "name": "Labour Costing"},
            ]},
        ]},
        {"code": "APP-OPER-BPM", "name": "Business Process Management", "category": "core", "children": [
            {"code": "APP-OPER-BPM-WFL", "name": "Workflow Automation", "children": [
                {"code": "APP-OPER-BPM-WFL-MOD", "name": "Process Modelling"},
                {"code": "APP-OPER-BPM-WFL-ORC", "name": "Workflow Orchestration"},
                {"code": "APP-OPER-BPM-WFL-TSK", "name": "Task Assignment & Tracking"},
            ]},
            {"code": "APP-OPER-BPM-RPA", "name": "Robotic Process Automation", "children": [
                {"code": "APP-OPER-BPM-RPA-BOT", "name": "Bot Development"},
                {"code": "APP-OPER-BPM-RPA-MON", "name": "Process Automation Monitoring"},
                {"code": "APP-OPER-BPM-RPA-EXC", "name": "Exception Handling"},
            ]},
            {"code": "APP-OPER-BPM-LOW", "name": "Low-Code / No-Code Platform", "children": [
                {"code": "APP-OPER-BPM-LOW-BLD", "name": "Application Builder"},
                {"code": "APP-OPER-BPM-LOW-STD", "name": "Process Automation Studio"},
                {"code": "APP-OPER-BPM-LOW-MOB", "name": "Mobile App Generation"},
            ]},
        ]},
        {"code": "APP-OPER-SCM", "name": "Supply Chain Platform", "category": "core", "children": [
            {"code": "APP-OPER-SCM-TMS", "name": "Transportation Management", "children": [
                {"code": "APP-OPER-SCM-TMS-SHP", "name": "Shipment Planning"},
                {"code": "APP-OPER-SCM-TMS-CAR", "name": "Carrier Rate Management"},
                {"code": "APP-OPER-SCM-TMS-TRK", "name": "Track & Trace"},
            ]},
            {"code": "APP-OPER-SCM-WMS", "name": "Warehouse Management System", "children": [
                {"code": "APP-OPER-SCM-WMS-RCV", "name": "Receiving & Putaway"},
                {"code": "APP-OPER-SCM-WMS-PCK", "name": "Picking & Packing"},
                {"code": "APP-OPER-SCM-WMS-VIS", "name": "Inventory Visibility"},
            ]},
            {"code": "APP-OPER-SCM-SCP", "name": "Supply Chain Planning", "children": [
                {"code": "APP-OPER-SCM-SCP-DEM", "name": "Demand Planning"},
                {"code": "APP-OPER-SCM-SCP-SUP", "name": "Supply Planning"},
                {"code": "APP-OPER-SCM-SCP-SOP", "name": "S&OP Collaboration"},
            ]},
        ]},
    ],
    "FIN": [
        {"code": "APP-FIN-FMS", "name": "Financial Management System", "category": "core", "children": [
            {"code": "APP-FIN-FMS-GL", "name": "General Ledger System", "children": [
                {"code": "APP-FIN-FMS-GL-COA", "name": "Chart of Accounts"},
                {"code": "APP-FIN-FMS-GL-ICP", "name": "Intercompany Processing"},
                {"code": "APP-FIN-FMS-GL-CLO", "name": "Financial Close Automation"},
            ]},
            {"code": "APP-FIN-FMS-AP", "name": "Accounts Payable Automation", "children": [
                {"code": "APP-FIN-FMS-AP-CAP", "name": "Invoice Capture & Matching"},
                {"code": "APP-FIN-FMS-AP-PAY", "name": "Payment Processing"},
                {"code": "APP-FIN-FMS-AP-POR", "name": "Vendor Portal"},
            ]},
            {"code": "APP-FIN-FMS-AR", "name": "Accounts Receivable Management", "children": [
                {"code": "APP-FIN-FMS-AR-BIL", "name": "Billing & Invoicing"},
                {"code": "APP-FIN-FMS-AR-CSH", "name": "Cash Application"},
                {"code": "APP-FIN-FMS-AR-DIS", "name": "Dispute Management"},
            ]},
        ]},
        {"code": "APP-FIN-EPM", "name": "Enterprise Performance Management", "category": "core", "children": [
            {"code": "APP-FIN-EPM-PLAN", "name": "Planning & Budgeting Platform", "children": [
                {"code": "APP-FIN-EPM-PLAN-DRV", "name": "Driver-Based Budgeting"},
                {"code": "APP-FIN-EPM-PLAN-ROL", "name": "Rolling Forecast"},
                {"code": "APP-FIN-EPM-PLAN-WIF", "name": "What-If Modelling"},
            ]},
            {"code": "APP-FIN-EPM-CONS", "name": "Financial Consolidation", "children": [
                {"code": "APP-FIN-EPM-CONS-ENT", "name": "Legal Entity Consolidation"},
                {"code": "APP-FIN-EPM-CONS-ELM", "name": "Intercompany Elimination"},
                {"code": "APP-FIN-EPM-CONS-RPT", "name": "GAAP / IFRS Reporting"},
            ]},
            {"code": "APP-FIN-EPM-FCST", "name": "Forecasting & Scenario Modelling", "children": [
                {"code": "APP-FIN-EPM-FCST-LRP", "name": "Long-Range Planning"},
                {"code": "APP-FIN-EPM-FCST-SCN", "name": "Scenario & Sensitivity Analysis"},
                {"code": "APP-FIN-EPM-FCST-AI", "name": "AI-Powered Forecasting"},
            ]},
        ]},
        {"code": "APP-FIN-EXP", "name": "Expense & Travel Management", "category": "supporting", "children": [
            {"code": "APP-FIN-EXP-T&E", "name": "Travel & Expense Platform", "children": [
                {"code": "APP-FIN-EXP-T&E-SUB", "name": "Expense Submission"},
                {"code": "APP-FIN-EXP-T&E-POL", "name": "Policy Compliance Check"},
                {"code": "APP-FIN-EXP-T&E-RMB", "name": "Reimbursement Processing"},
            ]},
            {"code": "APP-FIN-EXP-PRC", "name": "Procurement Card Management", "children": [
                {"code": "APP-FIN-EXP-PRC-ISS", "name": "Card Issuance Management"},
                {"code": "APP-FIN-EXP-PRC-MON", "name": "Transaction Monitoring"},
                {"code": "APP-FIN-EXP-PRC-RPT", "name": "Card Spend Reporting"},
            ]},
        ]},
    ],
    "RISK": [
        {"code": "APP-RISK-GRC", "name": "Governance Risk & Compliance Platform", "category": "core", "children": [
            {"code": "APP-RISK-GRC-POL", "name": "Policy & Controls Management", "children": [
                {"code": "APP-RISK-GRC-POL-LIB", "name": "Policy Library Management"},
                {"code": "APP-RISK-GRC-POL-CTL", "name": "Control Mapping"},
                {"code": "APP-RISK-GRC-POL-ACK", "name": "Policy Acknowledgement"},
            ]},
            {"code": "APP-RISK-GRC-AUD", "name": "Audit Management System", "children": [
                {"code": "APP-RISK-GRC-AUD-SCH", "name": "Audit Programme Scheduling"},
                {"code": "APP-RISK-GRC-AUD-FLD", "name": "Fieldwork & Evidence Collection"},
                {"code": "APP-RISK-GRC-AUD-FND", "name": "Finding Management"},
            ]},
            {"code": "APP-RISK-GRC-INC", "name": "Incident & Issue Management", "children": [
                {"code": "APP-RISK-GRC-INC-LOG", "name": "Incident Logging"},
                {"code": "APP-RISK-GRC-INC-RCA", "name": "Root Cause Analysis"},
                {"code": "APP-RISK-GRC-INC-CAP", "name": "Corrective Action Tracking"},
            ]},
        ]},
        {"code": "APP-RISK-BCM", "name": "Business Continuity Platform", "category": "supporting", "children": [
            {"code": "APP-RISK-BCM-DRP", "name": "Disaster Recovery Orchestration", "children": [
                {"code": "APP-RISK-BCM-DRP-REP", "name": "DR Plan Repository"},
                {"code": "APP-RISK-BCM-DRP-TST", "name": "DR Test Orchestration"},
                {"code": "APP-RISK-BCM-DRP-EXE", "name": "Recovery Execution"},
            ]},
            {"code": "APP-RISK-BCM-CRP", "name": "Crisis Response Management", "children": [
                {"code": "APP-RISK-BCM-CRP-COM", "name": "Crisis Communication"},
                {"code": "APP-RISK-BCM-CRP-SIT", "name": "Situation Room Management"},
                {"code": "APP-RISK-BCM-CRP-NTF", "name": "Stakeholder Notification"},
            ]},
        ]},
    ],
    "DATA": [
        {"code": "APP-DATA-BI", "name": "Business Intelligence & Analytics Platform", "category": "core", "children": [
            {"code": "APP-DATA-BI-RPT", "name": "Enterprise Reporting Engine", "children": [
                {"code": "APP-DATA-BI-RPT-DES", "name": "Report Design Studio"},
                {"code": "APP-DATA-BI-RPT-DST", "name": "Report Distribution"},
                {"code": "APP-DATA-BI-RPT-OUT", "name": "Pixel-Perfect Output"},
            ]},
            {"code": "APP-DATA-BI-VIZ", "name": "Data Visualisation & Dashboards", "children": [
                {"code": "APP-DATA-BI-VIZ-DSH", "name": "Interactive Dashboard Builder"},
                {"code": "APP-DATA-BI-VIZ-MOB", "name": "Mobile Analytics"},
                {"code": "APP-DATA-BI-VIZ-EMB", "name": "Embedded Analytics SDK"},
            ]},
            {"code": "APP-DATA-BI-AUG", "name": "Augmented Analytics", "children": [
                {"code": "APP-DATA-BI-AUG-NLQ", "name": "Natural Language Query"},
                {"code": "APP-DATA-BI-AUG-INS", "name": "Auto-Insight Generation"},
                {"code": "APP-DATA-BI-AUG-EXP", "name": "Explainability"},
            ]},
        ]},
        {"code": "APP-DATA-DI", "name": "Data Integration & ETL Platform", "category": "core", "children": [
            {"code": "APP-DATA-DI-ETL", "name": "ETL / ELT Processing", "children": [
                {"code": "APP-DATA-DI-ETL-AUT", "name": "Pipeline Authoring"},
                {"code": "APP-DATA-DI-ETL-TRN", "name": "Data Transformation"},
                {"code": "APP-DATA-DI-ETL-SCH", "name": "Job Scheduling & Monitoring"},
            ]},
            {"code": "APP-DATA-DI-CDC", "name": "Change Data Capture", "children": [
                {"code": "APP-DATA-DI-CDC-CAP", "name": "Change Event Capture"},
                {"code": "APP-DATA-DI-CDC-REP", "name": "Low-Latency Replication"},
                {"code": "APP-DATA-DI-CDC-SCH", "name": "Schema Evolution"},
            ]},
            {"code": "APP-DATA-DI-STRM", "name": "Real-Time Data Streaming", "children": [
                {"code": "APP-DATA-DI-STRM-ING", "name": "Event Ingestion"},
                {"code": "APP-DATA-DI-STRM-PRC", "name": "Stream Processing"},
                {"code": "APP-DATA-DI-STRM-DEL", "name": "Real-Time Data Delivery"},
            ]},
        ]},
        {"code": "APP-DATA-MDM", "name": "Master Data Management", "category": "differentiating", "children": [
            {"code": "APP-DATA-MDM-CUS", "name": "Customer MDM", "children": [
                {"code": "APP-DATA-MDM-CUS-MTH", "name": "Customer Record Matching"},
                {"code": "APP-DATA-MDM-CUS-GLD", "name": "Golden Record Management"},
                {"code": "APP-DATA-MDM-CUS-HIR", "name": "Hierarchy Management"},
            ]},
            {"code": "APP-DATA-MDM-PRD", "name": "Product MDM", "children": [
                {"code": "APP-DATA-MDM-PRD-ATR", "name": "Product Attribute Management"},
                {"code": "APP-DATA-MDM-PRD-TAX", "name": "Classification & Taxonomy"},
                {"code": "APP-DATA-MDM-PRD-SYN", "name": "Syndication"},
            ]},
            {"code": "APP-DATA-MDM-LOC", "name": "Location & Hierarchy MDM", "children": [
                {"code": "APP-DATA-MDM-LOC-HIR", "name": "Location Hierarchy"},
                {"code": "APP-DATA-MDM-LOC-VAL", "name": "Address Validation"},
                {"code": "APP-DATA-MDM-LOC-GEO", "name": "Geographic Data Management"},
            ]},
        ]},
        {"code": "APP-DATA-AI", "name": "AI & Machine Learning Platform", "category": "differentiating", "children": [
            {"code": "APP-DATA-AI-MLO", "name": "MLOps & Model Management", "children": [
                {"code": "APP-DATA-AI-MLO-FST", "name": "Feature Store"},
                {"code": "APP-DATA-AI-MLO-TRN", "name": "Model Training & Registry"},
                {"code": "APP-DATA-AI-MLO-SRV", "name": "Deployment & Serving"},
            ]},
            {"code": "APP-DATA-AI-GEN", "name": "Generative AI Services", "children": [
                {"code": "APP-DATA-AI-GEN-LLM", "name": "LLM Integration"},
                {"code": "APP-DATA-AI-GEN-PRM", "name": "Prompt Engineering"},
                {"code": "APP-DATA-AI-GEN-RAG", "name": "RAG Pipeline Management"},
            ]},
            {"code": "APP-DATA-AI-NLP", "name": "Natural Language Processing", "children": [
                {"code": "APP-DATA-AI-NLP-CLS", "name": "Text Classification"},
                {"code": "APP-DATA-AI-NLP-ENT", "name": "Entity Extraction"},
                {"code": "APP-DATA-AI-NLP-SEN", "name": "Sentiment Analysis"},
            ]},
        ]},
    ],
    "PART": [
        {"code": "APP-PART-PRO", "name": "Procurement Platform", "category": "core", "children": [
            {"code": "APP-PART-PRO-SRC", "name": "Strategic Sourcing", "children": [
                {"code": "APP-PART-PRO-SRC-CAT", "name": "Category Strategy Management"},
                {"code": "APP-PART-PRO-SRC-EVT", "name": "Sourcing Event Management"},
                {"code": "APP-PART-PRO-SRC-AWD", "name": "Contract Award"},
            ]},
            {"code": "APP-PART-PRO-P2P", "name": "Purchase-to-Pay Automation", "children": [
                {"code": "APP-PART-PRO-P2P-REQ", "name": "Purchase Requisition"},
                {"code": "APP-PART-PRO-P2P-PO", "name": "PO Management"},
                {"code": "APP-PART-PRO-P2P-INV", "name": "Invoice & Payment Automation"},
            ]},
            {"code": "APP-PART-PRO-CTR", "name": "Contract Lifecycle Management", "children": [
                {"code": "APP-PART-PRO-CTR-AUT", "name": "Contract Authoring"},
                {"code": "APP-PART-PRO-CTR-OBL", "name": "Obligation Management"},
                {"code": "APP-PART-PRO-CTR-REP", "name": "Contract Repository"},
            ]},
        ]},
        {"code": "APP-PART-SRM", "name": "Supplier Relationship Management", "category": "supporting", "children": [
            {"code": "APP-PART-SRM-ONB", "name": "Supplier Onboarding & Qualification", "children": [
                {"code": "APP-PART-SRM-ONB-REG", "name": "Supplier Registration Portal"},
                {"code": "APP-PART-SRM-ONB-KYC", "name": "KYC & Due Diligence"},
                {"code": "APP-PART-SRM-ONB-APR", "name": "Approval Workflows"},
            ]},
            {"code": "APP-PART-SRM-PRF", "name": "Supplier Performance Management", "children": [
                {"code": "APP-PART-SRM-PRF-KPI", "name": "Supplier KPI Dashboard"},
                {"code": "APP-PART-SRM-PRF-SCR", "name": "Scorecard Distribution"},
                {"code": "APP-PART-SRM-PRF-IMP", "name": "Improvement Planning"},
            ]},
        ]},
    ],
    "WORK": [
        {"code": "APP-WORK-HCM", "name": "Human Capital Management Platform", "category": "core", "children": [
            {"code": "APP-WORK-HCM-REC", "name": "Applicant Tracking & Recruitment", "children": [
                {"code": "APP-WORK-HCM-REC-JOB", "name": "Job Posting"},
                {"code": "APP-WORK-HCM-REC-SCR", "name": "CV Screening & Ranking"},
                {"code": "APP-WORK-HCM-REC-INT", "name": "Interview Scheduling"},
            ]},
            {"code": "APP-WORK-HCM-ONB", "name": "Onboarding Platform", "children": [
                {"code": "APP-WORK-HCM-ONB-PPR", "name": "Digital Paperwork"},
                {"code": "APP-WORK-HCM-ONB-RDY", "name": "Day-One Readiness"},
                {"code": "APP-WORK-HCM-ONB-ENG", "name": "Early Engagement"},
            ]},
            {"code": "APP-WORK-HCM-PAY", "name": "Payroll Management", "children": [
                {"code": "APP-WORK-HCM-PAY-ENG", "name": "Payroll Processing Engine"},
                {"code": "APP-WORK-HCM-PAY-TAX", "name": "Tax Calculation"},
                {"code": "APP-WORK-HCM-PAY-SLP", "name": "Payslip Distribution"},
            ]},
        ]},
        {"code": "APP-WORK-LMS", "name": "Learning Management System", "category": "core", "children": [
            {"code": "APP-WORK-LMS-CRS", "name": "Course & Content Management", "children": [
                {"code": "APP-WORK-LMS-CRS-CAT", "name": "Course Catalogue"},
                {"code": "APP-WORK-LMS-CRS-PTH", "name": "Learning Path Management"},
                {"code": "APP-WORK-LMS-CRS-ASS", "name": "Assessment & Certification"},
            ]},
            {"code": "APP-WORK-LMS-SKL", "name": "Skills & Competency Framework", "children": [
                {"code": "APP-WORK-LMS-SKL-PRF", "name": "Skill Profile Management"},
                {"code": "APP-WORK-LMS-SKL-GAP", "name": "Gap Assessment"},
                {"code": "APP-WORK-LMS-SKL-REC", "name": "Recommended Learning"},
            ]},
        ]},
        {"code": "APP-WORK-COL", "name": "Collaboration & Productivity Platform", "category": "supporting", "children": [
            {"code": "APP-WORK-COL-MSG", "name": "Messaging & Video Conferencing", "children": [
                {"code": "APP-WORK-COL-MSG-IM", "name": "Instant Messaging"},
                {"code": "APP-WORK-COL-MSG-VID", "name": "Video Conferencing"},
                {"code": "APP-WORK-COL-MSG-VRM", "name": "Virtual Rooms"},
            ]},
            {"code": "APP-WORK-COL-DOC", "name": "Document & Knowledge Management", "children": [
                {"code": "APP-WORK-COL-DOC-AUT", "name": "Document Authoring"},
                {"code": "APP-WORK-COL-DOC-VER", "name": "Version Control"},
                {"code": "APP-WORK-COL-DOC-SRH", "name": "Knowledge Search"},
            ]},
        ]},
    ],
    "TECH": [
        {"code": "APP-TECH-ITSM", "name": "IT Service Management", "category": "core", "children": [
            {"code": "APP-TECH-ITSM-INC", "name": "Incident & Problem Management", "children": [
                {"code": "APP-TECH-ITSM-INC-LOG", "name": "Incident Logging & Triage"},
                {"code": "APP-TECH-ITSM-INC-ESC", "name": "Escalation Management"},
                {"code": "APP-TECH-ITSM-INC-PRB", "name": "Root Cause & Problem Linking"},
            ]},
            {"code": "APP-TECH-ITSM-CHG", "name": "Change & Release Management", "children": [
                {"code": "APP-TECH-ITSM-CHG-REQ", "name": "Change Request Management"},
                {"code": "APP-TECH-ITSM-CHG-CAB", "name": "Change Advisory Board"},
                {"code": "APP-TECH-ITSM-CHG-CAL", "name": "Release Calendar"},
            ]},
            {"code": "APP-TECH-ITSM-CMB", "name": "CMDB & Asset Registry", "children": [
                {"code": "APP-TECH-ITSM-CMB-DIS", "name": "CI Discovery & Import"},
                {"code": "APP-TECH-ITSM-CMB-DEP", "name": "Dependency Mapping"},
                {"code": "APP-TECH-ITSM-CMB-RCN", "name": "CMDB Reconciliation"},
            ]},
        ]},
        {"code": "APP-TECH-DEV", "name": "DevOps & Development Platform", "category": "differentiating", "children": [
            {"code": "APP-TECH-DEV-CICD", "name": "CI/CD Pipeline", "children": [
                {"code": "APP-TECH-DEV-CICD-BLD", "name": "Build Pipeline"},
                {"code": "APP-TECH-DEV-CICD-TST", "name": "Automated Testing Gate"},
                {"code": "APP-TECH-DEV-CICD-DEP", "name": "Deployment Orchestration"},
            ]},
            {"code": "APP-TECH-DEV-SCM", "name": "Source Code Management", "children": [
                {"code": "APP-TECH-DEV-SCM-REP", "name": "Repository Management"},
                {"code": "APP-TECH-DEV-SCM-BRN", "name": "Branch Strategy"},
                {"code": "APP-TECH-DEV-SCM-REV", "name": "Code Review & Merge"},
            ]},
            {"code": "APP-TECH-DEV-TEST", "name": "Test Automation Platform", "children": [
                {"code": "APP-TECH-DEV-TEST-MGT", "name": "Test Management"},
                {"code": "APP-TECH-DEV-TEST-EXE", "name": "Test Execution"},
                {"code": "APP-TECH-DEV-TEST-DEF", "name": "Defect Tracking"},
            ]},
        ]},
        {"code": "APP-TECH-EA", "name": "Enterprise Architecture Platform", "category": "supporting", "children": [
            {"code": "APP-TECH-EA-REPO", "name": "Architecture Repository", "children": [
                {"code": "APP-TECH-EA-REPO-REG", "name": "Architecture Object Registry"},
                {"code": "APP-TECH-EA-REPO-REL", "name": "Relationship Mapping"},
                {"code": "APP-TECH-EA-REPO-IMP", "name": "Impact Analysis"},
            ]},
            {"code": "APP-TECH-EA-MODL", "name": "Architecture Modelling Tool", "children": [
                {"code": "APP-TECH-EA-MODL-ARM", "name": "ArchiMate Modelling"},
                {"code": "APP-TECH-EA-MODL-BPM", "name": "BPMN Modelling"},
                {"code": "APP-TECH-EA-MODL-VIW", "name": "View Generation"},
            ]},
        ]},
    ],
}

# =============================================================================
# UNIFIED TECHNICAL CAPABILITIES — ArchiMate 3.2 Technology Layer
# These represent technology/infrastructure capabilities that enable
# Application capabilities. All map to the TECH domain.
# =============================================================================
UNIFIED_TECHNICAL_CAPABILITIES = {
    "TECH": [
        {"code": "TCH-CLD", "name": "Cloud Infrastructure Services", "category": "core", "children": [
            {"code": "TCH-CLD-IAS", "name": "Infrastructure as a Service (IaaS)", "children": [
                {"code": "TCH-CLD-IAS-VM", "name": "Virtual Machine Provisioning"},
                {"code": "TCH-CLD-IAS-ASC", "name": "Auto-Scaling"},
                {"code": "TCH-CLD-IAS-BM", "name": "Bare Metal Services"},
            ]},
            {"code": "TCH-CLD-PAS", "name": "Platform as a Service (PaaS)", "children": [
                {"code": "TCH-CLD-PAS-RNT", "name": "Application Runtime"},
                {"code": "TCH-CLD-PAS-DBS", "name": "Database as a Service"},
                {"code": "TCH-CLD-PAS-SLS", "name": "Serverless Functions"},
            ]},
            {"code": "TCH-CLD-MULT", "name": "Multi-Cloud Management", "children": [
                {"code": "TCH-CLD-MULT-CST", "name": "Cloud Cost Management"},
                {"code": "TCH-CLD-MULT-GOV", "name": "Policy Governance"},
                {"code": "TCH-CLD-MULT-PORT", "name": "Workload Portability"},
            ]},
            {"code": "TCH-CLD-CONT", "name": "Container Orchestration (Kubernetes)", "children": [
                {"code": "TCH-CLD-CONT-REG", "name": "Container Registry"},
                {"code": "TCH-CLD-CONT-CLU", "name": "Cluster Lifecycle Management"},
                {"code": "TCH-CLD-CONT-MSH", "name": "Service Mesh"},
            ]},
        ]},
        {"code": "TCH-NET", "name": "Network & Connectivity Services", "category": "core", "children": [
            {"code": "TCH-NET-LAN", "name": "LAN / WAN Management", "children": [
                {"code": "TCH-NET-LAN-SW", "name": "Switch & Router Management"},
                {"code": "TCH-NET-LAN-FW", "name": "Firewall Policy"},
                {"code": "TCH-NET-LAN-QOS", "name": "QoS Management"},
            ]},
            {"code": "TCH-NET-SDN", "name": "Software Defined Networking", "children": [
                {"code": "TCH-NET-SDN-VRT", "name": "Network Virtualisation"},
                {"code": "TCH-NET-SDN-RTE", "name": "Policy-Driven Routing"},
                {"code": "TCH-NET-SDN-AUT", "name": "Network Automation"},
            ]},
            {"code": "TCH-NET-CDN", "name": "Content Delivery Network", "children": [
                {"code": "TCH-NET-CDN-CAC", "name": "Cache Node Management"},
                {"code": "TCH-NET-CDN-TRF", "name": "Traffic Distribution"},
                {"code": "TCH-NET-CDN-ANA", "name": "Performance Analytics"},
            ]},
            {"code": "TCH-NET-VPN", "name": "VPN & Secure Access", "children": [
                {"code": "TCH-NET-VPN-GW", "name": "VPN Gateway Management"},
                {"code": "TCH-NET-VPN-ZTA", "name": "Zero-Trust Network Access"},
                {"code": "TCH-NET-VPN-CRT", "name": "Certificate Management"},
            ]},
        ]},
        {"code": "TCH-SEC", "name": "Cybersecurity Services", "category": "core", "children": [
            {"code": "TCH-SEC-IAM", "name": "Identity & Access Management (IAM)", "children": [
                {"code": "TCH-SEC-IAM-DIR", "name": "Directory Services"},
                {"code": "TCH-SEC-IAM-SSO", "name": "SSO & Federation"},
                {"code": "TCH-SEC-IAM-ENT", "name": "Role & Entitlement Management"},
            ]},
            {"code": "TCH-SEC-SIEM", "name": "SIEM & Threat Detection", "children": [
                {"code": "TCH-SEC-SIEM-LOG", "name": "Log Collection & Correlation"},
                {"code": "TCH-SEC-SIEM-ALT", "name": "Alert Management"},
                {"code": "TCH-SEC-SIEM-INV", "name": "Threat Investigation"},
            ]},
            {"code": "TCH-SEC-ENDP", "name": "Endpoint Protection", "children": [
                {"code": "TCH-SEC-ENDP-EDR", "name": "Antivirus & EDR"},
                {"code": "TCH-SEC-ENDP-PAT", "name": "Patch Compliance"},
                {"code": "TCH-SEC-ENDP-CTL", "name": "Device Control"},
            ]},
            {"code": "TCH-SEC-ZERO", "name": "Zero Trust Architecture", "children": [
                {"code": "TCH-SEC-ZERO-SEG", "name": "Micro-Segmentation"},
                {"code": "TCH-SEC-ZERO-AUT", "name": "Continuous Authentication"},
                {"code": "TCH-SEC-ZERO-SDP", "name": "Software-Defined Perimeter"},
            ]},
        ]},
        {"code": "TCH-DATA", "name": "Data & Storage Services", "category": "core", "children": [
            {"code": "TCH-DATA-DB", "name": "Database Management Services", "children": [
                {"code": "TCH-DATA-DB-RDB", "name": "RDBMS Management"},
                {"code": "TCH-DATA-DB-NOS", "name": "NoSQL Services"},
                {"code": "TCH-DATA-DB-CAC", "name": "In-Memory Caching"},
            ]},
            {"code": "TCH-DATA-DWH", "name": "Data Warehouse Infrastructure", "children": [
                {"code": "TCH-DATA-DWH-PRV", "name": "Data Warehouse Provisioning"},
                {"code": "TCH-DATA-DWH-OPT", "name": "Query Optimisation"},
                {"code": "TCH-DATA-DWH-CAP", "name": "Capacity Management"},
            ]},
            {"code": "TCH-DATA-LAKE", "name": "Data Lake & Object Storage", "children": [
                {"code": "TCH-DATA-LAKE-STR", "name": "Object Store Management"},
                {"code": "TCH-DATA-LAKE-CAT", "name": "Data Cataloguing"},
                {"code": "TCH-DATA-LAKE-LCY", "name": "Data Lifecycle Policy"},
            ]},
            {"code": "TCH-DATA-BCK", "name": "Backup & Disaster Recovery", "children": [
                {"code": "TCH-DATA-BCK-POL", "name": "Backup Policy"},
                {"code": "TCH-DATA-BCK-SNP", "name": "Snapshot Management"},
                {"code": "TCH-DATA-BCK-TST", "name": "Recovery Testing"},
            ]},
        ]},
        {"code": "TCH-INTG", "name": "Integration & Middleware Services", "category": "core", "children": [
            {"code": "TCH-INTG-API", "name": "API Gateway & Management", "children": [
                {"code": "TCH-INTG-API-DES", "name": "API Design & Publishing"},
                {"code": "TCH-INTG-API-LMT", "name": "API Rate Limiting"},
                {"code": "TCH-INTG-API-ANA", "name": "API Analytics"},
            ]},
            {"code": "TCH-INTG-MSG", "name": "Message Broker & Event Streaming", "children": [
                {"code": "TCH-INTG-MSG-TOP", "name": "Topic & Queue Management"},
                {"code": "TCH-INTG-MSG-DLQ", "name": "Dead-Letter Handling"},
                {"code": "TCH-INTG-MSG-RPL", "name": "Message Replay"},
            ]},
            {"code": "TCH-INTG-ESB", "name": "Enterprise Service Bus", "children": [
                {"code": "TCH-INTG-ESB-MED", "name": "Service Mediation"},
                {"code": "TCH-INTG-ESB-TRN", "name": "Protocol Transformation"},
                {"code": "TCH-INTG-ESB-ERR", "name": "Error Handling"},
            ]},
            {"code": "TCH-INTG-IPP", "name": "iPaaS Integration Platform", "children": [
                {"code": "TCH-INTG-IPP-CON", "name": "Connector Library"},
                {"code": "TCH-INTG-IPP-FLW", "name": "Flow Design"},
                {"code": "TCH-INTG-IPP-BAT", "name": "Real-Time & Batch Integration"},
            ]},
        ]},
        {"code": "TCH-OBS", "name": "Observability & Monitoring", "category": "supporting", "children": [
            {"code": "TCH-OBS-LOG", "name": "Log Aggregation & Analysis", "children": [
                {"code": "TCH-OBS-LOG-AGG", "name": "Log Aggregation Pipeline"},
                {"code": "TCH-OBS-LOG-RET", "name": "Log Retention Policy"},
                {"code": "TCH-OBS-LOG-QRY", "name": "Log Search & Query"},
            ]},
            {"code": "TCH-OBS-APM", "name": "Application Performance Monitoring", "children": [
                {"code": "TCH-OBS-APM-TRC", "name": "Distributed Tracing"},
                {"code": "TCH-OBS-APM-TXN", "name": "Transaction Monitoring"},
                {"code": "TCH-OBS-APM-UXM", "name": "User Experience Monitoring"},
            ]},
            {"code": "TCH-OBS-INF", "name": "Infrastructure Monitoring", "children": [
                {"code": "TCH-OBS-INF-MET", "name": "Infrastructure Metrics"},
                {"code": "TCH-OBS-INF-ALT", "name": "Threshold & Alert Management"},
                {"code": "TCH-OBS-INF-CAP", "name": "Capacity Trending"},
            ]},
        ]},
        {"code": "TCH-END", "name": "End User Computing", "category": "supporting", "children": [
            {"code": "TCH-END-VDI", "name": "Virtual Desktop Infrastructure", "children": [
                {"code": "TCH-END-VDI-IMG", "name": "Desktop Image Management"},
                {"code": "TCH-END-VDI-BRK", "name": "Session Broker"},
                {"code": "TCH-END-VDI-PRF", "name": "User Profile Management"},
            ]},
            {"code": "TCH-END-MDM", "name": "Mobile Device Management", "children": [
                {"code": "TCH-END-MDM-ENR", "name": "Device Enrolment"},
                {"code": "TCH-END-MDM-POL", "name": "Mobile Policy Management"},
                {"code": "TCH-END-MDM-APP", "name": "App Distribution"},
            ]},
            {"code": "TCH-END-WRK", "name": "Digital Workplace Services", "children": [
                {"code": "TCH-END-WRK-UEM", "name": "Unified Endpoint Management"},
                {"code": "TCH-END-WRK-POR", "name": "Digital Workplace Portal"},
                {"code": "TCH-END-WRK-SUP", "name": "Employee Tech Support"},
            ]},
        ]},
    ],
}

# =============================================================================
# UNIFIED MANUFACTURING CAPABILITIES — ArchiMate 3.2 Business Layer specialization
# Operational capabilities specific to manufacturing industries.
# Mapped to the OPER domain.
# =============================================================================
UNIFIED_MANUFACTURING_CAPABILITIES = {
    "OPER": [
        {"code": "MFG-PLT", "name": "Plant Operations Management", "category": "core", "children": [
            {"code": "MFG-PLT-PRD", "name": "Production Planning & Scheduling", "children": [
                {"code": "MFG-PLT-PRD-MPS", "name": "Master Production Schedule"},
                {"code": "MFG-PLT-PRD-ORD", "name": "Production Order Release"},
                {"code": "MFG-PLT-PRD-CAP", "name": "Capacity Levelling"},
            ]},
            {"code": "MFG-PLT-OEE", "name": "Overall Equipment Effectiveness (OEE)", "children": [
                {"code": "MFG-PLT-OEE-AVL", "name": "Availability Tracking"},
                {"code": "MFG-PLT-OEE-PER", "name": "Performance Rate Analysis"},
                {"code": "MFG-PLT-OEE-QLT", "name": "Quality Loss Measurement"},
            ]},
            {"code": "MFG-PLT-CAP", "name": "Capacity Management", "children": [
                {"code": "MFG-PLT-CAP-RCC", "name": "Rough-Cut Capacity Planning"},
                {"code": "MFG-PLT-CAP-DET", "name": "Detailed Capacity Scheduling"},
                {"code": "MFG-PLT-CAP-BTN", "name": "Bottleneck Management"},
            ]},
        ]},
        {"code": "MFG-MES", "name": "Manufacturing Execution", "category": "core", "children": [
            {"code": "MFG-MES-SFC", "name": "Shop Floor Control", "children": [
                {"code": "MFG-MES-SFC-DSP", "name": "Work Order Dispatch"},
                {"code": "MFG-MES-SFC-OPR", "name": "Operation Tracking"},
                {"code": "MFG-MES-SFC-DSP2", "name": "Real-Time Status Display"},
            ]},
            {"code": "MFG-MES-WIP", "name": "Work-in-Progress Tracking", "children": [
                {"code": "MFG-MES-WIP-JOB", "name": "Job Tracking"},
                {"code": "MFG-MES-WIP-TRC", "name": "Material Traceability"},
                {"code": "MFG-MES-WIP-INV", "name": "WIP Inventory Management"},
            ]},
            {"code": "MFG-MES-LAB", "name": "Labour & Resource Management", "children": [
                {"code": "MFG-MES-LAB-TIM", "name": "Labour Time Capture"},
                {"code": "MFG-MES-LAB-SKL", "name": "Skills Matrix Enforcement"},
                {"code": "MFG-MES-LAB-CST", "name": "Labour Cost Reporting"},
            ]},
        ]},
        {"code": "MFG-QUAL", "name": "Manufacturing Quality Management", "category": "core", "children": [
            {"code": "MFG-QUAL-SPC", "name": "Statistical Process Control", "children": [
                {"code": "MFG-QUAL-SPC-CHT", "name": "Control Chart Management"},
                {"code": "MFG-QUAL-SPC-ANA", "name": "Statistical Analysis"},
                {"code": "MFG-QUAL-SPC-CPK", "name": "Process Capability Reporting"},
            ]},
            {"code": "MFG-QUAL-INSP", "name": "Inspection & Testing", "children": [
                {"code": "MFG-QUAL-INSP-PLN", "name": "Inspection Plan Execution"},
                {"code": "MFG-QUAL-INSP-CAP", "name": "Measurement Data Capture"},
                {"code": "MFG-QUAL-INSP-DEC", "name": "Pass / Fail Decision"},
            ]},
            {"code": "MFG-QUAL-NCR", "name": "Non-Conformance Management", "children": [
                {"code": "MFG-QUAL-NCR-LOG", "name": "Non-Conformance Logging"},
                {"code": "MFG-QUAL-NCR-QUA", "name": "Quarantine Management"},
                {"code": "MFG-QUAL-NCR-DIS", "name": "Disposition Decision"},
            ]},
        ]},
        {"code": "MFG-ASSET", "name": "Asset & Equipment Management", "category": "supporting", "children": [
            {"code": "MFG-ASSET-PRED", "name": "Predictive Maintenance", "children": [
                {"code": "MFG-ASSET-PRED-SEN", "name": "Sensor Data Collection"},
                {"code": "MFG-ASSET-PRED-MOD", "name": "Failure Prediction Model"},
                {"code": "MFG-ASSET-PRED-ALT", "name": "Maintenance Alert"},
            ]},
            {"code": "MFG-ASSET-CMMS", "name": "Computerised Maintenance Management", "children": [
                {"code": "MFG-ASSET-CMMS-WO", "name": "Work Order Management"},
                {"code": "MFG-ASSET-CMMS-SPR", "name": "Spare Parts Management"},
                {"code": "MFG-ASSET-CMMS-SCH", "name": "Maintenance Scheduling"},
            ]},
            {"code": "MFG-ASSET-EAM", "name": "Enterprise Asset Management", "children": [
                {"code": "MFG-ASSET-EAM-REG", "name": "Asset Register"},
                {"code": "MFG-ASSET-EAM-LCC", "name": "Life Cycle Cost Management"},
                {"code": "MFG-ASSET-EAM-REL", "name": "Reliability Analysis"},
            ]},
        ]},
        {"code": "MFG-SCE", "name": "Supply Chain Execution", "category": "core", "children": [
            {"code": "MFG-SCE-PROC", "name": "Direct Procurement & MRP", "children": [
                {"code": "MFG-SCE-PROC-MRP", "name": "MRP Run Management"},
                {"code": "MFG-SCE-PROC-PO", "name": "Purchase Requisition to PO"},
                {"code": "MFG-SCE-PROC-GR", "name": "Goods Receipt"},
            ]},
            {"code": "MFG-SCE-WHS", "name": "Warehouse & Yard Management", "children": [
                {"code": "MFG-SCE-WHS-RM", "name": "Raw Material Storage"},
                {"code": "MFG-SCE-WHS-FG", "name": "Finished Goods Warehouse"},
                {"code": "MFG-SCE-WHS-YRD", "name": "Yard Management"},
            ]},
            {"code": "MFG-SCE-DIST", "name": "Distribution & Outbound Logistics", "children": [
                {"code": "MFG-SCE-DIST-ORD", "name": "Outbound Order Management"},
                {"code": "MFG-SCE-DIST-RTE", "name": "Route Optimisation"},
                {"code": "MFG-SCE-DIST-POD", "name": "Proof of Delivery"},
            ]},
        ]},
        {"code": "MFG-IOT", "name": "Industrial IoT & Smart Manufacturing", "category": "differentiating", "children": [
            {"code": "MFG-IOT-SCAD", "name": "SCADA & Control Systems", "children": [
                {"code": "MFG-IOT-SCAD-PLC", "name": "PLC & DCS Integration"},
                {"code": "MFG-IOT-SCAD-ALM", "name": "Alarm Management"},
                {"code": "MFG-IOT-SCAD-HIS", "name": "Historical Data Collection"},
            ]},
            {"code": "MFG-IOT-SENS", "name": "Sensor Data Management", "children": [
                {"code": "MFG-IOT-SENS-NET", "name": "Sensor Network Management"},
                {"code": "MFG-IOT-SENS-ING", "name": "Data Ingestion Pipeline"},
                {"code": "MFG-IOT-SENS-EDG", "name": "Edge Processing"},
            ]},
            {"code": "MFG-IOT-DGTW", "name": "Digital Twin", "children": [
                {"code": "MFG-IOT-DGTW-MOD", "name": "Digital Twin Modelling"},
                {"code": "MFG-IOT-DGTW-SIM", "name": "Real-Time Simulation"},
                {"code": "MFG-IOT-DGTW-WIF", "name": "What-If Analysis"},
            ]},
        ]},
    ],
}


@click.group("seed-capabilities")
def seed_capabilities_cli():
    """Seed the reference capability taxonomy (business, technical, unified, APQC).

    CM-01: this group used to register as `flask seed-caps`, which did not
    match the `flask seed-capabilities` command CLAUDE.md documents, so nobody
    running the documented command found it — and the capability domain
    (/capability-map, /api/apqc/tree) stayed empty on every fresh install.
    Renamed to match the documented command; add --dry-run for a preview.
    """
    pass


@seed_capabilities_cli.command("all")
@click.option("--dry-run", is_flag=True, help="Report what would be seeded without writing to the database.")
@with_appcontext
def seed_all_capabilities(dry_run):
    """Seed all capability types: business, technical, unified, and APQC."""
    if dry_run:
        _print_dry_run_summary()
        return {"dry_run": True}

    click.echo("[*] Seeding ALL capabilities...")

    results = {
        "business": seed_business_caps(),
        "technical": seed_technical_caps(),
        "unified": seed_unified_caps(),
        "apqc": seed_apqc_processes(),
    }

    total_created = sum(r.get("created", 0) for r in results.values())
    click.echo(f"\n[OK] Total capabilities seeded: {total_created}")
    return results


def _print_dry_run_summary():
    """Report row counts that WOULD be created, without writing anything."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.technical_capability import TechnicalCapability
    from app.models.apqc_process import APQCProcess

    def _count_business(items, count=0):
        for item in items:
            count += 1
            count = _count_business(item.get("children", []), count)
        return count

    business_total = _count_business(BUSINESS_CAPABILITIES)
    business_existing = BusinessCapability.query.count()

    technical_total = sum(len(d["capabilities"]) for d in TECHNICAL_CAPABILITIES)
    technical_existing = TechnicalCapability.query.count()

    apqc_total = len(APQC_PROCESS_SEED_DATA)
    apqc_existing = APQCProcess.query.count()

    click.echo("[DRY RUN] Nothing was written.")
    click.echo(f"  Business capabilities:  {business_existing} already in DB, up to {business_total} in seed set")
    click.echo(f"  Technical capabilities: {technical_existing} already in DB, up to {technical_total} in seed set")
    click.echo(f"  APQC reference processes: {apqc_existing} already in DB, up to {apqc_total} in seed set")


@seed_capabilities_cli.command("business")
@with_appcontext
def seed_business_capabilities():
    """Seed business capability data."""
    click.echo("[B] Seeding business capabilities...")
    result = seed_business_caps()
    click.echo(
        f"[OK] Business capabilities: {result['created']} created, {result['skipped']} skipped"
    )
    return result


@seed_capabilities_cli.command("technical")
@with_appcontext
def seed_technical_capabilities():
    """Seed technical capability data (ACM domains)."""
    click.echo("[T] Seeding technical capabilities...")
    result = seed_technical_caps()
    click.echo(
        f"[OK] Technical capabilities: {result['created']} created, {result['skipped']} skipped"
    )
    return result


@seed_capabilities_cli.command("unified")
@with_appcontext
def seed_unified_capabilities():
    """Seed unified capability data."""
    click.echo("[U] Seeding unified capabilities...")
    result = seed_unified_caps()
    click.echo(
        f"[OK] Unified capabilities: {result['created']} created, {result['skipped']} skipped"
    )
    return result


@seed_capabilities_cli.command("apqc")
@with_appcontext
def seed_apqc_capabilities():
    """Seed the APQC Process Classification Framework reference taxonomy.

    CM-01: /api/apqc/tree reads from the APQCProcess model
    (app.models.apqc_process). A seed for a *different* model
    (ApqcProcessHierarchy, app/seeds/apqc_process_hierarchy_seed.py) existed
    but was never wired to any command and, even if run, would not have
    populated the table this endpoint actually queries. This seeds
    APQCProcess directly.
    """
    click.echo("[A] Seeding APQC reference processes...")
    result = seed_apqc_processes()
    click.echo(
        f"[OK] APQC processes: {result['created']} created, {result['skipped']} skipped"
    )
    return result


def seed_business_caps():
    """Seed business capabilities with hierarchy."""
    from app.models.business_capabilities import BusinessCapability

    created = 0
    skipped = 0

    # Find highest existing CAP-NNN numeric code to avoid unique constraint violations
    # Exclude non-numeric codes like CAP-ABACUS-* from imported data
    import re
    all_codes = db.session.query(BusinessCapability.code).filter(
        BusinessCapability.code.like('CAP-%')
    ).all()
    max_num = 0
    for (code,) in all_codes:
        m = re.match(r'^CAP-(\d+)$', code)
        if m:
            max_num = max(max_num, int(m.group(1)))
    code_counter = [max_num + 1]

    def generate_code(level, parent_code=None):
        """Generate unique capability code."""
        code = f"CAP-{code_counter[0]:03d}"
        code_counter[0] += 1
        return code

    def create_capability(data, parent_id=None, parent_category=None):
        nonlocal created, skipped

        # Check if exists
        existing = BusinessCapability.query.filter_by(name=data["name"]).first()
        if existing:
            skipped += 1
            cap_id = existing.id
        else:
            code = generate_code(data.get("level", 1))
            category = parent_category or data["name"]  # L0 name becomes category

            cap = BusinessCapability(
                name=data["name"],
                description=data.get("description", ""),
                code=code,
                level=data.get("level", 1) + 1,  # Level 0 becomes 1, etc.
                category=category,
                business_domain=category,
                parent_capability_id=parent_id,
                strategic_importance="high" if data.get("level", 1) == 0 else "medium",
                business_value=8 if data.get("level", 1) == 0 else 6,
                discovery_source="seeded",
            )
            db.session.add(cap)
            db.session.flush()
            cap_id = cap.id
            created += 1
            click.echo(f"  + [{code}] {data['name']}")

        # Create children with parent's name as category
        for child in data.get("children", []):
            create_capability(child, cap_id, data["name"])

    try:
        for cap_data in BUSINESS_CAPABILITIES:
            create_capability(cap_data)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        click.echo(f"[ERR] Error: {e}")
        raise

    return {"created": created, "skipped": skipped}


def seed_technical_caps():
    """Seed technical capabilities by ACM domain."""
    from app.models.technical_capability import TechnicalCapability

    created = 0
    skipped = 0

    try:
        for domain_data in TECHNICAL_CAPABILITIES:
            domain_name = domain_data["domain"]
            domain_code = domain_data["code"]

            for cap_data in domain_data["capabilities"]:
                # Check if exists
                existing = TechnicalCapability.query.filter_by(code=cap_data["code"]).first()
                if existing:
                    skipped += 1
                    continue

                cap = TechnicalCapability(
                    name=cap_data["name"],
                    code=cap_data["code"],
                    description=cap_data.get("description", ""),
                    acm_domain=domain_name,
                    level="L1",
                    level_number=1,
                    specialization_type="TECHNICAL",
                    capability_type="functional",
                    industry_maturity="mature",
                    complexity="medium",
                    created_at=datetime.utcnow(),
                )
                db.session.add(cap)
                created += 1
                click.echo(f"  + [{domain_code}] {cap_data['name']}")

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        click.echo(f"[ERR] Error: {e}")
        raise

    return {"created": created, "skipped": skipped}


def seed_unified_caps():
    """Seed unified capabilities with proper domain-aligned hierarchy."""

    from app.models.unified_capability import BusinessDomain, UnifiedCapability

    created = 0
    skipped = 0
    retired = 0
    domains_created = 0

    try:
        # Step 1: Mark existing UC-* capabilities as retiring (preserve FK refs)
        old_caps = UnifiedCapability.query.filter(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
            UnifiedCapability.code.like('UC-%')
        ).all()
        for cap in old_caps:
            if cap.status != "retiring":
                cap.status = "retiring"
                retired += 1
        if retired:
            db.session.flush()
            click.echo(f"  [RETIRE] Marked {retired} legacy UC-* capabilities as retiring")

        # Step 2: Create/update the 9 standard business domains
        domain_map = {}  # code -> id
        for dom_data in UNIFIED_CAPABILITY_DOMAINS:
            existing = BusinessDomain.query.filter_by(code=dom_data["code"]).first()
            if existing:
                domain_map[dom_data["code"]] = existing.id
            else:
                domain = BusinessDomain(
                    code=dom_data["code"],
                    name=dom_data["name"],
                    description=dom_data.get("description", ""),
                    domain_type=dom_data.get("domain_type", "primary"),
                    strategic_focus=dom_data.get("strategic_focus", ""),
                    domain_owner=dom_data.get("domain_owner", ""),
                )
                db.session.add(domain)
                db.session.flush()
                domain_map[dom_data["code"]] = domain.id
                domains_created += 1
                click.echo(f"  + Domain [{dom_data['code']}] {dom_data['name']}")

        def _seed_cap_set(cap_dict, spec_type, description_suffix="capabilities"):
            """Seed L1 + L2 + L3 UnifiedCapability records for a specialization_type.

            The existence lookups below are scoped to `organization_id=None`
            deliberately, and it is not defensive noise. This seeder creates
            SHARED REFERENCE capabilities, and UnifiedCapability does not use
            TenantMixin -- its scoping lives in a do_orm_execute listener
            (app/models/unified_capability.py:466) that returns early unless
            has_request_context(). A CLI command has no request, so the query
            is completely unfiltered here.

            Unscoped, the first tenant to own a capability with a given code
            would make this seeder "find" it and skip creating the reference
            row -- silently, and differently depending on which tenants exist.
            That is latent today only because unified_capabilities is empty in
            production; the projection that fills it activates the bug.
            """
            nonlocal created, skipped
            for domain_code, l1_list in cap_dict.items():
                d_id = domain_map.get(domain_code)
                if not d_id:
                    click.echo(f"  [WARN] Domain {domain_code} not found for {spec_type}, skipping")
                    continue

                for l1_data in l1_list:
                    existing_l1 = UnifiedCapability.query.filter_by(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                        code=l1_data["code"], organization_id=None).first()
                    if existing_l1:
                        skipped += 1
                        l1_id = existing_l1.id
                    else:
                        l1 = UnifiedCapability(
                            name=l1_data["name"],
                            code=l1_data["code"],
                            description=f"{l1_data['name']} {description_suffix}",
                            domain_id=d_id,
                            level=1,
                            category=l1_data.get("category", "core"),
                            capability_type=l1_data.get("category", "core"),
                            specialization_type=spec_type,
                            status="defined",
                            strategic_importance="high" if l1_data.get("category") == "core" else "medium",
                        )
                        db.session.add(l1)
                        db.session.flush()
                        l1_id = l1.id
                        created += 1
                        click.echo(f"  + [{spec_type}] [{l1_data['code']}] {l1_data['name']}")

                    for l2_data in l1_data.get("children", []):
                        existing_l2 = UnifiedCapability.query.filter_by(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                        code=l2_data["code"], organization_id=None).first()
                        if existing_l2:
                            skipped += 1
                            l2_id = existing_l2.id
                        else:
                            l2 = UnifiedCapability(
                                name=l2_data["name"],
                                code=l2_data["code"],
                                description=f"{l2_data['name']} — {l1_data['name']}",
                                domain_id=d_id,
                                level=2,
                                parent_capability_id=l1_id,
                                category=l1_data.get("category", "core"),
                                capability_type="operational",
                                specialization_type=spec_type,
                                status="defined",
                            )
                            db.session.add(l2)
                            db.session.flush()
                            l2_id = l2.id
                            created += 1

                        for l3_data in l2_data.get("children", []):
                            existing_l3 = UnifiedCapability.query.filter_by(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                        code=l3_data["code"], organization_id=None).first()
                            if existing_l3:
                                skipped += 1
                                continue
                            l3 = UnifiedCapability(
                                name=l3_data["name"],
                                code=l3_data["code"],
                                description=f"{l3_data['name']} — {l2_data['name']}",
                                domain_id=d_id,
                                level=3,
                                parent_capability_id=l2_id,
                                category=l1_data.get("category", "core"),
                                capability_type="operational",
                                specialization_type=spec_type,
                                status="defined",
                            )
                            db.session.add(l3)
                            created += 1

        # Step 3: Seed BUSINESS capabilities
        click.echo("  [>] Seeding BUSINESS capabilities...")
        _seed_cap_set(UNIFIED_CAPABILITIES, "BUSINESS")

        # Step 4: Seed APPLICATION capabilities (ArchiMate Application Layer)
        click.echo("  [>] Seeding APPLICATION capabilities...")
        _seed_cap_set(APPLICATION_CAPABILITIES, "APPLICATION", "application capabilities")

        # Step 5: Seed TECHNICAL capabilities (ArchiMate Technology Layer)
        click.echo("  [>] Seeding TECHNICAL capabilities...")
        _seed_cap_set(UNIFIED_TECHNICAL_CAPABILITIES, "TECHNICAL", "technology services")

        # Step 6: Seed MANUFACTURING capabilities (operational specialization)
        click.echo("  [>] Seeding MANUFACTURING capabilities...")
        _seed_cap_set(UNIFIED_MANUFACTURING_CAPABILITIES, "MANUFACTURING", "manufacturing capabilities")

        db.session.commit()
        click.echo(f"  [OK] Domains: {domains_created} new, L1+L2: {created} created, {skipped} skipped")
    except Exception as e:
        db.session.rollback()
        click.echo(f"[ERR] Error: {e}")
        raise

    return {"created": created, "skipped": skipped, "retired": retired, "domains_created": domains_created}


# =============================================================================
# APQC PROCESS CLASSIFICATION FRAMEWORK — L1-L3 reference taxonomy
# (code, name, level, parent_code)
# =============================================================================
APQC_PROCESS_SEED_DATA = [
    ("1.0", "Develop and Manage Products and Services", 1, None),
    ("2.0", "Market and Sell Products and Services", 1, None),
    ("3.0", "Deliver Products and Services", 1, None),
    ("4.0", "Manage Customer Service", 1, None),
    ("5.0", "Develop and Manage Human Capital", 1, None),
    ("6.0", "Manage Information Technology", 1, None),
    ("7.0", "Manage Financial Resources", 1, None),
    ("8.0", "Acquire, Construct, and Manage Assets", 1, None),
    ("9.0", "Manage Environmental Health and Safety", 1, None),
    ("10.0", "Manage External Relationships", 1, None),
    ("11.0", "Develop and Manage Business Capabilities", 1, None),
    ("12.0", "Manage Knowledge, Improvement, and Change", 1, None),
    ("1.1", "Manage product and service portfolio", 2, "1.0"),
    ("1.2", "Develop products and services", 2, "1.0"),
    ("1.3", "Deliver products and services", 2, "1.0"),
    ("1.4", "Manage product and service life cycle", 2, "1.0"),
    ("2.1", "Understand markets, customers, and capabilities", 2, "2.0"),
    ("2.2", "Develop marketing strategy", 2, "2.0"),
    ("2.3", "Develop sales strategy", 2, "2.0"),
    ("2.4", "Develop and manage marketing plans", 2, "2.0"),
    ("2.5", "Develop and manage sales plans", 2, "2.0"),
    ("3.1", "Plan and manage supply chain", 2, "3.0"),
    ("3.2", "Procure materials and services", 2, "3.0"),
    ("3.3", "Produce/manufacture/deliver product", 2, "3.0"),
    ("3.4", "Deliver service to customer", 2, "3.0"),
    ("3.5", "Manage logistics and warehousing", 2, "3.0"),
    ("4.1", "Develop customer care/customer service strategy", 2, "4.0"),
    ("4.2", "Plan and manage customer service operations", 2, "4.0"),
    ("4.3", "Measure and evaluate customer service operations", 2, "4.0"),
    ("5.1", "Develop and manage human resources (HR) planning", 2, "5.0"),
    ("5.2", "Recruit, source, and select employees", 2, "5.0"),
    ("5.3", "Develop and counsel employees", 2, "5.0"),
    ("5.4", "Reward and retain employees", 2, "5.0"),
    ("5.5", "Redeploy and retire employees", 2, "5.0"),
    ("5.6", "Manage employee information and analytics", 2, "5.0"),
    ("6.1", "Manage the business of IT", 2, "6.0"),
    ("6.2", "Develop and manage IT customer relationships", 2, "6.0"),
    ("6.3", "Develop and implement security, privacy, and data protection", 2, "6.0"),
    ("6.4", "Manage enterprise information", 2, "6.0"),
    ("6.5", "Develop and maintain IT solutions", 2, "6.0"),
    ("6.6", "Deploy IT solutions", 2, "6.0"),
    ("6.7", "Deliver and support IT services", 2, "6.0"),
    ("7.1", "Plan and manage enterprise finances", 2, "7.0"),
    ("7.2", "Process revenue and receipts", 2, "7.0"),
    ("7.3", "Process payables and expense reimbursements", 2, "7.0"),
    ("7.4", "Manage payroll", 2, "7.0"),
    ("7.5", "Manage treasury and risk", 2, "7.0"),
    ("7.6", "Manage internal controls", 2, "7.0"),
    ("7.7", "Manage taxes", 2, "7.0"),
    ("8.1", "Plan and manage assets", 2, "8.0"),
    ("8.2", "Design and construct/acquire productive assets", 2, "8.0"),
    ("8.3", "Maintain productive assets", 2, "8.0"),
    ("8.4", "Retire and dispose of assets", 2, "8.0"),
    ("9.1", "Formulate EHS strategy and policies", 2, "9.0"),
    ("9.2", "Ensure compliance with regulations", 2, "9.0"),
    ("9.3", "Manage environmental programs", 2, "9.0"),
    ("9.4", "Manage health and safety programs", 2, "9.0"),
    ("10.1", "Build investor relationships", 2, "10.0"),
    ("10.2", "Manage government and industry relationships", 2, "10.0"),
    ("10.3", "Manage relations with board of directors", 2, "10.0"),
    ("10.4", "Manage legal and ethical issues", 2, "10.0"),
    ("10.5", "Manage public relations program", 2, "10.0"),
    ("11.1", "Manage business architecture", 2, "11.0"),
    ("11.2", "Manage portfolio, programmes, and projects", 2, "11.0"),
    ("11.3", "Manage enterprise risk", 2, "11.0"),
    ("11.4", "Manage business process improvement", 2, "11.0"),
    ("12.1", "Create and manage organisational knowledge", 2, "12.0"),
    ("12.2", "Manage improvement initiatives", 2, "12.0"),
    ("12.3", "Implement management of change", 2, "12.0"),
    ("1.1.1", "Define product/service portfolio strategy", 3, "1.1"),
    ("1.1.2", "Conduct competitive analysis for product portfolio", 3, "1.1"),
    ("1.2.1", "Perform product/service research and development", 3, "1.2"),
    ("1.2.2", "Design and prototype product/service", 3, "1.2"),
    ("1.2.3", "Test and evaluate product/service", 3, "1.2"),
    ("6.5.1", "Develop and maintain application software", 3, "6.5"),
    ("6.5.2", "Develop and maintain IT infrastructure", 3, "6.5"),
    ("6.7.1", "Manage IT helpdesk and incident management", 3, "6.7"),
    ("6.7.2", "Manage IT change and release", 3, "6.7"),
    ("7.1.1", "Perform planning and budgeting", 3, "7.1"),
    ("7.1.2", "Perform financial analysis and reporting", 3, "7.1"),
    ("5.2.1", "Define employee sourcing strategy", 3, "5.2"),
    ("5.2.2", "Screen candidates", 3, "5.2"),
    ("5.3.1", "Manage employee performance", 3, "5.3"),
    ("5.3.2", "Develop and train employees", 3, "5.3"),
]


def seed_apqc_processes():
    """Seed the APQC PCF L1-L3 reference taxonomy into APQCProcess.

    Idempotent — skips codes already present. This is what
    /api/apqc/tree and APQCHierarchyService actually read from; it is
    reference-framework data, not anything the customer entered.
    """
    from app.models.apqc_process import APQCProcess

    created = 0
    skipped = 0

    try:
        code_to_id = {
            row.process_code: row.id for row in APQCProcess.query.all()
        }

        # First pass: create missing nodes without parent FK
        for code, name, level, parent_code in APQC_PROCESS_SEED_DATA:
            if code in code_to_id:
                skipped += 1
                continue
            cat1 = code if level == 1 else None
            proc = APQCProcess(
                process_code=code,
                process_name=name,
                category_level_1=cat1,
                process_category="reference",
                process_type="Core",
            )
            db.session.add(proc)
            db.session.flush()
            code_to_id[code] = proc.id
            created += 1
            click.echo(f"  + [{code}] {name}")

        # Second pass: wire parent_process_id now that all codes have ids
        for code, name, level, parent_code in APQC_PROCESS_SEED_DATA:
            if not parent_code:
                continue
            proc = APQCProcess.query.filter_by(process_code=code).first()
            parent_id = code_to_id.get(parent_code)
            if proc and parent_id and proc.parent_process_id is None:
                proc.parent_process_id = parent_id

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        click.echo(f"[ERR] Error: {e}")
        raise

    return {"created": created, "skipped": skipped}


def init_app(app):
    """Register CLI commands with Flask app."""
    app.cli.add_command(seed_capabilities_cli)


def register_capabilities_commands(app):
    """Register capabilities seed commands with Flask app."""
    app.cli.add_command(seed_capabilities_cli)
