"""The whole structure of the standards a company can choose at sign-up, as rows of the one framework catalogue.

Every title is a short paraphrase in our own words; the published text of these standards is not reproduced.
Each row keeps the official identifier of the control, criterion, article or subcategory it stands for, and names it
in official_reference. No row carries a priority: nobody has rated these, so none is shown.
The GDPR list is the articles that place a duty on a controller or processor, not every article of the regulation.
The published counts they must reach are in app/seed_data/standard_control_totals.py.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

_Row = Tuple[str, str]  # identifier, short paraphrase

_ISO_THEMES = (
    ("A.5", "Organizational controls", [
        "Setting out security policies", "Who is responsible for which security tasks", "Keeping conflicting duties apart",
        "What managers must do for security", "Staying in touch with authorities", "Links with security forums and peer groups",
        "Gathering and using threat information", "Security in project work", "Inventory of information and assets",
        "Acceptable use of assets", "Getting assets back when people leave", "Classifying information", "Marking information with its handling level",
        "Transferring information", "Controlling access", "Managing identities", "Looking after the secrets used to sign in",
        "Granting, changing and removing access", "Security in supplier relationships", "Security in supplier agreements",
        "Security in the ICT supply chain", "Monitoring supplier services", "Security of cloud services",
        "Planning incident management", "Assessing security events", "Responding to incidents",
        "Learning from incidents", "Collecting evidence", "Security during disruption",
        "ICT readiness for continuity", "Legal and contractual requirements", "Intellectual property",
        "Protecting records", "Privacy and personal data", "Independent review of security",
        "Compliance with policies and standards", "Written procedures for operations",
    ]),
    ("A.6", "People controls", [
        "Screening before hiring", "Terms of employment", "Awareness and training", "Handling breaches of security rules",
        "Responsibilities after leaving or changing role", "Confidentiality agreements", "Security when working away from the office",
        "Reporting security events",
    ]),
    ("A.7", "Physical controls", [
        "Boundaries around secure premises", "Controlling who enters premises", "Securing offices and rooms",
        "Monitoring premises", "Protection from physical threats", "Rules for work inside secure areas",
        "Keeping desks and screens clear", "Siting and protecting equipment", "Equipment off premises",
        "Handling removable and stored media", "Power and other utilities equipment relies on", "Protecting power and network cables", "Keeping equipment properly maintained",
        "Safely disposing of or reusing equipment",
    ]),
    ("A.8", "Technological controls", [
        "Laptops, phones and other user devices", "Tight control of administrator-level access", "Restricting access to information",
        "Who can read and change source code", "Safe ways of proving identity", "Planning for the capacity needed", "Defences against malicious software",
        "Managing technical vulnerabilities", "Keeping secure settings under control", "Deleting information",
        "Masking data", "Preventing data leakage", "Backing up information", "Redundant facilities",
        "Recording events in logs", "Watching systems for unusual behaviour", "Keeping system clocks in step", "Powerful system tools",
        "Installing software on live systems", "Securing the network itself", "Security terms for network services",
        "Separating networks", "Blocking risky websites", "Using cryptography", "Building security into development",
        "Security needs stated for applications", "Principles for designing secure systems",
        "Writing code securely", "Testing security before release", "Oversight of development done by others",
        "Separating environments", "Controlled changes to systems", "Choosing and protecting test data", "Protecting systems during audit testing",
    ]),
)


def iso_27001_controls() -> List[Dict]:
    controls = []
    for prefix, theme, titles in _ISO_THEMES:
        for number, title in enumerate(titles, start=1):
            code = "%s.%d" % (prefix, number)
            controls.append({
                "control_code": code, "title": title, "category": theme,
                "description": "Annex A control %s of ISO/IEC 27001:2022, in the theme %s." % (code, theme.lower()),
                "official_reference": "ISO/IEC 27001:2022 Annex %s" % code,
            })
    return controls


_SOC2_GROUPS = (
    ("Common Criteria", "CC", [
        ("1", ["Commitment to integrity and ethics", "Independent board oversight", "Reporting lines, authority and duties",
               "Attracting and keeping competent people", "Holding people to account"]),
        ("2", ["Using good-quality information", "Communicating inside the organisation", "Communicating with outside parties"]),
        ("3", ["Stating objectives clearly", "Finding and analysing risks", "Weighing the chance of fraud",
               "Assessing changes that affect controls"]),
        ("4", ["Checking that controls work", "Reporting weaknesses in controls"]),
        ("5", ["Choosing control activities", "Everyday controls over technology", "Putting controls into policies and procedures"]),
        ("6", ["Protecting systems with access controls", "Registering and authorising new users",
               "Authorising, changing and removing access", "Restricting physical access",
               "Retiring assets only once their data cannot be recovered", "Defending against threats from outside",
               "Controlling how information moves and is sent", "Stopping and spotting malicious software"]),
        ("7", ["Spotting configuration changes and new weaknesses", "Watching for unusual behaviour",
               "Judging whether a security event is an incident", "Responding to incidents", "Recovering from incidents"]),
        ("8", ["Authorising, testing and approving changes to systems"]),
        ("9", ["Reducing the risk of business disruption", "Managing risk from vendors and partners"]),
    ]),
    ("Availability", "A", [("1", ["Planning for capacity", "Environmental protection and backups", "Testing recovery"])]),
    ("Confidentiality", "C", [("1", ["Finding and protecting confidential information", "Disposing of confidential information"])]),
    ("Processing Integrity", "PI", [("1", ["Defining what processing must achieve", "Complete and accurate inputs",
                                           "Complete and accurate processing", "Complete and accurate outputs",
                                           "Storing data completely and accurately"])]),
    ("Privacy", "P", [
        ("1", ["Communicating privacy practices"]), ("2", ["Choice and consent"]),
        ("3", ["Collecting only what is needed", "Consent for sensitive collection"]),
        ("4", ["Using personal data for its stated purposes", "Retaining personal data", "Disposing of personal data"]),
        ("5", ["Giving people access to their data", "Correcting personal data"]),
        ("6", ["Disclosing to third parties with consent", "Record of authorised disclosures",
               "Record of unauthorised disclosures", "Privacy commitments from third parties",
               "Vendors reporting unauthorised disclosure", "Telling people and regulators about breaches",
               "Telling people what is held and disclosed"]),
        ("7", ["Quality of personal data"]), ("8", ["Handling privacy complaints and monitoring"]),
    ]),
)


def soc_2_controls() -> List[Dict]:
    controls = []
    for group, prefix, series in _SOC2_GROUPS:
        for major, titles in series:
            for minor, title in enumerate(titles, start=1):
                code = "%s%s.%d" % (prefix, major, minor)
                controls.append({
                    "control_code": code, "title": title, "category": group,
                    "description": "Trust services criterion %s, in the %s category." % (code, group.lower()),
                    "official_reference": "TSC 2017 %s" % code,
                })
    return controls


_GDPR_ARTICLES = (
    [(5, "Principles for processing personal data"), (6, "A lawful basis for processing"), (7, "Conditions for consent"),
     (8, "A child's consent for online services"), (9, "Special categories of personal data"),
     (10, "Criminal conviction data"), (11, "Processing that needs no identification"),
     (12, "Clear, timely answers to people"), (13, "Information given when data is collected"),
     (14, "Information given when data comes from elsewhere"), (15, "A person's right of access"),
     (16, "The right to correction"), (17, "The right to erasure"), (18, "The right to restrict processing"),
     (19, "Telling recipients about corrections and erasure"), (20, "The right to move data"),
     (21, "The right to object"), (22, "Limits on automated decisions")]
    + [(24, "The controller's responsibility"), (25, "Data protection by design and by default"),
       (26, "Joint controllers"), (27, "Representatives of controllers outside the EU"),
       (28, "Rules for processors"), (29, "Processing only on instructions"), (30, "Records of processing"),
       (31, "Cooperating with the supervisory authority"), (32, "Security of processing"),
       (33, "Notifying the authority of a breach"), (34, "Telling people about a breach"),
       (35, "Data protection impact assessment"), (36, "Consulting the authority beforehand"),
       (37, "Appointing a data protection officer"), (38, "The officer's position"), (39, "The officer's tasks")]
    + [(44, "The general principle for transfers abroad"), (45, "Transfers to countries judged adequate"),
       (46, "Transfers with appropriate safeguards"), (47, "Binding corporate rules"),
       (48, "Orders from outside the EU to hand over data"), (49, "Transfers in specific situations")]
)
_GDPR_GROUPS = ((5, 11, "Principles"), (12, 22, "People's rights"), (24, 39, "Controller and processor"),
                (44, 49, "Transfers abroad"))


def gdpr_controls() -> List[Dict]:
    controls = []
    for number, title in _GDPR_ARTICLES:
        group = next(name for low, high, name in _GDPR_GROUPS if low <= number <= high)
        controls.append({
            "control_code": "Article - %d" % number, "title": title, "category": group,
            "description": "Article %d of Regulation (EU) 2016/679." % number,
            "official_reference": "Regulation (EU) 2016/679, Article %d" % number,
        })
    return controls


_NIST_FUNCTIONS = (
    ("GV", "Govern", "Setting and overseeing the cybersecurity strategy"),
    ("ID", "Identify", "Knowing the assets and risks"),
    ("PR", "Protect", "Safeguards that reduce risk"),
    ("DE", "Detect", "Finding possible attacks and compromises"),
    ("RS", "Respond", "Acting on a detected incident"),
    ("RC", "Recover", "Restoring what an incident disrupted"),
)
_NIST_SUBCATEGORIES: Dict[str, List[_Row]] = {
    "GV.OC": [("01", "Mission understood to guide security risk"), ("02", "Internal and external stakeholders identified"),
              ("03", "Legal and contractual duties understood"), ("04", "Critical objectives and services made known"),
              ("05", "Outside dependencies made known")],
    "GV.RM": [("01", "Risk management objectives agreed"), ("02", "Risk appetite stated"), ("03", "Security risk within enterprise risk"),
              ("04", "A strategy for responding to risk"), ("05", "Lines of risk communication set"),
              ("06", "A standard way to rate and rank risk"), ("07", "Opportunities considered alongside threats")],
    "GV.RR": [("01", "Leaders answer for security risk"), ("02", "Roles and responsibilities set"),
              ("03", "Enough resources for the roles"), ("04", "Security built into people practices")],
    "GV.PO": [("01", "Policy set from the risk context"), ("02", "Policy reviewed and kept current")],
    "GV.OV": [("01", "Strategy outcomes reviewed"), ("02", "Strategy adjusted to cover requirements"),
              ("03", "Performance measured and reviewed")],
    "GV.SC": [("01", "A supply chain risk programme"), ("02", "Supplier roles and duties set"),
              ("03", "Supply chain risk in enterprise risk"), ("04", "Suppliers ranked by importance"),
              ("05", "Requirements written into supplier terms"), ("06", "Due diligence before choosing suppliers"),
              ("07", "Supplier risks monitored"), ("08", "Suppliers join incident planning"),
              ("09", "Supply chain practices in security programmes"), ("10", "Plans for the end of a supplier relationship")],
    "ID.AM": [("01", "Hardware inventoried"), ("02", "Software and services inventoried"), ("03", "Network flows mapped"),
              ("04", "Supplier services inventoried"), ("05", "Assets ranked by importance"),
              ("07", "Data and its metadata inventoried"), ("08", "Systems and data managed through their life")],
    "ID.RA": [("01", "Vulnerabilities found and recorded"), ("02", "Threat intelligence received"),
              ("03", "Threats to the organisation recorded"), ("04", "Impacts and likelihoods assessed"),
              ("05", "Risk ranked by threat, vulnerability and impact"), ("06", "Risk responses chosen and tracked"),
              ("07", "Changes and exceptions assessed"), ("08", "Disclosure processes set up"),
              ("09", "Hardware and software checked before use"), ("10", "Supplier risk assessed before acceptance")],
    "ID.IM": [("01", "Improvements found from evaluations"), ("02", "Improvements found from tests and exercises"),
              ("03", "Improvements found from operations"), ("04", "Response and recovery plans kept current")],
    "PR.AA": [("01", "Identities and credentials managed"), ("02", "Identity proofing matched to risk"),
              ("03", "Users, services and hardware authenticated"), ("04", "Identity assertions protected"),
              ("05", "Access rights follow least privilege"), ("06", "Physical access managed")],
    "PR.AT": [("01", "Staff taught security basics"), ("02", "Privileged users given specialist training")],
    "PR.DS": [("01", "Data at rest protected"), ("02", "Data in transit protected"),
              ("10", "Data in use protected"), ("11", "Backups made, protected and tested")],
    "PR.PS": [("01", "Configuration practice kept"), ("02", "Software maintained and replaced"),
              ("03", "Hardware maintained and replaced"), ("04", "Logs generated and available"),
              ("05", "Unauthorised software prevented"), ("06", "Secure development practice followed")],
    "PR.IR": [("01", "Networks protected from unauthorised access"), ("02", "Environmental threats to assets handled"),
              ("03", "Resilience in normal and adverse conditions"), ("04", "Spare capacity for availability")],
    "DE.CM": [("01", "Networks monitored"), ("02", "Physical environment monitored"), ("03", "Staff activity monitored"),
              ("06", "Supplier activity monitored"), ("09", "Computing hardware and software monitored")],
    "DE.AE": [("02", "Events analysed for meaning"), ("03", "Information from many sources correlated"),
              ("04", "Impact of events estimated"), ("06", "Event information shared with those who act"),
              ("07", "Intelligence added to analysis"), ("08", "Incidents declared against criteria")],
    "RS.MA": [("01", "Response plan carried out"), ("02", "Incident reports triaged"), ("03", "Incidents categorised and ranked"),
              ("04", "Incidents escalated"), ("05", "Criteria for recovery applied")],
    "RS.AN": [("03", "Cause and sequence analysed"), ("06", "Investigation actions recorded"),
              ("07", "Incident data preserved"), ("08", "Incident size estimated")],
    "RS.CO": [("02", "Stakeholders told of incidents"), ("03", "Information shared as planned")],
    "RS.MI": [("01", "Incidents contained"), ("02", "Incidents eradicated")],
    "RC.RP": [("01", "Recovery plan carried out"), ("02", "Recovery actions chosen and ranked"),
              ("03", "Backups checked before restoring"), ("04", "Critical services restored first"),
              ("05", "Restored assets verified"), ("06", "End of recovery declared")],
    "RC.CO": [("03", "Recovery progress shared"), ("04", "Public recovery messages shared")],
}


def nist_csf_subcategory_items(categories: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str, str]]:
    """The subcategories under the categories the method catalogue lists, as (code, title, group, what it is)."""
    items = []
    for category, category_title, _function in categories:
        for number, title in _NIST_SUBCATEGORIES[category]:
            items.append((
                "%s-%s" % (category, number), title, "%s — %s" % (category, category_title),
                "The %s-%s subcategory of the NIST Cybersecurity Framework 2.0." % (category, number),
            ))
    return items


def nist_csf_function_items() -> List[Tuple[str, str, str, str]]:
    return [(code, name, "Functions", note + ".") for code, name, note in _NIST_FUNCTIONS]
