"""

AI-Powered Duplicate Detection Service

Advanced AI/ML implementation for intelligent duplicate detection with:
- Semantic similarity using transformer models
- Business context awareness
- Adaptive learning from user feedback
- Explainable AI reasoning
- Performance optimization with caching

Phase 1: Foundation Intelligence Implementation
"""

import numpy as np
import logging
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List

from flask import g, has_app_context

# Heavy ML libraries are imported lazily (inside functions) to avoid adding
# ~50 seconds to application startup time on every boot.
# They are only loaded when the AI duplicate-detection feature is actually used.
_nx = None
_np = None
_cosine_similarity = None
_DBSCAN = None
_SentenceTransformer = None
SENTENCE_TRANSFORMERS_AVAILABLE = None  # None = not yet checked


def _ensure_ml_libs():
    """Lazily import heavy ML libraries on first use."""
    global \
        _nx, \
        _np, \
        _cosine_similarity, \
        _DBSCAN, \
        _SentenceTransformer, \
        SENTENCE_TRANSFORMERS_AVAILABLE
    if _np is not None:
        return  # already loaded
    import numpy as _numpy

    _np = _numpy
    import networkx as _networkx

    _nx = _networkx
    from sklearn.cluster import DBSCAN as _DBSCANcls

    _DBSCAN = _DBSCANcls
    from sklearn.metrics.pairwise import cosine_similarity as _cs

    _cosine_similarity = _cs
    if SENTENCE_TRANSFORMERS_AVAILABLE is None:
        try:
            from sentence_transformers import SentenceTransformer as _ST

            _SentenceTransformer = _ST
            SENTENCE_TRANSFORMERS_AVAILABLE = True
        except Exception as _e:
            _log = logging.getLogger(__name__)
            _log.warning(f"Sentence transformers not available: {_e}")
            SENTENCE_TRANSFORMERS_AVAILABLE = False


from app import db

try:
    from app import cache

    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False
    cache = None

from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.unified_duplicate_detection import (
    DetectionStrategy,
    DuplicateType,
    GroupStatus,
    UnifiedDetectionRun,
    UnifiedDuplicateGroup,
)

logger = logging.getLogger(__name__)



def _persisted_strategy(strategy: str) -> str:
    """A value the model's @validates will accept.

    detect_duplicates() advertises "ai_enhanced", which is not a
    DetectionStrategy and so raised ValueError on every run. The AI provenance
    is not lost by mapping it -- algorithm_version records "ai_v1.0".
    """
    if strategy in {s.value for s in DetectionStrategy}:
        return strategy
    return DetectionStrategy.ENHANCED.value

class SemanticDetectionEngine:
    """Advanced semantic similarity detection using transformer models"""

    def __init__(self):
        self.model = None
        self.embedding_cache = {}
        self._load_model()

    def _load_model(self):
        """Load transformer model for semantic similarity"""
        _ensure_ml_libs()
        try:
            if _SentenceTransformer is not None:
                self.model = _SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("Semantic detection model loaded successfully")
            else:
                self.model = None
        except Exception as e:
            logger.error(f"Failed to load semantic model: {e}")
            # Fallback to basic similarity
            self.model = None

    def generate_embeddings(self, texts: List[str]) -> "np.ndarray":
        """Generate semantic embeddings for text inputs"""
        _ensure_ml_libs()
        if not self.model:
            # Fallback to basic text processing
            return self._basic_text_vectors(texts)

        try:
            embeddings = self.model.encode(texts, convert_to_numpy=True)
            return embeddings
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return self._basic_text_vectors(texts)

    def _basic_text_vectors(self, texts: List[str]):
        """Fallback basic text vectorization"""
        _ensure_ml_libs()
        vectors = []
        for text in texts:
            # Simple character-level features
            features = [
                len(text),
                len(text.split()),
                text.count(" "),
                hash(text) % 1000,
            ]
            vectors.append(features)
        return _np.array(vectors)

    def calculate_semantic_similarity(self, text1: str, text2: str) -> float:
        """Calculate semantic similarity between two texts"""
        if not text1 or not text2:
            return 0.0

        if self.model:
            try:
                embeddings = self.model.encode([text1, text2])
                similarity = _cosine_similarity([embeddings[0]], [embeddings[1]])[0][0]
                return float(similarity)
            except Exception as e:
                logger.error(f"Semantic similarity calculation failed: {e}")

        # Fallback to basic string similarity
        return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()


