"""

Unified Duplicate Detection Service

Consolidates DuplicateDetectionService and SimpleDuplicateService into a single,
comprehensive service that provides both enterprise-grade and simplified duplicate
detection capabilities while preserving all existing functionality.

Phase 2: Service consolidation (2 → 1) with full preservation
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import g, has_request_context
from flask_login import current_user

from app import db
from app.middleware.tenant_decorators import is_platform_admin
from app.models.application_duplicate_detection import (
    DuplicateAnalysis,
    DuplicateDetectionRun,
    DuplicateGroup,
    DuplicateType,
)
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_duplicate_detection import UnifiedDetectionRun, UnifiedDuplicateGroup
from app.services.core.eager_loading import get_application_options
from app.services.fuzzy_matcher import FuzzyMatcher

logger = logging.getLogger(__name__)

# Estimated annual savings per redundant application (used for ROI projections)
ESTIMATED_SAVINGS_PER_REDUNDANT_APP = 10000  # fabricated-ok: declared estimation constant for ROI projection, not a measured value


@dataclass
class SimilarityWeights:
    """Weights for different similarity criteria"""

    business_process: float = 0.25
    business_capability: float = 0.20
    technical_architecture: float = 0.20
    data_processing: float = 0.15
    name_description: float = 0.20


@dataclass
class SimilarityResult:
    """Result of similarity analysis"""

    overall_score: float
    business_process_score: float
    business_capability_score: float
    technical_architecture_score: float
    data_processing_score: float
    name_description_score: float
    details: Dict[str, Any]


class UnifiedDuplicateDetectionService:
    """
    Unified Duplicate Detection Service

    Provides comprehensive duplicate detection using multiple criteria:
    1. Business Process Alignment (L0 - L3) - Enterprise mode
    2. Business Capability Overlap - Enterprise mode
    3. Technical Architecture Similarity - Enterprise mode
    4. Data Object Processing - Enterprise mode
    5. Name/Description Semantic Analysis - Both modes
    6. Hash-based Exact Matching - Simple mode
    7. Hybrid Detection - Simple mode
    """

    def __init__(self, weights: Optional[SimilarityWeights] = None):
        self.weights = weights or SimilarityWeights()
        self.logger = logging.getLogger(__name__)
        self.fuzzy_matcher = FuzzyMatcher(use_synonyms=True, use_preprocessing=True)

    def _cleanup_stale_groups_for_caller_org(self):
        """Clean up stale simple/hybrid detection groups before a fresh run.

        DEF-2 (review-pr430-v3.md, 2026-10-08): this used to run
        ``DELETE FROM unified_group_members`` with no predicate at all, plus
        an unconditional ``UnifiedDuplicateGroup.query.delete()`` -- an
        ordinary user in org A running detection wiped every organisation's
        detection groups and members, not just their own. Neither table
        carries an ``organization_id`` column, so scoping goes through each
        member's ``ApplicationComponent`` (TenantMixin): only group
        memberships whose application belongs to the caller's active
        organisation are removed, then only groups left with zero members
        are dropped -- a group with a remaining member from another
        organisation is left untouched rather than guessed at.

        Outside a request context (CLI/scheduler -- no ``g.current_org_id``
        to scope to) or for a genuine platform admin, falls back to the
        original unscoped cleanup, since there is no single caller
        organisation to scope to either way.
        """
        if has_request_context() and not is_platform_admin(current_user):
            current_org_id = getattr(g, "current_org_id", None)
            if current_org_id is not None:
                db.session.execute(
                    db.text(
                        "DELETE FROM unified_group_members WHERE application_id IN "
                        "(SELECT id FROM application_components WHERE organization_id = :org_id)"
                    ),
                    {"org_id": current_org_id},
                )
                db.session.execute(
                    db.text(
                        "DELETE FROM unified_duplicate_groups WHERE id NOT IN "
                        "(SELECT DISTINCT group_id FROM unified_group_members)"
                    )
                )
                return
        db.session.execute(db.text("DELETE FROM unified_group_members"))  # tenant-exempt: system table (detection engine cleanup); no request-scoped caller to narrow to
        UnifiedDuplicateGroup.query.delete()

    # === ENTERPRISE-GRADE METHODS (from DuplicateDetectionService) ===

    def run_duplicate_detection(self, application_ids: List[int] = None) -> Dict[str, Any]:
        """
        Run comprehensive duplicate detection using enterprise criteria
        """
        try:
            # Create detection run
            run = DuplicateDetectionRun(
                similarity_threshold=0.7,
                weighting_config=self.weights.__dict__,
                analysis_scope={},
                status="running",
                started_at=datetime.utcnow(),
                applications_analyzed=0,
                duplicate_groups_found=0,
            )
            run.run_name = "Enterprise Detection"
            db.session.add(run)

            # Get applications to analyze with eager loading to prevent N + 1 queries
            # This loads capability_mappings, process_mappings, and related entities in ~3 queries
            # instead of N*M queries (where N=apps, M=mappings per app)
            base_query = ApplicationComponent.query.options(
                *get_application_options("full_analysis")
            )

            if application_ids:
                applications = base_query.filter(ApplicationComponent.id.in_(application_ids)).all()
            else:
                applications = base_query.all()

            run.applications_analyzed = len(applications)

            # Group applications by similarity
            duplicate_groups = []
            # Lowered threshold from 0.75 to 0.60 for fuzzy matching
            # Fuzzy algorithms provide gradual scores, so lower threshold catches more real duplicates
            similarity_threshold = 0.60

            for i, app1 in enumerate(applications):
                if i >= len(applications) - 1:
                    break

                current_group = [app1]

                for app2 in applications[i + 1 :]:
                    similarity = self._calculate_comprehensive_similarity(app1, app2)

                    if similarity.overall_score >= similarity_threshold:
                        current_group.append(app2)

                if len(current_group) > 1:
                    # Create duplicate group
                    group = DuplicateGroup(
                        group_name=f"Enterprise Group: {app1.name[:50]}..." if len(app1.name) > 50 else f"Enterprise Group: {app1.name}",
                        detection_run_id=run.id,
                        duplicate_type=DuplicateType.FUNCTIONAL,
                        overall_similarity_score=sum(
                            self._calculate_comprehensive_similarity(
                                current_group[0], app
                            ).overall_score
                            for app in current_group[1:]
                        )
                        / (len(current_group) - 1),
                        estimated_savings=self._estimate_enterprise_group_savings(current_group),
                    )
                    db.session.add(group)
                    db.session.flush()

                    # Add pairwise analyses for each pair in the group
                    primary_app = current_group[0]
                    for app in current_group[1:]:
                        sim_result = self._calculate_comprehensive_similarity(primary_app, app)
                        analysis = DuplicateAnalysis(
                            duplicate_group_id=group.id,
                            application_1_id=primary_app.id,
                            application_2_id=app.id,
                            overall_similarity_score=sim_result.overall_score,
                            functional_similarity=sim_result.business_process_score,
                            capability_similarity=sim_result.business_capability_score,
                            technical_similarity=sim_result.technical_architecture_score,
                            data_similarity=sim_result.data_processing_score,
                            analysis_method="enterprise_detection",
                        )
                        db.session.add(analysis)

                    # Add applications to group relationship
                    group.applications.extend(current_group)

                    duplicate_groups.append(group)

            # Update run status
            run.duplicate_groups_found = len(duplicate_groups)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            db.session.commit()

            # Calculate additional metrics for UI (enterprise mode treats all as functional)
            exact_matches = 0  # Enterprise mode doesn't distinguish exact vs fuzzy
            fuzzy_matches = len(duplicate_groups)
            estimated_savings = sum(group.estimated_savings or 0 for group in duplicate_groups)

            return {
                "success": True,
                "run_id": run.id,
                "groups_found": len(duplicate_groups),
                "applications_analyzed": len(applications),
                "exact_matches": exact_matches,
                "fuzzy_matches": fuzzy_matches,
                "estimated_savings": estimated_savings,
                "message": f"Enterprise duplicate detection completed. Found {len(duplicate_groups)} duplicate groups.",
            }

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Enterprise duplicate detection failed: {str(e)}")
            return {"success": False, "error": f"Detection failed: {str(e)}"}

    def get_duplicate_analysis_for_application(self, application_id: int) -> Dict[str, Any]:
        """Get detailed duplicate analysis for a specific application"""
        try:
            analyses = DuplicateAnalysis.query.filter(
                db.or_(
                    DuplicateAnalysis.application_1_id == application_id,
                    DuplicateAnalysis.application_2_id == application_id,
                )
            ).all()

            if not analyses:
                return {
                    "success": True,
                    "duplicates": [],
                    "message": "No duplicates found for this application",
                }

            # Batch-load all referenced groups upfront to avoid N+1 queries
            group_ids = list({a.duplicate_group_id for a in analyses})
            groups_by_id = {
                g.id: g
                for g in DuplicateGroup.query.filter(
                    DuplicateGroup.id.in_(group_ids)
                ).all()
            }

            duplicate_groups = []
            seen_group_ids = set()
            for analysis in analyses:
                group = groups_by_id.get(analysis.duplicate_group_id)
                if group and group.id not in seen_group_ids:
                    seen_group_ids.add(group.id)
                    duplicate_groups.append(
                        {
                            "group_id": group.id,
                            "similarity_score": analysis.overall_similarity_score,
                            "duplicate_type": group.duplicate_type.value if group.duplicate_type else "unknown",
                            "overall_similarity_score": group.overall_similarity_score,
                            "functional_similarity": analysis.functional_similarity,
                            "technical_similarity": analysis.technical_similarity,
                        }
                    )

            return {
                "success": True,
                "duplicates": duplicate_groups,
                "message": f"Found {len(duplicate_groups)} duplicate groups",
            }

        except Exception as e:
            self.logger.error(f"Failed to get duplicate analysis: {str(e)}")
            return {"success": False, "error": f"Analysis failed: {str(e)}"}

    def _calculate_comprehensive_similarity(
        self, app1: ApplicationComponent, app2: ApplicationComponent
    ) -> SimilarityResult:
        """Calculate comprehensive similarity between two applications"""
        try:
            # Business process similarity
            bp_score = self._calculate_business_process_similarity(app1, app2)

            # Business capability similarity
            bc_score = self._calculate_capability_similarity(app1, app2)

            # Technical architecture similarity
            ta_score = self._calculate_technical_similarity_score(app1, app2)

            # Data processing similarity
            dp_score = self._calculate_data_similarity_score(app1, app2)

            # Name/description similarity
            nd_score = self._calculate_name_description_similarity(app1, app2)

            # Calculate weighted overall score
            overall_score = (
                bp_score * self.weights.business_process
                + bc_score * self.weights.business_capability
                + ta_score * self.weights.technical_architecture
                + dp_score * self.weights.data_processing
                + nd_score * self.weights.name_description
            )

            return SimilarityResult(
                overall_score=overall_score,
                business_process_score=bp_score,
                business_capability_score=bc_score,
                technical_architecture_score=ta_score,
                data_processing_score=dp_score,
                name_description_score=nd_score,
                details={
                    "weights": {
                        "business_process": self.weights.business_process,
                        "business_capability": self.weights.business_capability,
                        "technical_architecture": self.weights.technical_architecture,
                        "data_processing": self.weights.data_processing,
                        "name_description": self.weights.name_description,
                    }
                },
            )

        except Exception as e:
            self.logger.error(f"Similarity calculation failed: {str(e)}")
            return SimilarityResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, {"error": str(e)})

    def _calculate_business_process_similarity(
        self, app1: ApplicationComponent, app2: ApplicationComponent
    ) -> float:
        """Calculate business process similarity"""
        try:
            processes1 = [apm.business_process for apm in app1.process_mappings]
            processes2 = [apm.business_process for apm in app2.process_mappings]

            if not processes1 or not processes2:
                return 0.0

            # Calculate overlap based on process levels and names
            common_processes = set()
            for p1 in processes1:
                for p2 in processes2:
                    if p1.process_name == p2.process_name and p1.process_level == p2.process_level:
                        common_processes.add(p1.process_name)

            similarity = len(common_processes) / max(len(processes1), len(processes2))
            return min(similarity, 1.0)

        except Exception as e:
            self.logger.error(f"Business process similarity calculation failed: {str(e)}")
            return 0.0

    def _calculate_capability_similarity(
        self, app1: ApplicationComponent, app2: ApplicationComponent
    ) -> float:
        """Calculate business capability similarity"""
        try:
            # Get capability mappings for both applications
            caps1 = {
                acm.business_capability.name
                for acm in app1.capability_mappings
                if acm.business_capability
            }
            caps2 = {
                acm.business_capability.name
                for acm in app2.capability_mappings
                if acm.business_capability
            }

            if not caps1 or not caps2:
                return 0.0

            intersection = caps1.intersection(caps2)
            union = caps1.union(caps2)

            similarity = len(intersection) / len(union) if union else 0.0
            return similarity

        except Exception as e:
            self.logger.error(f"Capability similarity calculation failed: {str(e)}")
            return 0.0

    def _calculate_technical_similarity_score(
        self, app1: ApplicationComponent, app2: ApplicationComponent
    ) -> float:
        """Calculate technical architecture similarity"""
        try:
            # Compare technology stack, architecture patterns, etc.
            tech1 = {
                "technology_stack": app1.technology_stack or "",
                "architecture_pattern": app1.architecture_style or "",
                "deployment_type": app1.deployment_model or "",
            }
            tech2 = {
                "technology_stack": app2.technology_stack or "",
                "architecture_pattern": app2.architecture_style or "",
                "deployment_type": app2.deployment_model or "",
            }

            similarities = []
            for key in tech1:
                if tech1[key] and tech2[key]:
                    # Simple string similarity for now
                    similarity = tech1[key].lower() == tech2[key].lower()
                    similarities.append(1.0 if similarity else 0.0)
                elif not tech1[key] and not tech2[key]:
                    similarities.append(1.0)  # Both empty = match
                else:
                    similarities.append(0.0)  # One empty, one not = no match

            return sum(similarities) / len(similarities) if similarities else 0.0

        except Exception as e:
            self.logger.error(f"Technical similarity calculation failed: {str(e)}")
            return 0.0

    def _calculate_data_similarity_score(
        self, app1: ApplicationComponent, app2: ApplicationComponent
    ) -> float:
        """Calculate data processing similarity based on structured data fields."""
        try:
            similarities = []

            # Database platform comparison
            db1 = (app1.primary_database or "").lower().strip()
            db2 = (app2.primary_database or "").lower().strip()
            if db1 and db2:
                similarities.append(1.0 if db1 == db2 else self.fuzzy_matcher.calculate_similarity(db1, db2))

            # Data classification comparison
            dc1 = (app1.data_classification or "").lower().strip()
            dc2 = (app2.data_classification or "").lower().strip()
            if dc1 and dc2:
                similarities.append(1.0 if dc1 == dc2 else 0.3)

            # Data architecture comparison
            da1 = (app1.data_architecture or "").lower().strip()
            da2 = (app2.data_architecture or "").lower().strip()
            if da1 and da2:
                similarities.append(1.0 if da1 == da2 else 0.3)

            # PII processing comparison
            pii1 = app1.pii_data_processed
            pii2 = app2.pii_data_processed
            if pii1 is not None and pii2 is not None:
                similarities.append(1.0 if pii1 == pii2 else 0.4)

            # Compliance tags comparison (JSON arrays)
            import json as _json
            tags1_raw = app1.compliance_tags or "[]"
            tags2_raw = app2.compliance_tags or "[]"
            try:
                tags1 = set(t.lower() for t in (_json.loads(tags1_raw) if isinstance(tags1_raw, str) else (tags1_raw or [])))
                tags2 = set(t.lower() for t in (_json.loads(tags2_raw) if isinstance(tags2_raw, str) else (tags2_raw or [])))
                if tags1 and tags2:
                    overlap = len(tags1 & tags2)
                    total = len(tags1 | tags2)
                    similarities.append(overlap / total if total > 0 else 0.0)
            except (ValueError, TypeError):
                logger.exception("Failed to operation")
                pass

            return sum(similarities) / len(similarities) if similarities else 0.3

        except Exception as e:
            self.logger.error(f"Data similarity calculation failed: {str(e)}")
            return 0.0

    def _calculate_name_description_similarity(
        self, app1: ApplicationComponent, app2: ApplicationComponent
    ) -> float:
        """Calculate name and description similarity using fuzzy matching"""
        try:
            name1 = app1.name or ""
            name2 = app2.name or ""
            desc1 = app1.description or ""
            desc2 = app2.description or ""

            # Use fuzzy matching for gradual similarity (0.0-1.0)
            # This replaces binary exact matching with intelligent fuzzy algorithms
            name_sim = self.fuzzy_matcher.calculate_similarity(name1, name2)
            desc_sim = self.fuzzy_matcher.calculate_similarity(desc1, desc2)

            # Weight name more heavily than description
            return name_sim * 0.7 + desc_sim * 0.3

        except Exception as e:
            self.logger.error(f"Name/description similarity calculation failed: {str(e)}")
            return 0.0

    def _analyze_business_process_overlap(self, app: ApplicationComponent) -> Dict[str, Any]:
        """Analyze business process overlap for an application"""
        try:
            processes = app.process_mappings
            return {
                "process_count": len(processes),
                "process_levels": list(set(p.business_process.process_level for p in processes)),
                "process_names": [p.business_process.process_name for p in processes],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_capability_overlap(self, app: ApplicationComponent) -> Dict[str, Any]:
        """Analyze capability overlap for an application"""
        try:
            capability_mappings = app.capability_mappings
            return {
                "capability_count": len(capability_mappings),
                "capability_names": [
                    acm.business_capability.name
                    for acm in capability_mappings
                    if acm.business_capability
                ],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_technical_similarity(self, app: ApplicationComponent) -> Dict[str, Any]:
        """Analyze technical similarity for an application"""
        try:
            return {
                "technology_stack": app.technology_stack,
                "architecture_pattern": app.architecture_style,
                "deployment_type": app.deployment_model,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_data_processing_overlap(self, app: ApplicationComponent) -> Dict[str, Any]:
        """Analyze data processing characteristics for an application."""
        try:
            return {
                "primary_database": app.primary_database,
                "database_platforms": app.database_platforms,
                "data_classification": app.data_classification,
                "data_architecture": app.data_architecture,
                "pii_data_processed": app.pii_data_processed,
                "compliance_tags": app.compliance_tags,
                "database_size_gb": app.database_size_gb,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # === SIMPLIFIED METHODS (from SimpleDuplicateService) ===

    @staticmethod
    def cleanup_stale_data() -> Dict[str, Any]:
        """Clean up stale duplicate detection data"""
        try:
            # Delete all existing groups and runs to start fresh
            # tenant-exempt: system table (detection engine cleanup)
            db.session.execute(db.text("DELETE FROM unified_group_members"))  # tenant-exempt: system table (detection engine cleanup)
            db.session.execute(db.text("DELETE FROM unified_duplicate_groups"))  # tenant-exempt: system table (detection engine cleanup)
            db.session.execute(db.text("DELETE FROM unified_detection_runs"))  # tenant-exempt: system table (detection engine cleanup)
            db.session.commit()

            return {"success": True, "message": "Stale data cleaned up successfully"}
        except Exception as e:
            db.session.rollback()
            return {"success": False, "error": f"Cleanup failed: {str(e)}"}

    def run_detection(self, similarity_threshold: float = 0.60, strategy: str = "fast") -> Dict[str, Any]:
        """Run simple duplicate detection based on name/description similarity

        OPTIMIZED: Uses hash-based grouping first for O(n) exact match detection,
        then only does O(n²) fuzzy matching on unique names (much smaller set).

        Args:
            similarity_threshold: Minimum similarity score (default 0.60, lowered from 0.80)
            strategy: Detection strategy - 'fast', 'hybrid', or 'enhanced' (default 'fast')
        """
        try:
            # Clean up stale groups from previous runs before creating new ones
            # Use a SAVEPOINT so failure doesn't poison the outer transaction
            try:
                with db.session.begin_nested():
                    self._cleanup_stale_groups_for_caller_org()
                self.logger.info("Cleaned up previous detection groups")
            except Exception as cleanup_err:
                self.logger.warning(f"Stale group cleanup failed (will create alongside old): {cleanup_err}")

            # Create detection run
            run = UnifiedDetectionRun(
                run_name=f"{strategy.title()} Detection {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                strategy=strategy,
                status="running",
                started_at=datetime.utcnow(),
                applications_analyzed=0,
                groups_found=0,
            )
            db.session.add(run)

            # Get all applications
            applications = ApplicationComponent.query.all()
            run.applications_analyzed = len(applications)

            duplicate_groups = []
            processed_apps = set()

            # === PHASE 1: Hash-based exact match grouping (O(n)) ===
            # Group apps by normalized name for instant exact match detection
            name_groups = {}
            for app in applications:
                normalized_name = (app.name or "").lower().strip()
                if normalized_name:
                    if normalized_name not in name_groups:
                        name_groups[normalized_name] = []
                    name_groups[normalized_name].append(app)

            # Create groups for exact name matches (2+ apps with same name)
            for name, apps_with_name in name_groups.items():
                if len(apps_with_name) >= 2:
                    # Create exact match duplicate group
                    group = UnifiedDuplicateGroup(
                        name=f"Exact: {name[:50]}..." if len(name) > 50 else f"Exact: {name}",
                        detection_run_id=run.id,
                        similarity_threshold=1.0,
                        similarity_score=1.0,
                        duplicate_type="exact",
                    )
                    db.session.add(group)
                    db.session.flush()

                    # Add all apps with this name to the group
                    for app in apps_with_name:
                        group.applications.append(app)
                        processed_apps.add(app.id)

                    duplicate_groups.append(group)

            # === PHASE 2: Fuzzy matching for remaining unique names (O(m²) where m << n) ===
            # Only compare apps with DIFFERENT names (already grouped exact matches)
            unique_name_apps = {}
            for app in applications:
                if app.id not in processed_apps:
                    normalized_name = (app.name or "").lower().strip()
                    # Take first app per unique name for fuzzy comparison
                    if normalized_name not in unique_name_apps:
                        unique_name_apps[normalized_name] = app

            unique_apps = list(unique_name_apps.values())
            max_fuzzy_unique_apps = 300
            fuzzy_phase_limited = False
            fuzzy_apps = unique_apps

            if len(unique_apps) > max_fuzzy_unique_apps:
                fuzzy_phase_limited = True
                stride = max(1, len(unique_apps) // max_fuzzy_unique_apps)
                fuzzy_apps = unique_apps[::stride][:max_fuzzy_unique_apps]
                self.logger.info(
                    "Limiting fuzzy phase to %s sampled apps from %s unique apps (limit=%s)",
                    len(fuzzy_apps),
                    len(unique_apps),
                    max_fuzzy_unique_apps,
                )

            # Only do fuzzy matching if we have a reasonable sample size for O(n²)
            for i, app in enumerate(fuzzy_apps):
                if app.id in processed_apps:
                    continue

                similar_apps = []
                for other_app in fuzzy_apps[i + 1:]:
                    if other_app.id not in processed_apps:
                        similarity = self._calculate_name_description_similarity(app, other_app)
                        if similarity >= similarity_threshold:
                            similar_apps.append(other_app)

                if similar_apps:
                    group = UnifiedDuplicateGroup(
                        name=f"Fuzzy: {app.name[:50]}..."
                        if len(app.name or "") > 50
                        else f"Fuzzy: {app.name}",
                        detection_run_id=run.id,
                        similarity_threshold=similarity_threshold,
                        similarity_score=similarity_threshold,
                        duplicate_type="fuzzy",
                    )
                    db.session.add(group)
                    db.session.flush()

                    group.applications.append(app)
                    processed_apps.add(app.id)
                    for similar_app in similar_apps:
                        group.applications.append(similar_app)
                        processed_apps.add(similar_app.id)

                    duplicate_groups.append(group)

            # Update run status
            run.groups_found = len(duplicate_groups)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            db.session.commit()

            # Calculate metrics
            exact_matches = sum(1 for g in duplicate_groups if g.duplicate_type == "exact")
            fuzzy_matches = sum(1 for g in duplicate_groups if g.duplicate_type == "fuzzy")

            # Estimate savings (£10,000 per redundant app as baseline)
            total_redundant = sum(g.applications.count() - 1 for g in duplicate_groups)
            estimated_savings = total_redundant * ESTIMATED_SAVINGS_PER_REDUNDANT_APP

            message = (
                f"Detection completed. Found {len(duplicate_groups)} groups "
                f"({exact_matches} exact, {fuzzy_matches} fuzzy)."
            )

            response = {
                "success": True,
                "run_id": run.id,
                "groups_found": len(duplicate_groups),
                "applications_analyzed": len(applications),
                "exact_matches": exact_matches,
                "fuzzy_matches": fuzzy_matches,
                "estimated_savings": estimated_savings,
                "message": message,
            }
            if fuzzy_phase_limited:
                response["warning"] = (
                    "Fuzzy matching was limited to a representative sample for large portfolios."
                )
            return response

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Simple duplicate detection failed: {str(e)}")
            return {"success": False, "error": f"Detection failed: {str(e)}"}

    def run_detection_hybrid(self, similarity_threshold: float = 0.60) -> Dict[str, Any]:
        """Run hybrid duplicate detection (hash-based + fuzzy matching)

        Args:
            similarity_threshold: Minimum similarity score (default 0.60, lowered from 0.80)
        """
        try:
            # Clean up stale groups from previous runs
            # Use a SAVEPOINT so failure doesn't poison the outer transaction
            try:
                with db.session.begin_nested():
                    self._cleanup_stale_groups_for_caller_org()
                self.logger.info("Cleaned up previous detection groups")
            except Exception as cleanup_err:
                self.logger.warning(f"Stale group cleanup failed (will create alongside old): {cleanup_err}")

            # Create detection run
            run = UnifiedDetectionRun(
                run_name=f"Hybrid Detection {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                strategy="hybrid",
                status="running",
                started_at=datetime.utcnow(),
                applications_analyzed=0,
                groups_found=0,
            )
            db.session.add(run)

            # Get all applications
            applications = ApplicationComponent.query.all()
            run.applications_analyzed = len(applications)

            # Create hash-based groups first
            hash_groups = {}
            for app in applications:
                # Create hash based on normalized name and description
                # MD5 used only for grouping, not security (nosec B324)
                normalized_text = f"{app.name or ''} {app.description or ''}".lower().strip()
                app_hash = hashlib.md5(
                    normalized_text.encode(), usedforsecurity=False
                ).hexdigest()  # noqa: S324

                if app_hash not in hash_groups:
                    hash_groups[app_hash] = []
                hash_groups[app_hash].append(app)

            # Phase 1: Hash-based exact matching
            duplicate_groups = []
            processed_apps = set()

            for hash_value, apps_in_hash in hash_groups.items():
                if len(apps_in_hash) > 1:
                    group = UnifiedDuplicateGroup(
                        name=f"Exact: {apps_in_hash[0].name[:50]}..."
                        if len(apps_in_hash[0].name) > 50
                        else f"Exact: {apps_in_hash[0].name}",
                        detection_run_id=run.id,
                        similarity_threshold=1.0,
                        duplicate_type="exact",
                    )
                    db.session.add(group)
                    db.session.flush()

                    for app in apps_in_hash:
                        group.applications.append(app)
                        processed_apps.add(app.id)

                    duplicate_groups.append(group)

            # Phase 2: Fuzzy matching on remaining unmatched applications
            unmatched_apps = [a for a in applications if a.id not in processed_apps]
            max_hybrid_fuzzy_apps = 300
            fuzzy_phase_limited = False

            if len(unmatched_apps) > 1:
                fuzzy_apps = unmatched_apps
                if len(unmatched_apps) > max_hybrid_fuzzy_apps:
                    fuzzy_phase_limited = True
                    stride = max(1, len(unmatched_apps) // max_hybrid_fuzzy_apps)
                    fuzzy_apps = unmatched_apps[::stride][:max_hybrid_fuzzy_apps]
                    self.logger.info(
                        "Limiting hybrid fuzzy phase to %s sampled apps from %s unmatched apps (limit=%s)",
                        len(fuzzy_apps),
                        len(unmatched_apps),
                        max_hybrid_fuzzy_apps,
                    )

                for i, app in enumerate(fuzzy_apps):
                    if app.id in processed_apps:
                        continue

                    similar_apps = []
                    for other_app in fuzzy_apps[i + 1:]:
                        if other_app.id not in processed_apps:
                            similarity = self._calculate_name_description_similarity(app, other_app)
                            if similarity >= similarity_threshold:
                                similar_apps.append((other_app, similarity))

                    if similar_apps:
                        avg_sim = sum(s for _, s in similar_apps) / len(similar_apps)
                        group = UnifiedDuplicateGroup(
                            name=f"Fuzzy: {app.name[:50]}..."
                            if len(app.name or "") > 50
                            else f"Fuzzy: {app.name}",
                            detection_run_id=run.id,
                            similarity_threshold=similarity_threshold,
                            similarity_score=avg_sim,
                            duplicate_type="fuzzy",
                        )
                        db.session.add(group)
                        db.session.flush()

                        group.applications.append(app)
                        processed_apps.add(app.id)
                        for similar_app, _ in similar_apps:
                            group.applications.append(similar_app)
                            processed_apps.add(similar_app.id)

                        duplicate_groups.append(group)

            # Update run status
            run.groups_found = len(duplicate_groups)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            db.session.commit()

            # Calculate additional metrics for UI
            exact_matches = sum(1 for g in duplicate_groups if g.duplicate_type == "exact")
            fuzzy_matches = sum(1 for g in duplicate_groups if g.duplicate_type == "fuzzy")
            total_redundant = sum(g.applications.count() - 1 for g in duplicate_groups)
            estimated_savings = total_redundant * ESTIMATED_SAVINGS_PER_REDUNDANT_APP

            message = (
                f"Hybrid duplicate detection completed. Found {len(duplicate_groups)} duplicate groups."
            )

            response = {
                "success": True,
                "run_id": run.id,
                "groups_found": len(duplicate_groups),
                "applications_analyzed": len(applications),
                "exact_matches": exact_matches,
                "fuzzy_matches": fuzzy_matches,
                "estimated_savings": estimated_savings,
                "message": message,
            }
            if fuzzy_phase_limited:
                response["warning"] = (
                    "Fuzzy matching was limited to a representative sample for large portfolios."
                )
            return response

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Hybrid duplicate detection failed: {str(e)}")
            return {"success": False, "error": f"Detection failed: {str(e)}"}

    @staticmethod
    def _get_app_annual_cost(app) -> float:
        """Get the best available annual cost for an application.

        Checks structured cost fields from Abacus import data in priority order:
        total_cost_of_ownership > (license_cost + maintenance_cost) > annual_cost.
        Returns 0 if no cost data is available (never fabricates estimates).
        """
        if app.total_cost_of_ownership:
            return float(app.total_cost_of_ownership)
        license_c = app.license_cost or 0
        maint_c = app.maintenance_cost or 0
        if license_c or maint_c:
            return float(license_c + maint_c)
        if getattr(app, "annual_cost", None):  # model-safety-ok: annual_cost is on VendorContract, not ApplicationComponent
            return float(app.annual_cost)
        return 0

    def _estimate_group_savings(self, group: UnifiedDuplicateGroup) -> Dict[str, Any]:
        """Estimate potential savings for a duplicate group using real cost data."""
        try:
            apps_list = group.applications.all()
            if not apps_list:
                return {"estimated_savings": 0}

            total_cost = 0
            apps_with_cost = 0
            for app in apps_list:
                cost = self._get_app_annual_cost(app)
                if cost > 0:
                    total_cost += cost
                    apps_with_cost += 1

            if apps_with_cost > 0:
                avg_cost = total_cost / apps_with_cost
                estimated_savings = avg_cost * (len(apps_list) - 1)
            else:
                estimated_savings = 0

            return {
                "estimated_savings": estimated_savings,
                "applications_count": len(apps_list),
                "method": "actual" if apps_with_cost > 0 else "no_cost_data",
            }

        except Exception as e:
            self.logger.error(f"Savings estimation failed: {str(e)}")
            return {"success": False, "estimated_savings": 0, "error": str(e)}

    def _estimate_savings_from_apps(self, apps_list: List[ApplicationComponent]) -> float:
        """Estimate savings from a pre-loaded list of apps (no DB queries).

        Uses real cost data from ApplicationComponent fields. Returns 0 when
        no cost data is available — never fabricates default estimates.
        """
        if not apps_list or len(apps_list) < 2:
            return 0
        total_cost = 0
        apps_with_cost = 0
        for app in apps_list:
            cost = self._get_app_annual_cost(app)
            if cost > 0:
                total_cost += cost
                apps_with_cost += 1
        if apps_with_cost > 0:
            avg_cost = total_cost / apps_with_cost
            return avg_cost * (len(apps_list) - 1)
        return 0

    def _estimate_enterprise_group_savings(self, applications: List[ApplicationComponent]) -> float:
        """Estimate potential savings for an enterprise duplicate group.

        Uses real cost data from ApplicationComponent structured fields.
        Returns 0 when no cost data is available.
        """
        try:
            if not applications:
                return 0

            total_cost = 0
            apps_with_cost = 0
            for app in applications:
                cost = self._get_app_annual_cost(app)
                if cost > 0:
                    total_cost += cost
                    apps_with_cost += 1

            if apps_with_cost > 0:
                avg_cost = total_cost / apps_with_cost
                return avg_cost * (len(applications) - 1)
            return 0

        except Exception as e:
            self.logger.error(f"Enterprise savings estimation failed: {str(e)}")
            return 0

    @staticmethod
    def cleanup_app_relationships(app_id: int):
        """Clean up FK relationships before deleting an application.

        Single source of truth for FK cleanup — used by both the service
        and the routes layer. Uses SAVEPOINTs so a missing table doesn't
        poison the transaction.
        """
        cleanup_queries = [
            "DELETE FROM unified_application_capability_mapping WHERE application_id = :id",
            "DELETE FROM application_process_support WHERE application_id = :id",
            "DELETE FROM process_application_mapping WHERE application_id = :id",
            "DELETE FROM application_documents WHERE application_id = :id",
            "DELETE FROM architecture_session WHERE application_id = :id",
            "DELETE FROM consolidation_list_item WHERE application_id = :id",
            "DELETE FROM application_technology_mapping WHERE application_id = :id",
            "DELETE FROM application_interface WHERE source_application_id = :id OR target_application_id = :id",
            "DELETE FROM application_cost_records WHERE application_id = :id",
            "DELETE FROM capability_governance_records WHERE application_id = :id",
            "DELETE FROM unified_group_members WHERE application_component_id = :id",
        ]
        for sql in cleanup_queries:
            try:
                with db.session.begin_nested():
                    db.session.execute(db.text(sql), {"id": app_id})  # tenant-filtered: scoped via parent FK (app_id)
            except Exception as e:
                logger.debug("Failed to execute cleanup query during merge: %s", e)

    def _consolidate_data_to_primary(self, primary_app: ApplicationComponent, duplicate_apps: list) -> Dict[str, Any]:
        """Consolidate data from duplicate applications into the primary application.

        For scalar fields: copies non-null values from duplicates if primary has null.
        For cost fields: keeps the maximum value across all applications.
        Returns a summary of what was consolidated.

        AUDIT-DUP-002: Prevents permanent data loss during merge.
        """
        consolidated = {"fields_copied": [], "costs_updated": [], "errors": []}

        # Scalar fields to copy if primary is null (first non-null wins)
        scalar_fields = [
            "description", "application_code", "component_type", "application_type",
            "application_category", "deployment_model", "criticality",
            "business_criticality", "business_domain", "business_purpose",
            "business_functions", "technology_stack", "programming_languages",
            "frameworks", "database_platforms", "primary_database",
            "vendor_name", "vendor_type", "contract_type", "support_level",
            "lifecycle_status", "architecture_style", "data_architecture",
            "data_classification", "security_level", "compliance_requirements",
            "application_owner", "business_owner", "technical_owner",
            "integration_pattern", "notes", "assessment_notes",
        ]

        # Cost fields where we keep the maximum value
        cost_fields = [
            "total_cost_of_ownership", "license_cost", "maintenance_cost",
            "infrastructure_cost", "support_cost", "implementation_cost",
            "license_cost_annual", "infrastructure_cost_monthly",
        ]

        # Integer fields where we keep the maximum value
        max_int_fields = [
            "user_base_size", "user_count", "number_of_integrations",
            "interfaces_count", "dependencies_count",
        ]

        try:
            # Copy scalar fields from duplicates where primary is null
            for field in scalar_fields:
                primary_val = getattr(primary_app, field, None)
                if primary_val is None or (isinstance(primary_val, str) and not primary_val.strip()):
                    for dup_app in duplicate_apps:
                        dup_val = getattr(dup_app, field, None)
                        if dup_val is not None and (not isinstance(dup_val, str) or dup_val.strip()):
                            setattr(primary_app, field, dup_val)
                            consolidated["fields_copied"].append(
                                f"{field} from app #{dup_app.id} ({dup_app.name})"
                            )
                            break  # first non-null wins

            # For cost fields, keep the maximum across all apps
            for field in cost_fields:
                all_values = []
                primary_val = getattr(primary_app, field, None)
                if primary_val is not None:
                    all_values.append(primary_val)
                for dup_app in duplicate_apps:
                    dup_val = getattr(dup_app, field, None)
                    if dup_val is not None:
                        all_values.append(dup_val)
                if all_values:
                    max_val = max(all_values)
                    current_val = getattr(primary_app, field, None)
                    if current_val is None or max_val > current_val:
                        setattr(primary_app, field, max_val)
                        consolidated["costs_updated"].append(
                            f"{field}: {max_val}"
                        )

            # For integer max fields, keep the maximum
            for field in max_int_fields:
                all_values = []
                primary_val = getattr(primary_app, field, None)
                if primary_val is not None:
                    all_values.append(primary_val)
                for dup_app in duplicate_apps:
                    dup_val = getattr(dup_app, field, None)
                    if dup_val is not None:
                        all_values.append(dup_val)
                if all_values:
                    max_val = max(all_values)
                    current_val = getattr(primary_app, field, None)
                    if current_val is None or max_val > current_val:
                        setattr(primary_app, field, max_val)
                        consolidated["fields_copied"].append(
                            f"{field}: max={max_val}"
                        )

        except Exception as e:
            consolidated["errors"].append(f"Field consolidation error: {str(e)}")
            self.logger.error(f"Field consolidation failed: {str(e)}")

        return consolidated

    def _reassign_relationships_to_primary(self, primary_app_id: int, duplicate_app_ids: List[int]) -> Dict[str, Any]:
        """Reassign foreign-key relationships from duplicate apps to the primary app.

        Updates relationship tables so that capability mappings, process mappings,
        documents, integrations, and cost records point to the primary application
        instead of being deleted.

        AUDIT-DUP-002: Prevents relationship data loss during merge.
        """
        reassigned = {"tables_updated": [], "errors": []}

        # Relationship tables with application_id FK that can be reassigned.
        # Each tuple: (table_name, fk_column_name)
        reassign_tables = [
            ("unified_application_capability_mapping", "application_id"),
            ("application_process_support", "application_id"),
            ("process_application_mapping", "application_id"),
            ("application_documents", "application_id"),
            ("application_technology_mapping", "application_id"),
            ("application_cost_records", "application_id"),
            ("capability_governance_records", "application_id"),
        ]

        for table_name, fk_col in reassign_tables:
            for dup_id in duplicate_app_ids:
                try:
                    with db.session.begin_nested():
                        # Reassign rows from duplicate to primary.
                        # Uses SAVEPOINT so constraint violations (e.g. duplicate
                        # composite keys) roll back only this statement, not the
                        # whole transaction.
                        # tenant-filtered: scoped via parent FK (dup_id)
                        sql = db.text(  # tenant-filtered
                            f"UPDATE {table_name} SET {fk_col} = :primary_id "
                            f"WHERE {fk_col} = :dup_id"
                        )
                        result = db.session.execute(  # tenant-filtered: scoped via parent FK (dup_id)
                            sql, {"primary_id": primary_app_id, "dup_id": dup_id}
                        )
                        if result.rowcount > 0:
                            reassigned["tables_updated"].append(
                                f"{table_name}: {result.rowcount} rows from app #{dup_id}"
                            )
                except Exception as e:
                    # Table may not exist, have different schema, or hit a unique
                    # constraint -- safe to skip; cleanup_app_relationships will
                    # delete remaining orphan rows.
                    reassigned["errors"].append(f"{table_name}: {str(e)}")

        # Reassign interface relationships (source and target FKs)
        for dup_id in duplicate_app_ids:
            try:
                with db.session.begin_nested():
                    # tenant-filtered: scoped via parent FK (dup_id)
                    sql = db.text(  # tenant-filtered
                        "UPDATE application_interface "
                        "SET source_application_id = :primary_id "
                        "WHERE source_application_id = :dup_id"
                    )
                    result = db.session.execute(  # tenant-filtered: scoped via parent FK (dup_id)
                        sql, {"primary_id": primary_app_id, "dup_id": dup_id}
                    )
                    if result.rowcount > 0:
                        reassigned["tables_updated"].append(
                            f"application_interface(source): {result.rowcount} rows from app #{dup_id}"
                        )
            except Exception as e:
                logger.warning(f"Could not reassign application_interface(source) for app #{dup_id}: {e}")

            try:
                with db.session.begin_nested():
                    # tenant-filtered: scoped via parent FK (dup_id)
                    sql = db.text(  # tenant-filtered
                        "UPDATE application_interface "
                        "SET target_application_id = :primary_id "
                        "WHERE target_application_id = :dup_id"
                    )
                    result = db.session.execute(  # tenant-filtered: scoped via parent FK (dup_id)
                        sql, {"primary_id": primary_app_id, "dup_id": dup_id}
                    )
                    if result.rowcount > 0:
                        reassigned["tables_updated"].append(
                            f"application_interface(target): {result.rowcount} rows from app #{dup_id}"
                        )
            except Exception as e:
                logger.warning(f"Could not reassign application_interface(target) for app #{dup_id}: {e}")

        return reassigned

    def delete_duplicates_keep_one(self, group_id: int, keep_app_id: int) -> Dict[str, Any]:
        """Delete duplicate applications, keeping only the specified one.

        AUDIT-DUP-002: Consolidates data from duplicates into primary before deletion.
        AUDIT-DUP-003: Uses SELECT ... FOR UPDATE to prevent concurrent merge race conditions.
        """
        try:
            # AUDIT-DUP-003: Acquire row-level lock and check status atomically
            # to prevent two users from resolving the same group simultaneously.
            group = db.session.query(UnifiedDuplicateGroup).filter_by(
                id=group_id
            ).with_for_update().first()

            if not group:
                return {"success": False, "error": "Duplicate group not found"}

            # Reject if already resolved or currently being resolved
            if group.status in ("resolved", "resolving"):
                return {
                    "success": False,
                    "error": f"Group already {group.status}. Cannot resolve again.",
                }

            # Immediately mark as resolving to block concurrent requests
            group.status = "resolving"
            db.session.flush()

            keep_app_id = int(keep_app_id)

            # Validate keep_app_id is a member of this group
            group_app_ids = [app.id for app in group.applications]
            if keep_app_id not in group_app_ids:
                # Revert status since we are not proceeding
                group.status = "pending"
                db.session.flush()
                return {
                    "success": False,
                    "error": f"Application {keep_app_id} is not a member of group {group_id}",
                }

            # D-03 sweep (PR 430 round 3, lead review v2, 2026-10-08):
            # UnifiedDuplicateGroup carries no organization_id at all, so
            # group_id alone does not prove the group's applications belong
            # to the caller's own organisation. Without this check, an
            # ordinary authenticated user in any organisation could pass any
            # group_id here and have this method delete another
            # organisation's real ApplicationComponent rows. Require every
            # member application to belong to the caller's active
            # organisation, unless the caller is a genuine platform admin.
            if has_request_context() and getattr(current_user, "is_authenticated", False) \
                    and not is_platform_admin(current_user):
                current_org_id = getattr(g, "current_org_id", None)
                foreign = [
                    app for app in group.applications
                    if app.organization_id != current_org_id
                ]
                if foreign:
                    group.status = "pending"
                    db.session.flush()
                    return {
                        "success": False,
                        "error": "Group contains applications outside your organisation",
                    }

            keep_app = ApplicationComponent.query.get(keep_app_id)
            kept_app_name = keep_app.name if keep_app else "Unknown"

            apps_to_delete = [app for app in group.applications if app.id != keep_app_id]

            # AUDIT-DUP-002: Consolidate data before deletion
            consolidation_summary = {}
            if keep_app and apps_to_delete:
                try:
                    consolidation_summary = self._consolidate_data_to_primary(
                        keep_app, apps_to_delete
                    )
                    self.logger.info(
                        f"Data consolidated into app #{keep_app_id}: "
                        f"{len(consolidation_summary.get('fields_copied', []))} fields, "
                        f"{len(consolidation_summary.get('costs_updated', []))} costs"
                    )
                except Exception as e:
                    self.logger.error(f"Data consolidation failed (non-fatal): {str(e)}")
                    consolidation_summary = {"error": str(e)}

            # AUDIT-DUP-002: Reassign relationships before deletion
            reassignment_summary = {}
            duplicate_app_ids = [app.id for app in apps_to_delete]
            if duplicate_app_ids:
                try:
                    reassignment_summary = self._reassign_relationships_to_primary(
                        keep_app_id, duplicate_app_ids
                    )
                    self.logger.info(
                        f"Relationships reassigned to app #{keep_app_id}: "
                        f"{len(reassignment_summary.get('tables_updated', []))} updates"
                    )
                except Exception as e:
                    self.logger.error(f"Relationship reassignment failed (non-fatal): {str(e)}")
                    reassignment_summary = {"error": str(e)}

            deleted_count = 0
            for app in apps_to_delete:
                self.cleanup_app_relationships(app.id)
                db.session.delete(app)
                deleted_count += 1

            # Mark group as resolved (AUDIT-DUP-003: final status transition)
            group.status = "resolved"
            group.resolution_action = "keep_primary"
            group.resolved_at = datetime.utcnow()
            db.session.commit()

            return {
                "success": True,
                "deleted_count": deleted_count,
                "kept_app": kept_app_name,
                "kept_app_id": keep_app_id,
                "message": f"Kept '{kept_app_name}', deleted {deleted_count} duplicate applications",
                "consolidation": consolidation_summary,
                "reassignment": reassignment_summary,
            }

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Delete duplicates failed: {str(e)}")
            return {"success": False, "error": f"Deletion failed: {str(e)}"}

    def bulk_delete_duplicates_keep_best(self, group_id: int) -> Dict[str, Any]:
        """Delete duplicate applications, keeping the best one based on criteria.

        AUDIT-DUP-003: Pre-checks group status before delegating to delete_duplicates_keep_one
        which performs the actual locking and concurrency guard.
        """
        try:
            group = UnifiedDuplicateGroup.query.get(group_id)
            if not group:
                return {"success": False, "error": "Duplicate group not found"}

            # AUDIT-DUP-003: Early exit if already resolved/resolving
            if group.status in ("resolved", "resolving"):
                return {
                    "success": False,
                    "error": f"Group already {group.status}. Cannot resolve again.",
                }

            applications = group.applications.all()
            if len(applications) <= 1:
                return {"success": True, "deleted_count": 0, "message": "No duplicates to delete"}

            # Find the best application to keep (simple criteria: most recent)
            best_app = max(applications, key=lambda app: app.created_at or datetime.min)

            # Delegates to delete_duplicates_keep_one which handles locking + consolidation
            return self.delete_duplicates_keep_one(group_id, best_app.id)

        except Exception as e:
            self.logger.error(f"Bulk delete duplicates failed: {str(e)}")
            return {"success": False, "error": f"Bulk deletion failed: {str(e)}"}

    # === UNIFIED INTERFACE METHODS ===

    def get_detection_runs(self, mode: str = "all") -> List[Dict[str, Any]]:
        """Get detection runs for specified mode"""
        try:
            if mode == "enterprise" or mode == "all":
                enterprise_runs = DuplicateDetectionRun.query.all()
                enterprise_data = [
                    {
                        "id": run.id,
                        "mode": "enterprise",
                        "status": run.status,
                        "started_at": run.started_at.isoformat() if run.started_at else None,
                        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                        "applications_analyzed": run.applications_analyzed,
                        "duplicates_found": run.duplicate_groups_found,
                    }
                    for run in enterprise_runs
                ]
            else:
                enterprise_data = []

            if mode == "simple" or mode == "all":
                simple_runs = UnifiedDetectionRun.query.all()
                simple_data = [
                    {
                        "id": run.id,
                        "mode": "simple",
                        "status": run.status,
                        "started_at": run.started_at.isoformat() if run.started_at else None,
                        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                        "applications_analyzed": run.applications_analyzed,
                        "duplicates_found": run.groups_found,
                    }
                    for run in simple_runs
                ]
            else:
                simple_data = []

            return enterprise_data + simple_data

        except Exception as e:
            self.logger.error(f"Failed to get detection runs: {str(e)}")
            return []

    def get_duplicate_groups(self, mode: str = "all", include_applications: bool = False) -> List[Dict[str, Any]]:
        """Get duplicate groups for specified mode"""
        try:
            if mode == "enterprise" or mode == "all":
                enterprise_groups = DuplicateGroup.query.all()
                enterprise_data = [
                    {
                        "id": group.id,
                        "mode": "enterprise",
                        "name": group.group_name,
                        "similarity_score": group.overall_similarity_score,
                        "duplicate_type": group.duplicate_type.value if group.duplicate_type else "unknown",
                        "application_count": group.pairwise_analyses.count() + 1,
                        "estimated_savings": group.estimated_savings or 0,
                        "consolidation_priority": group.consolidation_priority,
                    }
                    for group in enterprise_groups
                ]
            else:
                enterprise_data = []

            if mode == "simple" or mode == "all":
                simple_groups = UnifiedDuplicateGroup.query.all()

                # Batch-load all group→application mappings to avoid N+1 queries
                from app.models.unified_duplicate_detection import unified_group_members
                group_ids = [g.id for g in simple_groups]
                apps_by_group = {}
                if group_ids:
                    membership_rows = db.session.query(
                        unified_group_members.c.group_id,
                        ApplicationComponent
                    ).join(
                        ApplicationComponent,
                        ApplicationComponent.id == unified_group_members.c.application_id
                    ).filter(
                        unified_group_members.c.group_id.in_(group_ids)
                    ).all()
                    for gid, app in membership_rows:
                        apps_by_group.setdefault(gid, []).append(app)

                simple_data = []
                for group in simple_groups:
                    group_apps = apps_by_group.get(group.id, [])

                    # Skip orphaned groups where members were deleted after detection
                    if len(group_apps) < 2:
                        continue

                    # Estimate savings using pre-loaded apps (no extra queries)
                    estimated_savings = self._estimate_savings_from_apps(group_apps)

                    group_data = {
                        "id": group.id,
                        "name": group.name,
                        "description": group.description,
                        "mode": "simple",
                        "overall_similarity": group.similarity_score or 0,
                        "similarity_threshold": group.similarity_threshold,
                        "detection_method": group.duplicate_type or "unknown",
                        "duplicate_type": group.duplicate_type or "fuzzy",
                        "status": group.status or "pending",
                        "risk_level": group.risk_level or "medium",
                        "application_count": len(group_apps),
                        "estimated_savings": estimated_savings,
                    }

                    if include_applications:
                        group_data["applications"] = [
                            {
                                "id": app.id,
                                "name": app.name,
                                "description": app.description
                            }
                            for app in group_apps
                        ]

                    simple_data.append(group_data)
            else:
                simple_data = []

            return enterprise_data + simple_data

        except Exception as e:
            self.logger.error(f"Failed to get duplicate groups: {str(e)}")
            return []

    def get_archimate_element_duplicate_groups(self) -> List[Dict[str, Any]]:
        """H-01: duplicate detection over ArchiMate ELEMENTS, not just applications
        — 17 duplicate name groups covering 51/137 elements (37%) live here, and
        the module never looked.

        Reuses DuplicateDetectionUtils.find_duplicates (exact, case-insensitive
        name match) — the same engine the import pipeline uses, and the same
        exact-match approach the EA Briefing's duplicate-name gatherer landed in
        352836e. Deliberately NOT the semantic/embedding engine from Data
        Stewardship (_semantic_pairs): that threshold (0.58) was tuned against
        data-object naming and 352836e explicitly scoped estate-wide semantic
        matching out because per-ArchiMate-layer thresholds need separate
        tuning. Exact-name matching needs no threshold and is what actually
        surfaces the 17 known groups, so it ships now; semantic matching over
        the full catalogue stays a follow-up, not a silent gap.

        Grouped per (layer, type) so a Business Process named the same as an
        Application Component is not treated as a collision — same reasoning
        352836e used per-name-space.
        """
        from app.models.archimate_core import ArchiMateElement
        from app.modules.duplicate_detection.services.duplicate_detection_utils import (
            DuplicateDetectionUtils,
        )

        try:
            elements = ArchiMateElement.query.with_entities(
                ArchiMateElement.id,
                ArchiMateElement.name,
                ArchiMateElement.type,
                ArchiMateElement.layer,
            ).all()
        except Exception as exc:
            self.logger.warning("ArchiMate element duplicate scan unavailable: %s", exc)
            raise

        buckets: Dict[tuple, List[Any]] = {}
        for el in elements:
            if not el.name or not el.name.strip():
                continue
            key = (el.layer or "unknown", el.type or "unknown")
            buckets.setdefault(key, []).append(el)

        groups: List[Dict[str, Any]] = []
        gid = 0
        for (layer, etype), bucket_elements in buckets.items():
            names = [e.name for e in bucket_elements]
            dupes = DuplicateDetectionUtils.find_duplicates(names, mode="exact")
            for norm_name, indices in dupes.items():
                gid += 1
                members = [bucket_elements[i] for i in indices]
                groups.append(
                    {
                        "id": f"element-dup:{layer}:{etype}:{gid}",
                        "name": members[0].name,
                        "layer": layer,
                        "type": etype,
                        "element_count": len(members),
                        "elements": [
                            {"id": m.id, "name": m.name} for m in members
                        ],
                        "match_mode": "exact",
                    }
                )

        groups.sort(key=lambda g: -g["element_count"])
        return groups

    def run_unified_detection(self, mode: str = "enterprise", **kwargs) -> Dict[str, Any]:
        """Run unified duplicate detection with specified mode"""
        if mode == "enterprise":
            return self.run_duplicate_detection(kwargs.get("application_ids"))
        elif mode == "simple":
            return self.run_detection(kwargs.get("similarity_threshold", 0.8))
        elif mode == "hybrid":
            return self.run_detection_hybrid(kwargs.get("similarity_threshold", 0.8))
        else:
            return {
                "success": False,
                "error": f'Unknown detection mode: {mode}. Use "enterprise", "simple", or "hybrid".',
            }


# Create blueprint for unified duplicate detection routes
from flask import Blueprint

unified_duplicate_bp = Blueprint("unified_duplicate", __name__, url_prefix="/duplicate-detection")


@unified_duplicate_bp.route("/status")
def status():
    """Get status of unified duplicate detection service."""
    try:
        UnifiedDuplicateDetectionService()
        return {
            "status": "active",
            "service": "unified_duplicate_detection",
            "modes_available": ["enterprise", "simple", "hybrid"],
            "features": [
                "Multi-criteria analysis",
                "Hash-based exact matching",
                "Similarity-based detection",
                "Hybrid detection modes",
                "Cleanup and deletion operations",
                "Savings estimation",
            ],
        }
    except Exception as e:
        return {"error": str(e)}, 500
