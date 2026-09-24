import csv
import io
import json

from email_validator import EmailNotValidError, validate_email
from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from app import db

# Import capability framework blueprint
from app.core.auth.decorators import admin_required
from app.main.capability_framework_routes import capability_framework_bp
from app.main.framework_management_routes import framework_management_bp
from app.models.business_capabilities import BusinessCapability
from app.services.rate_limiter import rate_limit
from app.services.vendor_analysis.capability_based_vendor_selector import (
    CapabilityBasedVendorSelector,
)

main = Blueprint("main", __name__)

# Register sub-blueprints
main.register_blueprint(capability_framework_bp)
main.register_blueprint(framework_management_bp)


@main.route("/", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.overview"))

    thanks = False
    error = None

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        consent = request.form.get("consent")

        if not email:
            error = "Please enter an email address."
        elif not consent:
            error = "You must agree that your email will be used only for launch news."
        else:
            try:
                valid = validate_email(email, check_deliverability=False)
                email = valid.normalized
            except EmailNotValidError:
                error = "Please enter a valid email address."
                return render_template("main/index.html", thanks=False, error=error)

            from app.models.waitlist_signup import WaitlistSignup

            existing = WaitlistSignup.query.filter_by(email=email).first()
            if existing is None:
                signup = WaitlistSignup(
                    email=email,
                    source="home_page",
                    consent_text="Email used only for launch news about Entelim.",
                )
                db.session.add(signup)
                db.session.commit()
            thanks = True

    return render_template("main/index.html", thanks=thanks, error=error)


@main.route("/admin/waitlist.csv")
@login_required
@admin_required
def waitlist_csv():
    """Export the waiting list as CSV. Admin only."""
    from app.models.waitlist_signup import WaitlistSignup

    rows = (
        WaitlistSignup.query
        .order_by(WaitlistSignup.created_at.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "created_at", "source", "consent_text"])
    for row in rows:
        writer.writerow([row.email, row.created_at.isoformat(), row.source, row.consent_text])

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=waitlist.csv"},
    )


@main.route("/login")
def login_redirect():
    """Convenience redirect — canonical login URL is /account/login."""
    return redirect(url_for("account.login"))


@main.route("/roadmaps")
def roadmaps_redirect():
    """Redirect /roadmaps to canonical /capability-roadmap."""
    return redirect(url_for("main.capability_roadmap"), code=301)


@main.route("/favicon.ico")
def favicon():
    """Suppress 404 log noise — no favicon file exists."""
    from flask import Response
    return Response(status=204)


@main.route("/portfolio/<path:subpath>")
def portfolio_redirect(subpath):
    """Redirect /portfolio/* to /enterprise/portfolio/* for convenience"""
    return redirect("/enterprise/portfolio/" + subpath, code=301)


@main.route("/dashboards/overview")
@login_required
def dashboards_overview():
    """Legacy overview URL — canonical landing page is dashboard.overview."""
    return redirect(url_for("dashboard.overview"))


@main.route("/dashboards/vendor-capability-matrix")
@login_required
def dashboards_vendor_capability_matrix():
    """Vendor-Capability Coverage Matrix — JS loads data from /capability-map/api/vendor-capability-matrix."""
    return render_template("dashboards/vendor_capability_matrix.html")


@main.route("/vendors")
@main.route("/vendors/")
@main.route("/vendors/<path:subpath>")
def vendors_redirect(subpath=None):
    """Redirect /vendors/* to /applications/vendors/* for convenience (FAR-016)

    Vendors are APPLICATION vendors (selling software products), not architecture vendors.
    """
    if subpath:
        return redirect("/applications/vendors/" + subpath, code=301)
    return redirect("/applications/vendors", code=301)


@main.route("/vendor-templates")
@main.route("/vendor-templates/<path:subpath>")
def vendor_templates_redirect(subpath=None):
    """Redirect /vendor-templates to canonical /architecture/vendor-templates."""
    return redirect(url_for("architecture.vendor_templates"), code=301)


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================


@main.route("/robots.txt")
def robots_txt():
    """Serve robots.txt for SEO"""
    return send_from_directory("static", "robots.txt")


@main.route("/sitemap.xml")
def sitemap_xml():
    """Serve sitemap.xml for SEO"""
    return send_from_directory("static", "sitemap.xml")


