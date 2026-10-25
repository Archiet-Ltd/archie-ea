"""The demonstration organisation's twelve framework canvases, built from elements the canvases share.

Each canvas lists names; a name is found in the organisation's model by name and type, and made only when nothing
matches, so a customer segment on the Business Model Canvas, the Lean Canvas, the Product Vision Board and the
Wardley Map is one element four times, not four copies. Idempotent: running it twice adds nothing.
"""

from app import db

# name -> (ArchiMate type, layer). A name that the Business Model Canvas seed already makes keeps its type there.
_SEGMENT = ("BusinessActor", "business")
_SERVICE = ("BusinessService", "business")
_ASSESSMENT = ("Assessment", "motivation")
_DRIVER = ("Driver", "motivation")
_GOAL = ("Goal", "motivation")
_OUTCOME = ("Outcome", "motivation")
_METRIC = ("Metric", "motivation")
_COURSE = ("CourseOfAction", "strategy")
_PROCESS = ("BusinessProcess", "business")
_RESOURCE = ("Resource", "strategy")
_PACKAGE = ("WorkPackage", "implementation")
_VALUE = ("Value", "motivation")
_EVIDENCE = ("EvidenceRecord", "business")

SEG_MUNICIPAL = "Municipal water utilities"
SEG_INDUSTRIAL = "Industrial process-water operators"
SEG_SURVEY = "Environmental survey firms"
SVC_CERTIFIED = "Calibrated sensors with a certificate for every batch"
SVC_RECOVERED = "Recovered assemblies recertified instead of scrapped"
SVC_REMOTE = "Remote diagnostics that cut site visits"
VAL_TRUST = "Operators know their readings can be trusted"
GOAL_VISION = "Recover every sensor assembly for reuse"
GOAL_MARGIN = "Raise gross margin on recertified assemblies"
GOAL_TURNAROUND = "Cut calibration turnaround to five days"
GOAL_TRACE = "Make every reading traceable to a certificate"
GOAL_TRAIN = "Train every engineer on the new calibration method"
A_WAIT = "Customers wait weeks for recalibration"
A_VISITS = "Field visits for faulty sensors cost more than the sensor"
A_ACCEPTED = "Regulators accept our certificates without re-testing"
A_THREE = "Calibration relies on three senior technicians"
A_RECERTIFY = "Only we recertify recovered assemblies at scale"
A_REGULATION = "New monitoring rules create demand for continuous readings"
A_IMPORTS = "Imported sensors undercut us on price"
A_BUYERS = "Large utilities buy in bulk and negotiate hard"
A_ENTRY = "Few barriers stop a well-funded entrant"
A_SLOW = "Leadership decides slowly on capital spending"
A_SATELLITE = "Satellite data is good enough for some surveys"
A_FUNDING = "Renewal funding opens budgets we have not reached"
D_LEGAL = "Drinking-water rules require continuous monitoring evidence"
D_PRICES = "Component prices rising with supply shortages"
D_RIVALS = "Established instrument makers cutting prices"
O_TURNAROUND = "Calibration turnaround at or under five days"
O_RECERTIFIED = "Recertified share of shipped sensors at 30 per cent"
O_MARGIN = "Gross margin on recertified assemblies at 42 per cent"
C_WIN = "Win on certified accuracy, not price"
PKG_RIGS = "Calibration Automation Phase 1"
PKG_LAB = "Calibration Automation Phase 2"
PKG_FIELD = "Field Tester Rollout"
PKG_ANALYTICS = "Predictive Maintenance Pilot"

