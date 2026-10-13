"""``ProgrammeTypeCatalogue``: the read-only loader for programme type
template files (ADR 0012, sdd.md 4.3).

Reads only files named in ``_manifest.yaml`` inside
``app/modules/transformation_room/programme_types/data/``. Rejects symlinks,
path separators in a manifest key, and files over 256 KB. Never raises into
a request: every failure -- a missing file, a parse error, a shape error, a
stale or missing review -- becomes a ``TemplateResult`` with ``valid`` or
``reviewed`` False, plus exactly one ``logger.warning`` naming the key and
the reason (no file content in the log line, security.md 7.7).

Process-level cache keyed by every manifest-listed file's mtime, so an
on-host edit (production runs from a bind-mounted checkout) takes effect on
the next call without a restart.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

import yaml

from app.modules.transformation_room.programme_types.hashing import canonical_content_hash
from app.modules.transformation_room.programme_types.validator import (
    validate_manifest,
    validate_review,
    validate_template,
)

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MANIFEST_FILENAME = "_manifest.yaml"
MAX_FILE_BYTES = 256 * 1024


@dataclass(frozen=True)
class TemplateResult:
    key: str
    valid: bool
    errors: tuple[str, ...]
    reviewed: bool
    content_sha256: str | None
    template: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ProgrammeType:
    """A valid, reviewed template, offered on the journey start form."""

    key: str
    name: str
    summary: str
    vendor_specific: bool
    arb_required_at_decide: bool
    workstreams: tuple[Mapping[str, Any], ...]
    stages: Mapping[str, Any]
    content_sha256: str
    data: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_template(cls, template: Mapping[str, Any], content_sha256: str) -> "ProgrammeType":
        return cls(
            key=template["key"],
            name=template.get("name", template["key"]),
            summary=template.get("summary", ""),
            vendor_specific=bool(template.get("vendor_specific")),
            arb_required_at_decide=bool(template.get("arb_required_at_decide")),
            workstreams=tuple(template.get("workstreams") or ()),
            stages=template.get("stages") or {},
            content_sha256=content_sha256,
            data=template,
        )


def _safe_load_file(path: str) -> Any:
    if os.path.islink(path):
        raise ValueError("symlink rejected")
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise ValueError("file exceeds the 256 KB cap")
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read_manifest(data_dir: str) -> list[str]:
    manifest_path = os.path.join(data_dir, MANIFEST_FILENAME)
    raw = _safe_load_file(manifest_path)
    if not isinstance(raw, list):
        raise ValueError("_manifest.yaml must be a YAML list of keys")
    return raw


class ProgrammeTypeCatalogue:
    """Process-level cache keyed by the manifest-listed files' mtimes."""

    def __init__(self, data_dir: str = DATA_DIR):
        self._data_dir = data_dir
        self._cache_signature: tuple | None = None
        self._cache_results: dict[str, TemplateResult] = {}

    def _current_signature(self) -> tuple:
        try:
            manifest_keys = _read_manifest(self._data_dir)
        except Exception:
            return ("__manifest_error__",)
        stamps = [os.path.getmtime(os.path.join(self._data_dir, MANIFEST_FILENAME))]
        for key in manifest_keys:
            path = self._key_path(key)
            try:
                stamps.append(os.path.getmtime(path))
            except OSError:
                stamps.append(None)
        return (tuple(manifest_keys), tuple(stamps))

    def _key_path(self, key: str) -> str:
        return os.path.join(self._data_dir, f"{key}.yaml")

    def load_all(self) -> list[TemplateResult]:
        """Parse and validate every manifest-listed file. Never raises."""
        try:
            manifest_keys = _read_manifest(self._data_dir)
        except Exception as exc:
            logger.warning("programme-types: _manifest.yaml unreadable: %s", exc)
            return []

        manifest_errors = validate_manifest(manifest_keys)
        for problem in manifest_errors:
            logger.warning("programme-types: %s", problem)

        results: list[TemplateResult] = []
        for key in manifest_keys:
            results.append(self._load_one(key, manifest_keys))
        return results

    def _load_one(self, key: str, manifest_keys: list[str]) -> TemplateResult:
        if not isinstance(key, str) or not key or os.sep in key or (os.altsep and os.altsep in key) or "/" in key:
            logger.warning("programme-types: invalid manifest key %r (path separator rejected)", key)
            return TemplateResult(key=str(key), valid=False, errors=("invalid key",), reviewed=False, content_sha256=None)

        path = self._key_path(key)
        try:
            template = _safe_load_file(path)
        except FileNotFoundError:
            logger.warning("programme-types: %s: file not found", key)
            return TemplateResult(key=key, valid=False, errors=("file not found",), reviewed=False, content_sha256=None)
        except Exception as exc:
            logger.warning("programme-types: %s: %s", key, exc)
            return TemplateResult(key=key, valid=False, errors=(str(exc),), reviewed=False, content_sha256=None)

        if not isinstance(template, Mapping):
            logger.warning("programme-types: %s: did not parse to a mapping", key)
            return TemplateResult(key=key, valid=False, errors=("not a mapping",), reviewed=False, content_sha256=None)

        errors = validate_template(template, expected_key=key, manifest_keys=manifest_keys)
        content_sha256 = canonical_content_hash(template)
        if errors:
            logger.warning("programme-types: %s: invalid (%d error(s))", key, len(errors))
            return TemplateResult(
                key=key, valid=False, errors=tuple(errors), reviewed=False,
                content_sha256=content_sha256, template=template,
            )

        reviewed, review_problems = validate_review(template)
        if review_problems:
            logger.warning("programme-types: %s: review block invalid (%d error(s))", key, len(review_problems))
            return TemplateResult(
                key=key, valid=False, errors=tuple(review_problems), reviewed=False,
                content_sha256=content_sha256, template=template,
            )
        if not reviewed:
            logger.warning("programme-types: %s: not reviewed (absent or stale)", key)

        return TemplateResult(
            key=key, valid=True, errors=(), reviewed=reviewed,
            content_sha256=content_sha256, template=template,
        )

    def _results(self) -> dict[str, TemplateResult]:
        signature = self._current_signature()
        if signature != self._cache_signature:
            self._cache_results = {result.key: result for result in self.load_all()}
            self._cache_signature = signature
        return self._cache_results

    @staticmethod
    def _include_unreviewed() -> bool:
        """Read-only-from-config test override (security.md 7.6). Defaults to
        False, including with no active app context -- there is no code path
        where the absence of a Flask app should widen what gets offered."""
        try:
            from flask import current_app

            return bool(current_app.config.get("PROGRAMME_TYPES_INCLUDE_UNREVIEWED", False))
        except RuntimeError:
            return False

    def offered_types(self) -> list[ProgrammeType]:
        """All-or-nothing (owner decision 6): every manifest type, only when
        every one is valid -- and, unless the test override is on, reviewed;
        otherwise an empty list."""
        results = self._results()
        require_reviewed = not self._include_unreviewed()
        if not results or any(
            not result.valid or (require_reviewed and not result.reviewed)
            for result in results.values()
        ):
            return []
        try:
            manifest_keys = _read_manifest(self._data_dir)
        except Exception:
            return []
        return [
            ProgrammeType.from_template(results[key].template, results[key].content_sha256)
            for key in manifest_keys
            if key in results
        ]

    def get(self, key: str) -> ProgrammeType | None:
        """A valid template (reviewed or not); the caller decides whether the
        test override or ``offered_types()`` membership permits its use."""
        result = self._results().get(key)
        if result is None or not result.valid:
            return None
        return ProgrammeType.from_template(result.template, result.content_sha256)


__all__ = ["ProgrammeTypeCatalogue", "ProgrammeType", "TemplateResult", "DATA_DIR"]
