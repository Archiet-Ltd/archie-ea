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
from app.middleware.tenant_decorators import platform_admin_required
from app.models.business_capabilities import BusinessCapability
from app.services.rate_limiter import rate_limit
from app.services.vendor_analysis.capability_based_vendor_selector import (
    CapabilityBasedVendorSelector,
)

main = Blueprint("main", __name__)

# Register sub-blueprints
main.register_blueprint(capability_framework_bp)
main.register_blueprint(framework_management_bp)


def _csv_safe(value):
    """Escape one CSV cell against spreadsheet formula injection.

    A value starting with ``=``, ``+``, ``-``, ``@``, or a leading tab/CR
    becomes a formula when the file is opened in Excel/Sheets. Prefixing it
    with a single quote keeps the value literal. Shared by every export in
    this module that writes a user-submitted string into a CSV cell.
    """
    if value is None:
        return ""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


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
@platform_admin_required
def waitlist_csv():
    """Export the waiting list as CSV. Platform admin only — this is prospect
    data across every organisation, not something an organisation's own
    admin should be able to download."""
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
        writer.writerow([
            _csv_safe(row.email),
            row.created_at.isoformat(),
            _csv_safe(row.source),
            _csv_safe(row.consent_text),
        ])

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=waitlist.csv"},
    )


def _notify_sales_of_inquiry(inquiry, page):
    """E-mail ``SALES_NOTIFY_EMAIL`` about one new sales enquiry.

    Store-only when the setting is unset: logged once as a warning, never
    raised, so a visitor's submission is never affected either way. Called
    after the inquiry row is already committed.
    """
    recipient = current_app.config.get("SALES_NOTIFY_EMAIL")
    if not recipient:
        current_app.logger.warning(
            "SALES_NOTIFY_EMAIL is not configured; product inquiry %s (offer=%s) was not emailed",
            inquiry.id,
            inquiry.offer,
        )
        return

    from app.flask_email import deliver_email

    delivered, error = deliver_email(
        recipient=recipient,
        subject="New enquiry: {}".format(inquiry.offer),
        template="public/email/sales_inquiry",
        inquiry=inquiry,
        page_url=page.url,
    )
    if not delivered:
        current_app.logger.error(
            "sales enquiry notification for product inquiry %s failed: %s",
            inquiry.id,
            error,
        )


@main.route("/offers/inquire", methods=["POST"])
@rate_limit(10, "1m", methods=("POST",))
def product_inquiry_submit():
    """Submit an inquiry from one of the fixed-price offer pages.

    One route serves every offer page; hidden fields say which page and
    family to reload. The offer identifier and the consent sentence shown
    next to the checkbox both come from that page's own front-matter, so
    what gets stored can never say something the visitor was not shown.
    """
    from flask import abort

    from app.models.product_inquiry import ProductInquiry
    from app.services.public_pages import build_jsonld, load_page

    page_family = request.form.get("family", "")
    page_slug = request.form.get("slug", "")
    page = load_page(page_family, slug=page_slug) if page_family and page_slug else None
    if page is None or page.cta != "inquiry":
        abort(404)

    offer = page.front_matter.get("offer")
    consent_text = page.front_matter.get("inquiry_consent_text")
    submitted_offer = request.form.get("offer", "")

    thanks = False
    error = None

    if not offer or not consent_text or submitted_offer != offer:
        error = "This request could not be matched to an offer. Please try again."
    else:
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip() or None
        consent = request.form.get("consent")

        if not email:
            error = "Please enter an email address."
        elif not consent:
            error = "You must agree to be contacted about this request."
        elif name is not None and len(name) > 200:
            error = "Please use a shorter name (200 characters or fewer)."
        else:
            try:
                valid = validate_email(email, check_deliverability=False)
                email = valid.normalized
            except EmailNotValidError:
                error = "Please enter a valid email address."

        if error is None:
            existing = ProductInquiry.query.filter_by(email=email, offer=offer).first()
            if existing is None:
                inquiry = ProductInquiry(
                    email=email,
                    name=name,
                    offer=offer,
                    consent_text=consent_text,
                )
                db.session.add(inquiry)
                db.session.commit()
                _notify_sales_of_inquiry(inquiry, page)
            thanks = True

    return render_template(
        "public/page.html",
        page=page,
        jsonld=build_jsonld(page),
        thanks=thanks,
        error=error,
    )


