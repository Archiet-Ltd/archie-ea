"""
-> app.modules.architecture.services.governance_service

Architect Workflow Service

Orchestrates actionable workflows for architects within the AI Chat:
1. ArchiMate Generation (Preview & Apply)
2. APQC Process Mapping (Preview & Apply)
3. Bulk Application Processing
4. Gap Analysis to Roadmap
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
from app.services.application_architecture_mapper import ApplicationArchitectureMapperService
from app.services.apqc_classification_service import classify_apqc_text
from app.services.comprehensive_archimate_service import ComprehensiveArchiMateService
from app.services.confidence_review_service import ConfidenceReviewService, ReviewQueueItemData
from app.models.archimate_element_types import StandardTypeTable

logger = logging.getLogger(__name__)


class ArchitectWorkflowService:
    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.mapper_service = ApplicationArchitectureMapperService
        self.archimate_service = ComprehensiveArchiMateService()
        self.confidence_service = ConfidenceReviewService()

    def generate_archimate_preview(self, application_id: int) -> Dict[str, Any]:
        """
        Generate a preview of ArchiMate elements for an application using AI analysis.
        Includes confidence evaluation.
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return {"success": False, "error": "Application not found"}

        # Use comprehensive analysis to get suggestions
        analysis = self.mapper_service.analyze_application_comprehensive(
            application_id=application_id,
            map_capabilities=False,
            map_processes=False,
            generate_archimate=True,
        )

        if "error" in analysis:
            return {"success": False, "error": analysis["error"]}

        suggestions = analysis.get("archimate_suggestions", [])

        # Structure for UI
        preview = {"application_name": app.name, "application_id": app.id, "elements": []}

        for item in suggestions:
            # item has {type, name, description, reasoning, confidence_score}
            # Infer layer from type
            layer = self._infer_layer(item.get("type"))
            confidence_score = item.get("confidence_score", 0.8)

            # Evaluate confidence using service
            evaluation = self._evaluate_suggestion(
                item_type="archimate_generation",
                item_name=item.get("name"),
                item_data=item,
                confidence_score=confidence_score,
            )

            preview["elements"].append(
                {
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "layer": layer,
                    "description": item.get("description"),
                    "confidence": int(confidence_score * 100),  # Convert to percentage for display
                    "reasoning": item.get("reasoning"),
                    "evaluation": evaluation,
                    "status": self._get_confidence_status(evaluation),  # green, yellow, red
                    "selected": evaluation.get("action", {}).get("action")
                    != "reject",  # Default selection based on rejection
                }
            )

        return {"success": True, "preview": preview}

    def apply_archimate_elements(
        self, application_id: int, elements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Apply selected ArchiMate elements to the application.
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return {"success": False, "error": "Application not found"}

        # Get or create architecture model
        arch_model = self.archimate_service._get_or_create_architecture_model(app)

        created_count = 0
        created_ids = []

        try:
            for elem_data in elements:
                # Create element
                element = self.archimate_service._create_element(
                    architecture_model=arch_model,
                    element_type=elem_data.get("type", "ApplicationComponent"),
                    name=elem_data.get("name"),
                    layer=elem_data.get("layer", "application").lower(),
                    description=elem_data.get("description"),
                    source_app_id=app.id,
                    properties={
                        "confidence_score": elem_data.get("confidence"),
                        "generated_by": "ai_architect_workflow",
                        "reasoning": elem_data.get("reasoning"),
                    },
                )
                created_count += 1
                created_ids.append(element.id)

                # Auto-link to application component if appropriate
                # Logic handled by services or could be enhanced here

            db.session.commit()
            return {
                "success": True,
                "message": f"Successfully created {created_count} ArchiMate elements",
                "element_ids": created_ids,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error applying ArchiMate elements: {e}")
            return {"success": False, "error": str(e)}

    def map_apqc_preview(self, application_id: int) -> Dict[str, Any]:
        """
        Generate APQC mapping suggestions for an application.
        Includes confidence evaluation.
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return {"success": False, "error": "Application not found"}

        # 1. Use extraction from mapper service
        extracted_codes = self.mapper_service.extract_apqc_processes_from_application(app)

        # 2. Use semantic search on description/capabilities
        search_text = f"{app.name} {app.description} {app.imported_capabilities or ''}"
        semantic_results = classify_apqc_text(search_text, max_results=10)

        suggestions = []
        seen_codes = set()

        # Add extracted codes (high confidence)
        for code in extracted_codes:
            process = APQCProcess.query.filter(APQCProcess.process_code.like(f"{code}%")).first()
            if process and process.process_code not in seen_codes:
                confidence = 0.95

                # Evaluate confidence
                evaluation = self._evaluate_suggestion(
                    item_type="apqc_process_classification",
                    item_name=f"{process.process_code} {process.process_name}",
                    item_data={"process_id": process.id, "code": process.process_code},
                    confidence_score=confidence,
                )

                suggestions.append(
                    {
                        "process_id": process.id,
                        "code": process.process_code,
                        "name": process.process_name,
                        "hierarchy": self._get_apqc_hierarchy(process),
                        "confidence": int(confidence * 100),
                        "source": "Keyword Match",
                        "evaluation": evaluation,
                        "status": self._get_confidence_status(evaluation),
                        "selected": True,
                    }
                )
                seen_codes.add(process.process_code)

        # Add semantic results
        for res in semantic_results:
            # res has process_code, process_name, score
            code = res.get("process_code")
            if code and code not in seen_codes:
                # Resolve to DB object to get ID
                process = APQCProcess.query.filter_by(process_code=code).first()
                if process:
                    confidence = res.get("score", 0)

                    # Evaluate confidence
                    evaluation = self._evaluate_suggestion(
                        item_type="apqc_process_classification",
                        item_name=f"{process.process_code} {process.process_name}",
                        item_data={"process_id": process.id, "code": process.process_code},
                        confidence_score=confidence,
                    )

                    suggestions.append(
                        {
                            "process_id": process.id,
                            "code": process.process_code,
                            "name": process.process_name,
                            "hierarchy": self._get_apqc_hierarchy(process),
                            "confidence": int(confidence * 100),
                            "source": "Semantic Match",
                            "evaluation": evaluation,
                            "status": self._get_confidence_status(evaluation),
                            "selected": evaluation.get("action", {}).get("action") != "reject",
                        }
                    )
                    seen_codes.add(code)

        # Sort by confidence
        suggestions.sort(key=lambda x: x["confidence"], reverse=True)

        return {
            "success": True,
            "application_name": app.name,
            "application_id": app.id,
            "suggestions": suggestions,
        }

    def apply_apqc_mappings(
        self, application_id: int, mappings: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Apply selected APQC mappings.
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return {"success": False, "error": "Application not found"}

        created_count = 0
        try:
            for item in mappings:
                process_id = item.get("process_id")

                # Check existence
                existing = ProcessApplicationMapping.query.filter_by(
                    application_id=application_id, apqc_process_id=process_id
                ).first()

                if not existing:
                    mapping = ProcessApplicationMapping(
                        application_id=application_id,
                        apqc_process_id=process_id,
                        support_level="partial",  # Default
                        process_coverage=item.get("confidence", 80),
                        application_role="supporting",
                        created_by=str(self.user_id) if self.user_id else "system",
                    )
                    db.session.add(mapping)
                    created_count += 1

            db.session.commit()
            return {
                "success": True,
                "message": f"Successfully created {created_count} APQC mappings",
                "count": created_count,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error applying APQC mappings: {e}")
            return {"success": False, "error": str(e)}

    def _infer_layer(self, element_type: str) -> str:
        """Infer ArchiMate layer from element type."""
        mapping = StandardTypeTable({
            "BusinessProcess": "Business",
            "BusinessFunction": "Business",
            "BusinessService": "Business",
            "BusinessRole": "Business",
            "BusinessActor": "Business",
            "ApplicationComponent": "Application",
            "ApplicationService": "Application",
            "ApplicationInterface": "Application",
            "DataObject": "Application",
            "SystemSoftware": "Technology",
            "Node": "Technology",
            "TechnologyService": "Technology",
            "Artifact": "Technology",
            "Capability": "Strategy",
            "ValueStream": "Strategy",
            "CourseOfAction": "Strategy",
            "Resource": "Strategy",
            "Requirement": "Motivation",
            "Goal": "Motivation",
            "Driver": "Motivation",
            "Principle": "Motivation",
            "WorkPackage": "Implementation",
            "Deliverable": "Implementation",
        })
        return mapping.get(element_type, "Application")

    def _get_apqc_hierarchy(self, process: APQCProcess) -> str:
        """Get breadcrumb hierarchy string for APQC process."""
        return f"{process.category_level_1} > {process.category_level_2}"

    def _evaluate_suggestion(
        self, item_type: str, item_name: str, item_data: Dict, confidence_score: float
    ) -> Dict[str, Any]:
        """Evaluate a suggestion using the ConfidenceReviewService."""
        try:
            queue_item = ReviewQueueItemData(
                item_type=item_type,
                item_id=0,  # Placeholder for new items
                item_name=item_name or "Unknown Item",
                item_data=item_data,
                confidence_score=confidence_score,
                confidence_factors={"source": "ai_generation"},
                ai_model_used="default",
                generation_timestamp=datetime.utcnow(),
                threshold_name="default",  # Will be resolved by service
            )
            return self.confidence_service.evaluate_confidence_threshold(queue_item)
        except Exception as e:
            logger.error(f"Error evaluating confidence: {e}")
            # Fallback evaluation
            return {
                "action": {"action": "queue_for_review"},
                "requires_review": True,
                "error": str(e),
            }

    def _get_confidence_status(self, evaluation: Dict[str, Any]) -> str:
        """Map evaluation result to UI status (green, yellow, red)."""
        action = evaluation.get("action", {}).get("action")
        if action == "auto_approve":
            return "high_confidence"  # Green
        elif action == "reject":
            return "low_confidence"  # Red
        else:
            return "medium_confidence"  # Yellow
