"""
Document Analysis Service for Application and Vendor ArchiMate Extraction

Intelligent document analysis service that extracts ArchiMate 3.2 elements
from architecture documents, PDFs, PowerPoints, Word documents, and diagrams,
then maps them to ApplicationComponent or VendorOrganization models.

This service provides:
- Multi-format document parsing (PDF, DOCX, PPTX, images)
- AI-powered ArchiMate element extraction
- Intelligent mapping to application/vendor models
- ArchiMate 3.2 compliance validation
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.models import (
    APISettings,
    ApplicationComponent,
    ArchiMateElement,
    LLMInteraction,
    VendorOrganization,
)
from app.services.archimate.archimate_validator import ArchiMateValidator
from app.services.archimate.document_text_extractor import extract_text_from_file
from app.services.archimate.multi_modal_llm_service import MultiModalLLMService
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class DocumentAnalysisService:
    """
    Service for analyzing architecture documents and extracting ArchiMate elements
    for applications and vendors.
    """

    # ArchiMate 3.2 Application Layer elements
    APPLICATION_ELEMENT_TYPES = [
        "ApplicationComponent",
        "ApplicationCollaboration",
        "ApplicationInterface",
        "ApplicationFunction",
        "ApplicationProcess",
        "ApplicationInteraction",
        "ApplicationEvent",
        "ApplicationService",
        "DataObject",
    ]

    # ArchiMate 3.2 Business Layer elements (for vendor context)
    BUSINESS_ELEMENT_TYPES = [
        "BusinessActor",
        "BusinessRole",
        "BusinessCollaboration",
        "BusinessProcess",
        "BusinessFunction",
        "BusinessService",
        "Product",
        "Contract",
    ]

    def __init__(self):
        """Initialize the document analysis service."""
        self.llm_service = LLMService()
        self.multi_modal_service = MultiModalLLMService()
        self.validator = ArchiMateValidator()

        # Initialize enhanced services
        try:
            from app.services.archimate.graph_relationship_service import GraphRelationshipService
            from app.services.archimate.relationship_pattern_service import (
                RelationshipPatternService,
            )
            from app.services.archimate.semantic_similarity_service import SemanticSimilarityService

            self.graph_service = GraphRelationshipService()
            self.semantic_service = SemanticSimilarityService()
            self.pattern_service = RelationshipPatternService()
        except Exception as e:
            logger.warning(f"Could not initialize enhanced services: {e}")
            self.graph_service = None
            self.semantic_service = None
            self.pattern_service = None

        # Initialize new enterprise-grade services
        try:
            from app.services.archimate.confidence_scoring_service import ConfidenceScoringService
            from app.services.archimate.document_chunking_service import DocumentChunkingService
            from app.services.archimate.enhanced_archimate_extractor import (
                EnhancedArchiMateExtractor,
            )
            from app.services.archimate.entity_resolution_service import EntityResolutionService
            from app.services.archimate.feedback_learning_service import FeedbackLearningService
            from app.services.archimate.knowledge_graph_service import KnowledgeGraphService

            self.chunking_service = DocumentChunkingService()
            self.entity_resolution_service = EntityResolutionService()
            self.confidence_scoring_service = ConfidenceScoringService()
            self.feedback_learning_service = FeedbackLearningService()
            self.knowledge_graph_service = KnowledgeGraphService()
            self.enhanced_extractor = EnhancedArchiMateExtractor()

            logger.info("Initialized enterprise-grade document analysis services")
        except Exception as e:
            logger.warning(f"Could not initialize enterprise services: {e}")
            self.chunking_service = None
            self.entity_resolution_service = None
            self.confidence_scoring_service = None
            self.feedback_learning_service = None
            self.knowledge_graph_service = None
            self.enhanced_extractor = None

    async def analyze_document_for_application(
        self,
        file_path: str,
        file_type: str,
        application_id: Optional[int] = None,
        user_id: Optional[int] = None,
        provider: str = "claude",
    ) -> Dict[str, Any]:
        """
        Analyze document and extract application details with ArchiMate elements.

        Args:
            file_path: Path to the document file
            file_type: Type of file ('image', 'document', 'text', 'spreadsheet')
            application_id: Optional existing application ID to update
            user_id: User ID performing the analysis
            provider: LLM provider ('claude', 'openai', 'gemini')

        Returns:
            Dictionary with analysis results:
            {
                'application_data': {...},
                'archimate_elements': [...],
                'relationships': [...],
                'confidence': float,
                'llm_interactions': [...]
            }
        """
        logger.info(f"Analyzing document for application: {file_path}")

        # Extract content based on file type
        if file_type == "image":
            extracted_data, interaction = await self._analyze_image(
                file_path, provider, "application"
            )
        elif file_type == "document":
            extracted_data, interactions = await self._analyze_document(
                file_path, provider, "application"
            )
            interaction = interactions[0] if interactions else None
        elif file_type == "spreadsheet":
            extracted_data, interaction = self._analyze_spreadsheet(
                file_path, provider, "application"
            )
        else:
            extracted_data, interaction = await self._analyze_text_file(
                file_path, provider, "application"
            )

        # Extract application-specific information
        application_data = self._extract_application_data(extracted_data, application_id)

        # Extract ArchiMate elements
        archimate_elements = extracted_data.get("elements", [])
        relationships = extracted_data.get("relationships", [])

        # Filter for application layer elements
        application_elements = [
            e for e in archimate_elements if e.get("type") in self.APPLICATION_ELEMENT_TYPES
        ]

        # Validate ArchiMate elements and relationships
        validation_results = self._validate_elements(application_elements, relationships)

        return {
            "application_data": application_data,
            "archimate_elements": application_elements,
            "relationships": relationships,
            "validation_results": validation_results,
            "confidence": extracted_data.get("metadata", {}).get("confidence", "medium"),
            "llm_interactions": [interaction] if interaction else [],
            "metadata": extracted_data.get("metadata", {}),
        }

    async def analyze_document_for_vendor(
        self,
        file_path: str,
        file_type: str,
        vendor_id: Optional[int] = None,
        user_id: Optional[int] = None,
        provider: str = "claude",
    ) -> Dict[str, Any]:
        """
        Analyze document and extract vendor details with ArchiMate elements.

        Args:
            file_path: Path to the document file
            file_type: Type of file ('image', 'document', 'text', 'spreadsheet')
            vendor_id: Optional existing vendor ID to update
            user_id: User ID performing the analysis
            provider: LLM provider ('claude', 'openai', 'gemini')

        Returns:
            Dictionary with analysis results:
            {
                'vendor_data': {...},
                'archimate_elements': [...],
                'relationships': [...],
                'confidence': float,
                'llm_interactions': [...]
            }
        """
        logger.info(f"Analyzing document for vendor: {file_path}")

        # Extract content based on file type
        if file_type == "image":
            extracted_data, interaction = await self._analyze_image(file_path, provider, "vendor")
        elif file_type == "document":
            extracted_data, interactions = await self._analyze_document(
                file_path, provider, "vendor"
            )
            interaction = interactions[0] if interactions else None
        elif file_type == "spreadsheet":
            extracted_data, interaction = self._analyze_spreadsheet(file_path, provider, "vendor")
        else:
            extracted_data, interaction = await self._analyze_text_file(
                file_path, provider, "vendor"
            )

        # Extract vendor-specific information
        vendor_data = self._extract_vendor_data(extracted_data, vendor_id)

        # Extract ArchiMate elements
        archimate_elements = extracted_data.get("elements", [])
        relationships = extracted_data.get("relationships", [])

        # Filter for business/application layer elements relevant to vendors
        vendor_elements = [
            e
            for e in archimate_elements
            if e.get("type") in self.BUSINESS_ELEMENT_TYPES + self.APPLICATION_ELEMENT_TYPES
        ]

        # Validate ArchiMate elements and relationships
        validation_results = self._validate_elements(vendor_elements, relationships)

        return {
            "vendor_data": vendor_data,
            "archimate_elements": vendor_elements,
            "relationships": relationships,
            "validation_results": validation_results,
            "confidence": extracted_data.get("metadata", {}).get("confidence", "medium"),
            "llm_interactions": [interaction] if interaction else [],
            "metadata": extracted_data.get("metadata", {}),
        }

    async def _analyze_image(
        self, image_path: str, provider: str, context: str
    ) -> Tuple[Dict, Optional[LLMInteraction]]:
        """Analyze image file for ArchiMate elements."""
        # `await ...__doc__` used to sit here, awaiting a docstring string. That
        # raises TypeError: object str can't be used in 'await' expression - so
        # every image upload through this path failed before it reached the
        # model, and the context prompt built above was never used by anything.
        #
        # Removing that await left `prompt_addition` referenced but never bound
        # (ruff F821 - a NameError on the first image upload), because both
        # branches of the context switch had been gutted to `pass`. Build the
        # value the call actually wants; extract_archimate_from_diagram documents
        # extra_instructions as caller-specific guidance appended to the prompt.
        # Name the ArchiMate types exactly (DESIGN.md: ArchiMate is the backbone,
        # not a view) so the model returns types that map straight onto
        # ArchiMateElement rows without a second normalisation pass.
        if context == "application":
            prompt_addition = (
                "Focus on Application Layer elements: ApplicationComponent, "
                "ApplicationService, ApplicationInterface, ApplicationFunction "
                "and the DataObject elements they access."
            )
        else:  # vendor
            prompt_addition = (
                "Focus on vendor-supplied products and what they run on: the "
                "ApplicationComponent elements each vendor provides, plus the "
                "Node, SystemSoftware and TechnologyService elements beneath them."
            )

        extracted_data, interaction = await self.multi_modal_service.extract_archimate_from_diagram(
            image_path, provider, extra_instructions=prompt_addition
        )

        return extracted_data, interaction

    async def _analyze_document(
        self, file_path: str, provider: str, context: str
    ) -> Tuple[Dict, List[LLMInteraction]]:
        """Analyze document file (PDF, DOCX, PPTX) for ArchiMate elements."""
        interactions = []

        # Check if this is a PDF and provider is Gemini - use native PDF processing
        if file_path.lower().endswith(".pdf") and provider.lower() == "gemini":
            return await self._analyze_pdf_with_gemini_native(file_path, context)

        # Extract text content for other file types or providers
        file_type = "document"
        if file_path.lower().endswith(".pdf"):
            file_type = "document"
        elif file_path.lower().endswith((".doc", ".docx")):
            file_type = "document"
        elif file_path.lower().endswith((".ppt", ".pptx")):
            file_type = "document"

        text_content = extract_text_from_file(file_path, file_type)

        if not text_content or text_content.startswith("Error"):
            return {
                "elements": [],
                "relationships": [],
                "metadata": {"error": text_content},
            }, interactions

        # ENHANCEMENT: Chunk large documents
        # Determine appropriate chunk size based on provider/model token limits
        from app.models.models import APISettings

        provider_settings = APISettings.query.filter_by(provider=provider, enabled=True).first()

        # Estimate token limits (characters / 4 ≈ tokens)
        # Hugging Face models typically have 1024 - 2048 token limits
        # Claude/GPT - 4 can handle much larger (32k - 200k tokens)
        if provider.lower() in ["huggingface", "hugging_face"]:
            max_chunk_chars = 3000  # ~750 tokens, safe for 1024 token models
            use_chunking = len(text_content) > max_chunk_chars and self.chunking_service
        else:
            # For Claude/GPT - 4, use larger chunks
            max_chunk_chars = 15000  # ~3750 tokens
            use_chunking = len(text_content) > max_chunk_chars and self.chunking_service

        if use_chunking:
            logger.info(
                f"Document is large ({len(text_content)} chars), using chunking for provider {provider}"
            )
            # Adjust chunk size based on provider
            from app.services.archimate.document_chunking_service import DocumentChunkingService

            if provider.lower() in ["huggingface", "hugging_face"]:
                # Use smaller chunks for Hugging Face models (1024 token limit)
                chunking_service = DocumentChunkingService(chunk_size=2500, overlap_size=500)
            else:
                chunking_service = self.chunking_service

            chunks = chunking_service.chunk_document(text_content)
            logger.info(
                f"Split into {len(chunks)} chunks (avg size: {sum(len(c.text) for c in chunks) // len(chunks) if chunks else 0} chars)"
            )
            # Use multi-pass analysis for chunked documents
            extracted_data, chunk_interactions = await self._analyze_chunked_document(
                chunks, provider, context
            )
            interactions.extend(chunk_interactions)
        else:
            # Single-pass analysis for smaller documents
            # Build context-specific prompt
            if context == "application":
                analysis_prompt = self._build_application_analysis_prompt(text_content)
            elif context == "vendor":
                analysis_prompt = self._build_vendor_analysis_prompt(text_content)
            else:  # architecture/general - extract all layers
                analysis_prompt = self._build_architecture_analysis_prompt(text_content)

            # Get configured provider and model
            provider_name, model = LLMService._get_configured_provider()
            if provider != provider_name:
                # Try to use requested provider
                settings = APISettings.query.filter_by(provider=provider, enabled=True).first()
                if settings and settings.default_model:
                    provider_name = provider
                    model = settings.default_model

            # ENHANCEMENT: Use multi-pass analysis if enhanced extractor available
            if self.enhanced_extractor:
                logger.info("Using enhanced multi-pass extraction")
                try:
                    extracted_data = self.enhanced_extractor.extract_comprehensive_architecture(
                        document_text=text_content,
                        document_type=context,
                        context=f"Provider: {provider}",
                        provider=provider,
                    )

                    # Normalize element types after enhanced extraction
                    from app.services.archimate.element_type_normalizer import ElementTypeNormalizer

                    normalizer = ElementTypeNormalizer()
                    if "elements" in extracted_data:
                        extracted_data["elements"] = normalizer.normalize_elements(
                            extracted_data["elements"]
                        )

                    # Check if extraction actually returned elements
                    element_count = len(extracted_data.get("elements", []))
                    logger.info(
                        f"Enhanced extraction returned {element_count} elements (after normalization)"
                    )

                    # If no elements found, check for errors and fall back to simple extraction
                    if element_count == 0:
                        error_msg = extracted_data.get("metadata", {}).get("error", "")
                        error_type = extracted_data.get("metadata", {}).get("error_type", "")
                        raw_response = extracted_data.get("metadata", {}).get("raw_response", "")

                        logger.warning("⚠️ Enhanced extraction returned 0 elements")
                        logger.warning(f"Error: {error_msg}")
                        logger.warning(f"Error type: {error_type}")
                        if raw_response:
                            logger.warning(f"Raw response preview: {raw_response[:500]}")

                        # Check if it's a Hugging Face error - try to use a paid provider
                        is_huggingface_error = (
                            "hugging face" in error_msg.lower()
                            or "gpt2" in error_msg.lower()
                            or "context window" in error_msg.lower()
                            or "too long" in error_msg.lower()
                            or provider_name == "huggingface"
                        )

                        fallback_provider = None
                        fallback_model = None

                        if is_huggingface_error:
                            logger.warning(
                                "🔍 Detected Hugging Face error - attempting to use paid provider..."
                            )
                            # Try to find a paid provider
                            from app.models.models import APISettings

                            for paid_provider in [
                                "anthropic",
                                "openai",
                                "gemini",
                                "azure",
                            ]:
                                paid_settings = APISettings.query.filter_by(
                                    provider=paid_provider, enabled=True
                                ).first()
                                if (
                                    paid_settings
                                    and paid_settings.api_key
                                    and paid_settings.default_model
                                ):
                                    fallback_provider = paid_provider
                                    fallback_model = paid_settings.default_model
                                    logger.info(
                                        f"✅ Found paid provider: {fallback_provider} with model: {fallback_model}"
                                    )
                                    break

                        # Use fallback provider if found, otherwise use same provider
                        final_provider = fallback_provider or provider_name
                        final_model = fallback_model or model

                        if fallback_provider:
                            logger.info(
                                f"🔄 Falling back to {final_provider} with model {final_model}"
                            )
                        else:
                            logger.warning(
                                "⚠️ No paid provider available, using same provider for fallback"
                            )

                        # Fall back to simple extraction with a more direct prompt
                        simple_prompt = f"""You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework.

Analyze the following document and extract ALL ArchiMate 3.2 elements you can find.

DOCUMENT TEXT:
{text_content[:20000]}

Return ONLY valid JSON in this exact format:
{{
  "elements": [
    {{
      "name": "Element Name",
      "type": "ApplicationComponent|BusinessProcess|DataObject|ApplicationService|BusinessService|etc",
      "layer": "application|business|technology|motivation|strategy",
      "description": "Element description",
      "properties": {{}}
    }}
  ],
  "relationships": [
    {{
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Serves|Accesses|Realizes|etc",
      "description": "Relationship description"
    }}
  ],
  "metadata": {{
    "confidence": "high|medium|low",
    "notes": "Any observations"
  }}
}}

