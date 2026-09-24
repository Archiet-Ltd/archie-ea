"""
-> app.modules.vendors.services.discovery_service

Vendor MDM Service

Provides vendor normalization, deduplication, and taxonomy management.
Implements fuzzy matching, canonical ID assignment, and reconciliation workflows.

Key Features:
- Fuzzy name matching with confidence scoring
- Canonical ID assignment and deduplication
- Bulk reconciliation UI support
- Integration with external data sources
- Audit trail for all MDM operations
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

# rapidfuzz (MIT), not fuzzywuzzy (GPL-2.0-only). fuzzywuzzy and its
# python-Levenshtein speedup are GPL-2.0, which cannot be sublicensed under
# Entelim's commercial licence — see docs/adr/0006. rapidfuzz is API-compatible
# for the functions used here; it returns a float where fuzzywuzzy returned
# int(round(...)), so call sites round to keep scores identical.
from rapidfuzz import fuzz
from sqlalchemy import and_

from app.models.vendor_taxonomy import ProductTaxonomy, TaxonomyMapping, VendorTaxonomy

from app import db
from app.models.vendor.vendor_organization import VendorOrganization

logger = logging.getLogger(__name__)


class VendorMDMService:
    """
    Master Data Management service for vendors and products.

    Handles normalization, deduplication, and taxonomy management.
    """

    def __init__(self):
        self.fuzzy_threshold = 0.8  # Minimum confidence for auto-matching
        self.cache = {}  # Simple in-memory cache for performance

    def normalize_vendor_name(self, raw_name: str) -> Dict:
        """
        Normalize a vendor name using fuzzy matching against canonical taxonomy.

        Args:
            raw_name: Raw vendor name to normalize

        Returns:
            Dict with canonical_name, confidence, and match_method
        """
        if not raw_name or not raw_name.strip():
            return {"canonical_name": None, "confidence": 0.0, "match_method": "invalid"}

        raw_name = raw_name.strip()

        # Check cache first
        cache_key = f"vendor_{raw_name.lower()}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Exact match first
        exact_match = VendorTaxonomy.query.filter(
            and_(
                VendorTaxonomy.canonical_name.ilike(raw_name),
                VendorTaxonomy.is_active.is_(True),
                VendorTaxonomy.is_canonical.is_(True),
            )
        ).first()

        if exact_match:
            result = {
                "canonical_name": exact_match.canonical_name,
                "confidence": 1.0,
                "match_method": "exact",
                "taxonomy_id": exact_match.id,
            }
            self.cache[cache_key] = result
            return result

        # Check aliases
        alias_match = VendorTaxonomy.query.filter(
            and_(
                VendorTaxonomy.aliases.contains(raw_name),
                VendorTaxonomy.is_active.is_(True),
                VendorTaxonomy.is_canonical.is_(True),
            )
        ).first()

        if alias_match:
            result = {
                "canonical_name": alias_match.canonical_name,
                "confidence": 0.95,
                "match_method": "alias",
                "taxonomy_id": alias_match.id,
            }
            self.cache[cache_key] = result
            return result

        # Fuzzy matching
        candidates = VendorTaxonomy.query.filter(
            and_(VendorTaxonomy.is_active.is_(True), VendorTaxonomy.is_canonical.is_(True))
        ).all()

        best_match = None
        best_score = 0.0

        for candidate in candidates:
            # Check canonical name
            score = round(fuzz.ratio(raw_name.lower(), candidate.canonical_name.lower()))
            if score > best_score:
                best_score = score
                best_match = candidate

            # Check aliases
            for alias in candidate.aliases_list:
                score = round(fuzz.ratio(raw_name.lower(), alias.lower()))
                if score > best_score:
                    best_score = score
                    best_match = candidate

        if best_match and best_score >= (self.fuzzy_threshold * 100):
            result = {
                "canonical_name": best_match.canonical_name,
                "confidence": best_score / 100.0,
                "match_method": "fuzzy",
                "taxonomy_id": best_match.id,
            }
        else:
            result = {
                "canonical_name": raw_name,  # Return as-is if no good match
                "confidence": 0.0,
                "match_method": "no_match",
            }

        self.cache[cache_key] = result
        return result

    def normalize_product_name(self, raw_name: str, vendor_name: Optional[str] = None) -> Dict:
        """
        Normalize a product name using fuzzy matching against canonical taxonomy.

        Args:
            raw_name: Raw product name to normalize
            vendor_name: Optional vendor name for context

        Returns:
            Dict with canonical_name, confidence, and match_method
        """
        if not raw_name or not raw_name.strip():
            return {"canonical_name": None, "confidence": 0.0, "match_method": "invalid"}

        raw_name = raw_name.strip()

        # Check cache first
        cache_key = f"product_{raw_name.lower()}_{vendor_name or ''}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Exact match first
        query = ProductTaxonomy.query.filter(
            and_(
                ProductTaxonomy.canonical_name.ilike(raw_name),
                ProductTaxonomy.is_active.is_(True),
                ProductTaxonomy.is_canonical.is_(True),
            )
        )

        exact_match = query.first()
        if exact_match:
            result = {
                "canonical_name": exact_match.canonical_name,
                "confidence": 1.0,
                "match_method": "exact",
                "taxonomy_id": exact_match.id,
            }
            self.cache[cache_key] = result
            return result

        # Fuzzy matching
        candidates = ProductTaxonomy.query.filter(
            and_(ProductTaxonomy.is_active.is_(True), ProductTaxonomy.is_canonical.is_(True))
        ).all()

        best_match = None
        best_score = 0.0

        for candidate in candidates:
            score = round(fuzz.ratio(raw_name.lower(), candidate.canonical_name.lower()))
            if score > best_score:
                best_score = score
                best_match = candidate

        if best_match and best_score >= (self.fuzzy_threshold * 100):
            result = {
                "canonical_name": best_match.canonical_name,
                "confidence": best_score / 100.0,
                "match_method": "fuzzy",
                "taxonomy_id": best_match.id,
            }
        else:
            result = {"canonical_name": raw_name, "confidence": 0.0, "match_method": "no_match"}

        self.cache[cache_key] = result
        return result

    def find_duplicates(self, name_type: str = "vendor", threshold: float = 0.9) -> List[Dict]:
        """
        Find potential duplicates in vendor/product data.

        Args:
            name_type: 'vendor' or 'product'
            threshold: Minimum similarity score (0.0 - 1.0)

        Returns:
            List of duplicate groups with confidence scores
        """
        if name_type == "vendor":
            model = VendorOrganization
            name_field = VendorOrganization.name
        else:
            # For products, we'd need to check vendor products
            return []

        # Get all active names
        names = (
            db.session.query(name_field)
            .filter(
                and_(
                    model.id.isnot(None),  # Ensure we have records
                    # Add any other filters for active records
                )
            )
            .all()
        )

        names = [n[0] for n in names if n[0]]

        # Find similar pairs
        duplicates = []
        checked = set()

        for i, name1 in enumerate(names):
            for j, name2 in enumerate(names):
                if i >= j or (name1, name2) in checked:
                    continue

                checked.add((name1, name2))

                similarity = round(fuzz.ratio(name1.lower(), name2.lower()))
                if similarity >= (threshold * 100):
                    duplicates.append(
                        {
                            "name1": name1,
                            "name2": name2,
                            "similarity": similarity / 100.0,
                            "method": "fuzzy_ratio",
                        }
                    )

        return duplicates

    def create_taxonomy_mapping(
        self,
        raw_name: str,
        canonical_id: int,
        name_type: str,
        confidence: float,
        method: str = "manual",
        source: str = "manual",
    ) -> TaxonomyMapping:
        """
        Create a taxonomy mapping record.

        Args:
            raw_name: Original raw name
            canonical_id: ID of canonical taxonomy entry
            name_type: 'vendor' or 'product'
            confidence: Match confidence (0.0 - 1.0)
            method: Match method ('exact', 'fuzzy', 'manual', 'ai')
            source: Source of the mapping

        Returns:
            Created TaxonomyMapping instance
        """
        # Get canonical name
        if name_type == "vendor":
            canonical = VendorTaxonomy.query.get(canonical_id)
        else:
            canonical = ProductTaxonomy.query.get(canonical_id)

        if not canonical:
            raise ValueError(f"Canonical {name_type} with ID {canonical_id} not found")

        mapping = TaxonomyMapping(
            raw_name=raw_name,
            name_type=name_type,
            canonical_id=canonical_id,
            canonical_name=canonical.canonical_name,
            match_confidence=confidence,
            match_method=method,
            match_source=source,
        )

        db.session.add(mapping)
        db.session.commit()

        return mapping

    def bulk_normalize(self, items: List[Dict], name_type: str = "vendor") -> List[Dict]:
        """
        Bulk normalize a list of vendor/product names.

        Args:
            items: List of dicts with 'name' key and optional 'id' key
            name_type: 'vendor' or 'product'

        Returns:
            List of normalized results
        """
        results = []

        for item in items:
            raw_name = item.get("name", "").strip()
            if not raw_name:
                results.append(
                    {
                        "id": item.get("id"),
                        "original_name": raw_name,
                        "normalized_name": None,
                        "confidence": 0.0,
                        "method": "invalid",
                    }
                )
                continue

            if name_type == "vendor":
                normalized = self.normalize_vendor_name(raw_name)
            else:
                normalized = self.normalize_product_name(raw_name)

            results.append(
                {
                    "id": item.get("id"),
                    "original_name": raw_name,
                    "normalized_name": normalized["canonical_name"],
                    "confidence": normalized["confidence"],
                    "method": normalized["match_method"],
                    "taxonomy_id": normalized.get("taxonomy_id"),
                }
            )

        return results

    def get_reconciliation_candidates(
        self, name_type: str = "vendor", min_confidence: float = 0.7
    ) -> List[Dict]:
        """
        Get candidates for manual reconciliation.

        Args:
            name_type: 'vendor' or 'product'
            min_confidence: Minimum confidence to include

        Returns:
            List of reconciliation candidates
        """
        # Get mappings with low confidence that need review
        mappings = (
            TaxonomyMapping.query.filter(
                and_(
                    TaxonomyMapping.name_type == name_type,
                    TaxonomyMapping.match_confidence < min_confidence,
                    TaxonomyMapping.is_validated.is_(False),
                )
            )
            .order_by(TaxonomyMapping.created_at.desc())
            .limit(100)
            .all()
        )

        candidates = []
        for mapping in mappings:
            candidates.append(
                {
                    "id": mapping.id,
                    "raw_name": mapping.raw_name,
                    "suggested_canonical": mapping.canonical_name,
                    "confidence": mapping.match_confidence,
                    "method": mapping.match_method,
                    "source": mapping.match_source,
                    "created_at": mapping.created_at.isoformat() if mapping.created_at else None,
                }
            )

        return candidates

    def validate_mapping(self, mapping_id: int, is_valid: bool, user: str) -> bool:
        """
        Validate or reject a taxonomy mapping.

        Args:
            mapping_id: ID of the mapping to validate
            is_valid: True to accept, False to reject
            user: Username performing validation

        Returns:
            True if successful
        """
        mapping = TaxonomyMapping.query.get(mapping_id)
        if not mapping:
            return False

        if is_valid:
            mapping.is_validated = True
            mapping.validated_by = user
            mapping.validated_at = datetime.utcnow()
        else:
            # For rejected mappings, we might want to delete or mark as invalid
            db.session.delete(mapping)

        db.session.commit()
        return True

    def import_external_data(self, source: str, data: List[Dict]) -> Dict:
        """
        Import vendor/product data from external sources.

        Args:
            source: Source name ('g2', 'crunchbase', 'croud')
            data: List of vendor/product records

        Returns:
            Import results summary
        """
        results = {"processed": 0, "created": 0, "updated": 0, "errors": 0}

        for item in data:
            try:
                results["processed"] += 1

                if source == "g2":
                    self._import_g2_data(item)
                elif source == "crunchbase":
                    self._import_crunchbase_data(item)
                elif source == "croud":
                    self._import_croud_data(item)
                else:
                    raise ValueError(f"Unknown source: {source}")

                results["created"] += 1  # Simplified - in reality check if created vs updated

            except Exception as e:
                logger.error(f"Error importing {item}: {e}")
                results["errors"] += 1

        return results

    def _import_g2_data(self, data: Dict):
        """Import data from G2."""
        # PROD-010: not yet implemented — log and return instead of crashing
        logger.warning(
            "VendorMDMService._import_g2_data called but G2 integration is not yet available."
        )
        return None

    def _import_crunchbase_data(self, data: Dict):
        """Import data from Crunchbase."""
        # PROD-010: not yet implemented — log and return instead of crashing
        logger.warning(
            "VendorMDMService._import_crunchbase_data called but Crunchbase integration is not yet available."
        )
        return None

    def _import_croud_data(self, data: Dict):
        """Import data from Croud."""
        # PROD-010: not yet implemented — log and return instead of crashing
        logger.warning(
            "VendorMDMService._import_croud_data called but Croud integration is not yet available."
        )
        return None
