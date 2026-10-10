"""The one writer of ``reference_packs`` and the refresher of the
``VendorArchiMateTemplate`` projection (decision D).

``load_packs`` is the only place in the codebase that constructs
``ReferencePack(`` and the only place that assigns ``status = "published"``
(enforced by ``test_one_writer.py``). The curator's ``publish``/``draft``
workflow is a later task's addition to this module, not a reason to add a
second writer here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.models.reference_pack import ReferencePack, compute_content_hash
from app.models.vendor.vendor_organization import VendorArchiMateTemplate
from app.modules.reference_packs.services.pack_files import (
    DEFAULT_ROOT,
    compute_pack_content,
    discover_pack_files,
    load_pack_file,
    validate_pack,
)

# R-3: the four packs that project onto vendor keys already read by
# VendorTemplateService.VENDOR_KEY_MAP — every other pack key becomes its own
# upper-snake vendor key.
LEGACY_VENDOR_KEYS = {
    "sap-s4hana": "SAP",
    "microsoft-dynamics-365": "MICROSOFT_DYNAMICS",
    "microsoft-power-platform": "MICROSOFT_POWER",
    "salesforce": "SALESFORCE",
}


@dataclass
class LoadReport:
    packs_seen: int = 0
    packs_loaded: int = 0
    packs_skipped_unchanged: int = 0
    sources_counted: int = 0
    projection_rows_written: int = 0
    projection_rows_removed: int = 0
    errors: list[str] = field(default_factory=list)


def _vendor_key_for(pack_key: str) -> str:
    return LEGACY_VENDOR_KEYS.get(pack_key, pack_key.upper().replace("-", "_"))


def refresh_projection(pack: dict, *, dry_run: bool = False) -> tuple[int, int]:
    """Upsert ``VendorArchiMateTemplate`` rows for one published pack's elements
    (decision D). Returns (rows_written, rows_removed)."""
    vendor_key = _vendor_key_for(pack["pack_key"])
    elements = pack.get("elements") or []

    written = 0
    removed = 0

    pack_element_names: set[str] = set()
    existing_by_name = {
        row.element_name: row
        for row in VendorArchiMateTemplate.query.filter_by(vendor_key=vendor_key).all()
    }

    for position, el in enumerate(elements):
        name = el["name"]
        pack_element_names.add(name)
        layer_title = el["layer"].title()
        row = existing_by_name.get(name)
        if row is None:
            if not dry_run:
                row = VendorArchiMateTemplate(
                    vendor_key=vendor_key,
                    element_id=None,
                    element_name=name,
                    element_type=el["type"],
                    archimate_layer=layer_title,
                    mandatory=bool(el.get("mandatory", True)),
                    version=pack["pack_version"],
                    display_order=position,
                    spec_data_seed=None,
                )
                db.session.add(row)
                # A pack can carry more than one element with the same display
                # name at different layers (for example a Node, a SystemSoftware
                # and an ApplicationComponent all called "SAP Gateway"), and the
                # projection is keyed on (vendor_key, element_name) alone. Without
                # this, a second same-named element later in the same elements[]
                # list would not find the row just created above and would insert
                # a duplicate instead of updating it, leaving which row survives a
                # later reload to query-order chance.
                existing_by_name[name] = row
            written += 1
        else:
            if not dry_run:
                row.element_type = el["type"]
                row.archimate_layer = layer_title
                row.mandatory = bool(el.get("mandatory", True))
                row.version = pack["pack_version"]
                row.display_order = position
                # element_id and spec_data_seed are left untouched.
            written += 1

    for name, row in existing_by_name.items():
        if name in pack_element_names:
            continue
        if row.element_id is None:
            if not dry_run:
                db.session.delete(row)
            removed += 1
        # else: kept, counted as "removed from the pack but retained because
        # a real ArchiMate element is now linked" — the brief only asks that
        # kept rows be counted, so they are folded into the same tally the
        # CLI prints as removed candidates that survived because of the FK.

    return written, removed


def load_packs(root: Path | None = None, *, dry_run: bool = False) -> LoadReport:
    """Validate and load every pack file under ``root`` (decision D)."""
    root = root or DEFAULT_ROOT
    report = LoadReport()

    for path in discover_pack_files(root):
        report.packs_seen += 1
        pack_key_from_dir = path.parent.name
        try:
            pack = load_pack_file(path)
        except Exception as exc:  # noqa: BLE001 - collected, not raised
            report.errors.append(f"{pack_key_from_dir}: {exc}")
            continue

        errors = validate_pack(pack)
        if errors:
            # validate_pack already prefixes each message with the pack_key.
            report.errors.extend(errors)
            continue

        sources = (pack.get("_sources_doc") or {}).get("sources") or []
        report.sources_counted += len(sources)

        content = compute_pack_content(pack)
        content_hash = compute_content_hash(content)

        existing = ReferencePack.query.filter_by(
            pack_key=pack["pack_key"], pack_version=str(pack["pack_version"])
        ).first()

        if existing is None:
            record_is_published = True
            if not dry_run:
                now = datetime.now(timezone.utc)
                record = ReferencePack(
                    pack_key=pack["pack_key"],
                    pack_version=str(pack["pack_version"]),
                    status="published",
                    vendor=pack["vendor"],
                    product=pack["product"],
                    product_edition=pack.get("product_edition"),
                    segment_tags=pack.get("segment_tags") or [],
                    industry_tags=pack.get("industry_tags") or [],
                    content=content,
                    sources=sources,
                    attribution_text=pack["attribution_text"],
                    trademark_line=pack["trademark_line"],
                    content_hash=content_hash,
                    published_at=now,
                )
                db.session.add(record)
                db.session.flush()
            report.packs_loaded += 1
        elif existing.content_hash == content_hash:
            record_is_published = existing.status == "published"
            report.packs_skipped_unchanged += 1
        else:
            record_is_published = existing.status == "published"
            if not dry_run:
                existing.content = content
                existing.sources = sources
                existing.vendor = pack["vendor"]
                existing.product = pack["product"]
                existing.product_edition = pack.get("product_edition")
                existing.segment_tags = pack.get("segment_tags") or []
                existing.industry_tags = pack.get("industry_tags") or []
                existing.attribution_text = pack["attribution_text"]
                existing.trademark_line = pack["trademark_line"]
                existing.content_hash = content_hash
                db.session.flush()
            report.packs_loaded += 1

        if record_is_published:
            written, removed = refresh_projection(pack, dry_run=dry_run)
            report.projection_rows_written += written
            report.projection_rows_removed += removed

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()

    return report
