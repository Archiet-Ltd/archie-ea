"""
Enhanced Detection Strategy

Advanced duplicate detection using business process and capability analysis.
Requires mapped business processes and capabilities for best results.
"""

import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set

from .base import DetectionResult, DetectionStrategy, SimilarityResult

logger = logging.getLogger(__name__)


class EnhancedDetectionStrategy(DetectionStrategy):
    """
    Enhanced detection strategy with business context.

    Analyzes:
    - Name and description similarity
    - Business process overlap
    - Capability alignment
    - Technical stack similarity
    - User base overlap

    Best for: Comprehensive analysis, rationalization planning
    """

    def __init__(self, threshold: float = 0.55, config: Optional[Dict[str, Any]] = None):
        super().__init__(threshold, config)

        # Weights for different similarity factors
        self.weights = config.get("weights", {}) if config else {}
        self.name_weight = self.weights.get("name", 0.30)
        self.description_weight = self.weights.get("description", 0.15)
        self.vendor_weight = self.weights.get("vendor", 0.05)
        self.process_weight = self.weights.get("process", 0.25)
        self.capability_weight = self.weights.get("capability", 0.25)

    def detect(self, applications: List[Any]) -> DetectionResult:
        """
        Run enhanced duplicate detection.

        Uses business context when available, falls back to name/description otherwise.
        """
        groups = []
        processed_ids: Set[int] = set()
        exact_matches = 0
        fuzzy_matches = 0

        # Pre-compute business context for all applications
        app_context = self._build_application_context(applications)

        for i, app1 in enumerate(applications):
            if app1.id in processed_ids:
                continue

            similar_apps = [app1]
            similarity_scores = {}
            match_reasons = []

            for app2 in applications[i + 1 :]:
                if app2.id in processed_ids:
                    continue

                result = self.calculate_similarity(
                    app1, app2, context1=app_context.get(app1.id), context2=app_context.get(app2.id)
                )

                if result.overall_score >= self.threshold:
                    similar_apps.append(app2)
                    similarity_scores[app2.id] = result.overall_score
                    processed_ids.add(app2.id)

                    # Track match reasons
                    if result.details:
                        if result.details.get("process_overlap", 0) > 0.5:
                            match_reasons.append("shared_processes")
                        if result.details.get("capability_overlap", 0) > 0.5:
                            match_reasons.append("shared_capabilities")
                        if result.name_similarity > 0.8:
                            match_reasons.append("name_similarity")

            # Create group
            if len(similar_apps) > 1:
                avg_similarity = (
                    sum(similarity_scores.values()) / len(similarity_scores)
                    if similarity_scores
                    else 0
                )

                is_exact = avg_similarity >= 0.95
                if is_exact:
                    exact_matches += 1
                else:
                    fuzzy_matches += 1

                # Determine duplicate type based on match reasons
                if "shared_processes" in match_reasons and "shared_capabilities" in match_reasons:
                    dup_type = "functional"
                elif "shared_capabilities" in match_reasons:
                    dup_type = "capability"
                elif is_exact:
                    dup_type = "exact"
                else:
                    dup_type = "fuzzy"

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
                    "duplicate_type": dup_type,
                    "match_details": {
                        "strategy": "enhanced",
                        "pair_count": len(similarity_scores),
                        "match_reasons": list(set(match_reasons)),
                        "threshold": self.threshold,
                        "member_count": len(similar_apps),
                        "has_business_context": bool(app_context.get(app1.id)),
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
                "strategy": "enhanced",
                "threshold": self.threshold,
                "weights": {
                    "name": self.name_weight,
                    "description": self.description_weight,
                    "process": self.process_weight,
                    "capability": self.capability_weight,
                },
                "apps_with_context": sum(1 for ctx in app_context.values() if ctx),
            },
        )

    def calculate_similarity(
        self, app1: Any, app2: Any, context1: Optional[Dict] = None, context2: Optional[Dict] = None
    ) -> SimilarityResult:
        """
        Calculate similarity using multiple dimensions including business context.
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

        # Business context similarities
        process_sim = 0.0
        capability_sim = 0.0

        if context1 and context2:
            process_sim = self._calculate_process_overlap(
                context1.get("processes", []), context2.get("processes", [])
            )
            capability_sim = self._calculate_capability_overlap(
                context1.get("capabilities", []), context2.get("capabilities", [])
            )

        # Calculate weighted overall score
        if context1 and context2 and (context1.get("processes") or context1.get("capabilities")):
            # Use full weights when business context available
            overall = (
                name_sim * self.name_weight
                + desc_sim * self.description_weight
                + vendor_sim * self.vendor_weight
                + process_sim * self.process_weight
                + capability_sim * self.capability_weight
            )
        else:
            # Fall back to name/description only
            overall = (name_sim * 0.70) + (desc_sim * 0.25) + (vendor_sim * 0.05)

        match_type = "exact" if overall >= 0.95 else "fuzzy"

        return SimilarityResult(
            overall_score=overall,
            name_similarity=name_sim,
            description_similarity=desc_sim,
            vendor_similarity=vendor_sim,
            match_type=match_type,
            details={
                "process_overlap": process_sim,
                "capability_overlap": capability_sim,
                "has_business_context": bool(context1 and context2),
            },
        )

    def _build_application_context(self, applications: List[Any]) -> Dict[int, Dict]:
        """
        Pre-build business context for all applications.
        """
        context = {}

        for app in applications:
            app_context = {"processes": [], "capabilities": []}

            # Get business processes
            try:
                if hasattr(app, "business_processes"):
                    app_context["processes"] = [
                        {"id": p.id, "name": getattr(p, "name", "")} for p in app.business_processes
                    ]
            except Exception as e:
                logger.debug(f"Could not get processes for app {app.id}: {e}")

            # Get capabilities
            try:
                if hasattr(app, "capabilities"):
                    app_context["capabilities"] = [
                        {"id": c.id, "name": getattr(c, "name", "")} for c in app.capabilities
                    ]
                elif hasattr(app, "business_capabilities"):
                    app_context["capabilities"] = [
                        {"id": c.id, "name": getattr(c, "name", "")}
                        for c in app.business_capabilities
                    ]
            except Exception as e:
                logger.debug(f"Could not get capabilities for app {app.id}: {e}")

            context[app.id] = app_context

        return context

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate name similarity"""
        if not name1 or not name2:
            return 0.0

        norm1 = self._normalize_name(name1)
        norm2 = self._normalize_name(name2)

        if not norm1 or not norm2:
            return 0.0

        if norm1 == norm2:
            return 1.0

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

        # Acronym expansion
        acronym_boost = 0.0
        for exp1 in self._expand_acronyms(norm1):
            for exp2 in self._expand_acronyms(norm2):
                if exp1 == exp2:
                    acronym_boost = 0.2
                    break

        return min(1.0, max(seq_ratio, jaccard) + acronym_boost)

    def _calculate_description_similarity(self, desc1: str, desc2: str) -> float:
        """Calculate description similarity"""
        if not desc1 or not desc2:
            return 0.0

        norm1 = self._normalize_name(desc1)
        norm2 = self._normalize_name(desc2)

        if not norm1 or not norm2:
            return 0.0

        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())

        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
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
            "that",
            "this",
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

    def _calculate_process_overlap(self, processes1: List[Dict], processes2: List[Dict]) -> float:
        """Calculate business process overlap"""
        if not processes1 or not processes2:
            return 0.0

        ids1 = set(p["id"] for p in processes1)
        ids2 = set(p["id"] for p in processes2)

        if not ids1 or not ids2:
            return 0.0

        overlap = len(ids1 & ids2)
        union = len(ids1 | ids2)

        return overlap / union if union > 0 else 0.0

    def _calculate_capability_overlap(
        self, capabilities1: List[Dict], capabilities2: List[Dict]
    ) -> float:
        """Calculate capability overlap"""
        if not capabilities1 or not capabilities2:
            return 0.0

        ids1 = set(c["id"] for c in capabilities1)
        ids2 = set(c["id"] for c in capabilities2)

        if not ids1 or not ids2:
            return 0.0

        overlap = len(ids1 & ids2)
        union = len(ids1 | ids2)

        return overlap / union if union > 0 else 0.0
