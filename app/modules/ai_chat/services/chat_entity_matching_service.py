"""
-> app.modules.ai_chat.services.chat_service

Chat Entity Matching Service

Combines direct document entity matching integration with enhanced AI chat context.
Provides seamless entity matching capabilities within the AI chat interface.

Features:
- Direct service integration with DocumentEntityMatchingService
- Chat-aware entity extraction and matching
- Persona-driven matching priorities
- Interactive UI feedback mechanisms
- Real-time entity relationship discovery
- Gap analysis visualization
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.document_entity_matching_service import DocumentEntityMatchingService
from app.services.multi_domain_chat_service import MultiDomainChatService

logger = logging.getLogger(__name__)


class ChatEntityMatchingService:
    """
    Enhanced entity matching service that integrates document entity matching
    with the AI chat system for seamless user experience.
    """

    def __init__(self):
        """Initialize the chat entity matching service."""
        self.entity_matcher = DocumentEntityMatchingService()
        self.chat_service = MultiDomainChatService()

    def analyze_document_with_chat_context(
        self,
        document_text: str,
        user_persona: str = "enterprise_architect",
        domain: str = "architecture",
        chat_history: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze document using chat context and provide entity matching results.

        Args:
            document_text: The document content to analyze
            user_persona: User persona for prioritized extraction
            domain: Domain specialization for context
            chat_history: Previous chat context for continuity

        Returns:
            Comprehensive analysis results with entity matches and chat insights
        """
        try:
            # Step 1: Extract entities using chat service context
            extracted_entities = self._extract_entities_with_chat_context(
                document_text, user_persona, domain, chat_history
            )

            # Step 2: Match entities using document matching service
            matching_results = self._match_all_entity_types(extracted_entities, user_persona)

            # Step 3: Generate chat-specific insights
            chat_insights = self._generate_chat_insights(
                extracted_entities, matching_results, user_persona, domain
            )

            # Step 4: Format for UI consumption
            ui_formatted = self._format_for_chat_ui(
                extracted_entities, matching_results, chat_insights
            )

            return {
                "success": True,
                "document_analysis": {
                    "extracted_entities": extracted_entities,
                    "entity_counts": self._count_entities_by_type(extracted_entities),
                    "processing_time": datetime.utcnow().isoformat(),
                },
                "matching_results": matching_results,
                "chat_insights": chat_insights,
                "ui_data": ui_formatted,
                "recommendations": self._generate_action_recommendations(
                    matching_results, user_persona
                ),
            }

        except PermissionError as e:
            return {"success": False, "error": str(e), "permission_denied": True}
        except Exception as e:
            logger.error(f"Error in chat entity analysis: {e}")
            return {
                "success": False,
                "error": str(e),
                "fallback_suggestion": "Try analyzing the document with a simpler approach",
            }

    def _extract_entities_with_chat_context(
        self, document_text: str, persona: str, domain: str, chat_history: Optional[List[Dict]]
    ) -> Dict[str, Any]:
        """Extract entities using chat service with persona and domain context."""

        # Build context-aware extraction prompt
        context_prompt = self._build_context_extraction_prompt(
            document_text, persona, domain, chat_history
        )

        # Use chat service. NOTE (M-03 root cause): this used to pass
        # template_name= / context_data=, but MultiDomainChatService.process_message
        # takes template= / context= — the TypeError from that mismatch fired on
        # every single call, which is why the Entity Matching Assistant errored on
        # every input in production even with an LLM provider configured.
        extraction_response = self.chat_service.process_message(
            message=context_prompt,
            domain=domain,
            template="entity_extraction",
            context={},
            persona=persona,
        )

        # process_message degrades to {"success": False, "response": "<human
        # message>", "error": "<raw>"} rather than raising (e.g. no LLM
        # provider configured, or the call itself failed). Left unchecked,
        # that human message gets treated as document text, fails JSON
        # parsing, and silently falls through to an empty-but-"success"
        # result — exactly the "0 matches, 0 new, no explanation" failure
        # mode the QA register flagged. Surface it honestly instead.
        if isinstance(extraction_response, dict) and extraction_response.get("permission_denied"):
            # A write the chat attempted was refused for lack of write access:
            # say so as a refusal, not as a failed extraction.
            raise PermissionError(extraction_response.get("response") or "Write access is required")
        if isinstance(extraction_response, dict) and extraction_response.get("success") is False:
            raise RuntimeError(
                extraction_response.get("error")
                or extraction_response.get("response")
                or "Entity extraction failed"
            )

        # Parse extraction response into structured entities
        return self._parse_extraction_response(extraction_response)

    def _build_context_extraction_prompt(
        self, document_text: str, persona: str, domain: str, chat_history: Optional[List[Dict]]
    ) -> str:
        """Build context-aware extraction prompt."""

        # Get persona-specific extraction priorities
        persona_additions = self.entity_matcher.get_persona_extraction_prompt_additions(persona)

        # Build chat context from history
        chat_context = ""
        if chat_history:
            recent_context = chat_history[-3:]  # Last 3 messages for context
            chat_context = "\n\nRECENT CHAT CONTEXT:\n"
            for msg in recent_context:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:200]  # Truncate for brevity
                chat_context += f"{role.upper()}: {content}...\n"

        return f"""
Analyze the following document and extract entities for enterprise architecture matching.

DOCUMENT TEXT:
{document_text}

{chat_context}

DOMAIN CONTEXT: {domain}
USER PERSONA: {persona}

{persona_additions}

Please extract and structure the following entities:
1. Application Components (name, description, technology stack, business domain)
2. Business Capabilities (name, level, description)
3. Vendor Organizations (name, products, strategic tier)
4. ArchiMate Elements (name, type, layer, description)
5. Relationships between entities (source, target, type)

Return results in structured JSON format with clear entity categorization.
"""

    def _parse_extraction_response(self, extraction_response: Dict) -> Dict[str, Any]:
        """Parse chat service extraction response into structured entities."""

        response_text = extraction_response.get("response", "")

        # Default structure if parsing fails
        default_entities = {
            "application_data": {},
            "vendor_data": {},
            "archimate_elements": [],
            "relationships": [],
            "business_capabilities": [],
        }

        try:
            # Try to extract JSON from response
            import json
            import re

            # Look for JSON blocks in response
            json_pattern = r"\{[\s\S]*\}"
            json_matches = re.findall(json_pattern, response_text)

            if json_matches:
                # Use the largest JSON block found
                largest_json = max(json_matches, key=len)
                parsed_entities = json.loads(largest_json)

                # Merge with default structure to ensure all keys exist
                return {**default_entities, **parsed_entities}

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to parse extraction JSON: {e}")

        # Fallback: basic text parsing
        return self._fallback_text_parsing(response_text, default_entities)

    def _fallback_text_parsing(self, text: str, default_entities: Dict) -> Dict[str, Any]:
        """Fallback parsing when JSON extraction fails. Returns empty entities rather than fabricated ones."""
        return default_entities.copy()

    def _match_all_entity_types(self, extracted_entities: Dict, persona: str) -> Dict[str, Any]:
        """Match all extracted entity types using the document matching service."""

        all_results = {
            "applications": {},
            "vendors": {},
            "capabilities": {},
            "archimate_elements": {},
            "summary": {},
        }

        # Match applications
        if extracted_entities.get("application_data"):
            all_results["applications"] = self.entity_matcher.match_extracted_entities(
                extracted_entities, "application", persona
            )

        # Match vendors
        if extracted_entities.get("vendor_data"):
            all_results["vendors"] = self.entity_matcher.match_extracted_entities(
                extracted_entities, "vendor", persona
            )

        # Match capabilities
        if extracted_entities.get("archimate_elements"):
            all_results["capabilities"] = self.entity_matcher.match_extracted_entities(
                extracted_entities, "capability", persona
            )

        # Match ArchiMate elements
        if extracted_entities.get("archimate_elements"):
            all_results["archimate_elements"] = self.entity_matcher.match_extracted_entities(
                extracted_entities, "archimate", persona
            )

        # Generate summary
        all_results["summary"] = self._generate_matching_summary(all_results)

        return all_results

    def _generate_matching_summary(self, all_results: Dict) -> Dict[str, Any]:
        """Generate summary statistics for all matching results."""

        summary = {
            "total_matches": 0,
            "total_duplicates": 0,
            "total_new_entities": 0,
            "total_relationship_suggestions": 0,
            "breakdown": {},
        }

        for entity_type, results in all_results.items():
            if isinstance(results, dict) and "matches" in results:
                matches_count = len(results.get("matches", []))
                duplicates_count = len(results.get("potential_duplicates", []))
                new_count = len(results.get("new_entities", []))
                relationships_count = len(results.get("relationship_suggestions", []))

                summary["total_matches"] += matches_count
                summary["total_duplicates"] += duplicates_count
                summary["total_new_entities"] += new_count
                summary["total_relationship_suggestions"] += relationships_count

                summary["breakdown"][entity_type] = {
                    "matches": matches_count,
                    "duplicates": duplicates_count,
                    "new_entities": new_count,
                    "relationships": relationships_count,
                }

        return summary

    def _generate_chat_insights(
        self, extracted_entities: Dict, matching_results: Dict, persona: str, domain: str
    ) -> Dict[str, Any]:
        """Generate chat-specific insights from matching results."""

        insights = {
            "persona_insights": {},
            "domain_insights": {},
            "actionable_insights": [],
            "conversation_starters": [],
        }

        # Get persona-specific insights
        if matching_results.get("applications", {}).get("persona_insights"):
            insights["persona_insights"] = matching_results["applications"]["persona_insights"]

        # Generate domain-specific insights
        insights["domain_insights"] = self._generate_domain_insights(
            extracted_entities, matching_results, domain
        )

        # Generate actionable insights
        insights["actionable_insights"] = self._generate_actionable_insights(
            matching_results, persona
        )

        # Generate conversation starters for user engagement
        insights["conversation_starters"] = self._generate_conversation_starters(
            matching_results, persona
        )

        return insights

    def _generate_domain_insights(
        self, extracted_entities: Dict, matching_results: Dict, domain: str
    ) -> Dict[str, Any]:
        """Generate domain-specific insights."""

        domain_insights = {
            "domain": domain,
            "focus_areas": [],
            "key_findings": [],
            "recommendations": [],
        }

        if domain == "architecture":
            domain_insights["focus_areas"] = [
                "Application Portfolio",
                "Capability Gaps",
                "Technology Stack",
            ]

            # Analyze application matches
            app_results = matching_results.get("applications", {})
            if app_results.get("matches"):
                domain_insights["key_findings"].append(
                    f"Found {len(app_results['matches'])} existing applications in portfolio"
                )

            if app_results.get("new_entities"):
                domain_insights["key_findings"].append(
                    f"Identified {len(app_results['new_entities'])} potential new applications"
                )

        elif domain == "technology":
            domain_insights["focus_areas"] = [
                "Technology Stack",
                "Vendor Landscape",
                "Integration Patterns",
            ]

            # Analyze vendor matches
            vendor_results = matching_results.get("vendors", {})
            if vendor_results.get("matches"):
                domain_insights["key_findings"].append(
                    f"Matched with {len(vendor_results['matches'])} known vendors"
                )

        return domain_insights

    def _generate_actionable_insights(self, matching_results: Dict, persona: str) -> List[str]:
        """Generate actionable insights based on matching results."""

        insights = []

        # Analyze summary statistics
        summary = matching_results.get("summary", {})

        if summary.get("total_new_entities", 0) > 5:
            insights.append(
                f"Consider batch importing {summary['total_new_entities']} new entities "
                "to maintain portfolio consistency"
            )

        if summary.get("total_duplicates", 0) > 3:
            insights.append(
                f"Review {summary['total_duplicates']} potential duplicates "
                "to avoid data redundancy"
            )

        if summary.get("total_relationship_suggestions", 0) > 0:
            insights.append(
                f"Explore {summary['total_relationship_suggestions']} relationship suggestions "
                "to enhance architecture connectivity"
            )

        # Add persona-specific insights
        if persona == "enterprise_architect":
            insights.append("Review strategic alignment of new applications with capability map")
        elif persona == "solutions_architect":
            insights.append("Analyze integration patterns for proposed solutions")
        elif persona == "business_analyst":
            insights.append("Validate business requirements coverage for new capabilities")

        return insights

    def _generate_conversation_starters(self, matching_results: Dict, persona: str) -> List[str]:
        """Generate conversation starters to engage user."""

        starters = []

        summary = matching_results.get("summary", {})

        if summary.get("total_matches", 0) > 0:
            starters.append(
                f"I found {summary['total_matches']} existing entities that match your document. "
                "Would you like to review the high-confidence matches?"
            )

        if summary.get("total_new_entities", 0) > 0:
            starters.append(
                f"Your document contains {summary['total_new_entities']} potentially new entities. "
                "Should we discuss how to integrate them into your architecture?"
            )

        if summary.get("total_duplicates", 0) > 0:
            starters.append(
                f"I detected {summary['total_duplicates']} possible duplicates. "
                "Would you like me to help you resolve these potential conflicts?"
            )

        # Add persona-specific conversation starters
        if persona == "enterprise_architect":
            starters.append(
                "Based on the analysis, how do these findings align with your strategic portfolio goals?"
            )
        elif persona == "solutions_architect":
            starters.append(
                "Would you like to explore the integration implications of these findings?"
            )

        return starters

    def _format_for_chat_ui(
        self, extracted_entities: Dict, matching_results: Dict, chat_insights: Dict
    ) -> Dict[str, Any]:
        """Format results for optimal chat UI display."""

        ui_data = {
            "overview": {
                "total_entities_processed": self._count_entities_by_type(extracted_entities)[
                    "total"
                ],
                "matching_summary": matching_results.get("summary", {}),
                "key_metrics": self._calculate_key_metrics(matching_results),
            },
            "entity_sections": self._build_entity_sections(matching_results),
            "interactive_elements": self._build_interactive_elements(matching_results),
            "visual_data": self._prepare_visual_data(matching_results),
            "actions": self._build_action_buttons(matching_results),
        }

        return ui_data

    def _count_entities_by_type(self, extracted_entities: Dict) -> Dict[str, int]:
        """Count extracted entities by type."""

        counts = {
            "applications": 0,
            "vendors": 0,
            "capabilities": 0,
            "archimate_elements": 0,
            "total": 0,
        }

        if extracted_entities.get("application_data", {}).get("name"):
            counts["applications"] = 1

        if extracted_entities.get("vendor_data", {}).get("name"):
            counts["vendors"] = 1

        counts["archimate_elements"] = len(extracted_entities.get("archimate_elements", []))

        # Count capabilities in archimate elements
        capability_elements = [
            e
            for e in extracted_entities.get("archimate_elements", [])
            if e.get("type") in ["BusinessCapability", "Capability"]
        ]
        counts["capabilities"] = len(capability_elements)

        counts["total"] = sum(counts.values())

        return counts

    def _calculate_key_metrics(self, matching_results: Dict) -> Dict[str, Any]:
        """Calculate key metrics for dashboard display."""

        summary = matching_results.get("summary", {})

        return {
            "match_rate": self._calculate_match_rate(summary),
            "duplicate_rate": self._calculate_duplicate_rate(summary),
            "coverage_score": self._calculate_coverage_score(matching_results),
            "relationship_potential": summary.get("total_relationship_suggestions", 0),
        }

    def _calculate_match_rate(self, summary: Dict) -> float:
        """Calculate entity match rate as percentage."""
        total_entities = summary.get("total_matches", 0) + summary.get("total_new_entities", 0)
        if total_entities == 0:
            return 0.0
        return round((summary.get("total_matches", 0) / total_entities) * 100, 1)

    def _calculate_duplicate_rate(self, summary: Dict) -> float:
        """Calculate duplicate detection rate as percentage."""
        total_processed = (
            summary.get("total_matches", 0)
            + summary.get("total_duplicates", 0)
            + summary.get("total_new_entities", 0)
        )
        if total_processed == 0:
            return 0.0
        return round((summary.get("total_duplicates", 0) / total_processed) * 100, 1)

    def _calculate_coverage_score(self, matching_results: Dict) -> float:
        """Calculate portfolio coverage score."""
        # This would be enhanced with actual portfolio size data
        summary = matching_results.get("summary", {})
        base_score = min(summary.get("total_matches", 0) * 10, 100)
        return round(base_score, 1)

    def _build_entity_sections(self, matching_results: Dict) -> List[Dict]:
        """Build entity sections for UI display."""

        sections = []

        for entity_type, results in matching_results.items():
            if entity_type == "summary" or not isinstance(results, dict):
                continue

            section = {
                "type": entity_type,
                "title": entity_type.title(),
                "matches": results.get("matches", [])[:3],  # Limit for display
                "duplicates": results.get("potential_duplicates", [])[:3],
                "new_entities": results.get("new_entities", [])[:3],
                "total_counts": {
                    "matches": len(results.get("matches", [])),
                    "duplicates": len(results.get("potential_duplicates", [])),
                    "new_entities": len(results.get("new_entities", [])),
                },
            }
            sections.append(section)

        return sections

    def _build_interactive_elements(self, matching_results: Dict) -> Dict[str, Any]:
        """Build interactive UI elements."""

        return {
            "expandable_sections": [
                "applications",
                "vendors",
                "capabilities",
                "archimate_elements",
            ],
            "filterable_fields": ["match_confidence", "entity_type", "match_level"],
            "sortable_columns": ["match_confidence", "entity_name", "match_level"],
            "actionable_items": self._get_actionable_items(matching_results),
        }

    def _get_actionable_items(self, matching_results: Dict) -> List[Dict]:
        """Get items that require user action."""

        actionable = []

        for entity_type, results in matching_results.items():
            if entity_type == "summary" or not isinstance(results, dict):
                continue

            # High-confidence matches for review
            for match in results.get("matches", []):
                if match.get("match_confidence", 0) >= 85:
                    actionable.append(
                        {
                            "type": "high_confidence_match",
                            "entity_type": entity_type,
                            "entity_name": match.get("entity_name", ""),
                            "action": "review_match",
                            "priority": "medium",
                        }
                    )

            # Potential duplicates for resolution
            for duplicate in results.get("potential_duplicates", []):
                if duplicate.get("match_confidence", 0) >= 70:
                    actionable.append(
                        {
                            "type": "potential_duplicate",
                            "entity_type": entity_type,
                            "entity_name": duplicate.get("entity_name", ""),
                            "action": "resolve_duplicate",
                            "priority": "high",
                        }
                    )

        return actionable[:10]  # Limit for display

    def _prepare_visual_data(self, matching_results: Dict) -> Dict[str, Any]:
        """Prepare data for visual components."""

        summary = matching_results.get("summary", {})
        breakdown = summary.get("breakdown", {})

        return {
            "chart_data": {
                "entity_distribution": self._build_entity_distribution_chart(breakdown),
                "match_confidence_distribution": self._build_confidence_distribution(
                    matching_results
                ),
                "relationship_types": self._build_relationship_types_chart(matching_results),
            },
            "progress_indicators": {
                "matching_progress": self._calculate_match_rate(summary),
                "duplicate_review_progress": 0,  # Would be calculated from user actions
                "integration_progress": 0,  # Would be calculated from user actions
            },
        }

    def _build_entity_distribution_chart(self, breakdown: Dict) -> Dict[str, Any]:
        """Build entity distribution chart data."""

        labels = []
        data = []

        for entity_type, counts in breakdown.items():
            labels.append(entity_type.title())
            data.append(counts.get("matches", 0) + counts.get("new_entities", 0))

        return {
            "type": "pie",
            "labels": labels,
            "datasets": [{"data": data, "backgroundColor": self._get_chart_colors(len(labels))}],
        }

    def _build_confidence_distribution(self, matching_results: Dict) -> Dict[str, Any]:
        """Build confidence distribution chart data."""

        confidence_ranges = {"high": 0, "medium": 0, "low": 0}

        for entity_type, results in matching_results.items():
            if entity_type == "summary" or not isinstance(results, dict):
                continue

            for match in results.get("matches", []):
                level = match.get("match_level", "low")
                confidence_ranges[level] = confidence_ranges.get(level, 0) + 1

        return {
            "type": "bar",
            "labels": ["High Confidence", "Medium Confidence", "Low Confidence"],
            "datasets": [
                {
                    "data": [
                        confidence_ranges["high"],
                        confidence_ranges["medium"],
                        confidence_ranges["low"],
                    ],
                    "backgroundColor": ["#10b981", "#f59e0b", "#ef4444"],
                }
            ],
        }

    def _build_relationship_types_chart(self, matching_results: Dict) -> Dict[str, Any]:
        """Build relationship types chart data."""

        relationship_types = {}

        for entity_type, results in matching_results.items():
            if entity_type == "summary" or not isinstance(results, dict):
                continue

            for rel in results.get("relationship_suggestions", []):
                rel_type = rel.get("relationship_type", "Unknown")
                relationship_types[rel_type] = relationship_types.get(rel_type, 0) + 1

        return {
            "type": "doughnut",
            "labels": list(relationship_types.keys()),
            "datasets": [
                {
                    "data": list(relationship_types.values()),
                    "backgroundColor": self._get_chart_colors(len(relationship_types)),
                }
            ],
        }

    def _get_chart_colors(self, count: int) -> List[str]:
        """Get chart colors based on count."""
        base_colors = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4"]
        return base_colors[:count] + ["#6b7280"] * max(0, count - len(base_colors))

    def _build_action_buttons(self, matching_results: Dict) -> List[Dict]:
        """Build action buttons for user interaction."""

        actions = []
        summary = matching_results.get("summary", {})

        # Basic actions
        actions.extend(
            [
                {
                    "id": "review_all_matches",
                    "label": "Review All Matches",
                    "action": "navigate_to_matches",
                    "variant": "primary",
                    "enabled": summary.get("total_matches", 0) > 0,
                },
                {
                    "id": "resolve_duplicates",
                    "label": f'Resolve Duplicates ({summary.get("total_duplicates", 0)})',
                    "action": "navigate_to_duplicates",
                    "variant": "secondary",
                    "enabled": summary.get("total_duplicates", 0) > 0,
                },
                {
                    "id": "import_new_entities",
                    "label": f'Import New Entities ({summary.get("total_new_entities", 0)})',
                    "action": "navigate_to_import",
                    "variant": "success",
                    "enabled": summary.get("total_new_entities", 0) > 0,
                },
            ]
        )

        # Advanced actions
        if summary.get("total_relationship_suggestions", 0) > 0:
            actions.append(
                {
                    "id": "explore_relationships",
                    "label": f'Explore Relationships ({summary.get("total_relationship_suggestions", 0)})',
                    "action": "navigate_to_relationships",
                    "variant": "outline",
                    "enabled": True,
                }
            )

        return actions

    def _generate_action_recommendations(self, matching_results: Dict, persona: str) -> List[Dict]:
        """Generate prioritized action recommendations."""

        recommendations = []
        summary = matching_results.get("summary", {})

        # High-priority recommendations
        if summary.get("total_duplicates", 0) > 0:
            recommendations.append(
                {
                    "priority": "high",
                    "title": "Resolve Potential Duplicates",
                    "description": f"Found {summary['total_duplicates']} potential duplicates that need review",
                    "action": "review_duplicates",
                    "estimated_time": "15 - 30 minutes",
                }
            )

        if summary.get("total_new_entities", 0) > 5:
            recommendations.append(
                {
                    "priority": "medium",
                    "title": "Batch Import New Entities",
                    "description": f"Consider batch importing {summary['total_new_entities']} new entities",
                    "action": "batch_import",
                    "estimated_time": "30 - 45 minutes",
                }
            )

        # Persona-specific recommendations
        if persona == "enterprise_architect":
            recommendations.append(
                {
                    "priority": "medium",
                    "title": "Strategic Portfolio Review",
                    "description": "Review how new entities align with strategic capabilities",
                    "action": "strategic_review",
                    "estimated_time": "45 - 60 minutes",
                }
            )
        elif persona == "solutions_architect":
            recommendations.append(
                {
                    "priority": "medium",
                    "title": "Integration Analysis",
                    "description": "Analyze integration patterns and dependencies",
                    "action": "integration_analysis",
                    "estimated_time": "30 - 45 minutes",
                }
            )

        return recommendations[:5]  # Limit for display

    def get_chat_response_suggestions(
        self, matching_results: Dict, user_question: str
    ) -> List[str]:
        """Generate contextual chat response suggestions based on matching results."""

        suggestions = []
        summary = matching_results.get("summary", {})

        # Analyze user question intent
        question_lower = user_question.lower()

        if "match" in question_lower or "found" in question_lower:
            if summary.get("total_matches", 0) > 0:
                suggestions.append(
                    f"I found {summary['total_matches']} matches in your existing portfolio. "
                    "Would you like me to show you the highest confidence matches first?"
                )
            else:
                suggestions.append(
                    "I didn't find any direct matches in your existing portfolio. "
                    "Would you like to see the new entities I identified?"
                )

        if "duplicate" in question_lower:
            if summary.get("total_duplicates", 0) > 0:
                suggestions.append(
                    f"I detected {summary['total_duplicates']} potential duplicates. "
                    "Should we review them together to determine which ones to keep?"
                )
            else:
                suggestions.append(
                    "Good news! I didn't detect any potential duplicates in your document."
                )

        if "new" in question_lower or "add" in question_lower:
            if summary.get("total_new_entities", 0) > 0:
                suggestions.append(
                    f"Your document contains {summary['total_new_entities']} entities that aren't in your portfolio. "
                    "Would you like to discuss how to integrate them?"
                )

        if "relationship" in question_lower or "connect" in question_lower:
            if summary.get("total_relationship_suggestions", 0) > 0:
                suggestions.append(
                    f"I identified {summary['total_relationship_suggestions']} potential relationships. "
                    "Would you like to explore how these entities connect to your existing architecture?"
                )

        # Default suggestions if no specific intent detected
        if not suggestions:
            suggestions.extend(
                [
                    "Would you like me to walk you through the key findings from your document analysis?",
                    "I can help you understand how these entities fit into your existing portfolio.",
                    "Should we focus on the matches, duplicates, or new entities first?",
                ]
            )

        return suggestions[:3]  # Return top 3 suggestions

    def get_available_entity_types(self) -> List[Dict[str, Any]]:
        """Get available entity types for matching."""
        return [
            {
                "id": "application_component",
                "name": "Application Component",
                "layer": "Application",
            },
            {"id": "application_service", "name": "Application Service", "layer": "Application"},
            {"id": "business_process", "name": "Business Process", "layer": "Business"},
            {"id": "business_capability", "name": "Business Capability", "layer": "Business"},
            {"id": "technology_service", "name": "Technology Service", "layer": "Technology"},
            {"id": "node", "name": "Node", "layer": "Technology"},
            {"id": "data_object", "name": "Data Object", "layer": "Application"},
            {"id": "artifact", "name": "Artifact", "layer": "Technology"},
            {"id": "actor", "name": "Actor", "layer": "Business"},
            {"id": "role", "name": "Role", "layer": "Business"},
        ]

    def match_entities(
        self, text: str, entity_types: List[str] = None, context: Dict = None
    ) -> Dict[str, Any]:
        """Match text against existing entities in the database."""
        try:
            from app.models.archimate_core import ApplicationComponent

            results = []
            query = text.lower()

            # Search application components
            components = (
                ApplicationComponent.query.filter(ApplicationComponent.name.ilike(f"%{query}%"))
                .limit(10)
                .all()
            )

            for comp in components:
                results.append(
                    {
                        "id": comp.id,
                        "name": comp.name,
                        "type": "Application Component",
                        "confidence": 0.8 if query in comp.name.lower() else 0.5,
                        "match_type": "exact" if query == comp.name.lower() else "partial",
                    }
                )

            return {"success": True, "matches": results, "total": len(results), "query": text}
        except Exception as e:
            return {"success": False, "error": str(e), "matches": [], "total": 0}
