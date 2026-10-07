"""Harness for browser smoke journeys.

These render real pages in a real browser against a real database. That matters
because every cheaper layer of verification in this codebase has passed defects
the next layer caught:

  * static analysis passed a front end that never loaded, because the templates
    were valid and only the browser checks integrity hashes
  * route maps passed handlers that 500 on their first row, because an empty
    portfolio never enters the loop
  * HTTP 200 passed a compliance page rendering "Consumed: / Entitled:" with both
    numbers blank, because Jinja prints Undefined as empty string

So the assertions here are deliberately about what a user experiences: does the
page render, does the front end boot, is every control named, does anything
overflow, and does submitted work actually persist and appear where it should.

Skips cleanly when Playwright or a browser is unavailable, so `pytest -q` on a
developer machine is unaffected; CI installs Chromium and runs them for real.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

pytest.importorskip("playwright", reason="playwright not installed - smoke journeys skipped")

PASSWORD = "SmokeJourney!2026"
BOOT_TIMEOUT = int(os.environ.get("SMOKE_BOOT_TIMEOUT", "180"))


def pytest_configure(config):
    """Register fallback ``page`` and ``context`` fixtures when the
    pytest-playwright plugin is absent.

    CI's "Browser journeys" and "Browser compatibility" jobs install
    ``playwright`` and ``pytest-timeout`` but NOT ``pytest-playwright``,
    so every test that uses the plugin's ``page`` (or ``context``) fixture
    errors at setup with "fixture 'page' not found".  These fallbacks are
    built on this suite's own ``browser`` fixture (package scope) and
    provide the same function-scoped lifecycle the plugin would.
    """
    if config.pluginmanager.hasplugin("playwright"):
        return

    class _SmokePageFallback:
        @pytest.fixture(scope="function")
        def context(self, browser):
            ctx = browser.new_context()
            yield ctx
            ctx.close()

        @pytest.fixture(scope="function")
        def page(self, context):
            p = context.new_page()
            yield p
            p.close()

    config.pluginmanager.register(_SmokePageFallback(), name="smoke-page-fallback")


def _tail(path, lines=40):
    """Last *lines* of the server log - what actually failed, in its own words."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except Exception as exc:
        return "(could not read server log: %s)" % exc


class SmokeServer(str):
    """The base URL, plus the server log behind it.

    Subclasses str so every existing `live_server + path` still works, while
    giving a failing test somewhere to look.

    ``app`` is an in-process Flask app (the shared session-scoped fixture from
    tests/conftest.py) - NOT the app actually serving the browser, which runs
    in a separate subprocess and has no importable app object. It exists only
    so pytest-flask's autouse `_push_request_context` fixture
    (site-packages/pytest_flask/plugin.py) has something to push a request
    context onto: that fixture activates whenever a test's fixtures include
    both `app` and `live_server`, and unconditionally does
    `getfixturevalue(request, "live_server").app` when `live_server` is
    present - a real attribute pytest-flask's own live_server carries, that
    ours never did. Without it, any smoke test whose fixtures reference `app`
    (e.g. application_history_records) errored at setup with
    `AttributeError: 'SmokeServer' object has no attribute 'app'` before a
    single line of the test ran.
    """

    def __new__(cls, base, log_path, app=None):
        obj = super().__new__(cls, base)
        obj.log_path = log_path
        obj.app = app
        return obj

    def tail(self, lines=40):
        return _tail(self.log_path, lines)


def _has_gunicorn():
    """gunicorn imports on Windows but cannot run there - it needs fcntl."""
    if sys.platform.startswith("win"):
        return False
    try:
        import gunicorn  # noqa: F401
        return True
    except Exception:
        return False


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _require_explicit_test_database(env):
    """Bind browser and seeder to one explicitly named candidate database."""
    test_url = env.get("TEST_DATABASE_URL")
    if not test_url:
        raise pytest.UsageError(
            "Smoke qualification requires an explicit TEST_DATABASE_URL; "
            "the long-lived fallback database is not valid release evidence."
        )
    server_url = env.get("DATABASE_URL")
    if server_url and server_url != test_url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL and DATABASE_URL must name the same database "
            "for smoke qualification."
        )
    env["DATABASE_URL"] = test_url