IMPORTANT: Extract ALL elements mentioned in the document. Be thorough and comprehensive."""

                        try:
                            response_text, interaction = LLMService._call_llm(
                                prompt=simple_prompt,
                                model=final_model,
                                provider=final_provider,
                                user_id=None,
                                project_id=None,
                                max_tokens=LLMService.get_max_tokens_limit(
                                    final_provider, final_model, 8000
                                ),
                            )

                            if interaction:
                                interactions.append(interaction)

                            logger.info(
                                f"Simple extraction LLM response length: {len(response_text)} chars"
                            )
                            logger.info(
                                f"Simple extraction response preview: {response_text[:500]}"
                            )

                            extracted_data = self._parse_llm_response(response_text)

                            # Normalize the fallback extraction too
                            if "elements" in extracted_data:
                                extracted_data["elements"] = normalizer.normalize_elements(
                                    extracted_data["elements"]
                                )

                            element_count_after_fallback = len(extracted_data.get("elements", []))
                            logger.info(
                                f"✅ Simple extraction returned {element_count_after_fallback} elements (after normalization)"
                            )

                            if element_count_after_fallback == 0:
                                # Still no elements - check if it's an error
                                error_in_fallback = extracted_data.get("metadata", {}).get(
                                    "error", ""
                                )
                                if error_in_fallback:
                                    logger.error(
                                        f"❌ Fallback extraction also failed: {error_in_fallback}"
                                    )
                        except ValueError as ve:
                            # Check if it's a budget error
                            if "budget" in str(ve).lower():
                                logger.error(f"💰 LLM budget exhausted: {ve}")
                                # Try Simple Parsing as last resort for CSV/Excel files
                                if file_path.lower().endswith((".csv", ".xlsx", ".xls")):
                                    logger.info(
                                        "🔄 Falling back to Simple Parsing (non-LLM) for structured file..."
                                    )
                                    try:
                                        from app.services.archimate.simple_parser_service import (
                                            SimpleParserService,
                                        )

                                        simple_parser = SimpleParserService()
                                        extracted_data = simple_parser.parse_document(
                                            file_path=file_path, analysis_context=context
                                        )
                                        logger.info(
                                            f"✅ Simple Parsing extracted {len(extracted_data.get('elements', []))} elements (no LLM used)"
                                        )
                                        # Add metadata to indicate fallback
                                        if "metadata" not in extracted_data:
                                            extracted_data["metadata"] = {}
                                        extracted_data["metadata"][
                                            "fallback_reason"
                                        ] = "llm_budget_exhausted"
                                        extracted_data["metadata"][
                                            "parsing_method"
                                        ] = "simple_parser_fallback"
                                    except Exception as simple_error:
                                        logger.error(
                                            f"❌ Simple Parsing also failed: {simple_error}"
                                        )
                                        # Re-raise original budget error
                                        raise ve
                                else:
                                    logger.error(
                                        "❌ Budget exhausted and file is not CSV/Excel - cannot use Simple Parsing"
                                    )
                                    raise ve
                            else:
                                # Re-raise non-budget ValueError
                                raise
                        except Exception as fallback_error:
                            logger.error(
                                f"❌ Fallback extraction failed with exception: {fallback_error}"
                            )
                            # Keep the original extracted_data with error

                    # Create interaction record
                    prompt_summary = (
                        analysis_prompt[:1000]
                        if "analysis_prompt" in locals()
                        else f"Enhanced extraction for {context} context"
                    )
                    interaction = LLMInteraction(
                        model_name=model,
                        provider=provider_name,
                        prompt=prompt_summary,
                        response=f"Extracted {len(extracted_data.get('elements', []))} elements"[
                            :2000
                        ],
                        token_count_input=len(text_content) // 4,
                        token_count_output=500,
                        cost=0.05,
                    )
                    interactions.append(interaction)
                except ValueError as ve:
                    # GRACEFUL DEGRADATION: Handle budget exhaustion at top level
                    if "budget" in str(ve).lower():
                        logger.error(f"💰 LLM budget exhausted during enhanced extraction: {ve}")
                        # Try Simple Parsing for CSV/Excel files
                        if file_path.lower().endswith((".csv", ".xlsx", ".xls")):
                            logger.info(
                                "🔄 Gracefully degrading to Simple Parsing (non-LLM) for structured file..."
                            )
                            try:
                                from app.services.archimate.simple_parser_service import (
                                    SimpleParserService,
                                )

                                simple_parser = SimpleParserService()
                                extracted_data = simple_parser.parse_document(
                                    file_path=file_path, analysis_context=context
                                )
                                logger.info(
                                    f"✅ Simple Parsing extracted {len(extracted_data.get('elements', []))} elements (no LLM used)"
                                )
                                # Add metadata to indicate fallback
                                if "metadata" not in extracted_data:
                                    extracted_data["metadata"] = {}
                                extracted_data["metadata"][
                                    "fallback_reason"
                                ] = "llm_budget_exhausted"
                                extracted_data["metadata"][
                                    "parsing_method"
                                ] = "simple_parser_fallback"
                                extracted_data["metadata"][
                                    "user_message"
                                ] = "LLM budget exhausted. Used free Simple Parsing instead (CSV/Excel only)."
                            except Exception as simple_error:
                                logger.error(f"❌ Simple Parsing also failed: {simple_error}")
                                # Re-raise original budget error with helpful message
                                raise ValueError(
                                    f"LLM budget exhausted and Simple Parsing failed. "
                                    f"Please increase your LLM budget or fix the file format. "
                                    f"Original error: {ve}"
                                )
                        else:
                            logger.error(
                                "❌ Budget exhausted and file is not CSV/Excel - cannot use Simple Parsing"
                            )
                            raise ValueError(
                                f"LLM budget exhausted. Simple Parsing only works for CSV/Excel files. "
                                f"Please increase your LLM budget or convert your document to CSV/Excel format. "
                                f"Original error: {ve}"
                            )
                    else:
                        # Re-raise non-budget errors
                        raise
                except Exception as e:
                    logger.error(
                        f"Enhanced extraction failed: {e}. Falling back to simple extraction."
                    )
                    # Fall back to simple extraction
                    response_text, interaction = LLMService._call_llm(
                        prompt=analysis_prompt,
                        model=model,
                        provider=provider_name,
                        user_id=None,
                        project_id=None,
                        max_tokens=LLMService.get_max_tokens_limit(provider_name, model, 8000),
                    )

                    if interaction:
                        interactions.append(interaction)

                    extracted_data = self._parse_llm_response(response_text)
            else:
                # Original single-pass extraction
                # Call LLM service
                response_text, interaction = LLMService._call_llm(
                    prompt=analysis_prompt,
                    model=model,
                    provider=provider_name,
                    user_id=None,
                    project_id=None,
                    max_tokens=8000,
                )

                if interaction:
                    interactions.append(interaction)

                # Parse JSON response
                extracted_data = self._parse_llm_response(response_text)

                # Normalize element types
                from app.services.archimate.element_type_normalizer import ElementTypeNormalizer

                normalizer = ElementTypeNormalizer()
                if "elements" in extracted_data:
                    extracted_data["elements"] = normalizer.normalize_elements(
                        extracted_data["elements"]
                    )

        # Normalize element types for chunked results as well
        if use_chunking and "elements" in extracted_data:
            from app.services.archimate.element_type_normalizer import ElementTypeNormalizer

            normalizer = ElementTypeNormalizer()
            extracted_data["elements"] = normalizer.normalize_elements(extracted_data["elements"])

        # ENHANCED: Post-process with intelligent relationship discovery
        # Pass document text for co-occurrence analysis (only if we have text_content)
        if not use_chunking and text_content:
            extracted_data = self._enhance_with_intelligent_discovery(
                extracted_data, context, document_text=text_content
            )

        # ENHANCEMENT: Entity resolution
        if self.entity_resolution_service:
            logger.info("Applying entity resolution")
            elements = extracted_data.get("elements", [])
            resolved_elements = self.entity_resolution_service.resolve_entities_batch(
                elements, context=text_content[:5000] if text_content else None
            )
            # Update elements with resolutions
            for i, resolved in enumerate(resolved_elements):
                if "resolution" in resolved and resolved["resolution"]["confidence"] > 0.7:
                    elements[i]["resolved_name"] = resolved["resolution"]["resolved"]
                    elements[i]["resolution"] = resolved["resolution"]
            extracted_data["elements"] = elements

        # ENHANCEMENT: Confidence scoring
        if self.confidence_scoring_service:
            logger.info("Calculating confidence scores")
            elements = extracted_data.get("elements", [])
            for element in elements:
                confidence = self.confidence_scoring_service.score_element(
                    element,
                    context=text_content[:5000] if text_content else None,
                    extraction_method="llm",
                    validation_result=extracted_data.get("validation_results"),
                    database_match=element.get("resolution", {}).get("database_match"),
                )
                element["confidence"] = confidence.to_dict()

        # ENHANCEMENT: Knowledge graph integration
        if self.knowledge_graph_service:
            logger.info("Enriching with knowledge graph")
            enriched = self.knowledge_graph_service.enrich_with_knowledge_graph(
                extracted_data.get("elements", []), extracted_data.get("relationships", [])
            )
            extracted_data["elements"] = enriched["elements"]
            extracted_data["relationships"] = enriched["relationships"]
            extracted_data["kg_enrichment"] = enriched.get("additional_context", [])

        # ENHANCEMENT: Apply learned rules from feedback
        if self.feedback_learning_service:
            logger.info("Applying learned rules from feedback")
            elements = extracted_data.get("elements", [])
            # Get document hash for pattern matching
            import hashlib

            doc_hash = hashlib.sha256(text_content.encode() if text_content else b"").hexdigest()
            improved_elements = self.feedback_learning_service.apply_learned_rules(
                elements, document_hash=doc_hash
            )
            extracted_data["elements"] = improved_elements

        return extracted_data, interactions

    async def _analyze_chunked_document(
        self, chunks: List, provider: str, context: str
    ) -> Tuple[Dict, List[LLMInteraction]]:
        """
        Analyze a document that has been chunked into multiple pieces.
        Uses multi-pass analysis with merging.
        """
        interactions = []
        all_elements = []
        all_relationships = []
        chunk_results = []

        logger.info(f"Analyzing {len(chunks)} chunks with multi-pass analysis")

        # Get configured provider
        provider_name, model = LLMService._get_configured_provider()
        if provider != provider_name:
            settings = APISettings.query.filter_by(provider=provider, enabled=True).first()
            if settings and settings.default_model:
                provider_name = provider
                model = settings.default_model

        # Analyze each chunk
        for i, chunk in enumerate(chunks):
            chunk_size_chars = len(chunk.text)
            estimated_tokens = chunk_size_chars // 4  # Rough estimate: 4 chars per token
            logger.info(
                f"Analyzing chunk {i + 1}/{len(chunks)} ({chunk_size_chars} chars, ~{estimated_tokens} tokens)"
            )

            # Check if chunk is too large for provider (especially Hugging Face)
            if provider_name.lower() in ["huggingface", "hugging_face"] and estimated_tokens > 800:
                logger.warning(
                    f"Chunk {i + 1} is too large for {provider_name} (~{estimated_tokens} tokens > 800 limit). "
                    f"Skipping this chunk. Consider using Simple Parsing mode or a different provider."
                )
                chunk_data = {
                    "elements": [],
                    "relationships": [],
                    "metadata": {
                        "error": f"Chunk too large for {provider_name} model (~{estimated_tokens} tokens > 800 limit)",
                        "error_type": "chunk_too_large",
                        "chunk_size_chars": chunk_size_chars,
                        "estimated_tokens": estimated_tokens,
                        "suggestion": "Use Simple Parsing mode or switch to Claude/GPT - 4 provider",
                    },
                }
                chunk_results.append(chunk_data)
                continue

            # Build prompt for this chunk
            if context == "application":
                analysis_prompt = self._build_application_analysis_prompt(chunk.text)
            elif context == "vendor":
                analysis_prompt = self._build_vendor_analysis_prompt(chunk.text)
            else:
                analysis_prompt = self._build_architecture_analysis_prompt(chunk.text)

            # Add chunk context to prompt
            analysis_prompt = f"""
This is chunk {i + 1} of {len(chunks)} from a larger document.
Section: {chunk.section_title or 'Unknown'}

{analysis_prompt}
"""

            # Call LLM
            response_text, interaction = LLMService._call_llm(
                prompt=analysis_prompt,
                model=model,
                provider=provider_name,
                user_id=None,
                project_id=None,
                max_tokens=8000,
            )

            if interaction:
                interactions.append(interaction)

            # Parse response
            chunk_data = self._parse_llm_response(response_text)

            # Check if chunk analysis failed (error in metadata)
            if chunk_data.get("metadata", {}).get("error"):
                error_msg = chunk_data["metadata"]["error"]
                logger.warning(f"Chunk {i + 1}/{len(chunks)} analysis failed: {error_msg[:200]}")
                # Continue with other chunks even if one fails
                chunk_results.append(chunk_data)
                continue

            # Normalize element types
            from app.services.archimate.element_type_normalizer import ElementTypeNormalizer

            normalizer = ElementTypeNormalizer()
            if "elements" in chunk_data:
                chunk_data["elements"] = normalizer.normalize_elements(chunk_data["elements"])

            chunk_results.append(chunk_data)

            # Collect elements and relationships
            all_elements.extend(chunk_data.get("elements", []))
            all_relationships.extend(chunk_data.get("relationships", []))

        # Merge and deduplicate results
        merged_data = self._merge_chunk_results(chunk_results, all_elements, all_relationships)

        logger.info(
            f"Merged {len(chunks)} chunks: {len(merged_data.get('elements', []))} elements, "
            f"{len(merged_data.get('relationships', []))} relationships"
        )

        return merged_data, interactions

    def _merge_chunk_results(
        self, chunk_results: List[Dict], all_elements: List[Dict], all_relationships: List[Dict]
    ) -> Dict:
        """Merge results from multiple chunks, deduplicating elements."""
        # Deduplicate elements by name (case-insensitive)
        seen_names = set()
        unique_elements = []
        for element in all_elements:
            name_lower = element.get("name", "").lower()
            if name_lower and name_lower not in seen_names:
                seen_names.add(name_lower)
                unique_elements.append(element)
            elif name_lower in seen_names:
                # Merge properties from duplicate
                existing = next(
                    e for e in unique_elements if e.get("name", "").lower() == name_lower
                )
                # Merge descriptions if one is longer
                if len(element.get("description", "")) > len(existing.get("description", "")):
                    existing["description"] = element.get("description", "")
                # Merge properties
                existing_props = existing.get("properties", {})
                element_props = element.get("properties", {})
                existing_props.update(element_props)
                existing["properties"] = existing_props

        # Deduplicate relationships
        seen_rels = set()
        unique_relationships = []
        for rel in all_relationships:
            rel_key = (
                rel.get("source", "").lower(),
                rel.get("target", "").lower(),
                rel.get("type", ""),
            )
            if rel_key not in seen_rels:
                seen_rels.add(rel_key)
                unique_relationships.append(rel)

        # Get metadata from first chunk
        metadata = chunk_results[0].get("metadata", {}) if chunk_results else {}
        metadata["chunked"] = True
        metadata["chunk_count"] = len(chunk_results)
        metadata["merged_elements"] = len(unique_elements)
        metadata["merged_relationships"] = len(unique_relationships)

        return {
            "elements": unique_elements,
            "relationships": unique_relationships,
            "metadata": metadata,
        }

    async def _analyze_pdf_with_gemini_native(
        self, pdf_path: str, context: str
    ) -> Tuple[Dict, List[LLMInteraction]]:
        """
        Analyze PDF using Gemini's native document understanding.

        This preserves visual elements like charts, diagrams, and formatting.
        """
        from app.services.archimate.multi_modal_llm_service import MultiModalLLMService

        multi_modal_service = MultiModalLLMService()

        # Build context-specific prompt
        if context == "application":
            analysis_prompt = self._build_application_analysis_prompt_for_pdf()
        elif context == "vendor":
            analysis_prompt = self._build_vendor_analysis_prompt_for_pdf()
        else:  # architecture/general
            analysis_prompt = self._build_architecture_analysis_prompt_for_pdf()

        # Use inline data for simplicity (Files API available for large docs)
        # Determine if we should use Files API based on file size
        file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        use_files_api = file_size_mb > 10  # Use Files API for files > 10MB

        try:
            logger.info(f"Starting Gemini native PDF analysis for: {pdf_path}")
            logger.info(f"File size: {file_size_mb:.2f} MB, using Files API: {use_files_api}")

            response_text, interaction = await multi_modal_service.analyze_pdf_with_gemini(
                pdf_path=pdf_path,
                prompt=analysis_prompt,
                max_tokens=8000,
                temperature=0.2,
                model="gemini - 2.5 - flash",  # Recommended for PDF processing
                use_files_api=use_files_api,
            )

            logger.info(f"Gemini response received, length: {len(response_text)} chars")
            logger.debug(f"Gemini response preview: {response_text[:500]}")

            # Parse the response
            extracted_data = self._parse_llm_response(response_text)

            element_count = len(extracted_data.get("elements", []))
            relationship_count = len(extracted_data.get("relationships", []))
            logger.info(
                f"Gemini analysis complete: {element_count} elements, {relationship_count} relationships"
            )

            return extracted_data, [interaction] if interaction else []

        except Exception as e:
            logger.error(f"Error in Gemini native PDF analysis: {e}", exc_info=True)
            # Fallback to text extraction
            logger.info("Falling back to text extraction method")
            text_content = extract_text_from_file(pdf_path, "document")

            if not text_content or text_content.startswith("Error"):
                logger.error(f"Text extraction failed: {text_content}")
                return {
                    "elements": [],
                    "relationships": [],
                    "metadata": {
                        "error": f"Text extraction failed: {text_content}",
                        "fallback_used": True,
                    },
                }, []

            logger.info(f"Extracted text length: {len(text_content)} chars")

            if context == "application":
                analysis_prompt = self._build_application_analysis_prompt(text_content)
            elif context == "vendor":
                analysis_prompt = self._build_vendor_analysis_prompt(text_content)
            else:
                # Use architecture prompt for general context
                analysis_prompt = f"""
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework.

Analyze the following document text and extract ArchiMate 3.2 elements across all layers.

DOCUMENT TEXT:
{text_content[:15000]}

Return ONLY valid JSON in this exact format:
{{
  "elements": [
    {{
      "name": "Element Name",
      "type": "ElementType",
      "layer": "motivation|strategy|business|application|technology",
      "description": "Element description",
      "properties": {{}}
    }}
  ],
  "relationships": [
    {{
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "RelationshipType",
      "description": "Relationship description"
    }}
  ],
  "metadata": {{
    "confidence": "high|medium|low",
    "notes": "Any observations about the analysis"
  }}
}}

