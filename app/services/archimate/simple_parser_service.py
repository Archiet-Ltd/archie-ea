"""Deterministic (no-LLM) spreadsheet parser for the document-upload flow.

Restored 1 Sep 2026. `app/modules/ai_chat/routes/document_routes.py::upload_document`
imports `SimpleParserService` on the `use_simple_parsing=true` branch — the branch
behind the "AI vs Simple Parsing" toggle in `templates/ai_chat/document_upload.html`.
The module had been dropped from this OSS extract, so flipping the toggle and
uploading raised `ImportError` → a 500 on a real, user-reachable control. This
restores the contract against the extractor that *does* exist
(`document_text_extractor.parse_spreadsheet_to_records`).

Design principle (Entelim is a system of record — never invent data): a row becomes
an element ONLY when a recognised name column carries a non-empty value. Rows with
no derivable name are skipped and counted, never fabricated into placeholder
elements. Every other column is preserved verbatim under the element's
`properties`, so nothing is lost and nothing is guessed. Type is taken from an
explicit type column when present; otherwise it defaults to ApplicationComponent
(Simple mode is documented as the CSV/Excel application-import path) and the
downstream `ElementTypeNormalizer` maps it. Relationships are never inferred here —
inferring links without an LLM would be invention — so the list is always empty.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.modules.architecture.services.document_text_extractor import (
    parse_spreadsheet_to_records,
)

# Column headers (lower-cased, stripped) that legitimately carry an element name.
_NAME_COLUMNS = (
    "name", "element", "element_name",
    "application", "application_name", "app", "app_name",
    "component", "component_name", "system", "system_name",
    "title", "capability", "capability_name", "service", "service_name",
)
# Columns that carry an explicit ArchiMate type.
_TYPE_COLUMNS = ("type", "element_type", "archimate_type", "category")
# Columns that carry a description.
_DESC_COLUMNS = ("description", "desc", "notes", "summary", "detail", "details")


def _first_nonempty(record: Dict[str, Any], columns) -> Optional[str]:
    """Return the first non-empty value among `columns`, matched case-insensitively
    against the record's keys. None if nothing matches."""
    lowered = {
        (k or "").strip().lower().replace(" ", "_"): v
        for k, v in record.items()
        if k is not None
    }
    for col in columns:
        val = lowered.get(col)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


class SimpleParserService:
    """No-LLM parser: spreadsheet rows → ArchiMate element dicts."""

    def parse_document(
        self,
        file_path: str,
        analysis_context: Optional[str] = None,
        target_application_id: Optional[int] = None,
        target_vendor_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return {"elements", "relationships", "metadata"} for the upload pipeline.

        Only CSV/Excel are supported (that is what Simple mode advertises). Any
        other format, or an unreadable file, returns zero elements with an honest
        `metadata.error` rather than raising — the caller renders the empty preview
        and the user can switch to AI mode.
        """
        source_name = os.path.basename(file_path or "")
        parsed = parse_spreadsheet_to_records(file_path)

        if parsed.get("error"):
            return self._empty(
                source_name,
                error=parsed["error"],
                note="Simple parsing supports CSV/Excel only — use AI analysis for "
                     "PDFs, documents and slides.",
            )

        records: List[Dict[str, Any]] = parsed.get("records", []) or []
        headers = parsed.get("headers", []) or []

        elements: List[Dict[str, Any]] = []
        skipped = 0
        for record in records:
            name = _first_nonempty(record, _NAME_COLUMNS)
            if not name:
                skipped += 1  # no name in the row — never invent one
                continue
            elem_type = _first_nonempty(record, _TYPE_COLUMNS) or "ApplicationComponent"
            description = _first_nonempty(record, _DESC_COLUMNS)

            # Preserve every remaining non-empty column as a property, so the
            # source data survives and nothing is discarded or fabricated.
            consumed = set(_NAME_COLUMNS + _TYPE_COLUMNS + _DESC_COLUMNS)
            properties = {
                str(k).strip(): str(v).strip()
                for k, v in record.items()
                if k is not None
                and (k or "").strip().lower().replace(" ", "_") not in consumed
                and v is not None
                and str(v).strip()
            }

            element: Dict[str, Any] = {
                "name": name,
                "type": elem_type,
                "source": "simple_parser",
            }
            if description:
                element["description"] = description
            if properties:
                element["properties"] = properties
            elements.append(element)

        return {
            "elements": elements,
            "relationships": [],  # never inferred without an LLM
            "metadata": {
                "parser": "simple",
                "source_file": source_name,
                "format": parsed.get("format"),
                "headers": headers,
                "row_count": len(records),
                "mapped_count": len(elements),
                "skipped_count": skipped,
                "analysis_context": analysis_context,
            },
        }

    @staticmethod
    def _empty(source_name: str, error: Optional[str] = None,
               note: Optional[str] = None) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "parser": "simple",
            "source_file": source_name,
            "row_count": 0,
            "mapped_count": 0,
            "skipped_count": 0,
        }
        if error:
            meta["error"] = error
        if note:
            meta["note"] = note
        return {"elements": [], "relationships": [], "metadata": meta}
