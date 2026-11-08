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
import shutil
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
    parser.add_argument("--help-clips", action="store_true",
                        help="record the \"See how\" clips the first empty screens offer, in the "
                             "demonstration organisation -- see scripts/capture/help_clips.py. "
                             "Re-run after each release.")
    parser.add_argument("--only", default="",
                        help="with --help-clips: the clip keys to record, comma separated (default: all six); "
                             "with --modules --clips-only: the module slugs to record (default: every module "
                             "with a storyboard)")
    parser.add_argument("--clips-only", action="store_true",
                        help="with --modules: record only the whole-journey module recordings -- see "
                             "scripts/capture/module_clips.py -- and leave the screenshots as they are")
    args = parser.parse_args()

    if args.modules:
        return run_public_pages_capture(clips_only=args.clips_only,
                                        only=[k for k in args.only.split(",") if k] or None)
    if args.help_clips:
        return run_help_clips_capture(only=[k for k in args.only.split(",") if k] or None)

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
    IMG_HELP_DIR,
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
from scripts.capture.clip_kit import (  # noqa: E402
    compress_to_webp,
    encode_recording,
    extract_poster,
    ffprobe_duration_seconds as _ffprobe_duration_seconds,
)

IMAGE_CAPTURE_VIEWPORT = {"width": 1440, "height": 900}
IMAGE_TARGET_WIDTH = 1200
VIDEO_VIEWPORT = {"width": 1280, "height": 800}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _complete_overlapping_copy(code: str = "LQ-DOCMGMT-DUP") -> None:
    """The overlapping copy of Document Management is a real system in the portfolio, so it carries what every
    other system carries: the same vendor, owner and capability as the one it overlaps. Without them the
    Applications list opens on a "1 of 20 have no owner, capability or vendor" warning that is about this
    capture, not the product. Idempotent: a value or link that exists is left as it is."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    copy = ApplicationComponent.query.filter_by(application_code=code).first()
    if copy is None:
        return
    source = (ApplicationComponent.query.filter_by(name=copy.name)
              .filter(ApplicationComponent.id != copy.id).order_by(ApplicationComponent.id).first())
    if source is None:
        return
    from app.models.application_owner import ApplicationOwner
    from app.models.application_capability import ApplicationCapabilityMapping

    for column in ("vendor_name", "vendor_organization_id", "vendor_product_id", "component_type", "business_domain"):
        if getattr(copy, column) in (None, ""):
            setattr(copy, column, getattr(source, column))
    for model, link in ((ApplicationOwner, "application_id"), (ApplicationCapabilityMapping, "application_component_id")):
        if model.query.filter(getattr(model, link) == copy.id).first():
            continue
        for row in model.query.filter(getattr(model, link) == source.id).all():
            values = {c.name: getattr(row, c.name) for c in model.__table__.columns if not c.primary_key}
            values[link] = copy.id
            db.session.add(model(**values))
    db.session.flush()


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
        _resolve_apps,
        _resolve_caps,
        _resolve_elements,
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

        # The board's reviews come from seed_demo_company (one seed). The capture adds none, so the figures a
        # picture shows are the seed's.

        # Business cases come from seed_demo_company (one seed): three cases with their options, recommendation and
        # three-year cost and return, tied to the solutions they support.

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

        # ── Saved ArchiMate diagrams (archimate.diagrams_library) ───────────
        from app.models.archimate_core import SavedDiagram, SavedDiagramElement

        diagram_names = [
            "Calibration estate overview",
            "Event backbone target state",
            "Supplier integration landscape",
        ]
        element_ids = [el.id for el in list(elements.values())[:6]]
        for name in diagram_names:
            if SavedDiagram.query.filter_by(name=name).first():
                continue
            diagram = SavedDiagram(name=name, created_by_id=demo_user_id)
            db.session.add(diagram)
            db.session.flush()
            for element_id in element_ids:
                db.session.add(SavedDiagramElement(diagram_id=diagram.id, element_id=element_id))
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

        _complete_overlapping_copy()

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
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )

        app_list = list(apps.values())[:8]
        cap_list = list(caps.values())[:8]
        for app_row, cap_row in zip(app_list, cap_list):
            exists = UnifiedApplicationCapabilityMapping.query.filter_by(
                application_component_id=app_row.id, unified_capability_id=cap_row.id,
            ).first()
            if exists:
                continue
            db.session.add(UnifiedApplicationCapabilityMapping(
                application_component_id=app_row.id, unified_capability_id=cap_row.id,
            ))
        db.session.flush()

        # The portfolio's initiatives come from seed_demo_company (one seed): seven, each with its benefit and the
        # objective it serves. A second set made here duplicated four of them under other codes.

        # The vendors and their contract renewals come from seed_demo_company (one seed); the capture adds none.

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
            for analysis_type, elem_type, severity, caps, apps_count, procs in impact_rows:
                db.session.add(ImpactAnalysisResult(
                    analysis_type=analysis_type, trigger_element_type=elem_type,
                    trigger_element_id=affected_app.id, overall_severity=severity,
                    affected_capabilities_count=caps, affected_applications_count=apps_count,
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

        db.session.commit()

    # ── Globally-scoped data: no tenant_scope, not a seeded script concern ──
    # Industry APQC frameworks (industry_apqc.industry_apqc_dashboard): the
    # standard default set the product's own "Seed Default Frameworks"
    # button would create -- not fictional data, calling the same service
    # function the button calls.
    from app.services.industry_apqc_service import IndustryAPQCService

    IndustryAPQCService().seed_default_frameworks()

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
    from app.models.batch_import import BatchImportJob

    job_rows = [
        ("lantern-quay-applications.xlsx", 20, "completed"),
        ("lantern-quay-capabilities.csv", 24, "completed"),
        ("lantern-quay-risk-register.csv", 10, "processing"),
    ]
    for filename, total, status in job_rows:
        if BatchImportJob.query.filter_by(filename=filename, user_id=demo_user_id).first():
            continue
        db.session.add(BatchImportJob(
            user_id=demo_user_id, filename=filename, total_applications=total, status=status,
        ))
    db.session.commit()


def _seed_lantern_quay(demo_password: str) -> None:
    """Seed Lantern Quay Systems and grant three capture-only persona roles.

    Calls the existing seed_demo_company() unchanged (app/commands/seed_demo_company.py
    stays untouched) then elevates three already-seeded owner accounts just
    enough to pass the role gate on the one page each needs -- Integrations
    (platform admin), My Applications (application_manager), Procurement
    (procurement). No new fictional person is invented: all three are already
    part of the seeded cast.
    """
    os.environ.setdefault("DEMO_USER_PASSWORD", demo_password)
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        Role.insert_roles()
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
        _seed_module_screen_data(org.id, demo_user.id)


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


def _wander(page, points) -> None:
    """A little mouse motion so a recording doesn't look like a frozen photo."""
    for x, y in points:
        try:
            page.mouse.move(x, y)
        except Exception:
            pass
        page.wait_for_timeout(500)


