"""ArchiMate 3.2 Relationship Validity Service.

Encodes the ArchiMate 3.2 derivation table as an in-memory lookup matrix.
Answers: given a source element type and target element type, which
relationship types are valid?

Returns results with a tier indicator:
  - 'standard': directly valid per ArchiMate 3.2 specification
  - 'derived': valid through derivation (suggest intermediate element)
  - 'fallback': Association is always technically valid but discouraged

Reference: The Open Group ArchiMate 3.2 Specification, Appendix B.
"""


# -- Element type to layer/aspect mapping -----------------------------------

_TYPE_LAYER = {
    # Business layer
    "BusinessActor": "business",
    "BusinessRole": "business",
    "BusinessCollaboration": "business",
    "BusinessInterface": "business",
    "BusinessProcess": "business",
    "BusinessFunction": "business",
    "BusinessInteraction": "business",
    "BusinessEvent": "business",
    "BusinessService": "business",
    "BusinessObject": "business",
    "Contract": "business",
    "Representation": "business",
    "Product": "business",
    # Application layer
    "ApplicationComponent": "application",
    "ApplicationCollaboration": "application",
    "ApplicationInterface": "application",
    "ApplicationFunction": "application",
    "ApplicationProcess": "application",
    "ApplicationInteraction": "application",
    "ApplicationEvent": "application",
    "ApplicationService": "application",
    "DataObject": "application",
    # Technology layer
    "Node": "technology",
    "Device": "technology",
    "SystemSoftware": "technology",
    "TechnologyCollaboration": "technology",
    "TechnologyInterface": "technology",
    "Path": "technology",
    "CommunicationNetwork": "technology",
    "TechnologyFunction": "technology",
    "TechnologyProcess": "technology",
    "TechnologyInteraction": "technology",
    "TechnologyEvent": "technology",
    "TechnologyService": "technology",
    "Artifact": "technology",
    # Physical layer (ArchiMate 3.2 — distinct from Technology)
    "Equipment": "physical",
    "Facility": "physical",
    "DistributionNetwork": "physical",
    "Material": "physical",
    # Motivation layer
    "Stakeholder": "motivation",
    "Driver": "motivation",
    "Assessment": "motivation",
    "Goal": "motivation",
    "Outcome": "motivation",
    "Principle": "motivation",
    "Requirement": "motivation",
    "Constraint": "motivation",
    "Meaning": "motivation",
    "Value": "motivation",
    # Strategy layer
    "Resource": "strategy",
    "Capability": "strategy",
    "ValueStream": "strategy",
    "CourseOfAction": "strategy",
    # Implementation & Migration layer
    "WorkPackage": "implementation",
    "Deliverable": "implementation",
    "ImplementationEvent": "implementation",
    "Plateau": "implementation",
    "Gap": "implementation",
}

_TYPE_ASPECT = {
    # Active structure
    "BusinessActor": "active", "BusinessRole": "active",
    "BusinessCollaboration": "active", "BusinessInterface": "active",
    "ApplicationComponent": "active", "ApplicationCollaboration": "active",
    "ApplicationInterface": "active",
    "Node": "active", "Device": "active", "SystemSoftware": "active",
    "TechnologyCollaboration": "active", "TechnologyInterface": "active",
    "Path": "active", "CommunicationNetwork": "active",
    # Physical layer — active structure
    "Equipment": "active", "Facility": "active",
    "DistributionNetwork": "active",
    # Physical layer — passive structure
    "Material": "passive",
    # Behaviour
    "BusinessProcess": "behaviour", "BusinessFunction": "behaviour",
    "BusinessInteraction": "behaviour", "BusinessEvent": "behaviour",
    "BusinessService": "behaviour",
    "ApplicationFunction": "behaviour", "ApplicationProcess": "behaviour",
    "ApplicationInteraction": "behaviour", "ApplicationEvent": "behaviour",
    "ApplicationService": "behaviour",
    "TechnologyFunction": "behaviour", "TechnologyProcess": "behaviour",
    "TechnologyInteraction": "behaviour", "TechnologyEvent": "behaviour",
    "TechnologyService": "behaviour",
    # Passive structure
    "BusinessObject": "passive", "Contract": "passive",
    "Representation": "passive", "Product": "passive",
    "DataObject": "passive", "Artifact": "passive",
    # Motivation (own aspect)
    "Stakeholder": "motivation", "Driver": "motivation",
    "Assessment": "motivation", "Goal": "motivation",
    "Outcome": "motivation", "Principle": "motivation",
    "Requirement": "motivation", "Constraint": "motivation",
    "Meaning": "motivation", "Value": "motivation",
    # Strategy (own aspect)
    "Resource": "strategy", "Capability": "strategy",
    "ValueStream": "strategy", "CourseOfAction": "strategy",
    # Implementation
    "WorkPackage": "implementation", "Deliverable": "implementation",
    "ImplementationEvent": "implementation",
    "Plateau": "implementation", "Gap": "implementation",
}


