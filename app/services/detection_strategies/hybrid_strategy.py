"""
Hybrid Detection Strategy

Combines hash-based exact matching with fuzzy matching for comprehensive detection.
Two-phase approach: instant exact matches, then fuzzy analysis on remaining apps.
"""

import hashlib
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Set

from .base import DetectionResult, DetectionStrategy, SimilarityResult

logger = logging.getLogger(__name__)


class HybridDetectionStrategy(DetectionStrategy):
    """
    Hybrid detection strategy combining exact and fuzzy matching.

    Phase 1: Hash-based exact matching (instant, 100% confidence)
    Phase 2: Fuzzy matching for remaining applications

    Best for: Balanced accuracy and speed, production use
    """

    def detect(self, applications: List[Any]) -> DetectionResult:
        """
        Run hybrid duplicate detection.

        Phase 1: O(n) hash-based exact matching
        Phase 2: O(n^2) fuzzy matching on remaining apps
        """
        groups = []
        processed_ids: Set[int] = set()
        exact_matches = 0
        fuzzy_matches = 0

        # Phase 1: Hash-based exact matching
        hash_groups: Dict[str, List[Any]] = {}

        for app in applications:
            normalized = self._normalize_for_hash(getattr(app, "name", "") or "")
            if not normalized:
                continue

            name_hash = hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()

            if name_hash in hash_groups:
                hash_groups[name_hash].append(app)
            else:
                hash_groups[name_hash] = [app]

        # Create groups from exact matches
        for name_hash, hash_apps in hash_groups.items():
            if len(hash_apps) > 1:
                exact_matches += 1

                group = {
                    "applications": [
                        {
                            "id": app.id,
                            "name": app.name,
                            "vendor": getattr(app, "vendor", None),
                            "status": getattr(app, "status", None),
                            "similarity_to_primary": 1.0,
                        }
                        for app in hash_apps
                    ],
                    "primary_app_id": hash_apps[0].id,
                    "similarity_score": 1.0,
                    "duplicate_type": "exact",
                    "match_details": {
                        "strategy": "hybrid",
                        "phase": "exact",
                        "hash": name_hash[:8],
                        "member_count": len(hash_apps),
                    },
                }
                groups.append(group)

                for app in hash_apps:
                    processed_ids.add(app.id)

        # Phase 2: Fuzzy matching for remaining applications
        remaining_apps = [app for app in applications if app.id not in processed_ids]

        for i, app1 in enumerate(remaining_apps):
            if app1.id in processed_ids:
                continue

            similar_apps = [app1]
            similarity_scores = {}

            for app2 in remaining_apps[i + 1 :]:
                if app2.id in processed_ids:
                    continue

                result = self.calculate_similarity(app1, app2)

                if result.overall_score >= self.threshold:
                    similar_apps.append(app2)
                    similarity_scores[app2.id] = result.overall_score
                    processed_ids.add(app2.id)

            # Create fuzzy match group
            if len(similar_apps) > 1:
                fuzzy_matches += 1

                avg_similarity = (
                    sum(similarity_scores.values()) / len(similarity_scores)
                    if similarity_scores
                    else 0
                )

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
                    "similarity_score": avg_similarity,
                    "duplicate_type": "fuzzy",
                    "match_details": {
                        "strategy": "hybrid",
                        "phase": "fuzzy",
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
            metadata={
                "strategy": "hybrid",
                "threshold": self.threshold,
                "phase1_exact": exact_matches,
                "phase2_fuzzy": fuzzy_matches,
            },
        )

    def calculate_similarity(self, app1: Any, app2: Any) -> SimilarityResult:
        """
        Calculate similarity using multiple signals.
        """
        name1 = getattr(app1, "name", "") or ""
        name2 = getattr(app2, "name", "") or ""
        desc1 = getattr(app1, "description", "") or ""
        desc2 = getattr(app2, "description", "") or ""
        vendor1 = getattr(app1, "vendor", "") or ""
        vendor2 = getattr(app2, "vendor", "") or ""

        # Name similarity
        name_sim = self._calculate_name_similarity(name1, name2)

        # Description similarity
        desc_sim = self._calculate_description_similarity(desc1, desc2)

        # Vendor similarity
        vendor_sim = self._calculate_vendor_similarity(vendor1, vendor2)

        # Weighted overall score
        overall = (name_sim * 0.65) + (desc_sim * 0.25) + (vendor_sim * 0.10)

        match_type = "exact" if overall >= 0.95 else "fuzzy"

        return SimilarityResult(
            overall_score=overall,
            name_similarity=name_sim,
            description_similarity=desc_sim,
            vendor_similarity=vendor_sim,
            match_type=match_type,
            details={
                "name_hash_match": self._normalize_for_hash(name1)
                == self._normalize_for_hash(name2)
            },
        )

    def _normalize_for_hash(self, name: str) -> str:
        """
        Normalize name for hash-based matching.
        More aggressive normalization than fuzzy matching.
        """
        if not name:
            return ""

        # Remove all non-alphanumeric, lowercase
        normalized = "".join(c.lower() for c in name if c.isalnum())
        return normalized

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate name similarity with multiple algorithms"""
        if not name1 or not name2:
            return 0.0

        norm1 = self._normalize_name(name1)
        norm2 = self._normalize_name(name2)

        if not norm1 or not norm2:
            return 0.0

        # Exact match after normalization
        if norm1 == norm2:
            return 1.0

        # Hash match
        if self._normalize_for_hash(name1) == self._normalize_for_hash(name2):
            return 0.98

        # Acronym matching
        acronym1 = self._extract_acronym_from_parentheses(name1)
        acronym2 = self._extract_acronym_from_parentheses(name2)

        if acronym1 and norm2 == acronym1:
            return 0.95
        if acronym2 and norm1 == acronym2:
            return 0.95

        # SequenceMatcher
        seq_ratio = SequenceMatcher(None, norm1, norm2).ratio()

        # Jaccard
        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())
        jaccard = len(tokens1 & tokens2) / len(tokens1 | tokens2) if (tokens1 | tokens2) else 0

        # Acronym expansion bonus
        acronym_boost = 0.0
        expansions1 = self._expand_acronyms(norm1)
        expansions2 = self._expand_acronyms(norm2)

        for exp1 in expansions1:
            for exp2 in expansions2:
                if exp1 == exp2:
                    acronym_boost = 0.25
                    break

        # Substring bonus
        substring_boost = 0.0
        if len(norm1) >= 5 and len(norm2) >= 5:
            if norm1 in norm2 or norm2 in norm1:
                substring_boost = 0.15

        base_score = max(seq_ratio, jaccard)
        return min(1.0, base_score + acronym_boost + substring_boost)

    def _calculate_description_similarity(self, desc1: str, desc2: str) -> float:
        """Calculate description similarity"""
        if not desc1 or not desc2:
            return 0.0

        norm1 = self._normalize_name(desc1)
        norm2 = self._normalize_name(desc2)

        if not norm1 or not norm2:
            return 0.0

        # Token overlap
        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())

        # Remove stop words
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
            "by",
            "with",
        }
        tokens1 = tokens1 - stop_words
        tokens2 = tokens2 - stop_words

        if not tokens1 or not tokens2:
            return 0.0

        return len(tokens1 & tokens2) / len(tokens1 | tokens2)

    def _calculate_vendor_similarity(self, vendor1: str, vendor2: str) -> float:
        """Calculate vendor similarity"""
        if not vendor1 or not vendor2:
            return 0.5

        norm1 = self._normalize_vendor(vendor1)
        norm2 = self._normalize_vendor(vendor2)

        if norm1 == norm2:
            return 1.0

        if norm1 in norm2 or norm2 in norm1:
            return 0.8

        return 0.0
