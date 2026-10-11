"""ArchiMate Impact Analysis Service — change propagation (ARCH-016).

Delegates the walk itself to the one canonical impact engine
(``IntelligenceQueryService.cross_layer_impact``, tenant-fenced and
covered by ``test_one_impact_engine.py``) and reshapes its rows into the
``direct_impacts``/``indirect_impacts``/``by_layer`` contract this
service's own callers (the architect UI's traceability/impact pages,
``get_capability_gaps``) already expect. It never runs its own
traversal query against ``ArchiMateElement``/``ArchiMateRelationship``.
"""

from app.utils.route_guards import load_entity
from app.models.archimate_core import ArchiMateElement


class ArchiMateImpactService:

    def get_impact_summary(self, element_id: int, max_hops: int = 2) -> dict:
        """Return a compact impact summary for lightweight detail-page previews."""
        analysis = self.analyze_impact(element_id, max_hops=max_hops)
        if analysis.get('error'):
            return analysis

        return {
            'total_affected': analysis.get('total_impacted', 0),
            'by_layer': analysis.get('by_layer', {}),
        }

    def analyze_impact(self, element_id: int, max_hops: int = 3) -> dict:
        """
        Given an element, find all elements impacted if this element changes/is removed.
        Returns: direct_impacts (hop=1), indirect_impacts (hop=2-3), summary by layer.
        """
        from app.modules.intelligence.services.query_service import IntelligenceQueryService

        root = load_entity(ArchiMateElement, element_id)
        if not root:
            return {'error': 'Element not found'}

        # Two directional walks (not direction="both") so each impacted
        # element can still be labelled "outgoing"/"incoming" the way the
        # UI already renders it. An element reached in both directions
        # keeps its (shorter, or on a tie outgoing) hop -- the same
        # tie-break the old same-session propagation used.
        downstream = IntelligenceQueryService.cross_layer_impact(
            element_id, include_derived=False, max_depth=max_hops,
            direction="downstream", with_owner=False,
        )
        upstream = IntelligenceQueryService.cross_layer_impact(
            element_id, include_derived=False, max_depth=max_hops,
            direction="upstream", with_owner=False,
        )

        all_impacts = []
        seen: dict[int, int] = {}  # element_id -> index into all_impacts
        for walk, direction in ((downstream, 'outgoing'), (upstream, 'incoming')):
            elements = walk.get('elements') or {}
            for row in walk.get('rows') or []:
                eid = row['element_id']
                info = elements.get(str(eid)) or {}
                hop = row['relation']['depth']
                # The canonical engine hands back layer as a canonical
                # lower-case string; this service's own callers (the
                # impact_analysis template, get_capability_gaps) expect the
                # capitalised convention ('Strategy', 'Business', ...).
                layer = info.get('layer')
                candidate = {
                    'id': eid,
                    'name': info.get('name'),
                    'layer': layer.capitalize() if layer else 'Unknown',
                    'type': info.get('type') or '',
                    'hop': hop,
                    'via_relationship': row['relation']['type'],
                    'direction': direction,
                }
                existing_index = seen.get(eid)
                if existing_index is None:
                    seen[eid] = len(all_impacts)
                    all_impacts.append(candidate)
                elif candidate['hop'] < all_impacts[existing_index]['hop']:
                    all_impacts[existing_index] = candidate

        direct = [i for i in all_impacts if i['hop'] == 1]
        indirect = [i for i in all_impacts if i['hop'] > 1]

        by_layer: dict = {}
        for imp in all_impacts:
            layer = imp['layer'] or 'Unknown'
            by_layer.setdefault(layer, []).append(imp)

        return {
            'root': {
                'id': root.id,
                'name': root.name,
                'layer': root.layer,
                'type': root.type,
            },
            'total_impacted': len(all_impacts),
            'direct_impacts': direct,
            'indirect_impacts': indirect,
            'by_layer': {layer: len(els) for layer, els in by_layer.items()},
            'impact_details': by_layer,
        }

    def get_capability_gaps(self, element_id: int) -> list:
        """Find Strategy/Capability elements at risk if this element is removed."""
        impact = self.analyze_impact(element_id, max_hops=3)
        return [e for e in impact.get('impact_details', {}).get('Strategy', [])]