def _select_browser_engine(playwright, env):
    """Return the explicitly requested supported Playwright engine."""
    name = env.get("SMOKE_BROWSER", "chromium").strip().lower()
    if name not in {"chromium", "firefox", "webkit"}:
        raise pytest.UsageError(
            "SMOKE_BROWSER must be one of chromium, firefox, or webkit; got %r."
            % name
        )
    return getattr(playwright, name), name


@pytest.fixture(scope="session")
def ai_protocol_stub():
    """An explicit test peer, never enabled by ordinary smoke qualification."""
    if os.environ.get("SMOKE_AI_PROTOCOL_STUB") != "1":
        yield None
        return
    from tests.smoke.ai_protocol_stub import AIProtocolStub

    with AIProtocolStub() as stub:
        yield stub


@pytest.fixture(scope="session")
def live_server(request, ai_protocol_stub, app):
    """Boot the real application on a free port and yield its base URL.

    Runs the app as a subprocess rather than via the test client, because a test
    client cannot execute JavaScript, load a stylesheet or enforce Subresource
    Integrity - which is precisely the class of defect these journeys exist to
    catch.
    """
    server = boot_live_server(request, ai_protocol_stub, app)
    yield server

    if request.session.testsfailed:
        print("\n[smoke] server log after journey failure:\n%s" % server.tail(300))