def _normalize_type(element_type):
    """Convert snake_case DB type to PascalCase for dict lookup.

    DB stores 'application_component', dicts use 'ApplicationComponent'.
    """
    if not element_type:
        return ""
    # Already PascalCase? Return as-is.
    if element_type[0].isupper() and "_" not in element_type:
        return element_type
    return "".join(word.capitalize() for word in element_type.split("_"))


def _layer(element_type):
    return _TYPE_LAYER.get(_normalize_type(element_type), "unknown")


def _aspect(element_type):
    return _TYPE_ASPECT.get(_normalize_type(element_type), "unknown")


# ArchiMate 3.2 §5.1.3: Realization "represents that an entity plays a critical role
# in the creation, achievement, sustenance, or operation of a MORE ABSTRACT entity".
# Its permitted targets are therefore the abstract ones — services and other behaviour,
# passive structure (objects, contracts, products, representations, artifacts,
# deliverables) and the motivation / strategy / implementation elements. An ACTIVE
# STRUCTURE element (Actor, Role, Collaboration, Interface, Component, Node, Device,
# SystemSoftware, Path, CommunicationNetwork, Equipment, Facility, DistributionNetwork)
# is a concrete performer, never "more abstract", so it can never be the TARGET of a
# realization.
#
# This is enforced as one predicate applied to every rule's output rather than being
# encoded per source→target pair, because the per-direction encoding is exactly how the
# two directions drifted: the cross-layer rule granted realization on layer RANK alone,
# so ApplicationComponent → BusinessActor was offered as valid while
# BusinessActor → ApplicationComponent (correctly) was not. Deriving the constraint from
# the target's aspect makes the answer the same however the pair is written down.
_LAYERS_WITH_ACTIVE_STRUCTURE = ("business", "application", "technology", "physical")


def permits_realization_target(target_type):
    """True when ``target_type`` may be the TARGET of a realization relationship."""
    return not (
        _aspect(target_type) == "active"
        and _layer(target_type) in _LAYERS_WITH_ACTIVE_STRUCTURE
    )


# QA-CMP-005: LAYER_ELEMENTS — inverse lookup: layer name → set of element types.
# Physical is explicitly separated from Technology per ArchiMate 3.2 §9.5.
LAYER_ELEMENTS = {
    "business": {
        t for t, layer in _TYPE_LAYER.items() if layer == "business"
    },
    "application": {
        t for t, layer in _TYPE_LAYER.items() if layer == "application"
    },
    "technology": {
        t for t, layer in _TYPE_LAYER.items() if layer == "technology"
    },
    "physical": {
        t for t, layer in _TYPE_LAYER.items() if layer == "physical"
    },
    "motivation": {
        t for t, layer in _TYPE_LAYER.items() if layer == "motivation"
    },
    "strategy": {
        t for t, layer in _TYPE_LAYER.items() if layer == "strategy"
    },
    "implementation": {
        t for t, layer in _TYPE_LAYER.items() if layer == "implementation"
    },
}


# -- Core validity matrix ---------------------------------------------------
# Encodes ArchiMate 3.2 Appendix B relationship tables as rules.
# Each rule: (source_aspect_or_type, target_aspect_or_type, rel_type, constraint)

