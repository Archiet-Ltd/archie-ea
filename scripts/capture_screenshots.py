#!/usr/bin/env python
"""Photograph the product, so the README shows it instead of describing it.

Archie is a visual product -- capability maps, ArchiMate models, roadmaps, ARB
queues -- and until 31 Aug 2026 the repository contained ZERO images. Someone
evaluating "an open-source LeanIX alternative" decides in a few seconds, from
pictures, and there were none to look at. That is a conversion problem no amount
of SEO fixes, because the traffic that already arrives has nothing to judge.

This drives a real browser against a real seeded database, so the images are the
product as it actually renders rather than a mock-up that will drift. Re-running
it after a UI change refreshes the shop window, and a screenshot that comes back
ugly is a UX finding rather than a marketing inconvenience -- which is the more
useful half of this script.

    # 1. a demo database with data the UI actually reads
    createdb archie_demo
    DATABASE_URL=postgresql://.../archie_demo flask --app manage init-db
    DATABASE_URL=postgresql://.../archie_demo flask --app manage reconcile-schema

    # 2. start the app against it, then:
    python scripts/capture_screenshots.py --base http://127.0.0.1:5100 \
        --email demo@archie.local --password 'DemoPassw0rd!23'

Images land in docs/screenshots/ at 1440x900, which is the width the README
renders at on GitHub without the reader zooming.

--modules is a second, self-contained mode for the public /modules/<slug> and
/use-cases/<slug> pages (see run_public_pages_capture() below): it seeds the
fictional "Lantern Quay Systems" demonstration organisation (the one
app/commands/seed_demo_company.py already builds -- this reuses it rather than
inventing a second fictional company), boots a real server against a database
you point it at, captures one real screen per live module/use-case and a short
recording for four multi-step use cases, and writes everything the public pages
render straight into app/static/. Needs an already-created, reachable Postgres
database (createdb first; this runs the schema migrations itself) and, for the
video step, ffmpeg on PATH.

    createdb archie_shots
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/archie_shots \
        python scripts/capture_screenshots.py --modules
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The surfaces that say what this product IS. Ordered as a story: what the
# estate looks like, what the business does, what runs it, how change is
# governed. Anything that renders empty is reported rather than shipped -- an
# empty screenshot is worse than no screenshot.
PAGES = [
    ("dashboard", "/dashboard/overview", "Executive dashboard"),
    ("capability-map", "/capability-map/", "Business capability map"),
    ("applications", "/applications/", "Application portfolio"),
    ("arb", "/arb/", "Architecture Review Board"),
    ("value-streams", "/value-streams/", "Value streams"),
    ("roadmap", "/capability-map/roadmap", "Capability roadmap"),
    ("archimate", "/architecture/", "ArchiMate model"),
]

VIEWPORT = {"width": 1440, "height": 900}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5100")
    parser.add_argument("--email", default="demo@archie.local")
    parser.add_argument("--password", default="DemoPassw0rd!23")
    parser.add_argument("--out", default=os.path.join("docs", "screenshots"))
    parser.add_argument("--full-page", action="store_true",
                        help="capture the whole scroll height, not just the fold")
    parser.add_argument("--modules", action="store_true",
                        help="self-contained capture for the public module/use-case "
                             "pages -- see the module docstring. Ignores every other "
                             "flag above except --out, which it reinterprets as the "
                             "repo root's static asset tree is used instead.")
    args = parser.parse_args()

    if args.modules:
        return run_public_pages_capture()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && "
              "python -m playwright install chromium", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    captured, empty, failed = [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)

        page.goto(args.base + "/account/login", wait_until="domcontentloaded",
                  timeout=60000)
        page.fill("#email", args.email)
        page.fill("#password", args.password)
        try:
            page.click("#submit", force=True, no_wait_after=True)
        except TypeError:
            page.locator("#submit").dispatch_event("click")
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=60000)
        print("signed in as", args.email)

        for slug, path, title in PAGES:
            try:
                response = page.goto(args.base + path,
                                     wait_until="networkidle", timeout=60000)
                status = response.status if response else 0
                if status >= 400:
                    failed.append("%s -> HTTP %d" % (path, status))
                    continue
                page.wait_for_timeout(1200)  # let charts and icons settle

                # An empty screen is a finding, not a picture. Report it rather
                # than shipping a shop window full of empty states.
                text = page.evaluate("() => document.body.innerText") or ""
                if len(text.strip()) < 200:
                    empty.append("%s (only %d chars of text)" % (path, len(text.strip())))

                target = os.path.join(args.out, slug + ".png")
                page.screenshot(path=target, full_page=args.full_page)
                captured.append((slug, title, path))
                print("  captured %-16s %s" % (slug, path))
            except Exception as exc:
                failed.append("%s -> %s: %s" % (path, type(exc).__name__, str(exc)[:80]))

        browser.close()

    print()
    print("captured %d, empty %d, failed %d" % (len(captured), len(empty), len(failed)))
    for line in empty:
        print("  LOOKS EMPTY: " + line)
    for line in failed:
        print("  FAILED:      " + line)
    return 0 if captured else 1


# ═════════════════════════════════════════════════════════════════════════════
#  --modules: public module/use-case page screenshots and recordings
# ═════════════════════════════════════════════════════════════════════════════
#
# Reuse, not a second system: the fictional organisation is the same "Lantern
# Quay Systems" app/commands/seed_demo_company.py already builds for other demo
# purposes (~300 ArchiMate elements, applications, risks, capabilities,
# programmes -- every name in it already invented). This adds three persona
# role-grants on top of that seed (never editing seed_demo_company.py itself)
# so the handful of role-gated module pages (Integrations, My Applications,
# Procurement) have someone allowed to view them, then drives the same
# Playwright/page.html/public_pages.py stack every other capture and render in
# this codebase already uses.

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# The registry (which slugs, which paths, which seeded persona, the caption
# and alt text) lives in app/services/public_pages.py -- the renderer's own
# module -- and is imported here rather than duplicated, so the capture tool
# and the page.html render can never silently drift out of agreement about
# what a slug's image is of. See that module for MODULE_CAPTURES,
# USE_CASE_SCREENSHOT_CAPTURES, USE_CASE_VIDEO_CAPTURES and the four persona
# constants.
from app.services.public_pages import (  # noqa: E402
    APP_MANAGER_PERSONA as APP_MANAGER_EMAIL,
    IMG_MODULES_DIR,
    IMG_USE_CASES_DIR,
    ITOPS_ADMIN_PERSONA as ITOPS_ADMIN_EMAIL,
    MODULE_CAPTURES as MODULES,
    PROCUREMENT_PERSONA as PROCUREMENT_EMAIL,
    USE_CASE_SCREENSHOT_CAPTURES as USE_CASE_SCREENSHOTS,
    USE_CASE_VIDEO_CAPTURES as USE_CASE_VIDEOS,
    VIDEO_USE_CASES_DIR,
)
from app.services.public_pages import DEMO_PERSONA as DEMO_EMAIL  # noqa: E402

MAX_IMAGE_BYTES = 200 * 1024
MAX_VIDEO_BYTES = 3 * 1024 * 1024
IMAGE_CAPTURE_VIEWPORT = {"width": 1440, "height": 900}
IMAGE_TARGET_WIDTH = 1200
VIDEO_VIEWPORT = {"width": 1280, "height": 800}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _seed_module_screen_data(org_id: int, demo_user_id: int) -> None:
    """Fill in the modules seed_demo_company() doesn't reach.

    That command builds Lantern Quay Systems' ArchiMate model, applications,
    capabilities, risks, programmes and work packages -- but roughly half the
    live module screens read a DIFFERENT model entirely (an empty review
    queue, business case list, canvas list, etc. is still "working as coded",
    just not what a module's main screen looks like with real use). Each
    block below targets exactly the model and tenant-scoping mechanism the
    live view code actually reads (confirmed by reading each view, not
    guessed), reusing the same organisation, the same seeded ArchiMate
    elements/applications/capabilities, and the same invented people already
    in the cast -- no second fictional universe.

    Idempotent the same way seed_demo_company() is: every insert is guarded
    by a lookup first, so re-running this after a partial failure does not
    duplicate rows or crash on a unique constraint.
    """
    import datetime as _dt

    from app import db
    from app.commands.seed_demo_company import (
        _APP_PROFILE,
        _resolve_apps,
        _resolve_caps,
        _resolve_elements,
        _resolve_users,
    )
    from app.jobs.tenant_safe_job import tenant_scope

    with tenant_scope(org_id):
        # tenant_scope() resets the session (see seed_demo_company.py's own
        # comment on the same pattern), so these must be resolved from
        # inside the scope -- resolving them before entering it produces
        # detached ORM instances the moment anything here touches a lazy
        # attribute.
        elements = _resolve_elements(org_id)
        apps = _resolve_apps(org_id)
        caps = _resolve_caps(org_id)
        users = _resolve_users(org_id)

        # ── ARB review items (arb.dashboard / arb.reviews read ARBReviewItem) ──
        # Round 2 (MP-B1): the hero capture moved from arb.dashboard to
        # arb.reviews (the "Full review ledger") because the dashboard's own
        # AI queue triage panel sat above the fold and a 4-item queue read as
        # a toy. The ledger has no AI panel at all, and a few dozen items
        # across every status/type/priority gives it genuine, paginated
        # volume (20/page) instead of a single screenful.
        from app.models.architecture_review_board import ARBReviewItem

        # Each item now carries a one- or two-sentence description
        # specific to its own title (the queue already had a real depth,
        # but every item still read "No description"), and submitter_id
        # rotates across ten existing cast members by subject -- infrastructure to
        # Sage ITOps, supplier items to Taylor Procurement, calibration
        # standards to Blake Quality, and others from the cast at
        # app/commands/seed_demo_company.py's own _OWNER_USERS -- instead of
        # every item reading "Demo User".
        _arb_titles = [
            ("Migrate event backbone to a managed message broker", "architecture_change",
             "Moves event traffic between calibration rigs and the ledger off the "
             "self-hosted broker onto a managed service, so a broker outage no "
             "longer needs an on-call page.", "sage.itops@lantern-quay.example.com"),
            ("Consolidate supplier onboarding into Supplier Portal", "architecture_change",
             "Brings the three separate supplier intake spreadsheets into the "
             "Supplier Portal, so every new vendor goes through the same checks "
             "before their first purchase order.", "taylor.procurement@lantern-quay.example.com"),
            ("Adopt predictive maintenance for field service", "architecture_change",
             "Uses sensor telemetry from deployed field testers to schedule "
             "maintenance before a unit fails, rather than on a fixed calendar.",
             "ivo.reed@lantern-quay.example.com"),
            ("Retire the manual inventory reconciliation spreadsheet", "architecture_change",
             "Replaces the monthly manual stock count spreadsheet with the "
             "inventory reconciliation tool now live in the warehouse.",
             "casey.inventory@lantern-quay.example.com"),
            ("Standardise calibration rig firmware update process", "standard",
             "Gives every calibration rig the same firmware update procedure, "
             "closing the gap where two depots were running different rig "
             "firmware versions.", "blake.quality@lantern-quay.example.com"),
            ("Adopt a shared API gateway for field service integrations", "architecture_change",
             "Puts every field service integration behind one gateway, instead "
             "of the handful of direct point-to-point connections added over "
             "the last two years.", "sage.itops@lantern-quay.example.com"),
            ("Introduce a canonical customer identifier across billing and CRM", "standard",
             "Gives each customer one identifier shared by billing and the CRM, "
             "closing the mismatch that currently needs a manual reconciliation "
             "each month.", "drew.dataanalyst@lantern-quay.example.com"),
            ("Replace the legacy EDI gateway with managed file transfer", "architecture_change",
             "Moves supplier EDI exchanges off the gateway that only one "
             "engineer still knows how to operate, onto a managed file "
             "transfer service.", "sage.itops@lantern-quay.example.com"),
            ("Consolidate three ticketing tools into one service desk", "solution",
             "Merges the separate IT, facilities and field service ticketing "
             "tools into a single service desk, so a request no longer gets "
             "lost between systems.", "finley.support@lantern-quay.example.com"),
            ("Add a calibration rig telemetry data lake", "architecture_change",
             "Collects calibration rig telemetry in one data lake, so "
             "engineering can run trend analysis that today needs exporting "
             "each rig's logs by hand.", "blake.quality@lantern-quay.example.com"),
            ("Change request: extend warranty tracking to field kits", "change_request",
             "Extends the existing warranty tracker, currently scoped to fixed "
             "equipment, to cover the handheld field test kits now in "
             "circulation.", "jordan.fieldeng@lantern-quay.example.com"),
            ("Adopt single sign-on across all customer-facing portals", "standard",
             "Brings every customer-facing portal under one sign-on, removing "
             "the separate password resets each portal currently manages on "
             "its own.", "sage.itops@lantern-quay.example.com"),
            ("Decommission the on-premises fax gateway", "change_request",
             "Retires the on-premises fax gateway that now carries fewer than "
             "ten transmissions a month, once the two remaining suppliers that "
             "use it confirm email receipt instead.", "sage.itops@lantern-quay.example.com"),
            ("Introduce event-driven inventory reconciliation", "architecture_change",
             "Replaces the nightly batch stock reconciliation with an "
             "event-driven update each time a part moves, closing the same-day "
             "visibility gap.", "casey.inventory@lantern-quay.example.com"),
            ("Adopt a shared vendor risk scoring standard", "standard",
             "Applies one risk scoring method to every supplier at onboarding "
             "and renewal, instead of each procurement category scoring risk "
             "its own way.", "taylor.procurement@lantern-quay.example.com"),
            ("Migrate the calibration ledger to the managed database tier", "solution",
             "Moves the calibration ledger database onto the managed tier the "
             "rest of the estate already runs on, ending its separate backup "
             "and patching schedule.", "blake.quality@lantern-quay.example.com"),
            ("Change request: add multi-currency support to contracts", "change_request",
             "Adds multi-currency fields to the contract record, needed now "
             "that two of the newer suppliers invoice in euros rather than "
             "sterling.", "taylor.procurement@lantern-quay.example.com"),
            ("Retire the spreadsheet-based capacity planning process", "architecture_change",
             "Replaces the shared capacity planning spreadsheet, which three "
             "teams currently edit by hand each week, with the programme's own "
             "planning view.", "ivo.reed@lantern-quay.example.com"),
            ("Adopt a common logging and tracing standard for field apps", "standard",
             "Gives every field application the same logging and tracing "
             "format, so a field incident can be traced across apps instead of "
             "app by app.", "sage.itops@lantern-quay.example.com"),
            ("Consolidate supplier onboarding forms into one intake flow", "solution",
             "Merges the separate finance, legal and procurement intake forms "
             "a new supplier currently fills in three times into one flow.",
             "taylor.procurement@lantern-quay.example.com"),
            ("Change request: shorten the calibration certificate renewal window", "change_request",
             "Shortens the calibration certificate renewal window from 90 to "
             "60 days, matching what two major customers now require in "
             "contract.", "blake.quality@lantern-quay.example.com"),
            ("Introduce a real-time parts availability feed", "architecture_change",
             "Gives field engineers a real-time feed of parts availability at "
             "each depot, replacing the end-of-day stock report they currently "
             "work from.", "casey.inventory@lantern-quay.example.com"),
            ("Adopt a shared incident severity classification standard", "standard",
             "Applies one severity classification to every incident, closing "
             "the gap where field service and IT currently label the same "
             "kind of outage differently.", "finley.support@lantern-quay.example.com"),
            ("Replace manual contract renewal reminders with automated alerts", "solution",
             "Replaces the manually maintained renewal reminder spreadsheet "
             "with automated alerts sent ahead of each contract's expiry.",
             "taylor.procurement@lantern-quay.example.com"),
            ("Change request: add offline mode to the field engineer app", "change_request",
             "Adds an offline mode to the field engineer app, needed for the "
             "depots where mobile coverage regularly drops during a "
             "calibration visit.", "jordan.fieldeng@lantern-quay.example.com"),
            ("Consolidate calibration certificate templates into one standard", "standard",
             "Reduces the five calibration certificate templates in use today "
             "to one standard template, aligned to the latest accreditation "
             "requirements.", "blake.quality@lantern-quay.example.com"),
            ("Adopt a managed queue for supplier status webhooks", "architecture_change",
             "Moves supplier status webhook delivery onto a managed queue, so "
             "a slow supplier endpoint no longer blocks the next webhook in "
             "line.", "sage.itops@lantern-quay.example.com"),
            ("Retire the duplicate document management instance", "architecture_change",
             "Retires the second Document Management instance stood up during "
             "the acquisition, once its records are confirmed migrated to the "
             "primary system.", "avery.compliance@lantern-quay.example.com"),
        ]
        _arb_cycle = [
            ("submitted", "high"), ("under_review", "medium"), ("approved", "high"),
            ("rejected", "low"), ("approved_with_conditions", "medium"), ("deferred", "low"),
            ("submitted", "critical"), ("under_review", "high"),
        ]
        for i, (title, review_type, description, submitter_email) in enumerate(_arb_titles):
            number = f"REV-2026-{i + 1:03d}"
            if ARBReviewItem.query.filter_by(review_number=number).first():
                continue
            status, priority = _arb_cycle[i % len(_arb_cycle)]
            submitter = users.get(submitter_email)
            db.session.add(ARBReviewItem(
                review_number=number, title=title, review_type=review_type,
                description=description,
                status=status, priority=priority,
                submitter_id=submitter.id if submitter else demo_user_id,
                submitted_at=_dt.datetime.utcnow() - _dt.timedelta(days=90 - i * 2),
            ))
        db.session.flush()

        # ── Business cases (business_case.index reads BusinessCase) ────────
        from app.models.business_case import BusinessCase

        case_rows = [
            ("Automate calibration batch release", "draft", 450000, 95000, 820000, 22.5, 18),
            ("Migrate the event backbone to the cloud", "submitted", 210000, 48000, 610000, 31.0, 14),
            ("Consolidate the supplier panel", "approved", 95000, 15000, 240000, 18.0, 24),
        ]
        for title, status, capex, opex, benefit, roi, payback in case_rows:
            if BusinessCase.query.filter_by(title=title).first():
                continue
            db.session.add(BusinessCase(
                title=title, description=f"Business case: {title.lower()}.",
                status=status, capex=capex, opex_annual=opex,
                financial_benefit_annual=benefit, roi_percentage=roi,
                payback_months=payback, created_by_id=demo_user_id,
            ))
        db.session.flush()

        # ── Business model canvas (business_model.index) ───────────────────
        from app.models.business_model import BusinessModelCanvas

        if not BusinessModelCanvas.query.filter_by(name="Lantern Quay core operating model").first():
            db.session.add(BusinessModelCanvas(
                name="Lantern Quay core operating model",
                description="How Lantern Quay Systems creates and captures value today.",
                operating_model_type="unification",
                key_partners="Calibration equipment suppliers; logistics carriers",
                key_activities="Sensor calibration; field service; quality assurance",
                key_resources="Calibration Rig Pool; Field Engineer Pool",
                value_propositions="Certified, traceable sensor calibration at scale",
                customer_relationships="Dedicated account management; service contracts",
                channels="Direct sales; field service visits",
                customer_segments="Industrial water utilities; manufacturing plants",
                cost_structure="Calibration equipment; field labour; compliance",
                revenue_streams="Calibration service contracts; equipment leasing",
            ))
        db.session.flush()

        # ── Saved ArchiMate diagrams (archimate.diagrams_library, and
        #    archimate.composer_page with ?viewpoint_id=<id>) ────────────────
        # Elements are laid out on an explicit grid, not left at the model's
        # default (0, 0), so the Composer has something real to draw. The
        # hero diagram's own elements are chosen by name -- a real, connected
        # cluster spanning the business, application and technology layers
        # around the calibration ledger -- laid out in layer rows, and every
        # ArchiMateRelationship joining two of them gets a
        # SavedDiagramRelationship so the composer actually draws the
        # connecting lines. The other two saved diagrams keep a generic
        # "first 9 elements" grid.
        from app.models import ArchiMateRelationship
        from app.models.archimate_core import (
            SavedDiagram,
            SavedDiagramElement,
            SavedDiagramRelationship,
        )

        diagram_names = [
            "Calibration estate overview",
            "Event backbone target state",
            "Supplier integration landscape",
        ]
        _diagrams_hero_name = diagram_names[0]

        # Layer rows, top to bottom: business, application, technology.
        # Width 220 and 300px horizontal spacing give a two-line name room
        # so it never overprints the type label the way it did at the old
        # 180px/260px grid.
        _hero_layers = [
            ("Evaluate calibration", "Validate measurement accuracy",
             "Compute drift trend", "Generate calibration certificate"),
            ("Calibration Ledger", "Calibration Lookup", "Calibration result"),
            ("Relational Database", "Quay Compute Pool", "Database Engine"),
        ]
        _hero_element_names = [name for row in _hero_layers for name in row]
        # (source name, target name, relationship type) -- every one of
        # these is a real row _RELATIONSHIPS already seeds; none invented
        # here. 11 pairs, enough that the hero shows many connecting lines.
        _hero_relationship_pairs = [
            ("Calibration Ledger", "Evaluate calibration", "Assignment"),
            ("Calibration Ledger", "Evaluate calibration", "Serving"),
            ("Evaluate calibration", "Calibration result", "Access"),
            ("Calibration Ledger", "Calibration Lookup", "Realization"),
            ("Calibration Ledger", "Validate measurement accuracy", "Serving"),
            ("Calibration Ledger", "Compute drift trend", "Serving"),
            ("Calibration Ledger", "Generate calibration certificate", "Serving"),
            ("Relational Database", "Calibration Ledger", "Serving"),
            ("Quay Compute Pool", "Relational Database", "Realization"),
            ("Database Engine", "Relational Database", "Assignment"),
            ("Validate measurement accuracy", "Calibration result", "Access"),
        ]
        _other_element_ids = [el.id for el in list(elements.values())[:9]]

        for name in diagram_names:
            diagram = SavedDiagram.query.filter_by(name=name).first()
            if diagram is not None:
                continue
            diagram = SavedDiagram(name=name, created_by_id=demo_user_id)
            db.session.add(diagram)
            db.session.flush()
            if name == _diagrams_hero_name:
                for row_index, row_names in enumerate(_hero_layers):
                    for col_index, el_name in enumerate(row_names):
                        el = elements.get(el_name)
                        if el is None:
                            continue
                        db.session.add(SavedDiagramElement(
                            diagram_id=diagram.id, element_id=el.id,
                            position_x=80 + col_index * 300,
                            position_y=80 + row_index * 220,
                            width=220, height=80,
                        ))
            else:
                for i, element_id in enumerate(_other_element_ids):
                    db.session.add(SavedDiagramElement(
                        diagram_id=diagram.id, element_id=element_id,
                        position_x=80 + (i % 3) * 260, position_y=80 + (i // 3) * 160,
                    ))
        db.session.flush()

        # Idempotency: a re-run against a database that already has the
        # hero diagram -- whichever elements or positions it currently
        # holds -- ends up with exactly the named cluster at the
        # layer-row positions above, every time.
        _hero_diagram = SavedDiagram.query.filter_by(name=_diagrams_hero_name).first()
        if _hero_diagram is not None:
            _hero_elements_by_element_id = {
                e.element_id: e
                for e in SavedDiagramElement.query.filter_by(diagram_id=_hero_diagram.id).all()
            }
            _wanted_ids = {
                elements[n].id for n in _hero_element_names if n in elements
            }
            if set(_hero_elements_by_element_id) != _wanted_ids:
                for el in list(_hero_elements_by_element_id.values()):
                    db.session.delete(el)
                db.session.flush()
                for row_index, row_names in enumerate(_hero_layers):
                    for col_index, el_name in enumerate(row_names):
                        el = elements.get(el_name)
                        if el is None:
                            continue
                        db.session.add(SavedDiagramElement(
                            diagram_id=_hero_diagram.id, element_id=el.id,
                            position_x=80 + col_index * 300,
                            position_y=80 + row_index * 220,
                            width=220, height=80,
                        ))
                db.session.flush()
                _hero_elements_by_element_id = {
                    e.element_id: e
                    for e in SavedDiagramElement.query.filter_by(diagram_id=_hero_diagram.id).all()
                }
            else:
                for row_index, row_names in enumerate(_hero_layers):
                    for col_index, el_name in enumerate(row_names):
                        el = elements.get(el_name)
                        pos = _hero_elements_by_element_id.get(el.id) if el else None
                        if pos is None:
                            continue
                        pos.position_x = 80 + col_index * 300
                        pos.position_y = 80 + row_index * 220
                        pos.width = 220
                        pos.height = 80
                db.session.flush()

            # SavedDiagramRelationship: one row per ArchiMateRelationship
            # that joins two elements in the hero cluster.
            _hero_ids_by_name = {
                n: elements[n].id for n in _hero_element_names if n in elements
            }
            _existing_diagram_rel_ids = {
                r.relationship_id
                for r in SavedDiagramRelationship.query.filter_by(diagram_id=_hero_diagram.id).all()
            }
            for source_name, target_name, rel_type in _hero_relationship_pairs:
                source_id = _hero_ids_by_name.get(source_name)
                target_id = _hero_ids_by_name.get(target_name)
                if source_id is None or target_id is None:
                    continue
                rel = ArchiMateRelationship.query.filter_by(
                    source_id=source_id, target_id=target_id, type=rel_type,
                    organization_id=org_id,
                ).first()
                if rel is None or rel.id in _existing_diagram_rel_ids:
                    continue
                db.session.add(SavedDiagramRelationship(
                    diagram_id=_hero_diagram.id, relationship_id=rel.id,
                ))
                _existing_diagram_rel_ids.add(rel.id)
            db.session.flush()

        # ── Duplicate detection: one real duplicate, then run it ───────────
        from app.models.application_portfolio import ApplicationComponent

        if not ApplicationComponent.query.filter_by(application_code="LQ-DOCMGMT-DUP").first():
            source = apps.get("Document Management")
            db.session.add(ApplicationComponent(
                name="Document Management",
                lifecycle_status="active", health_status="healthy",
                archimate_element_id=source.archimate_element_id if source else None,
                application_code="LQ-DOCMGMT-DUP",
                implementation_date=_dt.date(2023, 1, 10),
                go_live_date=_dt.date(2023, 4, 1),
                total_cost_of_ownership=60000, license_cost_annual=9000,
                infrastructure_cost_monthly=1800,
                business_criticality="medium", strategic_importance="medium",
            ))
            db.session.flush()

        try:
            from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
                UnifiedDuplicateDetectionService,
            )

            UnifiedDuplicateDetectionService().run_detection(
                similarity_threshold=0.55, strategy="fast"
            )
        except Exception as exc:  # pragma: no cover -- capture-time diagnostic only
            print(f"  duplicate detection run did not complete: {type(exc).__name__}: {exc}")

        # ── Investment priorities (application <-> capability mapping) ─────
        # Round 2 (MP-B1) widens this to full coverage across every
        # UnifiedCapability row -- see the dedicated block further down,
        # near the other investment-analysis fixes -- rather than just the
        # first 8, so this import stays here for the modules that still read
        # it directly above (duplicate detection's own capability linkage).
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )

        # ── Portfolio (portfolio.index reads EnterpriseInitiative, NOT
        #    PortfolioInitiative -- two separate, pre-existing models) ──────
        from app.models.vendor.vendor_organization import EnterpriseInitiative

        initiative_rows = [
            ("Calibration Modernisation", "LQ-INIT-CAL", "active", "green", "implementation",
             62, 450000, 320000, "Chief Operations Officer"),
            ("Field Service Transformation", "LQ-INIT-FSV", "active", "yellow", "design",
             35, 280000, 95000, "Head of Engineering"),
            ("Event Backbone Hardening", "LQ-INIT-EVT", "on_hold", "red", "planning",
             10, 210000, 40000, "Shared Services Director"),
            ("Supplier Optimisation", "LQ-INIT-SUP", "completed", "green", "closure",
             100, 95000, 92000, "Regulatory Compliance Officer"),
        ]
        for name, code, status, health, phase, pct, budget, spent, sponsor in initiative_rows:
            if EnterpriseInitiative.query.filter_by(code=code).first():
                continue
            db.session.add(EnterpriseInitiative(
                name=name, code=code, status=status, health_status=health,
                current_phase=phase, completion_percentage=pct,
                approved_budget=budget, spent_to_date=spent,
                executive_sponsor=sponsor,
                planned_end_date=_dt.datetime.utcnow() + _dt.timedelta(days=180),
            ))
        db.session.flush()

        # ── Vendor catalogue + contracts (vendors, procurement.renewals) ───
        # VendorOrganization is DELIBERATELY not tenant-scoped (ADR-0003): a
        # shared, name-unique reference catalogue. This capture runs against
        # an isolated, throwaway database made only to render these static
        # images -- never a shared or production database -- so adding
        # fictional entries here cannot leak into any real install. Names
        # are invented the same way the rest of the seeded cast is.
        from app.models.application_portfolio import VendorContract
        from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

        vendor_rows = [
            ("Meridian Sensor Systems", "software_vendor", "Calibration Ledger"),
            ("Calderbrook Cloud Services", "cloud_provider", "Event Transport"),
            ("Northgate Field Equipment", "systems_integrator", "Field Service Scheduler"),
        ]
        vendor_ids = []
        for name, vendor_type, product_name in vendor_rows:
            vendor = VendorOrganization.query.filter_by(name=name).first()
            if vendor is None:
                vendor = VendorOrganization(name=name, display_name=name, vendor_type=vendor_type)
                db.session.add(vendor)
                db.session.flush()
                db.session.add(VendorProduct(
                    vendor_organization_id=vendor.id, name=product_name,
                ))
            vendor_ids.append(vendor.id)
        db.session.flush()

        today = _dt.date.today()
        contract_rows = [
            ("Calibration Ledger support contract", today.replace(year=today.year - 1),
             today + _dt.timedelta(days=15), vendor_ids[0]),
            ("Event Transport hosting agreement", today.replace(year=today.year - 2),
             today + _dt.timedelta(days=45), vendor_ids[1]),
            ("Field Service Scheduler licence", today.replace(year=today.year - 1),
             today + _dt.timedelta(days=100), vendor_ids[2]),
            ("Calibration Ledger disaster-recovery add-on",
             today.replace(year=today.year - 1), today + _dt.timedelta(days=200), vendor_ids[0]),
        ]
        for name, start, renewal, vendor_id in contract_rows:
            if VendorContract.query.filter_by(contract_name=name).first():
                continue
            db.session.add(VendorContract(
                contract_name=name, start_date=start, renewal_date=renewal,
                status="active", vendor_id=vendor_id, organization_id=org_id,
            ))
        db.session.flush()

        # ── Work packages (enterprise.work_packages reads WorkPackage, NOT
        #    UnifiedWorkPackage -- two separate, pre-existing models) ──────
        from app.models.implementation_migration import WorkPackage

        wp_rows = [
            ("Deploy automated calibration rigs", "in_progress", "critical", 65),
            ("Integrate calibration ledger with rigs", "in_progress", "high", 80),
            ("Build digital twin of the calibration lab", "planned", "high", 0),
            ("Procure handheld field testers", "completed", "high", 100),
            ("Migrate event backbone to the cloud", "in_progress", "high", 40),
            ("Consolidate the supplier base", "in_progress", "medium", 55),
        ]
        for name, status, priority, pct in wp_rows:
            if WorkPackage.query.filter_by(name=name).first():
                continue
            db.session.add(WorkPackage(
                name=name, status=status, priority=priority, percent_complete=pct,
                target_date=_dt.datetime.utcnow() + _dt.timedelta(days=90),
            ))
        db.session.flush()

        # ── Impact analysis results (enterprise.impact_analysis, the
        #    "what breaks" use-case video's first step -- /strategic/
        #    impact-analysis needs a live element search + button click to
        #    show anything, which a static capture can't do; this route
        #    instead reads stored ImpactAnalysisResult rows, no interaction
        #    needed) ───────────────────────────────────────────────────────
        from app.models.traceability import ImpactAnalysisResult

        affected_app = apps.get("Event Relay") or next(iter(apps.values()), None)
        impact_rows = [
            ("change_impact", "application", "critical", 5, 3, 7),
            ("change_impact", "application", "high", 3, 2, 4),
            ("retirement_impact", "application", "medium", 1, 1, 2),
        ]
        existing_impact_count = ImpactAnalysisResult.query.filter_by(
            created_by_id=demo_user_id
        ).count()
        if existing_impact_count == 0 and affected_app is not None:
            # Round 2 (MP-B1): this loop's 4th variable was itself named
            # "caps" (an int count, not the capability dict), silently
            # shadowing the outer `caps = _resolve_caps(org_id)` dict for
            # the rest of the function -- every block added below that reads
            # `caps` (business capabilities, investment analysis, value
            # streams) crashed with "'int' object has no attribute 'values'"
            # the moment it ran after this loop. Renamed; behaviour unchanged.
            for analysis_type, elem_type, severity, affected_caps_count, apps_count, procs in impact_rows:
                db.session.add(ImpactAnalysisResult(
                    analysis_type=analysis_type, trigger_element_type=elem_type,
                    trigger_element_id=affected_app.id, overall_severity=severity,
                    affected_capabilities_count=affected_caps_count, affected_applications_count=apps_count,
                    affected_processes_count=procs, created_by_id=demo_user_id,
                ))
        db.session.flush()

        # ── Solutions (solution_design.list_solutions) ─────────────────────
        from app.models.solution_models import Solution

        solution_rows = [
            ("Calibration Automation Platform", "Platform", "Operations", "Complex", "in_progress"),
            ("Supplier Integration Hub", "Integration", "Supply Chain", "Moderate", "planned"),
            ("Field Service Mobile App", "Product", "Customer", "Moderate", "deployed"),
            ("Event Backbone Migration", "Platform", "Operations", "Complex", "in_progress"),
        ]
        for name, solution_type, domain, complexity, status in solution_rows:
            if Solution.query.filter_by(name=name).first():
                continue
            db.session.add(Solution(
                name=name, solution_type=solution_type, business_domain=domain,
                complexity_level=complexity, status=status,
                governance_status="proposed" if status == "planned" else "approved",
                solution_owner="Demo User", created_by_id=demo_user_id,
            ))
        db.session.flush()

        # ── Value streams (value_stream.index) ──────────────────────────────
        from app.models.unified_capability import ValueStream

        vs_rows = [
            ("Order to Cash", "OTC", "customer_facing", "Calibration Specialist"),
            ("Calibration to Certificate", "C2C", "customer_facing", "Quality Assurance Lead"),
            ("Procure to Pay", "P2P", "internal", "Procurement Officer"),
            ("Incident to Resolution", "I2R", "supporting", "IT Operations Lead"),
        ]
        for name, code, vs_type, owner in vs_rows:
            if ValueStream.query.filter_by(code=code).first():
                continue
            db.session.add(ValueStream(
                name=name, code=code, value_stream_type=vs_type,
                business_owner=owner, strategic_importance="high",
            ))
        db.session.flush()

        # No ConnectorConfig seeding: app/services/public_pages.py's
        # MODULE_CAPTURES explains why (api_list_connectors 500s on any
        # row). sage.itops keeps her Administrator role grant above anyway
        # -- harmless, and ready for whenever that bug is fixed and this
        # module's capture_status goes back to live.

        # ── Org chart (organization.chart reads BusinessActor + Composition/
        #    Aggregation ArchiMateRelationship) ─────────────────────────────
        # Round 2 (MP-B1): round 1's capture showed the module's hub page
        # (three link cards) because no BusinessActor hierarchy existed to
        # draw -- build_org_tree() falls back to a flat list with zero
        # relationships. BusinessActor auto-creates its own linked
        # ArchiMateElement on insert (see the model's own docstring), so the
        # composition edges below are added once each actor's element exists.
        from app.models.business_layer import BusinessActor

        _actor_rows = [
            # (name, actor_type, parent_name, headcount, location)
            ("Lantern Quay Systems", "Business Unit", None, 420, "Head Office"),
            ("Field Operations", "Department", "Lantern Quay Systems", 160, "Bristol Depot"),
            ("Calibration Services Team", "Team", "Field Operations", 48, "Bristol Depot"),
            ("Field Engineering Team", "Team", "Field Operations", 62, "Regional Depots"),
            ("Quality & Compliance", "Department", "Lantern Quay Systems", 38, "Head Office"),
            ("Quality Assurance Team", "Team", "Quality & Compliance", 22, "Head Office"),
            ("Technology", "Department", "Lantern Quay Systems", 74, "Head Office"),
            ("Platform Engineering Team", "Team", "Technology", 34, "Head Office"),
            ("Data & Architecture Team", "Team", "Technology", 18, "Head Office"),
            ("Commercial", "Department", "Lantern Quay Systems", 90, "Head Office"),
            ("Procurement Team", "Team", "Commercial", 16, "Head Office"),
            ("Customer Success Team", "Team", "Commercial", 28, "Head Office"),
        ]
        actors_by_name = {a.name: a for a in BusinessActor.query.all()}
        for name, actor_type, _parent_name, headcount, location in _actor_rows:
            if name in actors_by_name:
                continue
            actor = BusinessActor(
                name=name, actor_type=actor_type, headcount=headcount, location=location,
            )
            db.session.add(actor)
            db.session.flush()
            actors_by_name[name] = actor

        from app.models.archimate_core import ArchiMateRelationship

        existing_actor_rels = {
            (rel.source_id, rel.target_id, rel.type)
            for rel in ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id.in_(
                    [a.archimate_element_id for a in actors_by_name.values()
                     if a.archimate_element_id is not None]
                )
            ).all()
        }
        for name, _actor_type, parent_name, _headcount, _location in _actor_rows:
            if parent_name is None:
                continue
            parent = actors_by_name.get(parent_name)
            child = actors_by_name.get(name)
            if parent is None or child is None:
                continue
            if parent.archimate_element_id is None or child.archimate_element_id is None:
                continue
            key = (parent.archimate_element_id, child.archimate_element_id, "composition")
            if key in existing_actor_rels:
                continue
            db.session.add(ArchiMateRelationship(
                type="composition",
                source_id=parent.archimate_element_id,
                target_id=child.archimate_element_id,
                organization_id=org_id,
            ))
            existing_actor_rels.add(key)
        db.session.flush()

        # ── Business capabilities (capability-maturity.heatmap reads
        #    BusinessCapability directly; roadmap_routes.py's gap detection
        #    reads the same table via ApplicationCapabilityCoverage) ───────
        # Round 2 (MP-B1): round 1 only ever populated UnifiedCapability (a
        # separate, later capability store -- see that model's own comment on
        # business_capability being the projection's SOURCE, not a read
        # target). capability-maturity, gap-analysis and roadmaps all read
        # BusinessCapability, which stayed empty, so the heatmap showed "No
        # capabilities yet" and the gap detector had nothing to flag. Rows
        # below are a real, additional business-capability catalogue -- not a
        # duplicate of UnifiedCapability, which keeps representing the
        # portfolio's capability-to-application investment view.
        from app.models.business_capabilities import ApplicationCapabilityCoverage, BusinessCapability

        apps_list = list(apps.values())
        _biz_cap_rows = [
            # (name, domain, strategic_importance, current, target, coverage_apps)
            # coverage_apps: list of (app_name_or_None, strategic_importance_override,
            # lifecycle_status_override, technical_risk_override) -- empty list -> coverage gap.
            ("Calibration Service Delivery", "Operations", "critical", 4, 5,
             [("Calibration Ledger", "critical", None, None)]),
            ("Field Service Scheduling", "Operations", "high", 2, 4,
             [("Field Service Scheduler", "low", None, None)]),
            # These four used to carry no assessed maturity at all,
            # which rendered as an all-dash row rather than a coverage gap --
            # the gap these capabilities are meant to show is the empty
            # coverage_apps list below, not a missing assessment. Each now
            # gets a real current/target pair like every other row.
            ("Supplier Risk Management", "Procurement", "high", 1, 4, []),
            ("Regulatory Compliance Tracking", "Compliance", "critical", 3, 5,
             [("Document Management", "medium", "deprecated", None)]),
            ("Equipment Asset Management", "Operations", "medium", 2, 4,
             [("Inventory Manager", "medium", None, "critical")]),
            ("Customer Contract Administration", "Commercial", "medium", 2, 4, []),
            ("Quality Assurance & Certification", "Quality", "critical", 4, 5,
             [("Calibration Ledger", "high", None, None), ("Document Management", "high", None, None)]),
            ("Data & Integration Architecture", "Technology", "high", 1, 4, []),
            ("Workforce Planning", "People", "medium", 2, 3,
             [("Event Relay", "low", None, None)]),
            ("Financial Planning & Reporting", "Finance", "medium", 2, 3, []),
            ("Incident & Problem Management", "Operations", "high", 2, 4,
             [("Event Relay", "medium", None, "high")]),
            ("Vendor Performance Management", "Procurement", "medium", 3, 4,
             [("Document Management", "critical", None, None)]),
        ]
        biz_caps_by_name = {c.name: c for c in BusinessCapability.query.all()}
        today_dt = _dt.datetime.utcnow()
        for i, (name, domain, importance, current, target, coverage) in enumerate(_biz_cap_rows):
            cap = biz_caps_by_name.get(name)
            if cap is None:
                cap = BusinessCapability(
                    name=name, code=f"CAP-LQ-{i + 1:03d}", category=domain,
                    business_domain=domain, strategic_importance=importance,
                    current_maturity_level=current, target_maturity_level=target,
                    maturity_assessment_date=(today_dt - _dt.timedelta(days=30)) if current else None,
                    business_owner="Lantern Quay capability owner",
                )
                db.session.add(cap)
                db.session.flush()
                biz_caps_by_name[name] = cap
            if ApplicationCapabilityCoverage.query.filter_by(capability_id=cap.id).first():
                continue
            for app_name, importance_override, lifecycle_override, risk_override in coverage:
                app_row = apps.get(app_name)
                if app_row is None:
                    continue
                if importance_override is not None:
                    app_row.strategic_importance = importance_override
                if lifecycle_override is not None:
                    app_row.lifecycle_status = lifecycle_override
                if risk_override is not None:
                    app_row.technical_risk = risk_override
                db.session.add(ApplicationCapabilityCoverage(
                    application_component_id=app_row.id, capability_id=cap.id,
                    support_level="full", coverage_percentage=80.0,
                ))
        db.session.flush()

        # ── Roadmap: link seeded work packages to plateaus and gaps ─────────
        # The plateau narrative (name, description, target date) has
        # one source now -- app/commands/seed_demo_company.py's own
        # _PLATEAUS -- so this block only wires existing work packages to
        # the three plateaus and to a stored gap each, through the one
        # work-package writer (work_package_service.update_work_package).
        # Nothing here writes unified_work_packages.plateau_id/gap_id or any
        # association table directly.
        from app.models.implementation_migration import Gap, Plateau
        from app.services import work_package_service

        plateaus_by_name = {
            p.name: p for p in Plateau.query.filter_by(organization_id=org_id).all()
        }
        gaps_by_name = {
            g.name: g for g in Gap.query.filter_by(organization_id=org_id).all()
        }
        wps_by_name = {wp.name: wp for wp in work_package_service.query_for(org_id).all()}

        # (work package name, plateau name, gap name) -- each work package
        # addresses exactly one gap, and the per-plateau counts (2, 4, 3) are
        # deliberately uneven so the roadmap's "gap(s) assigned" figures are
        # not a row of identical numbers.
        _ROADMAP_WORK_LINKS = [
            ("Procure handheld field testers", "Baseline Architecture", "Field service coverage gap"),
            ("Deploy mobile field app", "Baseline Architecture", "Monitoring coverage gap"),
            ("Integrate calibration ledger with rigs", "Transition State 1", "Calibration throughput gap"),
            ("Migrate event backbone to cloud", "Transition State 1", "Event backbone resilience gap"),
            ("Consolidate supplier base", "Transition State 1", "Supplier performance gap"),
            ("Deploy inventory reconciliation tool", "Transition State 1", "Inventory visibility gap"),
            ("Build digital twin of calibration lab", "Target Architecture", "Data quality gap"),
            ("Upgrade compliance reporting module", "Target Architecture", "Documentation currency gap"),
            ("Build predictive maintenance model", "Target Architecture", "Integration coverage gap"),
        ]
        _existing_plateau_links = work_package_service.plateau_work_package_ids(
            [p.id for p in plateaus_by_name.values()], org_id
        )
        for wp_name, plateau_name, gap_name in _ROADMAP_WORK_LINKS:
            wp = wps_by_name.get(wp_name)
            plateau = plateaus_by_name.get(plateau_name)
            gap = gaps_by_name.get(gap_name)
            if wp is None or plateau is None or gap is None:
                continue
            if wp.id in _existing_plateau_links.get(plateau.id, set()):
                continue
            work_package_service.update_work_package(
                wp.id, organization_id=org_id, plateau_id=plateau.id, gap_id=gap.id,
            )
        db.session.flush()

        # ── Investment analysis (strategic.investment_matrix reads
        #    UnifiedCapability.industry_domain and UnifiedApplicationCapabilityMapping) ──
        # Round 2 (MP-B1): round 1 mapped only the first 8 of 24
        # UnifiedCapability rows to an application, and never set
        # industry_domain on any of them -- so investment_prioritization_service's
        # capability_domain fell through to "Unknown" and coverage_count read 0
        # on every OTHER row (and on these 8 too, once the matrix is read
        # fresh, since getattr(capability, 'business_domain', ...) is not a
        # real column on this model). Every capability gets a domain and at
        # least one mapped application below; a few are deliberately left
        # under-covered (1 app, or a lower-strategic one) so the matrix still
        # shows a believable spread of priorities rather than "fully solved".
        _domain_cycle = [
            "Operations", "Quality", "Technology", "Commercial", "Procurement",
            "Finance", "People", "Compliance",
        ]
        cap_list_all = list(caps.values())
        for i, cap_row in enumerate(cap_list_all):
            if not cap_row.industry_domain:
                cap_row.industry_domain = _domain_cycle[i % len(_domain_cycle)]
        db.session.flush()

        if apps_list:
            for i, cap_row in enumerate(cap_list_all):
                wanted_apps = [apps_list[i % len(apps_list)]]
                if i % 3 != 2:  # leave roughly a third single-covered
                    wanted_apps.append(apps_list[(i + 5) % len(apps_list)])
                for app_row in wanted_apps:
                    exists = UnifiedApplicationCapabilityMapping.query.filter_by(
                        application_component_id=app_row.id, unified_capability_id=cap_row.id,
                    ).first()
                    if exists:
                        continue
                    db.session.add(UnifiedApplicationCapabilityMapping(
                        application_component_id=app_row.id, unified_capability_id=cap_row.id,
                    ))
        db.session.flush()

        # ── Value streams (value_stream.detail reads ValueStreamStage +
        #    CapabilityValueStreamMapping -- the BIZBOK stage/capability grid) ──
        # Round 2 (MP-B1): the 4 ValueStream rows from round 1 had no stages
        # and no capability mappings, so every stream's own detail page (the
        # screen the content page actually points at) showed 0 of both.
        from app.models.unified_capability import ValueStream, ValueStreamStage, CapabilityValueStreamMapping

        _vs_stage_rows = {
            "OTC": ["Order Received", "Order Scheduled", "Calibration Performed", "Invoice Issued"],
            "C2C": ["Sample Received", "Calibration Executed", "Certificate Issued"],
            "P2P": ["Requisition Raised", "Supplier Selected", "Goods Received", "Invoice Paid"],
            "I2R": ["Incident Logged", "Triaged", "Resolved"],
        }
        value_streams_by_code = {v.code: v for v in ValueStream.query.all()}
        # The old offset used Python's own string hash of the
        # stream code, which is randomised per process, so which capability
        # mapped to which stage was different on every recapture. Sorted by
        # (code, id) is the same order on every run; the stream's own
        # position in _vs_stage_rows is the offset, so the four streams
        # don't all start from the same capability either.
        cap_list_sorted = sorted(cap_list_all, key=lambda c: (c.code, c.id))
        _vs_codes_ordered = list(_vs_stage_rows.keys())
        for code, stage_names in _vs_stage_rows.items():
            stream_offset = _vs_codes_ordered.index(code)
            vs_row = value_streams_by_code.get(code)
            if vs_row is None:
                continue
            existing_stages_by_name = {
                s.name: s for s in ValueStreamStage.query.filter_by(value_stream_id=vs_row.id).all()
            }
            for order, stage_name in enumerate(stage_names, start=1):
                stage = existing_stages_by_name.get(stage_name)
                if stage is None:
                    stage = ValueStreamStage(
                        name=stage_name, value_stream_id=vs_row.id, stage_order=order,
                        stage_type="operational",
                    )
                    db.session.add(stage)
                    db.session.flush()
                # Round 2 (MP-B1), second pass: this used to sit inside the
                # "stage didn't exist yet" branch above, so a database that
                # already had these stages (e.g. from an earlier capture run,
                # before UnifiedCapability's write-time projection was caught
                # up -- see apply-unified-capability-provenance-migration /
                # project-capabilities above) would skip ever creating the
                # capability link, leaving every stream's own "N capabilities"
                # stuck at 0 even once cap_list_all stopped being empty. The
                # mapping's own existence is checked independently of the
                # stage's, so a re-run always catches up.
                if cap_list_sorted and not CapabilityValueStreamMapping.query.filter_by(
                    value_stream_stage_id=stage.id
                ).first():
                    cap_row = cap_list_sorted[(order + stream_offset) % len(cap_list_sorted)]
                    db.session.add(CapabilityValueStreamMapping(
                        capability_id=cap_row.id, value_stream_id=vs_row.id,
                        value_stream_stage_id=stage.id, support_type="primary",
                    ))
        db.session.flush()

        # ── Application rationalization scores (unified_applications.
        #    rationalization_workbench reads ApplicationRationalizationScore) ──
        # Round 2 (MP-B1): round 1 ran duplicate detection (the Rationalization
        # dashboard's own stepper) but never scored any application, so the
        # Decision Workbench -- the screen the content page's capture
        # actually needs -- stayed on its "No scored applications found, run
        # detection from the Dashboard" onboarding panel regardless.
        from app.models.application_rationalization import ApplicationRationalizationScore
        from app.services.rationalization_scoring_service import RationalizationScoringService

        # The Disposition Breakdown panel and the "Decision Ready" /
        # "Insufficient Evidence" tiles read disposition_action and
        # is_decision_ready (rationalization_api_routes.py ~228, ~1459), not
        # rationalization_action -- these hand-written rows previously set
        # neither, so the panel rendered empty regardless of the TIME
        # action already shown elsewhere on the page.
        #
        # is_decision_ready is computed here by calling the product's own
        # RationalizationScoringService.evaluate_readiness() against each
        # seeded application -- the same call POST
        # /applications/rationalization/api/bulk-score makes -- rather than
        # set the flag directly, so the dashboard's readiness tiles reflect
        # an application's actual recorded fields (owner, lifecycle, risk):
        # seed_demo_company() now sets application_owner and lifecycle_status
        # on every application and technical_risk/business_risk on the ones a
        # real company would already have assessed (its strategic
        # applications and the ones already marked for sunset or
        # decommissioning), leaving the plain tactical workhorses without a
        # risk assessment the way an in-progress real inventory would. That
        # produces a genuine, data-driven split between decision-ready and
        # insufficient-evidence applications rather than a uniform flag.
        _time_actions = ["TOLERATE", "INVEST", "MIGRATE", "ELIMINATE"]
        _time_to_disposition = {
            "TOLERATE": "retain", "INVEST": "refactor",
            "MIGRATE": "replace", "ELIMINATE": "retire",
        }
        _priorities = ["critical", "high", "medium", "low"]
        # Applications already marked for sunset or decommissioning score low
        # on health rather than following the general index-based curve below
        # -- an application a company is retiring is not, in reality, a
        # healthy one, and this is what gives the Health Distribution panel's
        # "Critical" bucket a genuine, non-zero entry.
        _SUNSET_LIFECYCLES = {"3. sunset", "5. decommissioned"}
        for i, app_row in enumerate(apps_list):
            if ApplicationRationalizationScore.query.filter_by(
                application_component_id=app_row.id
            ).first():
                continue
            profile = _APP_PROFILE.get(app_row.name)
            is_sunsetting = bool(profile) and profile[0] in _SUNSET_LIFECYCLES
            if is_sunsetting:
                action = "ELIMINATE"
                technical_score = 8 + (i * 3) % 14
                business_score = 10 + (i * 5) % 14
                cost_score = 12 + (i * 7) % 12
                vendor_score = 10 + (i * 2) % 14
                overall_score = round(
                    (technical_score + business_score + cost_score + vendor_score) / 4
                )
            else:
                action = _time_actions[i % len(_time_actions)]
                technical_score = 40 + (i * 7) % 55
                business_score = 35 + (i * 11) % 60
                cost_score = 30 + (i * 13) % 65
                vendor_score = 45 + (i * 5) % 50
                overall_score = 38 + (i * 9) % 58

            readiness = RationalizationScoringService.evaluate_readiness(app_row)
            disposition = (
                _time_to_disposition[action]
                if readiness["is_decision_ready"]
                else "insufficient_evidence"
            )
            db.session.add(ApplicationRationalizationScore(
                application_component_id=app_row.id,
                technical_health_score=technical_score,
                business_value_score=business_score,
                cost_efficiency_score=cost_score,
                vendor_risk_score=vendor_score,
                overall_health_score=overall_score,
                rationalization_action=action,
                disposition_action=disposition,
                is_decision_ready=readiness["is_decision_ready"],
                readiness_dimensions=readiness["dimensions"],
                readiness_score=readiness["readiness_score"],
                priority=_priorities[i % len(_priorities)],
                priority_score=max(10, 90 - i * 4),
                action_rationale=f"{action.title()}: scored from technical health, business "
                                 f"value, cost efficiency and vendor risk signals.",
                review_status="reviewed" if i % 2 == 0 else "draft",
            ))
        db.session.flush()

        # ── Consolidation list entries (rationalization dashboard's "Plan" /
        #    "Roadmap" / "Track" pipeline stepper reads ConsolidationListEntry,
        #    a separate table from the scores above) ──────────────────────
        # Round 3 (MP-B1): the three applications already marked for sunset
        # or decommissioning above are exactly the ones a real consolidation
        # effort would already have logged -- this adds one entry per
        # application rather than inventing unrelated ones, so Plan's count
        # and Roadmap's count/total stop reading zero against a non-zero
        # scored portfolio.
        from app.models.consolidation_list import ConsolidationListEntry

        _CONSOLIDATION_ENTRIES = [
            # (app_name, recommended_action, status, notes)
            ("Document Management", "modernize", "under_review",
             "Sunsetting in favour of a managed records service; still used "
             "for compliance retrieval until the replacement is live."),
            ("Alert Manager", "decommission", "approved",
             "Decommissioned platform service; alerting now routes through "
             "Monitoring Dashboard."),
            ("Reporting Engine", "add_to_roadmap", "identified",
             "Sunsetting; reporting workloads are being planned onto the "
             "lakehouse migration already on the roadmap."),
        ]
        for app_name, recommended_action, status, notes in _CONSOLIDATION_ENTRIES:
            app_row = apps.get(app_name)
            if app_row is None:
                continue
            if ConsolidationListEntry.query.filter_by(application_id=app_row.id).first():
                continue
            db.session.add(ConsolidationListEntry(
                application_id=app_row.id,
                source_type="manual",
                recommended_action=recommended_action,
                priority="medium",
                status=status,
                notes=notes,
            ))
        db.session.flush()

        db.session.commit()

    # ── Globally-scoped data: no tenant_scope, not a seeded script concern ──
    # Industry APQC frameworks (industry_apqc.industry_apqc_dashboard): the
    # standard default set the product's own "Seed Default Frameworks"
    # button would create -- not fictional data, calling the same service
    # function the button calls.
    from app.services.industry_apqc_service import IndustryAPQCService

    IndustryAPQCService().seed_default_frameworks()

    # Round 2 (MP-B1): seed_default_frameworks() above only creates the
    # framework rows themselves -- industry_apqc_dashboard's own per-framework
    # stats (get_industry_statistics) count IndustryAPQCProcess rows, which
    # stayed at zero, so every framework showed "0 processes". Each framework
    # already carries its own real unique_process_areas/key_differentiators
    # (APQC's own published category names, not invented here); this reuses
    # that existing text as the process catalogue rather than writing a
    # second list of names.
    from app.models.industry_apqc import IndustryAPQCFramework, IndustryAPQCProcess

    _CROSS_INDUSTRY_PROCESSES = [
        "Develop vision and strategy",
        "Develop and manage products and services",
        "Market and sell products and services",
        "Deliver products and services",
        "Manage customer service",
        "Develop and manage human capital",
        "Manage information technology",
        "Manage financial resources",
    ]
    # "Regulatory Processes" (get_regulatory_processes(), read via
    # IndustryAPQCProcess.regulatory_requirements.isnot(None)) needs a real
    # SQL NULL on the non-regulatory rows, not a Python None and not an
    # omitted value -- omitting the keyword argument stores the column's own
    # default (an empty list, from `default=list`), and passing Python None
    # into a JSON column serialises it as the JSON literal `null`, and both
    # of those still satisfy `isnot(None)`, which is why every row read as
    # regulatory before. `sqlalchemy.null()` is the one way to write an
    # actual SQL NULL into a JSON column, so it is the one way the
    # IS NOT NULL filter can tell the two kinds of row apart. Which rows are
    # regulatory is decided by a fixed keyword rule on the process name,
    # applied the same way to every framework.
    from sqlalchemy import null as sa_null

    _REGULATORY_KEYWORDS = (
        "compliance", "regulat", "safety", "quality", "audit",
        "certif", "licens", "environment",
    )
    for framework in IndustryAPQCFramework.query.all():
        if IndustryAPQCProcess.query.filter_by(industry_framework_id=framework.id).first():
            continue
        process_names = (
            list(framework.unique_process_areas or [])
            + list(framework.key_differentiators or [])
        ) or _CROSS_INDUSTRY_PROCESSES
        for i, process_name in enumerate(process_names):
            is_regulatory = any(
                kw in process_name.lower() for kw in _REGULATORY_KEYWORDS
            )
            db.session.add(IndustryAPQCProcess(
                industry_framework_id=framework.id,
                base_process_id=None,
                industry_process_code=f"{framework.industry_code}-{i + 1:02d}",
                industry_process_name=process_name,
                industry_category_1=framework.industry_name,
                is_industry_unique=process_name in (framework.unique_process_areas or []),
                regulatory_requirements=(
                    [f"{framework.industry_name} regulatory requirement: {process_name}"]
                    if is_regulatory else sa_null()
                ),
            ))
    db.session.commit()

    # Every capability gets an assessed current and target maturity
    # pair (see _biz_cap_rows above) -- BusinessDomain rows from the one
    # canonical domain list (UNIFIED_CAPABILITY_DOMAINS) feed the roadmap's
    # own "Domains" tile the same way. Insert-if-missing on code, the same
    # fields app/commands/seed_capabilities.py's seed_unified_caps() writes
    # at its own domain-creation step -- that function is not called here,
    # since it also retires and creates capabilities, which is out of scope.
    from app.commands.seed_capabilities import UNIFIED_CAPABILITY_DOMAINS
    from app.models.unified_capability import BusinessDomain

    existing_domain_codes = {d.code for d in BusinessDomain.query.all()}
    for dom_data in UNIFIED_CAPABILITY_DOMAINS:
        if dom_data["code"] in existing_domain_codes:
            continue
        db.session.add(BusinessDomain(
            code=dom_data["code"],
            name=dom_data["name"],
            description=dom_data.get("description", ""),
            domain_type=dom_data.get("domain_type", "primary"),
            strategic_focus=dom_data.get("strategic_focus", ""),
            domain_owner=dom_data.get("domain_owner", ""),
        ))
    db.session.commit()

    # Regulatory compliance frameworks (dashboard/compliance): standard,
    # real framework/control names (GDPR, ISO 27001 -- not fictional data,
    # the same two the page's own empty-state copy names as examples).
    # RegulatoryFrameworkService.seed_manufacturing_frameworks() is the
    # product's own default-seed path for this, but its hardcoded fallback
    # (used when app/data/compliance_frameworks.json is absent, as it is
    # here) passes ComplianceControl a control_id kwarg that model doesn't
    # have -- a pre-existing bug in that fallback, out of this brief's
    # scope to fix. Insert directly instead, with the model's real fields.
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    framework_rows = [
        ("GDPR", "General Data Protection Regulation", "privacy", "EU", "mandatory",
         [("Art.30", "Records of processing activities"),
          ("Art.32", "Security of processing")]),
        ("ISO-27001", "ISO 27001 Information Security", "security", "Global", "recommended",
         [("A.9.2.1", "User access provisioning"),
          ("A.12.1.2", "Change management")]),
    ]
    for code, name, category, jurisdiction, enforcement, controls in framework_rows:
        framework = RegulatoryFramework.query.filter_by(code=code).first()
        if framework is None:
            framework = RegulatoryFramework(
                code=code, name=name, category=category,
                jurisdiction=jurisdiction, enforcement_level=enforcement,
            )
            db.session.add(framework)
            db.session.flush()
        for control_code, title in controls:
            if ComplianceControl.query.filter_by(
                framework_id=framework.id, control_code=control_code
            ).first():
                continue
            db.session.add(ComplianceControl(
                framework_id=framework.id, control_code=control_code, title=title,
            ))
    db.session.commit()

    # Batch import jobs (batch-import.dashboard): scoped by user_id only, no
    # organization_id column on this model at all.
    #
    # Round 2 (MP-B1): round 1's rows left total_batches/batches_completed at
    # their 0 defaults, so BatchImportJob.progress_percentage (batches_completed
    # / total_batches) read 0/0 -> 0% on every job, and no BatchImportBatch rows
    # existed at all, so list_jobs()'s elementsGenerated (SUM of
    # BatchImportBatch.total_elements_generated) read 0 too -- both genuinely
    # empty fields, not a product bug. Batches are added below, keyed by each
    # job's own filename so a re-run stays idempotent.
    from app.models.batch_import import BatchImportBatch, BatchImportJob, BatchStatus

    job_rows = [
        ("lantern-quay-applications.xlsx", 20, "completed", 4, 4,
         [("completed", 5, 12), ("completed", 5, 15), ("completed", 5, 10), ("completed", 5, 14)]),
        ("lantern-quay-capabilities.csv", 24, "completed", 5, 5,
         [("completed", 5, 8), ("completed", 5, 9), ("completed", 5, 7),
          ("completed", 5, 11), ("completed", 4, 6)]),
        ("lantern-quay-risk-register.csv", 10, "processing", 3, 1,
         [("completed", 4, 9), ("processing", 3, 0), ("queued", 3, 0)]),
        ("lantern-quay-vendor-contracts.csv", 15, "completed", 3, 3,
         [("completed", 5, 13), ("completed", 5, 11), ("completed", 5, 9)]),
        ("lantern-quay-work-packages.xlsx", 12, "completed", 3, 3,
         [("completed", 4, 10), ("completed", 4, 8), ("completed", 4, 12)]),
        ("lantern-quay-solutions.csv", 8, "failed", 2, 1,
         [("completed", 4, 6), ("failed", 4, 0)]),
    ]
    _batch_status = {
        "completed": BatchStatus.COMMITTED,
        "processing": BatchStatus.PROCESSING,
        "queued": BatchStatus.QUEUED,
        "failed": BatchStatus.FAILED,
    }
    for filename, total_apps, status, total_batches, batches_completed, batch_rows in job_rows:
        job = BatchImportJob.query.filter_by(filename=filename, user_id=demo_user_id).first()
        if job is None:
            job = BatchImportJob(
                user_id=demo_user_id, filename=filename, total_applications=total_apps,
                status=status, total_batches=total_batches, batches_completed=batches_completed,
                batches_committed=batches_completed if status == "completed" else 0,
            )
            db.session.add(job)
            db.session.flush()
        if len(job.batches) == 0:
            for batch_number, (batch_status, apps_in_batch, elements_generated) in enumerate(
                batch_rows, start=1
            ):
                db.session.add(BatchImportBatch(
                    job_id=job.id, batch_number=batch_number,
                    status=_batch_status[batch_status],
                    total_applications=apps_in_batch,
                    processed_applications=apps_in_batch if batch_status != "queued" else 0,
                    successful_applications=apps_in_batch if batch_status == "completed" else 0,
                    total_elements_generated=elements_generated,
                ))
    db.session.commit()


def _check_seed_health(org_id: int) -> None:
    """Stop the run, loudly, before any image is written, if
    the data every captured screen depends on did not actually get seeded.

    A brand-new database's UnifiedCapability table stays silently empty
    unless the write-time projection's provenance index is created first --
    nothing in the capture pipeline's own bootstrap used to do that, so a
    recapture from a fresh database could ship an empty or wrong screen
    with no warning before the screenshots were written and committed.
    Every count below is something a committed image actually needs to be
    honest; a zero here means a later screenshot would be empty or wrong.
    """
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.archimate_core import SavedDiagram, SavedDiagramRelationship
    from app.models.business_capabilities import BusinessCapability
    from app.models.implementation_migration import Plateau
    from app.models.industry_apqc import IndustryAPQCProcess
    from app.models.unified_capability import BusinessDomain, UnifiedCapability
    from app.services import work_package_service

    failures = []

    def _check(label, count):
        if not count:
            failures.append(label)

    _check(
        "UnifiedCapability",
        UnifiedCapability.query.filter_by(organization_id=org_id).count(),
    )
    _check(
        "BusinessCapability",
        BusinessCapability.query.filter_by(organization_id=org_id).count(),
    )
    _check("BusinessDomain", BusinessDomain.query.count())
    plateaus = Plateau.query.filter_by(organization_id=org_id).all()
    _check(
        "Plateau with a description",
        sum(1 for p in plateaus if p.description and p.description.strip()),
    )
    linked_wp_ids = work_package_service.plateau_work_package_ids(
        [p.id for p in plateaus], org_id
    )
    _check(
        "unified work packages linked to a plateau",
        sum(len(ids) for ids in linked_wp_ids.values()),
    )
    _check(
        "ARBReviewItem",
        ARBReviewItem.query.filter_by(organization_id=org_id).count(),
    )
    _check(
        "ApplicationRationalizationScore with disposition_action set",
        ApplicationRationalizationScore.query.filter(
            ApplicationRationalizationScore.organization_id == org_id,
            ApplicationRationalizationScore.disposition_action.isnot(None),
        ).count(),
    )
    _check(
        "IndustryAPQCProcess with non-null regulatory_requirements",
        IndustryAPQCProcess.query.filter(
            IndustryAPQCProcess.regulatory_requirements.isnot(None)
        ).count(),
    )
    hero_diagram = SavedDiagram.query.filter_by(name="Calibration estate overview").first()
    _check(
        "SavedDiagramRelationship on the hero diagram",
        SavedDiagramRelationship.query.filter_by(
            diagram_id=hero_diagram.id if hero_diagram else -1
        ).count(),
    )

    if failures:
        raise SystemExit(
            "capture_screenshots.py --modules: refusing to capture -- the "
            "seed is missing data every committed screenshot depends on: "
            + "; ".join(failures)
            + ". Run the bootstrap (apply-unified-capability-provenance-migration, "
            "then project-capabilities --apply) and reseed before recapturing."
        )


def _seed_lantern_quay(demo_password: str) -> dict:
    """Seed Lantern Quay Systems and grant three capture-only persona roles.

    Calls the existing seed_demo_company() unchanged (app/commands/seed_demo_company.py
    stays untouched) then elevates three already-seeded owner accounts just
    enough to pass the role gate on the one page each needs -- Integrations
    (platform admin), My Applications (application_manager), Procurement
    (procurement). No new fictional person is invented: all three are already
    part of the seeded cast.

    Returns a dict of slug -> path for the one module capture whose real
    URL carries a database-generated id (a saved diagram, opened in the
    Composer via ?viewpoint_id=<id>) that cannot be a fixed string in
    MODULE_CAPTURES -- run_public_pages_capture() applies this to
    MODULE_CAPTURES before building its capture job list.
    """
    os.environ.setdefault("DEMO_USER_PASSWORD", demo_password)
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        Role.insert_roles()

        # The schema migration that creates the unique index
        # project-capabilities needs (so UnifiedCapability has somewhere to
        # record provenance) has to run before anything is seeded. Through
        # the Flask CLI runner -- never a copy of its SQL.
        runner = app.test_cli_runner()
        migration_result = runner.invoke(
            args=["apply-unified-capability-provenance-migration"]
        )
        if migration_result.exit_code != 0:
            raise SystemExit(
                "apply-unified-capability-provenance-migration failed:\n"
                + str(migration_result.output)
            )

        stats = seed_demo_company()
        print("seeded Lantern Quay Systems:", stats)

        admin_role = Role.query.filter_by(name="Administrator").first()
        if admin_role is None:
            raise SystemExit("Administrator role missing after Role.insert_roles()")

        sage = User.query.filter_by(email=ITOPS_ADMIN_EMAIL).first()
        casey = User.query.filter_by(email=APP_MANAGER_EMAIL).first()
        taylor = User.query.filter_by(email=PROCUREMENT_EMAIL).first()
        missing = [e for u, e in ((sage, ITOPS_ADMIN_EMAIL), (casey, APP_MANAGER_EMAIL),
                                   (taylor, PROCUREMENT_EMAIL)) if u is None]
        if missing:
            raise SystemExit(f"expected seeded persona(s) missing: {missing}")

        sage.role = admin_role
        casey.enterprise_role = "application_manager"
        taylor.enterprise_role = "procurement"
        db.session.commit()

        from app.models.organization import Organization

        org = Organization.query.filter_by(slug="lantern-quay").first()
        demo_user = User.query.filter_by(email=DEMO_EMAIL).first()
        # Captured as plain ints before the call: _seed_module_screen_data()
        # enters tenant_scope(), which resets the session (see that
        # function's own docstring) and leaves org/demo_user detached, so
        # anything read after the call must not touch their attributes.
        org_id = org.id
        _seed_module_screen_data(org_id, demo_user.id)

        # Catch up the UnifiedCapability projection for every
        # BusinessCapability row created above -- seed_demo_company()'s 24
        # plus _seed_module_screen_data()'s own 12 -- through the Flask CLI
        # runner, after all seeding, so capability_count/domain_count on the
        # roadmap page and the gap-detection route below read real numbers.
        project_result = runner.invoke(args=["project-capabilities", "--apply"])
        if project_result.exit_code != 0:
            raise SystemExit(
                "project-capabilities --apply failed:\n" + str(project_result.output)
            )

        _check_seed_health(org_id)

        from app.models.archimate_core import SavedDiagram

        dynamic_paths = {}
        diagram = SavedDiagram.query.filter_by(name="Calibration estate overview").first()
        if diagram is not None:
            dynamic_paths["diagrams"] = f"/archimate/composer?viewpoint_id={diagram.id}"
        return dynamic_paths


def _boot(port: int, log_path: pathlib.Path):
    """Boot the real app, in a subprocess, against whatever DATABASE_URL names.

    Mirrors scripts/capture_journey_screenshots.py's _boot(): output to a file,
    never a pipe (a pipe deadlocks the child at 64KB).
    """
    env = dict(os.environ)
    env.setdefault("FLASK_CONFIG", "testing")
    database_url = env.get("TEST_DATABASE_URL") or env.get("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "DATABASE_URL (or TEST_DATABASE_URL) must point at an already-created, "
            "reachable Postgres database -- see this script's module docstring."
        )
    env["DATABASE_URL"] = database_url
    env["TEST_DATABASE_URL"] = database_url
    env.setdefault("SECRET_KEY", "capture-screenshots-session-key-not-for-prod")

    handle = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "manage", "run",
         "--no-reload", "--port", str(port)],
        cwd=str(REPO), env=env, stdout=handle, stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 180
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"server exited early; see {log_path}")
        try:
            urllib.request.urlopen(base + "/health", timeout=3)
            return process, base
        except urllib.error.HTTPError:
            return process, base
        except Exception:
            time.sleep(2)
    process.terminate()
    raise SystemExit(f"server did not boot within 180s; see {log_path}")


def _login(page, base: str, email: str, password: str) -> None:
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=60000)
    page.fill("#email", email)
    page.fill("#password", password)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=90000)
    except Exception:
        pass
    page.wait_for_timeout(800)
    if "/account/login" in page.url:
        raise SystemExit(
            f"could not sign in as {email}; the page said: "
            + " ".join(page.inner_text("body").split())[:200]
        )


def _dismiss_onboarding(page) -> None:
    """Remove the first-run role-picker overlay before photographing anything.

    Same approach as scripts/capture_journey_screenshots.py: a capture of that
    overlay documents onboarding, not the module, and the brief refuses it the
    same way it refuses an empty or error screen.
    """
    page.eval_on_selector_all(
        "[x-show='showOnboarding']", "els => els.forEach(el => el.remove())"
    )
    page.evaluate(
        """() => {
            document.querySelectorAll('.fixed.inset-0').forEach((el) => {
                const z = parseInt(window.getComputedStyle(el).zIndex || '0', 10);
                if (z >= 40) { el.remove(); }
            });
        }"""
    )
    page.wait_for_timeout(300)


def _visit_and_check(page, base: str, path: str, label: str):
    """Navigate to path; refuse (raise) an error response or an empty screen."""
    response = page.goto(base + path, wait_until="networkidle", timeout=60000)
    status = response.status if response else 0
    if status >= 400:
        raise SystemExit(f"{label} ({path}) returned HTTP {status}; refusing to capture it")
    page.wait_for_timeout(1000)
    _dismiss_onboarding(page)
    text = (page.evaluate("() => document.body.innerText") or "").strip()
    if len(text) < 200:
        raise SystemExit(
            f"{label} ({path}) looks empty ({len(text)} chars of text); refusing to "
            f"capture an empty state as though it were evidence"
        )


def compress_to_webp(png_bytes: bytes, dest: pathlib.Path, target_width: int,
                      max_bytes: int = MAX_IMAGE_BYTES) -> tuple[int, int]:
    """Write a PNG screenshot out as a WebP under max_bytes. Returns (width, height).

    Shrinks width in steps before dropping quality much further than ~40: a
    smaller-but-crisp image reads better than a full-size, muddier one, for a
    product screenshot full of small text and icons.
    """
    import io

    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    width = min(target_width, im.width)
    quality = 82
    while True:
        height = round(im.height * (width / im.width))
        resized = im.resize((width, height), Image.LANCZOS)
        resized.save(dest, format="WEBP", quality=quality, method=6)
        size = dest.stat().st_size
        if size <= max_bytes or (quality <= 35 and width <= 480):
            return width, height
        if quality > 35:
            quality -= 12
        else:
            width = round(width * 0.85)
            quality = 60


def _ffmpeg(*args: str) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed ({' '.join(cmd)}):\n{result.stderr}")


def _ffprobe_duration_seconds(path: pathlib.Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}:\n{result.stderr}")
    return float(result.stdout.strip())


def encode_recording(raw_webm: pathlib.Path, dest: pathlib.Path,
                      width: int = 1280, max_bytes: int = MAX_VIDEO_BYTES) -> None:
    """Transcode Playwright's raw (VP8) capture to a size-capped VP9 WebM.

    No audio track (Playwright's recording never has one; -an is explicit
    rather than assumed). Tightens crf upward until the cap is met -- product
    UI footage with little motion compresses hard, so a wide crf range rarely
    takes more than one or two passes in practice.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    for crf in (32, 36, 40, 44, 48, 52):
        _ffmpeg(
            "-i", str(raw_webm),
            "-an",
            "-vf", f"scale={width}:-2",
            "-c:v", "libvpx-vp9",
            "-crf", str(crf), "-b:v", "0",
            "-row-mt", "1", "-deadline", "good", "-cpu-used", "2",
            str(dest),
        )
        if dest.stat().st_size <= max_bytes:
            return
    raise SystemExit(f"could not encode {raw_webm} under {max_bytes} bytes")


def extract_poster(video: pathlib.Path, dest_webp: pathlib.Path, at_seconds: float) -> tuple[int, int]:
    tmp_png = video.with_suffix(".poster.png")
    _ffmpeg("-ss", str(at_seconds), "-i", str(video), "-frames:v", "1", str(tmp_png))
    width, height = compress_to_webp(tmp_png.read_bytes(), dest_webp, target_width=1280)
    tmp_png.unlink(missing_ok=True)
    return width, height


def _wander(page, points) -> None:
    """A little mouse motion so a recording doesn't look like a frozen photo."""
    for x, y in points:
        try:
            page.mouse.move(x, y)
        except Exception:
            pass
        page.wait_for_timeout(500)


def run_public_pages_capture() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if subprocess.run(["where" if os.name == "nt" else "which", tool],
                          capture_output=True).returncode != 0:
            print(f"{tool} is not on PATH; required for --modules's video step", file=sys.stderr)
            return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && "
              "python -m playwright install chromium", file=sys.stderr)
        return 2

    import secrets
    demo_password = os.environ.get("DEMO_USER_PASSWORD") or secrets.token_urlsafe(18)

    print("seeding Lantern Quay Systems ...")
    dynamic_paths = _seed_lantern_quay(demo_password)

    # A saved diagram's real URL carries a database id that cannot be a
    # fixed string in MODULE_CAPTURES (see _seed_lantern_quay's own
    # docstring) -- patch that entry's path in place, in memory only, right
    # before building the capture job list below.
    for i, entry in enumerate(MODULES):
        slug = entry[0]
        if slug in dynamic_paths:
            MODULES[i] = (slug, dynamic_paths[slug], entry[2], entry[3], entry[4])

    port = _free_port()
    scratch = pathlib.Path(os.environ.get("CAPTURE_SCRATCH_DIR",
                                          REPO / "scripts" / "_capture_scratch"))
    scratch.mkdir(parents=True, exist_ok=True)
    log_path = scratch / "server.log"

    print("booting the app ...")
    process, base = _boot(port, log_path)

    captured_images = []
    captured_videos = []
    failed = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                # ── roadmap gap detection, before any image is captured ─────
                # The "AI Generated" tile and the auto_generated
                # gaps roadmaps.webp needs come from stored Gap rows the
                # product's own detector creates -- POST through the signed-in
                # browser to the real route (gap_archimate_service), rather
                # than reimplementing detection here.
                detect_ctx = browser.new_context(viewport=IMAGE_CAPTURE_VIEWPORT)
                detect_page = detect_ctx.new_page()
                _login(detect_page, base, DEMO_EMAIL, demo_password)
                detect_page.goto(
                    base + "/capability-roadmap", wait_until="networkidle", timeout=60000
                )
                csrf_token = detect_page.eval_on_selector(
                    'meta[name="csrf-token"]', "el => el.getAttribute('content')"
                )
                detect_response = detect_page.request.post(
                    base + "/capability-map/api/roadmap/gaps/detect",
                    headers={"X-CSRFToken": csrf_token or ""},
                )
                if detect_response.status >= 400:
                    raise SystemExit(
                        f"gap detection ({detect_response.status}) failed: "
                        f"{detect_response.text()}"
                    )
                print(f"  gap detection: HTTP {detect_response.status}")
                detect_ctx.close()

                # ── module + use-case screenshots ──────────────────────────
                image_jobs = [(slug, path, email, IMG_MODULES_DIR) for slug, path, email, _, _ in MODULES]
                image_jobs += [(slug, path, email, IMG_USE_CASES_DIR)
                               for slug, path, email, _, _ in USE_CASE_SCREENSHOTS]
                for slug, path, email, out_dir in image_jobs:
                    try:
                        context = browser.new_context(viewport=IMAGE_CAPTURE_VIEWPORT)
                        page = context.new_page()
                        _login(page, base, email, demo_password)
                        _visit_and_check(page, base, path, slug)
                        # The guided-walkthrough launcher (and the
                        # equivalent AI-assistant launcher on public-facing
                        # layouts) is a floating round button fixed to the
                        # bottom-right corner of every authenticated page --
                        # on pages whose own content reaches that corner
                        # (e.g. rationalization's Disposition Breakdown
                        # panel) it sits on top of real numbers in the shot.
                        # A <style> tag is blocked by the page's own CSP
                        # (style-src-elem requires a nonce this script
                        # doesn't have), so set the property directly on
                        # each element instead.
                        page.evaluate(
                            "() => { ['guided-mode-trigger', 'guided-mode-panel', "
                            "'unified-ai-chat-fab'].forEach((id) => { "
                            "const el = document.getElementById(id); "
                            "if (el) el.style.setProperty('display', 'none', 'important'); }); }"
                        )
                        if slug == "diagrams":
                            # Use the composer's own existing
                            # fit-to-content control rather than invent a
                            # second way to frame a diagram -- the default
                            # zoom clipped the badge/toolbar at the right
                            # edge once the hero had a real layout.
                            fit_button = page.locator(
                                'button[title="Fit to content (Ctrl+0)"]'
                            )
                            if fit_button.count():
                                fit_button.first.click()
                                page.wait_for_timeout(500)
                        png_bytes = page.screenshot(full_page=False)
                        dest = out_dir / f"{slug}.webp"
                        width, height = compress_to_webp(png_bytes, dest, IMAGE_TARGET_WIDTH)
                        size = dest.stat().st_size
                        captured_images.append((slug, str(dest), size, width, height))
                        print(f"  image  {slug:45s} {size:6d} bytes  {width}x{height}")
                        context.close()
                    except Exception as exc:
                        failed.append(f"{slug}: {type(exc).__name__}: {exc}")

                # ── use-case recordings ─────────────────────────────────────
                for slug, steps, email, caption, alt in USE_CASE_VIDEOS:
                    try:
                        # Log in outside the recording so the clip is all product,
                        # no login-form filler, then replay the session into the
                        # recorded context via storage_state.
                        login_ctx = browser.new_context(viewport=VIDEO_VIEWPORT)
                        login_page = login_ctx.new_page()
                        _login(login_page, base, email, demo_password)
                        state = login_ctx.storage_state()
                        login_ctx.close()

                        video_dir = scratch / f"video-{slug}"
                        video_dir.mkdir(parents=True, exist_ok=True)
                        rec_ctx = browser.new_context(
                            viewport=VIDEO_VIEWPORT, storage_state=state,
                            record_video_dir=str(video_dir),
                            record_video_size=VIDEO_VIEWPORT,
                        )
                        page = rec_ctx.new_page()
                        for path, hold_seconds in steps:
                            _visit_and_check(page, base, path, f"{slug} ({path})")
                            _wander(page, [(560, 420), (900, 480), (700, 560)])
                            page.wait_for_timeout(int(hold_seconds * 1000))
                        video_handle = page.video
                        rec_ctx.close()
                        raw_path = pathlib.Path(video_handle.path())

                        dest = VIDEO_USE_CASES_DIR / f"{slug}.webm"
                        poster_dest = VIDEO_USE_CASES_DIR / f"{slug}-poster.webp"
                        encode_recording(raw_path, dest, width=VIDEO_VIEWPORT["width"])
                        duration = _ffprobe_duration_seconds(dest)
                        if not (15.0 <= duration <= 40.0):
                            raise SystemExit(
                                f"{slug}: recording is {duration:.1f}s, outside the 15-40s brief"
                            )
                        poster_w, poster_h = extract_poster(dest, poster_dest, at_seconds=2.0)

                        meta = {
                            "duration_seconds": round(duration, 1),
                            "width": VIDEO_VIEWPORT["width"],
                            "height": VIDEO_VIEWPORT["height"],
                            "poster_width": poster_w,
                            "poster_height": poster_h,
                            "captured_date": time.strftime("%Y-%m-%d"),
                        }
                        (VIDEO_USE_CASES_DIR / f"{slug}.json").write_text(
                            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
                        )
                        size = dest.stat().st_size
                        captured_videos.append((slug, str(dest), size, duration))
                        print(f"  video  {slug:45s} {size:7d} bytes  {duration:.1f}s")
                    except Exception as exc:
                        failed.append(f"{slug} (video): {type(exc).__name__}: {exc}")
            finally:
                browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()

    print()
    print(f"images captured: {len(captured_images)}, videos captured: {len(captured_videos)}, "
          f"failed: {len(failed)}")
    for line in failed:
        print("  FAILED: " + line)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