Be thorough and extract all relevant information. Focus on accuracy and ArchiMate 3.2 compliance.
"""

            provider_name, model = LLMService._get_configured_provider()
            logger.info(f"Using fallback provider: {provider_name}, model: {model}")

            response_text, interaction = LLMService._call_llm(
                prompt=analysis_prompt,
                model=model,
                provider=provider_name,
                user_id=None,
                project_id=None,
                max_tokens=8000,
            )

            logger.info(f"Fallback LLM response received, length: {len(response_text)} chars")

            extracted_data = self._parse_llm_response(response_text)

            element_count = len(extracted_data.get("elements", []))
            relationship_count = len(extracted_data.get("relationships", []))
            logger.info(
                f"Fallback analysis complete: {element_count} elements, {relationship_count} relationships"
            )

            return extracted_data, [interaction] if interaction else []

    def _build_application_analysis_prompt_for_pdf(self) -> str:
        """Build prompt for PDF analysis (preserves visual context)."""
        return """
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework.

Analyze this PDF document and extract:

1. APPLICATION DETAILS:
   - Application name, description, and purpose
   - Technology stack (programming languages, frameworks, databases)
   - Deployment information (cloud, on-premise, hybrid)
   - Business context (domain, owner, criticality)
   - Performance and scalability requirements
   - Integration points and APIs
   - Architecture style (Monolithic, Microservices, SOA, Serverless)

2. ARCHIMATE 3.2 ELEMENTS (Application Layer):
   - ApplicationComponent: Applications, systems, modules
   - ApplicationInterface: APIs, integration interfaces
   - ApplicationService: Services exposed by applications
   - ApplicationFunction: Functions performed
   - ApplicationEvent: Events that trigger application behavior
   - DataObject: Data entities managed by applications

3. RELATED ELEMENTS:
   - Business Layer: Processes, services, actors that use the application
   - Technology Layer: Infrastructure, platforms that host the application

4. RELATIONSHIPS:
   - Serving relationships (application serves business process)
   - Realization relationships (application realizes business service)
   - Access relationships (application accesses data)
   - Flow relationships (data flows between applications)

IMPORTANT: This PDF contains visual elements (diagrams, charts, tables). Analyze both:
- Text content
- Visual diagrams and architecture drawings
- Tables and structured data
- Any embedded images or screenshots

Return ONLY valid JSON in this exact format:
{
  "application": {
    "name": "Application Name",
    "description": "Detailed description",
    "component_type": "Web Application|Mobile App|Microservice|etc",
    "technology_stack": "React, Node.js, PostgreSQL",
    "programming_languages": ["JavaScript", "Python"],
    "frameworks": ["Express.js", "React"],
    "primary_database": "PostgreSQL",
    "deployment_model": "Cloud|On-Premise|Hybrid",
    "cloud_provider": "AWS|Azure|GCP",
    "business_domain": "Sales|Finance|Manufacturing|etc",
    "business_owner": "Owner name",
    "business_criticality": "Critical|High|Medium|Low",
    "user_count": 1000,
    "user_type": "Internal|External|B2B|B2C",
    "sla_availability_percentage": 99.9,
    "response_time_target_ms": 200,
    "architecture_style": "Monolithic|Microservices|SOA|Serverless"
  },
  "elements": [
    {
      "name": "Element Name",
      "type": "ApplicationComponent|ApplicationInterface|etc",
      "layer": "application",
      "description": "Element description",
      "properties": {
        "custom_property": "value"
      }
    }
  ],
  "relationships": [
    {
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Serving|Realization|Access|Flow",
      "description": "Relationship description"
    }
  ],
  "metadata": {
    "confidence": "high|medium|low",
    "notes": "Any observations about the analysis, including visual elements found"
  }
}

Be thorough and extract all relevant information. Focus on accuracy and ArchiMate 3.2 compliance.

CRITICAL: Return ONLY the JSON object. Do not include any explanatory text, markdown formatting, or conversational responses. Start with { and end with }.
"""

    def _build_vendor_analysis_prompt_for_pdf(self) -> str:
        """Build prompt for vendor PDF analysis (preserves visual context)."""
        return """
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework and vendor management.

Analyze this PDF document and extract:

1. VENDOR DETAILS:
   - Vendor organization name and display name
   - Vendor type (software_vendor, cloud_provider, systems_integrator)
   - Headquarters location and website
   - Market intelligence (Gartner position, Forrester wave, market share)
   - Company information (year founded, employees, revenue, customers)
   - Strategic assessment (tier, partnership level, enterprise readiness)
   - Certifications and compliance frameworks
   - Support and services information
   - Risk assessment (financial health, acquisition risk, vendor lock-in)

2. ARCHIMATE 3.2 ELEMENTS:
   - BusinessActor: Vendor organization as a business actor
   - Product: Vendor products and solutions
   - Contract: Vendor agreements, SLAs, contracts
   - BusinessService: Services provided by vendor
   - ApplicationComponent: Applications/products offered by vendor
   - TechnologyService: Technology services provided

3. RELATIONSHIPS:
   - Association relationships (vendor associated with products)
   - Realization relationships (products realize capabilities)
   - Serving relationships (vendor serves organization)

IMPORTANT: This PDF contains visual elements. Analyze both:
- Text content
- Product diagrams and architecture drawings
- Comparison tables
- Pricing or feature matrices
- Any embedded screenshots or demos

Return ONLY valid JSON in this exact format:
{
  "vendor": {
    "name": "Vendor Name",
    "display_name": "Vendor Display Name",
    "vendor_type": "software_vendor|cloud_provider|systems_integrator",
    "headquarters_location": "Location",
    "website": "https://vendor.com",
    "year_founded": 2000,
    "employee_count": 10000,
    "annual_revenue_usd": 1000000000,
    "customer_count": 50000,
    "public_company": true,
    "stock_symbol": "VNDR",
    "strategic_tier": "tier_1_strategic|tier_2_preferred|tier_3_approved|tier_4_restricted",
    "enterprise_readiness_score": 85,
    "innovation_score": 80,
    "partnership_level": "strategic_partner|preferred|approved|none",
    "gartner_magic_quadrant_position": "leader|challenger|visionary|niche",
    "forrester_wave_position": "leader|strong_performer|contender|challenger",
    "market_share_percentage": 15.5,
    "financial_health_score": 90,
    "acquisition_risk": "low|medium|high",
    "technology_maturity": "emerging|established|mature|legacy",
    "vendor_lock_in_risk": 5,
    "iso_certifications": ["ISO 27001", "ISO 9001"],
    "compliance_frameworks": ["SOC 2", "GDPR", "HIPAA"],
    "description": "Vendor description",
    "strengths": ["Strength 1", "Strength 2"],
    "weaknesses": ["Weakness 1", "Weakness 2"]
  },
  "elements": [
    {
      "name": "Element Name",
      "type": "BusinessActor|Product|Contract|ApplicationComponent|etc",
      "layer": "business|application|technology",
      "description": "Element description",
      "properties": {}
    }
  ],
  "relationships": [
    {
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Association|Realization|Serving",
      "description": "Relationship description"
    }
  ],
  "metadata": {
    "confidence": "high|medium|low",
    "notes": "Any observations about the analysis, including visual elements found"
  }
}

Be thorough and extract all relevant information. Focus on accuracy and ArchiMate 3.2 compliance.

CRITICAL: Return ONLY the JSON object. Do not include any explanatory text, markdown formatting, or conversational responses. Start with { and end with }.
"""

    def _build_architecture_analysis_prompt_for_pdf(self) -> str:
        """Build prompt for general architecture PDF analysis."""
        return """
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework.

Analyze this PDF document and extract ArchiMate 3.2 elements across all layers:

1. MOTIVATION LAYER:
   - Stakeholder, Driver, Goal, Requirement, Outcome, Principle, Constraint, Meaning, Value

2. STRATEGY LAYER:
   - Resource, Capability, ValueStream, CourseOfAction

3. BUSINESS LAYER:
   - BusinessActor, BusinessRole, BusinessProcess, BusinessFunction, BusinessService, BusinessObject, Product

4. APPLICATION LAYER:
   - ApplicationComponent, ApplicationService, ApplicationInterface, DataObject

5. TECHNOLOGY LAYER:
   - Node, Device, SystemSoftware, TechnologyService

6. RELATIONSHIPS:
   - All valid ArchiMate 3.2 relationships between elements

IMPORTANT: This PDF contains visual elements. Analyze both:
- Text content
- Architecture diagrams
- Process flows
- System diagrams
- Any embedded images or screenshots

Return ONLY valid JSON in this exact format:
{
  "elements": [
    {
      "name": "Element Name",
      "type": "ElementType",
      "layer": "motivation|strategy|business|application|technology",
      "description": "Element description",
      "properties": {}
    }
  ],
  "relationships": [
    {
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "RelationshipType",
      "description": "Relationship description"
    }
  ],
  "metadata": {
    "confidence": "high|medium|low",
    "notes": "Any observations about the analysis, including visual elements found"
  }
}

Be thorough and extract all relevant information. Focus on accuracy and ArchiMate 3.2 compliance.
"""

    async def _analyze_text_file(
        self, file_path: str, provider: str, context: str
    ) -> Tuple[Dict, Optional[LLMInteraction]]:
        """Analyze text file for ArchiMate elements."""
        # Read text content with encoding detection and fallback
        text_content = None
        encodings = [
            "utf-8",
            "latin-1",
            "cp1252",
            "iso-8859-1",
            "utf-16",
            "utf-16-le",
            "utf-16-be",
        ]

        for encoding in encodings:
            try:
                with open(file_path, "r", encoding=encoding, errors="replace") as f:
                    text_content = f.read()
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if text_content is None:
            # Last resort: read as binary and decode with errors='replace'
            with open(file_path, "rb") as f:
                raw_content = f.read()
            text_content = raw_content.decode("utf-8", errors="replace")

        # Build context-specific prompt
        if context == "application":
            analysis_prompt = self._build_application_analysis_prompt(text_content)
        elif context == "vendor":
            analysis_prompt = self._build_vendor_analysis_prompt(text_content)
        else:  # architecture/general - extract all layers
            analysis_prompt = self._build_architecture_analysis_prompt(text_content)

        # Get configured provider and model
        provider_name, model = LLMService._get_configured_provider()
        if provider != provider_name:
            settings = APISettings.query.filter_by(provider=provider, enabled=True).first()
            if settings and settings.default_model:
                provider_name = provider
                model = settings.default_model

        # Call LLM service
        response_text, interaction = LLMService._call_llm(
            prompt=analysis_prompt,
            model=model,
            provider=provider_name,
            user_id=None,
            project_id=None,
            max_tokens=8000,
        )

        # Parse JSON response
        extracted_data = self._parse_llm_response(response_text)

        return extracted_data, interaction

    def _analyze_spreadsheet(
        self, file_path: str, provider: str, analysis_type: str = "general"
    ) -> Tuple[Dict, Optional[LLMInteraction]]:
        """
        Analyze spreadsheet file (CSV, XLSX, XLS) for ArchiMate elements.

        Spreadsheets are particularly useful for:
        - Application inventories/portfolios
        - Capability matrices
        - Vendor catalogs
        - Integration mappings
        - Data dictionaries

        Args:
            file_path: Path to the spreadsheet file
            provider: LLM provider to use
            analysis_type: Type of analysis ('general', 'application', 'vendor', 'capability')

        Returns:
            Tuple of (extracted_data, LLMInteraction)
        """
        from app.services.archimate.document_text_extractor import (
            extract_from_spreadsheet,
            parse_spreadsheet_to_records,
        )

        logger.info(f"Analyzing spreadsheet: {file_path}, type: {analysis_type}")

        # Extract text representation for LLM analysis
        text_content = extract_from_spreadsheet(file_path)

        if text_content.startswith("Error"):
            logger.error(f"Spreadsheet extraction failed: {text_content}")
            return {"elements": [], "relationships": [], "metadata": {"error": text_content}}, None

        # Also get structured records for additional context
        structured_data = parse_spreadsheet_to_records(file_path)

        # Build spreadsheet-specific analysis prompt
        analysis_prompt = self._build_spreadsheet_analysis_prompt(
            text_content, structured_data, analysis_type
        )

        # Get provider - use requested provider if valid, otherwise fall back to configured default
        provider_name, model = LLMService._get_configured_provider()

        if provider and provider.strip():
            # Validate requested provider exists and is enabled
            from app.models.models import APISettings

            provider_settings = APISettings.query.filter_by(
                provider=provider.strip().lower(), enabled=True
            ).first()

            if provider_settings and provider_settings.api_key and provider_settings.default_model:
                provider_name = provider.strip().lower()
                model = provider_settings.default_model
                logger.info(
                    f"Using requested provider for spreadsheet analysis: {provider_name} with model: {model}"
                )
            else:
                logger.warning(
                    f"Requested provider '{provider}' not available or not enabled for spreadsheet analysis, "
                    f"falling back to default: {provider_name}"
                )

        # Call LLM service with higher token limit for spreadsheets (can have many elements)
        # Estimate needed tokens: ~200 tokens per element + base overhead
        record_count = (
            structured_data.get("record_count", 0) if structured_data.get("success") else 0
        )
        estimated_elements = min(record_count, 200)  # Cap at 200 elements
        # Use provider-specific max tokens limit, capped appropriately
        requested_max = 16000 if estimated_elements > 50 else 8000
        max_tokens = LLMService.get_max_tokens_limit(provider_name, model, requested_max)

        response_text, interaction = LLMService._call_llm(
            prompt=analysis_prompt,
            model=model,
            provider=provider_name,
            user_id=None,
            project_id=None,
            max_tokens=max_tokens,
        )

        # Parse JSON response
        extracted_data = self._parse_llm_response(response_text)

        # Enhance metadata with spreadsheet info
        if "metadata" not in extracted_data:
            extracted_data["metadata"] = {}

        extracted_data["metadata"]["source_type"] = "spreadsheet"
        extracted_data["metadata"]["file_path"] = file_path

        if structured_data.get("success"):
            if "sheets" in structured_data:
                extracted_data["metadata"]["sheet_count"] = structured_data.get("sheet_count", 0)
                extracted_data["metadata"]["sheets"] = list(
                    structured_data.get("sheets", {}).keys()
                )
            else:
                extracted_data["metadata"]["record_count"] = structured_data.get("record_count", 0)
                extracted_data["metadata"]["column_count"] = structured_data.get("column_count", 0)

        logger.info(
            f"Spreadsheet analysis complete: {len(extracted_data.get('elements', []))} elements extracted"
        )

        return extracted_data, interaction

    def _build_spreadsheet_analysis_prompt(
        self, text_content: str, structured_data: Dict, analysis_type: str
    ) -> str:
        """Build LLM prompt for spreadsheet analysis."""

        # Get column information for context
        column_info = ""
        if structured_data.get("success"):
            if "headers" in structured_data:
                column_info = f"Columns: {', '.join(structured_data['headers'])}"
            elif "sheets" in structured_data:
                for sheet_name, sheet_data in structured_data.get("sheets", {}).items():
                    column_info += f"\nSheet '{sheet_name}' columns: {', '.join(sheet_data.get('headers', []))}"

        # Analysis type specific instructions
        type_instructions = {
            "application": """
Focus on extracting Application Portfolio elements:
- ApplicationComponent: Each row likely represents an application
- Look for columns like: Name, Description, Owner, Status, Technology, Deployment
- Extract technology stack, business domain, criticality from relevant columns
- Create relationships between applications based on integration columns
""",
            "vendor": """
Focus on extracting Vendor/Product elements:
- BusinessActor: Vendor organizations
- Product: Vendor products and solutions
- Look for columns like: Vendor Name, Product, Category, Contract, Cost
- Extract market position, partnership level, risk assessment from relevant columns
""",
            "capability": """
Focus on extracting Business Capability elements:
- Capability: Business capabilities from the matrix
- Look for columns like: Capability Name, Level, Maturity, Owner, Applications
- Create hierarchy relationships between L1, L2, L3 capabilities
- Map applications to capabilities based on coverage columns
""",
            "general": """
Analyze the spreadsheet data to identify:
- What type of data is this? (Application inventory, vendor catalog, capability matrix, etc.)
- Extract all relevant ArchiMate elements based on the data type
- Create relationships between elements based on column associations
""",
        }

        instruction = type_instructions.get(analysis_type, type_instructions["general"])

        return f"""
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework.

Analyze the following SPREADSHEET DATA and extract ArchiMate 3.2 elements.

{column_info}

{instruction}

SPREADSHEET DATA:
{text_content[:10000]}

NOTE: If this spreadsheet has many rows, focus on extracting the most important elements.
You don't need to process every single row - prioritize quality over quantity.

IMPORTANT GUIDELINES FOR SPREADSHEET ANALYSIS:
1. Each ROW typically represents ONE entity (application, vendor, capability, etc.)
2. COLUMN HEADERS indicate the attributes of each entity
3. Look for ID, Name, or Key columns to identify unique entities
4. Look for relationship columns (e.g., "Connected To", "Depends On", "Parent", "Uses")
5. Map columns to ArchiMate properties where applicable
6. Create RELATIONSHIPS based on:
   - Explicit relationship columns
   - Foreign key references between rows
   - Hierarchical structures (Parent-Child)
   - Integration/dependency mappings