def boot_live_server(request, ai_protocol_stub, app, extra_env=None):
    """Start one app subprocess and return its SmokeServer; stopped by `request`'s finalizer.

    `extra_env` overrides configuration for this server only, so a module can
    exercise a feature flag without switching it on for every other journey.
    """
    # The smoke server starts against the shared candidate database before the
    # ORM seeding below runs. When a branch adds nullable columns to existing
    # tables, requests can 500 on the first SELECT unless the add-only repair
    # path runs first. Production already does init-db -> reconcile-schema on
    # boot; mirror that here so browser journeys observe the real branch code,
    # not drift left behind by an older local schema.
    from app.commands.reconcile_schema import _reconcile

    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert not failed, "smoke live_server could not reconcile schema: %s" % failed

    port = _free_port()
    env = dict(os.environ)
    env.update(extra_env or {})
    _require_explicit_test_database(env)
    if ai_protocol_stub is not None:
        env = ai_protocol_stub.child_environment(env)
    env.setdefault("SECRET_KEY", "smoke-only-not-secret-" + "x" * 16)
    # "smoke", not "testing": config.py's SmokeTestingConfig is identical to
    # TestingConfig except for ADMIN_MFA_BYPASS, which lets the dozens of
    # admin-archetype fixtures in this suite reach the app shell without a
    # browser driving a real TOTP round trip (R1-B12 PR 2). The hardcoded
    # switch lives only on that one config class -- see its docstring and
    # app/services/mfa_service.py's required_for().
    #
    # An explicit assignment, not setdefault: tests/conftest.py's session-
    # scoped `app` fixture (a dependency of `live_server` below) already ran
    # `os.environ.setdefault("FLASK_CONFIG", "testing")` in this same process
    # before this function is ever called, so `os.environ` here already has
    # FLASK_CONFIG="testing" -- a setdefault on `env` would silently keep
    # that inherited value and never select the smoke config at all. A caller
    # that genuinely needs a different config for one journey can still win,
    # since `extra_env` was folded into `env` above and is preserved here.
    env["FLASK_CONFIG"] = (extra_env or {}).get("FLASK_CONFIG", "smoke")
    env["FLASK_DEBUG"] = "0"
    # TestingConfig reads TEST_DATABASE_URL, not DATABASE_URL. Without this the
    # subprocess silently falls back to the default DSN on port 5432 and every
    # request 500s on "connection refused" - which surfaces as a browser timeout,
    # not as a database error.
    # Serve with gunicorn where it exists - which is CI and every deployment.
    #
    # Flask's development server cannot sustain these journeys: each dashboard
    # fires a dozen parallel API calls, and a single-process dev server queues
    # them until the browser gives up. That produced navigation timeouts on pages
    # that are perfectly healthy, and it also means the dev server is not what we
    # want to be asserting against - production runs gunicorn, so the smoke suite
    # should too.
    if _has_gunicorn():
        cmd = [sys.executable, "-m", "gunicorn", "manage:app",
               "--bind", "127.0.0.1:%d" % port,
               "--workers", "2", "--threads", "8",
               "--timeout", "120", "--graceful-timeout", "20",
               # No access log: one line per request, and the errors are what matter.
               "--error-logfile", "-"]
    else:                                   # Windows dev machines: no gunicorn
        cmd = [sys.executable, "-m", "flask", "--app", "manage", "run",
               "--host", "127.0.0.1", "--port", str(port), "--no-reload"]

    # Log to a FILE, never to a pipe.
    #
    # subprocess.PIPE with nothing draining it deadlocks the child as soon as the
    # 64 KB buffer fills. With --access-logfile that is roughly one journey: the
    # first test passed, the second filled the buffer, and every request after it
    # timed out on a server that was blocked writing a log line. A file also means
    # the server log survives to be printed when a test fails.
    log_path = os.path.join(tempfile.gettempdir(), "smoke-server-%d.log" % port)
    log_handle = open(log_path, "w+b")
    proc = subprocess.Popen(cmd, env=env, stdout=log_handle, stderr=subprocess.STDOUT)

    def stop_server():
        # Register before boot checks so an early failure cannot leave the
        # subprocess using a protocol listener that has already been closed.
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log_handle.close()

    request.addfinalizer(stop_server)
    base = "http://127.0.0.1:%d" % port

    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() or b"").decode("utf-8", "replace")[-2500:]
            pytest.fail("app exited during boot:\n%s" % out)
        try:
            import urllib.error
            import urllib.request
            # Wait for a served response, not just a bound socket: this app takes
            # tens of seconds to finish importing, and a bound port only means
            # the listener exists.
            with urllib.request.urlopen(base + "/health", timeout=5):
                break
        except urllib.error.HTTPError:
            # /health reports 503 when Redis is absent, which is the normal state
            # in CI and locally. Any HTTP response means the app is serving; a
            # degraded dependency is not a reason to refuse to start testing.
            break
        except Exception:
            pass
        time.sleep(3)
    else:
        proc.kill()
        pytest.fail("app did not bind %s within %ss" % (base, BOOT_TIMEOUT))

    # Warm the first template render. Jinja compiles lazily and several services
    # import on first request, so a cold /account/login can take a minute on a
    # loaded machine - long enough to look like a hang rather than a slow start.
    try:
        import urllib.request
        urllib.request.urlopen(base + "/account/login", timeout=180).read()
    except Exception:
        pass

    # Prove the server the browser will talk to is actually serving, and say so.
    # A silent assumption here cost hours: the subprocess was reachable by urllib
    # and the failure surfaced only as an opaque browser navigation timeout.
    import urllib.request as _u
    try:
        with _u.urlopen(base + "/account/login", timeout=60) as r:
            print("[smoke] live_server ready at %s (login page HTTP %s)" % (base, r.status))
    except Exception as exc:
        print("[smoke] live_server at %s NOT serving: %s" % (base, exc))

    return SmokeServer(base, log_path, app)


def _delete_api_settings(**filters):
    """Delete APISettings rows matching *filters* inside a fresh app context.

    Returns the number of rows deleted so callers can assert their own
    expectations (e.g. exactly one row for a fixture teardown, or any number
    for a test finalizer that may have already cleaned up).
    """
    from app import create_app, db
    from app.models.models import APISettings

    app = create_app("testing")
    with app.app_context():
        db.session.remove()
        existing = APISettings.query.filter_by(**filters).count()
        if existing:
            APISettings.query.filter_by(**filters).delete(
                synchronize_session=False)
            db.session.commit()
            assert APISettings.query.filter_by(**filters).count() == 0
        db.session.remove()
        return existing