_ELEMENTS = {
    SEG_MUNICIPAL: _SEGMENT, SEG_INDUSTRIAL: _SEGMENT, SEG_SURVEY: _SEGMENT,
    "Regional water cooperatives": _SEGMENT, "Established instrument makers": _SEGMENT,
    "Overseas sensor maker entering the market": _SEGMENT, "Component suppliers": _SEGMENT,
    SVC_CERTIFIED: _SERVICE, SVC_RECOVERED: _SERVICE, SVC_REMOTE: _SERVICE,
    "Remote diagnostics": _SERVICE, "Cloud hosting": ("TechnologyService", "technology"),
    VAL_TRUST: _VALUE, "Certified sensors that are never thrown away": _VALUE,
    "Time to recalibrate": _VALUE, "Purchase price": _VALUE, "Evidence for every reading": _VALUE,
    GOAL_VISION: _GOAL, GOAL_MARGIN: _GOAL, GOAL_TURNAROUND: _GOAL, GOAL_TRACE: _GOAL, GOAL_TRAIN: _GOAL,
    "Pass every audit without re-testing": _GOAL,
    A_WAIT: _ASSESSMENT, A_VISITS: _ASSESSMENT, A_ACCEPTED: _ASSESSMENT, A_THREE: _ASSESSMENT,
    A_RECERTIFY: _ASSESSMENT, A_REGULATION: _ASSESSMENT, A_IMPORTS: _ASSESSMENT,
    A_BUYERS: _ASSESSMENT, A_ENTRY: _ASSESSMENT, A_SLOW: _ASSESSMENT, A_SATELLITE: _ASSESSMENT, A_FUNDING: _ASSESSMENT,
    "Public funding for water-infrastructure renewal": _DRIVER, D_PRICES: _DRIVER,
    "Public demand for open water-quality data": _DRIVER,
    "Low-power networks make remote sensing cheap": _DRIVER, D_LEGAL: _DRIVER, D_RIVALS: _DRIVER,
    "Stricter limits on electronic waste": _DRIVER,
    O_TURNAROUND: _OUTCOME, O_RECERTIFIED: _OUTCOME, O_MARGIN: _OUTCOME,
    "Days from receipt to certificate": _METRIC, "Share of sensors recertified": _METRIC,
    "Site visits per 100 sensors": _METRIC,
    C_WIN: _COURSE, "Sell recertified assemblies to current municipal customers": _COURSE,
    "Add continuous monitoring to the sensor range": _COURSE, "Open the regional survey market": _COURSE,
    "Offer recertified sensors to industrial wastewater plants": _COURSE,
    "Stop posting paper certificates": _COURSE, "Cut routine site visits": _COURSE,
    "Offer a certificate on screen for every sensor": _COURSE,
    "Plant operations manager": ("BusinessRole", "business"),
    "Calibration technician": ("BusinessRole", "business"), "Field Service Division": _SEGMENT,
    "Waste nothing that can be recovered": ("Principle", "motivation"),
    "Evidence before claims": ("Principle", "motivation"),
    "Assemble sensor module": _PROCESS, "Test sensor calibration": _PROCESS,
    "Recertify recovered assembly": _PROCESS, "Keeps a spreadsheet of calibration dates": _PROCESS,
    "Annual calibration by a third-party laboratory": _PROCESS,
    "Calibration Rig Pool": _RESOURCE, "Field Engineer Pool": _RESOURCE,
    "Component stock and supplier panel": _RESOURCE,
    "Sensor Calibration": ("Capability", "strategy"), "Asset Lifecycle Management": ("Capability", "strategy"),
    "Direct account managers": ("BusinessInterface", "business"),
    "Customer portal for orders and returns": ("BusinessInterface", "business"),
    "Calibration Ledger": ("ApplicationComponent", "application"),
    "Manual grab-sample testing": ("Product", "business"),
    "Calibration Automation Phase 1": _PACKAGE, "Calibration Automation Phase 2": _PACKAGE,
    "Field Tester Rollout": _PACKAGE, "Predictive Maintenance Pilot": _PACKAGE,
    "Supplier Consolidation Programme": _PACKAGE,
    "Customers say a reading they cannot trace is a reading they cannot use": _EVIDENCE,
    "Operators say they dread the audit cycle": _EVIDENCE,
    "Customers wonder if the vendor will support a sensor in ten years": _EVIDENCE,
    "Operators feel anxious before every audit": _EVIDENCE,
    "Operators keep their own calibration dates because they do not trust ours": _EVIDENCE,
}

