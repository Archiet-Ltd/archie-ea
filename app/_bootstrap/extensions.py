import logging

logger = logging.getLogger(__name__)
"""
Extension initialization — called early in create_app().
"""


def init_extensions(app):
    """Call init_app() on every Flask extension."""
    from app.extensions import compress, csrf, db, login_manager, mail

    mail.init_app(app)
    db.init_app(app)

    # Request/job/assistant-run tracing (app/utils/tracing.py). Registered
    # before every other request hook so a request that an earlier hook turns
    # away (rate limit, session timeout, tenant check) still gets its id.
    from app.utils.tracing import install_tracing

    install_tracing(app)

    login_manager.init_app(app)

    # JSON 401 for API endpoints instead of redirect
    @login_manager.unauthorized_handler
    def _unauthorized():
        from flask import jsonify, redirect, request, url_for

        wants_json = (
            "/api/" in request.path
            or "/ai-chat/" in request.path
            or request.content_type == "application/json"
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json:
            resp = jsonify({"success": False, "error": "Authentication required"})
            resp.status_code = 401
            return resp
        # Site-relative, with the query string: the login view only follows a
        # rooted path (safe_next_url), so an absolute request.url here was
        # always discarded and every signed-out visitor landed on the dashboard
        # instead of the page they asked for.
        next_path = request.full_path.rstrip("?") if request.query_string else request.path
        return redirect(url_for("account.login", next=next_path))

    csrf.init_app(app)

    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        import logging
        from flask import request as _req, session, render_template, jsonify
        _log = logging.getLogger(__name__)
        _log.error(
            "CSRF FAILURE: reason=%r | method=%s path=%s | "
            "form_keys=%s | has_csrf_token_field=%s | "
            "session_keys=%s | cookie_names=%s",
            e.description,
            _req.method,
            _req.path,
            list(_req.form.keys()),
            bool(_req.form.get("csrf_token")),
            list(session.keys()),
            list(_req.cookies.keys()),
        )
        # Return JSON for AJAX/API requests instead of HTML login page
        wants_json = (
            "/api/" in _req.path
            or "/ai-chat/" in _req.path
            or _req.content_type == "application/json"
            or _req.accept_mimetypes.best == "application/json"
            or _req.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json:
            return jsonify({
                "success": False,
                "error": "Your session has expired. Please refresh the page and try again.",
                "error_type": "csrf",
            }), 400
        from flask import flash
        flash("Your session has expired. Please try again.", "form-error")
        from app.modules.account.forms.account_forms import LoginForm
        form = LoginForm()
        return render_template("account/login.html", form=form), 400

    from werkzeug.exceptions import HTTPException

    @app.errorhandler(HTTPException)
    def handle_http_exception_on_api(e):
        """An /api/ path must answer in JSON even when it is refusing.

        A front end that asks for JSON and receives an HTML error page fails at
        JSON.parse, so the user sees a generic script error instead of "you do
        not have access" - the refusal is correct and the explanation is lost.
        Flask-Login's unauthorized_handler already does this for the routes it
        guards, but abort(401)/abort(403) raised by a role decorator bypasses it
        and renders HTML.

        Everything outside /api/ is returned untouched, so ordinary pages keep
        their existing error templates. Blueprint-level handlers are more
        specific than this one and still win where they are registered.
        """
        from flask import jsonify, request

        if "/api/" not in request.path:
            return e
        return jsonify({
            "success": False,
            "error": e.description,
            "error_type": (e.name or "error").lower().replace(" ", "_"),
        }), e.code

    from sqlalchemy.orm.exc import StaleDataError

    @app.errorhandler(StaleDataError)
    def handle_stale_data_error(e):
        """Someone else saved this record first — say so, don't show a crash.

        Optimistic locking turns a silent overwrite into a refused write, which
        is only an improvement if the person who was refused understands what
        happened. Without this handler they get a 500 and no idea their work was
        rejected, which reads as the product being broken rather than as the
        product protecting a colleague's edit.

        409 Conflict is the accurate status: the request was well-formed and the
        user is allowed to make it — it lost a race. The record on screen is
        stale, so reloading is genuinely the fix, and the message says that
        rather than asking the user to guess.
        """
        from flask import flash, jsonify, redirect, render_template, request

        db.session.rollback()
        logger.warning(
            "optimistic lock conflict: method=%s path=%s user=%s",
            request.method, request.path,
            getattr(getattr(request, "user", None), "id", "anonymous"),
        )
        message = (
            "Someone else saved changes to this record while you were editing it. "
            "Your changes were not saved. Reload the page to see their version, "
            "then re-apply your edits."
        )
        wants_json = (
            "/api/" in request.path
            or request.content_type == "application/json"
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json:
            return jsonify({
                "success": False,
                "error": message,
                "error_type": "conflict",
                "conflict": True,
            }), 409
        flash(message, "warning")
        # Back to the record they were editing, which now reloads the saved
        # version — a redirect rather than a re-render, so a refresh does not
        # resubmit the losing write.
        referrer = request.referrer
        if referrer and request.host_url.rstrip("/") in referrer:
            return redirect(referrer)
        return render_template(
            "errors/generic_error.html",
            status_code=409,
            error={
                "error": "Someone else saved changes to this record while you "
                         "were editing it, so your changes were not saved.",
                "recovery_action": "Reload the page to see their version, then "
                                   "re-apply your edits.",
            },
        ), 409

    from app.services.billing_plans import PlanLimitReached

    @app.errorhandler(PlanLimitReached)
    def handle_plan_limit_reached(e):
        """Someone could not be added because the organisation's plan is full.

        Raised by the flush-time guard, so it reaches here from any path that
        adds a person: single sign-on, invitations, signup, an API. The person
        who triggered it is told why and nothing is saved.
        """
        from flask import jsonify, render_template, request

        db.session.rollback()
        logger.info("plan limit refused an addition: path=%s status=%s", request.path, e.status)
        wants_json = (
            "/api/" in request.path
            or request.content_type == "application/json"
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json:
            return jsonify({
                "success": False,
                "error": str(e),
                "error_type": "plan_limit_reached",
            }), 409
        return render_template(
            "errors/generic_error.html",
            status_code=409,
            error={
                "error": str(e),
                "recovery_action": "Ask an administrator of the organisation to upgrade "
                                   "its plan, then try again.",
            },
        ), 409

    compress.init_app(app)

    # Optional: Flask-Migrate
    try:
        from flask_migrate import Migrate
        Migrate(app, db)
    except ImportError:
        pass

    # Optional: Flask-RQ
    try:
        from flask_rq import RQ
        RQ(app)
    except ImportError:
        pass

    # Optional: Flask-Babel (S2-01 i18n — date/number/currency formatting)
    try:
        from flask_babel import Babel

        def get_locale():
            from flask import request, session

            # 1. Explicit session override
            locale = session.get("locale")
            if locale:
                return locale
            # 2. Accept-Language header
            return request.accept_languages.best_match(
                ["en", "de", "fr", "es", "ja", "zh"],
                default=app.config.get("BABEL_DEFAULT_LOCALE", "en"),
            )

        def get_timezone():
            from flask import session

            tz = session.get("timezone")
            if tz:
                return tz
            return app.config.get("BABEL_DEFAULT_TIMEZONE", "UTC")

        # Flask-Babel >=3.0 uses constructor kwargs; older versions use decorators
        try:
            babel = Babel(app, locale_selector=get_locale, timezone_selector=get_timezone)
        except TypeError:
            babel = Babel(app)
            babel.localeselector(get_locale)
            babel.timezoneselector(get_timezone)

    except ImportError:
        app.logger.info("Flask-Babel not installed — i18n formatting unavailable")

    # Redis cache manager
    try:
        from app.extensions.cache import cache_manager
        cache_manager.init_app(app)
    except Exception as e:
        app.logger.warning(f"Redis cache initialization failed (non-critical): {e}")


def _scheduler_belongs_in_this_process() -> bool:
    """Whether THIS process (web or worker) should own the APScheduler jobs.

    Every job below is registered exactly once, in whichever process calls
    ``init_scheduler`` — there is no separate registration path for a worker.
    Historically that call happened unconditionally inside ``create_app()``,
    so a deployment with a dedicated jobs worker (``app/jobs/worker.py``,
    built from ``Dockerfile.worker``) would run every job TWICE: once in the
    web process and once in the worker.

    ``JOBS_RUN_IN_WORKER`` opts a deployment into the split: set on the web
    process only, it makes the web process skip registration entirely.
    ``RUNNING_AS_JOBS_WORKER`` is set only by ``app/jobs/worker.py`` itself, so
    that process registers unconditionally regardless of the first flag.
    Neither var set (today's default, and every existing test) preserves the
    original single-process behaviour exactly — the web process keeps running
    the scheduler until a deployment sets ``JOBS_RUN_IN_WORKER=1`` on it.
    """
    import os

    if os.environ.get("RUNNING_AS_JOBS_WORKER") == "1":
        return True
    return os.environ.get("JOBS_RUN_IN_WORKER") != "1"


def init_scheduler(app):
    """Initialize APScheduler for background workflow execution."""
    if app.testing:
        return
    if not _scheduler_belongs_in_this_process():
        app.logger.info(
            "APScheduler not started in this process — JOBS_RUN_IN_WORKER=1 and "
            "this is not the jobs worker; app/jobs/worker.py owns these jobs instead."
        )
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        import atexit

        scheduler = BackgroundScheduler()

        def run_scheduled_workflows():
            """APScheduler job: execute due EA workflow schedules per tenant.

            EAWorkflowSchedule is a TenantMixin model, so the due-schedule
            read and every downstream query (workflow definition, phase gate)
            must run inside each organisation's tenant scope.  This function
            visits every active organisation through run_for_each_tenant so
            the ORM isolation listeners filter rows automatically.
            """
            with app.app_context():
                try:
                    from app.jobs.tenant_safe_job import run_for_each_tenant
                    from app.services.ea_workflow_engine import EAWorkflowEngine

                    def _log_failure(result):
                        if not result.ok:
                            import logging
                            logging.getLogger(__name__).error(
                                "APScheduler ea-workflows failed for org %s: %s",
                                result.organization_id,
                                result.error,
                            )

                    run_for_each_tenant(
                        app,
                        "ea-workflow-schedules",
                        lambda organization_id: EAWorkflowEngine().run_due_schedules(),
                        on_result=_log_failure,
                    )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).error("APScheduler ea-workflows error: %s", exc)

        scheduler.add_job(
            func=run_scheduled_workflows,
            trigger=IntervalTrigger(minutes=5),
            id="ea_workflow_scheduler",
            name="EA Workflow Schedule Runner",
            replace_existing=True,
            max_instances=1,
        )

        # PLT-009: Weekly data maturity digest (Monday 8am UTC). Declares its
        # organisation per run: send_data_maturity_digest calls
        # app.jobs.tenant_safe_job.run_for_each_tenant, which sets
        # g.current_org_id via tenant_scope() for every active organisation.
        def run_data_maturity_digest():
            with app.app_context():
                try:
                    from app._bootstrap._digest_emails import send_data_maturity_digest
                    send_data_maturity_digest(app)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).error(
                        "APScheduler data-maturity-digest error: %s", exc
                    )

        from apscheduler.triggers.cron import CronTrigger

        scheduler.add_job(
            func=run_data_maturity_digest,
            trigger=CronTrigger(day_of_week="mon", hour=8, minute=0),
            id="data_maturity_digest",
            name="PLT-009 Weekly Data Maturity Digest",
            replace_existing=True,
            max_instances=1,
        )

        # PLT-031: Weekly executive summary (Monday 7am UTC). Declares its
        # organisation per run via run_for_each_tenant / tenant_scope, same as
        # the maturity digest above.
        def run_executive_summary():
            with app.app_context():
                try:
                    from app._bootstrap._digest_emails import send_executive_summary
                    send_executive_summary(app)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).error(
                        "APScheduler executive-summary error: %s", exc
                    )

        scheduler.add_job(
            func=run_executive_summary,
            trigger=CronTrigger(day_of_week="mon", hour=7, minute=0),
            id="executive_summary",
            name="PLT-031 Weekly Executive Summary",
            replace_existing=True,
            max_instances=1,
        )

        # Error digest (10 Sep 2026): read-only notification, not an
        # autonomous fix -- summarises new unresolved error_events rows and
        # emails platform admins so a human notices without having to
        # remember to open /admin/errors. 30 minutes, not weekly like the
        # other digests here, because catching degradation promptly is the
        # whole point; _digest_emails.send_error_digest no-ops (and sends
        # nothing) when there is nothing new since the last run.
        #
        # Named platform job, deliberately: error_events carries no
        # organisation predicate (an error is a platform fact, not a tenant
        # one -- see _digest_emails._get_platform_admin_recipients), so there
        # is no per-organisation tenant_scope to run this inside.
        def run_error_digest():
            with app.app_context():
                try:
                    from app._bootstrap._digest_emails import send_error_digest
                    send_error_digest(app)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).error(
                        "APScheduler error-digest error: %s", exc
                    )

        scheduler.add_job(
            func=run_error_digest,
            trigger=IntervalTrigger(minutes=30),
            id="error_digest",
            name="Unresolved Error Digest",
            replace_existing=True,
            max_instances=1,
        )

        # An approval past its 15-minute review-by time stays pending and
        # actionable (never auto-expires) but must not go unseen —
        # this notifies each affected organisation's administrators once per
        # overdue row. Platform-wide job (groups by organization_id itself,
        # same shape as run_error_digest above), 15 minutes to match the
        # window it is watching.
        def run_approval_escalation():
            with app.app_context():
                try:
                    from app.modules.ai_chat.services.ai_chat_approval_service import (
                        escalate_overdue_approvals,
                    )
                    escalate_overdue_approvals(app)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).error(
                        "APScheduler approval-escalation error: %s", exc
                    )

        scheduler.add_job(
            func=run_approval_escalation,
            trigger=IntervalTrigger(minutes=15),
            id="approval_escalation",
            name="Overdue Approval Escalation",
            replace_existing=True,
            max_instances=1,
        )

        # Teams meeting intelligence: Graph callRecords subscriptions expire
        # every 3 days — renew twice daily; renew_if_needed re-creates the
        # subscription if Graph has already dropped it. No-op when the
        # integration was never configured.
        #
        # Declares its organisation per run: TeamsMeetingService reads its
        # M365 configuration from APISettings, a TenantMixin model. Called
        # with no tenant context (as this job previously did), the isolation
        # listener applies no organisation predicate at all, so the query
        # silently resolves whichever organisation's row the database happens
        # to return first -- every other organisation's subscription then
        # never renews. Looping through run_for_each_tenant fixes that
        # without changing TeamsMeetingService itself: each iteration runs
        # inside tenant_scope(organization_id), so the existing APISettings
        # query is correctly filtered by that organisation.
        def run_teams_subscription_renewal():
            with app.app_context():
                import logging

                from app.jobs.tenant_safe_job import run_for_each_tenant

                log = logging.getLogger(__name__)

                def _renew_one_tenant(organization_id):
                    from app.services.teams_meeting_service import TeamsMeetingService
                    return TeamsMeetingService.renew_if_needed()

                def _log_result(result):
                    if not result.ok:
                        log.error(
                            "APScheduler teams-renewal error for organization_id=%s: %s",
                            result.organization_id, result.error,
                        )
                        return
                    status = (result.value or {}).get("status")
                    if status == "ok":
                        log.info(
                            "APScheduler: Teams subscription renewed for "
                            "organization_id=%s until %s",
                            result.organization_id, (result.value or {}).get("expiry"),
                        )

                try:
                    run_for_each_tenant(
                        app,
                        "teams-subscription-renewal",
                        _renew_one_tenant,
                        on_result=_log_result,
                    )
                except Exception as exc:
                    log.error("APScheduler teams-renewal error: %s", exc)

        scheduler.add_job(
            func=run_teams_subscription_renewal,
            trigger=IntervalTrigger(hours=12),
            id="teams_subscription_renewal",
            name="Teams Meeting Graph Subscription Renewal",
            replace_existing=True,
            max_instances=1,
        )

        # Typed ARB waiver expiry is opt-in and tenant-explicit. The database
        # advisory lock in the batch service prevents duplicate Gunicorn
        # schedulers from processing the same deployment concurrently.
        arb_expiry_registered = False
        if app.config.get("ARB_CONDITION_EXPIRY_ORGANIZATION_IDS"):
            def run_arb_waiver_expiry():
                with app.app_context():
                    try:
                        from app.modules.transformation_room.arb_waiver_expiry_batch_service import (
                            ARBWaiverExpiryBatchService,
                        )

                        result = ARBWaiverExpiryBatchService.run_configured()
                        import logging
                        log = logging.getLogger(__name__)
                        if result.failed_count:
                            log.error(
                                "APScheduler typed ARB waiver expiry partial failure: %s",
                                result.as_dict(),
                            )
                        elif result.selected_count or not result.lock_acquired:
                            log.info(
                                "APScheduler typed ARB waiver expiry: %s",
                                result.as_dict(),
                            )
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).error(
                            "APScheduler typed ARB waiver expiry error: %s", exc
                        )

            try:
                expiry_interval_minutes = int(
                    app.config["ARB_CONDITION_EXPIRY_INTERVAL_MINUTES"]
                )
                if expiry_interval_minutes <= 0:
                    raise ValueError("interval must be positive")
                scheduler.add_job(
                    func=run_arb_waiver_expiry,
                    trigger=IntervalTrigger(minutes=expiry_interval_minutes),
                    id="typed_arb_waiver_expiry",
                    name="Typed ARB Condition Waiver Expiry",
                    replace_existing=True,
                    max_instances=1,
                )
                arb_expiry_registered = True
            except Exception as exc:
                app.logger.error(
                    "Typed ARB waiver expiry scheduler job was not registered: %s",
                    exc,
                )

        # T-002: recurring capability-maturity projection. Closes the gap PR
        # #23's write-time ORM sync listeners cannot: the three raw-SQL
        # maturity writers in maturity_routes.py never fire an ORM event.
        # Named platform job (see the module docstring on
        # app/jobs/capability_projection_job.py): the projection is
        # deliberately all-tenant in one pass, guarded by job_lock alone.
        capability_projection_registered = False
        try:
            def run_capability_projection():
                with app.app_context():
                    from app.jobs.capability_projection_job import run_capability_projection_job

                    run = run_capability_projection_job()
                    if run.status == "failed":
                        app.logger.error(
                            "APScheduler capability projection failed: %s", run.as_dict()
                        )
                    else:
                        app.logger.info(
                            "APScheduler capability projection: %s", run.as_dict()
                        )

            projection_interval_minutes = int(
                app.config["CAPABILITY_PROJECTION_INTERVAL_MINUTES"]
            )
            if projection_interval_minutes <= 0:
                raise ValueError("interval must be positive")
            scheduler.add_job(
                func=run_capability_projection,
                trigger=IntervalTrigger(minutes=projection_interval_minutes),
                id="capability_projection",
                name="Capability Maturity Projection",
                replace_existing=True,
                max_instances=1,
            )
            capability_projection_registered = True
        except Exception as exc:
            app.logger.error(
                "Capability projection scheduler job was not registered: %s", exc
            )

        # T-003: recurring recompute of stale derived facts (DE-4, ADR-003).
        # The on-demand endpoint (POST /api/v1/intelligence/derivation/recompute)
        # covers the immediate case; this covers everything nobody clicked.
        # Declares its organisation per run: recompute_derived_facts calls
        # run_for_each_tenant, visiting only stale-carrying organisations.
        derived_recompute_registered = False
        try:
            def run_derived_recompute():
                with app.app_context():
                    from app.modules.intelligence.services.recompute_job import (
                        recompute_derived_facts,
                    )

                    run = recompute_derived_facts(app)
                    if run.failed:
                        app.logger.error(
                            "APScheduler derived-facts recompute partial failure: %s",
                            run.as_dict(),
                        )
                    else:
                        app.logger.info(
                            "APScheduler derived-facts recompute: %s", run.as_dict()
                        )

            derived_recompute_interval_minutes = int(
                app.config["DERIVED_RECOMPUTE_INTERVAL_MINUTES"]
            )
            if derived_recompute_interval_minutes <= 0:
                raise ValueError("interval must be positive")
            scheduler.add_job(
                func=run_derived_recompute,
                trigger=IntervalTrigger(minutes=derived_recompute_interval_minutes),
                id="derived_facts_recompute",
                name="Derived Fact Recompute",
                replace_existing=True,
                max_instances=1,
            )
            derived_recompute_registered = True
        except Exception as exc:
            app.logger.error(
                "Derived-facts recompute scheduler job was not registered: %s", exc
            )

