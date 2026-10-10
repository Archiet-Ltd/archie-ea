"""Canonical content hash for a programme type template (security.md 7.3).

One function, shared by the loader, the validator and `flask programme-types
status`, so "does this file's review still match its content" is answered
the same way everywhere. SHA-256 over the canonical JSON of the
``yaml.safe_load`` result with the ``review`` key removed -- not raw bytes,
so a whitespace or comment edit does not invalidate a review, while every
semantic change does.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_content_hash(template: Mapping[str, Any]) -> str:
    """SHA-256 hex digest of *template* with any ``review`` key removed."""
    without_review = {key: value for key, value in template.items() if key != "review"}
    canonical = json.dumps(
        without_review, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["canonical_content_hash"]