# (canvas type, canvas name, {box: [element names]}) in the order the canvases are offered.
CANVASES = [
    ("lean_canvas", "Lantern Quay Systems: Lean Canvas", {
        "problem": [A_WAIT, A_VISITS],
        "existing_alternatives": ["Established instrument makers", "Annual calibration by a third-party laboratory"],
        "solution": [SVC_CERTIFIED, SVC_RECOVERED, SVC_REMOTE],
        "key_metrics": ["Days from receipt to certificate", "Share of sensors recertified"],
        "unique_value_proposition": [VAL_TRUST],
        "high_level_concept": ["Certified sensors that are never thrown away"],
        "unfair_advantage": ["Calibration Rig Pool", "Sensor Calibration"],
        "channels": ["Direct account managers", "Customer portal for orders and returns"],
        "customer_segments": [SEG_MUNICIPAL, SEG_INDUSTRIAL],
        "early_adopters": ["Regional water cooperatives"],
    }),
    ("empathy_map", "Lantern Quay Systems: Empathy Map", {
        "user": ["Plant operations manager"],
        "says": ["Customers say a reading they cannot trace is a reading they cannot use",
                 "Operators say they dread the audit cycle"],
        "thinks": ["Customers wonder if the vendor will support a sensor in ten years"],
        "does": ["Keeps a spreadsheet of calibration dates"],
        "feels": ["Operators feel anxious before every audit"],
        "pains": [A_WAIT, A_VISITS],
        "gains": ["Pass every audit without re-testing"],
    }),
    ("swot_analysis", "Lantern Quay Systems: SWOT", {
        "strengths": [A_ACCEPTED, A_RECERTIFY],
        "weaknesses": [A_WAIT, A_THREE],
        "opportunities": [A_REGULATION, A_FUNDING],
        "threats": [A_ENTRY, A_IMPORTS],
    }),
    ("pestle_analysis", "Lantern Quay Systems: PESTLE", {
        "political": ["Public funding for water-infrastructure renewal"],
        "economic": [D_PRICES],
        "social": ["Public demand for open water-quality data"],
        "technological": ["Low-power networks make remote sensing cheap"],
        "legal": [D_LEGAL],
        "environmental": ["Stricter limits on electronic waste"],
    }),
    ("porters_five_forces", "Lantern Quay Systems: Porter's Five Forces", {
        "new_entrants": [A_ENTRY, "Overseas sensor maker entering the market"],
        "suppliers": [D_PRICES, "Component suppliers"],
        "rivalry": [D_RIVALS, A_IMPORTS],
        "buyers": [A_BUYERS, SEG_MUNICIPAL],
        "substitutes": ["Manual grab-sample testing", A_SATELLITE],
    }),
    ("ansoff_matrix", "Lantern Quay Systems: Ansoff Matrix", {
        "market_penetration": ["Sell recertified assemblies to current municipal customers", O_RECERTIFIED],
        "product_development": ["Add continuous monitoring to the sensor range"],
        "market_development": ["Open the regional survey market", PKG_FIELD],
        "diversification": ["Offer recertified sensors to industrial wastewater plants"],
    }),
    ("mckinsey_7s", "Lantern Quay Systems: McKinsey 7S", {
        "shared_values": ["Waste nothing that can be recovered", "Evidence before claims"],
        "strategy": [C_WIN, GOAL_VISION],
        "structure": ["Field Service Division", "Calibration technician"],
        "systems": ["Test sensor calibration", "Calibration Ledger"],
        "skills": ["Sensor Calibration", A_THREE],
        "style": [A_SLOW],
        "staff": ["Field Engineer Pool", PKG_FIELD],
    }),
    ("blue_ocean", "Lantern Quay Systems: Blue Ocean", {
        "factors": [VAL_TRUST, "Time to recalibrate", "Purchase price"],
        "eliminate": ["Stop posting paper certificates"],
        "raise": [C_WIN],
        "reduce": ["Cut routine site visits"],
        "create": [SVC_RECOVERED, "Offer a certificate on screen for every sensor"],
    }),
    ("wardley_mapping", "Lantern Quay Systems: Wardley Map", {
        "user": [SEG_MUNICIPAL],
        "user_need": [VAL_TRUST],
        "genesis": [SVC_REMOTE],
        "custom_built": ["Recertify recovered assembly", "Calibration Rig Pool"],
        "product": [SVC_CERTIFIED, "Sensor Calibration"],
        "commodity": ["Component stock and supplier panel", "Cloud hosting"],
        "movement": [PKG_RIGS],
        "inertia": [A_THREE],
    }),
    ("balanced_scorecard", "Lantern Quay Systems: Balanced Scorecard", {
        "financial": [GOAL_MARGIN],
        "internal": [GOAL_TURNAROUND],
        "vision": [GOAL_VISION],
        "customer": [GOAL_TRACE],
        "strategy": [C_WIN],
        "learning": [GOAL_TRAIN],
        "measures": ["Days from receipt to certificate", "Share of sensors recertified", "Site visits per 100 sensors"],
        "targets": [O_TURNAROUND, O_RECERTIFIED, O_MARGIN],
        "initiatives": [PKG_RIGS, PKG_LAB, PKG_ANALYTICS],
    }),
    ("okr", "Lantern Quay Systems: OKR", {
        "objective": [GOAL_TURNAROUND],
        "key_results": [O_TURNAROUND, O_RECERTIFIED],
        "initiatives": [PKG_RIGS, PKG_LAB],
    }),
    ("product_vision_board", "Lantern Quay Systems: Product Vision Board", {
        "vision": [GOAL_VISION],
        "target_group": [SEG_MUNICIPAL, SEG_INDUSTRIAL],
        "needs": [A_WAIT, A_VISITS],
        "product": [SVC_CERTIFIED, SVC_RECOVERED],
        "business_goals": [GOAL_MARGIN, O_MARGIN],
    }),
]

