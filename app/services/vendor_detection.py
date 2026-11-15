"""Vendors an organisation uses, found from what it already has, proposed for a person to confirm.

Three sources, in the order the brief sets: the pages of the organisation's own website (read by the
website crawl), the public DNS records of its domain, and what a person tells the assistant ("we use
Okta, Slack and Datadog"). Only products in the public vendor catalogue are proposed, each with the
evidence that found it.

A detection is a proposal and nothing more. It goes into the one approvals inbox
(``ai_chat_crud_approvals``, through ``create_approval_record``) and creates no record of any kind.
Approving it (``AIChatApprovalService.approve_and_execute``, which calls ``execute_vendor_proposal``)
records the organisation's use of the product the same way a person would: an application supplied by
the vendor, its vendor product, and the catalogue link. The catalogue's published architecture for the
product is then proposed as a second, separate change. Rejecting it leaves nothing, and the same product
is not proposed again from any source.

Stores used, none added: ``ai_chat_crud_approvals`` (proposals), ``vendor_organizations`` and
``vendor_products`` (through ``vendor_register``), ``application_components`` (through the chat data
service), ``vendor_catalogue_links`` (through ``vendor_catalogue_service.link_vendor_product``),
ArchiMate elements (through ``archimate_backbone``) and ``notifications`` (through
``NotificationService``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from app import db

logger = logging.getLogger(__name__)

PROPOSAL_SOURCE = "vendor_detection"
OP_DETECTION = "vendor_detection"
OP_ARCHITECTURE = "vendor_architecture"
INBOX_PATH = "/ai-chat/approvals/inbox?source=" + PROPOSAL_SOURCE
PROPOSAL_DUE_DAYS = 30

SOURCE_LABELS = {
    "website": "your website",
    "dns": "your domain's DNS records",
    "chat": "what you told the assistant",
}

# Per catalogue product: what its presence leaves on a website (a script, frame or form address, or a
# path the vendor serves), in DNS (a mail exchanger, a name server, a verification or SPF record), and
# the names a person uses for it. Every needle is a vendor-owned address or record, never a plain word,
# so a page that only mentions a product does not count.
SIGNATURES: Dict[str, Dict[str, tuple]] = {
    "stripe": {"page": ("js.stripe.com", "checkout.stripe.com"), "dns": ("stripe-verification=",),
               "names": ("stripe",)},
    "intercom": {"page": ("widget.intercom.io", "js.intercomcdn.com"), "dns": ("intercom.io",),
                 "names": ("intercom",)},
    "hubspot": {"page": ("js.hs-scripts.com", "js.hsforms.net", "js.hs-analytics.net"),
                "dns": ("hubspotemail.net",), "names": ("hubspot",)},
    "zendesk": {"page": ("static.zdassets.com", ".zendesk.com/embeddable"), "dns": ("mail.zendesk.com",),
                "names": ("zendesk",)},
    "sentry": {"page": ("browser.sentry-cdn.com", "js.sentry-cdn.com", ".ingest.sentry.io"), "dns": (),
               "names": ("sentry",)},
    "datadog": {"page": ("datadoghq-browser-agent.com", "browser-intake-datadoghq.com"), "dns": (),
                "names": ("datadog",)},
    "new-relic": {"page": ("js-agent.newrelic.com", "bam.nr-data.net"), "dns": (),
                  "names": ("new relic", "newrelic")},
    "cloudflare": {"page": ("/cdn-cgi/",), "dns": (".ns.cloudflare.com",), "names": ("cloudflare",)},
    "vercel": {"page": ("/_vercel/insights", "vercel-insights.com"), "dns": ("vercel-dns.com",),
               "names": ("vercel",)},
    "salesforce": {"page": ("webto.salesforce.com",), "dns": ("_spf.salesforce.com",),
                   "names": ("salesforce", "sales cloud")},
    "google-workspace": {"page": (), "dns": ("aspmx.l.google.com", "_spf.google.com"),
                         "names": ("google workspace", "g suite", "gsuite", "gmail")},
    "microsoft-365": {"page": (), "dns": ("mail.protection.outlook.com", "spf.protection.outlook.com", "ms=ms"),
                      "names": ("microsoft 365", "office 365", "o365", "m365", "outlook")},
    "okta": {"page": (), "dns": ("okta-verification=",), "names": ("okta", "okta workforce identity")},
    "auth0": {"page": ("cdn.auth0.com",), "dns": (), "names": ("auth0",)},
    "slack": {"page": (), "dns": (), "names": ("slack",)},
    "jira": {"page": (), "dns": (), "names": ("jira", "jira software")},
    "confluence": {"page": (), "dns": (), "names": ("confluence",)},
    "github": {"page": (), "dns": (), "names": ("github",)},
    "gitlab": {"page": (), "dns": (), "names": ("gitlab",)},
    "aws": {"page": (), "dns": ("awsdns-",), "names": ("aws", "amazon web services")},
    "azure": {"page": (), "dns": ("azure-dns.",), "names": ("azure", "microsoft azure")},
    "google-cloud": {"page": (), "dns": ("googledomains.com",), "names": ("google cloud", "gcp")},
    "figma": {"page": ("figma.com/embed",), "dns": (), "names": ("figma",)},
    "notion": {"page": (), "dns": (), "names": ("notion",)},
    "pagerduty": {"page": (), "dns": (), "names": ("pagerduty",)},
    "snowflake": {"page": (), "dns": (), "names": ("snowflake",)},
    "twilio": {"page": ("media.twiliocdn.com", "sdk.twilio.com"), "dns": (), "names": ("twilio",)},
    "grafana-cloud": {"page": (), "dns": (), "names": ("grafana", "grafana cloud")},
}

# What a page loads: the address of a script, a frame or a form, and only a web address (http, https or
# protocol-relative). A link, a javascript: address or text that names a vendor's domain loads nothing.
_ADDRESS = re.compile(
    r"""<(?:script|iframe|form)\b[^>]*?\s(?:src|action|data-src)\s*=\s*["']\s*((?:https?:)?//[^"'\s]+)\s*["']""",
    re.I,
)
DNS_RECORD_TYPES = ("MX", "TXT", "NS")


@dataclass
class Detection:
    slug: str
    source: str
    evidence: List[Dict[str, str]] = field(default_factory=list)


# ----------------------------------------------------------------------------- detecting


def _served_from(address: str, needle: str) -> bool:
    """Whether the address is on the vendor's own host (and path, when the signature names one)."""
    parsed = urlparse("https:" + address if address.startswith("//") else address)
    host = (parsed.hostname or "").lower().rstrip(".")
    want_host, _, want_path = needle.lower().partition("/")
    if host != want_host and not host.endswith("." + want_host):
        return False
    return not want_path or parsed.path.lower().lstrip("/").startswith(want_path)


