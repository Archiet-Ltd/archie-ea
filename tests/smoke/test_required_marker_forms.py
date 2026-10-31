"""A required field says so: the 16 reachable converted forms show the marker beside the label of every
required control and require the control behind every marked label (WCAG 3.3.2).

The marker comes from one place, the field label macro's `required` argument, so no converted form
template writes an asterisk into a label by hand.
"""
import datetime
import pathlib
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROOT = pathlib.Path(__file__).resolve().parents[2]

FORMS = [
    ("platform_admin", "/admin/agent-registry/new"),
    ("procurement", "/procurement/licenses/new"),
    ("application_manager", "/my-applications/app/{application}/edit"),
    ("data_architect", "/data-governance/issues/new?data_entity_id={data_entity}"),
    ("business_architect", "/enterprise/capability-map/mapping"),
    ("application_manager", "/dashboard/rationalization/assessment"),
    ("application_manager", "/dashboard/rationalization/validate"),
    ("application_manager", "/applications/rationalization/enrich"),
    ("business_architect", "/architecture-journey/work/{architecture_journey}"),
    ("solution_architect", "/architecture-journey/{solution}"),
    ("solution_architect", "/solutions/{solution}/edit"),
    ("solution_architect", "/archimate/import-review"),
    ("solution_architect", "/archimate/import"),
    ("solution_architect", "/interface-register/new?initiative_id={interface_register_initiative}"),
    ("solution_architect", "/architecture/decisions/{decision}/record-outcome"),
    ("solution_architect", "/ea-workflows/deliverables/vision"),
]

TEMPLATES = [
    "ai_chat/agent_registry/new.html", "application_mgmt/custom_field_form.html",
    "application_mgmt/rationalization_assessment.html", "application_mgmt/response_validation.html",
    "applications/rationalization/enrich.html", "archimate/import_review.html", "architecture/import_csv.html",
    "architecture_assistant/architecture_journey_workspace.html", "architecture_assistant/journey_v3.html",
    "architecture_decisions/record_outcome.html", "capability_map/mapping.html",
    "data_governance/new_data_issue.html", "ea_workflows/deliverable_editor.html", "solutions/edit.html",
]

# Every control that is required, and every label (or legend) that carries the marker, seen the way a
# person or a screen reader meets them.
AUDIT_JS = """() => {
  const main = document.querySelector('main, [role=main]') || document.body;
  const marked = t => /\\*\\s*$/.test((t || '').replace(/\\s+/g, ' ').trim());
  const isReq = e => e.required || e.getAttribute('aria-required') === 'true';
  const controls = [...main.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea')];
  const nameOf = e => e.name || e.id || e.tagName;
  const unmarked = [], dangling = [];
  for (const c of controls) {
    if (!isReq(c)) continue;
    let label = c.labels && c.labels[0] ? c.labels[0] : null;
    if (!label || c.type === 'radio') { const fs = c.closest('fieldset'); const lg = fs && fs.querySelector('legend'); label = lg || label; }
    if (!label || !marked(label.textContent)) unmarked.push(nameOf(c));
  }
  for (const l of main.querySelectorAll('label, legend')) {
    if (!marked(l.textContent)) continue;
    let targets = [];
    if (l.tagName === 'LEGEND') targets = [...(l.closest('fieldset') || document).querySelectorAll('input, select, textarea')];
    else if (l.control) targets = [l.control];
    else targets = [...l.querySelectorAll('input, select, textarea')];
    if (!targets.some(isReq)) dangling.push((l.textContent || '').replace(/\\s+/g, ' ').trim());
  }
  return {unmarked, dangling, total: controls.length};
}"""


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    p = ctx.new_page()
    yield p
    ctx.close()


@pytest.fixture
def seeded_forms(seeded):
    """The seeded organisation plus a data entity and a decision due for review."""
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.process_data import DataEntity

    app = create_app("testing")
    with app.app_context():
        entity = DataEntity(name="Customer %s" % uuid.uuid4().hex[:6], domain_id=seeded["ids"]["data_domain"],
                            organization_id=seeded["ids"]["org"])
        decision = ArchitectureDecision(
            decision_id="RD-%s" % uuid.uuid4().hex[:6], title="Renew the managed database %s" % uuid.uuid4().hex[:6],
            status="accepted", organization_id=seeded["ids"]["org"], review_date=datetime.date(2020, 1, 1),
            context="Vendor contract signed for an initial term.")
        db.session.add_all([entity, decision])
        db.session.commit()
        ids = dict(seeded["ids"], data_entity=entity.id, decision=decision.id)
    return dict(seeded, ids=ids)


@pytest.mark.parametrize("archetype,path_fmt", FORMS)
def test_every_required_control_shows_the_marker_and_every_marker_has_a_required_control(
        page, live_server, seeded_forms, archetype, path_fmt):
    _login(page, live_server, seeded_forms["emails"][archetype])
    resp = page.goto(live_server + path_fmt.format(**seeded_forms["ids"]))
    assert resp.status < 400, (path_fmt, resp.status)
    result = page.evaluate(AUDIT_JS)
    assert not result["unmarked"], "required control without the marker on its label: %s" % result["unmarked"]
    assert not result["dangling"], "marker on a label whose control is not required: %s" % result["dangling"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_no_converted_form_writes_the_marker_into_a_label_by_hand(template):
    """The marker is the field label macro's job; a literal ` *` in label text is the hand-written copy."""
    source = (ROOT / "app" / "templates" / template).read_text(encoding="utf-8")
    hand = re.findall(r"""(?:text|label)\s*=\s*['"][^'"]*\s\*['"]""", source)
    hand += re.findall(r"""field\([^)]*['"][^'"]*\s\*['"]""", source)
    assert not hand, hand