# (source, target, relationship type): the links that make the shared elements a connected model.
LINKS = [
    (A_REGULATION, D_LEGAL, "Association"),
    (A_IMPORTS, D_RIVALS, "Association"),
    (O_TURNAROUND, GOAL_TURNAROUND, "Realization"),
    (O_RECERTIFIED, GOAL_VISION, "Realization"),
    (O_MARGIN, GOAL_MARGIN, "Realization"),
    (PKG_RIGS, O_TURNAROUND, "Realization"),
    (C_WIN, GOAL_VISION, "Realization"),
    (SVC_CERTIFIED, VAL_TRUST, "Realization"),
    (A_WAIT, SEG_MUNICIPAL, "Association"),
    ("Days from receipt to certificate", O_TURNAROUND, "Association"),
    # The Wardley Map's dependencies: what serves what, from the user's need downwards.
    (SVC_REMOTE, VAL_TRUST, "Realization"),
    ("Sensor Calibration", SVC_CERTIFIED, "Serving"),
    ("Recertify recovered assembly", SVC_CERTIFIED, "Serving"),
    ("Calibration Rig Pool", "Sensor Calibration", "Serving"),
    ("Calibration Rig Pool", "Recertify recovered assembly", "Serving"),
    ("Component stock and supplier panel", "Calibration Rig Pool", "Serving"),
    ("Cloud hosting", SVC_REMOTE, "Serving"),
    ("Cloud hosting", "Sensor Calibration", "Serving"),
    (PKG_RIGS, "Calibration Rig Pool", "Association"),
    (A_THREE, "Sensor Calibration", "Association"),
]


def seed(org_id):
    """Create or complete the twelve canvases for one organisation. Returns (canvases created, elements and links created)."""
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service
    from app.services.archimate_backbone import create_backbone_element

    made = 0
    existing = {
        (e.name, e.type): e
        for e in db.session.query(ArchiMateElement).filter(ArchiMateElement.organization_id == org_id)
    }

    def element(name):
        nonlocal made
        type_, layer = _ELEMENTS[name]
        el = existing.get((name, type_))
        if el is None:
            el = create_backbone_element(
                element_type=type_, layer=layer, name=name, organization_id=org_id,
                provenance={"created_from": "demo company seed"},
            )
            existing[(name, type_)] = el
            made += 1
        return el

    created = 0
    for canvas_type, name, boxes in CANVASES:
        canvas = BusinessModelCanvas.query.filter_by(organization_id=org_id, name=name).first()
        if canvas is None:
            canvas = BusinessModelCanvas(organization_id=org_id, name=name, canvas_type=canvas_type)
            db.session.add(canvas)
            db.session.flush()
            created += 1
        for box, names in boxes.items():
            for entry in names:
                model_service.place(canvas, box, element(entry))
        db.session.flush()

    for source, target, rel_type in LINKS:
        src, tgt = element(source), element(target)
        if not db.session.query(ArchiMateRelationship.id).filter_by(
            source_id=src.id, target_id=tgt.id, type=rel_type, organization_id=org_id
        ).first():
            db.session.add(ArchiMateRelationship(
                source_id=src.id, target_id=tgt.id, type=rel_type, organization_id=org_id,
            ))
            made += 1
    db.session.flush()
    return created, made
