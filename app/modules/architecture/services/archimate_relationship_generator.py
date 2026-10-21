"""
-> app.modules.architecture.services.modeling_service

ArchiMate Relationship Generator
Generates 80 - 150 relationships across all layers using 3 - pass algorithm
"""

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List

from app.services.llm_service import LLMService
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)


class ArchiMateRelationshipGenerator:
    """Generate comprehensive relationships between ArchiMate elements."""

    # ArchiMate 3.2 relationship type rules
    RELATIONSHIP_RULES = {
        "Association": {
            "description": "Unspecified relationship",
            "valid_sources": ["*"],
            "valid_targets": ["*"],
        },
        "Access": {
            "description": "Data access",
            "valid_sources": ["BusinessProcess", "ApplicationComponent"],
            "valid_targets": ["DataObject", "BusinessObject"],
        },
        "Assignment": {
            "description": "Allocates responsibility",
            "valid_sources": ["BusinessActor", "BusinessRole"],
            "valid_targets": ["BusinessProcess", "ApplicationService"],
        },
        "Composition": {
            "description": "Part-of relationship",
            "valid_sources": ["*"],
            "valid_targets": ["*"],
        },
        "Flow": {
            "description": "Transfer of information",
            "valid_sources": ["BusinessProcess"],
            "valid_targets": ["BusinessProcess"],
        },
        "Influence": {
            "description": "Impact relationship",
            "valid_sources": ["Driver", "Goal"],
            "valid_targets": ["Goal", "Requirement", "Capability"],
        },
        "Realization": {
            "description": "Implementation",
            "valid_sources": ["BusinessProcess", "ApplicationComponent", "Capability"],
            "valid_targets": ["BusinessService", "ApplicationService", "Goal"],
        },
        "Serving": {
            "description": "Provides functionality",
            "valid_sources": ["ApplicationComponent", "ApplicationService"],
            "valid_targets": ["BusinessProcess", "ApplicationComponent"],
        },
        "Triggering": {
            "description": "Temporal dependency",
            "valid_sources": ["BusinessProcess", "ApplicationService"],
            "valid_targets": ["BusinessProcess", "ApplicationService"],
        },
    }

    def __init__(self, llm_service=None):
        # LLMService is static, no need to store instance
        pass

    def generate_relationships(
        self, all_elements: List[Dict[str, Any]], app_name: str
    ) -> List[Dict[str, Any]]:
        """Generate 80 - 150 relationships using 3 - pass algorithm."""
        try:
            logger.info(f"🔗 Starting relationship generation for {app_name}...")
            elements_by_layer = self._organize_by_layer(all_elements)
            relationships = []

            # Pass 1: Intra-layer
            intra = self._generate_intra_layer_relationships(elements_by_layer)
            relationships.extend(intra)
            logger.info(f"✅ Pass 1: {len(intra)} intra-layer relationships")

            # Pass 2: Cross-layer
            cross = self._generate_cross_layer_relationships(elements_by_layer, app_name)
            relationships.extend(cross)
            logger.info(f"✅ Pass 2: {len(cross)} cross-layer relationships")

            # Pass 3: Semantic
            semantic = self._generate_semantic_relationships(all_elements, app_name)
            relationships.extend(semantic)
            logger.info(f"✅ Pass 3: {len(semantic)} semantic relationships")

            unique = self._deduplicate_relationships(relationships)
            logger.info(f"🎯 Total relationships: {len(unique)}")
            return unique

        except Exception as e:
            logger.error(f"❌ Relationship generation error: {e}")
            return []

    def _organize_by_layer(self, elements: List[Dict]) -> Dict[str, List[Dict]]:
        """Organize elements by layer."""
        by_layer = defaultdict(list)
        for elem in elements:
            by_layer[elem.get("layer", "unknown")].append(elem)
        return dict(by_layer)

    def _generate_intra_layer_relationships(self, elements_by_layer: Dict) -> List[Dict]:
        """Generate relationships within each layer."""
        relationships = []
        if "motivation" in elements_by_layer:
            relationships.extend(self._gen_motivation_rels(elements_by_layer["motivation"]))
        if "strategy" in elements_by_layer:
            relationships.extend(self._gen_strategy_rels(elements_by_layer["strategy"]))
        if "business" in elements_by_layer:
            relationships.extend(self._gen_business_rels(elements_by_layer["business"]))
        if "application" in elements_by_layer:
            relationships.extend(self._gen_application_rels(elements_by_layer["application"]))
        if "technology" in elements_by_layer:
            relationships.extend(self._gen_technology_rels(elements_by_layer["technology"]))
        return relationships

    def _gen_motivation_rels(self, elements: List[Dict]) -> List[Dict]:
        """Motivation layer relationships."""
        rels = []
        drivers = [e for e in elements if e.get("type") == "Driver"]
        goals = [e for e in elements if e.get("type") == "Goal"]
        reqs = [e for e in elements if e.get("type") == "Requirement"]

        for i, driver in enumerate(drivers):
            if i < len(goals):
                rels.append(
                    {
                        "source": driver["name"],
                        "target": goals[i]["name"],
                        "type": "Influence",
                        "description": f"{driver['name']} drives {goals[i]['name']}",
                    }
                )

        for i, goal in enumerate(goals):
            for req in reqs[i * 2 : min((i + 1) * 2, len(reqs))]:
                rels.append(
                    {
                        "source": goal["name"],
                        "target": req["name"],
                        "type": "Influence",
                        "description": f"{goal['name']} requires {req['name']}",
                    }
                )

        return rels

    def _gen_strategy_rels(self, elements: List[Dict]) -> List[Dict]:
        """Strategy layer relationships."""
        rels = []
        caps = [e for e in elements if e.get("type") == "Capability"]
        vs = [e for e in elements if e.get("type") == "ValueStream"]

        for i, stream in enumerate(vs):
            if i < len(caps):
                rels.append(
                    {
                        "source": stream["name"],
                        "target": caps[i]["name"],
                        "type": "Realization",
                        "description": f"{stream['name']} realizes {caps[i]['name']}",
                    }
                )

        return rels

    def _gen_business_rels(self, elements: List[Dict]) -> List[Dict]:
        """Business layer relationships."""
        rels = []
        actors = [e for e in elements if e.get("type") in ["BusinessActor", "BusinessRole"]]
        procs = [e for e in elements if e.get("type") == "BusinessProcess"]
        objs = [e for e in elements if e.get("type") == "BusinessObject"]

        for i, actor in enumerate(actors):
            if i < len(procs):
                rels.append(
                    {
                        "source": actor["name"],
                        "target": procs[i]["name"],
                        "type": "Assignment",
                        "description": f"{actor['name']} performs {procs[i]['name']}",
                    }
                )

        for i in range(len(procs) - 1):
            rels.append(
                {
                    "source": procs[i]["name"],
                    "target": procs[i + 1]["name"],
                    "type": "Triggering",
                    "description": f"{procs[i]['name']} triggers {procs[i + 1]['name']}",
                }
            )

        for i, proc in enumerate(procs):
            if i < len(objs):
                rels.append(
                    {
                        "source": proc["name"],
                        "target": objs[i]["name"],
                        "type": "Access",
                        "description": f"{proc['name']} accesses {objs[i]['name']}",
                    }
                )

        return rels

    def _gen_application_rels(self, elements: List[Dict]) -> List[Dict]:
        """Application layer relationships."""
        rels = []
        comps = [e for e in elements if is_application_kind(e.get("type"))]
        svcs = [e for e in elements if e.get("type") == "ApplicationService"]
        data = [e for e in elements if e.get("type") == "DataObject"]

        for i, svc in enumerate(svcs):
            if i < len(comps):
                rels.append(
                    {
                        "source": comps[i]["name"],
                        "target": svc["name"],
                        "type": "Realization",
                        "description": f"{comps[i]['name']} provides {svc['name']}",
                    }
                )

        for i, comp in enumerate(comps):
            if i < len(data):
                rels.append(
                    {
                        "source": comp["name"],
                        "target": data[i]["name"],
                        "type": "Access",
                        "description": f"{comp['name']} accesses {data[i]['name']}",
                    }
                )

        return rels

    def _gen_technology_rels(self, elements: List[Dict]) -> List[Dict]:
        """Technology layer relationships."""
        rels = []
        nodes = [e for e in elements if e.get("type") == "Node"]
        software = [e for e in elements if e.get("type") == "SystemSoftware"]

        for i, sw in enumerate(software):
            if i < len(nodes):
                rels.append(
                    {
                        "source": sw["name"],
                        "target": nodes[i]["name"],
                        "type": "Assignment",
                        "description": f"{sw['name']} deployed on {nodes[i]['name']}",
                    }
                )

        return rels

    def _generate_cross_layer_relationships(
        self, elements_by_layer: Dict, app_name: str
    ) -> List[Dict]:
        """Generate vertical traceability relationships."""
        rels = []

        # Motivation -> Strategy
        if "motivation" in elements_by_layer and "strategy" in elements_by_layer:
            goals = [e for e in elements_by_layer["motivation"] if e.get("type") == "Goal"]
            caps = [e for e in elements_by_layer["strategy"] if e.get("type") == "Capability"]
            for i, goal in enumerate(goals):
                if i < len(caps):
                    rels.append(
                        {
                            "source": caps[i]["name"],
                            "target": goal["name"],
                            "type": "Realization",
                            "description": f"{caps[i]['name']} realizes {goal['name']}",
                        }
                    )

        # Strategy -> Business
        if "strategy" in elements_by_layer and "business" in elements_by_layer:
            caps = [e for e in elements_by_layer["strategy"] if e.get("type") == "Capability"]
            procs = [e for e in elements_by_layer["business"] if e.get("type") == "BusinessProcess"]
            for i, cap in enumerate(caps):
                if i < len(procs):
                    rels.append(
                        {
                            "source": procs[i]["name"],
                            "target": cap["name"],
                            "type": "Realization",
                            "description": f"{procs[i]['name']} realizes {cap['name']}",
                        }
                    )

        # Business -> Application
        if "business" in elements_by_layer and "application" in elements_by_layer:
            procs = [e for e in elements_by_layer["business"] if e.get("type") == "BusinessProcess"]
            comps = [
                e
                for e in elements_by_layer["application"]
                if is_application_kind(e.get("type"))
            ]
            for i, proc in enumerate(procs):
                if i < len(comps):
                    rels.append(
                        {
                            "source": comps[i]["name"],
                            "target": proc["name"],
                            "type": "Serving",
                            "description": f"{comps[i]['name']} supports {proc['name']}",
                        }
                    )

        # Application -> Technology
        if "application" in elements_by_layer and "technology" in elements_by_layer:
            comps = [
                e
                for e in elements_by_layer["application"]
                if is_application_kind(e.get("type"))
            ]
            nodes = [e for e in elements_by_layer["technology"] if e.get("type") == "Node"]
            for i, comp in enumerate(comps):
                if i < len(nodes):
                    rels.append(
                        {
                            "source": comp["name"],
                            "target": nodes[i]["name"],
                            "type": "Assignment",
                            "description": f"{comp['name']} deployed on {nodes[i]['name']}",
                        }
                    )

        return rels

    def _generate_semantic_relationships(
        self, all_elements: List[Dict], app_name: str
    ) -> List[Dict]:
        """Use LLM to generate semantic relationships based on element names/descriptions."""
        try:
            elem_summary = "\n".join(
                [
                    f"- {e['name']} ({e['type']}, {e.get('layer', 'unknown')})"
                    for e in all_elements[:30]
                ]
            )

            prompt = f"""
Analyze these ArchiMate elements and suggest 10 - 15 additional meaningful relationships:

{elem_summary}

Generate relationships that make semantic sense based on element names. Return ONLY valid JSON:
{{"relationships": [{{"source": "element_name", "target": "element_name", "type": "Serving|Access|Realization|Triggering", "description": "why"}}]}}

Valid relationship types: Serving, Access, Realization, Triggering, Assignment, Influence
"""

            response = LLMService.generate_from_prompt(prompt)
            return self._parse_relationship_response(response)

        except Exception as e:
            logger.error(f"Semantic relationship generation error: {e}")
            return []

    def _parse_relationship_response(self, response: str) -> List[Dict]:
        """Parse LLM response for relationships."""
        try:
            cleaned = response.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0]
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0]

            result = json.loads(cleaned.strip())
            return result.get("relationships", [])
        except Exception:
            logger.debug("Failed to parse relationship suggestions", exc_info=True)
            return []

    def _deduplicate_relationships(self, relationships: List[Dict]) -> List[Dict]:
        """Remove duplicate relationships."""
        seen = set()
        unique = []
        for rel in relationships:
            key = (rel["source"], rel["target"], rel["type"])
            if key not in seen:
                seen.add(key)
                unique.append(rel)
        return unique