Return ONLY valid JSON in this exact format:
{{
  "elements": [
    {{
      "name": "Element Name (from Name/ID column)",
      "type": "ArchiMateElementType",
      "layer": "business|application|technology",
      "description": "Description from relevant columns",
      "properties": {{
        "source_row": 1,
        "original_columns": {{"column_name": "value"}}
      }}
    }}
  ],
  "relationships": [
    {{
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Serving|Realization|Access|Flow|Composition|etc",
      "description": "Relationship description",
      "properties": {{
        "derived_from": "column_name"
      }}
    }}
  ],
  "metadata": {{
    "confidence": "high|medium|low",
    "data_type_detected": "application_inventory|vendor_catalog|capability_matrix|integration_map|other",
    "notes": "Observations about the spreadsheet structure and data quality",
    "unmapped_columns": ["columns that couldn't be mapped to ArchiMate"]
  }}
}}

Be thorough and extract ALL entities from the spreadsheet. Each row should typically produce at least one element.
"""

    def _build_application_analysis_prompt(self, document_text: str) -> str:
        """Build LLM prompt for application analysis."""
        return f"""
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework.

Analyze the following architecture document and extract:

1. APPLICATION DETAILS:
   - Application name, description, and purpose
   - Technology stack (programming languages, frameworks, databases)
   - Deployment information (cloud, on-premise, hybrid)
   - Business context (domain, owner, criticality)
   - Performance and scalability requirements
   - Integration points and APIs

2. ARCHIMATE 3.2 ELEMENTS (Application Layer):
   - ApplicationComponent: Applications, systems, modules
   - ApplicationInterface: APIs, integration interfaces
   - ApplicationService: Services exposed by applications
   - ApplicationFunction: Functions performed
   - ApplicationEvent: Events that trigger application behavior
   - DataObject: Data entities managed by applications

3. RELATED ELEMENTS:
   - Business Layer: Processes, services, actors that use the application
   - Technology Layer: Infrastructure, platforms that host the application

4. CRITICAL: Extract SEMANTIC RELATIONSHIPS for this application:

   APPLICATION INTERNAL RELATIONSHIPS:
   - ApplicationComponent → ApplicationInterface (Composition: component exposes interfaces)
   - ApplicationInterface → ApplicationService (Serving: interface provides access to service)
   - ApplicationService → ApplicationComponent (Composition: service is part of component)
   - ApplicationComponent → DataObject (Access: component reads/writes data)
   - ApplicationFunction → ApplicationComponent (Composition: function is part of component)

   APPLICATION EXTERNAL RELATIONSHIPS:
   - ApplicationComponent → BusinessProcess (Serving: application supports business process)
   - ApplicationService → BusinessService (Realization: application service realizes business service)
   - ApplicationComponent → BusinessCapability (Realization: application realizes capability)
   - ApplicationInterface → ApplicationInterface (Flow: interfaces exchange data)
   - ApplicationComponent → TechnologyService (Realization: technology realizes application)

   For each relationship, provide:
   - Source element name (must match an element in elements array)
   - Target element name (must match an element in elements array)
   - Relationship type (Serving, Realization, Access, Flow, Composition, etc.)
   - Description explaining the relationship

DOCUMENT CONTENT:
{document_text[:15000]}  # Limit to avoid token limits

Return ONLY valid JSON in this exact format:
{{
  "application": {{
    "name": "Application Name",
    "description": "Detailed description",
    "component_type": "Web Application|Mobile App|Microservice|etc",
    "technology_stack": "React, Node.js, PostgreSQL",
    "programming_languages": ["JavaScript", "Python"],
    "frameworks": ["Express.js", "React"],
    "primary_database": "PostgreSQL",
    "deployment_model": "Cloud|On-Premise|Hybrid",
    "cloud_provider": "AWS|Azure|GCP",
    "business_domain": "Sales|Finance|Manufacturing|etc",
    "business_owner": "Owner name",
    "business_criticality": "Critical|High|Medium|Low",
    "user_count": 1000,
    "user_type": "Internal|External|B2B|B2C",
    "sla_availability_percentage": 99.9,
    "response_time_target_ms": 200,
    "architecture_style": "Monolithic|Microservices|SOA|Serverless"
  }},
  "elements": [
    {{
      "name": "Element Name",
      "type": "ApplicationComponent|ApplicationInterface|etc",
      "layer": "application",
      "description": "Element description",
      "properties": {{
        "custom_property": "value"
      }}
    }}
  ],
  "relationships": [
    {{
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Serving|Realization|Access|Flow",
      "description": "Relationship description"
    }}
  ],
  "metadata": {{
    "confidence": "high|medium|low",
    "notes": "Any observations about the analysis"
  }}
}}

Be thorough and extract all relevant information. Focus on accuracy and ArchiMate 3.2 compliance.

PRIORITY: Extract relationships between:
- Application components and their interfaces/services
- Application and business processes/capabilities
- Application and technology infrastructure
- Application and data objects

The relationships array is CRITICAL - infer relationships from context if not explicitly stated.
"""

    def _build_vendor_analysis_prompt(self, document_text: str) -> str:
        """Build LLM prompt for vendor analysis."""
        return f"""
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework and vendor management.

Analyze the following vendor document and extract:

1. VENDOR DETAILS:
   - Vendor organization name and display name
   - Vendor type (software_vendor, cloud_provider, systems_integrator)
   - Headquarters location and website
   - Market intelligence (Gartner position, Forrester wave, market share)
   - Company information (year founded, employees, revenue, customers)
   - Strategic assessment (tier, partnership level, enterprise readiness)
   - Certifications and compliance frameworks
   - Support and services information
   - Risk assessment (financial health, acquisition risk, vendor lock-in)

2. ARCHIMATE 3.2 ELEMENTS:
   - BusinessActor: Vendor organization as a business actor
   - Product: Vendor products and solutions
   - Contract: Vendor agreements, SLAs, contracts
   - BusinessService: Services provided by vendor
   - ApplicationComponent: Applications/products offered by vendor
   - TechnologyService: Technology services provided

3. CRITICAL: Extract SEMANTIC RELATIONSHIPS for this vendor:

   VENDOR-PRODUCT RELATIONSHIPS:
   - VendorOrganization (BusinessActor) → Product (Composition: vendor owns products)
   - Product → ApplicationComponent (Realization: product realizes application component)
   - Product → TechnologyService (Realization: product realizes technology service)
   - VendorOrganization → Contract (Composition: vendor has contracts)

   VENDOR-ORGANIZATION RELATIONSHIPS:
   - VendorOrganization → BusinessService (Serving: vendor provides business services)
   - VendorOrganization → ApplicationComponent (Association: vendor provides applications)
   - Product → BusinessCapability (Realization: product realizes business capability)

   For each relationship, provide:
   - Source element name (must match an element in elements array)
   - Target element name (must match an element in elements array)
   - Relationship type (Association, Realization, Serving, Composition, etc.)
   - Description explaining the relationship

DOCUMENT CONTENT:
{document_text[:15000]}  # Limit to avoid token limits

Return ONLY valid JSON in this exact format:
{{
  "vendor": {{
    "name": "Vendor Name",
    "display_name": "Vendor Display Name",
    "vendor_type": "software_vendor|cloud_provider|systems_integrator",
    "headquarters_location": "Location",
    "website": "https://vendor.com",
    "year_founded": 2000,
    "employee_count": 10000,
    "annual_revenue_usd": 1000000000,
    "customer_count": 50000,
    "public_company": true,
    "stock_symbol": "VNDR",
    "strategic_tier": "tier_1_strategic|tier_2_preferred|tier_3_approved|tier_4_restricted",
    "enterprise_readiness_score": 85,
    "innovation_score": 80,
    "partnership_level": "strategic_partner|preferred|approved|none",
    "gartner_magic_quadrant_position": "leader|challenger|visionary|niche",
    "forrester_wave_position": "leader|strong_performer|contender|challenger",
    "market_share_percentage": 15.5,
    "financial_health_score": 90,
    "acquisition_risk": "low|medium|high",
    "technology_maturity": "emerging|established|mature|legacy",
    "vendor_lock_in_risk": 5,
    "iso_certifications": ["ISO 27001", "ISO 9001"],
    "compliance_frameworks": ["SOC 2", "GDPR", "HIPAA"],
    "description": "Vendor description",
    "strengths": ["Strength 1", "Strength 2"],
    "weaknesses": ["Weakness 1", "Weakness 2"]
  }},
  "elements": [
    {{
      "name": "Element Name",
      "type": "BusinessActor|Product|Contract|ApplicationComponent|etc",
      "layer": "business|application|technology",
      "description": "Element description",
      "properties": {{}}
    }}
  ],
  "relationships": [
    {{
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Association|Realization|Serving",
      "description": "Relationship description"
    }}
  ],
  "metadata": {{
    "confidence": "high|medium|low",
    "notes": "Any observations about the analysis"
  }}
}}

Be thorough and extract all relevant information. Focus on accuracy and ArchiMate 3.2 compliance.

PRIORITY: Extract relationships between:
- Vendor organization and their products
- Products and application/technology components
- Vendor and business capabilities/services
- Products and contracts/agreements

The relationships array is CRITICAL - infer relationships from context if not explicitly stated.
"""

    def _build_architecture_analysis_prompt(self, document_text: str) -> str:
        """
        Build LLM prompt for general architecture analysis (all layers).

        This prompt works for ALL document types:
        - General architecture documents
        - Requirements documents
        - Design documents
        - Policy documents
        - Technical specifications
        - Business process documents
        - Application lists and inventories
        - Vendor catalogs
        - Any other architecture-related content

        It extracts elements from all ArchiMate 3.2 layers and identifies relationships.
        """
        return f"""
You are an Enterprise Architecture expert specializing in ArchiMate 3.2 framework.

Analyze the following document and extract ALL architecture-related entities, mapping them to ArchiMate 3.2 elements.

IMPORTANT: Extract entities even if they're not explicitly described as architecture elements:
- Application names (e.g., "Salesforce", "SAP", "Oracle") → ApplicationComponent
- System names (e.g., "CRM System", "ERP System") → ApplicationComponent
- Vendor/company names (e.g., "Microsoft", "IBM") → BusinessActor
- Product names (e.g., "Office 365", "Azure") → Product
- Process names (e.g., "Order Processing", "Customer Onboarding") → BusinessProcess
- Service names (e.g., "Payment Service", "Authentication Service") → ApplicationService or BusinessService
- Database/data names (e.g., "Customer Database", "Product Catalog") → DataObject
- API/Interface names (e.g., "REST API", "SOAP Interface") → ApplicationInterface

If the document contains simple lists (application names, vendor names, etc.), extract each item as an appropriate ArchiMate element type.

Extract elements across ALL ArchiMate 3.2 layers:

1. MOTIVATION LAYER:
   - Stakeholder: Individuals, groups, or organizations with interest
   - Driver: Internal/external factors creating needs for change
   - Goal: High-level statement of intent
   - Requirement: Formal statements of need
   - Outcome: End results
   - Principle: Normative statements of intent
   - Constraint: External limitations or restrictions
   - Meaning: Knowledge or expertise
   - Value: Relative worth or importance

2. STRATEGY LAYER:
   - Resource: Assets that can be used
   - Capability: Ability to employ resources
   - ValueStream: Series of activities that create value
   - CourseOfAction: Approach or plan for achieving goals

3. BUSINESS LAYER:
   - BusinessActor: Organizational unit, person, or role
   - BusinessRole: Responsibility for performing behavior
   - BusinessProcess: Sequence of activities
   - BusinessFunction: Ongoing activity
   - BusinessService: Service that fulfills needs
   - BusinessObject: Passive business entity
   - Product: Coherent collection of services/contract

4. APPLICATION LAYER:
   - ApplicationComponent: Modular, deployable, replaceable parts
   - ApplicationInterface: Access point to application services
   - ApplicationService: Services exposed by applications
   - ApplicationFunction: Internal behavior element
   - DataObject: Data structured for processing

5. TECHNOLOGY LAYER:
   - Node: Computational or physical resource
   - Device: Physical IT resource
   - SystemSoftware: Software environment for application components
   - TechnologyService: Services provided by technology
   - Artifact: Physical piece of data
   - TechnologyInterface: Access point to technology services

6. IMPLEMENTATION & MIGRATION LAYER:
   - WorkPackage: Series of actions to achieve objectives
   - Deliverable: Outputs of work packages
   - Gap: Difference between two plateaus

CRITICAL: Extract SEMANTIC RELATIONSHIPS between elements. Pay special attention to:

1. APPLICATION LAYER RELATIONSHIPS:
   - ApplicationComponent → ApplicationInterface (Composition: components expose interfaces)
   - ApplicationInterface → ApplicationService (Serving: interfaces provide access to services)
   - ApplicationService → ApplicationComponent (Composition: services are part of components)
   - ApplicationComponent → ApplicationComponent (Serving/Flow: components interact with each other)
   - DataObject → ApplicationComponent (Access: components access data objects)

2. VENDOR & PRODUCT RELATIONSHIPS:
   - VendorOrganization → Product (Composition: vendors own products)
   - Product → ApplicationComponent (Realization: products realize application components)
   - VendorOrganization → ApplicationComponent (Association: vendors provide components)

3. BUSINESS CAPABILITY RELATIONSHIPS:
   - BusinessCapability → ApplicationComponent (Realization: applications realize capabilities)
   - BusinessCapability → BusinessProcess (Realization: processes realize capabilities)
   - BusinessProcess → ApplicationService (Serving: services support processes)

4. CROSS-LAYER RELATIONSHIPS:
   - Goal → Requirement (Realization: requirements realize goals)
   - Requirement → BusinessCapability (Association: requirements relate to capabilities)
   - BusinessProcess → ApplicationService (Serving: services support processes)
   - ApplicationService → TechnologyService (Realization: technology realizes application services)

5. STANDARD ARCHIMATE RELATIONSHIPS (extract any of these you can identify):
   - Serving (provides functionality to)
   - Realization (makes real/concrete)
   - Assignment (responsibility)
   - Access (reads/writes data)
   - Flow (transfer between elements)
   - Triggering (temporal dependency)
   - Association (unspecified relationship)
   - Aggregation (combines elements)
   - Composition (consists of)
   - Specialization (is a kind of)
   - Influence (affects or impacts)

IMPORTANT: For each relationship, identify:
   - Source element name (must match an element name in the elements array)
   - Target element name (must match an element name in the elements array)
   - Relationship type (exact ArchiMate relationship type)
   - Description explaining why this relationship exists

DOCUMENT CONTENT:
{document_text[:15000]}

Return ONLY valid JSON in this exact format:
{{
  "elements": [
    {{
      "name": "Element Name",
      "type": "ElementType (e.g., BusinessProcess, ApplicationComponent, Goal)",
      "layer": "motivation|strategy|business|application|technology|implementation",
      "description": "Element description",
      "properties": {{}}
    }}
  ],
  "relationships": [
    {{
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Serving|Realization|Assignment|Access|Flow|etc",
      "description": "Relationship description"
    }}
  ],
  "metadata": {{
    "confidence": "high|medium|low",
    "notes": "Any observations about the analysis",
    "layers_found": ["business", "application"],
    "dominant_layer": "application"
  }}
}}

Be thorough and extract ALL relevant elements from ALL layers present in the document.

EXTRACTION GUIDELINES - Extract entities even if they're simple names:
- Application names (e.g., "Salesforce", "SAP", "Oracle") → ApplicationComponent
- System names (e.g., "CRM System", "ERP System") → ApplicationComponent
- Vendor/company names (e.g., "Microsoft", "IBM") → BusinessActor
- Product names (e.g., "Office 365", "Azure") → Product
- Process names (e.g., "Order Processing") → BusinessProcess
- Service names (e.g., "Payment Service") → ApplicationService or BusinessService
- Database names (e.g., "Customer Database") → DataObject
- API/Interface names (e.g., "REST API") → ApplicationInterface

If the document contains simple lists or inventories, extract each item as an appropriate ArchiMate element type. It's better to extract too many than too few.

Focus on accuracy and ArchiMate 3.2 compliance.

PRIORITY: Extract relationships between:
- Applications and their interfaces/services
- Vendors and their products
- Business capabilities and applications
- Requirements and their realizations
- Goals and their outcomes