# NOTE: /health route removed — canonical version is global_health_check
# in app/_bootstrap/routes.py (CSRF-exempt, Redis + DB + memory checks).


# ============================================================================
# ERROR HANDLERS
# ============================================================================


@main.errorhandler(404)
def not_found_error(error):
    return render_template("errors/404.html"), 404


@main.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template("errors/500.html"), 500


@main.errorhandler(403)
def forbidden_error(error):
    return render_template("errors/403.html"), 403


# ============================================================================
# STATIC FILE SERVING FOR DEVELOPMENT
# ============================================================================


@main.route("/static/uploads/<path:filename>")
@login_required
def uploaded_files(filename):
    """Serve uploaded files with tenant isolation."""
    from flask import abort

    from app.middleware.tenant_files import verify_file_access

    upload_dir = current_app.config.get("UPLOAD_FOLDER", "uploads")

    # Extract org_id from path if tenant-scoped: /uploads/{org_id}/...
    parts = filename.split("/")
    if len(parts) >= 2 and parts[0].isdigit():
        file_org_id = int(parts[0])
        if not verify_file_access(file_org_id):
            abort(403)

    return send_from_directory(upload_dir, filename)


# ============================================================================
# DASHBOARD WIDGET ENDPOINTS
# ============================================================================


@main.route("/api/widgets/metrics")
@login_required
def get_widget_metrics():
    """
    Get metrics for dashboard widgets
    ---
    tags:
      - Dashboard
      - Widgets
    summary: Get widget metrics
    description: Get metrics data for dashboard widgets
    responses:
      200:
        description: Widget metrics
      500:
        description: Server error
    """
    try:
        # Sample metrics data - in production, this would come from database
        metrics = {
            "total_users": 1234,
            "active_sessions": 89,
            "revenue": 45678.90,
            "conversion_rate": 3.45,
        }
        return jsonify(metrics)

    except Exception as e:
        current_app.logger.error(f"Widget metrics fetch failed: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/widgets/charts/<chart_type>")
