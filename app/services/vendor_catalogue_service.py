"""The public vendor catalogue (ADR-0021): reading, linking, research and review.

One store: the catalogue product is the shared ``VendorProductDetail`` (under a
shared ``VendorProductFamily`` and ``VendorOrganization``), its facts are
``VendorCatalogueFact`` rows, and an organisation's link from a vendor product
it uses is a ``VendorCatalogueLink`` row. Lifecycle dates stay where the
end-of-life sync already writes them (``VendorProductFamily.end_of_life_date``).

Research pipeline: ``research_product`` takes a product's research plan (the
facts we intend to record, in our own words, each with the public page it
comes from and the words that page must contain) and checks every source page:
robots.txt first, then a fetch, then a look for the evidence words. A fact
whose page confirms it is written as a draft with today's check date; a fact
whose page cannot be read or does not confirm it is written as a draft with no
check date, so it cannot be published. Nothing here publishes: publishing is
``publish_product``, run by a person after review. No documentation text is
stored, only our wording and the address.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.robotparser
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from app import db
from app.middleware.tenant_context import current_org_id
from app.models.vendor.vendor_catalogue import (
    FACT_KINDS,
    VendorCatalogueFact,
    VendorCatalogueLink,
)
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.models.vendor.vendor_product import VendorProductDetail, VendorProductFamily

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "vendor_catalogue"
PLAN_PATH = DATA_DIR / "research_plan.json"
REVIEWED_PATH = DATA_DIR / "reviewed.json"

USER_AGENT = "EntelimCatalogueResearch/1.0 (+https://entelim.com)"
FETCH_TIMEOUT = 20

# Display order of the record page's sections.
SECTION_ORDER = (
    ("component", "Components"),
    ("service", "Services"),
    ("interface", "Interfaces"),
    ("relationship", "Relationships"),
    ("capability", "Typical capabilities"),
    ("integration", "Common integrations"),
    ("certification", "Certifications"),
    ("data_region", "Data regions"),
    ("sub_processor", "Sub-processors"),
    ("price_tier", "List-price tiers"),
)


# ── Reading ─────────────────────────────────────────────────────────────────────


def _published(query):
    return query.filter(VendorCatalogueFact.review_state == "published")


def catalogue_products(search: str = ""):
    """Catalogue products with at least one published fact, with their counts."""
    q = (
        db.session.query(VendorProductDetail, db.func.count(VendorCatalogueFact.id))
        .join(VendorCatalogueFact, VendorCatalogueFact.product_detail_id == VendorProductDetail.id)
        .filter(VendorProductDetail.catalogue_slug.isnot(None))
        .filter(VendorCatalogueFact.review_state == "published")
        .group_by(VendorProductDetail.id)
        .order_by(VendorProductDetail.product_name)
    )
    if search:
        like = f"%{search.strip()}%"
        q = q.join(VendorProductFamily, VendorProductFamily.id == VendorProductDetail.family_id).join(
            VendorOrganization, VendorOrganization.id == VendorProductFamily.vendor_id
        ).filter(
            db.or_(
                VendorProductDetail.product_name.ilike(like),
                VendorOrganization.name.ilike(like),
                VendorProductFamily.category.ilike(like),
            )
        )
    rows = []
    for product, count in q.all():
        family = product.product_family
        summary = _published(
            VendorCatalogueFact.query.filter_by(product_detail_id=product.id, kind="summary")
        ).first()
        rows.append(
            {
                "product": product,
                "vendor": family.vendor.name if family and family.vendor else "",
                "category": family.category if family else "",
                "summary": summary,
                "fact_count": count,
            }
        )
    return rows


def catalogue_product(slug: str) -> Optional[VendorProductDetail]:
    return VendorProductDetail.query.filter_by(catalogue_slug=slug).first()


def published_facts(product: VendorProductDetail) -> dict:
    """Published facts of one product, grouped by kind."""
    facts = _published(
        VendorCatalogueFact.query.filter_by(product_detail_id=product.id)
    ).order_by(VendorCatalogueFact.kind, VendorCatalogueFact.id).all()
    grouped = {kind: [] for kind in FACT_KINDS}
    for fact in facts:
        grouped[fact.kind].append(fact)
    return grouped


def related_slug_names(grouped: dict) -> dict:
    """Integration facts name another catalogue product by slug; map slug to name."""
    slugs = {
        (f.attributes or {}).get("related_slug")
        for f in grouped.get("integration", [])
        if (f.attributes or {}).get("related_slug")
    }
    if not slugs:
        return {}
    rows = VendorProductDetail.query.filter(VendorProductDetail.catalogue_slug.in_(slugs)).all()
    return {r.catalogue_slug: r.product_name for r in rows}


def lifecycle(product: VendorProductDetail) -> dict:
    """Lifecycle as the end-of-life sync recorded it; never invented."""
    family = product.product_family
    return {
        "end_of_life_date": family.end_of_life_date if family else None,
        "last_synced": family.last_update_date if family else None,
        "source_url": "https://endoflife.date/",
    }


# ── An organisation's links ─────────────────────────────────────────────────────


def organisation_vendor_products():
    """Vendor products the signed-in organisation uses: mapped to its applications,
    of a vendor named on its systems, or on its contracts. Tenant scoping comes from the ORM filter on the
    organisation-owned rows (applications, contracts)."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.capability_to_vendor_mapping import ApplicationVendorProductMapping

    via_apps = (
        db.session.query(ApplicationVendorProductMapping.vendor_product_id)
        .join(
            ApplicationComponent,
            ApplicationComponent.id == ApplicationVendorProductMapping.application_component_id,
        )
        .filter(ApplicationComponent.organization_id == current_org_id())
    )
    vendor_ids = (
        db.session.query(VendorContract.vendor_id)
        .filter(VendorContract.organization_id == current_org_id())
        .filter(VendorContract.vendor_id.isnot(None))
    )
    ids = {r[0] for r in via_apps.all()}
    contract_vendors = [r[0] for r in vendor_ids.all()]
    # Vendors named on the organisation's systems (one vendor record per vendor).
    if hasattr(ApplicationComponent, "vendor_organization_id"):
        contract_vendors += [
            r[0]
            for r in db.session.query(ApplicationComponent.vendor_organization_id)
            .filter(ApplicationComponent.organization_id == current_org_id())
            .filter(ApplicationComponent.vendor_organization_id.isnot(None))
            .distinct()
            .all()
        ]
    if contract_vendors:
        ids |= {
            r[0]
            for r in db.session.query(VendorProduct.id)
            .filter(VendorProduct.vendor_organization_id.in_(contract_vendors))
            .all()
        }
    if not ids:
        return []
    return VendorProduct.query.filter(VendorProduct.id.in_(ids)).order_by(VendorProduct.name).all()


