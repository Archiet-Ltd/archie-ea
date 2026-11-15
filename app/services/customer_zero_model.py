"""The organisation's own products and what each can do, kept in its model.

A product file (``data/customer_zero/<product>.json``) names one product and lists what it can
and cannot do. ``load_product`` writes the product as an application component and each listed
capability as an application function it is composed of, through the one element writer. Shipped
status, what the product does, its known limits and the evidence live in the element's own
properties, so the chat and the element's page read the same record. A second run changes
nothing that has not changed; a capability dropped from the file is marked withdrawn, never
deleted. Everything is scoped to the one organisation passed in.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.services.archimate_backbone import create_backbone_element

KEY_PREFIX = "customer-zero:"
PRODUCT_TYPE, PRODUCT_LAYER = "ApplicationComponent", "Application"
CAPABILITY_TYPE, CAPABILITY_LAYER = "ApplicationFunction", "Application"

SHIPPED, NARROW, NOT_SHIPPED, WITHDRAWN = "shipped", "narrow", "not shipped", "withdrawn"
STATUS_WORDS = {SHIPPED: "Shipped", NARROW: "Shipped, narrow", NOT_SHIPPED: "Not shipped", WITHDRAWN: "Withdrawn"}
DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "customer_zero"


class ProductFileError(ValueError):
    """A product file that cannot be loaded; nothing has been written."""


def normalise_status(raw: Any) -> str:
    """The recorded status as one of shipped, narrow or not shipped. A claim that is
    "not proven" is not shipped; the words it was recorded in are kept beside it."""
    text = str(raw or "").strip().lower()
    if text.startswith("shipped"):
        return SHIPPED
    if text.startswith("narrow"):
        return NARROW
    if text.startswith("not shipped") or text.startswith("not proven"):
        return NOT_SHIPPED
    raise ProductFileError("Unknown status %r: use shipped, narrow or not shipped." % (raw,))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def read_product_file(path) -> Dict[str, Any]:
    """The product file's content, checked. Raises ProductFileError for anything unusable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProductFileError("Cannot read %s: %s" % (Path(path).name, exc))
    product = data.get("product") if isinstance(data, dict) else None
    rows = data.get("capabilities") if isinstance(data, dict) else None
    if not isinstance(product, str) or not product.strip() or not isinstance(rows, list):
        raise ProductFileError("%s needs a product name and a list of capabilities." % Path(path).name)
    seen = set()
    capabilities: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not str(row.get("name") or "").strip():
            raise ProductFileError("Every capability in %s needs an id and a name." % Path(path).name)
        if row["id"] in seen:
            raise ProductFileError("The id %r appears twice in %s." % (row["id"], Path(path).name))
        seen.add(row["id"])
        raw_status = row.get("status")
        capabilities.append({
            "id": str(row["id"]),
            "name": str(row["name"]).strip(),
            "status": normalise_status(raw_status),
            "status_detail": str(raw_status).strip(),
            "does": str(row.get("what_a_person_can_do") or row.get("what_exists") or "").strip(),
            "limits": [str(item).strip() for item in (row.get("limits") or []) if str(item).strip()],
            "evidence": str(row.get("evidence") or "").strip() or None,
            "commit": str(row.get("commit") or "").strip() or None,
        })
    return {"product": product.strip(), "as_of": data.get("as_of"), "capabilities": capabilities}


def describe(capability: Dict[str, Any]) -> str:
    """The text the element's page shows: status, what it does, what it does not."""
    status = STATUS_WORDS[capability["status"]]
    detail = capability["status_detail"].lower()
    if capability["status"] == NOT_SHIPPED and detail != NOT_SHIPPED:
        status = detail.capitalize() if detail.startswith("not shipped") else "Not shipped (not proven)"
    parts = ["%s." % status]
    if capability["does"]:
        parts.append(capability["does"])
    if capability["limits"]:
        parts.append("Not covered: %s." % "; ".join(capability["limits"]))
    return " ".join(parts)


def _properties(product: str, capability: Dict[str, Any], as_of: Optional[str]) -> Dict[str, Any]:
    return {
        "source_model": "customer_zero",
        "customer_zero_product": product,
        "customer_zero_key": capability["id"],
        "shipped_status": capability["status"],
        "status_detail": capability["status_detail"],
        "what_it_does": capability["does"],
        "limits": capability["limits"],
        "evidence": capability["evidence"],
        "commit": capability["commit"],
        "recorded_as_of": as_of,
    }


