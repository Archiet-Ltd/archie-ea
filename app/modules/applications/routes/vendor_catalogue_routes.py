"""The public vendor catalogue's pages (ADR-0021): the Catalogue tab of Vendors.

Routes:
    - vendor_catalogue()                    GET  /applications/vendors/catalogue
    - vendor_catalogue_product(slug)        GET  /applications/vendors/catalogue/<slug>
    - vendor_catalogue_link(slug)           POST /applications/vendors/catalogue/<slug>/link

The catalogue is read-only to organisations: there is no route here that writes
to it. The only write is an organisation's own link from a vendor product it
uses to a catalogue product, scoped to that organisation.
"""

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models.user import Permission
from app.services import vendor_catalogue_service as catalogue

from . import unified_applications_bp

CHOOSE = "Choose one of your vendor products to link."


def _link_message(product, links):
    """What the last link attempt came to, from the redirect's query string: shown in the card and announced."""
    outcome = request.args.get("link")
    if outcome == "done":
        try:
            linked = int(request.args.get("vp", ""))
        except ValueError:
            return None
        names = [link.vendor_product.name for link in links if link.vendor_product_id == linked]
        if names:
            return {"kind": "success", "text": f"{names[0]} is now linked to {product.product_name}."}
        return None
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
        link_message=_link_message(product, links),
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
        return redirect(url_for("unified_applications.vendor_catalogue_product", slug=slug, link="choose"))
    try:
        link = catalogue.link_vendor_product(vendor_product_id, product, current_user.id)
    except catalogue.LinkRefused:
        return redirect(url_for("unified_applications.vendor_catalogue_product", slug=slug, link="refused"))
    return redirect(url_for("unified_applications.vendor_catalogue_product", slug=slug, link="done",
                            vp=link.vendor_product_id))
