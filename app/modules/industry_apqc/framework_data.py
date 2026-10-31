"""The industry process frameworks every organisation starts with.

One shared copy of each framework (``organization_id`` NULL) is readable by
every organisation, so loading is a platform event, not a per-organisation
one: a data migration loads it for the organisations that exist, and the
organisation insert listener loads it when the catalogue is still empty (a new
installation, a test database). ``load_default_frameworks`` is idempotent and
only ever adds what is missing.

The process hierarchy is the APQC Process Classification Framework: the
thirteen categories, their level 2 process groups and the level 3 processes
listed below. Each framework carries the cross-industry hierarchy plus the
processes particular to its industry, flagged as industry specific.
"""

from datetime import date

import sqlalchemy as sa

# (code, name) for the thirteen categories.
CATEGORIES = [
    ("1.0", "Develop Vision and Strategy"),
    ("2.0", "Develop and Manage Products and Services"),
    ("3.0", "Market and Sell Products and Services"),
    ("4.0", "Deliver Physical Products"),
    ("5.0", "Deliver Services"),
    ("6.0", "Manage Customer Service"),
    ("7.0", "Manage Human Capital"),
    ("8.0", "Manage Information Technology"),
    ("9.0", "Manage Financial Resources"),
    ("10.0", "Acquire, Construct, and Manage Assets"),
    ("11.0", "Manage Enterprise Risk, Compliance, Remediation, and Resiliency"),
    ("12.0", "Manage External Relationships"),
    ("13.0", "Develop and Manage Business Capabilities"),
]

PROCESS_GROUPS = [
    ("1.1", "Define the business concept and long-term vision"),
    ("1.2", "Develop business strategy"),
    ("1.3", "Execute and measure strategic initiatives"),
    ("1.4", "Develop and maintain business model"),
    ("2.1", "Govern and manage product and service development program"),
    ("2.2", "Generate and define new product/service concepts"),
    ("2.3", "Develop products and services"),
    ("2.4", "Test market for new or revised products and services"),
    ("2.5", "Prepare for production"),
    ("3.1", "Understand markets, customers, and capabilities"),
    ("3.2", "Develop marketing strategy"),
    ("3.3", "Develop sales strategy"),
    ("3.4", "Develop and manage marketing plans"),
    ("3.5", "Develop and manage sales plans"),
    ("4.1", "Plan for and acquire necessary resources"),
    ("4.2", "Procure materials and services"),
    ("4.3", "Produce/manufacture/deliver product"),
    ("4.4", "Manage logistics and warehousing"),
    ("5.1", "Manage service delivery resources"),
    ("5.2", "Deliver service to customer"),
    ("6.1", "Develop customer care/customer service strategy"),
    ("6.2", "Plan and manage customer service operations"),
    ("6.3", "Measure and evaluate customer service operations"),
    ("7.1", "Develop and manage human resources (HR) planning, policies, and strategies"),
    ("7.2", "Recruit, source, and select employees"),
    ("7.3", "Develop and counsel employees"),
    ("7.4", "Reward and retain employees"),
    ("7.5", "Redeploy and retire employees"),
    ("7.6", "Manage employee information and analytics"),
    ("8.1", "Manage the business of information technology"),
    ("8.2", "Develop and manage IT customer relationships"),
    ("8.3", "Develop and implement security, privacy, and data protection controls"),
    ("8.4", "Manage enterprise information"),
    ("8.5", "Develop and maintain information technology solutions"),
    ("8.6", "Deploy information technology solutions"),
    ("8.7", "Deliver and support information technology services"),
    ("9.1", "Perform planning and management accounting"),
    ("9.2", "Perform revenue accounting"),
    ("9.3", "Perform general accounting and reporting"),
    ("9.4", "Manage fixed-asset project accounting"),
    ("9.5", "Process payroll"),
    ("9.6", "Process accounts payable and expense reimbursements"),
    ("9.7", "Manage treasury operations"),
    ("9.8", "Manage internal controls"),
    ("9.9", "Manage taxes"),
    ("10.1", "Plan and acquire assets"),
    ("10.2", "Design and construct productive assets"),
    ("10.3", "Maintain productive assets"),
    ("10.4", "Dispose of assets"),
    ("11.1", "Manage enterprise risk"),
    ("11.2", "Manage compliance"),
    ("11.3", "Manage remediation efforts"),
    ("11.4", "Manage business resiliency"),
    ("12.1", "Build investor relationships"),
    ("12.2", "Manage government and industry relationships"),
    ("12.3", "Manage relations with board of directors"),
    ("12.4", "Manage legal and ethical issues"),
    ("12.5", "Manage public relations program"),
    ("13.1", "Manage business processes"),
    ("13.2", "Manage portfolio, program, and project"),
    ("13.3", "Manage enterprise quality"),
    ("13.4", "Manage change"),
    ("13.5", "Develop and manage enterprise-wide knowledge management capability"),
    ("13.6", "Measure and benchmark"),
]