def _origin(key: str, product: str) -> Dict[str, Any]:
    return {"source_kind": "import", "source_ref": key, "source_label": "%s product record" % product,
            "source_recorded_at": datetime.now(timezone.utc).replace(tzinfo=None)}


def _existing(organization_id: int, key: str) -> Optional[ArchiMateElement]:
    return db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.organization_id == organization_id, ArchiMateElement.source_ref == key
        )
    ).scalars().first()


def _compose(organization_id: int, product: ArchiMateElement, element: ArchiMateElement, key: str, product_name: str) -> None:
    from app.modules.architecture.services.archimate_relationship_service import ArchiMateRelationshipService

    found = db.session.execute(
        db.select(ArchiMateRelationship.id).where(
            ArchiMateRelationship.source_id == product.id, ArchiMateRelationship.target_id == element.id,
            ArchiMateRelationship.type == "composition",
        )
    ).first()
    if found:
        return
    link = ArchiMateRelationshipService.create_relationship(
        product, element, "composition", None, organization_id=organization_id, rollback=False)
    if link is not None:
        # Written by the product file, not by a person.
        link.source_kind, link.source_ref, link.source_label = "import", key, "%s product record" % product_name


def load_product(path, organization_id: int, *, dry_run: bool = False) -> Dict[str, Any]:
    """Write the product file into the organisation's model and return the counts.

    Only elements of *organization_id* are read or written. Returns created, updated,
    unchanged and withdrawn counts and the product element's id (None on a dry run that
    would create it)."""
    from app.models import Organization

    if not isinstance(organization_id, int) or db.session.get(Organization, organization_id) is None:
        raise ProductFileError("Organisation %r does not exist." % (organization_id,))
    data = read_product_file(path)
    name, as_of = data["product"], data["as_of"]
    counts = {"product": name, "created": 0, "updated": 0, "unchanged": 0, "withdrawn": 0}
    product_key = "%s%s" % (KEY_PREFIX, _slug(name))
    product = _existing(organization_id, product_key)
    product_text = "%s: what it can and cannot do, as recorded in this model." % name
    if product is None:
        counts["created"] += 1
        if not dry_run:
            product = create_backbone_element(
                element_type=PRODUCT_TYPE, layer=PRODUCT_LAYER, name=name, description=product_text,
                organization_id=organization_id, keep_whole=True, origin=_origin(product_key, name),
                provenance={"source_model": "customer_zero", "customer_zero_product": name})
    else:
        counts["unchanged"] += 1
    listed = set()
    for capability in data["capabilities"]:
        key = "%s%s:%s" % (KEY_PREFIX, _slug(name), capability["id"])
        listed.add(key)
        element_name, text = "%s: %s" % (name, capability["name"]), describe(capability)
        props = _properties(name, capability, as_of)
        element = _existing(organization_id, key)
        if element is None:
            counts["created"] += 1
            if dry_run:
                continue
            element = create_backbone_element(
                element_type=CAPABILITY_TYPE, layer=CAPABILITY_LAYER, name=element_name, description=text,
                organization_id=organization_id, keep_whole=True, origin=_origin(key, name), provenance=props)
        elif (element.name, element.description, element.custom_properties) == (element_name, text, props):
            counts["unchanged"] += 1
        else:
            counts["updated"] += 1
            if not dry_run:
                element.name, element.description, element.custom_properties = element_name, text, props
        if not dry_run and product is not None:
            _compose(organization_id, product, element, key, name)
    prefix = "%s%s:" % (KEY_PREFIX, _slug(name))
    rows = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.organization_id == organization_id, ArchiMateElement.source_ref.like(prefix + "%")
        )
    ).scalars().all()
    for element in rows:
        props = dict(element.custom_properties or {})
        if element.source_ref in listed or props.get("shipped_status") == WITHDRAWN:
            continue
        counts["withdrawn"] += 1
        if not dry_run:
            props.update(shipped_status=WITHDRAWN)
            element.custom_properties = props
            element.description = "Withdrawn. This is no longer in %s's product record. %s" % (name, element.description or "")
    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()
    counts["product_element_id"] = product.id if product is not None else None
    return counts


def product_files(paths=None) -> List[Path]:
    """The files to load: those named, or every product file in the data folder."""
    return [Path(p) for p in paths] if paths else sorted(DATA_DIRECTORY.glob("*.json"))
