"""Global reference content — vendor and capability packs curated once for every tenant."""
# migration-exempt — new table created via db.create_all() (migration freeze)

import hashlib
import json

from app import db


class ReferencePack(db.Model):
    """Global reference content. Written only by pack_loader.py. Never read with a tenant predicate."""

    __tablename__ = "reference_packs"
    __table_args__ = (
        db.UniqueConstraint("pack_key", "pack_version", name="uq_reference_packs_key_version"),
        db.CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_reference_packs_status"
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    pack_key = db.Column(db.String(24), nullable=False)
    pack_version = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="draft")
    vendor = db.Column(db.String(120), nullable=False)
    product = db.Column(db.String(160), nullable=False)
    product_edition = db.Column(db.String(160), nullable=True)
    segment_tags = db.Column(db.JSON, nullable=False, default=list)
    industry_tags = db.Column(db.JSON, nullable=False, default=list)
    content = db.Column(db.JSON, nullable=False)
    sources = db.Column(db.JSON, nullable=False)
    attribution_text = db.Column(db.Text, nullable=False)
    trademark_line = db.Column(db.Text, nullable=False)
    content_hash = db.Column(db.String(64), nullable=False)
    curated_by = db.Column(db.String(120), nullable=True)
    curated_at = db.Column(db.DateTime, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True)
    # Plain integer, no FK — the request table is owned by a later task, which
    # documents the link; this task only reserves the column.
    request_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def __repr__(self):
        return f"<ReferencePack {self.pack_key}@{self.pack_version} {self.status}>"

    def to_dict(self):
        return {
            "id": self.id,
            "pack_key": self.pack_key,
            "pack_version": self.pack_version,
            "status": self.status,
            "vendor": self.vendor,
            "product": self.product,
            "product_edition": self.product_edition,
            "segment_tags": self.segment_tags,
            "industry_tags": self.industry_tags,
            "content_hash": self.content_hash,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


def compute_content_hash(content: dict) -> str:
    """SHA-256 of the canonical JSON encoding of ``content`` (decision A)."""
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