PROCESSES = [
    ("1.1.1", "Assess the external environment"),
    ("1.1.2", "Survey market and determine customer needs and wants"),
    ("1.1.3", "Conduct internal analysis"),
    ("1.1.4", "Establish strategic vision"),
    ("1.2.1", "Develop overall mission statement"),
    ("1.2.2", "Define strategic options"),
    ("1.2.3", "Evaluate strategic options"),
    ("1.2.4", "Select long-term business strategy"),
    ("1.2.5", "Coordinate and align functional and process strategies"),
    ("1.2.6", "Create organizational design"),
    ("1.2.7", "Develop and set organizational goals"),
    ("1.3.1", "Develop strategic initiatives"),
    ("1.3.2", "Evaluate strategic initiatives"),
    ("1.3.3", "Select strategic initiatives"),
    ("1.3.4", "Establish high-level measures"),
    ("7.2.1", "Develop recruiting strategy and plans"),
    ("7.2.2", "Source candidates"),
    ("7.2.3", "Screen and select candidates"),
    ("7.2.4", "Hire and onboard employees"),
    ("9.5.1", "Report time"),
    ("9.5.2", "Process payroll and deductions"),
    ("9.5.3", "Pay expatriates"),
]

# Industry specific processes: (framework code, name, category the process
# belongs to). They are numbered inside the framework, never inside the PCF.
INDUSTRY_PROCESSES = {
    "MFG": [
        ("Shop floor management", "4.0"),
        ("Material requirements planning (MRP)", "4.0"),
        ("Production execution and control", "4.0"),
        ("Lean/Six Sigma process improvement", "13.0"),
    ],
    "BFS": [
        ("Credit risk modeling", "11.0"),
        ("Treasury and liquidity management", "9.0"),
        ("Regulatory reporting (CCAR, DFAST)", "11.0"),
        ("Fraud detection and prevention", "11.0"),
    ],
    "HCP": [
        ("Patient registration and scheduling", "5.0"),
        ("Clinical decision support", "5.0"),
        ("Medical records and health information management", "5.0"),
        ("Utilization management and case management", "5.0"),
    ],
    "PHA": [
        ("Clinical trial design and execution", "2.0"),
        ("Regulatory submission management", "11.0"),
        ("Batch record management", "4.0"),
        ("Adverse event reporting", "11.0"),
    ],
    "RET": [
        ("Assortment planning", "3.0"),
        ("Price and promotion management", "3.0"),
        ("Store layout and visual merchandising", "3.0"),
        ("Last-mile delivery and fulfillment", "4.0"),
    ],
    "CRS": [],
}