_SAME_LAYER_STRUCTURAL = [
    # Within same layer: active -> behaviour
    ("active", "behaviour", "assignment",
     "Active structure element is assigned to perform this behaviour"),
    # Within same layer: behaviour -> passive
    ("behaviour", "passive", "access",
     "Behaviour element accesses this passive structure element"),
    # Within same layer: behaviour -> behaviour
    ("behaviour", "behaviour", "triggering",
     "One behaviour triggers another within the same layer"),
    ("behaviour", "behaviour", "flow",
     "Transfer of information or material between behaviours"),
    # Within same layer: active -> active, behaviour -> behaviour, passive -> passive
    ("active", "active", "composition",
     "One active structure is composed of another"),
    ("active", "active", "aggregation",
     "One active structure aggregates another"),
    ("behaviour", "behaviour", "composition",
     "One behaviour is composed of sub-behaviours"),
    ("behaviour", "behaviour", "aggregation",
     "One behaviour aggregates other behaviours"),
    ("passive", "passive", "composition",
     "One passive structure is composed of another"),
    ("passive", "passive", "aggregation",
     "One passive structure aggregates another"),
    # Service -> external (serving)
    ("behaviour", "behaviour", "serving",
     "One behaviour serves another"),
]

_CROSS_LAYER_RULES = [
    # Realisation: lower layer realises upper layer
    # Technology -> Application
    ("technology", "application", "realization",
     "Technology element realises an application element"),
    # Application -> Business
    ("application", "business", "realization",
     "Application element realises a business element"),
    # Serving across layers (service -> process/function in upper layer)
    ("application", "business", "serving",
     "Application service serves a business process"),
    ("technology", "application", "serving",
     "Technology service serves an application component"),
    # Physical <-> Technology (peer layers)
    ("physical", "technology", "serving",
     "Physical element serves a technology element"),
    ("technology", "physical", "serving",
     "Technology element serves a physical element"),
    ("physical", "technology", "realization",
     "Physical element realises a technology element"),
    ("technology", "physical", "realization",
     "Technology element realises a physical element"),
    # Physical -> Application (derived, through technology intermediary)
    ("physical", "application", "realization",
     "Physical element realises an application element (derived — use technology intermediary)"),
    # Strategy -> Motivation
    ("strategy", "motivation", "realization",
     "Strategy element realises a motivation element"),
]


