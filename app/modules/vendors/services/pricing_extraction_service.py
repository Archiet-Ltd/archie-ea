"""
Pricing Extraction Service — LLM-based extraction of vendor pricing from documents.

Parallel to DocumentIngestionService (architecture_assistant/document_ingestion.py).
NOT a subclass — different extraction target (pricing vs ArchiMate elements).

Extraction flow:
1. Read document text (PDF/DOCX/TXT)
2. Send to LLM with pricing-specific extraction prompt
3. Parse JSON response
4. Normalize pricing (monthly → annual, currency notes)
5. Return structured items for admin staging review

No DB writes — the caller (admin route) handles staging and confirmation.
"""
import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PLAIN_EXTRACTION_ERROR = "We couldn't read this document. Try a CSV or XLSX file instead."

# Imported at module level so tests can patch it
try:
    from app.modules.ai_chat.services.llm_service import LLMService
except ImportError:
    LLMService = None  # type: ignore[assignment,misc]

def _smart_truncate(text: str, max_chars: int = 15000) -> str:
    """Return at most max_chars characters from text.

    Strategy: take the first 5000 chars and the last 10000 chars, joined by
    a truncation marker.  Pricing schedules appear at the end of contracts, so
    the tail is more valuable than the middle.

    If the text fits within max_chars it is returned as-is.
    """
    if len(text) <= max_chars:
        return text

    head = 5000
    tail = max_chars - head  # 10000
    logger.warning(
        "Document text length %d exceeds %d chars — truncating to first %d + last %d chars.",
        len(text), max_chars, head, tail,
    )
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


PRICING_EXTRACTION_PROMPT = """You are a contract pricing extraction specialist.

Extract ALL vendor pricing information from the following document text.

For EACH vendor product mentioned, extract:
- vendor_name: The vendor/company name
- product_name: The specific product or service name
- tier: The pricing tier or plan name (e.g., "Standard", "Professional", "Enterprise")
- annual_cost: The annual cost in the document's currency. If monthly, multiply by 12. If per-user/month, multiply by 12 (do NOT multiply by user count — report per-unit annual).
- monthly_cost: The monthly cost if explicitly stated (before annualization)
- unit_type: What the price is per — "user", "seat", "host", "workload", "transaction", "flat_fee", "site"
- unit_quantity: How many units are covered by this price (if stated)
- currency: 3-letter ISO currency code (USD, EUR, GBP, etc.)
- contract_term_months: Contract duration in months (e.g., 12, 24, 36)
- discount_percent: Any discount percentage mentioned
- effective_date: Contract start date (ISO format YYYY-MM-DD)
- expiry_date: Contract end date (ISO format YYYY-MM-DD)
- setup_fee: One-time setup cost
- implementation_fee: Implementation/onboarding cost
- notes: Any important context about this pricing

Return ONLY valid JSON in this exact format:
{{
  "pricing_items": [
    {{
      "vendor_name": "...",
      "product_name": "...",
      "tier": "...",
      "annual_cost": 12000,
      "monthly_cost": null,
      "unit_type": "user",
      "unit_quantity": 100,
      "currency": "USD",
      "contract_term_months": 12,
      "discount_percent": null,
      "effective_date": null,
      "expiry_date": null,
      "setup_fee": null,
      "implementation_fee": null,
      "notes": null
    }}
  ]
}}

If no pricing information is found, return: {{"pricing_items": []}}

DOCUMENT TEXT:
{document_text}"""


class PricingExtractionService:
    """Extract pricing from document text via LLM."""

    def extract_from_text(self, text: str) -> Dict[str, Any]:
        """
        Extract pricing items from document text using LLM.

        Returns:
            {"success": True, "items": [...normalized items...]}
            or {"success": False, "error": "..."}
        """
        if not text or not text.strip():
            return {"success": False, "error": "Empty document text"}

        try:
            if LLMService is None:
                return {"success": False, "error": "LLMService not available"}

            provider, model = LLMService._get_configured_provider()
            document_text = _smart_truncate(text, max_chars=15000)
            prompt = PRICING_EXTRACTION_PROMPT.format(document_text=document_text)
            raw_text, _ = LLMService._call_llm(
                prompt=prompt,
                model=model,
                provider=provider,
                max_tokens=4000,
            )

            parsed = self._parse_llm_json(raw_text)
            if not parsed or "pricing_items" not in parsed:
                return {"success": False, "error": "LLM returned invalid JSON"}

            items = []
            for item in parsed["pricing_items"]:
                normalized = self._normalize_pricing_item(item)
                if normalized:
                    items.append(normalized)

            return {"success": True, "items": items}

        except Exception as e:
            logger.error("Pricing extraction failed: %s", e)
            return {"success": False, "error": PLAIN_EXTRACTION_ERROR}

    def extract_from_file(self, file_storage) -> Dict[str, Any]:
        """
        Extract text from uploaded file, then extract pricing via LLM.

        Supports: .pdf, .docx, .txt, .md
        """
        filename = file_storage.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        text = ""
        if ext in ("txt", "md"):
            text = file_storage.read().decode("utf-8", errors="replace")
        elif ext == "pdf":
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(file_storage)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                return {"success": False, "error": "PyPDF2 not installed"}
        elif ext == "docx":
            try:
                import docx
                doc = docx.Document(file_storage)
                text = "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                return {"success": False, "error": "python-docx not installed"}
        else:
            return {"success": False, "error": f"Unsupported file type: .{ext}"}

        if not text.strip():
            return {"success": False, "error": "No text extracted from document"}

        return self.extract_from_text(text)

    def _parse_llm_json(self, raw_text: str) -> Optional[Dict]:
        """Parse JSON from LLM response, stripping markdown fences."""
        if not raw_text:
            return None
        # Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
        cleaned = cleaned.strip()
        # Try to find a JSON object
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                logger.exception("Failed to operation")
                pass
        # Direct parse attempt
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON: %s", raw_text[:200])
            return None

    def _normalize_pricing_item(self, item: Dict) -> Optional[Dict]:
        """
        Normalize a pricing item:
        - Monthly → annual conversion
        - Ensure required fields exist
        - Clean up types
        """
        vendor = item.get("vendor_name", "").strip()
        product = item.get("product_name", "").strip()
        if not vendor or not product:
            return None

        annual_cost = item.get("annual_cost")
        monthly_cost = item.get("monthly_cost")
        if annual_cost is None and monthly_cost is not None:
            try:
                annual_cost = float(monthly_cost) * 12
            except (ValueError, TypeError):
                annual_cost = None

        return {
            "vendor_name": vendor,
            "product_name": product,
            "tier": item.get("tier", "Standard"),
            "annual_cost": annual_cost,
            "unit_type": item.get("unit_type") or None,
            "unit_quantity": item.get("unit_quantity"),
            "currency": item.get("currency", "USD"),
            "contract_term_months": item.get("contract_term_months", 12),
            "discount_percent": item.get("discount_percent"),
            "effective_date": item.get("effective_date"),
            "expiry_date": item.get("expiry_date"),
            "setup_fee": item.get("setup_fee"),
            "implementation_fee": item.get("implementation_fee"),
            "notes": item.get("notes"),
        }