FRAMEWORKS = [
    {
        "industry_code": "CRS",
        "industry_name": "Cross-Industry",
        "description": "The baseline Process Classification Framework, applicable to every industry.",
        "pcf_version": "8.0.0",
        "regulatory_intensity": "low",
        "digital_maturity_typical": 3,
        "process_complexity": "moderate",
    },
    {
        "industry_code": "MFG",
        "industry_name": "Manufacturing",
        "industry_subcategory": "General Manufacturing",
        "description": "Processes for manufacturing operations, including discrete, process and hybrid manufacturing.",
        "pcf_version": "7.3.0",
        "regulatory_intensity": "medium",
        "digital_maturity_typical": 3,
        "process_complexity": "complex",
    },
    {
        "industry_code": "BFS",
        "industry_name": "Banking & Financial Services",
        "description": "Processes for retail banking, commercial banking and financial services.",
        "pcf_version": "7.2.0",
        "regulatory_intensity": "very_high",
        "digital_maturity_typical": 4,
        "process_complexity": "highly_complex",
    },
    {
        "industry_code": "HCP",
        "industry_name": "Healthcare Provider",
        "description": "Processes for hospitals, clinics and healthcare delivery organisations.",
        "pcf_version": "7.1.0",
        "regulatory_intensity": "very_high",
        "digital_maturity_typical": 3,
        "process_complexity": "highly_complex",
    },
    {
        "industry_code": "PHA",
        "industry_name": "Pharmaceutical & Life Sciences",
        "description": "Processes for drug development, manufacturing and commercialisation.",
        "pcf_version": "7.0.0",
        "regulatory_intensity": "very_high",
        "digital_maturity_typical": 4,
        "process_complexity": "highly_complex",
    },
    {
        "industry_code": "RET",
        "industry_name": "Retail",
        "description": "Processes for retail operations, merchandising and customer experience.",
        "pcf_version": "7.2.0",
        "regulatory_intensity": "low",
        "digital_maturity_typical": 4,
        "process_complexity": "moderate",
    },
]


def process_level(code):
    """PCF level from the ID: 1.0 is level 1, 1.1 level 2, 1.1.1 level 3."""
    parts = code.split(".")
    if len(parts) == 2 and parts[1] == "0":
        return 1
    return len(parts)


def _hierarchy_rows():
    """(code, name, category_1, category_2, category_3) for the shared hierarchy."""
    names = dict(CATEGORIES)
    names.update(PROCESS_GROUPS)
    label = {code: "%s %s" % (code, name) for code, name in names.items()}
    rows = []
    for code, name in CATEGORIES:
        rows.append((code, name, label[code], None, None))
    for code, name in PROCESS_GROUPS:
        rows.append((code, name, label[code.split(".")[0] + ".0"], label[code], None))
    for code, name in PROCESSES:
        group = ".".join(code.split(".")[:2])
        rows.append((code, name, label[code.split(".")[0] + ".0"], label[group],
                     "%s %s" % (code, name)))
    return rows, label


def load_default_frameworks(connection):
    """Add every default framework and its processes that is not there yet.

    Works on a plain connection so the migration and the organisation insert
    listener share it. Returns the number of frameworks added.
    """
    meta = sa.MetaData()
    frameworks = sa.Table("industry_apqc_framework", meta, autoload_with=connection)
    processes = sa.Table("industry_apqc_process", meta, autoload_with=connection)
    have = {
        row[0]
        for row in connection.execute(sa.select(frameworks.c.industry_code))
    }
    rows, label = _hierarchy_rows()
    added = 0
    newly_added_codes = []
    for fw in FRAMEWORKS:
        if fw["industry_code"] in have:
            continue
        newly_added_codes.append(fw["industry_code"])
        values = dict(fw)
        values.update(
            release_date=date(2024, 1, 1), is_active=True,
            key_differentiators=[], unique_process_areas=[],
        )
        framework_id = connection.execute(
            sa.insert(frameworks).values(**values).returning(frameworks.c.id)
        ).scalar_one()
        batch = []
        for code, name, cat1, cat2, cat3 in rows:
            batch.append(dict(
                industry_framework_id=framework_id, industry_process_code=code,
                industry_process_name=name, industry_category_1=cat1,
                industry_category_2=cat2, industry_category_3=cat3,
                is_industry_unique=False, is_modified_from_base=False, is_active=True,
            ))
        for number, (name, category) in enumerate(INDUSTRY_PROCESSES[fw["industry_code"]], 1):
            batch.append(dict(
                industry_framework_id=framework_id,
                industry_process_code="%s-%d" % (fw["industry_code"], number),
                industry_process_name=name, industry_category_1=label[category],
                industry_category_2=None, industry_category_3=None,
                is_industry_unique=True, is_modified_from_base=False, is_active=True,
            ))
        connection.execute(sa.insert(processes), batch)
        added += 1
    return added, newly_added_codes