@login_required
def get_widget_chart(chart_type):
    """
    Get chart data for widgets
    ---
    tags:
      - Dashboard
      - Widgets
    summary: Get widget chart data
    description: Get chart data for a specific widget type
    parameters:
      - name: chart_type
        in: path
        type: string
        required: true
        enum: [revenue, users]
        description: Type of chart
    responses:
      200:
        description: Chart data
      404:
        description: Unknown chart type
      500:
        description: Server error
    """
    try:
        if chart_type == "revenue":
            data = {
                "labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
                "datasets": [
                    {
                        "label": "Revenue",
                        "data": [12000, 15000, 18000, 22000, 28000, 35000],
                    }
                ],
            }
        elif chart_type == "users":
            data = {
                "labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
                "datasets": [
                    {"label": "Active Users", "data": [450, 520, 580, 640, 720, 890]}
                ],
            }
        else:
            return jsonify({"error": "Unknown chart type"}), 404

        return jsonify(data)

    except Exception as e:
        current_app.logger.error(f"Widget chart fetch failed: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# BUSINESS CAPABILITY INTEGRATION
# ============================================================================


@main.route("/api/capabilities/summary")
@login_required
def get_capabilities_summary():
    """
    Get business capabilities summary for dashboards
    ---
    tags:
      - Capabilities
    summary: Get capabilities summary
    description: Get summary of business capabilities including counts by level, domain, and health
    responses:
      200:
        description: Capabilities summary
      500:
        description: Server error
    """
    try:
        capabilities = BusinessCapability.query.limit(2000).all()

        summary = {
            "total_capabilities": len(capabilities),
            "by_level": {},
            "by_domain": {},
            "health_distribution": {},
        }

        for cap in capabilities:
            # Count by level
            level = cap.level or "unknown"
            summary["by_level"][level] = summary["by_level"].get(level, 0) + 1

            # Count by domain
            domain = cap.business_domain or cap.category or "unknown"
            summary["by_domain"][domain] = summary["by_domain"].get(domain, 0) + 1

            # Health distribution (use strategic_importance as proxy for health)
            health = cap.strategic_importance or "unknown"
            summary["health_distribution"][health] = (
                summary["health_distribution"].get(health, 0) + 1
            )

        return jsonify(summary)

    except Exception as e:
        current_app.logger.error(f"Capabilities summary fetch failed: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capabilities/<int:capability_id>/vendors")
@login_required
def get_capability_vendors(capability_id):
    """
    Get vendors for a specific capability
    ---
    tags:
      - Capabilities
      - Vendors
    summary: Get capability vendors
    description: Get vendors that can support a specific business capability
    parameters:
      - name: capability_id
        in: path
        type: integer
        required: true
        description: Capability ID
    responses:
      200:
        description: List of vendors
      500:
        description: Server error
    """
    try:
        selector = CapabilityBasedVendorSelector()
        vendors = selector.find_vendors_for_capability(capability_id)

        return jsonify({"capability_id": capability_id, "vendors": vendors})

    except ValueError as e:
        # selector raises ValueError("Capability <id> not found") for a missing capability
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        current_app.logger.error(f"Capability vendors fetch failed: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# DASHBOARD EXPORT ENDPOINTS
# ============================================================================


@main.route("/integrations")
@login_required
def integrations():
    """System Integrations - Third-party service connections and API management"""
    return redirect(url_for("connectors.dashboard"))


@main.route("/settings")
@login_required
@admin_required
def settings():
    """System Settings - Application configuration and user preferences.

    Admin-only: the page reads and writes the global system_settings table, and
    it is linked only from the Administration section of the sidebar.
    """
    return render_template("settings/index.html")


@main.route("/api/system-settings", methods=["GET"])
@login_required
# system_settings is a GLOBAL table with no organization_id, so this is not
# tenant-scoped configuration - it is the platform's. With @login_required alone
# any authenticated user of any tenant could read it. The page that consumes it
# (settings/index.html) is linked only from the Administration sidebar section,
# so gating it on admin matches how it is actually reached.
@admin_required
def get_system_settings():
    """Return all saved system settings as JSON."""
    try:
        rows = db.session.execute(
            db.text("SELECT key, value FROM system_settings")
        ).fetchall()

        def _parse(v):
            # Values may be JSON or plain strings; one non-JSON row must not blank
            # out the whole settings response ("Expecting value: line 1 column 1").
            if v is None:
                return None
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return v

        result = {row[0]: _parse(row[1]) for row in rows}
        return jsonify({"settings": result, "status": "ok"})
    except Exception as e:
        current_app.logger.exception(f"Error loading system settings: {e}")
        return jsonify({"status": "error", "error": "Failed to load system settings"}), 500


@main.route("/api/system-settings/save", methods=["POST"])
@login_required
# The write half of the same global table: with @login_required alone, any
# authenticated user could rewrite platform-wide configuration for every tenant.
@admin_required
def save_system_settings():
    """Persist system settings to the database."""
    try:
        data = request.get_json(silent=True) or {}
        settings_data = data.get("settings", {})

        # Settings that must never be saved blank. app-name is the platform's
        # own branding, rendered in the header of every page: the QA audit of
        # 30 Aug 2026 (High #12) cleared the field, saved, and got a 200 -- the
        # application name was gone platform-wide with no validation anywhere,
        # client or server. A settings endpoint that accepts any key with any
        # value will eventually be handed an empty one.
        REQUIRED_NON_EMPTY = {"app-name"}
        blank = sorted(
            key for key in REQUIRED_NON_EMPTY
            if key in settings_data and not str(settings_data.get(key) or "").strip()
        )
        if blank:
            return jsonify({
                "status": "error",
                "message": "These settings cannot be empty: %s" % ", ".join(blank),
                "fields": blank,
            }), 400

        for key, value in settings_data.items():
            db.session.execute(
                db.text(
                    "INSERT INTO system_settings (key, value, updated_at) "
                    "VALUES (:key, :value, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value = :value, updated_at = NOW()"
                ),
                {"key": str(key), "value": json.dumps(value)},
            )
        db.session.commit()
        return jsonify({"status": "saved", "count": len(settings_data)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving system settings: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# Register EA workflow routes (adds routes to main blueprint)
from app.main import routes_ea_workflows

routes_ea_workflows.register_ea_workflow_routes(main)
