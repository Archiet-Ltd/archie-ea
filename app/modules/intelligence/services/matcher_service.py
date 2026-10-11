"""One matcher for every element or vendor arriving from an import or connector.

Identifier first through the crosswalk, then normalised name. A certain match
updates in place; an uncertain match becomes an approval row with evidence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app import db
from app.middleware.tenant_context import current_org_id
from app.models.archimate_core import ArchiMateElement
from app.models.ai_chat_crud_approval import AIChatCRUDApproval
from app.modules.intelligence.services.crosswalk_service import CrosswalkService

logger = logging.getLogger(__name__)

# Thresholds for match certainty.
# Certain = identifier hit, or identical normalised name and type with matching
# key attributes. 1.0 is required for certain name-based matching because
# anything less is a near-duplicate that needs human review.
CERTAIN_NAME_SIMILARITY = 1.0

# Threshold for fuzzy near-duplicate detection (uncertain matches).
# Matches the drift detector's NEAR_DUPLICATE_THRESHOLD (0.6) so the matcher
# and the genome pipeline flag the same candidates.
NEAR_DUPLICATE_THRESHOLD = 0.6

# Expiry for a near-duplicate approval proposal (in minutes).
PROPOSAL_EXPIRY_MINUTES = 60


@dataclass
class MatchResult:
    """Outcome of matching one incoming record against the organisation's data."""

    certain: bool
    """True when the match is authoritative enough to update in place."""

    matched_element_id: Optional[int] = None
    """The internal element id of the matched record, if any."""

    matched_name: Optional[str] = None
    """The name of the matched record, for evidence."""

    matched_type: Optional[str] = None
    """The type of the matched record."""

    evidence: Optional[Dict[str, Any]] = None
    """Payload for the approval row when the match is uncertain."""

    approval_id: Optional[int] = None
    """The id of the created approval row, if uncertain."""

    match_method: str = "none"
    """How the match was found: crosswalk, name, or none."""


def _normalise_name(name: str) -> str:
    """Casefold and collapse whitespace, matching duplicate_guard."""
    if not name:
        return ""
    return " ".join(str(name).split()).casefold()


def _compute_blocking_key(name: str) -> str:
    """First token of the normalised name, for candidate grouping.

    Mirrors compute_blocking_key in drift_detector.py so the matcher and the
    genome pipeline use the same grouping key.
    """
    if not name or not name.strip():
        return ""
    return (name.strip().split()[0] or "").lower()