class BusinessContextEngine:
    """Business context and organizational intelligence engine"""

    def __init__(self):
        self.capability_mappings = {}
        self.org_hierarchy = {}
        # Defer loading until an application context is available
        if has_app_context():
            self._load_business_context()

    def _load_business_context(self):
        """Load business capabilities and organizational context"""
        try:
            # Load business capabilities
            if not has_app_context():
                logger.info(
                    "Deferring business-context load until app context is available"
                )
                return
            capabilities = BusinessCapability.query.all()
            self.capability_mappings = {
                cap.name.lower(): {
                    "id": cap.id,
                    "level": cap.level,
                    "domain": cap.domain,
                    "description": cap.description,
                }
                for cap in capabilities
            }
            logger.info(f"Loaded {len(self.capability_mappings)} business capabilities")
        except Exception as e:
            logger.error(f"Failed to load business context: {e}")


class AdaptiveLearningEngine:
    """Adaptive learning system for improving detection accuracy"""

    def __init__(self):
        self.feedback_history = []
        self.threshold_history = {}
        self._load_feedback_history()

    def _load_feedback_history(self):
        """Load historical user feedback"""
        try:
            # Load from database (placeholder for actual implementation)
            logger.info("Loading feedback history for adaptive learning")
        except Exception as e:
            logger.error(f"Failed to load feedback history: {e}")

    def process_feedback(
        self, detection_result: Dict, user_action: str, confidence: int
    ):
        """Process user feedback to improve detection"""
        feedback_entry = {
            "timestamp": datetime.utcnow(),
            "detection_result": detection_result,
            "user_action": user_action,
            "confidence": confidence,
        }

        self.feedback_history.append(feedback_entry)

        # Update thresholds based on feedback
        self._update_thresholds(detection_result, user_action, confidence)

        logger.info(f"Processed feedback: {user_action} with confidence {confidence}")

    def _update_thresholds(
        self, detection_result: Dict, user_action: str, confidence: int
    ):
        """Update detection thresholds based on feedback"""
        algorithm = detection_result.get("algorithm", "unknown")
        original_confidence = detection_result.get("confidence", 0.0)

        if algorithm not in self.threshold_history:
            self.threshold_history[algorithm] = []

        # Store feedback for threshold optimization
        self.threshold_history[algorithm].append(
            {
                "original_confidence": original_confidence,
                "user_confidence": confidence,
                "action": user_action,
                "timestamp": datetime.utcnow(),
            }
        )

    def get_optimized_threshold(self, algorithm: str) -> float:
        """Get optimized threshold for an algorithm based on feedback"""
        if (
            algorithm not in self.threshold_history
            or len(self.threshold_history[algorithm]) < 10
        ):
            return 0.65  # Default threshold

        feedback_data = self.threshold_history[algorithm]

        # Calculate optimal threshold based on feedback
        accepted_confidences = [
            f["original_confidence"] for f in feedback_data if f["action"] == "accept"
        ]

        if not accepted_confidences:
            return 0.65

        # Simple optimization: use median of accepted confidences
        accepted_confidences.sort()
        optimal_threshold = accepted_confidences[len(accepted_confidences) // 2]

        return max(0.3, min(0.9, optimal_threshold))


class AIDuplicateDetectionService:
    """Main AI-powered duplicate detection service"""

    def __init__(self):
        # Defer heavy initialization until first use to avoid app-context side-effects
        self.semantic_engine = None
        self.business_engine = None
        self.learning_engine = None
        self.performance_metrics = {
            "detections_count": 0,
            "processing_time_total": 0.0,
            "accuracy_improvements": 0,
        }

    def _ensure_engines(self):
        """Initialize engines lazily when running under an app context."""
        if self.semantic_engine is None:
            try:
                self.semantic_engine = SemanticDetectionEngine()
            except Exception:
                self.semantic_engine = SemanticDetectionEngine()
        if self.business_engine is None:
            self.business_engine = BusinessContextEngine()
        if self.learning_engine is None:
            self.learning_engine = AdaptiveLearningEngine()

    def detect_duplicates(
        self,
        strategy: str = "ai_enhanced",
        threshold: float = 0.65,
        config: Dict = None,
    ) -> Dict[str, Any]:
        """
        AI-powered duplicate detection with semantic understanding

        Args:
            strategy: Detection strategy ('ai_enhanced', 'semantic_only', 'business_aware')
            threshold: Similarity threshold for duplicate detection
            config: Additional configuration options

        Returns:
            Detection results with AI insights and explanations
        """
        start_time = datetime.utcnow()

        try:
            # The engines are created lazily; without this the semantic engine is
            # still None and every call raises AttributeError.
            self._ensure_engines()

            # Get all applications
            applications = self._get_applications_data()

            # Generate embeddings for semantic analysis
            app_texts = [
                f"{app['name']} {app.get('description', '')}" for app in applications
            ]
            embeddings = self.semantic_engine.generate_embeddings(app_texts)

            # Perform AI-powered detection
            if strategy == "ai_enhanced":
                duplicates = self._ai_enhanced_detection(
                    applications, embeddings, threshold
                )
            elif strategy == "semantic_only":
                duplicates = self._semantic_detection(
                    applications, embeddings, threshold
                )
            elif strategy == "business_aware":
                duplicates = self._business_aware_detection(applications, threshold)
            else:
                raise ValueError(f"Unknown strategy: {strategy}")

            # Generate explanations and insights
            enhanced_duplicates = self._generate_ai_insights(duplicates, applications)

            # Create detection run record
            run = self._create_detection_run(strategy, threshold, enhanced_duplicates)

            # Update performance metrics
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            self._update_performance_metrics(processing_time, len(enhanced_duplicates))

            return {
                "success": True,
                "run_id": run.id,
                "duplicates": enhanced_duplicates,
                "statistics": self._calculate_statistics(enhanced_duplicates),
                "processing_time": processing_time,
                "algorithm_version": "ai_v1.0",
                "ai_insights": self._generate_global_insights(enhanced_duplicates),
            }

        except Exception:
            # Log the internal detail server-side; never return it to the client.
            logger.error("AI duplicate detection failed", exc_info=True)
            return {
                "success": False,
                "error": "Duplicate detection could not be completed.",
                "processing_time": (datetime.utcnow() - start_time).total_seconds(),
            }

    def _get_applications_data(self) -> List[Dict[str, Any]]:
        """Get application data with enhanced features"""
        apps = ApplicationComponent.query.all()

        applications_data = []
        for app in apps:
            applications_data.append(
                {
                    "id": app.id,
                    "name": app.name or "",
                    "description": app.description or "",
                    "component_type": app.component_type or "",
                    "technology_stack": app.technology_stack or "",
                    "business_domain": getattr(app, "business_domain", "") or "",
                    "lifecycle_status": app.lifecycle_status or "",
                    "deployment_status": getattr(app, "deployment_status", "") or "",
                }
            )

        return applications_data

    def _ai_enhanced_detection(
        self, applications: List[Dict], embeddings, threshold: float
    ) -> List[Dict[str, Any]]:
        """Enhanced AI detection combining semantic, business, and structural analysis"""
        duplicates = []

        for i, app1 in enumerate(applications):
            for j, app2 in enumerate(applications[i + 1 :], i + 1):
                # Semantic similarity
                semantic_sim = _cosine_similarity([embeddings[i]], [embeddings[j]])[0][
                    0
                ]

                # Business context similarity
                business_sim = self.business_engine.calculate_business_similarity(
                    app1, app2
                )

                # Structural similarity (technology, type, etc.)
                structural_sim = self._calculate_structural_similarity(app1, app2)

                # Ensemble scoring with weighted voting
                overall_similarity = (
                    semantic_sim * 0.5
                    + business_sim * 0.3  # Semantic understanding
                    + structural_sim * 0.2  # Business context  # Structural features
                )

                if overall_similarity >= threshold:
                    duplicates.append(
                        {
                            "app1_id": app1["id"],
                            "app2_id": app2["id"],
                            "app1_name": app1["name"],
                            "app2_name": app2["name"],
                            "similarity_scores": {
                                "semantic": float(semantic_sim),
                                "business": float(business_sim),
                                "structural": float(structural_sim),
                                "overall": float(overall_similarity),
                            },
                            "detection_algorithm": "ai_enhanced",
                            "confidence": float(overall_similarity),
                        }
                    )

        return duplicates

    def _semantic_detection(
        self, applications: List[Dict], embeddings, threshold: float
    ) -> List[Dict[str, Any]]:
        """Semantic-only duplicate detection"""
        duplicates = []

        for i, app1 in enumerate(applications):
            for j, app2 in enumerate(applications[i + 1 :], i + 1):
                semantic_sim = _cosine_similarity([embeddings[i]], [embeddings[j]])[0][
                    0
                ]

                if semantic_sim >= threshold:
                    duplicates.append(
                        {
                            "app1_id": app1["id"],
                            "app2_id": app2["id"],
                            "app1_name": app1["name"],
                            "app2_name": app2["name"],
                            "similarity_scores": {
                                "semantic": float(semantic_sim),
                                "overall": float(semantic_sim),
                            },
                            "detection_algorithm": "semantic_only",
                            "confidence": float(semantic_sim),
                        }
                    )

        return duplicates

    def _business_aware_detection(
        self, applications: List[Dict], threshold: float
    ) -> List[Dict[str, Any]]:
        """Business context-aware duplicate detection"""
        duplicates = []

        for i, app1 in enumerate(applications):
            for j, app2 in enumerate(applications[i + 1 :], i + 1):
                business_sim = self.business_engine.calculate_business_similarity(
                    app1, app2
                )

                # Add basic name similarity for business context
                name_sim = SequenceMatcher(
                    None, app1["name"].lower(), app2["name"].lower()
                ).ratio()
                overall_sim = (business_sim * 0.7) + (name_sim * 0.3)

                if overall_sim >= threshold:
                    duplicates.append(
                        {
                            "app1_id": app1["id"],
                            "app2_id": app2["id"],
                            "app1_name": app1["name"],
                            "app2_name": app2["name"],
                            "similarity_scores": {
                                "business": float(business_sim),
                                "name": float(name_sim),
                                "overall": float(overall_sim),
                            },
                            "detection_algorithm": "business_aware",
                            "confidence": float(overall_sim),
                        }
                    )

        return duplicates

    def _calculate_structural_similarity(self, app1: Dict, app2: Dict) -> float:
        """Calculate structural similarity between applications"""
        similarities = []

        # Component type similarity
        if app1["component_type"] and app2["component_type"]:
            type_sim = 1.0 if app1["component_type"] == app2["component_type"] else 0.0
            similarities.append(type_sim * 0.3)

        # Technology stack similarity
        if app1["technology_stack"] and app2["technology_stack"]:
            tech_sim = self._calculate_tech_similarity(
                app1["technology_stack"], app2["technology_stack"]
            )
            similarities.append(tech_sim * 0.4)

        # Lifecycle status similarity
        if app1["lifecycle_status"] and app2["lifecycle_status"]:
            status_sim = (
                1.0 if app1["lifecycle_status"] == app2["lifecycle_status"] else 0.0
            )
            similarities.append(status_sim * 0.2)

        # Deployment status similarity
        if app1["deployment_status"] and app2["deployment_status"]:
            deploy_sim = (
                1.0 if app1["deployment_status"] == app2["deployment_status"] else 0.0
            )
            similarities.append(deploy_sim * 0.1)

        return sum(similarities) if similarities else 0.0

    def _calculate_tech_similarity(self, tech1: str, tech2: str) -> float:
        """Calculate technology stack similarity"""
        if not tech1 or not tech2:
            return 0.0

        tech1_lower = tech1.lower()
        tech2_lower = tech2.lower()

        # Split into individual technologies
        tech1_items = set(item.strip() for item in tech1_lower.split(","))
        tech2_items = set(item.strip() for item in tech2_lower.split(","))

        if not tech1_items or not tech2_items:
            return 0.0

        # Calculate Jaccard similarity
        intersection = len(tech1_items.intersection(tech2_items))
        union = len(tech1_items.union(tech2_items))

        return intersection / union if union > 0 else 0.0

    def _generate_ai_insights(
        self, duplicates: List[Dict], applications: List[Dict]
    ) -> List[Dict]:
        """Generate AI insights and explanations for duplicates"""
        enhanced_duplicates = []

        for duplicate in duplicates:
            # Generate explanation
            explanation = self._generate_explanation(duplicate)

            # Generate business impact
            business_impact = self._calculate_business_impact(duplicate, applications)

            # Generate resolution recommendations
            recommendations = self._generate_resolution_recommendations(duplicate)

            enhanced_duplicate = duplicate.copy()
            enhanced_duplicate.update(
                {
                    "explanation": explanation,
                    "business_impact": business_impact,
                    "recommendations": recommendations,
                    "ai_insights": {
                        "detection_confidence": duplicate["confidence"],
                        "primary_similarity_factor": self._get_primary_factor(
                            duplicate
                        ),
                        "risk_level": self._assess_risk_level(duplicate),
                        "automation_potential": self._assess_automation_potential(
                            duplicate
                        ),
                    },
                }
            )

            enhanced_duplicates.append(enhanced_duplicate)

        return enhanced_duplicates

    def _generate_explanation(self, duplicate: Dict) -> str:
        """Generate human-readable explanation for duplicate detection"""
        scores = duplicate["similarity_scores"]
        explanation_parts = []

        if "semantic" in scores and scores["semantic"] > 0.7:
            explanation_parts.append(
                f"Strong semantic similarity ({scores['semantic']:.1%})"
            )

        if "business" in scores and scores["business"] > 0.6:
            explanation_parts.append(
                f"Shared business context ({scores['business']:.1%})"
            )

        if "structural" in scores and scores["structural"] > 0.5:
            explanation_parts.append(
                f"Similar technical structure ({scores['structural']:.1%})"
            )

        if "name" in scores and scores["name"] > 0.8:
            explanation_parts.append(f"Similar names ({scores['name']:.1%})")

        if not explanation_parts:
            explanation_parts.append(f"Overall similarity ({scores['overall']:.1%})")

        return "Detected as duplicate due to: " + ", ".join(explanation_parts)

    def _calculate_business_impact(
        self, duplicate: Dict, applications: List[Dict]
    ) -> Dict[str, Any]:
        """Calculate business impact of duplicate resolution"""
        # Find applications
        app1 = next(
            (app for app in applications if app["id"] == duplicate["app1_id"]), None
        )
        app2 = next(
            (app for app in applications if app["id"] == duplicate["app2_id"]), None
        )

        if not app1 or not app2:
            return {"cost_savings": 0, "risk_reduction": 0, "efficiency_gain": 0}

        # Simple impact calculation (can be enhanced with real business metrics)
        base_impact = duplicate["confidence"] * 50000  # $50K base for perfect match

        return {
            "cost_savings": int(base_impact * 0.6),  # License, maintenance savings
            "risk_reduction": int(base_impact * 0.3),  # Reduced complexity
            "efficiency_gain": int(base_impact * 0.1),  # Operational efficiency
        }

    def _generate_resolution_recommendations(self, duplicate: Dict) -> List[str]:
        """Generate resolution recommendations"""
        recommendations = []
        scores = duplicate["similarity_scores"]

        if duplicate["confidence"] > 0.8:
            recommendations.append(
                "High confidence match - consider immediate consolidation"
            )
        elif duplicate["confidence"] > 0.6:
            recommendations.append(
                "Moderate confidence - review business context before action"
            )
        else:
            recommendations.append(
                "Low confidence - investigate further before consolidation"
            )

        if "business" in scores and scores["business"] > 0.7:
            recommendations.append(
                "Strong business alignment - prioritize for consolidation"
            )

        if "semantic" in scores and scores["semantic"] > 0.8:
            recommendations.append(
                "Semantic similarity suggests functional equivalence"
            )

        return recommendations

    def _get_primary_factor(self, duplicate: Dict) -> str:
        """Get the primary similarity factor"""
        scores = duplicate["similarity_scores"]

        max_score = 0.0
        primary_factor = "overall"

        for factor, score in scores.items():
            if factor != "overall" and score > max_score:
                max_score = score
                primary_factor = factor

        return primary_factor

    def _assess_risk_level(self, duplicate: Dict) -> str:
        """Assess risk level of duplicate resolution"""
        confidence = duplicate["confidence"]

        if confidence > 0.8:
            return "low"  # Safe to consolidate
        elif confidence > 0.6:
            return "medium"  # Requires careful review
        else:
            return "high"  # Risky, detailed investigation needed

    def _assess_automation_potential(self, duplicate: Dict) -> str:
        """Assess automation potential"""
        confidence = duplicate["confidence"]
        scores = duplicate["similarity_scores"]

        # High automation potential for high-confidence semantic matches
        if confidence > 0.85 and scores.get("semantic", 0) > 0.8:
            return "high"
        elif confidence > 0.7:
            return "medium"
        else:
            return "low"

    def _create_detection_run(
        self, strategy: str, threshold: float, duplicates: List[Dict]
    ) -> UnifiedDetectionRun:
        """Create detection run record"""
        run = UnifiedDetectionRun(
            run_name=f"AI Detection Run {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            strategy=_persisted_strategy(strategy),
            similarity_threshold=threshold,
            status="completed",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            applications_analyzed=len(
                set(
                    [d["app1_id"] for d in duplicates]
                    + [d["app2_id"] for d in duplicates]
                )
            ),
            groups_found=len(duplicates),
            exact_matches=len([d for d in duplicates if d["confidence"] > 0.9]),
            fuzzy_matches=len([d for d in duplicates if 0.7 <= d["confidence"] <= 0.9]),
            estimated_savings=sum(
                d.get("business_impact", {}).get("cost_savings", 0) for d in duplicates
            ),
            algorithm_version="ai_v1.0",
            organization_id=getattr(g, "current_org_id", None),
        )

        db.session.add(run)
        db.session.commit()

        # Create duplicate groups
        for duplicate in duplicates:
            group = UnifiedDuplicateGroup(
                detection_run_id=run.id,
                name=f"AI Duplicate Group: {duplicate['app1_name']} / {duplicate['app2_name']}",
                description=duplicate.get("explanation", ""),
                duplicate_type=(
                    DuplicateType.EXACT.value
                    if duplicate["confidence"] > 0.9
                    else DuplicateType.FUZZY.value
                ),
                similarity_score=duplicate["confidence"],
                similarity_threshold=threshold,
                status=GroupStatus.PENDING.value,
                match_details={
                    "detection_algorithm": duplicate["detection_algorithm"],
                    "similarity_scores": duplicate["similarity_scores"],
                    "ai_insights": duplicate.get("ai_insights", {}),
                    "business_impact": duplicate.get("business_impact", {}),
                    "recommendations": duplicate.get("recommendations", []),
                },
            )

            db.session.add(group)
            db.session.flush()

            # Without members a group is not reviewable: the screen lists a pair
            # and offers a merge, and both come from this relationship.
            members = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(
                    [duplicate["app1_id"], duplicate["app2_id"]]
                )
            ).all()
            for member in members:
                group.applications.append(member)

        db.session.commit()
        return run

    def _calculate_statistics(self, duplicates: List[Dict]) -> Dict[str, Any]:
        """Calculate detection statistics"""
        if not duplicates:
            return {
                "total_duplicates": 0,
                "high_confidence": 0,
                "medium_confidence": 0,
                "low_confidence": 0,
                "average_confidence": 0.0,
                "total_savings": 0,
            }

        total_duplicates = len(duplicates)
        high_confidence = len([d for d in duplicates if d["confidence"] > 0.8])
        medium_confidence = len(
            [d for d in duplicates if 0.6 <= d["confidence"] <= 0.8]
        )
        low_confidence = len([d for d in duplicates if d["confidence"] < 0.6])

        average_confidence = sum(d["confidence"] for d in duplicates) / total_duplicates
        total_savings = sum(
            d.get("business_impact", {}).get("cost_savings", 0) for d in duplicates
        )

        return {
            "total_duplicates": total_duplicates,
            "high_confidence": high_confidence,
            "medium_confidence": medium_confidence,
            "low_confidence": low_confidence,
            "average_confidence": average_confidence,
            "total_savings": total_savings,
        }

    def _generate_global_insights(self, duplicates: List[Dict]) -> Dict[str, Any]:
        """Generate global insights from detection results"""
        if not duplicates:
            return {"insights": [], "recommendations": [], "quality_score": 0.0}

        insights = []
        recommendations = []

        # Analyze confidence distribution
        avg_confidence = sum(d["confidence"] for d in duplicates) / len(duplicates)

        if avg_confidence > 0.8:
            insights.append(
                "High-quality duplicate detection with strong AI confidence"
            )
            recommendations.append(
                "Proceed with automated consolidation of high-confidence matches"
            )
        elif avg_confidence > 0.6:
            insights.append("Moderate detection quality - requires human review")
            recommendations.append(
                "Implement review workflow for medium-confidence matches"
            )
        else:
            insights.append("Low detection quality - algorithm tuning needed")
            recommendations.append(
                "Consider adjusting thresholds or improving data quality"
            )

        # Analyze primary factors
        factors = [self._get_primary_factor(d) for d in duplicates]
        factor_counts = {factor: factors.count(factor) for factor in set(factors)}
        primary_factor = max(factor_counts, key=factor_counts.get)

        insights.append(f"Primary detection factor: {primary_factor}")

        # Quality score
        quality_score = min(
            1.0,
            avg_confidence
            * (
                1.0
                - (
                    len([d for d in duplicates if d["confidence"] < 0.5])
                    / len(duplicates)
                )
            ),
        )

        return {
            "insights": insights,
            "recommendations": recommendations,
            "quality_score": quality_score,
            "primary_factors": factor_counts,
        }

    def _update_performance_metrics(
        self, processing_time: float, duplicates_count: int
    ):
        """Update performance metrics"""
        self.performance_metrics["detections_count"] += 1
        self.performance_metrics["processing_time_total"] += processing_time
        self.performance_metrics["accuracy_improvements"] += duplicates_count

    def process_user_feedback(
        self, duplicate_id: int, user_action: str, confidence: int
    ):
        """Process user feedback for adaptive learning"""
        # This would integrate with the actual duplicate record
        feedback_data = {
            "duplicate_id": duplicate_id,
            "action": user_action,
            "confidence": confidence,
            "timestamp": datetime.utcnow(),
        }

        self._ensure_engines()
        self.learning_engine.process_feedback(feedback_data, user_action, confidence)

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get current performance metrics"""
        avg_processing_time = self.performance_metrics["processing_time_total"] / max(
            1, self.performance_metrics["detections_count"]
        )

        return {
            **self.performance_metrics,
            "average_processing_time": avg_processing_time,
            "detections_per_hour": 3600 / avg_processing_time
            if avg_processing_time > 0
            else 0,
        }


# Global service instance
ai_detection_service = AIDuplicateDetectionService()
