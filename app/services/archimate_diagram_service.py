"""ArchiMate diagram rendering service — generates SVG viewpoint diagrams."""
import html

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

LAYER_ORDER = [
    "motivation",
    "strategy",
    "business",
    "application",
    "technology",
    "implementation",
    "physical",
]

LAYER_COLORS = {
    "motivation": "#f0e6ff",
    "strategy": "#e6f0ff",
    "business": "#fff9e6",
    "application": "#e6ffe6",
    "technology": "#e6f9ff",
    "implementation": "#ffe6e6",
    "physical": "#f5f5f5",
}


class DiagramRenderService:
    BOX_W = 160
    BOX_H = 50
    BOX_GAP = 20
    LAYER_H = 100
    MARGIN = 40

    NAME_LINE_CHARS = 22
    BOX_H_TWO_LINES = 64

    @classmethod
    def _name_lines(cls, name: str) -> list:
        """Split a name over at most two lines on word boundaries; only a name
        longer than two lines is cut."""
        lines, current = [], ""
        for word in name.split():
            joined = (current + " " + word).strip()
            if len(joined) <= cls.NAME_LINE_CHARS or not current:
                current = joined
            else:
                lines.append(current)
                current = word
        lines.append(current)
        if len(lines) > 2:
            rest = " ".join(lines[1:])
            lines = [lines[0], rest]
        lines = [ln for ln in lines if ln]
        if len(lines) == 2 and len(lines[1]) > cls.NAME_LINE_CHARS:
            lines[1] = lines[1][: cls.NAME_LINE_CHARS - 1] + "\u2026"
        if len(lines) == 1 and len(lines[0]) > cls.NAME_LINE_CHARS:
            first, rest = lines[0][: cls.NAME_LINE_CHARS], lines[0][cls.NAME_LINE_CHARS:]
            lines = [first, rest if len(rest) <= cls.NAME_LINE_CHARS else rest[: cls.NAME_LINE_CHARS - 1] + "\u2026"]
        return lines or [""]

    def render_diagram(self, element_ids: list, viewpoint: str = "application", type_namer=None) -> str:
        """Return SVG string for the given element IDs grouped by ArchiMate layer.

        With no ``type_namer`` the labels are the stored type and a name cut at
        22 characters. With one (a callable from stored type to display name),
        the type line uses it and names wrap over two lines in full, with the
        full name on the box as a hover title.
        """
        if not element_ids:
            return (
                '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100">'
                '<text x="10" y="20" font-size="12">No elements selected</text>'
                "</svg>"
            )

        try:
            # Find relationships connected to the initial elements
            rels = ArchiMateRelationship.query.filter(
                db.or_(
                    ArchiMateRelationship.source_id.in_(element_ids),
                    ArchiMateRelationship.target_id.in_(element_ids),
                )
            ).all()

            # Get all unique element IDs from the initial set and the relationships
            all_element_ids = set(element_ids)
            for rel in rels:
                all_element_ids.add(rel.source_id)
                all_element_ids.add(rel.target_id)

            # Fetch all elements that will be part of the diagram
            elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(list(all_element_ids))
            ).all()
        except Exception:
            elements = []
            rels = []

        if not elements:
            return (
                '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100">'
                '<text x="10" y="20" font-size="12">No elements found</text>'
                "</svg>"
            )

        # Group by layer
        layers = {}
        for el in elements:
            layer = (el.layer or "application").lower()
            layers.setdefault(layer, []).append(el)

        # Determine draw order: canonical order first, then any extras
        ordered_layers = [item for item in LAYER_ORDER if item in layers] + [
            item for item in layers if item not in LAYER_ORDER
        ]

        canvas_w = max(
            max(
                len(els) * (self.BOX_W + self.BOX_GAP)
                for els in layers.values()
            )
            + self.MARGIN * 2,
            600,
        )
        full_labels = type_namer is not None
        box_h = self.BOX_H
        if full_labels and any(
            len(self._name_lines(e.name or "")) > 1 for e in elements
        ):
            box_h = self.BOX_H_TWO_LINES
        canvas_h = len(ordered_layers) * self.LAYER_H + self.MARGIN * 2

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}"'  # raw-html-ok: canvas_w/canvas_h are computed ints (layout math), never free text
            ' font-family="Arial,sans-serif" font-size="11">',
            (
                "<defs>"
                '<marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">'
                '<path d="M0,0 L0,6 L8,3 z" fill="#666"/>'
                "</marker>"
                "</defs>"
            ),
        ]

        # Compute centre positions for relationship drawing
        positions = {}  # element_id -> (cx, cy)

        for layer_idx, layer_name in enumerate(ordered_layers):
            els = layers[layer_name]
            color = LAYER_COLORS.get(layer_name, "#f5f5f5")
            y_top = self.MARGIN + layer_idx * self.LAYER_H
            row_w = len(els) * (self.BOX_W + self.BOX_GAP) - self.BOX_GAP
            x_start = (canvas_w - row_w) // 2

            # Layer background band
            svg_parts.append(
                f'<rect x="{self.MARGIN // 2}" y="{y_top}"'  # raw-html-ok: self.MARGIN/y_top are computed ints
                f' width="{canvas_w - self.MARGIN}" height="{self.LAYER_H - 10}"'  # raw-html-ok: canvas_w/self.MARGIN/self.LAYER_H are computed ints
                f' rx="4" fill="{color}" stroke="#ccc" stroke-width="1"/>'  # raw-html-ok: color is a LAYER_COLORS dict value (fixed hex palette, not user input)
            )
            svg_parts.append(
                f'<text x="{self.MARGIN}" y="{y_top + 14}"'  # raw-html-ok: self.MARGIN/y_top are computed ints
                f' font-size="10" fill="#666" font-weight="bold">'
                f"{html.escape(layer_name.upper())}</text>"
            )

            for el_idx, el in enumerate(els):
                bx = x_start + el_idx * (self.BOX_W + self.BOX_GAP)
                by = y_top + 25
                # Store centre point for arrow drawing
                positions[el.id] = (bx + self.BOX_W // 2, by + box_h // 2)

                raw_name = el.name or ""
                cx = bx + self.BOX_W // 2
                if full_labels:
                    name_lines = [html.escape(ln) for ln in self._name_lines(raw_name)]
                    el_type = html.escape(type_namer(el.type) if el.type else "")
                    svg_parts.append(f"<g><title>{html.escape(raw_name)}</title>")
                else:
                    name_lines = [html.escape(
                        raw_name[:22] + "\u2026" if len(raw_name) > 22 else raw_name
                    )]
                    el_type = html.escape(el.type or "")

                svg_parts.append(
                    f'<rect x="{bx}" y="{by}" width="{self.BOX_W}" height="{box_h}"'  # raw-html-ok: bx/by/self.BOX_W/box_h are computed ints (layout math)
                    f' rx="3" fill="white" stroke="#666" stroke-width="1.5"/>'
                )
                for line_idx, line in enumerate(name_lines):
                    svg_parts.append(
                        f'<text x="{cx}" y="{by + 18 + line_idx * 14}"'  # raw-html-ok: cx/by/line_idx are computed ints
                        f' text-anchor="middle" font-weight="bold">{line}</text>'  # raw-html-ok: line is html.escape()'d above, used unchanged
                    )
                svg_parts.append(
                    f'<text x="{cx}" y="{by + 32 + (len(name_lines) - 1) * 14}"'  # raw-html-ok: cx/by/name_lines are computed ints
                    f' text-anchor="middle" fill="#888">{el_type}</text>'  # raw-html-ok: el_type is html.escape()'d above, used unchanged
                )
                if full_labels:
                    svg_parts.append("</g>")

        # Relationships — drawn after elements so they appear on top of band fills
        try:
            for rel in rels:
                if rel.source_id in positions and rel.target_id in positions:
                    x1, y1 = positions[rel.source_id]
                    x2, y2 = positions[rel.target_id]
                    svg_parts.append(
                        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"'  # raw-html-ok: x1/y1/x2/y2 are computed ints from stored element centre-point tuples
                        f' stroke="#666" stroke-width="1.5" marker-end="url(#arrow)"/>'
                    )
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — relationship errors must not abort SVG output
            # Relationship query failures are non-fatal; diagram is still useful without edges
            pass

        svg_parts.append("</svg>")
        return "\n".join(svg_parts)
