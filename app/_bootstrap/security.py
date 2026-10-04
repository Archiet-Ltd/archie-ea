"""
Security headers and middleware — called after extensions init.
"""

import os
import re
import secrets
import time

from jinja2.ext import Extension


# Matches a <script> opening tag that does not already carry a nonce attribute.
# The negative lookahead makes it idempotent, so re-processing a template is a
# no-op rather than stacking duplicate attributes.
_SCRIPT_OPEN_RE = re.compile(r"<script(?![^>]*\bnonce\b)(?=[\s>])", re.IGNORECASE)

# Same trick for <style> elements (ARCH-070). Nonce-ing them is what lets
# style-src-elem drop 'unsafe-inline'.
_STYLE_OPEN_RE = re.compile(r"<style(?![^>]*\bnonce\b)(?=[\s>])", re.IGNORECASE)


class CspNonceExtension(Extension):
    """Add the CSP nonce to template-authored <script> tags, at compile time.

    This replaces an ``after_request`` hook that ran the same substitution over
    the *finished HTML body*. That was not a defence: it handed the nonce to
    every script in the response, including one injected through an XSS hole -
    which is precisely the case a nonce exists to stop. Combined with
    'strict-dynamic', an injected script received the nonce, executed, and could
    then load further code of the attacker's choosing. The CSP was decorative.

    Rewriting the template *source* instead draws the line in the right place.
    A tag written by a template author is nonce'd; markup that arrives later
    through ``{{ variable }}`` interpolation is not, because by then the template
    has already been compiled. Injected scripts therefore have no nonce and are
    blocked, while the 517 legitimate script tags across 263 templates keep
    working without being edited one by one.
    """

    def preprocess(self, source, name, filename=None):
        source = _SCRIPT_OPEN_RE.sub('<script nonce="{{ csp_nonce }}"', source)
        # ARCH-070: the same compile-time treatment for template-authored
        # <style> blocks. It has to happen here rather than in an after_request
        # body rewrite for exactly the reason the script case does - a rewrite
        # of the finished body would hand the nonce to injected markup too.
        return _STYLE_OPEN_RE.sub('<style nonce="{{ csp_nonce }}"', source)

# Matches content-hashed static filenames, e.g. "tailwind-output.b257857d.css".
# Such URLs change whenever the bytes change, so they are safe to cache forever.
_FINGERPRINTED_RE = re.compile(r"\.[0-9a-f]{8,}\.[^./]+$")


