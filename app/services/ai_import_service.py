"""
AI Import Service - Truly Intelligent Application Import Pipeline

Integrates all AI services to provide comprehensive AI-powered import:
1. Semantic APQC process classification using real embeddings
2. Business capability mapping using LLM analysis
3. ArchiMate element generation from application descriptions
4. Vendor template matching and cloning
5. Confidence scoring and user review workflow

This replaces the basic pattern-matching with sophisticated AI analysis.
"""

from app.services.change_source import records_changes_as
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional  # dead-code-ok

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.apqc_process import ProcessApplicationMapping  # dead-code-ok
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability
from app.services.apqc_hierarchy_service import APQCHierarchyService
from app.services.archimate.archimate_llm_service import ArchiMateLLMService
from app.services.archimate_layer_generators import ArchiMateLayerGenerators
from app.services.archimate_pattern_library import ArchiMatePatternLibrary
from app.services.archimate_relationship_generator import ArchiMateRelationshipGenerator
from app.services.batch_processing_service import BatchJobConfig, BatchProcessingService
from app.services.capability_taxonomy_service import CapabilityTaxonomyService
from app.services.confidence_review_service import ConfidenceReviewService, ReviewQueueItemData
from app.services.llm_service import LLMService
from app.services.unified_apqc_service import get_unified_apqc_service  # dead-code-ok
from app.services.vector_embedding_service import VectorEmbeddingService
from app.services.vendor_product_service import VendorProductService

logger = logging.getLogger(__name__)


@dataclass
class AIImportResult:
    """Result of AI-powered import analysis."""

    application_id: int
    application_name: str

    # Analysis results
    capability_mappings: List[Dict[str, Any]]
    process_mappings: List[Dict[str, Any]]
    vendor_product_analysis: Dict[str, Any]
    taxonomy_validation: Dict[str, Any]
    archimate_elements: List[Dict[str, Any]]

    # Confidence scores
    avg_capability_confidence: float
    avg_process_confidence: float
    avg_vendor_confidence: float
    avg_taxonomy_confidence: float
    avg_overall_confidence: float

    # Success indicators
    archimate_generation_success: bool
    vendor_product_identified: bool
    taxonomy_compliant: bool
    confidence_threshold_met: bool
    requires_human_review: bool

    # Processing metadata
    processing_time_ms: int
    ai_models_used: List[str]
    warnings: List[str]

    # Review queue information
    review_queue_items: List[Dict[str, Any]]