class MatcherService:
    """One matcher for elements and vendors arriving from imports or connectors.

    Resolution order:
      1. Identifier crosswalk (source_system + external_id).
      2. Normalised name + type match.

    A certain match (identifier hit, or identical normalised name and type)
    returns the matched element id. An uncertain match creates an approval row
    with evidence and returns its id.

    The candidate set is always the acting organisation's own records — the
    matcher never crosses organisations.
    """

    # Thresholds — named constants with rationale.
    # 1.0 (exact normalised match) is required for certain name-based matching
    # because anything less is a near-duplicate that needs human review.
    CERTAIN_NAME_SIMILARITY = CERTAIN_NAME_SIMILARITY

    @classmethod
    def _require_org_id(cls, org_id: Optional[int] = None) -> int:
        resolved = org_id if org_id is not None else current_org_id()
        if resolved is None:
            raise RuntimeError("matcher operations require an organisation context")
        return resolved

    @classmethod
    def match_by_identifier(
        cls,
        source_system: str,
        external_id: str,
        *,
        org_id: Optional[int] = None,
    ) -> MatchResult:
        """Match an incoming record by its external identifier.

        Returns a certain match when the crosswalk has a row for this
        (organisation, source_system, external_id) triple.
        """
        org_id = cls._require_org_id(org_id)
        link = CrosswalkService.get_link_by_external_id(
            source_system, external_id, org_id=org_id,
        )
        if link is None:
            return MatchResult(certain=False, match_method="none")

        element = db.session.get(ArchiMateElement, link.element_id)
        return MatchResult(
            certain=True,
            matched_element_id=link.element_id,
            matched_name=element.name if element else None,
            matched_type=element.type if element else None,
            match_method="crosswalk",
        )

    @classmethod
    def _find_name_candidates(
        cls,
        name: str,
        type_name: Optional[str],
        *,
        org_id: int,
    ) -> List[ArchiMateElement]:
        """Find candidate elements with the same normalised name and type.

        Uses the same blocking-key strategy as the drift detector: group by
        first token, then compare normalised names exactly.
        """
        norm = _normalise_name(name)
        if not norm:
            return []

        blocking_key = _compute_blocking_key(name)
        if not blocking_key:
            return []

        # Load all elements in this org that share the blocking key.
        candidates = (
            db.session.query(ArchiMateElement)
            .filter(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.deleted_at.is_(None),
                ArchiMateElement.name.ilike(f"{blocking_key}%"),
            )
            .all()
        )

        # Filter by exact normalised name match.
        matched = []
        for elem in candidates:
            if _normalise_name(elem.name or "") == norm:
                if type_name and elem.type:
                    if _normalise_name(elem.type) != _normalise_name(type_name):
                        continue
                matched.append(elem)
        return matched

    @classmethod
    def _find_near_duplicate_candidates(
        cls,
        name: str,
        type_name: Optional[str],
        *,
        org_id: int,
    ) -> List[ArchiMateElement]:
        """Find near-duplicate candidates using fuzzy name matching.

        Uses the same fuzzy path and threshold as the drift detector's
        _detect_near_duplicates. Returns candidates ordered by similarity
        descending.
        """
        from app.modules.duplicate_detection.services.duplicate_detection_utils import (
            DuplicateDetectionUtils,
        )

        norm = _normalise_name(name)
        if not norm:
            return []

        blocking_key = _compute_blocking_key(name)
        if not blocking_key:
            return []

        candidates = (
            db.session.query(ArchiMateElement)
            .filter(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.deleted_at.is_(None),
                ArchiMateElement.name.ilike(f"{blocking_key}%"),
            )
            .all()
        )

        scored = []
        for elem in candidates:
            if _normalise_name(elem.name or "") == norm:
                continue  # exact match handled elsewhere
            if type_name and elem.type:
                if _normalise_name(elem.type) != _normalise_name(type_name):
                    continue
            is_near, score = DuplicateDetectionUtils.is_duplicate(
                name, elem.name, mode="fuzzy", threshold=NEAR_DUPLICATE_THRESHOLD,
            )
            if is_near:
                scored.append((score, elem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [elem for _, elem in scored]

    @classmethod
    def match_by_name(
        cls,
        name: str,
        type_name: Optional[str] = None,
        *,
        org_id: Optional[int] = None,
    ) -> MatchResult:
        """Match an incoming record by its normalised name and type.

        Resolution:
          1. Exact normalised name + type match → certain.
          2. Fuzzy near-duplicate match → uncertain (with evidence).

        Everything else is returned as no match.
        """
        org_id = cls._require_org_id(org_id)

        # Step 1: Exact normalised name match (certain).
        candidates = cls._find_name_candidates(name, type_name, org_id=org_id)
        if candidates:
            if len(candidates) == 1:
                elem = candidates[0]
                return MatchResult(
                    certain=True,
                    matched_element_id=elem.id,
                    matched_name=elem.name,
                    matched_type=elem.type,
                    match_method="name",
                )
            # Multiple exact matches — uncertain.
            elem = candidates[0]
            return MatchResult(
                certain=False,
                matched_element_id=elem.id,
                matched_name=elem.name,
                matched_type=elem.type,
                evidence={
                    "match_method": "name",
                    "candidate_count": len(candidates),
                    "candidate_ids": [e.id for e in candidates],
                    "candidate_names": [e.name for e in candidates],
                },
                match_method="name",
            )

        # Step 2: Fuzzy near-duplicate match (uncertain).
        near_dups = cls._find_near_duplicate_candidates(name, type_name, org_id=org_id)
        if near_dups:
            elem = near_dups[0]
            return MatchResult(
                certain=False,
                matched_element_id=elem.id,
                matched_name=elem.name,
                matched_type=elem.type,
                evidence={
                    "match_method": "fuzzy_name",
                    "candidate_count": len(near_dups),
                    "candidate_ids": [e.id for e in near_dups],
                    "candidate_names": [e.name for e in near_dups],
                },
                match_method="fuzzy_name",
            )

        return MatchResult(certain=False, match_method="none")

    @classmethod
    def match(
        cls,
        *,
        source_system: Optional[str] = None,
        external_id: Optional[str] = None,
        name: Optional[str] = None,
        type_name: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> MatchResult:
        """Match an incoming record, identifier first then name.

        Resolution order:
          1. If source_system and external_id are provided, try the crosswalk.
          2. If no crosswalk match and name is provided, try normalised name.

        Returns a MatchResult with certain=True for authoritative matches
        and certain=False for uncertain ones (caller should create an approval).
        """
        org_id = cls._require_org_id(org_id)

        # Step 1: Identifier crosswalk.
        if source_system and external_id:
            result = cls.match_by_identifier(source_system, external_id, org_id=org_id)
            if result.certain:
                return result

        # Step 2: Normalised name.
        if name:
            result = cls.match_by_name(name, type_name, org_id=org_id)
            if result.certain:
                return result
            if result.matched_element_id is not None:
                # Uncertain name match — return it so the caller can create an approval.
                return result

        return MatchResult(certain=False, match_method="none")

    @classmethod
    def propose_match(
        cls,
        *,
        source_system: Optional[str] = None,
        external_id: Optional[str] = None,
        name: Optional[str] = None,
        type_name: Optional[str] = None,
        entity_type: str = "element",
        org_id: Optional[int] = None,
    ) -> MatchResult:
        """Match and, if uncertain, create an approval row with evidence.

        This is the main entry point for imports and connectors. It runs the
        matcher and:
        - Returns the certain match directly.
        - For an uncertain match, creates a PENDING approval row with the
          evidence payload and returns its id.

        Never merges silently — an uncertain match always goes through the
        approval queue.
        """
        org_id = cls._require_org_id(org_id)
        result = cls.match(
            source_system=source_system,
            external_id=external_id,
            name=name,
            type_name=type_name,
            org_id=org_id,
        )

        if result.certain:
            return result

        # Uncertain match — create an approval row.
        if result.matched_element_id is not None:
            evidence = result.evidence or {}
            evidence.update({
                "incoming_name": name,
                "incoming_type": type_name,
                "incoming_source_system": source_system,
                "incoming_external_id": external_id,
                "matched_element_id": result.matched_element_id,
                "matched_name": result.matched_name,
                "matched_type": result.matched_type,
                "match_method": result.match_method,
            })
            summary = (
                f"Near-duplicate {entity_type}: incoming '{name}' "
                f"matches existing '{result.matched_name}' "
                f"(type: {result.matched_type or 'unknown'})"
            )
        else:
            evidence = {
                "incoming_name": name,
                "incoming_type": type_name,
                "incoming_source_system": source_system,
                "incoming_external_id": external_id,
                "match_method": "none",
            }
            summary = (
                f"New {entity_type}: '{name or 'unnamed'}' "
                f"has no match in the organisation"
            )

        approval = AIChatCRUDApproval(
            organization_id=org_id,
            operation_type="merge" if result.matched_element_id is not None else "create",
            entity_type=entity_type,
            original_command=f"system: matcher proposed {entity_type}",
            operation_payload=json.dumps(evidence),
            summary=summary,
            source_table="matcher_service",
            source_id=result.matched_element_id,
            expires_at=datetime.utcnow() + timedelta(minutes=PROPOSAL_EXPIRY_MINUTES),
        )
        db.session.add(approval)
        db.session.flush()

        result.approval_id = approval.id
        result.evidence = evidence
        logger.info(
            "Created approval %s for uncertain %s match: %s",
            approval.id, entity_type, summary,
        )
        return result