def detect_from_pages(pages: Iterable[Dict[str, Any]]) -> List[Detection]:
    """Catalogue products the pages load: the web address of a script, frame or form on the vendor's own host."""
    found: Dict[str, Detection] = {}
    for page in pages:
        html = page.get("html") or ""
        addresses = {a.strip() for a in _ADDRESS.findall(html)}
        for slug, sig in SIGNATURES.items():
            for needle in sig["page"]:
                hit = next((a for a in sorted(addresses) if _served_from(a, needle)), None)
                if hit is None:
                    continue
                det = found.setdefault(slug, Detection(slug, "website"))
                if len(det.evidence) < 3:
                    det.evidence.append({"kind": "page", "url": page.get("url") or "", "detail": hit[:300]})
                break
    return list(found.values())


def default_resolver(name: str, record_type: str) -> List[str]:
    """The domain's public records of one type, as text. Empty when there are none or the lookup fails."""
    import dns.exception
    import dns.resolver

    try:
        answer = dns.resolver.resolve(name, record_type, lifetime=3.0)
    except (dns.exception.DNSException, OSError):
        return []
    return [r.to_text().strip('"') for r in answer]


def domain_of(site: str) -> Optional[str]:
    host = (urlparse(site if "//" in site else "//" + site).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def detect_from_dns(domain: str, resolver: Optional[Callable[[str, str], List[str]]] = None) -> List[Detection]:
    """Catalogue products the domain's mail exchangers, name servers, SPF and verification records name."""
    resolver = resolver or default_resolver
    records = []
    for record_type in DNS_RECORD_TYPES:
        for value in resolver(domain, record_type) or []:
            records.append((record_type, str(value)))
    found: Dict[str, Detection] = {}
    for slug, sig in SIGNATURES.items():
        for record_type, value in records:
            if any(needle in value.lower() for needle in sig["dns"]):
                det = found.setdefault(slug, Detection(slug, "dns"))
                if len(det.evidence) < 3:
                    det.evidence.append({"kind": "dns", "url": "", "detail": f"{record_type} {value}"[:300]})
    return list(found.values())


def _names_index() -> Dict[str, str]:
    index = {}
    for slug, sig in SIGNATURES.items():
        index[slug.replace("-", " ")] = slug
        for name in sig["names"]:
            index[name] = slug
    return index


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def detect_from_names(names: Iterable[str], words: str = "") -> tuple:
    """The catalogue products a person named, and the names that are not in the catalogue."""
    index = _names_index()
    found: Dict[str, Detection] = {}
    unknown = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        slug = index.get(_key(name))
        if slug is None:
            unknown.append(name)
            continue
        det = found.setdefault(slug, Detection(slug, "chat"))
        det.evidence.append({"kind": "chat", "url": "", "detail": (words or name)[:300]})
    return list(found.values()), unknown


# ----------------------------------------------------------------------------- proposing


def _published_products(slugs: Iterable[str]) -> Dict[str, Any]:
    """The catalogue products with these slugs that have published facts, by slug."""
    from app.models.vendor.vendor_catalogue import VendorCatalogueFact
    from app.models.vendor.vendor_product import VendorProductDetail

    slugs = list(set(slugs))
    if not slugs:
        return {}
    rows = (
        VendorProductDetail.query.filter(VendorProductDetail.catalogue_slug.in_(slugs))
        .filter(VendorProductDetail.id.in_(
            db.session.query(VendorCatalogueFact.product_detail_id).filter(VendorCatalogueFact.review_state == "published")
        ))
        .all()
    )
    return {p.catalogue_slug: p for p in rows}


def _proposed_slugs(org_id: int) -> set:
    """Products already proposed to the organisation: waiting, approved or turned down."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    rows = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org_id,
        AIChatCRUDApproval.source_table == PROPOSAL_SOURCE,
        AIChatCRUDApproval.operation_type == OP_DETECTION,
        AIChatCRUDApproval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED]),
    ).all()
    return {json.loads(r.operation_payload or "{}").get("catalogue_slug") for r in rows}


def _linked_product_ids(org_id: int) -> set:
    """Catalogue products the organisation has already linked one of its vendor products to."""
    from app.models.vendor.vendor_catalogue import VendorCatalogueLink

    return {r[0] for r in db.session.query(VendorCatalogueLink.product_detail_id).filter(
        VendorCatalogueLink.organization_id == org_id).all()}


def _vendor_name(product) -> str:
    family = product.product_family
    return family.vendor.name if family is not None and family.vendor is not None else product.product_name


def _summary(product, det: Detection) -> str:
    first = det.evidence[0] if det.evidence else {}
    if det.source == "website":
        how = "the page %s loads %s" % (first.get("url"), first.get("detail"))
    elif det.source == "dns":
        how = "the record %s" % first.get("detail")
    else:
        how = "you said “%s”" % first.get("detail")
    return "%s by %s, found in %s: %s" % (product.product_name, _vendor_name(product),
                                          SOURCE_LABELS[det.source], how)


# The words an inbox card uses for a vendor proposal: what it is, where it was found and who asked.
KIND_LABELS = {OP_DETECTION: "Vendor found", OP_ARCHITECTURE: "Vendor architecture"}
ENTITY_LABELS = {"vendor": "Vendor", "archimate_fragment": "Model change"}
FOUND_BY = {
    "website": "Found by reading your website",
    "dns": "Found in your domain records",
    "chat": "From your chat",
}
ASKED_BY = {"website": "Reading your website", "dns": "Your domain records", "chat": "Your chat"}
_EVIDENCE_SOURCE = {"page": "website", "dns": "dns", "chat": "chat"}


def card_words(approval, payload: Dict[str, Any], people: Dict[int, str]) -> Dict[str, str]:
    """The inbox card's words for one vendor proposal; ``people`` names the organisation's users by id."""
    if approval.operation_type == OP_ARCHITECTURE:
        return {"operation": KIND_LABELS[OP_ARCHITECTURE], "entity": ENTITY_LABELS["archimate_fragment"],
                "source": "From the vendor catalogue", "requester": "The vendor catalogue"}
    sources = [payload.get("source")] + [_EVIDENCE_SOURCE.get(e.get("kind")) for e in payload.get("evidence") or []
                                         if isinstance(e, dict)]
    sources = [s for s in dict.fromkeys(sources) if s in FOUND_BY]
    first = sources[0] if sources else "website"
    found = FOUND_BY[first]
    if "website" in sources and "dns" in sources:
        found = "Found by reading your website and your domain records"
    requester = ASKED_BY[first]
    if first == "chat" and people.get(payload.get("requested_by")):
        requester = people[payload["requested_by"]]
    return {"operation": KIND_LABELS.get(approval.operation_type, KIND_LABELS[OP_DETECTION]),
            "entity": ENTITY_LABELS.get(approval.entity_type, "Vendor"), "source": found, "requester": requester}


def propose(org_id: int, detections: Iterable[Detection], *, requested_by: Optional[int] = None) -> Dict[str, list]:
    """Queue one pending approval per newly found catalogue product. Nothing else is written.

    A product already proposed (waiting, approved or turned down) or already linked by the organisation is
    reported as known and not proposed again. Detections of one product from two sources are one proposal,
    with the evidence of both."""
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    merged: Dict[str, Detection] = {}
    for det in detections:
        if det.slug in merged:
            merged[det.slug].evidence.extend(det.evidence)
        else:
            merged[det.slug] = Detection(det.slug, det.source, list(det.evidence))
    products = _published_products(merged)
    known_slugs = _proposed_slugs(org_id)
    linked = _linked_product_ids(org_id)
    result: Dict[str, list] = {"proposed": [], "known": [], "not_in_catalogue": []}
    for slug, det in merged.items():
        product = products.get(slug)
        if product is None:
            result["not_in_catalogue"].append(slug)
            continue
        if slug in known_slugs or product.id in linked:
            result["known"].append(product.product_name)
            continue
        approval = create_approval_record(
            organization_id=org_id,
            operation_type=OP_DETECTION,
            entity_type="vendor",
            summary=_summary(product, det)[:1000],
            operation_payload={
                "catalogue_slug": slug,
                "catalogue_product_id": product.id,
                "product_name": product.product_name,
                "vendor_name": _vendor_name(product),
                "source": det.source,
                "evidence": det.evidence[:6],
                "detected_on": date.today().isoformat(),
                "requested_by": requested_by,
            },
            source_table=PROPOSAL_SOURCE,
            expiry_minutes=PROPOSAL_DUE_DAYS * 24 * 60,
        )
        result["proposed"].append({"id": approval.id, "product": product.product_name, "source": det.source})
    return result


def notify_reviewers(org_id: int, proposed: List[Dict[str, Any]]) -> int:
    """Tell the people who can decide proposals that vendors were found. The bell links to the inbox."""
    from app.models.user import Permission, User
    from app.services.notification_service import NotificationService

    if not proposed:
        return 0
    names = [p["product"] for p in proposed]
    shown = ", ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else "")
    message = ("%d vendor%s your organisation appears to use: %s. Confirm the ones you use."
               % (len(names), "" if len(names) == 1 else "s", shown))
    sent = 0
    for user in User.query.filter_by(organization_id=org_id).all():
        if not getattr(user, "is_active", True) or not user.can(Permission.GENERAL):
            continue
        NotificationService.create(user.id, message, url=INBOX_PATH, organization_id=org_id,
                                   event_type="vendor_detection", entity_type="vendor")
        sent += 1
    return sent


def detect_for_site(org_id: int, site: str, pages: List[Dict[str, Any]],
                    resolver: Optional[Callable[[str, str], List[str]]] = None) -> Dict[str, list]:
    """The website crawl's step: the pages it read and the site's domain records, proposed and announced."""
    detections = detect_from_pages(pages)
    domain = domain_of(site)
    if domain:
        detections += detect_from_dns(domain, resolver)
    result = propose(org_id, detections)
    notify_reviewers(org_id, result["proposed"])
    db.session.commit()
    return result


def propose_from_names(org_id: int, names: Iterable[str], words: str = "",
                       requested_by: Optional[int] = None) -> Dict[str, Any]:
    """The assistant's step: the products a person named, proposed for them (or a colleague) to confirm."""
    detections, unknown = detect_from_names(names, words)
    result = propose(org_id, detections, requested_by=requested_by)
    result["not_in_catalogue"] = sorted(set(result["not_in_catalogue"]) | set(unknown))
    db.session.commit()
    return result


# ----------------------------------------------------------------------------- approving


def execute_vendor_proposal(approval, data_service=None) -> Dict[str, Any]:
    """Apply one approved proposal of this module (called by the approval service)."""
    try:
        payload = json.loads(approval.operation_payload or "{}")
    except (TypeError, ValueError):
        return {"success": False, "error": "Invalid vendor proposal payload"}
    if approval.operation_type == OP_DETECTION:
        return _record_use(approval, payload, data_service)
    if approval.operation_type == OP_ARCHITECTURE:
        return _add_architecture(approval, payload)
    return {"success": False, "error": f"Unsupported vendor proposal: {approval.operation_type}"}


def _vendor_product(vendor, product):
    """The shared vendor product record for a catalogue product, found by name under its vendor or created."""
    from sqlalchemy import func

    from app.models.vendor.vendor_organization import VendorProduct

    vp = VendorProduct.query.filter(
        VendorProduct.vendor_organization_id == vendor.id,
        func.lower(VendorProduct.name) == product.product_name.lower(),
    ).first()
    if vp is None:
        vp = VendorProduct(vendor_organization_id=vendor.id, name=product.product_name[:200],
                           deployment_model="cloud", product_type="service")
        db.session.add(vp)
        db.session.flush()
    return vp


def _record_use(approval, payload, data_service) -> Dict[str, Any]:
    from app.models.application_portfolio import ApplicationComponent
    from app.models.vendor.vendor_product import VendorProductDetail
    from app.services import vendor_register
    from app.services.vendor_catalogue_service import link_vendor_product, published_facts
    from app.utils.duplicate_guard import find_duplicate_by_name

    org_id = approval.organization_id
    product = db.session.get(VendorProductDetail, payload.get("catalogue_product_id"))
    if product is None or product.catalogue_slug != payload.get("catalogue_slug"):
        return {"success": False, "error": "That product is no longer in the catalogue"}
    vendor = vendor_register.resolve_vendor(payload.get("vendor_name") or _vendor_name(product), org_id)
    vp = _vendor_product(vendor, product)

    application = ApplicationComponent.query.filter_by(organization_id=org_id, vendor_product_id=vp.id).first()
    if application is None:
        application = find_duplicate_by_name(ApplicationComponent, product.product_name,
                                             extra_filters=[ApplicationComponent.organization_id == org_id])
    if application is None:
        summary = next(iter(published_facts(product).get("summary") or []), None)
        if data_service is None:
            from app.modules.ai_chat.services.ai_data_interaction_service import AIDataInteractionService

            data_service = AIDataInteractionService(user_id=approval.approved_by_id)
        created = data_service.create_application({
            "name": product.product_name,
            "description": summary.detail if summary is not None else "",
            "lifecycle_status": "operational",
            "deployment_model": "cloud",
            "vendor_name": vendor.name,
        })
        if not created.get("success"):
            return {"success": False, "error": created.get("error") or "The application could not be recorded"}
        application = db.session.get(ApplicationComponent, created["application_id"])
    if application.vendor_product_id is None:
        application.vendor_product_id = vp.id
    if application.vendor_organization_id is None:
        vendor_register.set_application_vendor(application, vendor)
    db.session.flush()
    link = link_vendor_product(vp.id, product, approval.approved_by_id)
    fragment = _propose_architecture(org_id, application, product)
    db.session.commit()
    return {
        "success": True,
        "application_id": application.id,
        "vendor_id": vendor.id,
        "vendor_product_id": vp.id,
        "catalogue_link_id": link.id,
        "architecture_approval_id": fragment.id if fragment is not None else None,
        "message": "%s is recorded as used, supplied by %s." % (product.product_name, vendor.name),
    }


ARCHITECTURE_KINDS = {"component": "Composition", "interface": "Composition", "service": "Realization"}


def _propose_architecture(org_id: int, application, product):
    """The catalogue's published parts of the product, proposed for the organisation's model as one change."""
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record
    from app.services.vendor_catalogue_service import published_facts

    grouped = published_facts(product)
    facts = [f for kind in ARCHITECTURE_KINDS for f in grouped.get(kind) or [] if f.archimate_type]
    if not facts:
        return None
    counts = {}
    for f in facts:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    parts = ", ".join("%d %s%s" % (n, k, "" if n == 1 else "s") for k, n in counts.items())
    return create_approval_record(
        organization_id=org_id,
        operation_type=OP_ARCHITECTURE,
        entity_type="archimate_fragment",
        entity_id=application.id,
        summary=("Add the published architecture of %s to your model, attached to %s: %s, each with its source "
                 "in the vendor catalogue." % (product.product_name, application.name, parts))[:1000],
        operation_payload={
            "application_id": application.id,
            "catalogue_slug": product.catalogue_slug,
            "catalogue_product_id": product.id,
            "facts": [{"id": f.id, "kind": f.kind, "name": f.name, "archimate_type": f.archimate_type,
                       "detail": f.detail or "", "source_url": f.source_url,
                       "checked_on": f.checked_on.isoformat() if f.checked_on else None} for f in facts],
        },
        source_table=PROPOSAL_SOURCE,
        expiry_minutes=PROPOSAL_DUE_DAYS * 24 * 60,
    )


def _element_name(product_name: str, fact_name: str) -> str:
    return fact_name if product_name.lower() in fact_name.lower() else "%s %s" % (product_name, fact_name)


def _add_architecture(approval, payload) -> Dict[str, Any]:
    from app.models import ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.vendor.vendor_catalogue import VendorCatalogueFact
    from app.services.archimate_backbone import create_backbone_element, ensure_archimate_element

    org_id = approval.organization_id
    application = ApplicationComponent.query.filter_by(id=payload.get("application_id"), organization_id=org_id).first()
    if application is None:
        return {"success": False, "error": "The application is no longer in this organisation"}
    anchor = ensure_archimate_element(application)
    if anchor is None:
        return {"success": False, "error": "The application has no element in the model to attach to"}
    product_name = application.name
    created, reused, links = 0, 0, 0
    for item in payload.get("facts") or []:
        fact = db.session.get(VendorCatalogueFact, item.get("id"))
        if fact is None or fact.review_state != "published" or fact.kind not in ARCHITECTURE_KINDS:
            continue
        name = _element_name(product_name, fact.name)
        element = ArchiMateElement.query.filter_by(organization_id=org_id, type=fact.archimate_type, name=name).first()
        if element is None:
            element = create_backbone_element(
                element_type=fact.archimate_type, layer="Application", name=name, description=fact.detail or None,
                organization_id=org_id,
                provenance={"vendor_catalogue": {"slug": payload.get("catalogue_slug"), "fact_id": fact.id,
                                                 "source_url": fact.source_url,
                                                 "checked_on": fact.checked_on.isoformat() if fact.checked_on else None}},
            )
            created += 1
        else:
            reused += 1
        kind = ARCHITECTURE_KINDS[fact.kind]
        exists = ArchiMateRelationship.query.filter_by(
            organization_id=org_id, source_id=anchor.id, target_id=element.id, type=kind).first()
        if exists is None:
            db.session.add(ArchiMateRelationship(
                type=kind, source_id=anchor.id, target_id=element.id, organization_id=org_id,
                description=("From the vendor catalogue: %s" % fact.source_url)[:2000],
                custom_properties={"vendor_catalogue": {"slug": payload.get("catalogue_slug"), "fact_id": fact.id}},
            ))
            links += 1
    db.session.flush()
    return {"success": True, "application_id": application.id, "elements_created": created,
            "elements_reused": reused, "relationships_created": links}