@main.route("/admin/product-inquiries.csv")
@platform_admin_required
def product_inquiries_csv():
    """Export product inquiries as CSV. Platform admin only — this is prospect
    data across every organisation, not something an organisation's own
    admin should be able to download."""
    from app.models.product_inquiry import ProductInquiry

    rows = (
        ProductInquiry.query
        .order_by(ProductInquiry.created_at.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "name", "offer", "created_at", "consent_text"])
    for row in rows:
        writer.writerow([
            _csv_safe(row.email),
            _csv_safe(row.name or ""),
            _csv_safe(row.offer),
            row.created_at.isoformat(),
            _csv_safe(row.consent_text),
        ])

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=product-inquiries.csv"},
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
    """Serve sitemap.xml for SEO — generated from public content pages."""
    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    base_url = "https://entelim.org"
    urls = []
    # Homepage is not a content page but is the most important URL
    urls.append(
        f"  <url><loc>{base_url}/</loc><priority>1.0</priority></url>"
    )
    # The /vs comparison hub is a view, not a content page from load_all_pages(),
    # so it needs its own entry here, same as the homepage above.
    urls.append(
        f"  <url><loc>{base_url}/vs</loc></url>"
    )
    for p in pages:
        urls.append(
            f"  <url><loc>{base_url}{p.url}</loc></url>"
        )
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>"
    from flask import Response
    return Response(xml, mimetype="application/xml")


@main.route("/llms.txt")
def llms_txt():
    """Serve llms.txt listing every public content page with a Capabilities section."""
    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    base_url = "https://entelim.org"
    lines = ["# Entelim"]
    lines.append("")
    lines.append(
        "> Entelim is the open-source Enterprise Intelligence Model: "
        "enter your website address and see your company."
    )
    lines.append("")

    # Capabilities section: modules and intelligence lenses
    module_pages = [p for p in pages if p.family == "module"]
    if module_pages:
        lines.append("## Capabilities")
        lines.append("")
        for p in module_pages:
            # Extract a quotable factual sentence from the page body
            sentence = _extract_first_sentence(p.body_html)
            lines.append(f"- [{p.title}]({base_url}{p.url}) — {sentence}")
        lines.append("")

    # All pages list (exclude module pages already listed in Capabilities)
    non_module_pages = [p for p in pages if p.family != "module"]
    for p in non_module_pages:
        lines.append(f"- [{p.title}]({base_url}{p.url})")
    text = "\n".join(lines) + "\n"
    from flask import Response
    return Response(text, mimetype="text/plain")


@main.route("/llms-full.txt")
def llms_full_txt():
    """Serve llms-full.txt with the full text of every public module, use-case and comparison page."""
    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    base_url = "https://entelim.org"
    lines = ["# Entelim — Full Content"]
    lines.append("")
    lines.append(
        "> Entelim is the open-source Enterprise Intelligence Model: "
        "enter your website address and see your company."
    )
    lines.append("")

    # Include modules, use-cases, and comparisons
    target_families = {"module", "function-per-segment", "comparison"}
    target_pages = [p for p in pages if p.family in target_families]

    for p in target_pages:
        lines.append(f"## {p.title}")
        lines.append("")
        lines.append(f"URL: {base_url}{p.url}")
        lines.append("")
        # Convert HTML body to plain text/markdown
        plain_text = _html_to_plain_text(p.body_html)
        lines.append(plain_text)
        lines.append("")
        lines.append("---")
        lines.append("")

    text = "\n".join(lines) + "\n"
    from flask import Response
    return Response(text, mimetype="text/plain")


def _extract_first_sentence(html: str) -> str:
    """Extract the first meaningful sentence from rendered HTML body.

    Takes the first sentence from the first <p> element (skipping headings)
    to avoid the h1 title running into the first paragraph.
    """
    import re
    import html as html_mod

    # Find the first <p> element content
    p_match = re.search(r"<p[^>]*>(.*?)</p>", html, flags=re.DOTALL | re.IGNORECASE)
    if p_match:
        text = p_match.group(1)
        # Strip any nested HTML tags from the paragraph content
        text = re.sub(r"<[^>]+>", "", text)
    else:
        # Fallback: remove all tags and use the whole text
        text = re.sub(r"<[^>]+>", "", html)

    text = html_mod.unescape(text)
    text = " ".join(text.split())  # Normalize whitespace

    # Find first sentence ending with . ! or ?
    match = re.search(r"([^.!?]*[.!?])", text)
    if match:
        sentence = match.group(1).strip()
        # Limit length
        if len(sentence) > 200:
            sentence = sentence[:197] + "..."
        return sentence
    return text[:200] if text else "No description available."


def _html_to_plain_text(html: str) -> str:
    """Convert rendered HTML body to plain text/markdown."""
    import re
    import html as html_mod

    # Remove <script> and <style> elements with their content FIRST
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style\b[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Convert common HTML elements to markdown-like plain text
    # Headings
    text = re.sub(r"<h1[^>]*>(.*?)</h1>", r"# \1", text, flags=re.DOTALL)
    text = re.sub(r"<h2[^>]*>(.*?)</h2>", r"## \1", text, flags=re.DOTALL)
    text = re.sub(r"<h3[^>]*>(.*?)</h3>", r"### \1", text, flags=re.DOTALL)

    # Links
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL)

    # Bold/italic
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)

    # Code
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
    text = re.sub(r"<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.DOTALL)

    # Lists
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1", text, flags=re.DOTALL)
    text = re.sub(r"</?(ul|ol)[^>]*>", "", text, flags=re.DOTALL)

    # Paragraphs and line breaks
    text = re.sub(r"</p>", "\n\n", text, flags=re.DOTALL)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.DOTALL)

    # Horizontal rule
    text = re.sub(r"<hr\s*/?>", "\n---\n", text, flags=re.DOTALL)

    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)

    # Unescape HTML entities
    text = html_mod.unescape(text)

    # Normalize whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