def organisation_links_to(product: VendorProductDetail):
    """This organisation's links to one catalogue product."""
    return (
        VendorCatalogueLink.query.filter_by(product_detail_id=product.id, organization_id=current_org_id())
        .order_by(VendorCatalogueLink.linked_at.desc())
        .all()
    )


def link_for_vendor_product(vendor_product_id: int) -> Optional[VendorCatalogueLink]:
    """The signed-in organisation's link for one of its vendor products, if any.

    This is the read the per-organisation vendor record uses to show catalogue
    facts beside the organisation's own data."""
    return VendorCatalogueLink.query.filter_by(
        vendor_product_id=vendor_product_id, organization_id=current_org_id()
    ).first()


NOT_YOURS = "That vendor product is not one your organisation uses."


def applications_using(vendor_product_id: int) -> list[str]:
    """Names of the signed-in organisation's applications that use a vendor product, by name."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.capability_to_vendor_mapping import ApplicationVendorProductMapping

    mapped = db.session.query(ApplicationVendorProductMapping.application_component_id).filter(
        ApplicationVendorProductMapping.vendor_product_id == vendor_product_id
    )
    rows = (
        ApplicationComponent.query.filter(ApplicationComponent.organization_id == current_org_id())
        .filter(db.or_(ApplicationComponent.vendor_product_id == vendor_product_id,
                       ApplicationComponent.id.in_(mapped)))
        .order_by(ApplicationComponent.name)
        .all()
    )
    return [row.name for row in rows]


class LinkRefused(ValueError):
    pass


def link_vendor_product(vendor_product_id: int, product: VendorProductDetail, user_id: int) -> VendorCatalogueLink:
    """Link one of the organisation's vendor products to a catalogue product.

    Refused when the vendor product is not one the organisation uses, so an
    organisation cannot attach links to products it has nothing to do with."""
    allowed = {vp.id for vp in organisation_vendor_products()}
    if vendor_product_id not in allowed:
        raise LinkRefused(NOT_YOURS)
    org_id = current_org_id()
    # Read before a new link joins the session: a product expired by an earlier commit reloads here, and
    # the reload's autoflush must not meet a link without its product.
    product_id = product.id
    link = VendorCatalogueLink.query.filter_by(
        vendor_product_id=vendor_product_id, organization_id=org_id
    ).first()
    if link is None:
        link = VendorCatalogueLink(
            organization_id=org_id, vendor_product_id=vendor_product_id, product_detail_id=product_id
        )
        db.session.add(link)
    link.product_detail_id = product_id
    link.linked_by_id = user_id
    link.linked_at = datetime.utcnow()
    db.session.commit()
    return link


# ── Research pipeline ───────────────────────────────────────────────────────────


@dataclass
class FetchResult:
    allowed: bool
    status: Optional[int]
    text: str


def _robots_allows(url: str, cache: dict) -> bool:
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    if root not in cache:
        import requests

        rp = urllib.robotparser.RobotFileParser(root + "/robots.txt")
        try:
            # Read robots.txt under our own user agent: some sites refuse the
            # standard library's default agent, which it would take as "disallow all".
            resp = requests.get(root + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT)
            if resp.status_code in (401, 403):
                rp.disallow_all = True
            elif resp.status_code >= 400:
                rp = None  # no robots.txt: allowed, as the standard says
            else:
                rp.parse(resp.text.splitlines())
        except Exception:
            rp = None
        cache[root] = rp
    rp = cache[root]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def http_fetch(url: str, robots_cache: Optional[dict] = None) -> FetchResult:
    """Fetch a public page, respecting robots.txt. The text is used only to
    confirm the evidence words and is never stored."""
    import requests

    robots_cache = robots_cache if robots_cache is not None else {}
    if not _robots_allows(url, robots_cache):
        return FetchResult(False, None, "")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT)
    except Exception as exc:
        logger.info("catalogue research: %s unreadable (%s)", url, exc)
        return FetchResult(True, None, "")
    return FetchResult(True, resp.status_code, resp.text if resp.ok else "")


def _confirms(text: str, evidence: str) -> bool:
    """Each ';'-separated group must be present; within a group, '|' means any of."""
    if not evidence:
        return False
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).lower()
    for group in evidence.split(";"):
        options = [o.strip().lower() for o in group.split("|") if o.strip()]
        if options and not any(o in flat for o in options):
            return False
    return True


def load_plan(path: Path = PLAN_PATH) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_product(entry: dict) -> VendorProductDetail:
    """Find or create the shared vendor, family and product records for a plan entry.
    Reuses an existing family of the same name (the one the end-of-life sync writes)."""
    vendor = VendorOrganization.query.filter(
        db.func.lower(VendorOrganization.name) == entry["vendor"].lower()
    ).first()
    if vendor is None:
        vendor = VendorOrganization(
            name=entry["vendor"], display_name=entry["vendor"], website=entry.get("website"),
            vendor_type=entry.get("vendor_type", "software_vendor"),
        )
        db.session.add(vendor)
        db.session.flush()
    family_name = entry.get("family", entry["name"])
    family = VendorProductFamily.query.filter_by(vendor_id=vendor.id, family_name=family_name).first()
    if family is None:
        family = VendorProductFamily(
            vendor_id=vendor.id, family_name=family_name, category=entry.get("category"),
            family_code=("CAT-" + entry["slug"].upper())[:50],
        )
        db.session.add(family)
        db.session.flush()
    product = VendorProductDetail.query.filter_by(catalogue_slug=entry["slug"]).first()
    if product is None:
        product = VendorProductDetail(
            family_id=family.id, product_name=entry["name"], catalogue_slug=entry["slug"],
            deployment_model=entry.get("deployment_model", "SaaS"),
        )
        db.session.add(product)
        db.session.flush()
    return product


def _upsert_fact(product: VendorProductDetail, spec: dict) -> VendorCatalogueFact:
    fact = VendorCatalogueFact.query.filter_by(
        product_detail_id=product.id, kind=spec["kind"], name=spec["name"]
    ).first()
    if fact is None:
        fact = VendorCatalogueFact(
            product_detail_id=product.id, kind=spec["kind"], name=spec["name"], review_state="draft"
        )
        db.session.add(fact)
    fact.archimate_type = spec.get("archimate_type")
    fact.detail = spec.get("detail")
    fact.attributes = spec.get("attributes") or {}
    fact.source_url = spec.get("source_url")
    fact.evidence = spec.get("evidence")
    return fact


def research_product(
    entry: dict,
    fetch: Callable[[str], FetchResult] = None,
    today: Optional[date] = None,
) -> dict:
    """Check every planned fact of one product against its source page and write
    drafts. Never publishes. Returns a per-fact report."""
    today = today or date.today()
    cache: dict = {}
    pages: dict = {}
    fetch = fetch or (lambda url: http_fetch(url, cache))
    product = ensure_product(entry)
    report = {"slug": entry["slug"], "confirmed": [], "unconfirmed": [], "withdrawn": []}
    planned = {(s["kind"], s["name"]) for s in entry["facts"]}
    for old in list(product.catalogue_facts):
        if (old.kind, old.name) not in planned:
            # No longer in the plan: withdrawn, so it can never be shown again.
            report["withdrawn"].append(old.name)
            db.session.delete(old)
    db.session.flush()
    for spec in entry["facts"]:
        url = spec.get("source_url")
        fact = _upsert_fact(product, spec)
        if fact.review_state == "published":
            # A source change sends a published fact back for a fresh review.
            fact.review_state = "needs_recheck"
        ok = False
        reason = "no source address"
        if url:
            if url not in pages:
                pages[url] = fetch(url)
            page = pages[url]
            if not page.allowed:
                reason = "robots.txt disallows"
            elif not page.text:
                reason = f"page unreadable (status {page.status})"
            elif _confirms(page.text, spec.get("evidence", "")):
                ok = True
            else:
                reason = "page does not confirm"
        if ok:
            fact.checked_on = today
            report["confirmed"].append(spec["name"])
        else:
            fact.checked_on = None
            if fact.review_state != "draft":
                fact.review_state = "needs_recheck"
            report["unconfirmed"].append((spec["name"], url, reason))
    db.session.commit()
    return report


def publish_product(slug: str, reviewer_name: str, reviewer_id: Optional[int] = None) -> dict:
    """A person's review step: publish every sourced fact of one product. Facts
    without a confirmed source stay drafts."""
    product = catalogue_product(slug)
    if product is None:
        raise LookupError(f"no catalogue product {slug!r}")
    published, held = 0, 0
    for fact in product.catalogue_facts:
        if fact.is_sourced:
            fact.publish(reviewer_name, reviewer_id)
            published += 1
        else:
            held += 1
    db.session.commit()
    return {"slug": slug, "published": published, "held": held}


# ── Reviewed snapshot (what is deployed) ────────────────────────────────────────


def export_reviewed(path: Path = REVIEWED_PATH) -> int:
    """Write every catalogue product and its facts, with their review state, to the
    reviewed snapshot that ships with the code."""
    out = []
    for product in VendorProductDetail.query.filter(
        VendorProductDetail.catalogue_slug.isnot(None)
    ).order_by(VendorProductDetail.catalogue_slug):
        family = product.product_family
        out.append(
            {
                "slug": product.catalogue_slug,
                "name": product.product_name,
                "vendor": family.vendor.name,
                "website": family.vendor.website,
                "family": family.family_name,
                "category": family.category,
                "facts": [
                    {
                        "kind": f.kind, "name": f.name, "archimate_type": f.archimate_type,
                        "detail": f.detail, "attributes": f.attributes or {},
                        "source_url": f.source_url, "evidence": f.evidence,
                        "checked_on": f.checked_on.isoformat() if f.checked_on else None,
                        "review_state": f.review_state,
                        "reviewed_by_name": f.reviewed_by_name,
                        "reviewed_at": f.reviewed_at.isoformat() if f.reviewed_at else None,
                    }
                    for f in sorted(product.catalogue_facts, key=lambda x: (x.kind, x.id))
                ],
            }
        )
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(out)


def load_reviewed(path: Path = REVIEWED_PATH) -> dict:
    """Load the reviewed snapshot into this database. Published facts keep their
    reviewer and date; the model refuses any published fact without a source."""
    data = json.loads(path.read_text(encoding="utf-8"))
    counts = {"products": 0, "published": 0, "draft": 0}
    for entry in data:
        product = ensure_product(entry)
        counts["products"] += 1
        for spec in entry["facts"]:
            fact = _upsert_fact(product, spec)
            fact.checked_on = date.fromisoformat(spec["checked_on"]) if spec.get("checked_on") else None
            if spec.get("review_state") == "published":
                fact.publish(
                    spec.get("reviewed_by_name") or "Entelim catalogue team",
                    None,
                    datetime.fromisoformat(spec["reviewed_at"]) if spec.get("reviewed_at") else None,
                )
                counts["published"] += 1
            else:
                fact.review_state = spec.get("review_state") or "draft"
                counts["draft"] += 1
    db.session.commit()
    return counts