def init_security(app):
    """Register security headers and guardrails."""

    # Must be added before any template is compiled: Jinja caches compiled
    # templates, and preprocess() only runs on compilation.
    app.jinja_env.add_extension(CspNonceExtension)

    @app.before_request
    def generate_csp_nonce():
        """Generate a unique CSP nonce per request for script-src."""
        from flask import g

        g.csp_nonce = secrets.token_urlsafe(32)

    @app.context_processor
    def inject_csp_nonce():
        """Expose csp_nonce to all templates (for manual use if needed)."""
        from flask import g

        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def add_security_headers(response):
        from flask import g

        nonce = getattr(g, "csp_nonce", "")

        # The nonce is applied by CspNonceExtension when a template is compiled,
        # not by rewriting the response body here. Rewriting the body nonce'd
        # injected scripts too, which defeated the entire mechanism. See the
        # extension's docstring.

        # Standard security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        if getattr(g, "allow_framing", False):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "same-origin"

        # ARCH-072: Permissions-Policy and Cross-Origin-Opener-Policy were
        # audited as absent. Both added, scoped so neither silently breaks a
        # feature already in use:
        #  - Cross-Origin-Opener-Policy: same-origin isolates this origin's
        #    browsing-context group from cross-origin openers/popups. The app
        #    has no cross-origin popup+window.opener flow (no OAuth popup, no
        #    postMessage handshake with another origin's popup — every
        #    window.open()/postMessage() call in app/static/js targets a
        #    same-origin window or an in-page iframe), so there is nothing
        #    for it to sever.
        #  - Permissions-Policy denies the sensitive device features
        #    (camera, microphone, geolocation, payment, usb) everywhere
        #    except where a feature is genuinely delegated today: the
        #    codegen workbench embeds an Expo Snack simulator
        #    (app/templates/codegen/_wb_ide.html,
        #    _wb_right_panel.html) in a cross-origin iframe whose `allow=`
        #    attribute requests exactly these features. A blanket `()` here
        #    would silently break that preview regardless of the iframe's
        #    own allow attribute, since the top-level Permissions-Policy
        #    gates delegation. snack.expo.dev is carried over from the CSP's
        #    existing frame-src allowance for the same embed.
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        _snack = '"https://snack.expo.dev"'
        response.headers["Permissions-Policy"] = (
            f"camera=(self {_snack}), "
            f"microphone=(self {_snack}), "
            f"geolocation=(self {_snack}), "
            f"payment=(self {_snack}), "
            f"usb=(self {_snack})"
        )

        # Content-Security-Policy
        # Development: permissive — allow CDN scripts (Alpine.js, Lucide, DOMPurify) + unsafe-inline
        # Production: nonce-based strict policy
        # NOTE: 'strict-dynamic' must NOT be used in dev — it overrides CDN host allowlists
        # and blocks Alpine.js/Lucide/DOMPurify which are loaded from unpkg/jsdelivr without nonces.
        from flask import current_app as _app

        # Conditionally add GA4/Clarity hosts to CSP when configured
        _ga4_hosts = ""
        _clarity_hosts = ""
        if _app.config.get("GA4_MEASUREMENT_ID"):
            _ga4_hosts = " https://www.googletagmanager.com https://www.google-analytics.com https://region1.google-analytics.com"
        if _app.config.get("CLARITY_PROJECT_ID"):
            _clarity_hosts = " https://www.clarity.ms https://c.bing.com"

        if _app.debug:
            csp_directives = [
                "default-src 'self'",
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                "https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com "
                "https://d3js.org https://cdn.segment.com https://www.google-analytics.com "
                "https://esm.sh" + _ga4_hosts + _clarity_hosts,
                "style-src 'self' 'unsafe-inline' "
                "https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com "
                "https://fonts.googleapis.com",
                "font-src 'self' data: https://fonts.googleapis.com https://fonts.gstatic.com https://cdn.jsdelivr.net",
                "connect-src 'self' https://unpkg.com https://cdn.jsdelivr.net https://cdn.segment.com https://www.google-analytics.com https://esm.sh ws://localhost:* wss://localhost:*" + _ga4_hosts + _clarity_hosts,
                "img-src 'self' data: blob: https://api.qrserver.com" + _ga4_hosts + _clarity_hosts,
                "frame-src 'self' https://snack.expo.dev",
                "object-src 'none'",
                "report-uri /api/csp-report",
            ]
        else:
            # Production. Dead allowances removed: unpkg, jsdelivr, cdnjs and
            # d3js have ZERO references anywhere in app/templates or
            # app/static/js (excluding the vendored copies), because ADR 0005
            # vendored those libraries - but the policy kept permitting them.
            # A permitted host that nothing uses is a standing invitation: an
            # injected tag pointing at it would still execute.
            #
            # cdn.segment.com and www.google-analytics.com are removed for the
            # reason ADR 0005 §3 already states as fact - enabling third-party
            # telemetry should require widening this policy explicitly, so the
            # decision is visible in review rather than silent.
            #
            # esm.sh STAYS: it is genuinely live. codegen/workbench.html:337
            # builds "https://esm.sh/" in JavaScript and dynamically imports
            # CodeMirror 6 from it. The air-gap gate cannot see that, because it
            # scans for static src= attributes - so this is a real external
            # dependency that ADR 0005's "browser egress: zero" claim misses.
            # ARCH-070 remaining half: 'unsafe-eval' stays, measured rather than
            # assumed. Swapping the standard Alpine.js build for the
            # CSP-compatible one (alpinejs-csp, which drops `new Function(...)`
            # expression evaluation and would let this directive drop
            # 'unsafe-eval') was evaluated and rejected FOR NOW:
            # `scripts/check_alpine_csp_compat.py` scans every `x-*`/`@*`/`:*`
            # Alpine directive in app/templates + app/modules and classifies
            # each expression as CSP-build-safe (bare property/method access)
            # or not (contains JS operators, ternaries, template literals,
            # comparisons, etc. — the CSP build cannot evaluate these; they'd
            # need rewriting into component methods). Result: 17,997 Alpine
            # expressions across 529 templates, of which 8,523 (47%) are NOT
            # CSP-build-safe — e.g. `x-show="!loading && filteredSpecs.length
            # === 0"`, `x-bind:class="loading ? 'animate-spin' : ''"`,
            # `x-text="`${endpointList(spec).length} endpoint(s)`"`. A
            # half-migrated Alpine build is a broken application (inert
            # dropdowns, dead toggles) across most interactive pages, not a
            # contained regression, so this is staged work, not a swap made
            # here. Re-run the scanner after any large front-end pass to see
            # whether the incompatible count has fallen enough to reconsider.
            # Residual risk while 'unsafe-eval' stays: script-src already
            # carries a nonce with 'strict-dynamic' and NO 'unsafe-inline', so
            # an attacker still needs a script tag with a valid nonce (or a
            # descendant of one) to execute anything at all — 'unsafe-eval'
            # only matters if such a script also wants to call eval()/Function()
            # itself, which narrows the exploitable surface to "already got a
            # nonce'd script running" rather than arbitrary injected markup.
            csp_directives = [
                "default-src 'self'",
                # ARCH-070: 'unsafe-eval' REMOVED. Alpine's new Function() evaluator
                # is replaced by the CSP-safe interpreter in app/static/js/csp/
                # (wired in _head.html via Alpine.setEvaluator), so no Alpine
                # expression needs eval. Verified end-to-end in tests/csp/: the
                # evaluator matches native eval on 10,819 pure expressions with 0
                # divergences, and real rendered pages (composer/dashboard/
                # capability-map) run with 0 CSP violations under this policy.
                # d3-dsv's new Function() row parser is never reached (no page
                # calls d3.csv/tsv/dsvParse — only SVG/force/scale/zoom APIs), and
                # CodeMirror loads as ES modules from esm.sh (no eval), so nothing
                # else on the write surface needs the directive either.
                f"script-src 'self' 'nonce-{nonce}' 'strict-dynamic' "
                "https://esm.sh" + _ga4_hosts + _clarity_hosts,  # CodeMirror 6 ES module imports
                # ARCH-070. The blanket style-src 'unsafe-inline' is gone,
                # split into the two directives it was conflating:
                #
                #  - style-src-elem covers <style> blocks and <link
                #    rel=stylesheet>. This is the injection-relevant half: an
                #    XSS-injected <style> can exfiltrate form values through
                #    attribute-selector background-image requests, and can
                #    overlay the page for clickjacking. Template-authored
                #    <style> blocks are nonce'd at compile time by
                #    CspNonceExtension, so they keep working while injected
                #    ones - which never see the nonce - are blocked.
                #
                #  - style-src-attr covers style="..." attributes. 449 of those
                #    exist across app/templates today, and CSP has no nonce
                #    mechanism for attributes at all: the only ways to drop
                #    'unsafe-inline' here are to delete every one of them, or
                #    to enumerate 449 hashes and regenerate them on every edit.
                #    Narrowing the allowance to attributes only is the
                #    reduction that is verifiable without a browser; removing
                #    the attributes themselves is separate work.
                #
                # Alpine's :style / x-bind:style are unaffected either way -
                # they write element.style through the CSSOM, which CSP does
                # not govern.
                f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com",
                f"style-src-elem 'self' 'nonce-{nonce}' https://fonts.googleapis.com",
                "style-src-attr 'unsafe-inline'",
                "font-src 'self' data: https://fonts.googleapis.com https://fonts.gstatic.com",
                "connect-src 'self' https://esm.sh" + _ga4_hosts + _clarity_hosts,  # CodeMirror 6 ES module imports
                "img-src 'self' data: blob: https://api.qrserver.com" + _ga4_hosts + _clarity_hosts,
                "frame-src 'self' https://snack.expo.dev",
                "object-src 'none'",
                "base-uri 'self'",     # stop <base> rewriting every relative URL
                "form-action 'self'",  # stop an injected form posting off-site
                # Modern equivalent of X-Frame-Options, and must agree with it:
                # codegen preview routes set g.allow_framing to embed themselves,
                # so a flat 'none' here would block the very pages that opt in.
                "frame-ancestors 'self'"
                if getattr(g, "allow_framing", False)
                else "frame-ancestors 'none'",
                "report-uri /api/csp-report",
            ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # Cache-Control headers (T-033) — vary by content type
        if response.content_type:
            ct = response.content_type
            request_path = ""
            request_versioned = False
            try:
                from flask import request as _req

                request_path = _req.path
                request_versioned = bool(_req.args.get("v"))
            except RuntimeError:
                pass

            if request_path.startswith("/static/"):
                # Only cache-forever assets whose URL changes when their bytes
                # change: content-hashed filenames (…​.<hash>.css) or URLs carrying
                # an mtime ?v= cache-buster (see assets.asset_url). Un-versioned
                # static paths (e.g. /static/css/tailwind-output.css) can change in
                # place on deploy, so they must revalidate — otherwise `immutable`
                # pins clients to a stale build for a year (observed as an
                # unstyled login page after a CSS rebuild).
                if request_versioned or _FINGERPRINTED_RE.search(request_path):
                    response.headers["Cache-Control"] = (
                        "public, max-age=31536000, immutable"
                    )
                else:
                    # ETag / Last-Modified are already set, so revalidation is cheap
                    # (304 when unchanged) and a rebuilt file is picked up at once.
                    response.headers["Cache-Control"] = (
                        "public, max-age=0, must-revalidate"
                    )
            elif "text/html" in ct:
                # HTML pages: always revalidate
                response.headers["Cache-Control"] = (
                    "no-cache, no-store, must-revalidate"
                )
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            elif "application/json" in ct or request_path.startswith("/api/"):
                # API responses: never cache
                response.headers["Cache-Control"] = "no-store"

        return response

    # Wire MetricsCollector to request pipeline for automatic tracking, and
    # the same request into the Prometheus HTTP counters that
    # app/services/platform_slo_service.py reads -- one collector recording
    # every request, not two (CLAUDE.md ADR 0008).
    try:
        from flask import g, request as flask_request, request_started
        from app.core.observability.metrics import metrics_collector
        from app.services.prometheus_metrics import prometheus_metrics

        # Timing starts on the `request_started` signal rather than in a
        # `before_request` function: Flask sends that signal before any
        # `before_request` hook runs, including ones registered ahead of
        # this one, so a request is still timed even when an earlier hook
        # raises before this module's own `before_request` would have run.
        def _metrics_request_started(sender, **extra):
            g._metrics_start = time.monotonic()
            g._metrics_status_code = None

        # weak=False: blinker's default weak reference would let this local
        # closure be garbage-collected almost immediately (nothing else
        # holds a strong reference to it), silently dropping every request
        # from the metrics -- unlike @app.before_request, which keeps its
        # own strong reference list.
        request_started.connect(_metrics_request_started, app, weak=False)

        @app.after_request
        def _metrics_capture_status(response):
            # Stash the real status for teardown to read -- teardown_request
            # is not handed the response object.
            g._metrics_status_code = response.status_code
            return response

        @app.teardown_request
        def _metrics_record(exc):
            # teardown_request always runs -- even when a before_request
            # hook, the view, or an after_request hook raised and no normal
            # response was ever built (e.g. under TESTING's
            # PROPAGATE_EXCEPTIONS, where `_metrics_capture_status` above
            # never fires) -- so a failing request is still counted instead
            # of silently vanishing from the availability figures.
            start = getattr(g, "_metrics_start", None)
            if start is None:
                return
            duration_ms = (time.monotonic() - start) * 1000
            status_code = getattr(g, "_metrics_status_code", None)
            if status_code is None:
                # No response was ever produced: an unhandled failure, which
                # is exactly what "5xx counts as bad" means to capture.
                status_code = 500

            metrics_collector.record(
                flask_request.endpoint, status_code, duration_ms
            )

            # URL matching happens before any before_request hook runs, so
            # `url_rule` is set even when a hook raised early; only a 404
            # (no route matched at all) leaves it None, which is correctly
            # excluded -- there is no route prefix to attribute it to.
            url_rule = flask_request.url_rule
            if url_rule is not None:
                prometheus_metrics.track_http_request(
                    flask_request.method, url_rule.rule, status_code, duration_ms / 1000.0
                )

        app.logger.info("MetricsCollector wired to request pipeline")
    except Exception as e:
        app.logger.warning(f"MetricsCollector wiring failed (non-critical): {e}")

    # Batch import configuration (kept — used by import routes)
    app.config["MAX_CONCURRENT_BATCH_JOBS"] = 3
    app.config["BATCH_COMMIT_FAILURE_THRESHOLD"] = 0.5
    app.config["BATCH_RECOVERY_ENABLED"] = True
    app.config["BATCH_PROCESSING_TIMEOUT_MINUTES"] = 30
    app.config["BATCH_JOB_TIMEOUT_HOURS"] = 24

    # Initialize guardrails for AI safety
    try:
        from app.guardrails import GuardrailEnforcement

        _guardrail = GuardrailEnforcement(app)  # noqa: F841
        app.logger.info("Guardrail enforcement initialized successfully")
    except Exception as e:
        app.logger.warning(f"Guardrail initialization failed (non-critical): {e}")

    # Seed default AI prompt templates (non-critical)
    try:
        from app.services.ai_prompt_seeder import seed_default_ai_prompt_templates

        with app.app_context():
            seed_default_ai_prompt_templates()
        app.logger.info("AI prompt templates seeded (if missing).")
    except Exception as e:
        app.logger.warning(f"AI prompt seeding failed (non-critical): {e}")

    # DEV bootstrap for vendor catalog dev surface (dev only)
    try:
        if os.environ.get("DEV_VEND_CATALOG", "0") == "1":
            from app.extensions.vendor_catalog_bootstrap import bootstrap_vendor_catalog

            bootstrap_vendor_catalog(app)
            app.logger.info(
                "[DEV] Vendor Catalog bootstrap executed (DEV_VEND_CATALOG)"
            )
        else:
            app.logger.info(
                "[DEV] Vendor Catalog bootstrap skipped (DEV_VEND_CATALOG not set)"
            )
    except Exception as e:
        app.logger.warning(f"[DEV] Vendor Catalog bootstrap failed: {e}")
