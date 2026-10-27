"""The public vendor catalogue's pages (ADR-0021): the Catalogue tab of Vendors.

Routes:
    - vendor_catalogue()                    GET  /applications/vendors/catalogue
    - vendor_catalogue_product(slug)        GET  /applications/vendors/catalogue/<slug>
    - vendor_catalogue_link(slug)           POST /applications/vendors/catalogue/<slug>/link

The catalogue is read-only to organisations: there is no route here that writes
to it. The only write is an organisation's own link from a vendor product it
uses to a catalogue product, scoped to that organisation.
"""

from flask import abort, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.models.user import Permission
from app.services import vendor_catalogue_service as catalogue

from . import unified_applications_bp

CHOOSE = "Choose one of your vendor products to link."
LAST_ATTEMPT = "vendor_catalogue_link_attempt"


def _attempt(slug, outcome, vendor_product_id=None):
    """Remember, for this person's next view of the page only, what their link attempt came to."""
    session[LAST_ATTEMPT] = {"slug": slug, "outcome": outcome, "vp": vendor_product_id}
    return redirect(url_for("unified_applications.vendor_catalogue_product", slug=slug))


def _link_message(product):
    """What this person's link attempt came to, read once: shown in the card and announced."""
    attempt = session.pop(LAST_ATTEMPT, None)
    if not attempt or attempt.get("slug") != product.catalogue_slug:
        return None
    outcome = attempt.get("outcome")
    if outcome == "done":
        names = catalogue.applications_using(attempt.get("vp"))
        if not names:
            return {"kind": "success", "text": f"{product.product_name} is now linked to your organisation."}
        shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        return {"kind": "success", "text": f"{product.product_name} is now linked to {shown}."}
    if outcome == "refused":
        return {"kind": "danger", "text": catalogue.NOT_YOURS}
    if outcome == "choose":
        return {"kind": "danger", "text": CHOOSE}
    return None


@unified_applications_bp.route("/vendors/catalogue")
@login_required
def vendor_catalogue():
    search = (request.args.get("search") or "").strip()[:100]
    rows = catalogue.catalogue_products(search)
    return render_template("vendors/catalogue_list.html", rows=rows, search=search)


@unified_applications_bp.route("/vendors/catalogue/<slug>")
@login_required
def vendor_catalogue_product(slug):
    product = catalogue.catalogue_product(slug)
    if product is None:
        abort(404)
    grouped = catalogue.published_facts(product)
    if not any(grouped.values()):
        abort(404)
    family = product.product_family
    links = catalogue.organisation_links_to(product)
    return render_template(
        "vendors/catalogue_detail.html",
        product=product,
        vendor=family.vendor if family else None,
        category=family.category if family else "",
        grouped=grouped,
        sections=catalogue.SECTION_ORDER,
        related=catalogue.related_slug_names(grouped),
        lifecycle=catalogue.lifecycle(product),
        org_options=[(vp.id, vp.name) for vp in catalogue.organisation_vendor_products()],
        links=links,
        can_link=current_user.can(Permission.GENERAL),
        link_message=_link_message(product),
    )


@unified_applications_bp.route("/vendors/catalogue/<slug>/link", methods=["POST"])
@login_required
def vendor_catalogue_link(slug):
    product = catalogue.catalogue_product(slug)
    if product is None:
        abort(404)
    try:
        vendor_product_id = int(request.form.get("vendor_product_id", ""))
    except ValueError:
        return _attempt(slug, "choose")
    try:
        link = catalogue.link_vendor_product(vendor_product_id, product, current_user.id)
    except catalogue.LinkRefused:
        return _attempt(slug, "refused")
    return _attempt(slug, "done", link.vendor_product_id)
