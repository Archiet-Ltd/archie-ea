"""BA-001: AI-infer capability-to-application mapping.

Populates unified_application_capability_mapping from 881 ApplicationComponents
and 273 UnifiedCapabilities using keyword similarity matching.
"""
import re
from typing import Dict
from datetime import datetime

from app import db
from app.models.unified_capability import UnifiedCapability
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

# Column availability flags (graceful degradation if DB lacks columns)
_HAS_CONFIDENCE_SCORE = hasattr(UnifiedApplicationCapabilityMapping, "confidence_score")
_HAS_INFERENCE_REASONING = hasattr(UnifiedApplicationCapabilityMapping, "inference_reasoning")


class CapabilityMappingInferenceService:
    """Infers capability-to-application mappings using name/description similarity.

    Uses keyword overlap scoring (no LLM dependency — pure ORM + string matching)
    to produce confidence scores and reasoning for each mapping.
    """

    MIN_CONFIDENCE = 0.3
    MAX_MAPPINGS_PER_APP = 5

    def infer_mappings(self, dry_run: bool = False) -> Dict:
        """Infer and persist capability→application mappings.

        Args:
            dry_run: If True, compute but do not persist to DB.

        Returns:
            dict with keys: inserted, skipped, total_processed
        """
        apps = (
            db.session.query(ApplicationComponent)
            .filter(ApplicationComponent.name.isnot(None))
            .limit(200)
            .all()
        )

        capabilities = (
            db.session.query(UnifiedCapability).filter(UnifiedCapability.retired_into_id.is_(None))
            .filter(UnifiedCapability.name.isnot(None))
            .all()
        )

        if not apps or not capabilities:
            return {"inserted": 0, "skipped": 0, "total_processed": 0, "error": "No data"}

        # Build capability keyword index
        cap_keywords = {}
        for cap in capabilities:
            words = self._extract_keywords(cap.name, getattr(cap, "description", None))
            cap_keywords[cap.id] = (cap, words)

        # Existing mappings to avoid duplicates
        existing = {
            (m.application_component_id, m.unified_capability_id)
            for m in db.session.query(
                UnifiedApplicationCapabilityMapping.application_component_id,
                UnifiedApplicationCapabilityMapping.unified_capability_id,
            ).all()
        }

        inserted = 0
        skipped = 0

        for app in apps:
            app_words = self._extract_keywords(
                app.name, getattr(app, "description", None)
            )
            if not app_words:
                continue

            scores = []
            for cap_id, (cap, cap_words) in cap_keywords.items():
                if not cap_words:
                    continue
                score, reasoning = self._score_match(app, app_words, cap, cap_words)
                if score >= self.MIN_CONFIDENCE:
                    scores.append((score, reasoning, cap_id))

            # Take top N matches per app
            scores.sort(key=lambda x: x[0], reverse=True)
            for score, reasoning, cap_id in scores[: self.MAX_MAPPINGS_PER_APP]:
                key = (app.id, cap_id)
                if key in existing:
                    skipped += 1
                    continue
                if not dry_run:
                    mapping = UnifiedApplicationCapabilityMapping(
                        application_component_id=app.id,
                        unified_capability_id=cap_id,
                        support_level="partial",
                        is_active=True,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    # Graceful degradation: set inference columns only if model has them
                    if _HAS_CONFIDENCE_SCORE:
                        mapping.confidence_score = round(score, 4)
                    if _HAS_INFERENCE_REASONING:
                        mapping.inference_reasoning = reasoning
                    else:
                        # Fall back to notes column (always present)
                        mapping.notes = reasoning[:500]
                    db.session.add(mapping)
                    existing.add(key)
                inserted += 1

        if not dry_run:
            db.session.commit()

        return {
            "inserted": inserted,
            "skipped": skipped,
            "total_processed": len(apps),
        }

    def _extract_keywords(self, name: str, description: str = None) -> set:
        """Extract meaningful keywords from name and description."""
        text = " ".join(filter(None, [name or "", description or ""]))
        text = text.lower()
        words = re.findall(r"\b[a-z]{3,}\b", text)
        stopwords = {
            "the", "and", "for", "are", "with", "that", "this", "from", "have",
            "has", "will", "can", "not", "but", "all", "any", "its", "our", "was",
            "been", "their", "they", "also", "into", "more", "than", "when",
            "which", "would", "each", "then", "there", "system", "application",
            "module", "service", "management", "based", "provide", "support",
        }
        return {w for w in words if w not in stopwords}

    def _score_match(self, app, app_words: set, cap, cap_words: set):
        """Compute similarity score and generate reasoning text."""
        intersection = app_words & cap_words
        if not intersection:
            return 0.0, ""

        union = app_words | cap_words
        jaccard = len(intersection) / len(union)

        cap_name_words = self._extract_keywords(cap.name)
        app_name_words = self._extract_keywords(app.name)
        name_overlap = cap_name_words & app_name_words
        boost = min(len(name_overlap) * 0.1, 0.3)

        score = min(jaccard + boost, 1.0)

        matching = sorted(intersection)[:5]
        reasoning = (
            f"App '{app.name}' matched capability '{cap.name}' "
            f"via keywords: {', '.join(matching)}. "
            f"Jaccard similarity: {jaccard:.3f}. "
            f"Source: name/description keyword analysis."
        )
        return score, reasoning