class AIImportService:
    """
    Comprehensive AI-powered import service.

    Features:
    - Real semantic embeddings for APQC classification
    - LLM-powered business capability analysis
    - ArchiMate element generation from application descriptions
    - Confidence scoring and user review
    - Vendor template integration
    """

    def __init__(self):
        # Use unified APQC service instead of individual services
        self.unified_apqc = get_unified_apqc_service()
        self.archimate_llm = None  # Lazy load to avoid fast-init issues
        self.vector_embeddings = VectorEmbeddingService()
        self.llm_service = LLMService()
        self.apqc_hierarchy = APQCHierarchyService()
        self.vendor_product = VendorProductService()
        self.capability_taxonomy = CapabilityTaxonomyService()
        self.batch_processing = BatchProcessingService()
        self.confidence_review = ConfidenceReviewService()
        self.layer_generators = ArchiMateLayerGenerators(self.llm_service)
        self.relationship_generator = ArchiMateRelationshipGenerator(self.llm_service)
        self.pattern_library = ArchiMatePatternLibrary()

    def _get_archimate_service(self) -> ArchiMateLLMService:
        """Lazy load ArchiMate service to avoid fast-init issues."""
        if self.archimate_llm is None:
            try:
                self.archimate_llm = ArchiMateLLMService()
                logger.info("ArchiMateLLMService initialized for AI import")
            except Exception as e:
                logger.error(f"Failed to initialize ArchiMateLLMService: {e}")
                raise
        return self.archimate_llm

    def analyze_application_for_ai_mapping(self, application_id: int) -> AIImportResult:
        """
        Perform comprehensive AI analysis of a single application.

        Args:
            application_id: ID of application to analyze

        Returns:
            AIImportResult with all AI-powered mappings and confidence scores
        """
        start_time = datetime.utcnow()

        # Get application
        app = ApplicationComponent.query.get(application_id)
        if not app:
            raise ValueError(f"Application {application_id} not found")

        # Build comprehensive application context
        app_context = self._build_application_context(app)

        # Initialize result
        result = AIImportResult(
            application_id=app.id,
            application_name=app.name,
            capability_mappings=[],
            process_mappings=[],
            vendor_product_analysis={},
            taxonomy_validation={},
            archimate_elements=[],
            avg_capability_confidence=0.0,
            avg_process_confidence=0.0,
            avg_vendor_confidence=0.0,
            avg_taxonomy_confidence=0.0,
            avg_overall_confidence=0.0,
            archimate_generation_success=False,
            vendor_product_identified=False,
            taxonomy_compliant=False,
            confidence_threshold_met=False,
            requires_human_review=False,
            processing_time_ms=0,
            ai_models_used=[],
            warnings=[],
            review_queue_items=[],
        )

        try:
            # 1. AI-powered business capability mapping
            result.capability_mappings = self._map_capabilities_with_ai(app, app_context)

            # 2. Semantic APQC process classification
            result.process_mappings = self._classify_processes_with_ai(app, app_context)

            # 4. Vendor product analysis
            result.vendor_product_analysis = self._analyze_vendor_product_with_ai(app, app_context)

            # 5. Capability taxonomy validation
            result.taxonomy_validation = self._validate_capability_taxonomy_with_ai(
                app, app_context
            )

            # 6. ArchiMate element generation
            result.archimate_elements = self._generate_archimate_with_ai(app, app_context)

            # 7. Confidence threshold validation and review queue processing
            result.review_queue_items = self._validate_confidence_thresholds(app, result)

            # Calculate overall confidence score
            all_confidences = [
                result.avg_capability_confidence,
                result.avg_process_confidence,
                result.avg_vendor_confidence,
                result.avg_taxonomy_confidence,
            ]
            result.avg_overall_confidence = (
                sum(c for c in all_confidences if c > 0)
                / len([c for c in all_confidences if c > 0])
                if all_confidences
                else 0.0
            )

            # Determine if confidence thresholds are met
            result.confidence_threshold_met = result.avg_overall_confidence >= 0.6
            result.requires_human_review = any(
                item.get("requires_review", False) for item in result.review_queue_items
            )

            # NEW: Add items to review queue if they require human review
            if result.requires_human_review and self.confidence_review:
                for mapping in result.review_queue_items:
                    if mapping.get("requires_review", False):
                        try:
                            review_item = self.confidence_review.add_to_review_queue(
                                application_id=app.id,
                                mapping_type=mapping.get("type", "unknown"),
                                mapping_data=mapping.get("data", {}),
                                confidence_score=mapping.get("confidence_score", 0.0),
                                ai_model_used=mapping.get("ai_model_used", "unknown"),
                                threshold_name=mapping.get("threshold_name", "default"),
                                rationale=mapping.get("rationale", "Requires human review"),
                                confidence_factors=mapping.get("confidence_factors", {}),
                                alternatives=mapping.get("alternatives", []),
                            )
                            logger.info(
                                f"Added mapping to review queue: {review_item.id} for application {app.name}"
                            )
                        except Exception as e:
                            logger.error(f"Failed to add mapping to review queue: {e}")

            # Log confidence review results
            if result.requires_human_review:
                logger.info(
                    f"Application {app.name} requires human review: {len(result.review_queue_items)} items queued"
                )
            else:
                logger.info(
                    f"Application {app.name} passed confidence thresholds: {result.avg_overall_confidence:.2f}"
                )

            # Calculate confidence scores
            if result.capability_mappings:
                result.avg_capability_confidence = sum(
                    m.get("confidence_score", 0) for m in result.capability_mappings
                ) / len(result.capability_mappings)

            if result.process_mappings:
                result.avg_process_confidence = sum(
                    m.get("similarity_score", 0) for m in result.process_mappings
                ) / len(result.process_mappings)

            if result.vendor_product_analysis:
                result.avg_vendor_confidence = result.vendor_product_analysis.get(
                    "overall_confidence", 0.0
                )
                result.vendor_product_identified = result.vendor_product_analysis.get(
                    "success", False
                )

            if result.taxonomy_validation:
                result.avg_taxonomy_confidence = result.taxonomy_validation.get(
                    "overall_confidence", 0.0
                )
                result.taxonomy_compliant = result.taxonomy_validation.get("is_valid", False)

            result.archimate_generation_success = len(result.archimate_elements) > 0

            # Track AI models used
            if result.capability_mappings:
                result.ai_models_used.append("LLM-capability-analysis")
            if result.process_mappings:
                result.ai_models_used.extend(["sentence-transformers", "FAISS"])
            if result.vendor_product_analysis:
                result.ai_models_used.append("vendor-pattern-matching")
            if result.taxonomy_validation:
                result.ai_models_used.append("capability-taxonomy-validation")
            if result.archimate_elements:
                result.ai_models_used.append("LLM-ArchiMate-generation")
            if result.review_queue_items:
                result.ai_models_used.append("confidence-threshold-validation")

        except Exception as e:
            logger.error(f"AI analysis failed for app {application_id}: {e}")
            result.warnings.append(f"AI analysis error: {str(e)}")

        # Calculate processing time
        end_time = datetime.utcnow()
        result.processing_time_ms = int((end_time - start_time).total_seconds() * 1000)

        return result

    def _build_application_context(self, app: ApplicationComponent) -> str:
        """Build comprehensive text context for AI analysis."""
        context_parts = []

        # Basic info
        context_parts.append(f"Application: {app.name}")
        if app.description:
            context_parts.append(f"Description: {app.description}")

        # Technical details
        if app.technology_stack:
            context_parts.append(f"Technology: {app.technology_stack}")
        if app.vendor_name:
            context_parts.append(f"Vendor: {app.vendor_name}")

        # Business context
        if app.business_domain:
            context_parts.append(f"Business Domain: {app.business_domain}")
        if app.business_criticality:
            context_parts.append(f"Criticality: {app.business_criticality}")

        # Imported data (most valuable for AI)
        if app.imported_capabilities:
            context_parts.append(f"Imported Capabilities: {app.imported_capabilities}")
        if app.application_services:
            context_parts.append(f"Application Services: {app.application_services}")
        if app.application_functions_text:
            context_parts.append(f"Business Functions: {app.application_functions_text}")
        if app.imported_apqc_codes:
            context_parts.append(f"APQC Codes: {app.imported_apqc_codes}")

        return "\n".join(filter(None, context_parts))

    def _map_capabilities_with_ai(
        self, app: ApplicationComponent, context: str
    ) -> List[Dict[str, Any]]:
        """Use LLM to map application to business capabilities."""
        try:
            # Get all business capabilities
            capabilities = UnifiedCapability.query.all()
            logger.info(f"Found {len(capabilities)} business capabilities in database")

            app_name = app.name if app else "Unknown Application"

            # Two modes: match existing capabilities OR suggest new ones
            if capabilities:
                # Mode 1: Match to existing capabilities in database
                capability_descriptions = []
                for cap in capabilities:
                    desc = f"{cap.name}"
                    if cap.description:
                        desc += f": {cap.description}"
                    if cap.domain:
                        desc += f" (Domain: {cap.domain})"
                    capability_descriptions.append(desc)

                logger.info(f"Built descriptions for {len(capability_descriptions)} capabilities")

                prompt = f"""
Analyze this application and suggest the most relevant business capabilities:

APPLICATION CONTEXT:
{context}

AVAILABLE BUSINESS CAPABILITIES:
{chr(10).join(f"{i + 1}. {desc}" for i, desc in enumerate(capability_descriptions))}

Instructions:
1. Analyze the application's functionality and business purpose
2. Match to the most relevant business capabilities
3. Assign confidence scores (0.0 - 1.0) based on fit
4. Return JSON format with top 3 - 5 matches

Response format:
{{
    "matches": [
        {{
            "capability_id": <id>,
            "capability_name": "<name>",
            "confidence_score": <0.0 - 1.0>,
            "rationale": "<explanation>"
        }}
    ]
}}
"""
            else:
                # Mode 2: Generate capability suggestions (database is empty)
                logger.info(
                    "No capabilities in database - generating AI suggestions for new capabilities"
                )

                prompt = f"""
Analyze this application and suggest 3 - 5 business capabilities it should be mapped to:

APPLICATION CONTEXT:
{context}

Instructions:
1. Analyze the application's functionality and business purpose
2. Suggest appropriate business capabilities this application supports
3. Use standard enterprise capability taxonomy (e.g., Customer Management, Order Management, Financial Management, etc.)
4. Assign confidence scores (0.0 - 1.0) based on fit
5. Return JSON format

Response format:
{{
    "matches": [
        {{
            "capability_id": null,
            "capability_name": "<suggested capability name>",
            "confidence_score": <0.0 - 1.0>,
            "rationale": "<explanation of why this capability fits>"
        }}
    ]
}}

Note: capability_id should be null since these are suggestions for new capabilities to create.
"""

            # Get LLM response
            logger.info(f"Sending capability mapping request to LLM for {app_name}")
            response = self.llm_service.generate_from_prompt(prompt)
            logger.info(f"LLM response received: {len(response)} characters")

            # Parse response
            try:
                # Extract JSON from response that may have text preamble
                cleaned_response = response.strip()
                if "{" in cleaned_response:
                    # Find first { and last }
                    start_idx = cleaned_response.find("{")
                    end_idx = cleaned_response.rfind("}") + 1
                    if start_idx != -1 and end_idx > start_idx:
                        cleaned_response = cleaned_response[start_idx:end_idx]

                logger.info(
                    f"Cleaned response for JSON parsing: {len(cleaned_response)} characters"
                )
                llm_result = json.loads(cleaned_response)
                matches = llm_result.get("matches", [])
                logger.info(f"LLM returned {len(matches)} capability matches")

                # Validate and enrich matches
                validated_matches = []
                min_confidence = 0.5  # Lower threshold for enterprise-scale mapping
                max_matches = 10  # Support complex applications with multiple capabilities

                for i, match in enumerate(matches):
                    if i >= max_matches:
                        break

                    cap_id = match.get("capability_id")
                    cap_name = match.get("capability_name", "")
                    confidence = match.get("confidence_score", 0.0)

                    logger.info(
                        f"Processing match {i + 1}: capability_id={cap_id}, capability_name={cap_name}, confidence={confidence}"
                    )

                    if confidence >= min_confidence:
                        if cap_id:
                            # Existing capability - validate it exists in database
                            cap = UnifiedCapability.query.get(cap_id)
                            if cap:
                                validated_matches.append(
                                    {
                                        "capability_id": cap.id,
                                        "capability_name": cap.name,
                                        "confidence_score": min(
                                            1.0, max(0.0, match.get("confidence_score", 0.5))
                                        ),
                                        "rationale": match.get("rationale", ""),
                                        "domain": cap.domain.name if cap.domain else "Unknown",
                                        "level": cap.level,
                                    }
                                )
                                logger.info(f"✅ Validated match: {cap.name}")
                            else:
                                logger.warning(f"❌ Capability not found for ID: {cap_id}")
                        elif cap_name:
                            # AI-suggested capability (no ID) - include as suggestion
                            validated_matches.append(
                                {
                                    "capability_id": None,
                                    "capability_name": cap_name,
                                    "confidence_score": min(
                                        1.0, max(0.0, match.get("confidence_score", 0.5))
                                    ),
                                    "rationale": match.get("rationale", ""),
                                    "domain": "AI Suggestion",
                                    "level": 0,
                                    "is_suggestion": True,
                                }
                            )
                            logger.info(f"✅ AI-suggested capability: {cap_name}")
                    else:
                        logger.warning(f"❌ No capability_id in match: {match}")

                logger.info(f"Final validated matches: {len(validated_matches)}")
                return validated_matches

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM capability response: {e}")
                return []

        except Exception as e:
            logger.error(f"Capability mapping error: {e}")
            return []

    def _classify_processes_with_ai(
        self, app: ApplicationComponent, context: str
    ) -> List[Dict[str, Any]]:
        """Use enhanced APQC hierarchy service for intelligent process classification."""
        try:
            # Use APQC hierarchy service for intelligent matching
            search_query = f"{app.name} {app.description or ''} {context}"

            # Get enhanced matches with hierarchy and rationale
            enhanced_matches = self.apqc_hierarchy.search_processes(
                query=search_query, limit=5, industry=self._detect_industry(app)
            )

            # Convert to standard format with enhanced information
            mappings = []
            for match in enhanced_matches:
                mapping = {
                    "process_id": match.process_id,
                    "process_code": match.process_code,
                    "process_name": match.process_name,
                    "similarity_score": match.similarity_score,
                    "confidence": match.confidence,
                    "match_method": "enhanced_hierarchy_search",
                    "category_level_1": match.hierarchy_path[0]["name"]
                    if match.hierarchy_path
                    else None,
                    "category_level_2": match.hierarchy_path[1]["name"]
                    if len(match.hierarchy_path) > 1
                    else None,
                    "level": match.level,
                    # Enhanced fields from hierarchy service
                    "hierarchy_path": match.hierarchy_path,
                    "parent_ids": match.parent_ids,
                    "process_category": match.process_category,
                    "industry_domain": match.industry_domain,
                    "benchmark_available": match.benchmark_available,
                    "match_rationale": {
                        "primary_reason": match.match_rationale.primary_reason,
                        "keyword_matches": match.match_rationale.keyword_matches,
                        "semantic_similarity": match.match_rationale.semantic_similarity,
                        "confidence_factors": match.match_rationale.confidence_factors,
                    },
                }
                mappings.append(mapping)

            # Auto-link parent processes for high-confidence matches
            for mapping in mappings:
                if mapping["similarity_score"] >= 0.7 and mapping["process_id"]:
                    try:
                        link_result = self.apqc_hierarchy.auto_link_parent_processes(
                            app.id, mapping["process_id"], 0.6
                        )
                        if link_result["success"]:
                            mapping["auto_linked_parents"] = link_result["linked_parents"]
                            logger.info(
                                f"Auto-linked {link_result['linked_parents']} parent processes for {app.name}"
                            )
                    except Exception as e:
                        logger.warning(f"Failed to auto-link parent processes: {e}")
                        mapping["auto_linked_parents"] = 0

            return mappings

        except Exception as e:
            logger.error(f"Enhanced process classification error: {e}")
            # Fallback to basic semantic classification
            return self._fallback_process_classification(app, context)

    def _fallback_process_classification(
        self, app: ApplicationComponent, context: str
    ) -> List[Dict[str, Any]]:
        """Fallback to basic semantic APQC classification."""
        try:
            # Use unified APQC service for classification
            classification_results = self.unified_apqc.classify(context, top_k=5)

            # Convert to standard format
            mappings = []
            for match in classification_results:
                mappings.append(
                    {
                        "process_id": match.process_id,
                        "process_code": match.process_code,
                        "process_name": match.process_name,
                        "similarity_score": float(
                            match.confidence
                        ),  # Use confidence from unified result
                        "confidence": match.confidence_level,
                        "match_method": f"fallback_{match.classification_method}",
                        "category_level_1": match.category_level_1,
                        "category_level_2": match.category_level_2,
                        "level": match.apqc_level,
                        "hierarchy_path": [],  # Not available in fallback
                        "parent_ids": [],  # Not available in fallback
                        "match_rationale": {
                            "primary_reason": f"Unified classification via {match.classification_method}"
                        },
                    }
                )

            return mappings

        except Exception as e:
            logger.error(f"Fallback process classification error: {e}")
            return []

    def _detect_industry(self, app: ApplicationComponent) -> Optional[str]:
        """Detect industry from application name and description."""
        text = f"{app.name} {app.description or ''}".lower()

        industry_keywords = {
            "banking": ["bank", "financial", "finance", "credit", "loan", "payment", "transaction"],
            "healthcare": [
                "health",
                "medical",
                "hospital",
                "patient",
                "clinical",
                "pharma",
                "biotech",
            ],
            "manufacturing": [
                "manufactur",
                "production",
                "factory",
                "plant",
                "assembly",
                "supply chain",
            ],
            "retail": ["retail", "store", "shop", "ecommerce", "sales", "customer", "pos"],
            "insurance": ["insurance", "claim", "policy", "underwrite", "risk", "premium"],
            "telecom": ["telecom", "telecommunication", "network", "carrier", "mobile", "wireless"],
        }

        for industry, keywords in industry_keywords.items():
            if any(keyword in text for keyword in keywords):
                return industry

        return None

    def _analyze_vendor_product_with_ai(
        self, app: ApplicationComponent, context: str
    ) -> Dict[str, Any]:
        """Use AI pattern matching to identify vendor products from application name and description."""
        try:
            # Use vendor product service for intelligent extraction
            extraction_result = self.vendor_product.extract_vendor_product(
                app.name, app.description or ""
            )

            # Convert to analysis result format
            analysis_result = {
                "success": extraction_result.product_id is not None,
                "vendor": {
                    "id": extraction_result.vendor_id,
                    "name": extraction_result.vendor_name,
                    "confidence": extraction_result.vendor_confidence,
                },
                "product_family": {
                    "id": extraction_result.family_id,
                    "name": extraction_result.family_name,
                    "confidence": extraction_result.family_confidence,
                },
                "product": {
                    "id": extraction_result.product_id,
                    "name": extraction_result.product_name,
                    "confidence": extraction_result.product_confidence,
                    "version": extraction_result.version,
                    "edition": extraction_result.edition,
                },
                "overall_confidence": (
                    extraction_result.vendor_confidence * 0.3
                    + extraction_result.family_confidence * 0.3
                    + extraction_result.product_confidence * 0.4
                )
                if extraction_result.product_confidence > 0
                else 0.0,
                "extraction_method": extraction_result.extraction_method,
                "rationale": extraction_result.rationale,
                "alternative_matches": extraction_result.alternative_matches,
                "ai_insights": {
                    "extraction_confidence": "high"
                    if extraction_result.product_confidence >= 0.8
                    else "medium"
                    if extraction_result.product_confidence >= 0.6
                    else "low",
                    "vendor_identified": extraction_result.vendor_confidence >= 0.5,
                    "product_family_identified": extraction_result.family_confidence >= 0.5,
                    "specific_product_identified": extraction_result.product_confidence >= 0.5,
                    "has_alternatives": len(extraction_result.alternative_matches) > 0,
                },
            }

            # Create vendor product mapping if confidence is high enough
            if analysis_result["success"] and analysis_result["overall_confidence"] >= 0.7:
                try:
                    mapping_result = self.vendor_product.create_vendor_product_mapping(
                        application_id=app.id,
                        vendor_product_id=extraction_result.product_id,
                        confidence_score=analysis_result["overall_confidence"],
                        mapping_method="ai_extracted",
                        version_deployed=extraction_result.version,
                        license_type="unknown",
                        user_id=None,  # System-generated mapping
                    )

                    if mapping_result["success"]:
                        analysis_result["mapping_created"] = True
                        analysis_result["mapping_id"] = mapping_result["mapping_id"]
                        logger.info(
                            f"Created vendor product mapping for {app.name}: {extraction_result.product_name}"
                        )
                    else:
                        analysis_result["mapping_created"] = False
                        analysis_result["mapping_error"] = mapping_result.get("error")

                except Exception as e:
                    logger.warning(f"Failed to create vendor product mapping for {app.name}: {e}")
                    analysis_result["mapping_created"] = False
                    analysis_result["mapping_error"] = str(e)

            return analysis_result

        except Exception as e:
            logger.error(f"Vendor product analysis error for {app.name}: {e}")
            return {
                "success": False,
                "error": str(e),
                "vendor": {"id": None, "name": "Unknown", "confidence": 0.0},
                "product_family": {"id": None, "name": "Unknown", "confidence": 0.0},
                "product": {"id": None, "name": "Unknown", "confidence": 0.0},
                "overall_confidence": 0.0,
                "extraction_method": "error",
                "rationale": [f"Analysis error: {str(e)}"],
                "alternative_matches": [],
                "ai_insights": {"extraction_confidence": "error"},
            }

    def _generate_archimate_with_ai(
        self, app: ApplicationComponent, context: str, mode: str = "standard"
    ) -> List[Dict[str, Any]]:
        """Multi-stage ArchiMate generation with configurable detail level (quick/standard/comprehensive)."""
        try:
            logger.info(
                f"🏗️ Starting multi-stage ArchiMate generation for {app.name} (mode: {mode})..."
            )

            # Pattern Detection: Check if application matches a known pattern
            pattern_match = self.pattern_library.detect_pattern(app)

            if pattern_match and pattern_match["confidence"] >= 0.7:
                # Apply pattern for base architecture
                logger.info(f"🎨 Applying pattern: {pattern_match['pattern_name']}")
                pattern_result = self.pattern_library.apply_pattern(
                    pattern_match["pattern_id"], app
                )
                base_elements = pattern_result.get("elements", [])
                base_relationships = pattern_result.get("relationships", [])

                # Enhance pattern with additional layer-specific elements
                logger.info("🔧 Enhancing pattern with additional elements...")
            else:
                # No pattern match, start with empty base
                base_elements = []
                base_relationships = []

            # Create mode-specific layer generators
            from app.services.archimate_layer_generators import ArchiMateLayerGenerators

            mode_generators = ArchiMateLayerGenerators(mode=mode)

            # Build comprehensive context
            app_context = mode_generators.build_comprehensive_app_context(app, context)

            # Stage 1: Motivation Layer
            motivation_elements = mode_generators.generate_motivation_layer(app, app_context)
            logger.info(
                f"✅ Stage 1: Generated {len(motivation_elements)} Motivation layer elements"
            )

            # Stage 2: Strategy Layer
            strategy_elements = mode_generators.generate_strategy_layer(
                app, app_context, motivation_elements
            )
            logger.info(f"✅ Stage 2: Generated {len(strategy_elements)} Strategy layer elements")

            # Stage 3: Business Layer
            business_elements = mode_generators.generate_business_layer(
                app, app_context, strategy_elements
            )
            logger.info(f"✅ Stage 3: Generated {len(business_elements)} Business layer elements")

            # Stage 4: Application Layer
            application_elements = mode_generators.generate_application_layer(
                app, app_context, business_elements
            )
            logger.info(
                f"✅ Stage 4: Generated {len(application_elements)} Application layer elements"
            )

            # Stage 5: Technology Layer
            technology_elements = mode_generators.generate_technology_layer(
                app, app_context, application_elements
            )
            logger.info(
                f"✅ Stage 5: Generated {len(technology_elements)} Technology layer elements"
            )

            # Stage 6: Physical Layer
            physical_elements = mode_generators.generate_physical_layer(app, app_context)
            logger.info(f"✅ Stage 6: Generated {len(physical_elements)} Physical layer elements")

            # Stage 7: Implementation Layer
            implementation_elements = mode_generators.generate_implementation_layer(
                app, app_context
            )
            logger.info(
                f"✅ Stage 7: Generated {len(implementation_elements)} Implementation layer elements"
            )

            # Combine all elements (pattern base + layer-generated)
            all_elements = (
                base_elements
                + motivation_elements
                + strategy_elements
                + business_elements
                + application_elements
                + technology_elements
                + physical_elements
                + implementation_elements
            )

            logger.info(f"🎯 Total ArchiMate elements generated: {len(all_elements)}")
            if base_elements:
                logger.info(f"   - Pattern Base: {len(base_elements)}")
            logger.info(f"   - Motivation: {len(motivation_elements)}")
            logger.info(f"   - Strategy: {len(strategy_elements)}")
            logger.info(f"   - Business: {len(business_elements)}")
            logger.info(f"   - Application: {len(application_elements)}")
            logger.info(f"   - Technology: {len(technology_elements)}")
            logger.info(f"   - Physical: {len(physical_elements)}")
            logger.info(f"   - Implementation: {len(implementation_elements)}")

            # Validate minimum element count (target: 100 - 200)
            if len(all_elements) < 80:
                logger.warning(
                    f"⚠️ Generated only {len(all_elements)} elements (target: 100 - 200). Architecture may be incomplete."
                )
            elif len(all_elements) >= 100:
                logger.info(f"✅ Achieved target element count: {len(all_elements)} elements")

            # Stage 8: Relationship Generation (80 - 150 relationships)
            generated_relationships = self.relationship_generator.generate_relationships(
                all_elements, app.name
            )

            # Combine pattern relationships with generated relationships
            relationships = base_relationships + generated_relationships
            logger.info(f"✅ Stage 8: Generated {len(generated_relationships)} relationships")
            if base_relationships:
                logger.info(f"   - Pattern Base: {len(base_relationships)} relationships")
            logger.info(f"   - Total: {len(relationships)} relationships")

            # Validate relationship count (target: 150 - 300)
            if len(relationships) < 100:
                logger.warning(
                    f"⚠️ Generated only {len(relationships)} relationships (target: 150 - 300). Traceability may be incomplete."
                )
            elif len(relationships) >= 150:
                logger.info(
                    f"✅ Achieved target relationship count: {len(relationships)} relationships"
                )

            # Store relationships in elements for return (will be processed separately)
            for elem in all_elements:
                elem["_relationships"] = relationships

            return all_elements

        except Exception as e:
            logger.error(f"❌ Multi-stage ArchiMate generation error for {app.name}: {e}")
            return []

    # Layer generation helper methods (delegate to ArchiMateLayerGenerators)
    def _build_comprehensive_app_context(self, app: ApplicationComponent, context: str) -> str:
        """Build comprehensive application context for ArchiMate generation."""
        return self.layer_generators.build_comprehensive_app_context(app, context)

    def _generate_motivation_layer(
        self, app: ApplicationComponent, app_context: str
    ) -> List[Dict[str, Any]]:
        """Generate Motivation layer elements."""
        return self.layer_generators.generate_motivation_layer(app, app_context)

    def _generate_strategy_layer(
        self, app: ApplicationComponent, app_context: str, motivation_elements: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate Strategy layer elements."""
        return self.layer_generators.generate_strategy_layer(app, app_context, motivation_elements)

    def _generate_business_layer(
        self, app: ApplicationComponent, app_context: str, strategy_elements: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate Business layer elements."""
        return self.layer_generators.generate_business_layer(app, app_context, strategy_elements)

    def _generate_application_layer(
        self, app: ApplicationComponent, app_context: str, business_elements: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate Application layer elements."""
        return self.layer_generators.generate_application_layer(app, app_context, business_elements)

    def _generate_technology_layer(
        self, app: ApplicationComponent, app_context: str, application_elements: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate Technology layer elements."""
        return self.layer_generators.generate_technology_layer(
            app, app_context, application_elements
        )

    def _generate_physical_layer(
        self, app: ApplicationComponent, app_context: str
    ) -> List[Dict[str, Any]]:
        """Generate Physical layer elements."""
        return self.layer_generators.generate_physical_layer(app, app_context)

    def _generate_implementation_layer(
        self, app: ApplicationComponent, app_context: str
    ) -> List[Dict[str, Any]]:
        """Generate Implementation layer elements."""
        return self.layer_generators.generate_implementation_layer(app, app_context)

    def bulk_ai_analyze_batch(
        self,
        application_ids: List[int],
        confidence_threshold: float = 0.6,
        user_id: int = None,
        job_name: str = "AI Bulk Analysis",
    ) -> Dict[str, Any]:
        """
        Perform bulk AI analysis of multiple applications using batch processing (async job).

        Args:
            application_ids: List of application IDs to analyze
            confidence_threshold: Minimum confidence threshold for auto-creation
            user_id: User ID initiating the batch job
            job_name: Name for the batch job

        Returns:
            Dictionary with batch job creation result
        """
        try:
            from app.models.application_portfolio import ApplicationComponent

            # Get applications
            applications = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(application_ids)
            ).all()

            if not applications:
                return {"success": False, "error": "No applications found for the provided IDs"}

            # Prepare batch job items
            items = []
            for app in applications:
                items.append(
                    {
                        "id": app.id,
                        "name": app.name,
                        "type": "application",
                        "description": app.description,
                        "technology_stack": app.technology_stack,
                        "business_domain": app.business_domain,
                        "business_criticality": app.business_criticality,
                    }
                )

            # Create batch job configuration
            config = BatchJobConfig(
                job_name=f"{job_name} - {len(applications)} Applications",
                job_type="ai_import",
                items=items,
                confidence_threshold=confidence_threshold,
                auto_retry=True,
                max_retries=3,
                parallel_processing=False,  # Start with sequential processing
                batch_size=10,
                checkpoint_interval=5,
                timeout_per_item=300,
                priority=5,
                user_id=user_id,
            )

            # Create and start batch job
            job_result = self.batch_processing.create_batch_job(config)

            if job_result["success"]:
                # Start the job
                start_result = self.batch_processing.start_batch_job(job_result["job_id"])

                return {
                    "success": True,
                    "batch_job_id": job_result["job_id"],
                    "job_name": job_result["job_name"],
                    "total_applications": len(applications),
                    "job_started": start_result["success"],
                    "status": "started" if start_result["success"] else "created",
                    "job_id": job_result["job_id"],
                }
            else:
                return job_result

        except Exception as e:
            logger.error(f"Error creating bulk AI analysis batch job: {e}")
            return {"success": False, "error": str(e)}

    def get_bulk_analysis_status(self, job_id: int) -> Dict[str, Any]:
        """
        Get status of bulk AI analysis batch job.

        Args:
            job_id: Batch job ID

        Returns:
            Dictionary with job status and progress
        """
        try:
            # Get job progress
            progress = self.batch_processing.get_job_progress(job_id)

            if not progress:
                return {"success": False, "error": "Batch job not found"}

            # Get job results if completed
            results = None
            if progress.status in ["completed", "failed", "cancelled"]:
                results = self.batch_processing.get_job_results(job_id)

            return {
                "success": True,
                "job_id": job_id,
                "job_name": progress.job_name,
                "status": progress.status,
                "progress": {
                    "total_applications": progress.total_items,
                    "processed_applications": progress.processed_items,
                    "successful_applications": progress.successful_items,
                    "failed_applications": progress.failed_items,
                    "progress_percentage": progress.progress_percentage,
                    "applications_per_second": progress.items_per_second,
                    "estimated_completion": progress.estimated_completion_time.isoformat()
                    if progress.estimated_completion_time
                    else None,
                    "current_application": progress.current_item_name,
                    "error_count": progress.error_count,
                    "last_error": progress.last_error_message,
                    "start_time": progress.start_time.isoformat(),
                    "elapsed_time": str(progress.elapsed_time),
                },
                "results": results.to_dict() if results else None,
            }

        except Exception as e:
            logger.error(f"Error getting bulk analysis status for job {job_id}: {e}")
            return {"success": False, "error": str(e)}

    def cancel_bulk_analysis(self, job_id: int) -> Dict[str, Any]:
        """
        Cancel a bulk AI analysis batch job.

        Args:
            job_id: Batch job ID

        Returns:
            Dictionary with cancellation result
        """
        try:
            result = self.batch_processing.cancel_batch_job(job_id)

            return result

        except Exception as e:
            logger.error(f"Error cancelling bulk analysis job {job_id}: {e}")
            return {"success": False, "error": str(e)}

    def get_bulk_analysis_results(self, job_id: int) -> Dict[str, Any]:
        """
        Get detailed results of completed bulk AI analysis.

        Args:
            job_id: Batch job ID

        Returns:
            Dictionary with detailed results
        """
        try:
            results = self.batch_processing.get_job_results(job_id)

            if not results:
                return {"success": False, "error": "Batch job not found or not completed"}

            # Process results to extract AI analysis specific data
            application_results = []
            for item_result in results.results:
                if item_result.get("result"):
                    # Extract AI analysis data from result
                    ai_data = item_result["result"]
                    application_results.append(
                        {
                            "application_id": item_result["item_sequence"],
                            "application_name": item_result["item_name"],
                            "status": item_result["status"],
                            "confidence_score": item_result.get("confidence_score", 0.0),
                            "capability_mappings": ai_data.get("capability_mappings", []),
                            "process_mappings": ai_data.get("process_mappings", []),
                            "vendor_product_analysis": ai_data.get("vendor_product_analysis", {}),
                            "taxonomy_validation": ai_data.get("taxonomy_validation", {}),
                            "archimate_elements": ai_data.get("archimate_elements", []),
                            "processing_duration": item_result.get("processing_duration", 0),
                            "warnings": item_result.get("warnings", []),
                            "recommendations": item_result.get("recommendations", []),
                        }
                    )

            return {
                "success": True,
                "job_id": results.job_id,
                "job_name": results.job_name,
                "status": results.status,
                "summary": {
                    "total_applications": results.total_items,
                    "processed_applications": results.processed_items,
                    "successful_applications": results.successful_items,
                    "failed_applications": results.failed_items,
                    "success_rate": results.success_rate,
                    "total_processing_time": results.total_processing_time,
                    "average_processing_time": results.average_items_per_second,
                },
                "application_results": application_results,
                "errors": results.errors,
                "checkpoints_created": results.checkpoints_created,
                "recovery_attempts": results.recovery_attempts,
            }

        except Exception as e:
            logger.error(f"Error getting bulk analysis results for job {job_id}: {e}")
            return {"success": False, "error": str(e)}

    def _validate_confidence_thresholds(
        self, app: ApplicationComponent, result: AIImportResult
    ) -> List[Dict[str, Any]]:
        """Validate confidence thresholds and add items to review queue if needed."""
        review_queue_items = []

        try:
            # Validate capability mappings
            if result.capability_mappings:
                for mapping in result.capability_mappings:
                    confidence_score = mapping.get("confidence_score", 0.0)
                    confidence_factors = {
                        "name_similarity": mapping.get("similarity_score", 0.0),
                        "description_match": mapping.get("confidence_score", 0.0),
                        "business_alignment": mapping.get("confidence_score", 0.0),
                    }

                    # Create review queue item data
                    item_data = ReviewQueueItemData(
                        item_type="capability_mapping",
                        item_id=app.id,
                        item_name=f"{app.name} - {mapping.get('capability_name', 'Unknown')}",
                        item_data={
                            "application_id": app.id,
                            "application_name": app.name,
                            "capability_id": mapping.get("capability_id"),
                            "capability_name": mapping.get("capability_name"),
                            "confidence_score": confidence_score,
                            "mapping_rationale": mapping.get("rationale", ""),
                        },
                        confidence_score=confidence_score,
                        confidence_factors=confidence_factors,
                        ai_model_used="LLM-capability-analysis",
                        generation_timestamp=datetime.utcnow(),
                        threshold_name="capability_mapping",
                        context_type="capability_level",
                        context_value=mapping.get("capability_level", "unknown"),
                        domain=mapping.get("domain", "business"),
                    )

                    # Evaluate confidence threshold
                    evaluation_result = self.confidence_review.evaluate_confidence_threshold(
                        item_data
                    )

                    if evaluation_result["success"] and evaluation_result["requires_review"]:
                        # Add to review queue
                        queue_result = self.confidence_review.add_to_review_queue(
                            item_data, evaluation_result
                        )
                        review_queue_items.append(
                            {
                                "item_type": "capability_mapping",
                                "item_name": item_data.item_name,
                                "confidence_score": confidence_score,
                                "evaluation_result": evaluation_result,
                                "queue_result": queue_result,
                                "requires_review": True,
                            }
                        )
                    else:
                        review_queue_items.append(
                            {
                                "item_type": "capability_mapping",
                                "item_name": item_data.item_name,
                                "confidence_score": confidence_score,
                                "evaluation_result": evaluation_result,
                                "requires_review": False,
                            }
                        )

            # Validate process mappings
            if result.process_mappings:
                for mapping in result.process_mappings:
                    confidence_score = mapping.get("similarity_score", 0.0)
                    confidence_factors = {
                        "semantic_similarity": mapping.get("similarity_score", 0.0),
                        "keyword_match": mapping.get("similarity_score", 0.0),
                        "hierarchy_match": mapping.get("similarity_score", 0.0),
                    }

                    # Create review queue item data
                    item_data = ReviewQueueItemData(
                        item_type="process_classification",
                        item_id=app.id,
                        item_name=f"{app.name} - {mapping.get('process_name', 'Unknown')}",
                        item_data={
                            "application_id": app.id,
                            "application_name": app.name,
                            "process_id": mapping.get("process_id"),
                            "process_name": mapping.get("process_name"),
                            "process_code": mapping.get("process_code"),
                            "similarity_score": confidence_score,
                        },
                        confidence_score=confidence_score,
                        confidence_factors=confidence_factors,
                        ai_model_used="semantic-classification",
                        generation_timestamp=datetime.utcnow(),
                        threshold_name="apqc_process_classification",
                        context_type="process_level",
                        context_value=str(mapping.get("level", "unknown")),
                        domain="business",
                    )

                    # Evaluate confidence threshold
                    evaluation_result = self.confidence_review.evaluate_confidence_threshold(
                        item_data
                    )

                    if evaluation_result["success"] and evaluation_result["requires_review"]:
                        # Add to review queue
                        queue_result = self.confidence_review.add_to_review_queue(
                            item_data, evaluation_result
                        )
                        review_queue_items.append(
                            {
                                "item_type": "process_classification",
                                "item_name": item_data.item_name,
                                "confidence_score": confidence_score,
                                "evaluation_result": evaluation_result,
                                "queue_result": queue_result,
                                "requires_review": True,
                            }
                        )
                    else:
                        review_queue_items.append(
                            {
                                "item_type": "process_classification",
                                "item_name": item_data.item_name,
                                "confidence_score": confidence_score,
                                "evaluation_result": evaluation_result,
                                "requires_review": False,
                            }
                        )

            # Validate vendor product analysis
            if result.vendor_product_analysis:
                vendor_analysis = result.vendor_product_analysis
                confidence_score = vendor_analysis.get("overall_confidence", 0.0)
                confidence_factors = {
                    "vendor_confidence": vendor_analysis.get("vendor", {}).get("confidence", 0.0),
                    "product_confidence": vendor_analysis.get("product", {}).get("confidence", 0.0),
                    "extraction_confidence": vendor_analysis.get("overall_confidence", 0.0),
                }

                # Create review queue item data
                item_data = ReviewQueueItemData(
                    item_type="vendor_analysis",
                    item_id=app.id,
                    item_name=f"{app.name} - {vendor_analysis.get('vendor', {}).get('name', 'Unknown')}",
                    item_data={
                        "application_id": app.id,
                        "application_name": app.name,
                        "vendor_id": vendor_analysis.get("vendor", {}).get("id"),
                        "vendor_name": vendor_analysis.get("vendor", {}).get("name"),
                        "product_id": vendor_analysis.get("product", {}).get("id"),
                        "product_name": vendor_analysis.get("product", {}).get("name"),
                        "overall_confidence": confidence_score,
                    },
                    confidence_score=confidence_score,
                    confidence_factors=confidence_factors,
                    ai_model_used="vendor-pattern-matching",
                    generation_timestamp=datetime.utcnow(),
                    threshold_name="vendor_product_analysis",
                    context_type="vendor_tier",
                    context_value=vendor_analysis.get("vendor", {}).get("name", "unknown"),
                    domain="technology",
                )

                # Evaluate confidence threshold
                evaluation_result = self.confidence_review.evaluate_confidence_threshold(item_data)

                if evaluation_result["success"] and evaluation_result["requires_review"]:
                    # Add to review queue
                    queue_result = self.confidence_review.add_to_review_queue(
                        item_data, evaluation_result
                    )
                    review_queue_items.append(
                        {
                            "item_type": "vendor_analysis",
                            "item_name": item_data.item_name,
                            "confidence_score": confidence_score,
                            "evaluation_result": evaluation_result,
                            "queue_result": queue_result,
                            "requires_review": True,
                        }
                    )
                else:
                    review_queue_items.append(
                        {
                            "item_type": "vendor_analysis",
                            "item_name": item_data.item_name,
                            "confidence_score": confidence_score,
                            "evaluation_result": evaluation_result,
                            "requires_review": False,
                        }
                    )

            # Validate taxonomy validation
            if result.taxonomy_validation:
                taxonomy_validation = result.taxonomy_validation
                confidence_score = taxonomy_validation.get("overall_confidence", 0.0)
                confidence_factors = {
                    "validation_confidence": taxonomy_validation.get("overall_confidence", 0.0),
                    "compliance_score": 1.0 if taxonomy_validation.get("success", False) else 0.0,
                    "violation_count": len(
                        taxonomy_validation.get("validation_result", {}).get("violations", [])
                    ),
                }

                # Create review queue item data
                item_data = ReviewQueueItemData(
                    item_type="taxonomy_validation",
                    item_id=app.id,
                    item_name=f"{app.name} - Taxonomy Validation",
                    item_data={
                        "application_id": app.id,
                        "application_name": app.name,
                        "validation_result": taxonomy_validation.get("validation_result", {}),
                        "overall_confidence": confidence_score,
                    },
                    confidence_score=confidence_score,
                    confidence_factors=confidence_factors,
                    ai_model_used="capability-taxonomy-validation",
                    generation_timestamp=datetime.utcnow(),
                    threshold_name="taxonomy_validation",
                    context_type="capability_level",
                    context_value=app.level,
                    domain=app.domain,
                )

                # Evaluate confidence threshold
                evaluation_result = self.confidence_review.evaluate_confidence_threshold(item_data)

                if evaluation_result["success"] and evaluation_result["requires_review"]:
                    # Add to review queue
                    queue_result = self.confidence_review.add_to_review_queue(
                        item_data, evaluation_result
                    )
                    review_queue_items.append(
                        {
                            "item_type": "taxonomy_validation",
                            "item_name": item_data.item_name,
                            "confidence_score": confidence_score,
                            "evaluation_result": evaluation_result,
                            "queue_result": queue_result,
                            "requires_review": True,
                        }
                    )
                else:
                    review_queue_items.append(
                        {
                            "item_type": "taxonomy_validation",
                            "item_name": item_data.item_name,
                            "confidence_score": confidence_score,
                            "evaluation_result": evaluation_result,
                            "requires_review": False,
                        }
                    )

            return review_queue_items

        except Exception as e:
            logger.error(f"Error validating confidence thresholds for {app.name}: {e}")
            return []

        # NOTE: an older analyze_file_data_for_preview (176 lines, signature without
        # archimate_mode and with confidence_threshold=0.6) was defined here. Python
        # bound the name to the LATER definition below, so this one had been dead —
        # every caller already got the newer version. Removed so the two cannot
        # diverge further and so the wrong one is not edited by mistake.

    def _build_file_data_context(self, app_data: Dict[str, Any]) -> str:
        """Build comprehensive text context from file data for AI analysis."""
        context_parts = []

        # Basic info
        name = app_data.get("name", "")
        if name:
            context_parts.append(f"Application: {name}")

        description = app_data.get("description", "")
        if description:
            context_parts.append(f"Description: {description}")

        # Technical details
        technology = app_data.get("technology_stack", "")
        if technology:
            context_parts.append(f"Technology: {technology}")

        vendor = app_data.get("vendor_name", "")
        if vendor:
            context_parts.append(f"Vendor: {vendor}")

        # Business context
        domain = app_data.get("business_domain", "")
        if domain:
            context_parts.append(f"Business Domain: {domain}")

        criticality = app_data.get("business_criticality", "")
        if criticality:
            context_parts.append(f"Criticality: {criticality}")

        # File-specific data (most valuable for AI)
        functions = app_data.get("business_functions", "")
        if functions:
            context_parts.append(f"Business Functions: {functions}")

        capabilities = app_data.get("capabilities", "")
        if capabilities:
            context_parts.append(f"Capabilities: {capabilities}")

        apqc_codes = app_data.get("apqc_codes", "")
        if apqc_codes:
            context_parts.append(f"APQC Codes: {apqc_codes}")

        return "\n".join(filter(None, context_parts))

    def _map_capabilities_with_ai_from_file(
        self, app_data: Dict[str, Any], context: str
    ) -> List[Dict[str, Any]]:
        """Use LLM to map file data to business capabilities."""
        # Use existing capability mapping logic but with file data
        return self._map_capabilities_with_ai(None, context)  # Pass None for app_id, use context

    def _classify_processes_with_ai_from_file(
        self, app_data: Dict[str, Any], context: str
    ) -> List[Dict[str, Any]]:
        """Use semantic embeddings for APQC process classification from file data."""
        try:
            # Use unified APQC service for classification
            classification_results = self.unified_apqc.classify(context, top_k=5)

            # Convert to standard format
            mappings = []
            for match in classification_results:
                mappings.append(
                    {
                        "process_id": match.process_id,
                        "process_code": match.process_code,
                        "process_name": match.process_name,
                        "similarity_score": float(match.confidence),
                        "confidence": match.confidence_level,
                        "match_method": match.classification_method,
                        "category_level_1": match.category_level_1,
                        "category_level_2": match.category_level_2,
                        "level": match.apqc_level,
                    }
                )

            return mappings

        except Exception as e:
            logger.error(f"Process classification error from file: {e}")
            return []

    def _generate_archimate_with_ai_from_file(
        self, app_data: Dict[str, Any], context: str
    ) -> List[Dict[str, Any]]:
        """Use LLM to generate ArchiMate elements from file data."""

        # Create a mock application object for compatibility
        class MockApp:
            def __init__(self, data):
                self.name = data.get("name", "Unknown Application")
                self.description = data.get("description", "")
                self.application_functions_text = data.get("business_functions", "")
                self.technology_stack = data.get("technology_stack", "")

        mock_app = MockApp(app_data)
        return self._generate_archimate_with_ai(mock_app, context)

    def _analyze_vendor_from_file(self, app_data: Dict[str, Any], context: str) -> Dict[str, Any]:
        """Analyze vendor information from file data."""
        vendor_name = app_data.get("vendor_name", "")
        if not vendor_name:
            return {}

        try:
            # Use LLM for vendor analysis if available
            if self.llm_service:
                prompt = f"""
Analyze this vendor for enterprise architecture assessment:

VENDOR: {vendor_name}
APPLICATION CONTEXT:
{context}

Provide analysis in JSON format:
{{
    "vendor_type": "enterprise|commercial|open_source|custom",
    "market_presence": "high|medium|low",
    "reliability_score": 0.0 - 1.0,
    "integration_complexity": "low|medium|high",
    "recommendations": ["list of recommendations"]
}}
"""

                response = self.llm_service.generate_from_prompt(prompt)

                try:
                    # Extract JSON from response
                    cleaned_response = response.strip()
                    if "{" in cleaned_response:
                        start_idx = cleaned_response.find("{")
                        end_idx = cleaned_response.rfind("}") + 1
                        if start_idx != -1 and end_idx > start_idx:
                            cleaned_response = cleaned_response[start_idx:end_idx]

                    vendor_analysis = json.loads(cleaned_response)
                    vendor_analysis["vendor_name"] = vendor_name
                    return vendor_analysis

                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse vendor analysis JSON for {vendor_name}")
                    return {"vendor_name": vendor_name, "analysis_failed": True}

        except Exception as e:
            logger.error(f"Vendor analysis error for {vendor_name}: {e}")

        # Fallback basic vendor info
        return {
            "vendor_name": vendor_name,
            "vendor_type": "unknown",
            "market_presence": "unknown",
            "analysis_failed": False,
        }

    def bulk_ai_analyze(
        self, max_applications: int = 50, confidence_threshold: float = 0.7,
        application_ids: list = None, write_immediately: bool = False,
        map_capabilities: bool = True, map_processes: bool = True,
        generate_archimate: bool = True, created_by: str = "auto_map",
    ) -> Dict[str, Any]:
        """
        Bulk AI analysis of applications needing mapping.

        Args:
            max_applications: Maximum applications to process
            confidence_threshold: Minimum confidence for auto-creation
            application_ids: When given, analyze exactly these applications
                (still filtered to ones with imported capabilities) instead of
                the newest `max_applications` rows. A caller naming specific
                applications - e.g. "map the batch I just imported" - must get
                those applications back, not whichever rows are newest overall.
            write_immediately: When True, persist high-confidence mappings for
                each application as it is analyzed (see the per-app commit
                below). Defaults to False: analysis alone must never write -
                a caller previewing results, or one that has not set
                auto_create, must get pure analysis back. The one caller that
                wants writes (the comprehensive-auto-map route) opts in
                explicitly and only when the request actually asked to create
                mappings and is not a preview.
            map_capabilities/map_processes/generate_archimate: When
                write_immediately is True, these gate which categories are
                persisted; a category the caller did not ask for is never
                built into the create_ai_mappings call, let alone written.
            created_by: Attribution recorded on any mapping this creates.

        Returns:
            Comprehensive results with statistics and application details
        """
        try:
            # Find applications needing AI analysis
            query = ApplicationComponent.query.filter(
                ApplicationComponent.imported_capabilities.isnot(None)
            )
            if application_ids:
                query = query.filter(ApplicationComponent.id.in_(application_ids))
            apps = (
                query.order_by(ApplicationComponent.created_at.desc())
                .limit(max_applications)
                .all()
            )

            results = {
                "total_analyzed": 0,
                "capability_mappings_found": 0,
                "process_mappings_found": 0,
                "archimate_elements_generated": 0,
                "high_confidence_mappings": 0,
                "applications": [],
                "processing_stats": {"avg_processing_time_ms": 0, "ai_models_used": set()},
            }

            # If no applications found, return early with empty results
            if not apps:
                logger.info("No applications with imported capabilities found for AI analysis")
                results["processing_stats"]["ai_models_used"] = []
                return results

            total_processing_time = 0

            for app in apps:
                try:
                    ai_result = self.analyze_application_for_ai_mapping(app.id)

                    # Count high-confidence mappings
                    high_conf_count = 0
                    if ai_result.capability_mappings:
                        high_conf_count = sum(
                            1
                            for m in ai_result.capability_mappings
                            if m.get("confidence_score", 0) >= confidence_threshold
                        )

                    # Save high-confidence mappings immediately, but only when
                    # the caller opted into writes and only for the categories
                    # it asked for - see write_immediately above. This ensures
                    # progress is not lost if interrupted or tokens run out,
                    # without turning a preview or a plain analysis call into
                    # a write.
                    mappings_saved = {"capabilities": 0, "processes": 0, "archimate": 0}
                    if write_immediately:
                        try:
                            high_conf_capabilities = [
                                m
                                for m in ai_result.capability_mappings
                                if map_capabilities
                                and m.get("confidence_score", 0) >= confidence_threshold
                            ]
                            high_conf_processes = [
                                m
                                for m in ai_result.process_mappings
                                if map_processes
                                and m.get("similarity_score", 0) >= confidence_threshold
                            ]
                            archimate_to_save = (
                                ai_result.archimate_elements[:5]
                                if generate_archimate
                                else []
                            )

                            if high_conf_capabilities or high_conf_processes or archimate_to_save:
                                save_result = self.create_ai_mappings(
                                    application_id=app.id,
                                    capability_mappings=high_conf_capabilities,
                                    process_mappings=high_conf_processes,
                                    archimate_elements=archimate_to_save,
                                    created_by=created_by,
                                )
                                mappings_saved["capabilities"] = save_result.get(
                                    "capability_mappings_created", 0
                                )
                                mappings_saved["processes"] = save_result.get(
                                    "process_mappings_created", 0
                                )
                                mappings_saved["archimate"] = save_result.get(
                                    "archimate_elements_created", 0
                                )

                                # Commit after each application to prevent data loss
                                db.session.commit()
                                logger.info(
                                    f"Saved {mappings_saved['capabilities']} capabilities, {mappings_saved['processes']} processes for {app.name}"
                                )
                        except Exception as save_error:
                            logger.error(f"Failed to save mappings for {app.name}: {save_error}")
                            db.session.rollback()

                    # Update statistics
                    results["total_analyzed"] += 1
                    results["capability_mappings_found"] += len(ai_result.capability_mappings)
                    results["process_mappings_found"] += len(ai_result.process_mappings)
                    results["archimate_elements_generated"] += len(ai_result.archimate_elements)
                    results["high_confidence_mappings"] += high_conf_count

                    total_processing_time += ai_result.processing_time_ms
                    results["processing_stats"]["ai_models_used"].update(ai_result.ai_models_used)

                    # Store application result with save status
                    results["applications"].append(
                        {
                            "application_id": ai_result.application_id,
                            "application_name": ai_result.application_name,
                            "capability_mappings": ai_result.capability_mappings,
                            "process_mappings": ai_result.process_mappings,
                            "archimate_elements": ai_result.archimate_elements,
                            "avg_capability_confidence": ai_result.avg_capability_confidence,
                            "avg_process_confidence": ai_result.avg_process_confidence,
                            "high_confidence_mappings": high_conf_count,
                            "processing_time_ms": ai_result.processing_time_ms,
                            "warnings": ai_result.warnings,
                            "saved_to_db": mappings_saved,  # NEW: Track what was actually saved
                            "status": "saved"
                            if sum(mappings_saved.values()) > 0
                            else "analyzed_only",
                        }
                    )

                except Exception as e:
                    logger.error(f"Bulk AI analysis failed for app {app.id}: {e}")
                    results["applications"].append(
                        {
                            "application_id": app.id,
                            "application_name": app.name,
                            "error": str(e),
                            "warnings": [f"Analysis failed: {str(e)}"],
                        }
                    )

            # Calculate averages
            if results["total_analyzed"] > 0:
                results["processing_stats"]["avg_processing_time_ms"] = (
                    total_processing_time // results["total_analyzed"]
                )
            results["processing_stats"]["ai_models_used"] = list(
                results["processing_stats"]["ai_models_used"]
            )

            # Log comprehensive summary
            logger.info(
                f"""
🤖 BULK AI ANALYSIS COMPLETE:
📊 Applications Analyzed: {results['total_analyzed']}
🎯 Capability Mappings: {results['capability_mappings_found']}
🔄 Process Mappings: {results['process_mappings_found']}
🏗️  ArchiMate Elements: {results['archimate_elements_generated']}
⭐ High Confidence Mappings: {results['high_confidence_mappings']}
⏱️  Avg Processing Time: {results['processing_stats']['avg_processing_time_ms']}ms
🤖 AI Models Used: {', '.join(results['processing_stats']['ai_models_used'])}
"""
            )

            return results

        except Exception as e:
            logger.error(f"Critical error in bulk_ai_analyze: {e}", exc_info=True)
            # Return a safe error response instead of raising
            return {
                "total_analyzed": 0,
                "capability_mappings_found": 0,
                "process_mappings_found": 0,
                "archimate_elements_generated": 0,
                "high_confidence_mappings": 0,
                "applications": [],
                "processing_stats": {"avg_processing_time_ms": 0, "ai_models_used": []},
                "error": str(e),
                "critical_error": True,
            }

    def create_ai_mappings(
        self,
        application_id: int,
        capability_mappings: List[Dict] = None,
        process_mappings: List[Dict] = None,
        archimate_elements: List[Dict] = None,
        created_by: str = "ai_import",
    ) -> Dict[str, Any]:
        """
        Create actual database mappings from AI suggestions.

        Args:
            application_id: Application to create mappings for
            capability_mappings: List of capability mappings to create
            process_mappings: List of process mappings to create
            archimate_elements: List of ArchiMate elements to create
            created_by: User/agent creating the mappings

        Returns:
            Creation results with counts and any errors
        """
        results = {
            "capability_mappings_created": 0,
            "process_mappings_created": 0,
            "archimate_elements_created": 0,
            "errors": [],
        }

        try:
            # Create capability mappings
            if capability_mappings:
                for mapping in capability_mappings:
                    try:
                        # UnifiedApplicationCapabilityMapping has no application_id/
                        # capability_id/confidence_score/mapping_method/rationale/
                        # created_by columns (app/models/unified_application_capability_mapping.py)
                        # - this constructor call raised TypeError on every
                        # invocation, so no capability mapping this wrote ever
                        # actually persisted. Use the columns that exist.
                        existing = UnifiedApplicationCapabilityMapping.query.filter_by(
                            application_component_id=application_id,
                            unified_capability_id=mapping["capability_id"],
                        ).first()

                        if not existing:
                            confidence = mapping.get("confidence_score", 0.5)
                            new_mapping = UnifiedApplicationCapabilityMapping(
                                application_component_id=application_id,
                                unified_capability_id=mapping["capability_id"],
                                support_quality=max(1, min(5, round(confidence * 5))),
                                notes=(mapping.get("rationale") or "")
                                + f" (AI mapping, confidence {confidence:.2f}, by {created_by})",
                            )
                            db.session.add(new_mapping)
                            results["capability_mappings_created"] += 1
                    except Exception as e:
                        results["errors"].append(f"Capability mapping error: {str(e)}")

            # Create process mappings
            if process_mappings:
                for mapping in process_mappings:
                    try:
                        # ProcessApplicationMapping's process FK column is
                        # apqc_process_id, not process_id, and it has no
                        # confidence_score/mapping_method/rationale/created_by
                        # columns either - same class of defect as above.
                        existing = ProcessApplicationMapping.query.filter_by(
                            application_id=application_id,
                            apqc_process_id=mapping["process_id"],
                        ).first()

                        if not existing:
                            similarity = mapping.get("similarity_score", 0.5)
                            new_mapping = ProcessApplicationMapping(
                                application_id=application_id,
                                apqc_process_id=mapping["process_id"],
                                process_coverage=max(0, min(100, round(similarity * 100))),
                                assessment_notes=(
                                    f"AI classification with {mapping.get('confidence', 'medium')} "
                                    f"confidence, similarity {similarity:.2f}, by {created_by}"
                                ),
                                assessor=created_by,
                            )
                            db.session.add(new_mapping)
                            results["process_mappings_created"] += 1
                    except Exception as e:
                        results["errors"].append(f"Process mapping error: {str(e)}")

            # Create ArchiMate elements
            if archimate_elements:
                archimate_service = self._get_archimate_service()

                for element_data in archimate_elements:
                    try:
                        # Create ArchiMate element through service
                        element = archimate_service.create_element_from_dict(
                            element_data, created_by=created_by
                        )
                        if element:
                            results["archimate_elements_created"] += 1
                    except Exception as e:
                        results["errors"].append(f"ArchiMate element error: {str(e)}")

            # Commit all changes
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            results["errors"].append(f"Database error: {str(e)}")
            logger.error(f"Failed to create AI mappings: {e}")

        return results

    def _validate_capability_taxonomy_with_ai(self, app, app_context=None):
        """
        Validate capability taxonomy using AI analysis.

        Args:
            app: ApplicationComponent object
            app_context: Application context for analysis

        Returns:
            Dictionary with validation results
        """
        try:
            logger.info(f"Starting capability taxonomy validation for application: {app.name}")

            validation_result = {
                "is_valid": True,
                "validation_score": 0.0,
                "issues_found": [],
                "recommendations": [],
                "taxonomy_coverage": {},
                "validation_method": "ai_analysis",
            }

            # Get existing capabilities
            from app.models.business_capability import BusinessCapability

            capabilities = BusinessCapability.query.all()

            if not capabilities:
                logger.warning("No capabilities found for validation")
                validation_result["is_valid"] = False
                validation_result["issues_found"].append("No capability taxonomy found in system")
                validation_result["recommendations"].append("Import capability taxonomy first")
                return validation_result

            # Analyze application against capabilities
            app_text = f"{app.name} {app.description} {app.business_purpose or ''}"

            # Use LLM to validate taxonomy coverage
            llm_service = self._get_llm_service()
            if llm_service:
                try:
                    prompt = f"""
                    Analyze this application against the existing capability taxonomy:

                    Application: {app.name}
                    Description: {app.description}
                    Business Purpose: {app.business_purpose or 'Not specified'}

                    Available Capabilities (sample):
                    {', '.join([cap.name for cap in capabilities[:10]])}

                    Tasks:
                    1. Identify which capabilities this application should map to
                    2. Check if the taxonomy adequately covers this application's functionality
                    3. Identify any gaps in the current taxonomy
                    4. Suggest improvements to the taxonomy

                    Return JSON format:
                    {{
                        "coverage_score": 0.0 - 1.0,
                        "mapped_capabilities": ["capability names"],
                        "taxonomy_gaps": ["missing capability areas"],
                        "recommendations": ["taxonomy improvements"],
                        "validation_issues": ["specific issues found"]
                    }}
                    """

                    response = llm_service.generate_response(prompt)

                    if response and response.get("success"):
                        llm_result = response.get("content", "{}")

                        # Parse the response (simplified JSON parsing)
                        try:
                            import re

                            json_match = re.search(r"\{.*\}", llm_result, re.DOTALL)
                            if json_match:
                                import json

                                parsed_result = json.loads(json_match.group())

                                validation_result["validation_score"] = parsed_result.get(
                                    "coverage_score", 0.5
                                )
                                validation_result["issues_found"] = parsed_result.get(
                                    "validation_issues", []
                                )
                                validation_result["recommendations"] = parsed_result.get(
                                    "recommendations", []
                                )

                                # Calculate taxonomy coverage
                                mapped_caps = parsed_result.get("mapped_capabilities", [])
                                validation_result["taxonomy_coverage"] = {
                                    "total_capabilities": len(capabilities),
                                    "mapped_capabilities": len(mapped_caps),
                                    "coverage_percentage": validation_result["validation_score"]
                                    * 100,
                                    "mapped_capability_names": mapped_caps,
                                }

                                # Determine if valid based on coverage score
                                validation_result["is_valid"] = (
                                    validation_result["validation_score"] >= 0.6
                                )

                        except json.JSONDecodeError:
                            logger.warning("Failed to parse LLM response for taxonomy validation")
                            validation_result["validation_score"] = 0.5
                            validation_result["issues_found"].append(
                                "Unable to parse AI analysis results"
                            )

                except Exception as e:
                    logger.error(f"LLM taxonomy validation failed: {e}")
                    validation_result["validation_method"] = "basic_analysis"

            # Fallback to basic analysis if LLM fails
            if validation_result["validation_method"] == "basic_analysis":
                # Simple keyword matching against capabilities
                capability_names = [cap.name.lower() for cap in capabilities]
                app_keywords = app_text.lower().split()

                matched_capabilities = []
                for cap_name in capability_names:
                    for keyword in app_keywords:
                        if keyword in cap_name or cap_name in app_text.lower():
                            matched_capabilities.append(cap_name)
                            break

                coverage_score = (
                    len(matched_capabilities) / len(capabilities) if capabilities else 0
                )

                validation_result["validation_score"] = coverage_score
                validation_result["taxonomy_coverage"] = {
                    "total_capabilities": len(capabilities),
                    "mapped_capabilities": len(matched_capabilities),
                    "coverage_percentage": coverage_score * 100,
                    "mapped_capability_names": matched_capabilities,
                }
                validation_result["is_valid"] = (
                    coverage_score >= 0.4
                )  # Lower threshold for basic analysis

                if coverage_score < 0.4:
                    validation_result["issues_found"].append(
                        f"Low taxonomy coverage: {coverage_score:.1%}"
                    )
                    validation_result["recommendations"].append(
                        "Consider expanding capability taxonomy"
                    )

            logger.info(
                f"Capability taxonomy validation completed for {app.name}: "
                f"Score={validation_result['validation_score']:.2f}, "
                f"Valid={validation_result['is_valid']}"
            )

            return validation_result

        except Exception as e:
            logger.error(f"Capability taxonomy validation failed for {app.name}: {e}")
            return {
                "is_valid": False,
                "validation_score": 0.0,
                "issues_found": [f"Validation error: {str(e)}"],
                "recommendations": ["Fix validation system errors"],
                "taxonomy_coverage": {},
                "validation_method": "error",
            }

    @records_changes_as("import")
    def import_with_ai_analysis(
        self,
        app_data: Dict[str, Any],
        map_capabilities: bool = True,
        map_processes: bool = True,
        generate_archimate: bool = True,
        match_vendor_products: bool = True,
        confidence_threshold: float = 0.7,
        created_by: str = "ai_import",
        # This method reads kwargs.get("clone_vendor_archimate") but its signature
        # never accepted **kwargs, so that lookup raised NameError.
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Import single application with integrated AI analysis.
        Creates/updates ApplicationComponent, runs AI, creates all mappings atomically.
        """
        from app import db
        from app.models.application_portfolio import ApplicationComponent
        from app.models.apqc_process import ProcessApplicationMapping
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )

        result = {
            "success": False,
            "application_id": None,
            "application_name": app_data.get("name", "Unknown"),
            "created": False,
            "updated": False,
            "ai_analysis": {
                "capability_mappings": [],
                "process_mappings": [],
                "archimate_elements": [],
            },
            "mappings_created": {"capabilities": 0, "processes": 0, "archimate_elements": 0},
            "errors": [],
        }

        try:
            name = app_data.get("name", "").strip()
            if not name:
                result["errors"].append("Application name required")
                return result

            # Create or update application
            existing = ApplicationComponent.query.filter(
                ApplicationComponent.name.ilike(name)
            ).first()
            if existing:
                for key, value in app_data.items():
                    if hasattr(existing, key) and value and key != "id":
                        setattr(existing, key, value)
                app = existing
                result["updated"] = True
            else:
                valid_fields = {col.name for col in ApplicationComponent.__table__.columns}
                filtered = {k: v for k, v in app_data.items() if k in valid_fields and k != "id"}
                app = ApplicationComponent(**filtered)
                db.session.add(app)
                result["created"] = True

            db.session.flush()
            result["application_id"] = app.id

            # Build context for AI
            app_context = self._build_application_context(app)

            # AI Capability mapping
            if map_capabilities:
                try:
                    caps = self._map_capabilities_with_ai(app, app_context)
                    result["ai_analysis"]["capability_mappings"] = caps
                    for m in caps:
                        if m.get("confidence_score", 0) >= confidence_threshold:
                            if not UnifiedApplicationCapabilityMapping.query.filter_by(
                                application_id=app.id, capability_id=m["capability_id"]
                            ).first():
                                db.session.add(
                                    UnifiedApplicationCapabilityMapping(
                                        application_id=app.id,
                                        capability_id=m["capability_id"],
                                        confidence_score=m.get("confidence_score", 0.5),
                                        mapping_method="ai_import_integrated",
                                        rationale=m.get("rationale", ""),
                                        created_by=created_by,
                                    )
                                )
                                result["mappings_created"]["capabilities"] += 1
                except Exception as e:
                    result["errors"].append(f"Capability error: {e}")

            # AI APQC process classification
            if map_processes:
                try:
                    procs = self._classify_processes_with_ai(app, app_context)
                    result["ai_analysis"]["process_mappings"] = procs
                    for m in procs:
                        if m.get("similarity_score", 0) >= confidence_threshold:
                            if not ProcessApplicationMapping.query.filter_by(
                                application_id=app.id, process_id=m["process_id"]
                            ).first():
                                db.session.add(
                                    ProcessApplicationMapping(
                                        application_id=app.id,
                                        process_id=m["process_id"],
                                        confidence_score=m.get("similarity_score", 0.5),
                                        mapping_method="ai_import_integrated",
                                        created_by=created_by,
                                    )
                                )
                                result["mappings_created"]["processes"] += 1
                except Exception as e:
                    result["errors"].append(f"Process error: {e}")

            # AI ArchiMate generation
            if generate_archimate:
                try:
                    elements = self._generate_archimate_with_ai(app, app_context)
                    result["ai_analysis"]["archimate_elements"] = elements
                    if elements:
                        archimate_service = self._get_archimate_service()
                        primary_id = None
                        for elem in elements:
                            el = archimate_service.create_element_from_dict(
                                elem, created_by=created_by
                            )
                            if el:
                                result["mappings_created"]["archimate_elements"] += 1
                                if not primary_id and elem.get("type") == "ApplicationComponent":
                                    primary_id = el.id
                        if primary_id:
                            app.archimate_element_id = primary_id
                except Exception as e:
                    result["errors"].append(f"ArchiMate error: {e}")

            # Vendor Product Matching (ARCHITECT-FOCUSED: Match specific products, not just vendor names)
            if match_vendor_products:
                try:
                    from app.services.vendor_product_service import VendorProductService

                    vendor_service = VendorProductService()

                    # Extract vendor and product from application name and description
                    vendor_result = vendor_service.extract_vendor_and_product(
                        application_name=app.name, description=app.description or ""
                    )

                    if (
                        vendor_result.product_id
                        and vendor_result.product_confidence >= confidence_threshold
                    ):
                        app.vendor_product_id = vendor_result.product_id
                        result["vendor_product_matched"] = {
                            "vendor": vendor_result.vendor_name,
                            "product": vendor_result.product_name,
                            "version": vendor_result.version,
                            "confidence": vendor_result.product_confidence,
                        }
                    elif (
                        vendor_result.vendor_id
                        and vendor_result.vendor_confidence >= confidence_threshold
                    ):
                        # At least match vendor if product not found
                        app.vendor_name = vendor_result.vendor_name
                        result["vendor_matched"] = {
                            "vendor": vendor_result.vendor_name,
                            "confidence": vendor_result.vendor_confidence,
                        }
                except Exception as e:
                    result["errors"].append(f"Vendor product matching error: {e}")

            # Clone Vendor ArchiMate Templates
            if kwargs.get("clone_vendor_archimate", False):
                try:
                    from app.services.application_architecture_mapper import (
                        ApplicationArchitectureMapperService,
                    )

                    vendor_result = (
                        ApplicationArchitectureMapperService.clone_vendor_archimate_to_application(
                            application_id=app.id, created_by=created_by
                        )
                    )
                    if vendor_result.get("success"):
                        result["mappings_created"]["vendor_archimate"] = vendor_result.get(
                            "elements_cloned", 0
                        )
                        result["vendor_matched"] = vendor_result.get("vendor_matched", None)
                except Exception as e:
                    result["errors"].append(f"Vendor clone error: {e}")

            db.session.commit()
            result["success"] = True

        except Exception as e:
            db.session.rollback()
            result["errors"].append(f"Import error: {e}")

        return result

    @records_changes_as("import")
    def bulk_import_with_ai(self, applications_data: List[Dict], **kwargs) -> Dict:
        """Bulk import with AI - calls import_with_ai_analysis for each app."""
        results = {
            "total": len(applications_data),
            "successful": 0,
            "failed": 0,
            "created": 0,
            "updated": 0,
            "total_capabilities_mapped": 0,
            "total_processes_mapped": 0,
            "total_archimate_created": 0,
            "total_vendor_archimate_cloned": 0,
            "vendor_matches": 0,
            "applications": [],
            "errors": [],
        }

        for app_data in applications_data:
            r = self.import_with_ai_analysis(app_data, **kwargs)
            if r["success"]:
                results["successful"] += 1
                results["created"] += int(r["created"])
                results["updated"] += int(r["updated"])
                results["total_capabilities_mapped"] += r["mappings_created"]["capabilities"]
                results["total_processes_mapped"] += r["mappings_created"]["processes"]
                results["total_archimate_created"] += r["mappings_created"]["archimate_elements"]
                results["total_vendor_archimate_cloned"] += r["mappings_created"].get(
                    "vendor_archimate", 0
                )
                if r.get("vendor_matched"):
                    results["vendor_matches"] += 1
            else:
                results["failed"] += 1
                results["errors"].extend(r["errors"])
            results["applications"].append(r)

        return results

    def analyze_file_data_for_preview(
        self,
        applications_data: List[Dict],
        confidence_threshold: float = 0.7,
        archimate_mode: str = "standard",
    ) -> Dict[str, Any]:
        """
        Analyze file data for preview without saving to database.

        Args:
            applications_data: List of application dictionaries from parsed file
            confidence_threshold: Threshold for accepting matches
            archimate_mode: Generation mode - 'quick', 'standard', or 'comprehensive'

        Returns:
            Dictionary with preview analysis results
        """
        import time

        from app.models.application_portfolio import ApplicationComponent

        results = {
            "total_analyzed": 0,
            "capability_mappings_found": 0,
            "process_mappings_found": 0,
            "archimate_elements_generated": 0,
            "vendor_matches": 0,
            "high_confidence_count": 0,
            "avg_processing_time_ms": 0,
            "applications": [],
        }

        total_time = 0

        for app_data in applications_data:
            start_time = time.time()
            name = app_data.get("name", "Unknown")
            # Create a temporary app object for context building
            # We don't save this to DB
            temp_app = ApplicationComponent(
                name=name,
                description=app_data.get("description", ""),
                business_purpose=app_data.get("business_purpose", ""),
            )

            # Manually set id to 0 to indicate it's not persisted
            temp_app.id = 0

            app_result = {"name": name, "capabilities": [], "processes": [], "archimate": []}

            try:
                # Build context
                app_context = self._build_application_context(temp_app)

                # Analyze capabilities - return ALL mappings, not just high confidence
                caps = self._map_capabilities_with_ai(temp_app, app_context)
                app_result["capabilities"] = caps  # Include all capabilities
                results["capability_mappings_found"] += len(caps)
                # Count high confidence separately for stats
                high_conf_caps = [
                    c for c in caps if c.get("confidence_score", 0) >= confidence_threshold
                ]
                results["high_confidence_count"] += len(high_conf_caps)

                # Analyze processes - return ALL mappings
                procs = self._classify_processes_with_ai(temp_app, app_context)
                app_result["processes"] = procs  # Include all processes
                results["process_mappings_found"] += len(procs)
                # Count high confidence separately
                high_conf_procs = [
                    p for p in procs if p.get("similarity_score", 0) >= confidence_threshold
                ]
                results["high_confidence_count"] += len(high_conf_procs)

                # Generate ArchiMate with selected mode - include all elements
                arch = self._generate_archimate_with_ai(temp_app, app_context, mode=archimate_mode)
                app_result["archimate"] = arch
                results["archimate_elements_generated"] += len(arch)

                # Vendor Product Matching (always attempt for all applications)
                try:
                    from app.services.vendor_product_service import VendorProductService

                    vendor_service = VendorProductService()
                    vendor_result = vendor_service.extract_vendor_product(
                        application_name=name, description=app_data.get("description", "")
                    )
                    if vendor_result.vendor_id or vendor_result.product_id:
                        results["vendor_matches"] += 1
                        app_result["vendor_match"] = {
                            "vendor": vendor_result.vendor_name,
                            "product": vendor_result.product_name,
                            "confidence": vendor_result.vendor_confidence
                            or vendor_result.product_confidence,
                        }
                    else:
                        # No match found, but service worked
                        app_result["vendor_match"] = None
                except Exception as vendor_error:
                    # Vendor matching failed - log as warning so it's visible
                    logger.warning(f"Vendor matching failed for {name}: {vendor_error}")
                    app_result["vendor_match"] = {"error": str(vendor_error)}

                    # Rollback transaction to prevent poisoning subsequent queries
                    try:
                        db.session.rollback()
                        logger.info("Database transaction rolled back after vendor matching error")
                    except Exception as rollback_error:
                        logger.error(f"Failed to rollback transaction: {rollback_error}")

                results["total_analyzed"] += 1

            except Exception as e:
                app_result["error"] = str(e)

            end_time = time.time()
            total_time += (end_time - start_time) * 1000  # Convert to ms
            results["applications"].append(app_result)

        # Calculate average processing time
        if results["total_analyzed"] > 0:
            results["avg_processing_time_ms"] = int(total_time / results["total_analyzed"])

        return results


# Singleton instance
_ai_import_service = AIImportService()


def get_ai_import_service() -> AIImportService:
    """Get the singleton AI import service instance."""
    return _ai_import_service