# ============================================================================
# PUBLIC CONTENT PAGES
# ============================================================================


@main.route("/vision")
def public_vision():
    """The vision / home narrative page."""
    from app.services.public_pages import build_jsonld, load_page

    page = load_page("vision")
    if page is None:
        from flask import abort
        abort(404)
    return render_template("public/page.html", page=page, jsonld=build_jsonld(page))


@main.route("/modules/<slug>")
def public_module(slug):
    """A module content page."""
    from app.services.public_pages import build_jsonld, load_page

    page = load_page("module", slug=slug)
    if page is None:
        from flask import abort
        abort(404)
    return render_template("public/page.html", page=page, jsonld=build_jsonld(page))


@main.route("/use-cases/<slug>")
def public_use_case(slug):
    """A function-per-segment content page."""
    from app.services.public_pages import build_jsonld, load_page

    page = load_page("function-per-segment", slug=slug)
    if page is None:
        from flask import abort
        abort(404)
    return render_template("public/page.html", page=page, jsonld=build_jsonld(page))


@main.route("/vs")
def public_comparison_hub():
    """The /vs comparison hub: links to every comparison page at its real URL.

    Reuses the same page loader as every other public page (no second loader):
    a comparison page's own front-matter `routing` decides its real address — most
    carry an archiet.ai canonical URL, so the hub links there rather than assuming
    every comparison page lives on entelim.org.
    """
    from app.services.public_pages import load_all_pages

    site_url = "https://entelim.org"
    pages = [p for p in load_all_pages() if p.family == "comparison"]
    entries = [
        {
            "competitor": p.front_matter.get("competitor", p.title),
            "real_url": p.canonical_url or f"{site_url}{p.url}",
        }
        for p in pages
    ]
    entries.sort(key=lambda entry: entry["competitor"].lower())
    return render_template("public/vs_hub.html", entries=entries)


@main.route("/vs/<slug>")
def public_comparison(slug):
    """A comparison content page."""
    from app.services.public_pages import build_jsonld, load_page

    page = load_page("comparison", slug=slug)
    if page is None:
        from flask import abort
        abort(404)
    return render_template("public/page.html", page=page, jsonld=build_jsonld(page))


@main.route("/how-archiet-runs-on-entelim")
def public_dogfood():
    """The dogfood / proof story page."""
    from app.services.public_pages import build_jsonld, load_page

    page = load_page("dogfood")
    if page is None:
        from flask import abort
        abort(404)
    return render_template("public/page.html", page=page, jsonld=build_jsonld(page))


@main.route(
    "/<any(about, security, privacy, terms, contact, features, pricing, docs, "
    "'architecture-health-check', 'team-annual-onboarding'):slug>"
)
def public_site_page(slug):
    """A fixed top-level marketing/legal page (one file per page under content/pages/site/)."""
    from app.services.public_pages import build_jsonld, load_page

    page = load_page("site", slug=slug)
    if page is None:
        from flask import abort
        abort(404)
    return render_template("public/page.html", page=page, jsonld=build_jsonld(page))


@main.route(
    "/<any('data-processing-agreement', 'cookie-policy', 'refund-policy', "
    "'commercial-licence'):slug>"
)
def public_legal_page(slug):
    """A legal page whose text awaits approval; 404 until LEGAL_PAGES_ENABLED is on."""
    from flask import abort

    from app.services.legal_pages import legal_pages_enabled
    from app.services.public_pages import build_jsonld, load_page

    if not legal_pages_enabled():
        abort(404)
    page = load_page("legal", slug=slug)
    if page is None:
        abort(404)
    return render_template("public/page.html", page=page, jsonld=build_jsonld(page))


@main.route("/signup")
def public_signup_redirect():
    """/signup is not a second form — it redirects to the real sign-up page."""
    return redirect(url_for("account.register"), code=301)


@main.route("/register")
def public_register_redirect():
    """/register is not a second form — it redirects to the real sign-up page."""
    return redirect(url_for("account.register"), code=301)


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


# ── demo company website page ──────────────────────────────────────────────


@main.route("/demo/lantern-quay")
def demo_lantern_quay():
    """Public one-page website for the Lantern Quay Systems demonstration."""
    from app.models.organization import Organization

    org = Organization.query.filter_by(slug="lantern-quay").first()
    if org is None:
        return render_template("main/demo_lantern_quay.html", org=None)
    return render_template("main/demo_lantern_quay.html", org=org)
