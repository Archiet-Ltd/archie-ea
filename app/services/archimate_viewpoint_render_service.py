from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship


class ArchimateViewpointRenderService:
    LAYER_ORDER = ["strategy", "motivation", "business", "application", "technology", "physical", "implementation_migration"]

    def render_viewpoint(self, phase_code: str, element_ids: list) -> dict:
        """
        Given a phase_code and list of ArchiMateElement IDs,
        query the graph and return a structured viewpoint dict.
        Returns empty viewpoint (never raises) if element_ids is empty or DB error occurs.
        """
        if not element_ids:
            return self._empty_viewpoint(phase_code)
        try:
            elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
            relationships = ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id.in_(element_ids),
                ArchiMateRelationship.target_id.in_(element_ids)
            ).all()
            elements_by_layer = {}
            for e in elements:
                layer = e.layer or "unknown"
                elements_by_layer.setdefault(layer, []).append({
                    "id": e.id, "name": e.name, "type": e.type,
                    "layer": e.layer, "plateau": e.togaf_plateau,
                    "building_block_type": e.building_block_type
                })
            rels = [{"id": r.id, "type": r.type, "source_id": r.source_id, "target_id": r.target_id} for r in relationships]
            layer_summary = {layer: len(elems) for layer, elems in elements_by_layer.items()}
            return {
                "phase_code": phase_code,
                "elements_by_layer": elements_by_layer,
                "relationships": rels,
                "element_count": len(elements),
                "relationship_count": len(relationships),
                "layer_summary": layer_summary
            }
        except Exception:
            return self._empty_viewpoint(phase_code)

    def _empty_viewpoint(self, phase_code: str) -> dict:
        return {"phase_code": phase_code, "elements_by_layer": {}, "relationships": [], "element_count": 0, "relationship_count": 0, "layer_summary": {}}
