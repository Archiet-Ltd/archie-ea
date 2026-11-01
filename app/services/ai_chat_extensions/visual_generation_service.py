"""
Visual Generation Service

Generates visual representations from AI Chat queries including:
- ArchiMate diagrams (Mermaid/PlantUML format)
- Capability heat maps
- Dependency graphs
- Roadmap timelines
- Portfolio matrices
"""

import logging
from datetime import datetime
from html import escape
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


class VisualGenerationService:
    """Service for generating visual diagrams and charts from enterprise data."""

    # Supported visualization types
    VISUALIZATION_TYPES = {
        "archimate_diagram": {
            "name": "ArchiMate Diagram",
            "description": "Generate ArchiMate 3.2 compliant diagrams",
            "output_formats": ["mermaid", "plantuml", "svg_data"],
        },
        "capability_heatmap": {
            "name": "Capability Heat Map",
            "description": "Visual heat map of capability maturity/coverage",
            "output_formats": ["html_grid", "chart_data"],
        },
        "dependency_graph": {
            "name": "Dependency Graph",
            "description": "Application and system dependency visualization",
            "output_formats": ["mermaid", "d3_data", "cytoscape"],
        },
        "roadmap_timeline": {
            "name": "Roadmap Timeline",
            "description": "Implementation roadmap with milestones",
            "output_formats": ["gantt_mermaid", "timeline_data"],
        },
        "portfolio_matrix": {
            "name": "Portfolio Matrix",
            "description": "2x2 or quadrant analysis matrices",
            "output_formats": ["chart_data", "html_grid"],
        },
        "flow_diagram": {
            "name": "Flow Diagram",
            "description": "Process and data flow visualizations",
            "output_formats": ["mermaid", "plantuml"],
        },
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def get_available_visualizations(self) -> Dict[str, Any]:
        """Return available visualization types and their capabilities."""
        return {"visualizations": self.VISUALIZATION_TYPES, "count": len(self.VISUALIZATION_TYPES)}

    def generate_visualization(
        self,
        viz_type: str,
        context: Dict[str, Any],
        output_format: str = None,
        options: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Generate a visualization based on type and context.

        Args:
            viz_type: Type of visualization to generate
            context: Data context for the visualization
            output_format: Desired output format
            options: Additional options for customization

        Returns:
            Visualization data with rendering instructions
        """
        if viz_type not in self.VISUALIZATION_TYPES:
            return {
                "success": False,
                "error": f"Unknown visualization type: {viz_type}",
                "available_types": list(self.VISUALIZATION_TYPES.keys()),
            }

        options = options or {}

        try:
            if viz_type == "archimate_diagram":
                return self._generate_archimate_diagram(context, output_format, options)
            elif viz_type == "capability_heatmap":
                return self._generate_capability_heatmap(context, output_format, options)
            elif viz_type == "dependency_graph":
                return self._generate_dependency_graph(context, output_format, options)
            elif viz_type == "roadmap_timeline":
                return self._generate_roadmap_timeline(context, output_format, options)
            elif viz_type == "portfolio_matrix":
                return self._generate_portfolio_matrix(context, output_format, options)
            elif viz_type == "flow_diagram":
                return self._generate_flow_diagram(context, output_format, options)
            else:
                return {"success": False, "error": "Visualization type not implemented"}

        except Exception as e:
            self.logger.error(f"Error generating visualization: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _generate_archimate_diagram(
        self, context: Dict[str, Any], output_format: str, options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate ArchiMate diagram in Mermaid or PlantUML format."""
        elements = context.get("elements", [])
        relationships = context.get("relationships", [])
        title = options.get("title", "ArchiMate Diagram")
        layer_filter = options.get("layer", None)

        # If no elements provided, try to load from database
        if not elements:
            elements = self._load_archimate_elements(layer_filter, limit=20)

        # Generate Mermaid diagram
        mermaid_code = self._elements_to_mermaid(elements, relationships, title)

        # Generate PlantUML if requested
        plantuml_code = None
        if output_format == "plantuml":
            plantuml_code = self._elements_to_plantuml(elements, relationships, title)

        return {
            "success": True,
            "type": "archimate_diagram",
            "title": title,
            "mermaid": mermaid_code,
            "plantuml": plantuml_code,
            "element_count": len(elements),
            "relationship_count": len(relationships),
            "render_instructions": {
                "type": "mermaid",
                "container_class": "mermaid-diagram",
                "theme": options.get("theme", "default"),
            },
        }

    def _elements_to_mermaid(
        self, elements: List[Dict], relationships: List[Dict], title: str
    ) -> str:
        """Convert ArchiMate elements to Mermaid flowchart."""
        lines = ["flowchart TB"]
        lines.append(f'    subgraph {title.replace(" ", "_")}["{title}"]')

        # Group by layer
        layers = {"Business": [], "Application": [], "Technology": []}
        for el in elements:
            layer = el.get("layer", "Application")
            if layer in layers:
                layers[layer].append(el)

        # Add layer subgraphs
        layer_styles = {
            "Business": "fill:#ffffb5,stroke:#c9b922",
            "Application": "fill:#b5ffff,stroke:#22c9c9",
            "Technology": "fill:#c9e7b5,stroke:#6bb522",
        }

        for layer_name, layer_elements in layers.items():
            if layer_elements:
                lines.append(f'        subgraph {layer_name}Layer["{layer_name} Layer"]')
                for el in layer_elements:
                    el_id = f"el_{el.get('id', 'unknown')}"
                    # Mermaid renders HTML inside node labels by default
                    # (that's why <br/>/<small> below work at all) -- so
                    # el_name/el_type (architect-authored, from an
                    # AI-chat-reachable diagram request) must be escaped or
                    # they inject live HTML into whatever renders this
                    # Mermaid diagram client-side.
                    el_name = escape(el.get("name", "Unknown"))
                    el_type = escape(el.get("type", "Component"))
                    lines.append(f'            {el_id}["{el_name}<br/><small>{el_type}</small>"]')  # raw-html-ok: el_name/el_type are escape()'d 3 lines above
                lines.append("        end")

        lines.append("    end")

        # Add relationships
        for rel in relationships:
            source = f"el_{rel.get('source_id', 'unknown')}"
            target = f"el_{rel.get('target_id', 'unknown')}"
            rel_type = rel.get("type", "Association")

            arrow = (
                "-->"
                if rel_type in ["Composition", "Aggregation", "Assignment"]
                else "-.->|{rel_type}|"
            )
            lines.append(f"    {source} {arrow} {target}")

        # Add styles
        for layer_name, style in layer_styles.items():
            lines.append(f"    style {layer_name}Layer {style}")

        return "\n".join(lines)

    def _elements_to_plantuml(
        self, elements: List[Dict], relationships: List[Dict], title: str
    ) -> str:
        """Convert ArchiMate elements to PlantUML."""
        lines = ["@startuml", f"title {title}", ""]

        # ArchiMate sprites would be defined here
        lines.append("' ArchiMate styling")
        lines.append("skinparam backgroundColor #FEFEFE")
        lines.append("")

        for el in elements:
            el_id = f"el_{el.get('id', 'unknown')}"
            # PlantUML source text sent to an external rendering server, not
            # HTML a browser interprets -- <size:10>...</size> here is
            # PlantUML's own creole markup, not an HTML tag, so html.escape()
            # would corrupt the diagram rather than protect anything. The
            # real risk in this format is a literal '"' in the name breaking
            # out of the quoted rectangle label and corrupting the diagram
            # syntax -- close that instead.
            el_name = el.get("name", "Unknown").replace('"', "'")
            el_type = el.get("type", "Component").replace('"', "'")
            lines.append(f'rectangle "{el_name}\\n<size:10>{el_type}</size>" as {el_id}')  # raw-html-ok: PlantUML source for an external renderer, not browser HTML; el_name/el_type quote-sanitized above (see comment)

        lines.append("")

        for rel in relationships:
            source = f"el_{rel.get('source_id', 'unknown')}"
            target = f"el_{rel.get('target_id', 'unknown')}"
            lines.append(f"{source} --> {target}")

        lines.append("@enduml")
        return "\n".join(lines)

    def _generate_capability_heatmap(
        self, context: Dict[str, Any], output_format: str, options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate capability heat map visualization."""
        capabilities = context.get("capabilities", [])
        metric = options.get("metric", "maturity")  # maturity, coverage, health
        title = options.get("title", "Capability Heat Map")

        # Load capabilities if not provided
        if not capabilities:
            capabilities = self._load_capabilities_for_heatmap(metric)

        # Generate heat map data
        heatmap_data = []
        for cap in capabilities:
            value = cap.get(metric, cap.get("maturity_level", 0))
            if isinstance(value, str):
                value = {"Low": 1, "Medium": 2, "High": 3, "Very High": 4}.get(value, 2)

            heatmap_data.append(
                {
                    "id": cap.get("id"),
                    "name": cap.get("name", "Unknown"),
                    "level": cap.get("level", "L1"),
                    "value": value,
                    "color": self._get_heatmap_color(value, metric),
                    "parent_id": cap.get("parent_id"),
                }
            )

        # Generate HTML grid representation
        html_grid = self._generate_heatmap_html(heatmap_data, title, metric)

        return {
            "success": True,
            "type": "capability_heatmap",
            "title": title,
            "metric": metric,
            "data": heatmap_data,
            "html_grid": html_grid,
            "capability_count": len(heatmap_data),
            "legend": self._get_heatmap_legend(metric),
            "render_instructions": {"type": "html_grid", "container_class": "capability-heatmap"},
        }

    def _get_heatmap_color(self, value: float, metric: str) -> str:
        """Get color based on value and metric type."""
        if metric in ["maturity", "health", "coverage"]:
            # Higher is better - green gradient
            if value >= 4:
                return "#22c55e"  # green - 500
            elif value >= 3:
                return "#84cc16"  # lime - 500
            elif value >= 2:
                return "#eab308"  # yellow - 500
            elif value >= 1:
                return "#f97316"  # orange - 500
            else:
                return "#ef4444"  # red - 500
        else:
            # Risk metric - lower is better
            if value <= 1:
                return "#22c55e"
            elif value <= 2:
                return "#84cc16"
            elif value <= 3:
                return "#eab308"
            else:
                return "#ef4444"

    def _get_heatmap_legend(self, metric: str) -> List[Dict]:
        """Get legend for heat map."""
        if metric in ["maturity", "health", "coverage"]:
            return [
                {"value": 4, "label": "Excellent", "color": "#22c55e"},
                {"value": 3, "label": "Good", "color": "#84cc16"},
                {"value": 2, "label": "Fair", "color": "#eab308"},
                {"value": 1, "label": "Poor", "color": "#f97316"},
                {"value": 0, "label": "Critical", "color": "#ef4444"},
            ]
        return []

    def _generate_heatmap_html(self, data: List[Dict], title: str, metric: str) -> str:
        """Generate HTML grid for heat map. title/metric/item['name']/
        item['level']/item['value'] are all AI-chat-reachable freeform
        text -- escape() every one before it reaches this browser-rendered
        HTML fragment. item['color'] is exempt: it's always a fixed hex
        string from _get_heatmap_color(), never free text."""
        safe_title = escape(str(title))
        safe_metric = escape(str(metric))
        html = (
            f'<div class="heatmap-container"><h3 class="text-lg font-semibold mb - 4">{safe_title}</h3>'  # raw-html-ok: safe_title is escape()'d above
        )
        html += '<div class="grid grid-cols - 4 gap - 2">'

        for item in data:
            safe_value = escape(str(item['value']))
            safe_name = escape(str(item['name']))
            safe_level = escape(str(item['level']))
            html += f"""
            <div class="p - 3 rounded-lg text-center text-white text-sm font-medium"
                 style="background-color: {item['color']}"
                 title="{safe_metric}: {safe_value}">
                <div class="truncate">
{safe_name}</div>
                <div class="text-xs opacity - 75">
{safe_level}</div>
            </div>
            """

        html += "</div></div>"
        return html

    def _generate_dependency_graph(
        self, context: Dict[str, Any], output_format: str, options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate application dependency graph."""
        applications = context.get("applications", [])
        dependencies = context.get("dependencies", [])
        title = options.get("title", "Application Dependencies")
        focus_app = options.get("focus_application", None)

        # Load applications if not provided
        if not applications:
            applications = self._load_applications_for_graph(focus_app, limit=15)

        # Generate Mermaid graph
        mermaid_code = self._dependencies_to_mermaid(applications, dependencies, title)

        # Generate D3/Cytoscape data format
        graph_data = {
            "nodes": [
                {
                    "id": str(app.get("id")),
                    "label": app.get("name", "Unknown"),
                    "type": app.get("type", "Application"),
                    "status": app.get("status", "Active"),
                    "criticality": app.get("criticality", "Medium"),
                }
                for app in applications
            ],
            "edges": [
                {
                    "source": str(dep.get("source_id")),
                    "target": str(dep.get("target_id")),
                    "type": dep.get("type", "depends_on"),
                    "label": dep.get("label", ""),
                }
                for dep in dependencies
            ],
        }

        return {
            "success": True,
            "type": "dependency_graph",
            "title": title,
            "mermaid": mermaid_code,
            "graph_data": graph_data,
            "node_count": len(applications),
            "edge_count": len(dependencies),
            "render_instructions": {
                "type": "mermaid",
                "alternative": "d3_force",
                "container_class": "dependency-graph",
            },
        }

    def _dependencies_to_mermaid(
        self, applications: List[Dict], dependencies: List[Dict], title: str
    ) -> str:
        """Convert dependencies to Mermaid graph."""
        lines = ["flowchart LR"]
        lines.append(f'    subgraph Dependencies["{title}"]')

        # Add nodes with styling based on criticality
        for app in applications:
            app_id = f"app_{app.get('id', 'unknown')}"
            app_name = app.get("name", "Unknown").replace('"', "'")
            criticality = app.get("criticality", "Medium")

            shape_start, shape_end = "([", "])" if criticality == "High" else ("[", "]")
            lines.append(f'        {app_id}{shape_start}"{app_name}"{shape_end}')

        lines.append("    end")

        # Add edges
        for dep in dependencies:
            source = f"app_{dep.get('source_id', 'unknown')}"
            target = f"app_{dep.get('target_id', 'unknown')}"
            dep_type = dep.get("type", "depends_on")

            if dep_type == "critical":
                arrow = "==>"
            elif dep_type == "data_flow":
                arrow = "-.->|data|"
            else:
                arrow = "-->"

            lines.append(f"    {source} {arrow} {target}")

        return "\n".join(lines)

    def _generate_roadmap_timeline(
        self, context: Dict[str, Any], output_format: str, options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate roadmap timeline visualization."""
        milestones = context.get("milestones", [])
        work_packages = context.get("work_packages", [])
        title = options.get("title", "Implementation Roadmap")
        start_date = options.get("start_date", datetime.now().strftime("%Y-%m-%d"))

        # Generate Gantt chart in Mermaid
        gantt_mermaid = self._generate_gantt_mermaid(milestones, work_packages, title, start_date)

        # Generate timeline data for custom rendering
        timeline_data = {
            "title": title,
            "start_date": start_date,
            "milestones": milestones,
            "work_packages": work_packages,
            "phases": self._group_into_phases(milestones, work_packages),
        }

        return {
            "success": True,
            "type": "roadmap_timeline",
            "title": title,
            "gantt_mermaid": gantt_mermaid,
            "timeline_data": timeline_data,
            "milestone_count": len(milestones),
            "work_package_count": len(work_packages),
            "render_instructions": {"type": "gantt", "container_class": "roadmap-timeline"},
        }

    def _generate_gantt_mermaid(
        self, milestones: List[Dict], work_packages: List[Dict], title: str, start_date: str
    ) -> str:
        """Generate Mermaid Gantt chart."""
        lines = ["gantt"]
        lines.append(f"    title {title}")
        lines.append("    dateFormat YYYY-MM-DD")
        lines.append("    axisFormat %b %Y")
        lines.append("")

        # Add sections for phases
        current_section = None
        for wp in work_packages:
            section = wp.get("phase", "General")
            if section != current_section:
                lines.append(f"    section {section}")
                current_section = section

            wp_name = wp.get("name", "Task").replace(":", "-")
            wp_start = wp.get("start_date", start_date)
            wp_duration = wp.get("duration_days", 30)
            wp_status = wp.get("status", "active")

            status_marker = (
                "done," if wp_status == "completed" else "active," if wp_status == "active" else ""
            )
            lines.append(f"    {wp_name} :{status_marker} {wp_start}, {wp_duration}d")

        # Add milestones
        if milestones:
            lines.append("    section Milestones")
            for ms in milestones:
                ms_name = ms.get("name", "Milestone").replace(":", "-")
                ms_date = ms.get("date", start_date)
                lines.append(f"    {ms_name} : milestone, {ms_date}, 0d")

        return "\n".join(lines)

    def _group_into_phases(self, milestones: List[Dict], work_packages: List[Dict]) -> List[Dict]:
        """Group work packages into phases."""
        phases = {}
        for wp in work_packages:
            phase = wp.get("phase", "General")
            if phase not in phases:
                phases[phase] = {"name": phase, "work_packages": [], "milestones": []}
            phases[phase]["work_packages"].append(wp)

        for ms in milestones:
            phase = ms.get("phase", "General")
            if phase in phases:
                phases[phase]["milestones"].append(ms)

        return list(phases.values())

    def _generate_portfolio_matrix(
        self, context: Dict[str, Any], output_format: str, options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate portfolio matrix (2x2 quadrant analysis)."""
        items = context.get("items", [])
        x_axis = options.get("x_axis", {"label": "Business Value", "field": "business_value"})
        y_axis = options.get("y_axis", {"label": "Technical Health", "field": "technical_health"})
        title = options.get("title", "Portfolio Matrix")

        # Load applications if items not provided
        if not items:
            items = self._load_applications_for_matrix()

        # Calculate quadrant positions
        matrix_data = {
            "title": title,
            "x_axis": x_axis,
            "y_axis": y_axis,
            "quadrants": {
                "invest": {"label": "Invest", "items": [], "color": "#22c55e"},
                "maintain": {"label": "Maintain", "items": [], "color": "#3b82f6"},
                "migrate": {"label": "Migrate", "items": [], "color": "#eab308"},
                "retire": {"label": "Retire", "items": [], "color": "#ef4444"},
            },
            "items": [],
        }

        for item in items:
            x_val = item.get(x_axis["field"], 50)
            y_val = item.get(y_axis["field"], 50)

            # Determine quadrant
            if x_val >= 50 and y_val >= 50:
                quadrant = "invest"
            elif x_val >= 50 and y_val < 50:
                quadrant = "migrate"
            elif x_val < 50 and y_val >= 50:
                quadrant = "maintain"
            else:
                quadrant = "retire"

            item_data = {
                "id": item.get("id"),
                "name": item.get("name", "Unknown"),
                "x": x_val,
                "y": y_val,
                "quadrant": quadrant,
                "size": item.get("size", 10),
            }

            matrix_data["items"].append(item_data)
            matrix_data["quadrants"][quadrant]["items"].append(item_data)

        return {
            "success": True,
            "type": "portfolio_matrix",
            "title": title,
            "matrix_data": matrix_data,
            "item_count": len(items),
            "render_instructions": {
                "type": "scatter_quadrant",
                "container_class": "portfolio-matrix",
            },
        }

    def _generate_flow_diagram(
        self, context: Dict[str, Any], output_format: str, options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate process or data flow diagram."""
        nodes = context.get("nodes", [])
        flows = context.get("flows", [])
        title = options.get("title", "Flow Diagram")
        direction = options.get("direction", "LR")  # LR, TB, RL, BT

        # Generate Mermaid flowchart
        mermaid_code = self._flows_to_mermaid(nodes, flows, title, direction)

        return {
            "success": True,
            "type": "flow_diagram",
            "title": title,
            "mermaid": mermaid_code,
            "node_count": len(nodes),
            "flow_count": len(flows),
            "render_instructions": {"type": "mermaid", "container_class": "flow-diagram"},
        }

    def _flows_to_mermaid(
        self, nodes: List[Dict], flows: List[Dict], title: str, direction: str
    ) -> str:
        """Convert flow data to Mermaid diagram."""
        lines = [f"flowchart {direction}"]

        # Add nodes
        for node in nodes:
            node_id = f"n_{node.get('id', 'unknown')}"
            node_name = node.get("name", "Unknown").replace('"', "'")
            node_type = node.get("type", "process")

            if node_type == "start":
                shape = f"(({node_name}))"
            elif node_type == "end":
                shape = f"(({node_name}))"
            elif node_type == "decision":
                shape = f"{{{node_name}}}"
            elif node_type == "data":
                shape = f"[({node_name})]"
            else:
                shape = f"[{node_name}]"

            lines.append(f"    {node_id}{shape}")

        # Add flows
        for flow in flows:
            source = f"n_{flow.get('source_id', 'unknown')}"
            target = f"n_{flow.get('target_id', 'unknown')}"
            label = flow.get("label", "")

            if label:
                lines.append(f"    {source} -->|{label}| {target}")
            else:
                lines.append(f"    {source} --> {target}")

        return "\n".join(lines)

    # Database loading helpers

    def _load_archimate_elements(self, layer: str = None, limit: int = 20) -> List[Dict]:
        """Load ArchiMate elements from database."""
        try:
            from app.models.archimate_core import ArchiMateElement

            query = ArchiMateElement.query
            if layer:
                query = query.filter(ArchiMateElement.layer == layer)
            elements = query.limit(limit).all()
            return [
                {
                    "id": el.id,
                    "name": el.name,
                    "type": getattr(el, "type", "Component"),
                    "layer": getattr(el, "layer", "Application"),
                    "description": getattr(el, "description", ""),
                }
                for el in elements
            ]
        except Exception as e:
            self.logger.warning(f"Could not load ArchiMate elements: {e}")
            return []

    def _load_capabilities_for_heatmap(self, metric: str, limit: int = 30) -> List[Dict]:
        """Load capabilities for heat map visualization."""
        try:
            from app.models.unified_capability import UnifiedCapability

            capabilities = UnifiedCapability.active().limit(limit).all()
            return [
                {
                    "id": cap.id,
                    "name": cap.name,
                    "level": getattr(cap, "level", "L1"),
                    "maturity_level": getattr(cap, "maturity_level", 2),
                    "parent_id": getattr(cap, "parent_id", None),
                }
                for cap in capabilities
            ]
        except Exception as e:
            self.logger.warning(f"Could not load capabilities: {e}")
            return []

    def _load_applications_for_graph(self, focus_app: str = None, limit: int = 15) -> List[Dict]:
        """Load applications for dependency graph."""
        try:
            from app.models.application_portfolio import ApplicationComponent

            query = ApplicationComponent.query.limit(limit)
            apps = query.all()
            return [
                {
                    "id": app.id,
                    "name": app.name,
                    "status": getattr(app, "lifecycle_status", "Active"),
                    "criticality": getattr(app, "criticality", "Medium"),
                }
                for app in apps
            ]
        except Exception as e:
            self.logger.warning(f"Could not load applications: {e}")
            return []

    def _load_applications_for_matrix(self, limit: int = 30) -> List[Dict]:
        """Load applications for portfolio matrix."""
        try:
            from app.models.application_portfolio import ApplicationComponent

            apps = ApplicationComponent.query.limit(limit).all()
            return [
                {
                    "id": app.id,
                    "name": app.name,
                    "business_value": getattr(app, "business_value", 50),
                    "technical_health": getattr(app, "health_score", 50),
                    "size": getattr(app, "user_count", 10) or 10,
                }
                for app in apps
            ]
        except Exception as e:
            self.logger.warning(f"Could not load applications: {e}")
            return []