def run_public_pages_capture(clips_only: bool = False, only=None) -> int:
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
    os.environ["DEMO_USER_PASSWORD"] = demo_password

    print("seeding Lantern Quay Systems ...")
    _seed_lantern_quay(demo_password)

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
                # ── module + use-case screenshots ──────────────────────────
                image_jobs = [(slug, path, email, IMG_MODULES_DIR) for slug, path, email, _, _ in MODULES]
                image_jobs += [(slug, path, email, IMG_USE_CASES_DIR)
                               for slug, path, email, _, _ in USE_CASE_SCREENSHOTS]
                if clips_only:
                    image_jobs = []
                for slug, path, email, out_dir in image_jobs:
                    try:
                        context = browser.new_context(viewport=IMAGE_CAPTURE_VIEWPORT)
                        page = context.new_page()
                        _login(page, base, email, demo_password)
                        _visit_and_check(page, base, path, slug)
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
                for slug, steps, email, caption, alt in ([] if clips_only else USE_CASE_VIDEOS):
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

                # ── whole-journey module recordings ─────────────────────────
                from scripts.capture import module_clips
                from app import create_app
                from app.models.organization import Organization
                from app.models.user import User

                with create_app("testing").app_context():
                    demo_org_id = User.query.filter_by(email=DEMO_EMAIL).one().organization_id
                    others = [o.name for o in Organization.query.all() if o.name and o.id != demo_org_id]
                written, clip_failed = module_clips.record_all(
                    browser, base, IMG_MODULES_DIR, other_organisation_names=others, only=only)
                captured_videos += [(pathlib.Path(w).stem, w, pathlib.Path(w).stat().st_size, 0) for w in written]
                failed += clip_failed
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


def run_help_clips_capture(only=None) -> int:
    """Record every "See how" clip as the demonstration person, then scan each for secrets and other organisations."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg and ffprobe are required to record the clips", file=sys.stderr)
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && "
              "python -m playwright install chromium", file=sys.stderr)
        return 2

    import secrets
    from scripts.capture import github_fixture, help_clips
    from scripts.capture.clip_kit import DEMO_PASSWORD_VARIABLE, DEMO_USER_EMAIL

    demo_password = os.environ.get(DEMO_PASSWORD_VARIABLE) or secrets.token_urlsafe(18)
    os.environ[DEMO_PASSWORD_VARIABLE] = demo_password
    print("seeding Lantern Quay Systems ...")
    _seed_lantern_quay(demo_password)

    from app import create_app
    from app.models.organization import Organization

    from app.models.user import User

    with create_app("testing").app_context():
        demo_org_id = User.query.filter_by(email=DEMO_USER_EMAIL).one().organization_id
        others = [o.name for o in Organization.query.all() if o.name and o.id != demo_org_id]

    scratch = pathlib.Path(os.environ.get("CAPTURE_SCRATCH_DIR", REPO / "scripts" / "_capture_scratch"))
    scratch.mkdir(parents=True, exist_ok=True)
    # The server answers GitHub from the recorded organisation, and runs the background read the connect step starts.
    (scratch / "sitecustomize.py").write_text(github_fixture.SITECUSTOMIZE)
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(scratch), os.environ.get("PYTHONPATH", "")) if p)
    os.environ["ALLOW_EXTERNAL_API_CALLS"] = "true"
    os.environ["RUN_JOB_WORKER"] = "1"
    # The testing configuration makes new vault keys at every start, so a connection saved by an earlier
    # recording could not be read by this one. Keep one pair beside the scratch folder instead.
    from cryptography.fernet import Fernet

    keys = scratch / "vault-keys.json"
    if not keys.is_file():
        keys.write_text(json.dumps({name: Fernet.generate_key().decode() for name in
                                    ("CREDENTIAL_ENCRYPTION_KEY", "ORG_ENCRYPTION_MASTER_KEY")}))
        keys.chmod(0o600)
    for name, value in json.loads(keys.read_text()).items():
        os.environ.setdefault(name, value)
    process, base = _boot(_free_port(), scratch / "server.log")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                help_clips.prepare_github_model(browser, base, "recorder-" + secrets.token_hex(8))
                written, failed = help_clips.record_all(browser, base, IMG_HELP_DIR, other_organisation_names=others, only=only)
            finally:
                browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()

    print()
    print(f"clips recorded: {len(written)}, failed: {len(failed)}")
    for line in failed:
        print("  FAILED: " + line)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
