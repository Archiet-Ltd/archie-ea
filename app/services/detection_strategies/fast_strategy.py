"""
Fast Detection Strategy

Quick duplicate detection using name similarity with acronym expansion.
Optimized for speed over comprehensive analysis.
"""

import logging
from difflib import SequenceMatcher
from typing import Any, List

from .base import DetectionResult, DetectionStrategy, SimilarityResult

logger = logging.getLogger(__name__)


class FastDetectionStrategy(DetectionStrategy):
    """
    Fast & Simple detection strategy.

    Uses name similarity with:
    - SequenceMatcher for fuzzy matching
    - Jaccard similarity for token overlap
    - Acronym expansion for IT terms
    - Vendor normalization

    Best for: Quick scans, large portfolios, initial triage
    """

    def detect(self, applications: List[Any]) -> DetectionResult:
        """
        Run fast duplicate detection.

        Time complexity: O(n^2) but optimized with early termination.
        """
        groups = []
        processed_ids = set()
        exact_matches = 0
        fuzzy_matches = 0

        for i, app1 in enumerate(applications):
            if app1.id in processed_ids:
                continue

            # Find all similar applications
            similar_apps = [app1]
            similarity_scores = {}

            for app2 in applications[i + 1 :]:
                if app2.id in processed_ids:
                    continue

                result = self.calculate_similarity(app1, app2)

                if result.overall_score >= self.threshold:
                    similar_apps.append(app2)
                    similarity_scores[app2.id] = result.overall_score
                    processed_ids.add(app2.id)

            # Create group if duplicates found
            if len(similar_apps) > 1:
                # Determine if exact or fuzzy match
                max_similarity = max(similarity_scores.values()) if similarity_scores else 0
                is_exact = max_similarity >= 0.95

                if is_exact:
                    exact_matches += 1
                else:
                    fuzzy_matches += 1

                group = {
                    "applications": [
                        {
                            "id": app.id,
                            "name": app.name,
                            "vendor": getattr(app, "vendor", None),
                            "status": getattr(app, "status", None),
                            "similarity_to_primary": similarity_scores.get(app.id, 1.0),
                        }
                        for app in similar_apps
                    ],
                    "primary_app_id": app1.id,
                    "similarity_score": sum(similarity_scores.values()) / len(similarity_scores)
                    if similarity_scores
                    else 1.0,
                    "duplicate_type": "exact" if is_exact else "fuzzy",
                    "match_details": {
                        "strategy": "fast",
                        "pair_count": len(similarity_scores),
                        "threshold": self.threshold,
                        "member_count": len(similar_apps),
                    },
                }
                groups.append(group)
                processed_ids.add(app1.id)

        # Calculate estimated savings
        total_savings = sum(
            self._estimate_group_savings(
                [app for app in applications if app.id in [a["id"] for a in g["applications"]]]
            )
            for g in groups
        )

        return DetectionResult(
            groups=groups,
            exact_matches=exact_matches,
            fuzzy_matches=fuzzy_matches,
            applications_analyzed=len(applications),
            estimated_savings=total_savings,
            metadata={"strategy": "fast", "threshold": self.threshold},
        )

    def calculate_similarity(self, app1: Any, app2: Any) -> SimilarityResult:
        """
        Calculate similarity using name-based algorithms.
        """
        name1 = getattr(app1, "name", "") or ""
        name2 = getattr(app2, "name", "") or ""
        desc1 = getattr(app1, "description", "") or ""
        desc2 = getattr(app2, "description", "") or ""
        vendor1 = getattr(app1, "vendor", "") or ""
        vendor2 = getattr(app2, "vendor", "") or ""

        # Calculate name similarity
        name_sim = self._calculate_name_similarity(name1, name2)

        # Calculate description similarity (lighter weight)
        desc_sim = self._calculate_description_similarity(desc1, desc2)

        # Calculate vendor similarity
        vendor_sim = self._calculate_vendor_similarity(vendor1, vendor2)

        # Weighted overall score
        # Name: 70%, Description: 20%, Vendor: 10%
        overall = (name_sim * 0.70) + (desc_sim * 0.20) + (vendor_sim * 0.10)

        # Determine match type
        match_type = "exact" if overall >= 0.95 else "fuzzy"

        return SimilarityResult(
            overall_score=overall,
            name_similarity=name_sim,
            description_similarity=desc_sim,
            vendor_similarity=vendor_sim,
            match_type=match_type,
            details={"name_normalized": self._normalize_name(name1), "acronym_matched": False},
        )

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate name similarity using multiple algorithms.
        """
        if not name1 or not name2:
            return 0.0

        # Normalize names
        norm1 = self._normalize_name(name1)
        norm2 = self._normalize_name(name2)

        if not norm1 or not norm2:
            return 0.0

        # Exact match
        if norm1 == norm2:
            return 1.0

        # Check for acronym in parentheses match
        acronym1 = self._extract_acronym_from_parentheses(name1)
        acronym2 = self._extract_acronym_from_parentheses(name2)

        # If one name IS the acronym of another
        if acronym1 and norm2 == acronym1:
            return 0.95
        if acronym2 and norm1 == acronym2:
            return 0.95

        # Check if acronym matches the full name pattern
        # e.g., "PRS" should match "Plasterboard Recycling System (PRS)"
        if acronym2 and norm1 == acronym2:
            return 0.95
        if acronym1 and norm2 == acronym1:
            return 0.95

        # SequenceMatcher similarity
        seq_ratio = SequenceMatcher(None, norm1, norm2).ratio()

        # Jaccard similarity (token-based)
        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())
        if tokens1 and tokens2:
            jaccard = len(tokens1 & tokens2) / len(tokens1 | tokens2)
        else:
            jaccard = 0.0

        # Check acronym expansions
        expansions1 = self._expand_acronyms(norm1)
        expansions2 = self._expand_acronyms(norm2)

        acronym_boost = 0.0
        for exp1 in expansions1:
            for exp2 in expansions2:
                if exp1 == exp2:
                    acronym_boost = 0.3
                    break
                exp_ratio = SequenceMatcher(None, exp1, exp2).ratio()
                if exp_ratio > 0.8:
                    acronym_boost = max(acronym_boost, 0.2)

        # Substring matching bonus
        substring_boost = 0.0
        if len(norm1) >= 5 and len(norm2) >= 5:
            if norm1 in norm2 or norm2 in norm1:
                substring_boost = 0.2

        # Combine scores
        base_score = max(seq_ratio, jaccard)
        final_score = min(1.0, base_score + acronym_boost + substring_boost)

        return final_score

    def _calculate_description_similarity(self, desc1: str, desc2: str) -> float:
        """Calculate description similarity using token overlap"""
        if not desc1 or not desc2:
            return 0.0

        # Normalize descriptions
        norm1 = self._normalize_name(desc1)
        norm2 = self._normalize_name(desc2)

        if not norm1 or not norm2:
            return 0.0

        # Token-based Jaccard similarity
        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())

        # Remove common stop words
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "for",
            "to",
            "of",
            "and",
            "or",
            "in",
            "on",
            "at",
        }
        tokens1 = tokens1 - stop_words
        tokens2 = tokens2 - stop_words

        if not tokens1 or not tokens2:
            return 0.0

        jaccard = len(tokens1 & tokens2) / len(tokens1 | tokens2)
        return jaccard

    def _calculate_vendor_similarity(self, vendor1: str, vendor2: str) -> float:
        """Calculate vendor similarity with alias matching"""
        if not vendor1 or not vendor2:
            return 0.5  # Neutral score if vendor unknown

        norm1 = self._normalize_vendor(vendor1)
        norm2 = self._normalize_vendor(vendor2)

        if norm1 == norm2:
            return 1.0

        # Check for partial match
        if norm1 in norm2 or norm2 in norm1:
            return 0.8

        return 0.0