# Event-log relay: copies undelivered outbox rows into event_log
        # per organisation. Runs every 5 seconds so consumers see events
        # with at most a few seconds of latency.
        def run_event_log_relay():
            with app.app_context():
                import logging

                from app.jobs.tenant_safe_job import run_for_each_tenant

                log = logging.getLogger(__name__)

                def _relay_one_tenant(_organization_id):
                    from app.services.event_log_service import relay_outbox_batch
                    return relay_outbox_batch()

                def _log_result(result):
                    if not result.ok:
                        log.error(
                            "event_log relay failed for org %s: %s",
                            result.organization_id, result.error,
                        )

                try:
                    run_for_each_tenant(
                        app,
                        "event-log-relay",
                        _relay_one_tenant,
                        on_result=_log_result,
                    )
                except Exception as exc:
                    log.error("event_log relay error: %s", exc)

        scheduler.add_job(
            func=run_event_log_relay,
            trigger=IntervalTrigger(seconds=5),
            id="event_log_relay",
            name="Event Log Outbox Relay",
            replace_existing=True,
            max_instances=1,
        )

        # Event-log partition maintenance: creates the next three months'
        # partitions if missing. Runs daily so partitions exist before any
        # outbox event needs them.  Platform job — partitions are shared
        # across all organisations.
        event_log_partition_registered = False
        try:
            def run_event_log_partition_maintenance():
                with app.app_context():
                    from app.services.event_log_service import (
                        ensure_future_partitions,
                    )
                    created = ensure_future_partitions(months_ahead=3)
                    app.logger.info(
                        "event_log partition maintenance: %s partitions created",
                        created,
                    )

            scheduler.add_job(
                func=run_event_log_partition_maintenance,
                trigger=CronTrigger(hour=3, minute=0),
                id="event_log_partition_maintenance",
                name="Event Log Partition Maintenance",
                replace_existing=True,
                max_instances=1,
            )
            event_log_partition_registered = True
        except Exception as exc:
            app.logger.error(
                "Event-log partition maintenance job was not registered: %s",
                exc,
            )

        # Per-organisation model-health / drift scan. Runs the
        # deterministic drift detector for every active organisation and
        # stores the report so the page reads a single row rather than
        # scanning the whole genome on every page load.
        model_health_registered = False
        try:
            def run_model_health_scan():
                with app.app_context():
                    from app.jobs.tenant_safe_job import run_for_each_tenant
                    from app.models.drift_report import DriftReport
                    from app.modules.genome.services.drift_detector import (
                        detect_model_drift,
                    )

                    def _scan_one(organization_id):
                        report = detect_model_drift(organization_id)
                        DriftReport.upsert(organization_id, report)
                        return report.get("summary", {}).get("total", 0)

                    run = run_for_each_tenant(
                        app, "model_health_scan", _scan_one
                    )
                    if run.failed:
                        app.logger.error(
                            "APScheduler model-health scan partial failure: %s",
                            run.as_dict(),
                        )
                    else:
                        app.logger.info(
                            "APScheduler model-health scan: %s", run.as_dict()
                        )

            model_health_interval_minutes = int(
                app.config["MODEL_HEALTH_SCAN_INTERVAL_MINUTES"]
            )
            if model_health_interval_minutes <= 0:
                raise ValueError("interval must be positive")
            scheduler.add_job(
                func=run_model_health_scan,
                trigger=IntervalTrigger(minutes=model_health_interval_minutes),
                id="model_health_scan",
                name="Model Health Drift Scan",
                replace_existing=True,
                max_instances=1,
            )
            model_health_registered = True
        except Exception as exc:
            app.logger.error(
                "Model-health scan scheduler job was not registered: %s", exc
            )

        # Remove any undeclared job ids BEFORE starting the scheduler —
        # every job must be in PLATFORM_JOBS or TENANT_JOBS in
        # app/jobs/tenant_safe_job.py, or it runs unfiltered.  If enforcement
        # raises, the scheduler is not started (fail closed).
        try:
            from app.jobs.tenant_safe_job import _remove_undeclared_jobs

            _remove_undeclared_jobs(scheduler)
        except Exception as exc:
            logger.exception(
                "init_scheduler: _remove_undeclared_jobs failed — scheduler not started: %s",
                exc,
            )
            raise

        scheduler.start()

        def _shutdown_scheduler():
            try:
                scheduler.pause()  # stop new jobs from firing before shutdown
                scheduler.shutdown(wait=False)
            except Exception as exc:
                logger.debug("suppressed error in init_scheduler._shutdown_scheduler (app/_bootstrap/extensions.py): %s", exc)  # prevent atexit race from crashing gunicorn master

        atexit.register(_shutdown_scheduler)
        app.extensions["ea_workflow_scheduler"] = scheduler
        scheduled_jobs = (
            "EA workflows (5 min), maturity digest (Mon 8am), "
            "executive summary (Mon 7am), Teams subscription renewal (12h), "
            "approval escalation (15 min), event log relay (5s)"
        )
        if arb_expiry_registered:
            scheduled_jobs += ", typed ARB waiver expiry (configured)"
        if capability_projection_registered:
            scheduled_jobs += ", capability maturity projection (interval)"
        if derived_recompute_registered:
            scheduled_jobs += ", derived-facts recompute (interval)"
        if model_health_registered:
            scheduled_jobs += ", model-health drift scan (interval)"
        if event_log_partition_registered:
            scheduled_jobs += ", event log partition maintenance (daily)"
        app.logger.info("APScheduler started: %s", scheduled_jobs)
    except ImportError:
        app.logger.warning("APScheduler not available — EA workflow schedules disabled")
    except Exception as exc:
        app.logger.error("APScheduler init failed: %s", exc)