The relationships array is CRITICAL - do not skip relationship extraction even if the document is long.
If relationships are not explicitly stated, infer them from context (e.g., if a document mentions "Salesforce API",
create a relationship: ApplicationInterface "Salesforce API" → ApplicationService "Salesforce Integration").
"""

    def _parse_llm_response(self, response_text: str) -> Dict:
        """Parse LLM JSON response, handling markdown code blocks."""
        try:
            # Log raw response for debugging (first 500 chars)
            logger.info(f"Parsing LLM response (length: {len(response_text)} chars)")
            logger.debug(f"Response preview: {response_text[:500]}")

            # Check if response is an error message (not JSON)
            if (
                response_text.strip().startswith("Error:")
                or "failed to generate" in response_text.lower()
            ):
                error_msg = response_text.strip()
                logger.error(f"LLM returned error message instead of JSON: {error_msg[:200]}")
                return {
                    "elements": [],
                    "relationships": [],
                    "metadata": {
                        "error": error_msg,
                        "error_type": "llm_error",
                        "suggestion": "Try using a different provider (Claude/GPT - 4) or enable Simple Parsing mode for large documents",
                    },
                }

            # Try to extract JSON from markdown code blocks
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                json_text = response_text[json_start:json_end].strip()
                logger.info("Extracted JSON from ```json code block")
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                json_text = response_text[json_start:json_end].strip()
                logger.info("Extracted JSON from ``` code block")
            else:
                json_text = response_text.strip()
                logger.info("Using response text directly as JSON")

            parsed = json.loads(json_text)

            # Validate structure
            if not isinstance(parsed, dict):
                logger.error(f"Parsed JSON is not a dict, got: {type(parsed)}")
                return {
                    "elements": [],
                    "relationships": [],
                    "metadata": {"error": "Invalid response format - not a dictionary"},
                }

            # Ensure required keys exist
            if "elements" not in parsed:
                logger.warning("Response missing 'elements' key, adding empty list")
                parsed["elements"] = []
            if "relationships" not in parsed:
                logger.warning("Response missing 'relationships' key, adding empty list")
                parsed["relationships"] = []
            if "metadata" not in parsed:
                parsed["metadata"] = {}

            # Log what was parsed
            element_count = len(parsed.get("elements", []))
            relationship_count = len(parsed.get("relationships", []))
            logger.info(
                f"Successfully parsed: {element_count} elements, {relationship_count} relationships"
            )

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error: {e}")
            logger.error(
                f"Failed to parse JSON. Response text (first 1000 chars): {response_text[:1000]}"
            )
            logger.error(
                f"Response text (last 500 chars): {response_text[-500:] if len(response_text) > 500 else response_text}"
            )

            # Check for truncation indicators
            import re

            is_truncated = (
                response_text.count("{") > response_text.count("}")
                or response_text.count("[") > response_text.count("]")
                or '"description":' in response_text
                and response_text.count('"description"')
                > response_text.count('"description": "[^"]*"')
            )

            if is_truncated:
                logger.warning("Detected truncated JSON response - attempting robust extraction")

            # Try to find the main JSON object (look for {"elements": pattern)
            # Look for the main JSON structure starting with {"elements":
            main_json_pattern = r'\{\s*"elements"\s*:\s*\[.*?\](?:\s*,\s*"relationships"\s*:\s*\[.*?\])?(?:\s*,\s*"metadata"\s*:\s*\{.*?\})?\s*\}'
            main_matches = re.findall(main_json_pattern, response_text, re.DOTALL)

            if main_matches:
                logger.info(
                    f"Found {len(main_matches)} potential main JSON structures, trying to parse the largest one"
                )
                largest_match = max(main_matches, key=len)
                try:
                    # Try to fix common JSON issues (trailing commas, incomplete strings)
                    # Remove trailing commas before closing brackets/braces
                    fixed_json = re.sub(r",(\s*[}\]])", r"\1", largest_match)
                    parsed = json.loads(fixed_json)
                    logger.info("Successfully parsed JSON from main structure regex match")
                    # Ensure it has the right structure
                    if "elements" in parsed:
                        return parsed
                except json.JSONDecodeError as parse_error:
                    logger.warning(f"Failed to parse main JSON structure: {parse_error}")

            # Fallback: Try to extract just the elements array and build a minimal structure
            # Enhanced pattern to handle truncated responses
            elements_pattern = r'"elements"\s*:\s*\[(.*?)(?:\]|$)'
            elements_match = re.search(elements_pattern, response_text, re.DOTALL)
            if elements_match:
                logger.info("Attempting to extract elements array directly")
                try:
                    # Try to parse just the elements
                    elements_text = "[" + elements_match.group(1)

                    # Fix incomplete strings (truncated descriptions)
                    # Remove incomplete string values at the end
                    elements_text = re.sub(
                        r'"description"\s*:\s*"[^"]*$', '"description": ""', elements_text
                    )
                    elements_text = re.sub(r'"name"\s*:\s*"[^"]*$', '"name": ""', elements_text)

                    # Fix common issues: remove trailing commas, fix incomplete objects
                    elements_text = re.sub(
                        r",(\s*\})", r"\1", elements_text
                    )  # Remove trailing commas
                    elements_text = re.sub(
                        r",(\s*\])", r"\1", elements_text
                    )  # Remove trailing commas before array close

                    # Try to complete incomplete objects (remove last incomplete element)
                    open_braces = elements_text.count("{")
                    close_braces = elements_text.count("}")
                    if open_braces > close_braces:
                        # Find the last incomplete object and remove it
                        last_open = elements_text.rfind("{")
                        if last_open > 0:
                            # Find the start of this incomplete object
                            # Look backwards for the previous complete object
                            before_incomplete = elements_text[:last_open].rstrip()
                            if before_incomplete.endswith(","):
                                before_incomplete = before_incomplete[:-1]
                            elements_text = before_incomplete + "]"

                    # Ensure proper closing
                    if not elements_text.endswith("]"):
                        elements_text = elements_text.rstrip().rstrip(",") + "]"

                    elements = json.loads(elements_text)
                    if isinstance(elements, list):
                        # Filter out invalid elements (missing required fields)
                        valid_elements = []
                        for elem in elements:
                            if isinstance(elem, dict) and elem.get("name") and elem.get("type"):
                                valid_elements.append(elem)

                        logger.info(
                            f"Successfully extracted {len(valid_elements)} valid elements from partial JSON (out of {len(elements)} total)"
                        )
                        return {
                            "elements": valid_elements,
                            "relationships": [],
                            "metadata": {
                                "confidence": "medium",
                                "notes": "Extracted from partial/incomplete JSON response",
                                "partial_extraction": True,
                                "original_count": len(elements),
                                "valid_count": len(valid_elements),
                            },
                        }
                except (json.JSONDecodeError, AttributeError) as extract_error:
                    logger.warning(f"Failed to extract elements array: {extract_error}")

            # Last resort: Try to extract all element objects and build structure
            # Enhanced pattern to handle truncated descriptions
            element_objects = []
            # More flexible pattern that handles incomplete strings
            element_pattern = r'\{\s*"name"\s*:\s*"([^"]*)"\s*,\s*"type"\s*:\s*"([^"]*)".*?\}'
            element_matches = re.findall(element_pattern, response_text, re.DOTALL)

            # Also try to find incomplete element objects (with truncated descriptions)
            incomplete_pattern = r'\{\s*"name"\s*:\s*"([^"]*)"\s*,\s*"type"\s*:\s*"([^"]*)"[^}]*'
            incomplete_matches = re.findall(incomplete_pattern, response_text, re.DOTALL)

            # Combine both patterns
            all_matches = list(set(element_matches + incomplete_matches))

            if all_matches:
                logger.info(
                    f"Found {len(all_matches)} potential element objects, attempting to parse"
                )
                for name, elem_type in all_matches:
                    if name and elem_type:
                        try:
                            # Try to find the full object for this element
                            # Fixed: use single brace, not double
                            obj_pattern = (
                                r'\{\s*"name"\s*:\s*"'
                                + re.escape(name)
                                + r'"\s*,\s*"type"\s*:\s*"'
                                + re.escape(elem_type)
                                + r'"[^}]*'
                            )
                            obj_match = re.search(obj_pattern, response_text, re.DOTALL)
                            if obj_match:
                                obj_text = obj_match.group(0)
                                # Fix incomplete strings
                                obj_text = re.sub(
                                    r'"description"\s*:\s*"[^"]*$', '"description": ""', obj_text
                                )
                                # Try to close the object
                                if not obj_text.endswith("}"):
                                    obj_text = obj_text.rstrip().rstrip(",") + "}"
                                # Fix trailing commas
                                obj_text = re.sub(r",(\s*\})", r"\1", obj_text)

                                parsed_elem = json.loads(obj_text)
                                if (
                                    isinstance(parsed_elem, dict)
                                    and parsed_elem.get("name")
                                    and parsed_elem.get("type")
                                ):
                                    element_objects.append(parsed_elem)
                                else:
                                    # Create minimal element from name and type
                                    element_objects.append(
                                        {
                                            "name": name,
                                            "type": elem_type,
                                            "description": "",
                                            "layer": "",
                                            "properties": {},
                                        }
                                    )
                        except (json.JSONDecodeError, ValueError):
                            # Create minimal element as fallback
                            try:
                                element_objects.append(
                                    {
                                        "name": name,
                                        "type": elem_type,
                                        "description": "",
                                        "layer": "",
                                        "properties": {},
                                    }
                                )
                            except (ValueError, KeyError, TypeError):
                                continue

                if element_objects:
                    logger.info(
                        f"Successfully extracted {len(element_objects)} elements from individual objects"
                    )
                    return {
                        "elements": element_objects,
                        "relationships": [],
                        "metadata": {
                            "confidence": "medium",
                            "notes": "Extracted elements from individual JSON objects in response",
                            "partial_extraction": True,
                        },
                    }

            # Final fallback: Try to find any JSON-like content
            json_pattern = r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}"
            matches = re.findall(json_pattern, response_text, re.DOTALL)
            if matches:
                logger.info(
                    f"Found {len(matches)} potential JSON blocks, trying to parse the largest one"
                )
                largest_match = max(matches, key=len)
                try:
                    # Fix trailing commas
                    fixed_json = re.sub(r",(\s*[}\]])", r"\1", largest_match)
                    parsed = json.loads(fixed_json)
                    logger.info("Successfully parsed JSON from regex match")
                    # Check if it's the full structure
                    if isinstance(parsed, dict):
                        if "elements" in parsed:
                            return parsed
                        # If it's a single element object, wrap it
                        elif "name" in parsed and "type" in parsed:
                            return {
                                "elements": [parsed],
                                "relationships": [],
                                "metadata": {
                                    "confidence": "low",
                                    "notes": "Extracted single element from malformed JSON",
                                },
                            }
                    return {
                        "elements": [],
                        "relationships": [],
                        "metadata": {"error": "Invalid response format"},
                    }
                except json.JSONDecodeError:
                    logger.exception("Failed to compute fixed_json")
                    pass

            return {
                "elements": [],
                "relationships": [],
                "metadata": {
                    "error": f"JSON parsing failed: {str(e)}",
                    "raw_response": response_text[:2000],  # Increased from 1000
                    "response_length": len(response_text),
                },
            }

    def _enhance_with_intelligent_discovery(
        self, extracted_data: Dict, context: str, document_text: Optional[str] = None
    ) -> Dict:
        """
        Enhance extracted data with intelligent relationship discovery.

        Uses:
        - Graph-based relationship inference
        - Semantic similarity matching
        - Co-occurrence analysis
        - Pattern-based suggestions
        """
        if not self.graph_service or not self.semantic_service or not self.pattern_service:
            return extracted_data

        elements = extracted_data.get("elements", [])
        relationships = extracted_data.get("relationships", [])

        if not elements:
            return extracted_data

        enhanced_relationships = list(relationships)

        try:
            # 1. Graph-based discovery
            graph_relationships = self.graph_service.discover_relationships_via_graph(elements)
            enhanced_relationships.extend(graph_relationships)

            # 2. Co-occurrence analysis (if document text available)
            if document_text and self.semantic_service:
                co_occurrence_rels = self.semantic_service.analyze_co_occurrence(
                    elements, document_text
                )
                enhanced_relationships.extend(co_occurrence_rels)

            # 3. Pattern-based suggestions
            for elem in elements:
                elem_type = elem.get("type", "")
                elem_layer = elem.get("layer", "")

                # Apply patterns to other elements
                for other_elem in elements:
                    if other_elem == elem:
                        continue

                    other_type = other_elem.get("type", "")
                    other_layer = other_elem.get("layer", "")

                    # Check if pattern applies
                    suggestions = self.pattern_service.suggest_relationships_from_patterns(
                        elem_type, other_type, elem_layer, other_layer
                    )

                    if suggestions:
                        best_suggestion = suggestions[0]
                        enhanced_relationships.append(
                            {
                                "source": elem.get("name"),
                                "target": other_elem.get("name"),
                                "type": best_suggestion["relationship_type"],
                                "confidence": best_suggestion["confidence"],
                                "description": best_suggestion.get("evidence", ""),
                                "discovery_method": "pattern_based",
                            }
                        )

            # Deduplicate relationships
            seen = set()
            unique_relationships = []
            for rel in enhanced_relationships:
                source = rel.get("source") or rel.get("source_name", "")
                target = rel.get("target") or rel.get("target_name", "")
                rel_type = rel.get("type") or rel.get("relationship_type", "")
                key = (source, target, rel_type)

                if key not in seen:
                    seen.add(key)
                    unique_relationships.append(rel)

            extracted_data["relationships"] = unique_relationships
            if "metadata" not in extracted_data:
                extracted_data["metadata"] = {}
            extracted_data["metadata"]["enhanced_discovery"] = True
            discovery_methods = ["llm_extraction", "graph_traversal", "pattern_matching"]
            if document_text:
                discovery_methods.append("co_occurrence")
            extracted_data["metadata"]["discovery_methods"] = discovery_methods

        except Exception as e:
            logger.warning(f"Intelligent discovery enhancement failed: {e}")

        return extracted_data

    def _extract_application_data(
        self, extracted_data: Dict, application_id: Optional[int]
    ) -> Dict:
        """Extract application-specific data from analysis results."""
        app_data = extracted_data.get("application", {})

        # If application_id provided, fetch existing application
        if application_id:
            existing_app = ApplicationComponent.query.get(application_id)
            if existing_app:
                # Merge with existing data
                app_data["id"] = application_id
                app_data["existing_name"] = existing_app.name
                app_data["existing_description"] = existing_app.description

        return app_data

    def _extract_vendor_data(self, extracted_data: Dict, vendor_id: Optional[int]) -> Dict:
        """Extract vendor-specific data from analysis results."""
        vendor_data = extracted_data.get("vendor", {})

        # If vendor_id provided, fetch existing vendor
        if vendor_id:
            existing_vendor = VendorOrganization.query.get(vendor_id)
            if existing_vendor:
                # Merge with existing data
                vendor_data["id"] = vendor_id
                vendor_data["existing_name"] = existing_vendor.name
                vendor_data["existing_description"] = existing_vendor.description

        return vendor_data

    def _validate_elements(
        self, elements: List[Dict], relationships: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Validate ArchiMate elements and relationships against ArchiMate 3.2 metamodel rules.

        Args:
            elements: List of element dictionaries
            relationships: Optional list of relationship dictionaries to validate

        Returns:
            Validation results dictionary with errors and warnings
        """
        validation_results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "element_errors": [],
            "relationship_errors": [],
            "relationship_warnings": [],
        }

        # Build element index for relationship validation
        element_index = {}
        temp_elements = []

        # Validate elements
        for element in elements:
            element_type = element.get("type")
            layer = element.get("layer", "").lower()
            element_name = element.get("name", "Unknown")

            # Create a temporary ArchiMateElement object for validation
            from app.models.models import ArchiMateElement

            temp_element = ArchiMateElement(name=element_name, type=element_type, layer=layer)
            db.session.add(temp_element)
            db.session.flush()  # Get ID without committing
            temp_elements.append(temp_element)

            # Store in index for relationship validation
            element_index[element_name] = temp_element

            try:
                # Validate element type belongs to layer
                is_valid, error = self.validator.validate_element_type(temp_element)

                if not is_valid:
                    validation_results["valid"] = False
                    validation_results["errors"].append(
                        {
                            "element": element_name,
                            "type": "element_type",
                            "error": error,
                            "severity": "error",
                        }
                    )
                    validation_results["element_errors"].append(
                        {
                            "element": element_name,
                            "type": element_type,
                            "layer": layer,
                            "error": error,
                        }
                    )
            except Exception as e:
                validation_results["warnings"].append(
                    {
                        "element": element_name,
                        "type": "validation_exception",
                        "error": f"Validation exception: {str(e)}",
                        "severity": "warning",
                    }
                )

        # Validate relationships if provided
        if relationships:
            for rel in relationships:
                source_name = rel.get("source")
                target_name = rel.get("target")
                rel_type = rel.get("type", "Association")

                if not source_name or not target_name:
                    validation_results["warnings"].append(
                        {
                            "relationship": f"{source_name} -> {target_name}",
                            "type": "missing_endpoints",
                            "error": "Relationship missing source or target",
                            "severity": "warning",
                        }
                    )
                    continue

                source_element = element_index.get(source_name)
                target_element = element_index.get(target_name)

                if not source_element or not target_element:
                    validation_results["warnings"].append(
                        {
                            "relationship": f"{source_name} -> {target_name}",
                            "type": "unknown_element",
                            "error": "Source or target element not found in extracted elements",
                            "severity": "warning",
                        }
                    )
                    continue

                # Create temporary relationship for validation
                from app.models.models import ArchiMateRelationship

                temp_relationship = ArchiMateRelationship(
                    source_id=source_element.id,
                    target_id=target_element.id,
                    type=rel_type,
                    name=rel.get("name", ""),
                )
                temp_relationship.source = source_element
                temp_relationship.target = target_element

                try:
                    # Validate relationship against metamodel rules
                    is_valid, error, allowed_types = self.validator.validate_relationship(
                        temp_relationship
                    )

                    if not is_valid:
                        validation_results["valid"] = False
                        validation_results["errors"].append(
                            {
                                "relationship": f"{source_name} ({source_element.type}) -> {target_name} ({target_element.type})",
                                "type": "relationship_rule",
                                "error": error,
                                "severity": "error",
                                "relationship_type": rel_type,
                                "allowed_types": allowed_types if allowed_types else [],
                            }
                        )
                        validation_results["relationship_errors"].append(
                            {
                                "source": source_name,
                                "target": target_name,
                                "source_type": source_element.type,
                                "target_type": target_element.type,
                                "relationship_type": rel_type,
                                "error": error,
                                "allowed_types": allowed_types if allowed_types else [],
                            }
                        )
                    elif allowed_types and len(allowed_types) > 1:
                        # Valid but there are other options - informational
                        validation_results["warnings"].append(
                            {
                                "relationship": f"{source_name} -> {target_name}",
                                "type": "relationship_alternatives",
                                "error": f"Valid relationship. Other allowed types: {', '.join(allowed_types)}",
                                "severity": "info",
                                "relationship_type": rel_type,
                                "alternatives": allowed_types,
                            }
                        )
                except Exception as e:
                    validation_results["warnings"].append(
                        {
                            "relationship": f"{source_name} -> {target_name}",
                            "type": "validation_exception",
                            "error": f"Relationship validation exception: {str(e)}",
                            "severity": "warning",
                        }
                    )

        # Clean up temporary elements
        for temp_element in temp_elements:
            try:
                db.session.expunge(temp_element)
            except Exception as e:
                logger.debug("Failed to expunge temp element: %s", e)

        return validation_results

    def apply_analysis_to_application(
        self, application_id: int, analysis_results: Dict, user_id: Optional[int] = None
    ) -> Tuple[ApplicationComponent, List[ArchiMateElement]]:
        """
        Apply analysis results to an application component.

        Args:
            application_id: ID of application to update
            analysis_results: Results from analyze_document_for_application
            user_id: User ID performing the update

        Returns:
            Tuple of (updated_application, created_archimate_elements)
        """
        application = ApplicationComponent.query.get_or_404(application_id)
        app_data = analysis_results.get("application_data", {})
        archimate_elements_data = analysis_results.get("archimate_elements", [])

        # Update application fields
        if app_data.get("name") and not application.name:
            application.name = app_data["name"]
        if app_data.get("description"):
            application.description = app_data["description"]
        if app_data.get("component_type"):
            application.component_type = app_data["component_type"]
        if app_data.get("technology_stack"):
            application.technology_stack = app_data["technology_stack"]
        if app_data.get("programming_languages"):
            application.programming_languages = json.dumps(app_data["programming_languages"])
        if app_data.get("frameworks"):
            application.frameworks = json.dumps(app_data["frameworks"])
        if app_data.get("primary_database"):
            application.primary_database = app_data["primary_database"]
        if app_data.get("deployment_model"):
            application.deployment_model = app_data["deployment_model"]
        if app_data.get("cloud_provider"):
            application.cloud_provider = app_data["cloud_provider"]
        if app_data.get("business_domain"):
            application.business_domain = app_data["business_domain"]
        if app_data.get("business_owner"):
            application.business_owner = app_data["business_owner"]
        if app_data.get("business_criticality"):
            application.business_criticality = app_data["business_criticality"]
        if app_data.get("user_count"):
            application.user_count = app_data["user_count"]
        if app_data.get("user_type"):
            application.user_type = app_data["user_type"]
        if app_data.get("sla_availability_percentage"):
            application.sla_availability_percentage = app_data["sla_availability_percentage"]
        if app_data.get("response_time_target_ms"):
            application.response_time_target_ms = app_data["response_time_target_ms"]
        if app_data.get("architecture_style"):
            application.architecture_style = app_data["architecture_style"]

        # Create ArchiMate elements
        created_elements = []
        for elem_data in archimate_elements_data:
            element = ArchiMateElement(
                name=elem_data["name"],
                type=elem_data["type"],
                layer=elem_data.get("layer", "application"),
                description=elem_data.get("description", ""),
                properties=json.dumps(elem_data.get("properties", {})),
                application_component_id=application.id,
            )
            db.session.add(element)
            created_elements.append(element)

        db.session.commit()

        return application, created_elements

    def apply_analysis_to_vendor(
        self, vendor_id: int, analysis_results: Dict, user_id: Optional[int] = None
    ) -> Tuple[VendorOrganization, List[ArchiMateElement]]:
        """
        Apply analysis results to a vendor organization.

        Args:
            vendor_id: ID of vendor to update
            analysis_results: Results from analyze_document_for_vendor
            user_id: User ID performing the update

        Returns:
            Tuple of (updated_vendor, created_archimate_elements)
        """
        vendor = VendorOrganization.query.get_or_404(vendor_id)
        vendor_data = analysis_results.get("vendor_data", {})
        archimate_elements_data = analysis_results.get("archimate_elements", [])

        # Update vendor fields
        if vendor_data.get("display_name"):
            vendor.display_name = vendor_data["display_name"]
        if vendor_data.get("vendor_type"):
            vendor.vendor_type = vendor_data["vendor_type"]
        if vendor_data.get("headquarters_location"):
            vendor.headquarters_location = vendor_data["headquarters_location"]
        if vendor_data.get("website"):
            vendor.website = vendor_data["website"]
        if vendor_data.get("year_founded"):
            vendor.year_founded = vendor_data["year_founded"]
        if vendor_data.get("employee_count"):
            vendor.employee_count = vendor_data["employee_count"]
        if vendor_data.get("annual_revenue_usd"):
            vendor.annual_revenue_usd = vendor_data["annual_revenue_usd"]
        if vendor_data.get("customer_count"):
            vendor.customer_count = vendor_data["customer_count"]
        if vendor_data.get("public_company") is not None:
            vendor.public_company = vendor_data["public_company"]
        if vendor_data.get("stock_symbol"):
            vendor.stock_symbol = vendor_data["stock_symbol"]
        if vendor_data.get("strategic_tier"):
            vendor.strategic_tier = vendor_data["strategic_tier"]
        if vendor_data.get("enterprise_readiness_score"):
            vendor.enterprise_readiness_score = vendor_data["enterprise_readiness_score"]
        if vendor_data.get("innovation_score"):
            vendor.innovation_score = vendor_data["innovation_score"]
        if vendor_data.get("partnership_level"):
            vendor.partnership_level = vendor_data["partnership_level"]
        if vendor_data.get("gartner_magic_quadrant_position"):
            vendor.gartner_magic_quadrant_position = vendor_data["gartner_magic_quadrant_position"]
        if vendor_data.get("forrester_wave_position"):
            vendor.forrester_wave_position = vendor_data["forrester_wave_position"]
        if vendor_data.get("market_share_percentage"):
            vendor.market_share_percentage = vendor_data["market_share_percentage"]
        if vendor_data.get("financial_health_score"):
            vendor.financial_health_score = vendor_data["financial_health_score"]
        if vendor_data.get("acquisition_risk"):
            vendor.acquisition_risk = vendor_data["acquisition_risk"]
        if vendor_data.get("technology_maturity"):
            vendor.technology_maturity = vendor_data["technology_maturity"]
        if vendor_data.get("vendor_lock_in_risk"):
            vendor.vendor_lock_in_risk = vendor_data["vendor_lock_in_risk"]
        if vendor_data.get("iso_certifications"):
            vendor.iso_certifications = json.dumps(vendor_data["iso_certifications"])
        if vendor_data.get("compliance_frameworks"):
            vendor.compliance_frameworks = json.dumps(vendor_data["compliance_frameworks"])
        if vendor_data.get("description"):
            vendor.description = vendor_data["description"]
        if vendor_data.get("strengths"):
            vendor.strengths = json.dumps(vendor_data["strengths"])
        if vendor_data.get("weaknesses"):
            vendor.weaknesses = json.dumps(vendor_data["weaknesses"])

        # Create ArchiMate elements
        created_elements = []
        for elem_data in archimate_elements_data:
            element = ArchiMateElement(
                name=elem_data["name"],
                type=elem_data["type"],
                layer=elem_data.get("layer", "business"),
                description=elem_data.get("description", ""),
                properties=json.dumps(elem_data.get("properties", {})),
            )
            db.session.add(element)
            created_elements.append(element)

        db.session.commit()

        return vendor, created_elements

    def _analyze_spreadsheet(
        self, file_path: str, provider: str, analysis_type: str
    ) -> Tuple[Dict[str, Any], Optional[LLMInteraction]]:
        """
        Analyze spreadsheet file (CSV, Excel) and extract structured data.

        Args:
            file_path: Path to the spreadsheet file
            provider: LLM provider for analysis
            analysis_type: 'application' or 'vendor'

        Returns:
            Tuple of (extracted_data, llm_interaction)
        """
        logger.info(f"Analyzing spreadsheet: {file_path}")

        # Extract structured text from spreadsheet
        spreadsheet_text = extract_text_from_file(file_path, "spreadsheet")

        # Also get structured records for potential database import
        from app.services.archimate.document_text_extractor import parse_spreadsheet_to_records

        structured_data = parse_spreadsheet_to_records(file_path)

        # Create LLM interaction for analysis
        try:
            # Use the provider the user selected, not the hardcoded one
            from app.models.models import APISettings

            settings = APISettings.query.filter_by(provider=provider, enabled=True).first()
            if settings and settings.api_key and len(settings.api_key.strip()) > 0:
                provider_name = provider
                model_name = settings.default_model or "unknown"
            else:
                # Fallback to configured provider if user's choice is not available
                provider_name, model_name = LLMService._get_configured_provider()
        except (ValueError, Exception):
            # Final fallback
            provider_name, model_name = provider, "unknown"

        interaction = LLMInteraction(
            provider=provider_name,
            model_name=model_name,
            prompt=f"Spreadsheet analysis for {analysis_type}: {file_path}"[:1000],
        )

        # Prepare analysis prompt based on analysis type
        if analysis_type == "application":
            prompt = self._build_application_spreadsheet_prompt(spreadsheet_text, structured_data)
        else:
            prompt = self._build_vendor_spreadsheet_prompt(spreadsheet_text, structured_data)

        # ENHANCEMENT: Implement chunking for very large spreadsheets (>200 records)
        record_count = structured_data.get("record_count", 0)

        # For very large spreadsheets, use chunking instead of single large call
        if record_count > 200:
            logger.info(
                f"Very large spreadsheet detected ({record_count} records), using chunking strategy"
            )
            return self._analyze_spreadsheet_chunked(
                file_path, provider_name, analysis_type, structured_data, model_name
            )

        # Get LLM analysis with dynamic token limit
        # ENHANCEMENT: Calculate dynamic token limit based on record count and provider limits
        if record_count > 100:
            # Large spreadsheets: request maximum available tokens (will be capped by provider)
            requested_max = 32000  # Request high limit, will be capped by provider
        elif record_count > 50:
            # Medium spreadsheets: moderate increase
            requested_max = 16000
        else:
            # Small spreadsheets: default limit
            requested_max = 8000

        # Cap to provider-specific limit
        max_tokens = LLMService.get_max_tokens_limit(provider_name, model_name, requested_max)
        logger.info(
            f"Spreadsheet detected ({record_count} records), using max_tokens={max_tokens} (requested {requested_max}, provider limit applied)"
        )

        try:
            response, llm_interaction = self.llm_service._call_llm(
                prompt=prompt,
                model=model_name,
                provider=provider_name,
                pipeline_stage_id=None,
                max_tokens=max_tokens,
            )

            interaction.response = response

            # Parse LLM response
            extracted_data = self._parse_llm_response(response)

            # ENHANCEMENT: Check if response was truncated and warn user
            if extracted_data.get("metadata", {}).get("partial_extraction"):
                logger.warning(
                    f"LLM response was truncated. Extracted {len(extracted_data.get('elements', []))} elements "
                    f"from partial response. Consider using chunking for very large spreadsheets."
                )
                # Add warning to metadata
                extracted_data["metadata"]["truncation_warning"] = True
                extracted_data["metadata"]["truncation_message"] = (
                    f"Response was truncated at {max_tokens} tokens. "
                    f"Only {len(extracted_data.get('elements', []))} elements extracted. "
                    f"Consider using chunking for spreadsheets with >200 records."
                )

            # Add structured data metadata
            extracted_data["structured_data"] = structured_data
            extracted_data["metadata"].update(
                {
                    "source_type": "spreadsheet",
                    "confidence": "high"
                    if not extracted_data.get("metadata", {}).get("partial_extraction")
                    else "medium",
                    "record_count": structured_data.get("record_count", 0),
                    "sheet_count": structured_data.get("sheet_count", 1),
                    "column_count": structured_data.get("column_count", 0),
                    "max_tokens_used": max_tokens,
                }
            )

            return extracted_data, interaction

        except Exception as e:
            logger.error(f"Error analyzing spreadsheet with LLM: {e}")
            interaction.response = f"Error: {str(e)}"

            # ENHANCED FALLBACK: Extract elements and relationships directly from structured data
            fallback_elements = self._extract_elements_from_structured_data(
                structured_data, analysis_type
            )

            # Extract relationships if relationship columns are present
            fallback_relationships = self._extract_relationships_from_structured_data(
                structured_data, fallback_elements, analysis_type
            )

            fallback_data = {
                "elements": fallback_elements,
                "relationships": fallback_relationships,
                "structured_data": structured_data,
                "metadata": {
                    "source_type": "spreadsheet",
                    "confidence": "medium" if fallback_elements else "low",
                    "error": str(e),
                    "fallback_extraction": True,
                    "extracted_count": len(fallback_elements),
                    "relationships_extracted": len(fallback_relationships),
                },
            }

            return fallback_data, interaction

    def _analyze_spreadsheet_chunked(
        self,
        file_path: str,
        provider: str,
        analysis_type: str,
        structured_data: Dict[str, Any],
        model: str,
    ) -> Tuple[Dict[str, Any], Optional[LLMInteraction]]:
        """
        Analyze very large spreadsheets by chunking records into batches.

        This prevents token limit issues and improves reliability for large datasets.
        """
        logger.info("Analyzing large spreadsheet using chunking strategy")

        records = structured_data.get("records", [])
        columns = structured_data.get("columns", []) or structured_data.get("headers", [])

        if not records:
            return {
                "elements": [],
                "relationships": [],
                "metadata": {
                    "source_type": "spreadsheet",
                    "confidence": "low",
                    "error": "No records found in spreadsheet",
                    "chunking_used": True,
                },
            }, None

        # Chunk records into batches of 50 - 75 records each
        # This keeps each LLM call manageable while processing all data
        CHUNK_SIZE = 75
        chunks = [records[i : i + CHUNK_SIZE] for i in range(0, len(records), CHUNK_SIZE)]

        logger.info(
            f"Split {len(records)} records into {len(chunks)} chunks of ~{CHUNK_SIZE} records each"
        )

        all_elements = []
        all_relationships = []
        interactions = []

        # Process each chunk
        for chunk_idx, chunk_records in enumerate(chunks):
            logger.info(
                f"Processing chunk {chunk_idx + 1}/{len(chunks)} ({len(chunk_records)} records)"
            )

            # Create chunk-specific structured data
            chunk_data = {
                "records": chunk_records,
                "columns": columns,
                "record_count": len(chunk_records),
                "sheet_count": 1,
                "column_count": len(columns),
                "chunk_index": chunk_idx,
                "total_chunks": len(chunks),
            }

            # Extract text for this chunk
            from app.services.archimate.document_text_extractor import extract_text_from_file

            chunk_text = extract_text_from_file(file_path, "spreadsheet")

            # Build prompt for this chunk
            if analysis_type == "application":
                chunk_prompt = self._build_application_spreadsheet_prompt(chunk_text, chunk_data)
            else:
                chunk_prompt = self._build_vendor_spreadsheet_prompt(chunk_text, chunk_data)

            # Add chunk context to prompt
            chunk_prompt = f"""
This is chunk {chunk_idx + 1} of {len(chunks)} from a large spreadsheet analysis.
Processing {len(chunk_records)} records from this chunk.

{chunk_prompt}

IMPORTANT: Extract elements from THIS CHUNK ONLY. Do not try to process the entire spreadsheet.
"""

            try:
                # Call LLM with moderate token limit (each chunk is smaller)
                # Cap to provider-specific limit
                max_tokens = LLMService.get_max_tokens_limit(provider, model, 16000)
                response, llm_interaction = self.llm_service._call_llm(
                    prompt=chunk_prompt,
                    model=model,
                    provider=provider,
                    pipeline_stage_id=None,
                    max_tokens=max_tokens,
                )

                if llm_interaction:
                    interactions.append(llm_interaction)

                # Parse response
                chunk_data_result = self._parse_llm_response(response)

                # Collect elements and relationships
                chunk_elements = chunk_data_result.get("elements", [])
                chunk_relationships = chunk_data_result.get("relationships", [])

                # Add chunk metadata to elements
                for elem in chunk_elements:
                    elem["properties"] = elem.get("properties", {})
                    elem["properties"]["chunk_index"] = chunk_idx
                    elem["properties"]["chunk_total"] = len(chunks)

                all_elements.extend(chunk_elements)
                all_relationships.extend(chunk_relationships)

                logger.info(
                    f"Chunk {chunk_idx + 1} extracted {len(chunk_elements)} elements, {len(chunk_relationships)} relationships"
                )

            except Exception as e:
                logger.error(f"Error processing chunk {chunk_idx + 1}: {e}")
                # Continue with other chunks even if one fails
                continue

        # Deduplicate elements by name (case-insensitive)
        seen_names = set()
        unique_elements = []
        for elem in all_elements:
            name_lower = elem.get("name", "").lower()
            if name_lower and name_lower not in seen_names:
                seen_names.add(name_lower)
                unique_elements.append(elem)
            else:
                # Mark as duplicate
                elem["properties"] = elem.get("properties", {})
                elem["properties"]["is_duplicate"] = True

        logger.info(
            f"Chunking complete: {len(unique_elements)} unique elements from {len(all_elements)} total (deduplicated)"
        )

        # Create interaction record for the overall analysis
        interaction = LLMInteraction(
            provider=provider,
            model_name=model,
            prompt=f"Chunked spreadsheet analysis: {len(chunks)} chunks",
            response=f"Extracted {len(unique_elements)} elements from {len(records)} records using chunking",
        )

        return {
            "elements": unique_elements,
            "relationships": all_relationships,
            "structured_data": structured_data,
            "metadata": {
                "source_type": "spreadsheet",
                "confidence": "high" if len(unique_elements) > 0 else "medium",
                "record_count": structured_data.get("record_count", 0),
                "sheet_count": structured_data.get("sheet_count", 1),
                "column_count": structured_data.get("column_count", 0),
                "chunking_used": True,
                "chunks_processed": len(chunks),
                "elements_before_dedup": len(all_elements),
                "elements_after_dedup": len(unique_elements),
            },
        }, interaction

    def _build_application_spreadsheet_prompt(
        self, spreadsheet_text: str, structured_data: Dict[str, Any]
    ) -> str:
        """Build prompt for application spreadsheet analysis."""
        return f"""
Analyze this spreadsheet data for application architecture and extract ArchiMate 3.2 elements.

SPREADSHEET CONTENT:
{spreadsheet_text}

STRUCTURED DATA SUMMARY:
- Format: {structured_data.get('format', 'unknown')}
- Records: {structured_data.get('record_count', 0)}
- Sheets: {structured_data.get('sheet_count', 1)}
- Columns: {structured_data.get('column_count', 0)}

TASK:
Extract Application Layer ArchiMate elements from this spreadsheet data:

1. ApplicationComponent - Applications, systems, software components
2. ApplicationInterface - APIs, interfaces, integration points
3. ApplicationFunction - Business functions, capabilities
4. ApplicationProcess - Workflows, business processes
5. ApplicationService - Services provided by applications
6. DataObject - Data entities, databases, files

RESPONSE FORMAT (JSON):
{{
    "elements": [
        {{
            "name": "Element Name",
            "type": "ApplicationComponent",
            "layer": "application",
            "description": "Description based on spreadsheet data",
            "properties": {{"source": "spreadsheet", "row_data": "..."}}
        }}
    ],
    "relationships": [
        {{
            "source": "Source Element",
            "target": "Target Element",
            "type": "Association",
            "description": "Relationship description"
        }}
    ],
    "analysis_summary": "Brief summary of what was found in the spreadsheet"
}}

Focus on extracting actual applications, interfaces, and data structures mentioned in the spreadsheet data.
"""

    def _build_vendor_spreadsheet_prompt(
        self, spreadsheet_text: str, structured_data: Dict[str, Any]
    ) -> str:
        """Build prompt for vendor spreadsheet analysis."""
        return f"""
Analyze this spreadsheet data for vendor information and extract relevant ArchiMate 3.2 elements.

SPREADSHEET CONTENT:
{spreadsheet_text}

STRUCTURED DATA SUMMARY:
- Format: {structured_data.get('format', 'unknown')}
- Records: {structured_data.get('record_count', 0)}
- Sheets: {structured_data.get('sheet_count', 1)}
- Columns: {structured_data.get('column_count', 0)}

TASK:
Extract vendor-related information and Business/Application Layer ArchiMate elements:

1. BusinessActor - Vendor organizations, companies
2. BusinessService - Services offered by vendors
3. Product - Products and solutions
4. ApplicationComponent - Vendor software/products
5. ApplicationService - Vendor-provided services
6. Contract - Vendor contracts, agreements

RESPONSE FORMAT (JSON):
{{
    "elements": [
        {{
            "name": "Element Name",
            "type": "BusinessActor",
            "layer": "business",
            "description": "Description based on spreadsheet data",
            "properties": {{"source": "spreadsheet", "vendor_type": "..."}}
        }}
    ],
    "relationships": [
        {{
            "source": "Source Element",
            "target": "Target Element",
            "type": "Association",
            "description": "Relationship description"
        }}
    ],
    "vendor_info": {{
        "name": "Vendor Name",
        "products": ["Product1", "Product2"],
        "services": ["Service1", "Service2"],
        "market_position": "Market position info"
    }},
    "analysis_summary": "Brief summary of vendor information found"
}}

Focus on extracting vendor companies, their products, services, and business relationships from the spreadsheet data.
"""

    def _extract_elements_from_structured_data(
        self, structured_data: Dict[str, Any], analysis_type: str
    ) -> List[Dict]:
        """
        Enhanced extraction of ArchiMate elements directly from CSV/Excel structured data.

        Features:
        - Complete ArchiMate 3.2 type mapping (50+ types)
        - Fuzzy column matching
        - Relationship extraction
        - Data validation
        - Duplicate detection
        - Multi-sheet support
        - Hierarchical structures
        - Property extraction
        - Integration with existing elements
        """
        elements = []

        try:
            # Handle multi-sheet Excel files
            if "sheets" in structured_data:
                # Process each sheet
                for sheet_name, sheet_data in structured_data["sheets"].items():
                    sheet_elements = self._extract_from_sheet(sheet_data, analysis_type, sheet_name)
                    elements.extend(sheet_elements)
            else:
                # Single sheet or CSV
                records = structured_data.get("records", [])
                columns = structured_data.get("columns", []) or structured_data.get("headers", [])

                if records:
                    elements = self._extract_from_records(
                        records, columns, analysis_type, sheet_name=None
                    )

            # Check against existing elements in database
            elements = self._check_existing_elements(elements)

            logger.info(f"Extracted {len(elements)} elements from structured data fallback")

        except Exception as e:
            logger.warning(f"Error extracting elements from structured data: {e}")

        return elements

    def _extract_from_sheet(
        self, sheet_data: Dict[str, Any], analysis_type: str, sheet_name: str
    ) -> List[Dict]:
        """Extract elements from a single sheet."""
        records = sheet_data.get("records", [])
        columns = sheet_data.get("headers", [])

        # Detect sheet purpose from name
        sheet_purpose = self._detect_sheet_purpose(sheet_name, columns)

        return self._extract_from_records(
            records, columns, analysis_type, sheet_name, sheet_purpose
        )

    def _detect_sheet_purpose(self, sheet_name: str, columns: List[str]) -> str:
        """Detect what type of data is in this sheet."""
        name_lower = sheet_name.lower()
        cols_lower = " ".join(str(c).lower() for c in columns)

        if any(word in name_lower for word in ["relationship", "rel", "link", "connection"]):
            return "relationships"
        elif any(word in name_lower for word in ["element", "component", "application", "service"]):
            return "elements"
        elif any(word in name_lower for word in ["property", "attribute", "metadata"]):
            return "properties"
        elif "source" in cols_lower and "target" in cols_lower:
            return "relationships"
        else:
            return "elements"  # Default

    def _extract_from_records(
        self,
        records: List[Dict],
        columns: List[str],
        analysis_type: str,
        sheet_name: Optional[str] = None,
        sheet_purpose: str = "elements",
    ) -> List[Dict]:
        """Extract elements from records with enhanced features."""
        if not records:
            return []

        # Detect header row if not already detected
        columns = self._detect_header_row(records, columns)

        # Enhanced column detection with fuzzy matching
        column_mapping = self._detect_columns_fuzzy(columns, analysis_type)

        # Detect hierarchical structure
        hierarchy_info = self._detect_hierarchy(columns, records)

        # Extract elements from records
        seen_names = set()  # For duplicate detection within this extraction
        elements = []

        for idx, record in enumerate(records):
            try:
                # Extract element data
                element_data = self._extract_element_from_record(
                    record, column_mapping, analysis_type, idx, hierarchy_info
                )

                if not element_data or not element_data.get("name"):
                    continue

                # Extract custom properties
                element_data = self._extract_custom_properties(
                    element_data, record, column_mapping, columns
                )

                # Validate element
                validation_result = self._validate_extracted_element(element_data)
                if not validation_result["valid"]:
                    logger.warning(
                        f"Element {idx + 1} validation failed: {validation_result.get('error')}"
                    )
                    element_data["properties"]["validation_warning"] = validation_result.get(
                        "error"
                    )

                # Check for duplicates within this extraction
                name_lower = element_data["name"].lower()
                if name_lower in seen_names:
                    logger.warning(f"Duplicate element name detected: {element_data['name']}")
                    element_data["properties"]["is_duplicate"] = True
                    element_data["properties"]["duplicate_handling"] = "detected"
                else:
                    seen_names.add(name_lower)

                # Add sheet metadata
                if sheet_name:
                    element_data["properties"]["source_sheet"] = sheet_name
                    element_data["properties"]["sheet_purpose"] = sheet_purpose

                elements.append(element_data)

            except Exception as e:
                logger.warning(f"Error processing record {idx + 1}: {e}")
                continue

        return elements

    def _detect_columns_fuzzy(
        self, columns: List[str], analysis_type: str
    ) -> Dict[str, Optional[str]]:
        """
        Enhanced column detection with fuzzy matching.

        Returns mapping of: name, type, description, layer, source, target, relationship_type
        """
        from difflib import SequenceMatcher

        # Expanded column name patterns with weights
        name_patterns = [
            ("name", 1.0),
            ("element_name", 0.95),
            ("application_name", 0.9),
            ("component_name", 0.9),
            ("service_name", 0.9),
            ("interface_name", 0.9),
            ("capability_name", 0.9),
            ("process_name", 0.9),
            ("vendor_name", 0.9),
            ("product_name", 0.9),
            ("app_name", 0.85),
            ("title", 0.8),
            ("element", 0.75),
            ("item", 0.7),
            ("id", 0.6),
        ]

        type_patterns = [
            ("type", 1.0),
            ("element_type", 0.95),
            ("archimate_type", 0.95),
            ("archimate", 0.9),
            ("category", 0.8),
            ("class", 0.75),
            ("classification", 0.7),
        ]

        desc_patterns = [
            ("description", 1.0),
            ("desc", 0.95),
            ("details", 0.9),
            ("notes", 0.85),
            ("summary", 0.85),
            ("info", 0.8),
            ("comment", 0.75),
            ("remarks", 0.7),
        ]

        layer_patterns = [("layer", 1.0), ("archimate_layer", 0.95), ("level", 0.7)]

        # Relationship columns
        source_patterns = [
            ("source", 1.0),
            ("from", 0.9),
            ("source_element", 0.95),
            ("source_name", 0.9),
            ("from_element", 0.85),
        ]

        target_patterns = [
            ("target", 1.0),
            ("to", 0.9),
            ("target_element", 0.95),
            ("target_name", 0.9),
            ("to_element", 0.85),
        ]

        rel_type_patterns = [
            ("relationship_type", 1.0),
            ("rel_type", 0.95),
            ("relationship", 0.9),
            ("rel", 0.85),
            ("link_type", 0.8),
            ("connection", 0.75),
        ]

        def find_best_match(patterns, threshold=0.6):
            best_match = None
            best_score = 0.0

            for col in columns:
                col_lower = str(col).lower().strip()
                for pattern, weight in patterns:
                    similarity = SequenceMatcher(None, pattern, col_lower).ratio()
                    score = similarity * weight
                    if score > best_score and score >= threshold:
                        best_score = score
                        best_match = col

            return best_match

        return {
            "name": find_best_match(name_patterns, 0.6),
            "type": find_best_match(type_patterns, 0.6),
            "description": find_best_match(desc_patterns, 0.6),
            "layer": find_best_match(layer_patterns, 0.6),
            "source": find_best_match(source_patterns, 0.6),
            "target": find_best_match(target_patterns, 0.6),
            "relationship_type": find_best_match(rel_type_patterns, 0.6),
        }

    def _extract_element_from_record(
        self,
        record: Dict,
        column_mapping: Dict[str, Optional[str]],
        analysis_type: str,
        record_idx: int,
        hierarchy_info: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Extract a single element from a record."""
        name = None
        elem_type = None
        description = ""
        layer = None

        # Get name
        if column_mapping["name"] and column_mapping["name"] in record:
            name = str(record[column_mapping["name"]]).strip()
        elif len(record) > 0:
            # Use first non-empty column as name
            for key, value in record.items():
                if (
                    value
                    and str(value).strip()
                    and str(value).strip().lower() not in ["n/a", "none", "null", ""]
                ):
                    name = str(value).strip()
                    break

        if not name or name.lower() in ["", "n/a", "none", "null"]:
            return None

        # Get type
        if column_mapping["type"] and column_mapping["type"] in record:
            type_val = str(record[column_mapping["type"]]).strip()
            elem_type = self._map_type_to_archimate(type_val, analysis_type)

        # Get layer
        if column_mapping["layer"] and column_mapping["layer"] in record:
            layer = str(record[column_mapping["layer"]]).strip().lower()

        # Infer type and layer if not provided
        if not elem_type:
            elem_type = self._infer_type_from_analysis_type(analysis_type)

        if not layer:
            layer = self._infer_layer_from_type(elem_type)

        # Get description
        if column_mapping["description"] and column_mapping["description"] in record:
            description = str(record[column_mapping["description"]]).strip()
        elif len(record) > 1:
            # Use second non-empty column as description
            values = [v for v in record.values() if v and str(v).strip()]
            if len(values) > 1:
                description = str(values[1]).strip()

        return {
            "name": name,
            "type": elem_type,
            "layer": layer,
            "description": description,
            "properties": {
                "source": "structured_data_fallback",
                "extracted_from": "csv_column_analysis",
                "record_index": record_idx,
            },
        }

    def _map_type_to_archimate(self, type_value: str, analysis_type: str) -> str:
        """
        Map type value to ArchiMate element type using comprehensive mapping.

        Supports all 50+ ArchiMate 3.2 element types with fuzzy matching.
        """
        if not type_value:
            return self._infer_type_from_analysis_type(analysis_type)

        type_lower = type_value.lower().strip()

        # Complete ArchiMate 3.2 type mapping with variations
        type_mapping = {
            # Motivation Layer
            "stakeholder": "Stakeholder",
            "driver": "Driver",
            "assessment": "Assessment",
            "goal": "Goal",
            "outcome": "Outcome",
            "principle": "Principle",
            "requirement": "Requirement",
            "constraint": "Constraint",
            "meaning": "Meaning",
            "value": "Value",
            # Strategy Layer
            "resource": "Resource",
            "capability": "Capability",
            "businesscapability": "BusinessCapability",
            "valuestream": "ValueStream",
            "value_stream": "ValueStream",
            "courseofaction": "CourseOfAction",
            "course_of_action": "CourseOfAction",
            # Business Layer
            "businessactor": "BusinessActor",
            "business_actor": "BusinessActor",
            "actor": "BusinessActor",
            "businessrole": "BusinessRole",
            "business_role": "BusinessRole",
            "role": "BusinessRole",
            "businesscollaboration": "BusinessCollaboration",
            "business_collaboration": "BusinessCollaboration",
            "collaboration": "BusinessCollaboration",
            "businessinterface": "BusinessInterface",
            "business_interface": "BusinessInterface",
            "businessprocess": "BusinessProcess",
            "business_process": "BusinessProcess",
            "process": "BusinessProcess",
            "businessfunction": "BusinessFunction",
            "business_function": "BusinessFunction",
            "function": "BusinessFunction",
            "businessinteraction": "BusinessInteraction",
            "business_interaction": "BusinessInteraction",
            "interaction": "BusinessInteraction",
            "businessevent": "BusinessEvent",
            "business_event": "BusinessEvent",
            "event": "BusinessEvent",
            "businessservice": "BusinessService",
            "business_service": "BusinessService",
            "service": "BusinessService",
            "businessobject": "BusinessObject",
            "business_object": "BusinessObject",
            "object": "BusinessObject",
            "contract": "Contract",
            "representation": "Representation",
            "product": "Product",
            # Application Layer
            "applicationcomponent": "ApplicationComponent",
            "application_component": "ApplicationComponent",
            "application": "ApplicationComponent",
            "app": "ApplicationComponent",
            "component": "ApplicationComponent",
            "applicationcollaboration": "ApplicationCollaboration",
            "application_collaboration": "ApplicationCollaboration",
            "applicationinterface": "ApplicationInterface",
            "application_interface": "ApplicationInterface",
            "interface": "ApplicationInterface",
            "api": "ApplicationInterface",
            "applicationfunction": "ApplicationFunction",
            "application_function": "ApplicationFunction",
            "applicationprocess": "ApplicationProcess",
            "application_process": "ApplicationProcess",
            "applicationinteraction": "ApplicationInteraction",
            "application_interaction": "ApplicationInteraction",
            "applicationevent": "ApplicationEvent",
            "application_event": "ApplicationEvent",
            "applicationservice": "ApplicationService",
            "application_service": "ApplicationService",
            "appservice": "ApplicationService",
            "dataobject": "DataObject",
            "data_object": "DataObject",
            "data": "DataObject",
            # Technology Layer
            "node": "Node",
            "device": "Device",
            "systemsoftware": "SystemSoftware",
            "system_software": "SystemSoftware",
            "software": "SystemSoftware",
            "technologycollaboration": "TechnologyCollaboration",
            "technology_collaboration": "TechnologyCollaboration",
            "technologyinterface": "TechnologyInterface",
            "technology_interface": "TechnologyInterface",
            "path": "Path",
            "communicationnetwork": "CommunicationNetwork",
            "communication_network": "CommunicationNetwork",
            "network": "CommunicationNetwork",
            "technologyfunction": "TechnologyFunction",
            "technology_function": "TechnologyFunction",
            "technologyprocess": "TechnologyProcess",
            "technology_process": "TechnologyProcess",
            "technologyinteraction": "TechnologyInteraction",
            "technology_interaction": "TechnologyInteraction",
            "technologyevent": "TechnologyEvent",
            "technology_event": "TechnologyEvent",
            "technologyservice": "TechnologyService",
            "technology_service": "TechnologyService",
            "artifact": "Artifact",
            # Physical Layer
            "equipment": "Equipment",
            "facility": "Facility",
            "distributionnetwork": "DistributionNetwork",
            "distribution_network": "DistributionNetwork",
            "material": "Material",
            # Implementation & Migration Layer
            "workpackage": "WorkPackage",
            "work_package": "WorkPackage",
            "package": "WorkPackage",
            "deliverable": "Deliverable",
            "implementationevent": "ImplementationEvent",
            "implementation_event": "ImplementationEvent",
            "plateau": "Plateau",
            "gap": "Gap",
            # Vendor/Organization
            "vendor": "VendorOrganization",
            "vendororganization": "VendorOrganization",
            "vendor_organization": "VendorOrganization",
            "organization": "VendorOrganization",
            "company": "VendorOrganization",
            "vendorproduct": "Product",
            "vendor_product": "Product",
        }

        # Direct match
        if type_lower in type_mapping:
            return type_mapping[type_lower]

        # Fuzzy match - check if any key is contained in the type value
        for key, archimate_type in type_mapping.items():
            if key in type_lower or type_lower in key:
                return archimate_type

        # If no match, try to infer from analysis_type
        return self._infer_type_from_analysis_type(analysis_type)

    def _infer_type_from_analysis_type(self, analysis_type: str) -> str:
        """Infer default element type from analysis type."""
        if analysis_type == "application":
            return "ApplicationComponent"
        elif analysis_type == "vendor":
            return "VendorOrganization"
        else:
            return "ApplicationComponent"

    def _infer_layer_from_type(self, element_type: str) -> str:
        """Infer ArchiMate layer from element type."""
        if not element_type:
            return "application"

        type_lower = element_type.lower()

        # Complete layer mapping for all ArchiMate types
        layer_mapping = {
            # Motivation
            "stakeholder": "motivation",
            "driver": "motivation",
            "assessment": "motivation",
            "goal": "motivation",
            "outcome": "motivation",
            "principle": "motivation",
            "requirement": "motivation",
            "constraint": "motivation",
            "meaning": "motivation",
            "value": "motivation",
            # Strategy
            "resource": "strategy",
            "capability": "strategy",
            "businesscapability": "strategy",
            "valuestream": "strategy",
            "courseofaction": "strategy",
            # Business
            "businessactor": "business",
            "businessrole": "business",
            "businesscollaboration": "business",
            "businessinterface": "business",
            "businessprocess": "business",
            "businessfunction": "business",
            "businessinteraction": "business",
            "businessevent": "business",
            "businessservice": "business",
            "businessobject": "business",
            "contract": "business",
            "representation": "business",
            "product": "business",
            "vendororganization": "business",
            # Application
            "applicationcomponent": "application",
            "applicationcollaboration": "application",
            "applicationinterface": "application",
            "applicationfunction": "application",
            "applicationprocess": "application",
            "applicationinteraction": "application",
            "applicationevent": "application",
            "applicationservice": "application",
            "dataobject": "application",
            # Technology
            "node": "technology",
            "device": "technology",
            "systemsoftware": "technology",
            "technologycollaboration": "technology",
            "technologyinterface": "technology",
            "path": "technology",
            "communicationnetwork": "technology",
            "technologyfunction": "technology",
            "technologyprocess": "technology",
            "technologyinteraction": "technology",
            "technologyevent": "technology",
            "technologyservice": "technology",
            "artifact": "technology",
            # Physical
            "equipment": "physical",
            "facility": "physical",
            "distributionnetwork": "physical",
            "material": "physical",
            # Implementation
            "workpackage": "implementation",
            "deliverable": "implementation",
            "implementationevent": "implementation",
            "plateau": "implementation",
            "gap": "implementation",
        }

        for key, layer in layer_mapping.items():
            if key in type_lower:
                return layer

        return "application"  # Default

    def _validate_extracted_element(self, element: Dict) -> Dict[str, Any]:
        """
        Validate extracted element against ArchiMate 3.2 metamodel.

        Returns: {'valid': bool, 'error': str, 'warnings': List[str]}
        """
        errors = []
        warnings = []

        # Check required fields
        if not element.get("name"):
            errors.append("Element name is required")

        if not element.get("type"):
            errors.append("Element type is required")

        if not element.get("layer"):
            warnings.append("Layer not specified, inferred from type")

        # Validate element type against ArchiMate 3.2
        valid_types_by_layer = {
            "motivation": [
                "Stakeholder",
                "Driver",
                "Assessment",
                "Goal",
                "Outcome",
                "Principle",
                "Requirement",
                "Constraint",
                "Meaning",
                "Value",
            ],
            "strategy": ["Resource", "Capability", "ValueStream", "CourseOfAction"],
            "business": [
                "BusinessActor",
                "BusinessRole",
                "BusinessCollaboration",
                "BusinessInterface",
                "BusinessProcess",
                "BusinessFunction",
                "BusinessInteraction",
                "BusinessEvent",
                "BusinessService",
                "BusinessObject",
                "Contract",
                "Representation",
                "Product",
            ],
            "application": [
                "ApplicationComponent",
                "ApplicationCollaboration",
                "ApplicationInterface",
                "ApplicationFunction",
                "ApplicationProcess",
                "ApplicationInteraction",
                "ApplicationEvent",
                "ApplicationService",
                "DataObject",
            ],
            "technology": [
                "Node",
                "Device",
                "SystemSoftware",
                "TechnologyCollaboration",
                "TechnologyInterface",
                "Path",
                "CommunicationNetwork",
                "TechnologyFunction",
                "TechnologyProcess",
                "TechnologyInteraction",
                "TechnologyEvent",
                "TechnologyService",
                "Artifact",
            ],
            "physical": ["Equipment", "Facility", "DistributionNetwork", "Material"],
            "implementation": [
                "WorkPackage",
                "Deliverable",
                "ImplementationEvent",
                "Plateau",
                "Gap",
            ],
        }

        elem_type = element.get("type")
        layer = element.get("layer", "").lower()

        if elem_type and layer:
            valid_types = valid_types_by_layer.get(layer, [])
            if elem_type not in valid_types:
                warnings.append(
                    f"Element type '{elem_type}' may not be valid for layer '{layer}'. "
                    f"Valid types for {layer}: {', '.join(valid_types[:5])}..."
                )

        # Validate name length
        name = element.get("name", "")
        if len(name) > 200:
            warnings.append(f"Element name is very long ({len(name)} chars), may be truncated")

        # Check for suspicious values
        if name.lower() in ["test", "example", "sample", "dummy", "placeholder"]:
            warnings.append("Element name appears to be a placeholder")

        return {
            "valid": len(errors) == 0,
            "error": "; ".join(errors) if errors else None,
            "warnings": warnings,
        }

    def _detect_header_row(self, records: List[Dict], columns: List[str]) -> List[str]:
        """Detect header row if not already provided."""
        if columns and len(columns) > 0:
            # Check if first record looks like headers (all strings, no numbers)
            first_record = records[0] if records else {}
            if isinstance(first_record, dict):
                first_values = list(first_record.values())
                # If first record values are all strings and look like headers
                if all(isinstance(v, str) and v.strip() for v in first_values[:3]):
                    # Check if they match common header patterns
                    header_keywords = ["name", "type", "description", "id", "element"]
                    first_str = " ".join(str(v).lower() for v in first_values[:5])
                    if any(kw in first_str for kw in header_keywords):
                        # First record is likely headers, use it
                        return [str(v) for v in first_values]

        return columns if columns else []

    def _detect_hierarchy(self, columns: List[str], records: List[Dict]) -> Optional[Dict]:
        """Detect hierarchical structure in data."""
        hierarchy_patterns = [
            "parent",
            "parent_id",
            "parent_name",
            "parent_element",
            "parent_component",
            "parent_app",
            "level",
            "depth",
            "hierarchy",
        ]

        parent_col = None
        level_col = None

        for col in columns:
            col_lower = str(col).lower()
            if not parent_col and any(pattern in col_lower for pattern in hierarchy_patterns[:5]):
                parent_col = col
            if not level_col and any(pattern in col_lower for pattern in hierarchy_patterns[5:]):
                level_col = col

        if parent_col:
            return {"parent_column": parent_col, "level_column": level_col, "has_hierarchy": True}

        return None

    def _extract_custom_properties(
        self,
        element_data: Dict,
        record: Dict,
        column_mapping: Dict[str, Optional[str]],
        all_columns: List[str],
    ) -> Dict:
        """Extract custom properties from unmapped columns."""
        # Get all mapped columns
        mapped_cols = set(v for v in column_mapping.values() if v)

        # Find unmapped columns that might be properties
        custom_props = {}
        for col in all_columns:
            if col not in mapped_cols and col in record:
                value = record[col]
                if (
                    value
                    and str(value).strip()
                    and str(value).strip().lower() not in ["n/a", "none", "null", ""]
                ):
                    # Normalize property name
                    prop_name = str(col).lower().replace(" ", "_").replace("-", "_")
                    custom_props[prop_name] = str(value).strip()

        if custom_props:
            if "properties" not in element_data:
                element_data["properties"] = {}
            element_data["properties"]["custom_properties"] = custom_props

        return element_data

    def _check_existing_elements(self, elements: List[Dict]) -> List[Dict]:
        """Check extracted elements against existing database elements."""
        try:
            from app.models.archimate_core import ArchiMateElement

            # Build name lookup
            element_names = [e["name"].lower() for e in elements if e.get("name")]
            if not element_names:
                return elements

            # Query existing elements (limit to avoid performance issues)
            existing = ArchiMateElement.query.filter(
                db.func.lower(ArchiMateElement.name).in_(
                    element_names[:100]
                )  # Limit to 100 for performance
            ).all()

            existing_names = {e.name.lower(): e for e in existing}

            # Mark elements that already exist and get full ApplicationComponent if available
            for element in elements:
                name_lower = element.get("name", "").lower()
                if name_lower in existing_names:
                    existing_elem = existing_names[name_lower]
                    element["properties"]["exists_in_db"] = True
                    element["properties"]["existing_element_id"] = existing_elem.id
                    element["properties"]["existing_element_type"] = existing_elem.type
                    element["properties"]["duplicate_handling"] = "exists"

                    # Try to get ApplicationComponent details for comparison
                    try:
                        if (
                            hasattr(existing_elem, "application_component")
                            and existing_elem.application_component
                        ):
                            app_comp = existing_elem.application_component
                            element["properties"]["existing_application_id"] = app_comp.id
                            element["properties"]["existing_application_details"] = {
                                "description": app_comp.description,
                                "component_type": app_comp.component_type,
                                "deployment_status": app_comp.deployment_status,
                                "business_domain": app_comp.business_domain,
                                "technology_stack": app_comp.technology_stack,
                                "business_owner": app_comp.business_owner,
                            }
                    except Exception as e:
                        logger.debug(f"Could not get ApplicationComponent details: {e}")

                    logger.info(
                        f"Element '{element['name']}' already exists in database (ID: {existing_elem.id})"
                    )

        except Exception as e:
            logger.warning(f"Error checking existing elements: {e}")

        return elements

    def _extract_relationships_from_structured_data(
        self, structured_data: Dict[str, Any], elements: List[Dict], analysis_type: str
    ) -> List[Dict]:
        """Extract relationships from structured data with enhanced features."""
        relationships = []

        try:
            # Build element name map for lookup
            element_map = {elem["name"].lower(): elem for elem in elements}

            # Handle multi-sheet Excel files
            if "sheets" in structured_data:
                for sheet_name, sheet_data in structured_data["sheets"].items():
                    sheet_rels = self._extract_relationships_from_sheet(
                        sheet_data, element_map, sheet_name
                    )
                    relationships.extend(sheet_rels)
            else:
                # Single sheet or CSV
                records = structured_data.get("records", [])
                columns = structured_data.get("columns", []) or structured_data.get("headers", [])

                if records:
                    # Try explicit relationship columns first
                    rels_from_columns = self._extract_relationships_from_columns(
                        records, columns, element_map
                    )
                    relationships.extend(rels_from_columns)

                    # Try relationship matrix pattern
                    rels_from_matrix = self._extract_relationships_from_matrix(
                        records, columns, element_map
                    )
                    relationships.extend(rels_from_matrix)

            # Extract hierarchical relationships
            hierarchical_rels = self._extract_hierarchical_relationships(elements)
            relationships.extend(hierarchical_rels)

            # Validate relationships
            relationships = self._validate_relationships(relationships, element_map)

            logger.info(
                f"Extracted {len(relationships)} relationships from structured data fallback"
            )

        except Exception as e:
            logger.warning(f"Error extracting relationships from structured data: {e}")

        return relationships

    def _extract_relationships_from_sheet(
        self, sheet_data: Dict[str, Any], element_map: Dict[str, Dict], sheet_name: str
    ) -> List[Dict]:
        """Extract relationships from a single sheet."""
        records = sheet_data.get("records", [])
        columns = sheet_data.get("headers", [])

        relationships = []

        # Check if this sheet contains relationships
        sheet_purpose = self._detect_sheet_purpose(sheet_name, columns)
        if sheet_purpose == "relationships":
            # Try explicit columns
            rels = self._extract_relationships_from_columns(records, columns, element_map)
            relationships.extend(rels)

            # Try matrix pattern
            rels = self._extract_relationships_from_matrix(records, columns, element_map)
            relationships.extend(rels)

        return relationships

    def _extract_relationships_from_columns(
        self, records: List[Dict], columns: List[str], element_map: Dict[str, Dict]
    ) -> List[Dict]:
        """Extract relationships from explicit source/target columns."""
        relationships = []

        # Detect relationship columns
        column_mapping = self._detect_columns_fuzzy(columns, "general")
        source_col = column_mapping.get("source")
        target_col = column_mapping.get("target")
        rel_type_col = column_mapping.get("relationship_type")

        if not source_col or not target_col:
            return relationships

        for record in records:
            source_name = None
            target_name = None
            rel_type = "Association"  # Default

            if source_col in record:
                source_name = str(record[source_col]).strip()
            if target_col in record:
                target_name = str(record[target_col]).strip()
            if rel_type_col and rel_type_col in record:
                rel_type = str(record[rel_type_col]).strip()

            # Validate both elements exist
            if source_name and target_name:
                source_lower = source_name.lower()
                target_lower = target_name.lower()

                if source_lower in element_map and target_lower in element_map:
                    valid_rel_type = self._validate_relationship_type(rel_type)

                    relationships.append(
                        {
                            "source": source_name,
                            "target": target_name,
                            "type": valid_rel_type,
                            "description": f"Relationship from {source_name} to {target_name}",
                            "discovery_method": "structured_data_fallback",
                        }
                    )

        return relationships

    def _extract_relationships_from_matrix(
        self, records: List[Dict], columns: List[str], element_map: Dict[str, Dict]
    ) -> List[Dict]:
        """Extract relationships from matrix pattern."""
        relationships = []

        if len(records) < 2 or len(columns) < 2:
            return relationships

        # Detect if this looks like a matrix
        first_col = columns[0] if columns else None
        if not first_col:
            return relationships

        # Check if first column values match element names
        source_col_values = [str(record.get(first_col, "")).strip() for record in records[:10]]
        potential_sources = [v for v in source_col_values if v.lower() in element_map]

        # If at least 50% of first column values are elements, treat as matrix
        if len(potential_sources) / max(len(source_col_values), 1) >= 0.5:
            # Process matrix
            for record in records:
                source_name = str(record.get(first_col, "")).strip()
                if not source_name or source_name.lower() not in element_map:
                    continue

                # Check other columns for relationships
                for col in columns[1:]:  # Skip first column
                    target_name = str(col).strip()  # Column name might be target
                    cell_value = record.get(col)

                    if target_name.lower() in element_map and cell_value:
                        # Cell value indicates relationship exists
                        rel_type = "Association"
                        if isinstance(cell_value, str):
                            rel_type = self._validate_relationship_type(cell_value)
                        elif cell_value in [1, "1", "Y", "y", "Yes", "yes", True]:
                            rel_type = "Association"

                        relationships.append(
                            {
                                "source": source_name,
                                "target": target_name,
                                "type": rel_type,
                                "description": f"Matrix relationship from {source_name} to {target_name}",
                                "discovery_method": "matrix_pattern",
                            }
                        )

        return relationships

    def _extract_hierarchical_relationships(self, elements: List[Dict]) -> List[Dict]:
        """Extract relationships from hierarchical parent-child structure."""
        relationships = []

        for element in elements:
            parent_name = element.get("properties", {}).get("parent_name")
            if parent_name:
                # Find parent element
                for other_elem in elements:
                    if other_elem.get("name", "").lower() == parent_name.lower():
                        relationships.append(
                            {
                                "source": parent_name,
                                "target": element.get("name"),
                                "type": "Composition",  # Parent-child is typically composition
                                "description": f"Hierarchical relationship: {element.get('name')} is part of {parent_name}",
                                "discovery_method": "hierarchical_structure",
                            }
                        )
                        break

        return relationships

    def _validate_relationships(
        self, relationships: List[Dict], element_map: Dict[str, Dict]
    ) -> List[Dict]:
        """Validate relationships - ensure source and target exist."""
        validated = []

        for rel in relationships:
            source = rel.get("source", "").lower()
            target = rel.get("target", "").lower()

            # Both must exist in element map
            if source in element_map and target in element_map:
                validated.append(rel)
            else:
                missing = []
                if source not in element_map:
                    missing.append(f"source '{rel.get('source')}'")
                if target not in element_map:
                    missing.append(f"target '{rel.get('target')}'")
                logger.warning(f"Relationship skipped - missing elements: {', '.join(missing)}")

        return validated

    def _validate_relationship_type(self, rel_type: str) -> str:
        """
        Validate and normalize relationship type.
        """
        if not rel_type:
            return "Association"

        rel_lower = rel_type.lower().strip()

        # ArchiMate 3.2 relationship types
        valid_relationships = {
            "association": "Association",
            "serving": "Serving",
            "realization": "Realization",
            "assignment": "Assignment",
            "access": "Access",
            "flow": "Flow",
            "triggering": "Triggering",
            "composition": "Composition",
            "aggregation": "Aggregation",
            "specialization": "Specialization",
            "influence": "Influence",
            "usedby": "Serving",
            "uses": "Serving",
            "realizes": "Realization",
            "assignedto": "Assignment",
            "accesses": "Access",
            "flows": "Flow",
            "triggers": "Triggering",
            "composedof": "Composition",
            "aggregates": "Aggregation",
            "specializes": "Specialization",
            "influences": "Influence",
        }

        # Direct match
        if rel_lower in valid_relationships:
            return valid_relationships[rel_lower]

        # Fuzzy match
        for key, value in valid_relationships.items():
            if key in rel_lower or rel_lower in key:
                return value

        return "Association"  # Default
