"""
Document upload, management, analysis, and element creation routes.

Routes: upload-document, documents (list), documents/<id> (detail),
        documents/<id> (delete), documents/<id>/re-analyze,
        create-elements, documents/<id>/feedback, documents/<id>/compare.
"""

import json
import logging
import os
from datetime import datetime

from flask import current_app, g, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.decorators import audit_log
from app.utils.duplicate_guard import find_similar_entities
from . import unified_ai_chat_bp
from app.utils.pagination import MAX_PAGE_SIZE, safe_int_arg
from app.middleware.tenant_decorators import is_platform_admin
from app.modules.ai_chat.services.failure_messages import ASSISTANT_UNAVAILABLE
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)

DEFAULT_BASE_INFRASTRUCTURE_COST = 100000  # fabricated-ok: named default config constant, not a per-record measured value

@unified_ai_chat_bp.route("/upload-document", methods=["POST"])
@login_required
@audit_log("upload_document")
def upload_document():
    """
    Upload document and automatically create ArchiMate elements in Architecture CRUD.

    Extracts elements from uploaded documents and creates them in the appropriate tables.
    Supports context-aware analysis for applications and vendors.
    """
    try:
        from werkzeug.utils import secure_filename

        from app.archimate_crud.routes import MODEL_REGISTRY
        from app.services.archimate.document_analysis_service import (
            DocumentAnalysisService,
        )
        from app.services.core.async_utils import get_or_create_event_loop
        from app.services.core.retry_handler import execute_with_db_retry

        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"success": False, "error": "No file selected"}), 400

        # Sanitize filename FIRST to prevent double-extension bypass (e.g. "file.pdf.php")
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({"success": False, "error": "Invalid filename"}), 400

        # Validate file extension on the SANITIZED filename
        ALLOWED_EXTENSIONS = {
            ".pdf",
            ".docx",
            ".doc",
            ".xlsx",
            ".xls",
            ".csv",
            ".txt",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".webp",
            ".pptx",
            ".ppt",
            ".rtf",
            ".md",
        }
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            return jsonify(
                {
                    "success": False,
                    "error": f"File type '{file_ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                }
            ), 400

        # Validate file size (50MB max)
        MAX_UPLOAD_SIZE = 50 * 1024 * 1024
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        if file_size > MAX_UPLOAD_SIZE:
            return jsonify(
                {
                    "success": False,
                    "error": f"File too large ({file_size // (1024 * 1024)}MB). Maximum: {MAX_UPLOAD_SIZE // (1024 * 1024)}MB",
                }
            ), 400

        # Get user ID
        user_id = current_user.id if current_user.is_authenticated else None

        # Get context-aware parameters
        analysis_context = request.form.get(
            "analysis_context", "general"
        )  # 'general', 'application', or 'vendor'
        target_application_id = request.form.get("target_application_id")
        target_vendor_id = request.form.get("target_vendor_id")

        # Get provider from form data (user selection)
        requested_provider = request.form.get("provider", "").strip().lower()

        # Convert IDs to integers if provided
        if target_application_id:
            try:
                target_application_id = int(target_application_id)
            except ValueError:
                target_application_id = None

        if target_vendor_id:
            try:
                target_vendor_id = int(target_vendor_id)
            except ValueError:
                target_vendor_id = None

        # Check if this is preview-only mode (extract but don't create)
        preview_only = request.form.get("preview_only", "false").lower() == "true"

        current_app.logger.info(
            f"Document upload - Context: {analysis_context}, App ID: {target_application_id}, Vendor ID: {target_vendor_id}, Preview: {preview_only}, Requested Provider: {requested_provider}"
        )

        # Save file (filename already sanitized via secure_filename above)
        upload_dir = os.path.join(
            current_app.config.get("UPLOAD_FOLDER", "uploads"), "ai_chat_documents"
        )
        os.makedirs(upload_dir, exist_ok=True)

        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)

        # Determine file type
        file_ext = os.path.splitext(filename)[1].lower()
        file_type = "document"
        if file_ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]:
            file_type = "image"
        elif file_ext in [".xlsx", ".xls", ".csv"]:
            file_type = "spreadsheet"

        # Create upload record
        from app.models.ai_chat_document import AIChatDocumentUpload

        upload_record = AIChatDocumentUpload(
            file_name=filename,
            original_filename=file.filename,
            file_path=file_path,
            file_size=os.path.getsize(file_path),
            file_type=file_type,
            uploaded_by_id=user_id,
            status="analyzing" if not preview_only else "preview",
            upload_progress=0,
        )
        db.session.add(upload_record)
        db.session.commit()

        # Check if simple parsing is requested (bypass LLM)
        use_simple_parsing = (
            request.form.get("use_simple_parsing", "false").lower() == "true"
        )

        # Initialize provider_name for use later (even if simple parsing)
        provider_name = None
        model = None

        if use_simple_parsing:
            # Use simple parser (no LLM) - only works for CSV/Excel
            from app.services.archimate.simple_parser_service import SimpleParserService

            simple_parser = SimpleParserService()
            extracted_data = simple_parser.parse_document(
                file_path=file_path,
                analysis_context=analysis_context,
                target_application_id=target_application_id,
                target_vendor_id=target_vendor_id,
            )
            interaction = None
            interactions = []
            current_app.logger.info(f"Using simple parsing (no LLM) for {filename}")
        else:
            # Use AI-powered analysis
            analysis_service = DocumentAnalysisService()

            # Get or create event loop for async operations
            loop = get_or_create_event_loop()

            # Determine extraction context based on analysis_context parameter
            extraction_context = "architecture"
            if analysis_context == "application" and target_application_id:
                extraction_context = f"application:{target_application_id}"
            elif analysis_context == "vendor" and target_vendor_id:
                extraction_context = f"vendor:{target_vendor_id}"

            # Get provider - use requested provider if valid, otherwise fall back to configured default
            from app.modules.ai_chat.services.llm_service_impl import LLMService

            try:
                provider_name, model = LLMService._get_configured_provider()
            except ValueError as e:
                # I-01: this used to fall through to the catch-all at the
                # bottom of the function, which swallowed the real reason
                # ("no LLM provider configured") into a generic
                # "An internal error occurred" 500 — indistinguishable from
                # every other failure mode. This is not a server error: it is
                # a feature that is honestly unavailable until an admin
                # configures a provider, so it gets its own 503 naming that.
                current_app.logger.warning(f"Document upload: no LLM provider configured: {e}")
                return jsonify(
                    {
                        "success": False,
                        "error": ASSISTANT_UNAVAILABLE,
                        "code": "LLM_NOT_CONFIGURED",
                    }
                ), 503

            if requested_provider:
                # Validate requested provider exists and is enabled
                from app.models.models import APISettings

                provider_settings = APISettings.query.filter_by(
                    provider=requested_provider, enabled=True
                ).first()

                if provider_settings and provider_settings.api_key:
                    provider_name = requested_provider
                    model = provider_settings.default_model or model
                    current_app.logger.info(
                        f"✅ Using requested provider: {provider_name} with model: {model}"
                    )
                else:
                    current_app.logger.warning(
                        f"⚠️ Requested provider '{requested_provider}' not available or not enabled. "
                        f"Settings found: {provider_settings is not None}, "
                        f"Has key: {provider_settings.api_key if provider_settings else False}, "
                        f"Falling back to default: {provider_name}"
                    )
            else:
                current_app.logger.info(
                    f"No provider requested, using default: {provider_name}"
                )

            upload_record.provider = provider_name

            # Analyze based on file type
            if file_type == "image":
                extracted_data, interaction = loop.run_until_complete(
                    analysis_service._analyze_image(
                        file_path, provider_name, extraction_context
                    )
                )
                interactions = [interaction] if interaction else []
            elif file_type == "document":
                extracted_data, interactions = loop.run_until_complete(
                    analysis_service._analyze_document(
                        file_path, provider_name, extraction_context
                    )
                )
                interaction = interactions[0] if interactions else None
            elif file_type == "spreadsheet":
                extracted_data, interaction = analysis_service._analyze_spreadsheet(
                    file_path, provider_name, analysis_type=extraction_context
                )
                interactions = [interaction] if interaction else []
            else:
                extracted_data, interaction = loop.run_until_complete(
                    analysis_service._analyze_text_file(
                        file_path, provider_name, extraction_context
                    )
                )
                interactions = [interaction] if interaction else []

        # Log extracted data for debugging
        current_app.logger.info(
            f"Document analysis result keys: {list(extracted_data.keys())}"
        )
        current_app.logger.info(
            f"Extracted data metadata: {extracted_data.get('metadata', {})}"
        )

        # Extract ArchiMate elements
        archimate_elements = extracted_data.get("elements", [])
        relationships = extracted_data.get("relationships", [])

        # Normalize element types to match MODEL_REGISTRY
        from app.services.archimate.element_type_normalizer import ElementTypeNormalizer

        normalizer = ElementTypeNormalizer()
        archimate_elements = normalizer.normalize_elements(archimate_elements)

        # Log what was extracted
        current_app.logger.info(
            f"Found {len(archimate_elements)} elements (after normalization) and {len(relationships)} relationships"
        )
        if archimate_elements:
            current_app.logger.info(
                f"Sample elements: {[e.get('name', 'Unknown') + ' (' + e.get('type', 'Unknown') + ')' for e in archimate_elements[:3]]}"
            )
        else:
            # Check if there's an error in metadata
            metadata = extracted_data.get("metadata", {})
            if "error" in metadata:
                current_app.logger.error(
                    f"Document analysis error: {metadata.get('error')}"
                )

        # PREVIEW MODE: Return extracted elements without creating them
        if preview_only:
            current_app.logger.info(
                f"Preview mode: returning {len(archimate_elements)} extracted elements without creating"
            )

            # If no elements found, provide helpful error message
            if len(archimate_elements) == 0:
                metadata = extracted_data.get("metadata", {})
                error_message = metadata.get(
                    "error",
                    "No ArchiMate elements could be identified in the document.",
                )
                error_type = metadata.get("error_type", "no_elements")
                suggestion = metadata.get(
                    "suggestion",
                    "Try uploading a document with application names, system descriptions, or architecture diagrams.",
                )

                current_app.logger.warning(
                    f"Preview mode: No elements extracted - {error_message}"
                )

                # If it's an LLM error, provide specific guidance
                if (
                    error_type == "llm_error"
                    or "failed to generate" in error_message.lower()
                ):
                    suggestion = "The LLM provider failed to process this document. Try: 1) Using Simple Parsing mode, 2) Switching to Claude/GPT - 4 provider, or 3) Uploading a smaller document."
                elif error_type == "chunk_too_large":
                    suggestion = "Document chunks are too large for the selected provider. Try: 1) Using Simple Parsing mode, 2) Switching to Claude/GPT - 4 provider, or 3) Breaking the document into smaller files."

                return jsonify(
                    {
                        "success": True,
                        "message": "Document analyzed but no elements found",
                        "preview_mode": True,
                        "extracted_elements": [],
                        "relationships": [],
                        "metadata": {
                            **metadata,
                            "error": error_message,
                            "error_type": error_type,
                            "suggestion": suggestion,
                        },
                        "file_name": filename,
                        "upload_id": upload_record.id if upload_record else None,
                        "preview_analysis": {
                            "total_elements": 0,
                            "elements_with_missing_fields": 0,
                            "elements_with_duplicates": 0,
                            "average_completeness_score": 0,
                            "elements_with_generated_suggestions": 0,
                        },
                    }
                )

            # ENHANCED: Analyze missing fields and generate suggestions
            from app.services.archimate.confidence_scoring_service import (
                ConfidenceScoringService,
            )
            from app.services.archimate.entity_resolution_service import (
                EntityResolutionService,
            )
            from app.services.archimate.knowledge_graph_service import (
                KnowledgeGraphService,
            )
            from app.services.archimate.missing_fields_analyzer import (
                MissingFieldsAnalyzer,
            )

            analyzer = MissingFieldsAnalyzer()
            confidence_service = ConfidenceScoringService()
            entity_resolution_service = EntityResolutionService()
            kg_service = KnowledgeGraphService()

            # Use the same provider that was requested for the main analysis (if not simple parsing)
            # Get the provider that was actually used (from requested_provider or default)
            analysis_provider = None
            if not use_simple_parsing:
                if provider_name:
                    analysis_provider = provider_name
                elif requested_provider:
                    # Fallback: use requested provider even if main analysis failed
                    from app.models.models import APISettings

                    provider_settings = APISettings.query.filter_by(
                        provider=requested_provider, enabled=True
                    ).first()
                    if provider_settings and provider_settings.api_key:
                        analysis_provider = requested_provider

            # Analyze missing fields for each element and add enhancements
            # OPTIMIZATION: For preview mode with many elements, skip expensive LLM field generation
            # to avoid timeout. Only analyze first 50 elements for field generation.
            MAX_FIELD_GENERATION_ELEMENTS = 50
            should_generate_fields = (
                len(archimate_elements) <= MAX_FIELD_GENERATION_ELEMENTS
            )

            if not should_generate_fields:
                current_app.logger.info(
                    f"Skipping LLM field generation for {len(archimate_elements)} elements "
                    f"(limit: {MAX_FIELD_GENERATION_ELEMENTS}) to prevent timeout"
                )

            analyzed_elements = []
            for idx, elem in enumerate(archimate_elements):
                # Analyze missing fields (lightweight operation)
                analyzed = analyzer.analyze_missing_fields(
                    [elem], elem.get("type", "ApplicationComponent")
                )
                if analyzed:
                    analyzed_elem = analyzed[0]

                    # Generate missing fields ONLY if:
                    # 1. We're within the limit (or preview mode with few elements)
                    # 2. Important fields are missing
                    # 3. We have a valid provider
                    missing_important = analyzed_elem.get("missing_fields", {}).get(
                        "important", []
                    )
                    if (
                        missing_important
                        and analysis_provider
                        and should_generate_fields
                    ):
                        # Only use LLM if we have a valid provider (not simple parsing)
                        try:
                            generated = analyzer.generate_missing_fields(
                                analyzed_elem,
                                analyzed_elem.get("type", "ApplicationComponent"),
                                use_llm=True,
                                provider=analysis_provider,
                            )
                            analyzed_elem["generated_fields"] = generated
                            analyzed_elem["has_generated_suggestions"] = True
                        except (ValueError, Exception) as e:
                            # Provider failed - skip LLM for this and remaining elements
                            if (
                                "404" in str(e)
                                or "not found" in str(e).lower()
                                or "permanent" in str(e).lower()
                            ):
                                # Mark provider as failed and break loop to avoid more attempts
                                current_app.logger.warning(
                                    f"Provider {analysis_provider} failed permanently: {e}. "
                                    f"Skipping LLM field generation for remaining elements."
                                )
                                analysis_provider = (
                                    None  # Disable provider for remaining elements
                                )
                                should_generate_fields = (
                                    False  # Stop trying for remaining elements
                                )
                            # Use heuristics instead
                            analyzed_elem["generated_fields"] = {}
                            analyzed_elem["has_generated_suggestions"] = False
                    else:
                        # No field generation (either skipped due to limit or no provider)
                        analyzed_elem["generated_fields"] = {}
                        analyzed_elem["has_generated_suggestions"] = False

                    analyzed_elem = analyzed_elem
                else:
                    analyzed_elem = elem.copy()

                # ENHANCEMENT: Add confidence score (lightweight, always do this)
                if confidence_service:
                    try:
                        confidence = confidence_service.score_element(
                            analyzed_elem,
                            context=None,
                            extraction_method="llm",
                            validation_result=None,
                            database_match=analyzed_elem.get("resolution", {}).get(
                                "database_match"
                            ),
                        )
                        analyzed_elem["confidence"] = confidence.to_dict()
                    except Exception as e:
                        current_app.logger.warning(f"Error calculating confidence: {e}")

                # ENHANCEMENT: Add entity resolution if not already present
                # OPTIMIZATION: Only resolve first 100 elements to prevent timeout
                if (
                    entity_resolution_service
                    and not analyzed_elem.get("resolution")
                    and idx < 100
                ):
                    try:
                        resolution = entity_resolution_service.resolve_entity(
                            analyzed_elem.get("name", ""),
                            analyzed_elem.get("type"),
                            context=None,
                        )
                        if resolution and resolution.get("confidence", 0) > 0.5:
                            analyzed_elem["resolution"] = resolution
                            # Update name if resolution found
                            if resolution.get("resolved") and resolution.get(
                                "resolved"
                            ) != analyzed_elem.get("name"):
                                analyzed_elem["resolved_name"] = resolution["resolved"]
                    except Exception as e:
                        current_app.logger.warning(f"Error resolving entity: {e}")

                # ENHANCEMENT: Add knowledge graph context
                # OPTIMIZATION: Skip KG for large datasets (expensive operation)
                if kg_service and len(archimate_elements) <= 100:
                    try:
                        kg_context = kg_service.get_semantic_context(
                            analyzed_elem, max_context=3
                        )
                        if kg_context:
                            analyzed_elem["kg_related_entities"] = kg_context
                    except Exception as e:
                        current_app.logger.warning(f"Error getting KG context: {e}")

                analyzed_elements.append(analyzed_elem)

            # Calculate summary statistics
            total_elements = len(analyzed_elements)
            elements_with_missing = sum(
                1
                for e in analyzed_elements
                if e.get("missing_fields", {}).get("all_missing")
            )
            elements_with_duplicates = sum(
                1
                for e in analyzed_elements
                if e.get("properties", {}).get("exists_in_db")
            )
            avg_completeness = (
                sum(e.get("completeness_score", 0) for e in analyzed_elements)
                / total_elements
                if total_elements > 0
                else 0
            )

            # Save upload record in preview state
            try:
                upload_record.status = "preview"
                upload_record.upload_progress = 100
                db.session.commit()
            except Exception as e:
                current_app.logger.warning(
                    f"Could not update upload record for preview: {e}"
                )

            return jsonify(
                {
                    "success": True,
                    "message": f"Preview mode: Found {len(analyzed_elements)} elements",
                    "preview_mode": True,
                    "extracted_elements": analyzed_elements,
                    "relationships": relationships,
                    "metadata": extracted_data.get("metadata", {}),
                    "file_name": filename,
                    "upload_id": upload_record.id if upload_record else None,
                    "preview_analysis": {
                        "total_elements": total_elements,
                        "elements_with_missing_fields": elements_with_missing,
                        "elements_with_duplicates": elements_with_duplicates,
                        "average_completeness_score": round(avg_completeness, 1),
                        "elements_with_generated_suggestions": sum(
                            1
                            for e in analyzed_elements
                            if e.get("has_generated_suggestions")
                        ),
                    },
                }
            )

        # CREATE MODE: Create elements in database
        created_elements = []
        created_count = 0
        skipped_count = 0
        errors = []

        # Batch prefetch: group elements by type, load all existing names per type
        existing_by_type = {}
        elements_by_type = {}
        for elem in archimate_elements:
            etype = elem.get("type")
            ename = elem.get("name", "").strip()
            if etype and ename:
                elements_by_type.setdefault(etype, []).append(ename)
        for (
            etype,
            names,
        ) in (
            elements_by_type.items()
        ):  # model-safety-ok: small fixed set (element types from MODEL_REGISTRY)
            mc = MODEL_REGISTRY.get(etype)
            if mc and names:
                # Not every registered model labels things "name" (e.g. Requirement
                # uses "title"); resolve the label column instead of crashing.
                label_col = getattr(mc, "name", None) or getattr(mc, "title", None)
                if label_col is None:
                    continue  # no label column — duplicate detection skipped for this type
                existing_rows = mc.query.filter(  # model-safety-ok: small fixed set
                    func.lower(label_col).in_([n.lower() for n in names])
                ).all()  # model-safety-ok: small fixed set
                existing_by_type[etype] = {
                    (getattr(row, "name", None) or getattr(row, "title", "")).lower(): row
                    for row in existing_rows
                }

        for elem in archimate_elements:
            try:
                element_type = elem.get("type")
                if not element_type:
                    errors.append(
                        f"Element '{elem.get('name', 'Unknown')}' missing type field"
                    )
                    skipped_count += 1
                    continue

                model_class = MODEL_REGISTRY.get(element_type)
                if not model_class:
                    errors.append(
                        f"Element type '{element_type}' not supported in MODEL_REGISTRY"
                    )
                    skipped_count += 1
                    current_app.logger.warning(
                        f"Unsupported element type: '{element_type}' for element '{elem.get('name', 'Unknown')}'"
                    )
                    continue

                name = elem.get("name", "").strip()
                if not name:
                    errors.append(
                        f"Element with type '{element_type}' missing name field"
                    )
                    skipped_count += 1
                    continue

                # Resolve the ArchiMate layer once so it is available for the
                # archimate link AND the result payload's per-layer breakdown
                # (the upload panel's "Show Details" view groups by layer).
                inferred_layer = elem.get("layer") or normalizer.infer_layer(element_type)

                # Check for existing element (pre-fetched, case-insensitive)
                existing = existing_by_type.get(element_type, {}).get(name.lower())
                if existing:
                    current_app.logger.info(
                        f"Skipping duplicate element: {name} (type: {element_type})"
                    )
                    created_elements.append(
                        {
                            "type": element_type,
                            "name": name,
                            "id": existing.id,
                            "layer": inferred_layer,
                            "status": "existing",
                        }
                    )
                    continue

                # ARCH-030/S-06: no exact-name match, but check for a near
                # duplicate before creating — advisory only, never blocks the
                # write. Routes document ingestion through the same
                # duplicate_guard helper the interactive create endpoints use
                # instead of this path staying silent about near-duplicates.
                label_field = "name" if hasattr(model_class, "name") else "title"
                similar_existing = (
                    find_similar_entities(model_class, name, name_field=label_field)
                    if hasattr(model_class, label_field)
                    else []
                )

                # Build element data with all available fields
                element_data = {
                    "name": name,
                    "description": elem.get("description", "").strip() or None,
                }

                # Add any additional properties that the model might support
                properties = elem.get("properties", {})
                if isinstance(properties, dict):
                    for key, value in properties.items():
                        if hasattr(model_class, key) and value is not None:
                            element_data[key] = value

                # Only add created_by_id if the model supports it
                if hasattr(model_class, "created_by_id"):
                    element_data["created_by_id"] = current_user.id

                # Create the element
                new_element = model_class(**element_data)
                db.session.add(new_element)
                db.session.flush()

                # Auto-create ArchiMateElement link if the model has the FK AND it
                # wasn't already linked by a model's own after_insert listener (e.g.
                # strategy models self-link — UIQA-005). Avoids orphaned duplicates.
                if (
                    hasattr(new_element, "archimate_element_id")
                    and not getattr(new_element, "archimate_element_id", None)
                ):  # model-safety-ok: polymorphic - model_class varies by element_type
                    from app.models.archimate_core import ArchiMateElement

                    archimate_element = ArchiMateElement(
                        name=name,
                        type=element_type,
                        layer=inferred_layer if inferred_layer else None,
                        description=elem.get("description", "").strip() or None,
                    )
                    db.session.add(archimate_element)
                    db.session.flush()
                    new_element.archimate_element_id = archimate_element.id

                created_elements.append(
                    {
                        "type": element_type,
                        "name": name,
                        "id": new_element.id,
                        "layer": inferred_layer,
                        "status": "created",
                        "similar_existing": similar_existing,
                    }
                )
                created_count += 1
                current_app.logger.info(
                    f"Created element: {name} (type: {element_type}, id: {new_element.id})"
                    + (f" — {len(similar_existing)} similar existing element(s) found" if similar_existing else "")
                )

            except Exception as e:
                error_msg = f"Error creating {elem.get('type', 'unknown')} '{elem.get('name', 'Unknown')}': {str(e)}"
                errors.append(error_msg)
                current_app.logger.error(error_msg, exc_info=True)
                skipped_count += 1

        if created_count > 0:
            db.session.commit()

        # Update document record with consolidated retry logic
        def safe_json_dumps(data):
            """Safely serialize data to JSON."""
            try:
                return json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                current_app.logger.warning(f"JSON serialization error: {e}")
                return json.dumps({"error": "Serialization error occurred"})

        def update_document_results():
            db.session.refresh(upload_record)
            upload_record.status = "completed" if not errors else "partial"
            upload_record.upload_progress = 100
            upload_record.created_elements_count = created_count
            upload_record.created_elements_details = safe_json_dumps(created_elements)
            upload_record.errors = safe_json_dumps(errors) if errors else None
            upload_record.confidence = extracted_data.get("metadata", {}).get(
                "confidence", "medium"
            )
            upload_record.analysis_results = safe_json_dumps(extracted_data)
            upload_record.analyzed_at = datetime.utcnow()
            db.session.commit()

        success, _, error = execute_with_db_retry(
            update_document_results, operation_name="update document upload results"
        )

        if not success:
            current_app.logger.error(f"Failed to update document record: {error}")
            raise Exception(f"Failed to save upload results: {error}")

        # RAG-004: Chunk and embed the document text for retrieval
        try:
            from app.modules.ai_chat.services.document_processing_service import (
                DocumentProcessingService,
            )

            doc_svc = DocumentProcessingService()
            extraction_result = doc_svc.extract_text(file_path)
            if extraction_result.get("success") and extraction_result.get("content"):
                chunk_count = doc_svc.chunk_and_embed(
                    upload_record.id, extraction_result["content"]
                )
                current_app.logger.info(
                    f"RAG-004: Created {chunk_count} chunks for document {upload_record.id}"
                )
        except Exception as chunk_err:
            current_app.logger.warning(
                f"RAG-004: Chunking/embedding failed for document {upload_record.id}: {chunk_err}"
            )

        # Build comprehensive response
        response_message = f"Document analyzed. Created {created_count} new elements."
        if skipped_count > 0:
            response_message += (
                f" Skipped {skipped_count} elements (duplicates or errors)."
            )
        if errors:
            response_message += f" {len(errors)} errors occurred."

        return jsonify(
            {
                "success": True,
                "message": response_message,
                "analysis_results": {
                    "created_elements": created_count,
                    "skipped_elements": skipped_count,
                    "total_extracted": len(archimate_elements),
                    "created_details": created_elements,
                    "errors": errors,
                },
                "upload_id": upload_record.id,
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error uploading document: {e}", exc_info=True)
        # I-01: name the real failure instead of a generic apology — the QA
        # register's whole complaint was a 500 that "gives nothing to act
        # on". str(e) here is a caught exception's message, not user input,
        # so it is not a data-fabrication concern under CLAUDE.md.
        return jsonify(
            {
                "success": False,
                "error": f"Document upload failed: {e}",
                "error_type": type(e).__name__,
            }
        ), 500


@unified_ai_chat_bp.route("/documents", methods=["GET"])
@login_required
def get_document_history():
    """Get list of uploaded documents for the current user."""
    try:
        # Ensure table exists (auto-create if missing from migration)
        from sqlalchemy import inspect as sa_inspect

        from app.models.ai_chat_document import AIChatDocumentUpload

        if not sa_inspect(db.engine).has_table(AIChatDocumentUpload.__tablename__):
            AIChatDocumentUpload.__table__.create(db.engine)
            current_app.logger.info(
                f"Auto-created missing table: {AIChatDocumentUpload.__tablename__}"
            )

        # Get query parameters with pagination bounds checking
        MAX_LIMIT = 1000  # Maximum records per request
        DEFAULT_LIMIT = 50
        try:
            limit = safe_int_arg("limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_PAGE_SIZE)
            limit = min(max(limit, 1), MAX_LIMIT)  # Clamp between 1 and MAX_LIMIT
        except (ValueError, TypeError):
            limit = DEFAULT_LIMIT

        try:
            offset = safe_int_arg('offset', 0, minimum=0)
            offset = max(offset, 0)  # Ensure non-negative
        except (ValueError, TypeError):
            offset = 0

        status_filter = request.args.get("status")  # Optional status filter
        ALLOWED_STATUSES = {"uploading", "analyzing", "completed", "failed", "preview"}
        if status_filter and status_filter not in ALLOWED_STATUSES:
            status_filter = None

        # Build query
        query = AIChatDocumentUpload.query.filter_by(uploaded_by_id=current_user.id)

        if status_filter:
            query = query.filter_by(status=status_filter)

        # Order by most recent first
        query = query.order_by(AIChatDocumentUpload.created_at.desc())

        # Get total count
        total = query.count()

        # Get paginated results
        documents = query.limit(limit).offset(offset).all()

        return jsonify(
            {
                "success": True,
                "documents": [doc.to_dict() for doc in documents],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error fetching document history: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/documents/<int:doc_id>", methods=["GET"])
@login_required
def get_document_details(doc_id):
    """Get detailed information about a specific uploaded document."""
    try:
        from app.models.ai_chat_document import AIChatDocumentUpload

        document = AIChatDocumentUpload.query.filter_by(
            id=doc_id, uploaded_by_id=current_user.id
        ).first()

        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        # Parse analysis results
        analysis_results = None
        if document.analysis_results:
            try:
                analysis_results = (
                    json.loads(document.analysis_results)
                    if isinstance(document.analysis_results, str)
                    else document.analysis_results
                )
            except (json.JSONDecodeError, TypeError):
                analysis_results = {"error": "Failed to parse analysis results"}

        return jsonify(
            {
                "success": True,
                "document": {
                    "id": document.id,
                    "filename": document.original_filename,
                    "file_name": document.file_name,
                    "file_size": document.file_size,
                    "file_type": document.file_type,
                    "status": document.status,
                    "uploaded_at": document.created_at.isoformat()
                    if document.created_at
                    else None,
                    "analyzed_at": document.analyzed_at.isoformat()
                    if document.analyzed_at
                    else None,
                    "created_elements_count": document.created_elements_count,
                    "confidence": document.confidence,
                    "summary": document.chat_context_summary,
                    "analysis_results": analysis_results,
                },
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error getting document details: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/documents/<int:doc_id>", methods=["DELETE"])
@login_required
@audit_log("delete_document")
def delete_document(doc_id):
    """Delete an uploaded document record."""
    try:
        from app.models.ai_chat_document import AIChatDocumentUpload

        current_app.logger.info(
            f"Delete request for document ID: {doc_id}, user ID: {current_user.id}"
        )

        # Try to find the document
        document = AIChatDocumentUpload.query.filter_by(id=doc_id).first()

        if not document:
            current_app.logger.warning(f"Document {doc_id} not found")
            return jsonify({"success": False, "error": "Document not found"}), 404

        # Check ownership
        if document.uploaded_by_id != current_user.id:
            current_app.logger.warning(
                f"User {current_user.id} attempted to delete document {doc_id} owned by {document.uploaded_by_id}"
            )
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Unauthorized - you can only delete your own documents",
                    }
                ),
                403,
            )

        # Delete file if it exists
        if document.file_path and os.path.exists(document.file_path):
            try:
                os.remove(document.file_path)
            except Exception as e:
                current_app.logger.warning(
                    f"Could not delete file {document.file_path}: {e}"
                )

        # Delete database record
        db.session.delete(document)
        db.session.commit()

        return jsonify({"success": True, "message": "Document deleted successfully"})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting document: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/documents/<int:doc_id>/re-analyze", methods=["POST"])
@login_required
@audit_log("re_analyze_document")
def re_analyze_document(doc_id):
    """Re-analyze a previously uploaded document."""
    try:
        from app.archimate_crud.routes import MODEL_REGISTRY
        from app.models.ai_chat_document import AIChatDocumentUpload
        from app.services.archimate.document_analysis_service import (
            DocumentAnalysisService,
        )
        from app.services.core.async_utils import get_or_create_event_loop
        from app.services.core.retry_handler import execute_with_db_retry

        document = AIChatDocumentUpload.query.filter_by(
            id=doc_id, uploaded_by_id=current_user.id
        ).first()

        if not document:
            return jsonify(
                {"success": False, "error": "Document not found or unauthorized"}
            ), 404

        # Check if file still exists
        if not document.file_path or not os.path.exists(document.file_path):
            return jsonify({"success": False, "error": "File no longer exists"}), 404

        # Update status
        document.status = "analyzing"
        document.upload_progress = 0
        db.session.commit()

        # Analyze document
        analysis_service = DocumentAnalysisService()

        # Get or create event loop for async operations
        loop = get_or_create_event_loop()

        if document.file_type == "image":
            extracted_data, interaction = loop.run_until_complete(
                analysis_service._analyze_image(
                    document.file_path, document.provider, "architecture"
                )
            )
        elif document.file_type == "document":
            extracted_data, interactions = loop.run_until_complete(
                analysis_service._analyze_document(
                    document.file_path, document.provider, "architecture"
                )
            )
            interaction = interactions[0] if interactions else None
        else:
            extracted_data, interaction = loop.run_until_complete(
                analysis_service._analyze_text_file(
                    document.file_path, document.provider, "architecture"
                )
            )

        # Extract and create elements (same logic as upload)
        archimate_elements = extracted_data.get("elements", [])
        created_elements = []
        created_count = 0
        errors = []

        # Batch prefetch: group elements by type, load all existing names per type
        _elements_by_type2 = {}
        for _elem in archimate_elements:
            _etype = _elem.get("type")
            _ename = _elem.get("name", "")
            if _etype and _ename:
                _elements_by_type2.setdefault(_etype, []).append(_ename)
        _existing_by_type2 = {}
        for (
            _etype,
            _names,
        ) in (
            _elements_by_type2.items()
        ):  # model-safety-ok: small fixed set (element types from MODEL_REGISTRY)
            _mc = MODEL_REGISTRY.get(_etype)
            if _mc and _names:
                _existing_rows = _mc.query.filter(
                    _mc.name.in_(_names)
                ).all()  # model-safety-ok: small fixed set
                _existing_by_type2[_etype] = {r.name for r in _existing_rows}

        for elem in archimate_elements:
            try:
                element_type = elem.get("type")
                model_class = MODEL_REGISTRY.get(element_type)
                if not model_class:
                    errors.append(f"Element type '{element_type}' not supported")
                    continue

                name = elem.get("name", "")
                # Check for existing element (pre-fetched)
                if name in _existing_by_type2.get(element_type, set()):
                    continue

                element_data = {
                    "name": name,
                    "description": elem.get("description", ""),
                }

                # Only add created_by_id if the model supports it
                if hasattr(model_class, "created_by_id"):
                    element_data["created_by_id"] = current_user.id

                new_element = model_class(**element_data)
                db.session.add(new_element)
                db.session.flush()

                # Auto-create ArchiMateElement link if the model has the FK AND it
                # wasn't already linked by a model's own after_insert listener (e.g.
                # strategy models self-link — UIQA-005). Avoids orphaned duplicates.
                if (
                    hasattr(new_element, "archimate_element_id")
                    and not getattr(new_element, "archimate_element_id", None)
                ):  # model-safety-ok: polymorphic - model_class varies by element_type
                    from app.models.archimate_core import ArchiMateElement

                    layer = elem.get("layer", "")
                    archimate_element = ArchiMateElement(
                        name=name,
                        type=element_type,
                        layer=layer if layer else None,
                        description=elem.get("description", ""),
                    )
                    db.session.add(archimate_element)
                    db.session.flush()
                    new_element.archimate_element_id = archimate_element.id

                created_elements.append(
                    {
                        "type": element_type,
                        "name": name,
                        "id": new_element.id,
                        "status": "created",
                    }
                )
                created_count += 1

            except Exception as e:
                errors.append(f"Error creating {elem.get('type', 'unknown')}: {str(e)}")

        if created_count > 0:
            db.session.commit()

        # Update document record with consolidated retry logic
        def safe_json_dumps(data):
            """Safely serialize data to JSON."""
            try:
                return json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                current_app.logger.warning(f"JSON serialization error: {e}")
                return json.dumps({"error": "Serialization error occurred"})

        def update_document_results():
            db.session.refresh(document)
            document.status = "completed" if not errors else "partial"
            document.upload_progress = 100
            document.created_elements_count = created_count
            document.created_elements_details = safe_json_dumps(created_elements)
            document.errors = safe_json_dumps(errors) if errors else None
            document.confidence = extracted_data.get("metadata", {}).get(
                "confidence", "medium"
            )
            document.analysis_results = safe_json_dumps(extracted_data)
            document.analyzed_at = datetime.utcnow()
            db.session.commit()

        success, _, error = execute_with_db_retry(
            update_document_results, operation_name="update re-analysis results"
        )

        if not success:
            current_app.logger.error(f"Failed to update document record: {error}")
            raise Exception(f"Failed to save re-analysis results: {error}")

        return jsonify(
            {
                "success": True,
                "message": f"Document re-analyzed. Created {created_count} new elements.",
                "analysis_results": {
                    "created_elements": created_count,
                    "created_details": created_elements,
                    "errors": errors,
                },
            }
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error re-analyzing document: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/create-elements", methods=["POST"])
@login_required
@audit_log("create_elements")
def create_elements():
    """
    Create ArchiMate elements from user-selected preview list.

    This endpoint is called after preview mode to create only the elements
    the user has selected and optionally edited.

    Expected JSON body:
    {
        "elements": [{"name": "...", "type": "...", "description": "...", "layer": "..."}],
        "analysis_context": "general|application|vendor",
        "target_application_id": 123,  # optional
        "target_vendor_id": 456  # optional
    }
    """
    try:
        from app.archimate_crud.routes import MODEL_REGISTRY
        from app.models.archimate_core import ArchiMateElement

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        elements = data.get("elements", [])
        if not elements:
            return jsonify({"success": False, "error": "No elements provided"}), 400

        analysis_context = data.get("analysis_context", "general")
        target_application_id = data.get("target_application_id")
        target_vendor_id = data.get("target_vendor_id")

        # Convert IDs to integers if provided
        if target_application_id:
            try:
                target_application_id = int(target_application_id)
            except (ValueError, TypeError):
                target_application_id = None

        if target_vendor_id:
            try:
                target_vendor_id = int(target_vendor_id)
            except (ValueError, TypeError):
                target_vendor_id = None

        # Vendor-context creation writes VendorProduct rows into the global
        # vendor catalogue, so it is platform-admin only. Application and
        # general-context creation stays open to ordinary users.
        if (
            target_vendor_id
            and analysis_context == "vendor"
            and not is_platform_admin(current_user)
        ):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Platform admin access required to create vendor elements",
                    }
                ),
                403,
            )

        user_id = current_user.id if current_user.is_authenticated else None

        current_app.logger.info(
            f"Creating {len(elements)} elements - Context: {analysis_context}, App: {target_application_id}, Vendor: {target_vendor_id}"
        )

        created_elements = []
        created_count = 0
        errors = []

        # Import field mapper
        from app.ai_chat.element_field_mapper import (
            check_element_exists,
            map_element_data_to_model_fields,
        )

        for elem in elements:
            element_type = elem.get("type")
            name = elem.get("name", "").strip()
            layer = elem.get("layer", "").lower()
            description = elem.get("description", "")

            if not name:
                errors.append(
                    f"Element with type '{element_type}' has no name - skipped"
                )
                continue

            # Find the model class
            model_class = MODEL_REGISTRY.get(element_type)
            if not model_class:
                errors.append(
                    f"Element type '{element_type}' not supported - skipped '{name}'"
                )
                continue

            # Check if element already exists (using correct field name)
            existing = check_element_exists(model_class, element_type, name)
            if existing:
                created_elements.append(
                    {
                        "type": element_type,
                        "name": name,
                        "id": existing.id,
                        "status": "skipped",
                        "reason": "Already exists",
                    }
                )
                continue

            # Use savepoint for each element
            try:
                savepoint = db.session.begin_nested()

                # Map element data to model-specific fields
                element_data = map_element_data_to_model_fields(
                    element_type, elem, user_id
                )

                # Only add created_by_id if the model supports it
                if hasattr(model_class, "created_by_id"):
                    element_data["created_by_id"] = user_id

                # Filter out any fields that don't exist on the model to prevent TypeError
                # This prevents errors like 'deployment_type' is an invalid keyword argument
                from sqlalchemy import inspect

                valid_columns = {col.key for col in inspect(model_class).columns}
                element_data = {
                    k: v for k, v in element_data.items() if k in valid_columns
                }

                # Create the element
                new_element = model_class(**element_data)
                db.session.add(new_element)
                db.session.flush()

                # Auto-create ArchiMateElement link
                archimate_element = ArchiMateElement(
                    name=name,
                    type=element_type,
                    layer=layer if layer else None,
                    description=description,
                )
                db.session.add(archimate_element)
                db.session.flush()

                # Link them
                if hasattr(
                    new_element, "archimate_element_id"
                ):  # model-safety-ok: polymorphic - model_class varies by element_type
                    new_element.archimate_element_id = archimate_element.id

                # Link to target application or vendor if specified
                linked_to = None
                if target_application_id and analysis_context == "application":
                    if hasattr(
                        new_element, "application_id"
                    ):  # model-safety-ok: polymorphic - model_class varies by element_type
                        new_element.application_id = target_application_id
                        linked_to = {"type": "application", "id": target_application_id}
                    # Also add to relationship table if available
                    try:
                        from app.models.relationship_tables import (
                            application_component_elements,
                        )

                        if element_type in [
                            "ApplicationService",
                            "ApplicationInterface",
                            "DataObject",
                            "ApplicationFunction",
                        ]:
                            db.session.execute(  # tenant-filtered: scoped via parent FK (application_component_id)
                                application_component_elements.insert().values(
                                    application_component_id=target_application_id,
                                    archimate_element_id=archimate_element.id,
                                )
                            )
                            linked_to = {
                                "type": "application",
                                "id": target_application_id,
                            }
                    except Exception as link_error:
                        current_app.logger.warning(
                            f"Could not link element to application: {link_error}"
                        )

                elif target_vendor_id and analysis_context == "vendor":
                    if hasattr(
                        new_element, "vendor_id"
                    ):  # model-safety-ok: polymorphic - model_class varies by element_type
                        new_element.vendor_id = target_vendor_id
                        linked_to = {"type": "vendor", "id": target_vendor_id}
                    elif hasattr(
                        new_element, "organization_id"
                    ):  # model-safety-ok: polymorphic - model_class varies by element_type
                        new_element.organization_id = target_vendor_id
                        linked_to = {"type": "vendor", "id": target_vendor_id}
                    # For vendor products
                    try:
                        if is_application_kind(element_type) or element_type in [
                            "TechnologyService",
                            "Product",
                        ]:
                            from app.models.vendor.vendor_organization import (
                                VendorProduct,
                            )

                            vendor_product = VendorProduct(
                                name=name,
                                description=description,
                                vendor_organization_id=target_vendor_id,
                                product_type=element_type,
                                # A company's own record, never a shared catalogue row.
                                organization_id=getattr(g, "current_org_id", None),
                            )
                            db.session.add(vendor_product)
                            linked_to = {
                                "type": "vendor_product",
                                "id": target_vendor_id,
                                "product_name": name,
                            }
                    except Exception as vendor_link_error:
                        current_app.logger.warning(
                            f"Could not create vendor product: {vendor_link_error}"
                        )

                # Commit the savepoint
                savepoint.commit()

                created_elements.append(
                    {
                        "type": element_type,
                        "name": name,
                        "id": new_element.id,
                        "status": "created",
                        "layer": layer,
                        "linked_to": linked_to,
                    }
                )
                created_count += 1

            except Exception as e:
                try:
                    savepoint.rollback()
                except Exception:
                    logger.debug(
                        "Failed to rollback savepoint for element creation",
                        exc_info=True,
                    )
                errors.append(f"Error creating {element_type} '{name}': {str(e)}")
                current_app.logger.error(
                    f"Error creating element {name}: {e}", exc_info=True
                )

        # Commit all successfully created elements
        if created_count > 0:
            try:
                db.session.commit()
            except Exception as commit_error:
                db.session.rollback()
                current_app.logger.error(f"Error committing elements: {commit_error}")
                errors.append(f"Failed to commit created elements: {str(commit_error)}")

        # Build response with links to view created elements
        view_links = {}
        if target_application_id and analysis_context == "application":
            view_links["application"] = (
                f"/dashboard/application/{target_application_id}"
            )
            view_links["application_architecture"] = (
                f"/dashboard/application/{target_application_id}#architecture"
            )
        if target_vendor_id and analysis_context == "vendor":
            view_links["vendor"] = f"/vendors/view/{target_vendor_id}"

        return jsonify(
            {
                "success": True,
                "message": f"Created {created_count} ArchiMate elements",
                "created_count": created_count,
                "created_elements": created_elements,
                "errors": errors,
                "view_links": view_links,
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating elements: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# DOCUMENT FEEDBACK AND COMPARISON
# ============================================================================


@unified_ai_chat_bp.route("/documents/<int:doc_id>/feedback", methods=["POST"])
@login_required
@audit_log("record_document_feedback")
def record_feedback(doc_id):
    """Record user feedback/correction for learning."""
    try:
        from app.models.ai_chat_document import AIChatDocumentUpload
        from app.services.archimate.feedback_learning_service import (
            FeedbackLearningService,
        )

        document = AIChatDocumentUpload.query.filter_by(
            id=doc_id, uploaded_by_id=current_user.id
        ).first()

        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        data = request.json
        original_element = data.get("original_element")
        corrected_element = data.get("corrected_element")

        if not original_element or not corrected_element:
            return (
                jsonify(
                    {"success": False, "error": "Missing original or corrected element"}
                ),
                400,
            )

        # Get document hash for pattern matching
        import hashlib

        doc_hash = (
            hashlib.sha256((document.analysis_results or "").encode()).hexdigest()
            if document.analysis_results
            else None
        )

        # Get confidence before if available
        confidence_before = original_element.get("confidence", {}).get("score")

        learning_service = FeedbackLearningService()
        feedback_id = learning_service.record_correction(
            original_element=original_element,
            corrected_element=corrected_element,
            document_id=doc_id,
            document_hash=doc_hash,
            user_id=current_user.id,
            confidence_before=confidence_before,
        )

        return jsonify(
            {
                "success": True,
                "feedback_id": feedback_id,
                "message": "Feedback recorded successfully",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error recording feedback: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/documents/<int:doc_id>/compare", methods=["GET"])
@login_required
def compare_document_versions(doc_id):
    """Compare document analysis versions."""
    try:
        from app.models.ai_chat_document import AIChatDocumentUpload
        from app.services.archimate.document_comparison_service import (
            DocumentComparisonService,
        )

        document = AIChatDocumentUpload.query.filter_by(
            id=doc_id, uploaded_by_id=current_user.id
        ).first()

        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        request.args.get("version1_id", type=int)
        version2_id = request.args.get("version2_id", type=int)

        comparison_service = DocumentComparisonService()

        # Parse analysis results
        analysis1 = (
            json.loads(document.analysis_results) if document.analysis_results else {}
        )
        analysis2 = {}

        if version2_id:
            # Compare with another document version
            doc2 = AIChatDocumentUpload.query.get(version2_id)
            if doc2:
                analysis2 = (
                    json.loads(doc2.analysis_results) if doc2.analysis_results else {}
                )
        else:
            # Compare with current state (if re-analyzed)
            analysis2 = analysis1.copy()

        comparison = comparison_service.compare_analyses(analysis1, analysis2)
        comparison_service.generate_diff_report(comparison)

        return jsonify(
            {
                "success": True,
                "comparison": {
                    "summary": comparison.summary,
                    "added_elements": [
                        {"name": e.get("name"), "type": e.get("type")}
                        for e in comparison.added_elements
                    ],
                    "removed_elements": [
                        {"name": e.get("name"), "type": e.get("type")}
                        for e in comparison.removed_elements
                    ],
                    "modified_elements": [
                        {
                            "name": change.element_name,
                            "changes": {
                                field: {"old": old_val, "new": new_val}
                                for field, (old_val, new_val) in change.changes.items()
                            },
                        }
                        for change in comparison.modified_elements
                    ],
                },
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error comparing documents: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

