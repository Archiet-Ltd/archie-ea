"""A stakeholder reads a shared dashboard with a keyboard and a screen reader, and
the conformance statement is generated from that run.

The stakeholder has the link and no account. The journey opens it, walks every
control with Tab (each must be reached, named and show where focus is), runs axe
with the audit's WCAG 2.2 AA tag set, reloads and does it all again. The
conformance statement is then built from exactly those results by
scripts/accessibility_conformance.py and must state that the journey meets WCAG 2.2
AA. A second organisation's link shows only that organisation.

Set SMOKE_EVIDENCE_DIR to keep the run record and the statement.
"""

import datetime
import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT
from .test_accessibility_audit import TAGS

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("accessibility_conformance", ROOT / "scripts" / "accessibility_conformance.py")
conformance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conformance)

FOCUSABLE = ("a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]),"
             " textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")

DESCRIBE = """
() => {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  const cs = getComputedStyle(a);
  const ring = (cs.boxShadow && cs.boxShadow !== 'none') ||
    (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0);
  const labelled = a.getAttribute('aria-labelledby');
  const name = (a.getAttribute('aria-label') || (labelled && document.getElementById(labelled)
    ? document.getElementById(labelled).textContent : '') || a.innerText || a.value || a.title || '').trim();
  if (!a.dataset.kbId) a.dataset.kbId = 'kb' + Math.random().toString(36).slice(2);
  return {id: a.dataset.kbId, name: name.slice(0, 80), ring: !!ring, tag: a.tagName};
}
"""


def _share_link(org_id, artefact_type="maturity_heatmap"):
    from app import create_app, db
    from app.models.artefact_share import ArtefactShareLink, generate_share_token

    app = create_app("testing")
    with app.app_context():
        link = ArtefactShareLink(token=generate_share_token(), artefact_type=artefact_type,
                                 organization_id=org_id, created_at=datetime.datetime.utcnow(), view_count=0)
        db.session.add(link)
        db.session.commit()
        return link.token


@pytest.fixture(scope="module")
def shared(seeded, live_server):
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        org_a_name = db.session.get(Organization, seeded["ids"]["org"]).name
        other = Organization(name="Harbourline Share %s" % suffix, slug="harbourline-%s" % suffix)
        db.session.add(other)
        db.session.commit()
        other_id, other_name = other.id, other.name
    return {
        "a": {"token": _share_link(seeded["ids"]["org"]), "name": org_a_name},
        "b": {"token": _share_link(other_id), "name": other_name},
    }


@pytest.fixture
def visitor(browser):
    """Someone with the link and no account: a fresh browser, never signed in."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _keyboard_walk(page):
    """Tab through the page from the top until focus comes back round; every stop is
    recorded, and every focusable control on the page must have been one of them."""
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
    seen, stops = set(), []
    for _ in range(200):
        page.keyboard.press("Tab")
        state = page.evaluate(DESCRIBE)
        if state is None or state["id"] in seen:
            break
        seen.add(state["id"])
        stops.append(state)
    unreachable = page.eval_on_selector_all(
        FOCUSABLE,
        "(els) => els.filter(e => e.offsetParent !== null && !e.dataset.kbId)"
        ".map(e => (e.innerText || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 60))")
    return {
        "controls": len(stops),
        "unnamed": [s["tag"] for s in stops if not s["name"]],
        "no_focus_ring": [s["name"] or s["tag"] for s in stops if not s["ring"]],
        "unreachable": unreachable,
    }


def _audit(page, name, path):
    from axe_playwright_python import sync_playwright as axe_module

    keyboard = _keyboard_walk(page)
    axe = axe_module.Axe()
    report = axe.run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
    data = report.response if hasattr(report, "response") else report
    return {
        "name": name,
        "path": path,
        "violations": [{"id": v["id"], "impact": v.get("impact"), "nodes": len(v.get("nodes") or [])}
                       for v in data.get("violations", [])],
        "passes": len(data.get("passes", [])),
        "keyboard": keyboard,
    }


def test_a_stakeholder_reads_a_shared_dashboard_by_keyboard_and_the_statement_says_it_meets_wcag_22_aa(
    visitor, live_server, shared
):
    path = "/shared/%s" % shared["a"]["token"]
    visitor.goto(live_server + path, wait_until="load", timeout=PAGE_TIMEOUT)
    assert "/account/login" not in visitor.url, "a shared link must open without an account"
    assert visitor.get_by_role("heading", level=1).count() == 1

    pages = [_audit(visitor, "Shared dashboard", "/shared/<link>")]
    visitor.reload(wait_until="load")
    pages.append(_audit(visitor, "Shared dashboard, after reload", "/shared/<link>"))

    run = {
        "journey": "Read a shared dashboard with a keyboard and a screen reader",
        "tags": TAGS,
        "generated_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "pages": pages,
    }
    statement = conformance.build_statement(run)
    folder = os.environ.get("SMOKE_EVIDENCE_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "shared-dashboard-run.json"), "w", encoding="utf-8") as fh:
            json.dump(run, fh, indent=2)
        with open(os.path.join(folder, "shared-dashboard-conformance.md"), "w", encoding="utf-8") as fh:
            fh.write(statement["text"])

    assert all(p["keyboard"]["controls"] >= 1 for p in pages), pages
    assert statement["meets"], statement["text"]
    assert "This journey meets WCAG 2.2 level AA." in statement["text"]


def test_each_organisations_shared_link_shows_only_that_organisation(visitor, live_server, shared):
    visitor.goto(live_server + "/shared/%s" % shared["b"]["token"], wait_until="load", timeout=PAGE_TIMEOUT)
    body = visitor.locator("main").text_content()
    assert shared["b"]["name"] in body
    assert shared["a"]["name"] not in body
    visitor.goto(live_server + "/shared/%s" % shared["a"]["token"], wait_until="load", timeout=PAGE_TIMEOUT)
    body = visitor.locator("main").text_content()
    assert shared["a"]["name"] in body
    assert shared["b"]["name"] not in body