class ArchimateValidityService:
    """Relationship validity checking based on ArchiMate 3.2 specification."""

    def get_valid_relationships(self, source_type, target_type):
        """Return list of valid relationship types for a source-target pair.

        Returns list of dicts:
            [{"type": "serving", "tier": "standard", "description": "..."}]
        """
        # Normalize snake_case DB types to PascalCase for internal lookups
        source_type = _normalize_type(source_type)
        target_type = _normalize_type(target_type)

        results = []
        src_layer = _layer(source_type)
        tgt_layer = _layer(target_type)
        src_aspect = _aspect(source_type)
        tgt_aspect = _aspect(target_type)

        if src_layer == "unknown" or tgt_layer == "unknown":
            return results

        # -- Motivation layer special handling --
        if src_layer == "motivation" and tgt_layer == "motivation":
            results.extend(self._motivation_rules(source_type, target_type))
            results.append({
                "type": "association",
                "tier": "fallback",
                "description": "Generic association (consider a more specific type)",
            })
            return results

        # -- Strategy layer --
        if src_layer == "strategy" or tgt_layer == "strategy":
            results.extend(self._strategy_rules(source_type, target_type,
                                                 src_layer, tgt_layer))

        # -- Implementation layer --
        if src_layer == "implementation" or tgt_layer == "implementation":
            results.extend(self._implementation_rules(source_type, target_type,
                                                       src_layer, tgt_layer))

        # -- Same layer rules --
        if src_layer == tgt_layer and src_layer in ("business", "application", "technology", "physical"):
            results.extend(self._same_layer_rules(source_type, target_type,
                                                   src_aspect, tgt_aspect, src_layer))

        # -- Cross-layer rules --
        if src_layer != tgt_layer:
            results.extend(self._cross_layer_rules(source_type, target_type,
                                                    src_layer, tgt_layer,
                                                    src_aspect, tgt_aspect))

        # -- Association is always available as fallback --
        if not any(r["type"] == "association" for r in results):
            results.append({
                "type": "association",
                "tier": "fallback",
                "description": "Generic association (consider a more specific type)",
            })

        # Realization may only point at a more abstract entity — never at active
        # structure. Applied once, over every rule's output, so the constraint cannot
        # be granted by one branch and withheld by another (see the note on
        # permits_realization_target).
        if not permits_realization_target(target_type):
            results = [r for r in results if r["type"] != "realization"]

        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            key = r["type"]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def is_valid(self, source_type, target_type, relationship_type):
        """Check if a specific relationship type is valid between two element types."""
        src = _normalize_type(source_type)
        tgt = _normalize_type(target_type)

        # ArchiMate 3.2 §5.1: Specialization is always valid between elements of the same type.
        if relationship_type == "specialization" and src == tgt:
            return True

        # ArchiMate 3.2 §5.1: Influence is valid from/to any Strategy or Motivation element
        # (and commonly used in practice across all layers to express soft dependencies).
        if relationship_type == "influence":
            src_layer = _layer(src)
            tgt_layer = _layer(tgt)
            if src_layer in ("strategy", "motivation") or tgt_layer in ("strategy", "motivation"):
                return True

        # Association is always valid as a fallback.
        if relationship_type == "association":
            return True

        valid = self.get_valid_relationships(source_type, target_type)
        return any(r["type"] == relationship_type for r in valid
                   if r["tier"] != "fallback" or relationship_type == "association")

    def get_practitioner_warnings(self, source_type, target_type, relationship_type):
        """Return warnings for common practitioner mistakes."""
        source_type = _normalize_type(source_type)
        target_type = _normalize_type(target_type)
        warnings = []
        src_aspect = _aspect(source_type)
        tgt_aspect = _aspect(target_type)
        src_layer = _layer(source_type)
        tgt_layer = _layer(target_type)

        # Mistake 1: Assignment from passive to active (should be Access)
        if relationship_type == "assignment" and src_aspect == "passive":
            warnings.append(
                f"Assignment from a passive element ({source_type}) is unusual. "
                f"Did you mean Access (read/write)?"
            )
        if relationship_type == "assignment" and tgt_aspect == "passive":
            warnings.append(
                f"Assignment to a passive element ({target_type}) is invalid. "
                f"Use Access instead."
            )

        # Mistake 2: Cross-layer Composition
        if relationship_type == "composition" and src_layer != tgt_layer:
            if {src_layer, tgt_layer} == {"physical", "technology"}:
                warnings.append(
                    f"Composition cannot cross layers ({src_layer} -> {tgt_layer}). "
                    f"Physical and Technology are peer layers but Composition still "
                    f"requires same-layer ownership. Use Serving or Realization instead."
                )
            else:
                warnings.append(
                    f"Composition cannot cross layers ({src_layer} -> {tgt_layer}). "
                    f"Composition means ownership within the same layer."
                )

        # Mistake 3: Backwards Serving
        if relationship_type == "serving":
            if src_aspect == "active" and tgt_aspect == "behaviour":
                pass  # This is actually fine via assignment chain
            # The common mistake is drawing serving from consumer to provider
            # We can't detect direction intent, but we can remind
            if src_layer == "business" and tgt_layer == "application":
                warnings.append(
                    "Serving goes from provider to consumer. If the application "
                    "serves the business process, the arrow should go FROM "
                    "application TO business."
                )

        # Mistake 4: Cross-layer Triggering without intermediary
        if relationship_type == "triggering" and src_layer != tgt_layer:
            if {src_layer, tgt_layer} == {"physical", "technology"}:
                warnings.append(
                    f"Triggering between Physical and Technology layers "
                    f"({src_layer} -> {tgt_layer}) may be valid for peer "
                    f"interactions, but consider using Serving instead."
                )
            else:
                warnings.append(
                    f"Triggering across layers ({src_layer} -> {tgt_layer}) is "
                    f"unusual. Consider adding an intermediary element at the "
                    f"layer boundary (e.g., a Service)."
                )

        return warnings

    # -- Private rule methods -----------------------------------------------

    def _same_layer_rules(self, source_type, target_type,
                          src_aspect, tgt_aspect, layer):
        results = []

        # Active -> Behaviour: Assignment
        if src_aspect == "active" and tgt_aspect == "behaviour":
            results.append({
                "type": "assignment",
                "tier": "standard",
                "description": f"{source_type} is assigned to perform {target_type}",
            })

        # Behaviour -> Passive: Access
        if src_aspect == "behaviour" and tgt_aspect == "passive":
            results.append({
                "type": "access",
                "tier": "standard",
                "description": f"{source_type} accesses {target_type} (read/write/readwrite)",
            })

        # Active -> Passive: Access (via active performing behaviour that accesses)
        if src_aspect == "active" and tgt_aspect == "passive":
            results.append({
                "type": "access",
                "tier": "derived",
                "description": f"{source_type} accesses {target_type} (derived through behaviour)",
            })

        # Same aspect: Composition, Aggregation
        if src_aspect == tgt_aspect and src_aspect in ("active", "behaviour", "passive"):
            results.append({
                "type": "composition",
                "tier": "standard",
                "description": f"{source_type} is composed of {target_type} (ownership)",
            })
            results.append({
                "type": "aggregation",
                "tier": "standard",
                "description": f"{source_type} aggregates {target_type} (grouping)",
            })

        # Behaviour -> Behaviour: Triggering, Flow, Serving
        if src_aspect == "behaviour" and tgt_aspect == "behaviour":
            results.append({
                "type": "triggering",
                "tier": "standard",
                "description": f"{source_type} triggers {target_type}",
            })
            results.append({
                "type": "flow",
                "tier": "standard",
                "description": f"Transfer of data/material from {source_type} to {target_type}",
            })
            results.append({
                "type": "serving",
                "tier": "standard",
                "description": f"{source_type} serves {target_type}",
            })
            # CMP-060: broad behaviour→behaviour realization (ArchiMate 3.2 §5.3.1)
            # Any behaviour element can realize another behaviour (e.g. process realises service,
            # function realises service, interaction realises service, event realises event)
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": f"{source_type} realises {target_type}",
            })

        # Active -> Active (same layer): Serving, Triggering, Flow.
        # ArchiMate 3.2 Appendix B permits the dynamic and dependency
        # relationships between active structure elements of one layer (an
        # application component serving, triggering or passing data to another,
        # an interface serving the component that uses it) -- the basis of the
        # Application Cooperation viewpoint. The Appendix B table in
        # app/config/archimate_relationship_matrix.py already lists them; without
        # this rule the Composer refused the most common application-landscape
        # connection there is.
        if src_aspect == "active" and tgt_aspect == "active":
            results.append({
                "type": "serving",
                "tier": "standard",
                "description": f"{source_type} serves {target_type}",
            })
            results.append({
                "type": "flow",
                "tier": "standard",
                "description": f"Transfer of data from {source_type} to {target_type}",
            })
            results.append({
                "type": "triggering",
                "tier": "standard",
                "description": f"{source_type} triggers {target_type}",
            })

        # Behaviour -> Active: Serving. A service serves the active structure
        # element that uses it (ArchiMate 3.2 §5.4.1, e.g. an application
        # service serves an application component).
        if src_aspect == "behaviour" and tgt_aspect == "active":
            results.append({
                "type": "serving",
                "tier": "standard",
                "description": f"{source_type} serves {target_type}",
            })

        # CMP-060: active→behaviour realization (ArchiMate 3.2 §5.3.1)
        # An active structure element can realize a behaviour element of the same layer
        # (e.g. ApplicationComponent realizes ApplicationService,
        #       Node realizes TechnologyService)
        if src_aspect == "active" and tgt_aspect == "behaviour":
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": f"{source_type} realises {target_type} (active structure realises behaviour)",
            })

        # Specialization is valid between same-type elements
        if source_type == target_type:
            results.append({
                "type": "specialization",
                "tier": "standard",
                "description": f"{source_type} is a specialization of another {target_type}",
            })

        return results

    def _cross_layer_rules(self, source_type, target_type,
                           src_layer, tgt_layer, src_aspect, tgt_aspect):
        results = []

        # Layer ordering: physical = technology < application < business
        # Physical and Technology are peer layers at rank 0 (adjacent to Application)
        layer_rank = {
            "physical": 0, "technology": 0, "application": 1, "business": 2,
            "motivation": 3, "strategy": 3, "implementation": -1,
        }
        src_rank = layer_rank.get(src_layer, -1)
        tgt_rank = layer_rank.get(tgt_layer, -1)

        # Realisation: lower layer realises upper layer
        if src_rank < tgt_rank and src_rank >= 0 and tgt_rank >= 0:
            # Only between adjacent layers or same-aspect
            if abs(src_rank - tgt_rank) == 1:
                results.append({
                    "type": "realization",
                    "tier": "standard",
                    "description": f"{source_type} ({src_layer}) realises {target_type} ({tgt_layer})",
                })

        # Serving: lower layer serves upper layer
        if src_rank < tgt_rank and src_rank >= 0:
            if abs(src_rank - tgt_rank) == 1:
                results.append({
                    "type": "serving",
                    "tier": "standard",
                    "description": f"{source_type} ({src_layer}) serves {target_type} ({tgt_layer})",
                })
            elif abs(src_rank - tgt_rank) > 1:
                results.append({
                    "type": "serving",
                    "tier": "derived",
                    "description": (
                        f"{source_type} ({src_layer}) serves {target_type} ({tgt_layer}) — "
                        f"consider adding an intermediary in the {list(layer_rank.keys())[src_rank + 1]} layer"
                    ),
                })

        # Upper serving lower is also valid (business using application)
        if src_rank > tgt_rank and tgt_rank >= 0 and src_rank <= 2:
            if abs(src_rank - tgt_rank) == 1:
                results.append({
                    "type": "serving",
                    "tier": "derived",
                    "description": (
                        f"Serving typically flows from provider (lower) to consumer (upper). "
                        f"Check direction: should {target_type} serve {source_type}?"
                    ),
                })

        # Triggering across layers — derived only
        if src_layer != tgt_layer and src_aspect == "behaviour" and tgt_aspect == "behaviour":
            if abs(src_rank - tgt_rank) == 1:
                results.append({
                    "type": "triggering",
                    "tier": "derived",
                    "description": (
                        f"Cross-layer triggering ({src_layer} -> {tgt_layer}). "
                        f"Consider using a Service at the boundary."
                    ),
                })

        # Flow across layers — derived
        if src_layer != tgt_layer and src_aspect == "behaviour" and tgt_aspect == "behaviour":
            results.append({
                "type": "flow",
                "tier": "derived",
                "description": f"Cross-layer flow from {source_type} to {target_type}",
            })

        # Physical <-> Technology peer-layer rules (ArchiMate 3.2 §11)
        # Physical and Technology are peer layers — both can serve and realise each other.
        if (src_layer == "physical" and tgt_layer == "technology") or \
           (src_layer == "technology" and tgt_layer == "physical"):
            results.append({
                "type": "serving",
                "tier": "standard",
                "description": (
                    f"{source_type} ({src_layer}) serves "
                    f"{target_type} ({tgt_layer})"
                ),
            })
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": (
                    f"{source_type} ({src_layer}) realises "
                    f"{target_type} ({tgt_layer})"
                ),
            })

        # Physical -> Application: Realization (e.g., Facility realises Node which realises AppComponent)
        if src_layer == "physical" and tgt_layer == "application":
            results.append({
                "type": "realization",
                "tier": "derived",
                "description": (
                    f"{source_type} ({src_layer}) realises {target_type} ({tgt_layer}) — "
                    f"consider adding a Technology intermediary (e.g., Node)"
                ),
            })

        return results

    def _motivation_rules(self, source_type, target_type):
        """Motivation layer has its own relationship patterns."""
        results = []

        # Influence is the primary motivation relationship
        results.append({
            "type": "influence",
            "tier": "standard",
            "description": f"{source_type} influences {target_type}",
        })

        # Realisation within motivation (Requirement realises Goal, etc.)
        realization_pairs = {
            ("Requirement", "Goal"), ("Requirement", "Outcome"),
            ("Constraint", "Principle"), ("CourseOfAction", "Goal"),
            ("CourseOfAction", "Outcome"),
        }
        if (source_type, target_type) in realization_pairs:
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": f"{source_type} realises {target_type}",
            })

        # Composition/Aggregation/Specialization within motivation
        same_type_composable = {
            "Goal", "Requirement", "Constraint", "Principle", "Outcome",
        }
        if source_type == target_type and source_type in same_type_composable:
            results.append({
                "type": "specialization",
                "tier": "standard",
                "description": f"{source_type} is a specialization of another {target_type}",
            })
            results.append({
                "type": "composition",
                "tier": "standard",
                "description": f"{source_type} is composed of sub-{target_type}s",
            })
            results.append({
                "type": "aggregation",
                "tier": "standard",
                "description": f"{source_type} aggregates {target_type}s",
            })

        # Stakeholder -> Driver: Association (stakeholder has driver)
        if source_type == "Stakeholder" and target_type == "Driver":
            results.append({
                "type": "association",
                "tier": "standard",
                "description": "Stakeholder is associated with this Driver",
            })

        # Driver -> Assessment
        if source_type == "Driver" and target_type == "Assessment":
            results.append({
                "type": "association",
                "tier": "standard",
                "description": "Driver is assessed by this Assessment",
            })

        # Assessment -> Goal
        if source_type == "Assessment" and target_type == "Goal":
            results.append({
                "type": "association",
                "tier": "standard",
                "description": "Assessment leads to this Goal",
            })

        # NOTE (O-01): a "Goal -> Principle/Requirement: realization" rule used to live
        # here, granting the exact inverse of the `realization_pairs` set above (which
        # correctly encodes Requirement -> Goal). Per ArchiMate 3.2 §5.1.3, realization
        # points at the MORE ABSTRACT entity being realised — a Requirement realises a
        # Goal, never the reverse — so this was the motivation-layer instance of the
        # same per-direction drift C-01 fixed for the cross-layer matrix. It bypassed
        # permits_realization_target() because the motivation branch returns before
        # that check runs, so it was never caught by that guard. Removed rather than
        # corrected-in-place because realization_pairs already states the right
        # direction; keeping both was how the drift happened.

        return results

    def _strategy_rules(self, source_type, target_type, src_layer, tgt_layer):
        """Strategy layer relationship patterns.

        Canvas relationship keys ("aggregation","strategy","strategy") and
        ("assignment","strategy","strategy") added to VALID_RELATIONSHIPS
        (app/models/archimate_core.py) need no new rule here: the same-type
        block below already grants aggregation between two elements of the
        same strategy type (Capability/Capability, Resource/Resource — the
        "hierarchies" ArchiMate 3.2 §5.1.2 describes), and the Resource ->
        Capability assignment rule two lines down already grants assignment
        for that pair (§7.4). The six new "association" keys (option ↔ plan
        item, outcome ↔ work package, key partner ↔ resource/capability)
        likewise need no rule here: ``is_valid`` treats association as
        always valid (ArchiMate 3.2 §5.2.4: "association may connect any two
        concepts"), unconditionally, before any layer rule runs.
        """
        results = []

        # Within strategy layer
        if src_layer == "strategy" and tgt_layer == "strategy":
            # Resource -> Capability: Assignment
            if source_type == "Resource" and target_type == "Capability":
                results.append({
                    "type": "assignment",
                    "tier": "standard",
                    "description": "Resource is assigned to this Capability",
                })
            # Capability -> ValueStream: Serving
            if source_type == "Capability" and target_type == "ValueStream":
                results.append({
                    "type": "serving",
                    "tier": "standard",
                    "description": "Capability serves this Value Stream",
                })
            # CourseOfAction -> Capability: Realization
            if source_type == "CourseOfAction" and target_type == "Capability":
                results.append({
                    "type": "realization",
                    "tier": "standard",
                    "description": "Course of Action realises this Capability",
                })
            # Same-type composition and specialization
            if source_type == target_type:
                results.append({
                    "type": "specialization",
                    "tier": "standard",
                    "description": f"{source_type} is a specialization of another {target_type}",
                })
                results.append({
                    "type": "composition",
                    "tier": "standard",
                    "description": f"{source_type} composed of sub-{target_type}",
                })
                results.append({
                    "type": "aggregation",
                    "tier": "standard",
                    "description": f"{source_type} aggregates {target_type}",
                })

        # Strategy -> Core layers: Realisation and Influence
        if src_layer == "strategy" and tgt_layer in ("business", "application", "technology", "physical"):
            results.append({
                "type": "realization",
                "tier": "derived",
                "description": (
                    f"{source_type} is realised by elements in the {tgt_layer} layer "
                    f"(consider intermediary)"
                ),
            })
            results.append({
                "type": "influence",
                "tier": "standard",
                "description": f"{source_type} (strategy) influences {target_type} ({tgt_layer})",
            })

        # Core layers -> Strategy: Realisation
        if src_layer in ("business", "application", "technology", "physical") and tgt_layer == "strategy":
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": f"{source_type} realises {target_type}",
            })

        # Strategy -> Motivation: Realisation
        if src_layer == "strategy" and tgt_layer == "motivation":
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": f"{source_type} realises {target_type}",
            })

        return results

    def _implementation_rules(self, source_type, target_type, src_layer, tgt_layer):
        """Implementation & Migration layer relationship patterns."""
        results = []

        # Within implementation layer
        if src_layer == "implementation" and tgt_layer == "implementation":
            # WorkPackage composition
            if source_type == "WorkPackage" and target_type == "WorkPackage":
                results.append({
                    "type": "composition",
                    "tier": "standard",
                    "description": "Work Package is composed of sub-Work Packages",
                })
                results.append({
                    "type": "aggregation",
                    "tier": "standard",
                    "description": "Work Package aggregates other Work Packages",
                })
            # WorkPackage -> Deliverable: Realisation
            if source_type == "WorkPackage" and target_type == "Deliverable":
                results.append({
                    "type": "realization",
                    "tier": "standard",
                    "description": "Work Package produces this Deliverable",
                })
            # Triggering between work packages
            if source_type == "WorkPackage" and target_type == "WorkPackage":
                results.append({
                    "type": "triggering",
                    "tier": "standard",
                    "description": "Work Package triggers another Work Package",
                })
            # Plateau -> Gap
            if source_type == "Plateau" and target_type == "Gap":
                results.append({
                    "type": "association",
                    "tier": "standard",
                    "description": "Plateau identifies this Gap",
                })
            # Plateau composition
            if source_type == "Plateau" and target_type == "Plateau":
                results.append({
                    "type": "composition",
                    "tier": "standard",
                    "description": "Plateau composed of sub-Plateaus",
                })

        # Core layers -> Implementation: Realisation
        if src_layer in ("business", "application", "technology", "physical") and tgt_layer == "implementation":
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": f"{source_type} is realised by {target_type}",
            })

        # Implementation -> Core layers: Realisation
        if src_layer == "implementation" and tgt_layer in ("business", "application", "technology", "physical"):
            results.append({
                "type": "realization",
                "tier": "standard",
                "description": f"{source_type} realises {target_type}",
            })

        return results

    # -- Unified entry points ------------------------------------------------

    def validate(self, source_type, target_type, relationship_type=None):
        """Unified validation entry point.

        Returns dict with:
            valid: bool — whether the relationship/element combination is valid
            relationships: list — valid relationship types (if no specific type given)
            warnings: list — practitioner warnings
            tier: str — 'standard', 'derived', or 'fallback'
        """
        result = {
            "valid": False,
            "relationships": [],
            "warnings": [],
            "tier": "unknown",
        }

        if relationship_type:
            result["valid"] = self.is_valid(source_type, target_type, relationship_type)
            result["warnings"] = self.get_practitioner_warnings(
                source_type, target_type, relationship_type
            )
            # Determine tier
            valid_rels = self.get_valid_relationships(source_type, target_type)
            for r in valid_rels:
                if r["type"] == relationship_type:
                    result["tier"] = r["tier"]
                    break
        else:
            result["relationships"] = self.get_valid_relationships(source_type, target_type)
            result["valid"] = len(result["relationships"]) > 0

        return result

    def validate_element_type(self, element_type):
        """Check if an element type is a known ArchiMate 3.2 element."""
        normalized = _normalize_type(element_type)
        return normalized in _TYPE_LAYER