def _seed_standard_org(request, ai_protocol_stub, fixed_suffix=None):
    """One organisation, one user per archetype, and the fixtures they need.

    Returns {archetype: email} plus the ids the journeys navigate to.

    Data is created through the ORM rather than the UI because several archetypes
    have no create path for their own entity - which was itself a finding - and a
    journey should not be blocked from testing a read screen by a missing write
    screen.

    A plain function, not a fixture: `seeded` below calls it once for the
    session-wide organisation every ordinary smoke test shares, and a second
    caller (test_visual_regression.py's `visual_org`) calls it again for a
    dedicated organisation of exactly the same shape, so a screen capture
    that needs real content does not also need the shared organisation to be
    in whatever state 500 other tests have left it in.

    Every name below embeds a per-call suffix so two calls in the same
    database never collide. It is random (a fresh uuid) by default, which is
    what every ordinary smoke test wants -- nothing about its own content is
    asserted on. `fixed_suffix` overrides that with a caller-chosen, stable
    value instead, for the one caller (test_visual_regression.py's
    `visual_org`) whose whole point is a screen whose content -- not just its
    shape -- must render identically every run. A fixed suffix reused across
    two calls in the same database collides on the organisation's slug (and
    the seeded users' emails): the caller is responsible for calling this at
    most once per database when passing one (see `visual_org`'s own guard,
    which pytest's fixture scope alone was not enough to provide).
    """
    from app import create_app, db

    app = create_app("testing")
    suffix = fixed_suffix or uuid.uuid4().hex[:8]
    out = {"emails": {}, "ids": {}}

    with app.app_context():
        from app.models.application_owner import ApplicationOwner
        from app.models.application_portfolio import ApplicationComponent, VendorContract
        from app.models.architecture_journey import ArchitectureJourney
        from app.models.organization import Organization
        from app.models.solution_models import Solution
        from app.models.technology_layer import Node
        from app.models.user import Role, User

        db.create_all()
        # ADR 0008's write-time projection listener (app/models/business_capabilities.py)
        # silently no-ops -- by design, so a capability create never 500s on an
        # un-migrated database -- until `uq_unified_capabilities_provenance` exists.
        # `db.create_all()` never creates it (it's applied by a dedicated migration
        # command, not declared as a SQLAlchemy Index), so without this call every
        # fresh smoke-test database would fail test_capability_journey.py's
        # canonical-store assertion for an environmental reason that looks
        # identical to a real regression. Mirrors scripts/database/deploy-schema.sh's
        # own ordering (migration before any capability write).
        from sqlalchemy import text as _text

        from app.commands.apply_unified_capability_provenance_migration import (
            MIGRATION_PATH,
        )

        # The command itself is a click.Command decorated with @with_appcontext,
        # which requires an active click context (`.callback()` alone raises
        # RuntimeError: no active click context) -- so the SQL file is applied
        # directly here rather than invoking the command, following the same
        # BEGIN/COMMIT-stripping the command itself does so it nests inside this
        # session's own transaction instead of opening a second one.
        _migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
        _migration_body = "\n".join(
            line for line in _migration_sql.splitlines()
            if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
        )
        db.session.execute(_text(_migration_body))
        db.session.commit()
        if ai_protocol_stub is not None:
            from app.models.models import APISettings

            # Clean up any stale protocol-stub records from interrupted runs.
            # The live_server subprocess may have created a record, then the
            # seeder's own app_context reads the same database.  Without this
            # cleanup a previous run whose finalizer did not execute leaves an
            # enabled provider behind, and every smoke test errors at setup.
            for stale in APISettings.query.filter_by(key_label="ci-protocol-stub").all():
                db.session.delete(stale)
            db.session.commit()

            # This app context is intentionally unscoped: reject ANY existing
            # enabled provider (other than our own, which was just removed)
            # before exercising AI in a candidate database.
            if APISettings.query.filter_by(enabled=True).count():
                pytest.fail("AI protocol qualification requires a candidate database without enabled provider records")
        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        administrator_role = Role.query.filter_by(name="Administrator").one()

        org = Organization(name="Smoke Org %s" % suffix, slug="smoke-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        # One person per archetype is more than Community admits; the plan is
        # recorded where every limit is read from, the subscriptions row.
        from app.services.billing_plans import set_contract_plan

        set_contract_plan(org, "enterprise", None)
        db.session.commit()
        out["ids"]["org"] = org.id

        # Enable the implementation_planning feature flag so /implementation/ routes work
        from app.models.feature_flags import FeatureFlag, FeatureState
        impl_flag = FeatureFlag.query.filter_by(key="architecture_implementation_planning").first()
        if not impl_flag:
            impl_flag = FeatureFlag(
                key="architecture_implementation_planning",
                name="Architecture Implementation Planning",
                description="Enable the Implementation Planning module (gap discovery, work packages, plateaus)",
                enabled=True,
                state=FeatureState.STABLE,
            )
            db.session.add(impl_flag)
            db.session.commit()
        elif not impl_flag.enabled or impl_flag.state != FeatureState.STABLE:
            impl_flag.enabled = True
            impl_flag.state = FeatureState.STABLE
            db.session.commit()

        if ai_protocol_stub is not None:
            from tests.smoke.ai_protocol_stub import MODEL, TOKEN

            setting = APISettings(provider="openai", key_label="ci-protocol-stub",
                                  api_key=TOKEN, enabled=True, default_model=MODEL,
                                  organization_id=org.id)
            db.session.add(setting)
            db.session.commit()
            provider_id, provider_org = setting.id, org.id
            out["ids"]["ai_protocol_provider"] = provider_id

            def remove_protocol_provider():
                count = _delete_api_settings(
                    id=provider_id, organization_id=provider_org,
                    provider="openai", key_label="ci-protocol-stub")
                assert count == 1, "Protocol provider fixture was unexpectedly changed"

            request.addfinalizer(remove_protocol_provider)

        for archetype in ARCHETYPES:
            email = "smoke.%s.%s@example.com" % (archetype.replace("_", "-"), suffix)
            user = User(
                email=email, first_name="Smoke", last_name=archetype[:12],
                organization_id=org.id, enterprise_role=archetype, confirmed=True,
            )
            user.role = administrator_role if archetype == "platform_admin" else architect_role
            user.is_platform_admin = archetype == "platform_admin"
            user.is_org_admin = archetype == "platform_admin"
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            out["emails"][archetype] = email
            if archetype == "application_manager":
                out["ids"]["app_manager_user"] = user.id
            if archetype == "business_architect":
                out["ids"]["business_architect_user"] = user.id
            if archetype == "solution_architect":
                out["ids"]["solution_architect_user"] = user.id

        journey = ArchitectureJourney(
            owner_id=out["ids"]["business_architect_user"],
            organization_id=org.id,
            title="Smoke operating model %s" % suffix,
            intent="operating_model",
            selected_layers=["motivation", "business"],
            current_stage="frame",
            status="active",
        )
        db.session.add(journey)
        db.session.commit()
        out["ids"]["architecture_journey"] = journey.id

        solution = Solution(
            name="Smoke solution blueprint %s" % suffix,
            description="Outcome-test fixture for solution blueprint controls.",
            organization_id=org.id,
            created_by_id=out["ids"]["solution_architect_user"],
            governance_status="draft",
            adm_phase="C",
            has_acm_domains=True,
        )
        db.session.add(solution)
        db.session.commit()
        out["ids"]["solution"] = solution.id

        # An ARB-approved reference solution so the reference catalogue lens has
        # real content to render and filter, rather than only its empty state.
        reference_solution = Solution(
            name="Smoke reference payroll integration %s" % suffix,
            description="Approved, reusable integration for the reference catalogue lens.",
            organization_id=org.id,
            created_by_id=out["ids"]["solution_architect_user"],
            governance_status="approved",
            business_domain="Finance",
            solution_type="integration",
            solution_owner="Smoke Arch Team",
            adm_phase="F",
            arb_approval_date=__import__("datetime").date.today(),
        )
        db.session.add(reference_solution)
        db.session.commit()
        out["ids"]["reference_solution"] = reference_solution.id

        # Two related ArchiMate elements so the impact / dependency-graph lens
        # renders a real node-link picture (2 nodes, 1 edge), not just its
        # "no relationships recorded" empty state.
        from app.models.archimate_core import (
            ArchiMateElement as _ImpactElement,
            ArchiMateRelationship as _ImpactRel,
        )

        impact_source = _ImpactElement(
            name="Smoke impact source %s" % suffix, type="application_component",
            layer="application", organization_id=org.id,
        )
        impact_target = _ImpactElement(
            name="Smoke impact target %s" % suffix, type="application_component",
            layer="application", organization_id=org.id,
        )
        db.session.add_all([impact_source, impact_target])
        db.session.commit()
        db.session.add(_ImpactRel(
            type="serving", source_id=impact_source.id, target_id=impact_target.id,
            organization_id=org.id,
        ))
        db.session.commit()
        out["ids"]["impact_source_element"] = impact_source.id
        out["ids"]["impact_target_element"] = impact_target.id

        component = ApplicationComponent(
            name="Smoke Payroll %s" % suffix, organization_id=org.id,
            lifecycle_status="operational", description="Smoke fixture.",
        )
        db.session.add(component)
        db.session.commit()
        out["ids"]["application"] = component.id

        # Interface Register (SAP S/4HANA Interface Register, Task 02): a
        # TechnologyRoadmapInitiative resolved through a real ArchitectureModel,
        # per the feature's own tenant-scoping argument (SDD §8.2).
        from app.models.archimate_core import ArchitectureModel
        from app.models.implementation_migration import TechnologyRoadmapInitiative

        interface_register_architecture = ArchitectureModel(
            name="Smoke Architecture %s" % suffix, organization_id=org.id,
        )
        db.session.add(interface_register_architecture)
        db.session.commit()
        interface_register_initiative = TechnologyRoadmapInitiative(
            name="Smoke S/4HANA Programme %s" % suffix,
            fiscal_year_start=2026, fiscal_year_end=2027,
            architecture_id=interface_register_architecture.id,
        )
        db.session.add(interface_register_initiative)
        db.session.commit()
        out["ids"]["interface_register_initiative"] = interface_register_initiative.id

        # Select a real tenant domain in entity journeys rather than triggering
        # the create form's implicit General-domain side effect.
        from app.models.process_data import DataDomain

        data_domain = DataDomain(name="Smoke data domain %s" % suffix,
                                 organization_id=org.id, domain_type="reference")
        db.session.add(data_domain)
        db.session.commit()
        out["ids"]["data_domain"] = data_domain.id

        # Exercise the populated dashboard, not only its sparse-org onboarding
        # shell. Five applications is the production threshold for data mode.
        db.session.add_all([
            ApplicationComponent(name="Smoke dashboard app %s %s" % (suffix, index),
                                 organization_id=org.id, lifecycle_status="operational")
            for index in range(4)
        ])
        db.session.commit()

        radar_node = Node(
            name="Smoke radar node %s" % suffix, organization_id=org.id,
            description="Isolated technology classification browser fixture.",
        )
        db.session.add(radar_node)
        db.session.commit()
        assert radar_node.archimate_element_id is not None
        out["ids"]["radar_element"] = radar_node.archimate_element_id

        db.session.add(ApplicationOwner(
            user_id=out["ids"]["app_manager_user"], application_id=component.id,
            organization_id=org.id, ownership_type="primary",
        ))
        contract = VendorContract(
            organization_id=org.id, contract_name="Smoke MSA %s" % suffix,
            contract_number="SMOKE-%s" % suffix, status="active",
            contract_value=100000, start_date=__import__("datetime").date.today(),
        )
        db.session.add(contract)
        db.session.commit()
        out["ids"]["contract"] = contract.id

        # A second solution, separate from the blueprint-controls one above,
        # with a real ArchiMate element linked to it via the polymorphic
        # solution_archimate_elements junction. This is what the Code
        # Workbench's "Quick Generate" one-click path requires
        # (codegen_routes.workbench_page's linked_elements_count > 0) --
        # without it the button never renders and codegen is untestable
        # from a fresh seed.
        from app.models.solution_models import SolutionArchiMateElement

        codegen_component = ApplicationComponent(
            name="Smoke Codegen Service %s" % suffix, organization_id=org.id,
            lifecycle_status="operational", description="Smoke fixture for codegen journey.",
        )
        db.session.add(codegen_component)
        db.session.commit()
        assert codegen_component.archimate_element_id is not None

        codegen_solution = Solution(
            name="Smoke codegen solution %s" % suffix,
            description="Outcome-test fixture for the Code Workbench journey.",
            organization_id=org.id,
            created_by_id=out["ids"]["solution_architect_user"],
            governance_status="draft",
            adm_phase="C",
            has_acm_domains=True,
        )
        db.session.add(codegen_solution)
        db.session.commit()
        out["ids"]["codegen_solution"] = codegen_solution.id

        db.session.add(SolutionArchiMateElement(
            solution_id=codegen_solution.id,
            layer_type="application",
            element_id=codegen_component.archimate_element_id,
            element_table="archimate_elements",
            element_name=codegen_component.name,
            element_role="primary",
        ))
        db.session.commit()

    return out


@pytest.fixture(scope="session")
def seeded(live_server, request, ai_protocol_stub):
    """The one organisation, one user per archetype, and their fixtures
    every ordinary smoke test in this session shares. See
    `_seed_standard_org` above for what it contains."""
    return _seed_standard_org(request, ai_protocol_stub)


PAGE_TIMEOUT = int(os.environ.get("SMOKE_PAGE_TIMEOUT", "90000"))


# `package`, not `session`, and the distinction is load-bearing.
#
# Playwright's sync API drives its asyncio loop from a greenlet that stays parked
# in run_until_complete() for as long as `sync_playwright()` is open. Greenlets
# share the OS thread and asyncio's running-loop marker is thread-global, so from
# the moment the first smoke test asks for `browser`, asyncio._get_running_loop()
# returns Playwright's loop for every test that follows in the process.
#
# Session scope held that context open until the whole run ended. pytest collects
# tests/journeys -> tests/smoke -> tests/test_*.py, so every later module ran
# inside the leaked loop, and tests/test_lucid_import.py:705 — the only
# asyncio.run() in the suite — died with:
#
#     RuntimeError: asyncio.run() cannot be called from a running event loop
#
# Package scope finalises the fixture when tests/smoke finishes (it has an
# __init__.py, so it is a Package node), releasing the loop before the rest of the
# suite. Chromium is still launched exactly once — there is one smoke package —
# and it is scope-legal: `audited` is module-scoped and every `page` is
# function-scoped, so no consumer outlives it.
#
# CI passes today only because its `tests` job never runs `playwright install`,
# so the launch raises, the skip below unwinds the context, and the loop is
# released. Adding a browser to that job would have turned it red.


@pytest.fixture(scope="session")
def _sync_playwright_instance(request):
    """One sync_playwright() instance for the whole session.

    Used only when the pytest-playwright plugin is absent (the CI browser jobs
    that install ``playwright`` but not ``pytest-playwright``).  In those jobs
    the smoke package is the last (and only) browser consumer, so a
    session-scoped lifecycle is safe — there is no later ``asyncio.run()`` to
    collide with.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    request.addfinalizer(pw.stop)
    return pw


@pytest.fixture(scope="package")
def browser(request):
    if request.config.pluginmanager.hasplugin("playwright"):
        playwright = request.getfixturevalue("playwright")
    else:
        playwright = request.getfixturevalue("_sync_playwright_instance")
    engine, engine_name = _select_browser_engine(playwright, os.environ)
    try:
        b = engine.launch(headless=True)
    except Exception as exc:                      # no browser binary in this env
        message = "%s unavailable: %s" % (engine_name, str(exc)[:120])
        if os.environ.get("SMOKE_REQUIRE_BROWSER") == "1":
            pytest.fail(message)
        pytest.skip(message)
    yield b
    b.close()


def type_and_wait(page, prefix, term):
    """Type *term* into the ask-picker input and wait for the option list.

    Uses ``fill()`` (clears existing text, then types) so repeated
    calls across question switches do not concatenate onto stale input.
    """
    box = page.locator("#%s-picker-input" % prefix)
    box.fill(term)
    page.wait_for_selector("#%s-picker-listbox [role=option]" % prefix)
    return box


# Every enterprise role the product defines. The scope contract below prevents
# a new or promoted persona from silently disappearing from browser coverage.
ARCHETYPES = [
    "solution_architect", "enterprise_architect", "business_architect",
    "arb_member", "portfolio_manager", "cto", "procurement",
    "application_manager", "platform_admin", "security_architect",
    "data_architect", "technology_architect", "application_architect",
    "data_architect",
    # R1-B36 (TB-0146): promoted from unassignable to assignable.
    "finance", "compliance", "risk", "operations", "non_technical_owner",
]
