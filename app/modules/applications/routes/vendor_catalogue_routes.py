"""The public vendor catalogue's pages (ADR-0021): the Catalogue tab of Vendors.

Routes:
    - vendor_catalogue()                    GET  /applications/vendors/catalogue
    - vendor_catalogue_product(slug)        GET  /applications/vendors/catalogue/<slug>
    - vendor_catalogue_link(slug)           POST /applications/vendors/catalogue/<slug>/link

The catalogue is read-only to organisations: there is no route here that writes
to it. The only write is an organisation's own link from a vendor product it
uses to a catalogue product, scoped to that organisation.
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.services import vendor_catalogue_service as catalogue

from . import unified_applications_bp


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
        links=catalogue.organisation_links_to(product),
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
        flash("Choose one of your vendor products to link.", "error")
        return redirect(url_for("unified_applications.vendor_catalogue_product", slug=slug))
    try:
        link = catalogue.link_vendor_product(vendor_product_id, product, current_user.id)
    except catalogue.LinkRefused as exc:
        flash(str(exc), "error")
        return redirect(url_for("unified_applications.vendor_catalogue_product", slug=slug))
    flash(f"{link.vendor_product.name} is now linked to {product.product_name}.", "success")
    return redirect(url_for("unified_applications.vendor_catalogue_product", slug=slug))
