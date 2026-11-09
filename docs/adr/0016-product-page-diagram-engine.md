# ADR 0016 — One drawing engine and one diagram store for every notation on a product page

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

The founder asked whether a product's page needs BPMN 2.0, ERD, class, use-case and sequence diagrams, and
whether they reuse the ArchiMate composer's engine. A grep of `app/` for every drawing, rendering and
diagram-storage implementation turns up five live surfaces and three dead ones (full inventory in the launch-cut
report, same date). Three of the five live surfaces already load the same vendor stack —
`vendor/joint.min.js` (JointJS) plus `vendor/dagre.min.js` (auto-layout) — through three separate wrapper
scripts: the composer (`app/static/js/archimate/composer.js` + `composer_renderer.js`/`composer_graph.js`),
the BPMN flow editor (`app/static/js/bpmn/bpmn_editor.js`), and the data-entity ERD page
(`app/static/js/data/erd.js`). The composer's canvas already has a working precedent for a *derived* notation:
`layoutSequence()` in `composer.js` re-lays out the elements and relationships already on the canvas into a
lifeline/message view, ordered by `ArchiMateRelationship.sequence_order`
(`app/models/archimate_core.py`), with no second store — the comment there already cites ADR 0008. No
equivalent layout mode exists yet for use case, ERD or class.

## Decision

**One engine, by precedent already proven once.** The composer's canvas (JointJS + dagre, driven by
`composer.js`'s layout-mode pattern) is the engine for every *derived* notation. A derived notation is a new
layout mode in `composer.js` that feeds it a different slice of the model — not a new canvas, not a new
wrapper script. `layoutSequence()` is the pattern to copy, not a one-off.

**One store, because most of these notations need none.** `SavedDiagram` / `SavedDiagramElement` /
`SavedDiagramRelationship` (`app/models/archimate_core.py`) stays the only persisted diagram store, for
ArchiMate composer views and any view a person explicitly saves. A derived notation (sequence, use case, ERD,
class) is computed from the model on every view and rendered through a composer layout mode; it is not written
to `SavedDiagram`. A notation that is itself editable model data — the BPMN flow — keeps living on the model
row it belongs to (`BusinessProcess.bpmn_flow`), because the flow *is* the model, not a cache of it; that is
ADR 0008's rule applied here, not an exception to it. BPMN keeps its own editor
(`bpmn_editor.js`) because it is the authoring surface for that source data, not a second engine for a derived
view.

**Per notation:**

| Notation | Source in the model | Engine | Editable or derived | On the product page |
|---|---|---|---|---|
| ArchiMate view | Elements/relationships of the product's members | Composer canvas | Editable | Linked material — already live (`product_page.py`) |
| BPMN 2.0 (journey) | `BusinessProcess.bpmn_flow` of a journey assigned to the product | BPMN flow editor | Editable (source = the flow) | New: Journeys section, read-only embed + "open in editor" |
| UML sequence | A journey's ordered steps + the services/components serving each (new: a step→service serving relationship, ordered) | Composer canvas, new layout mode (extends `layoutSequence()`) | Derived, read-only, "edit the model" link | New: Journeys section, per-journey tab |
| Use case | Business roles (already assigned to BPMN lanes by `Assignment`) × journeys assigned to them | Composer canvas, new layout mode | Derived, read-only | New: Roles section |
| ERD | `DataEntity`/`DataEntityRelationship` of the product's Data objects (already keyed to the ArchiMate data object via `archimate_element_id`); after a repository connector lands, also the repository schema | Composer canvas, new layout mode, replacing the ERD page's own JointJS wiring | Derived, read-only | New: Data section |
| Class | Classes a connected repository supplies (no connector exists yet) | Composer canvas, new layout mode, lowest priority | Derived, read-only | New: Code section, once a repository connector exists |

**What is frozen, and why it cannot hold for one notation.** The ERD page's own JointJS wiring
(`app/static/js/data/erd.js`, `app/templates/data_architecture/entity_diagram.html`) is a second wrapper around
the same engine and is frozen: no further feature work beyond what exists, until it is rebuilt as a composer
layout mode. `UMLModel`/`UMLElement` (`app/models/models.py`) is dead — a stub store with no reader anywhere in
`app/` — and is not the system of record for the class or sequence notations; it is frozen rather than reused.
Codegen's `class_diagram`/`sequence_diagram` JSON (`app/modules/codegen/services/*`) is a different concept — an
LLM-authored code-generation plan, never rendered as a diagram — and is out of scope; it may become a class-diagram
*data source* once a repository connector supplies real classes, but it is not an engine or a store for this
decision.

## Consequences

No new diagram-storage table is created by the briefs this ADR implies; every new notation is a projection, not
a new authority, so `store-agreement` has nothing new to disagree about. The composer's layout-mode pattern
means each notation brief is small (a data query + a layout function), not a new rendering stack. BPMN stays the
one place a journey's flow is authored. The ERD page's freeze is a visible, reviewable debt — it is not deleted,
and its data model (`DataEntity`/`DataEntityRelationship`) is correct and stays; only its rendering wrapper is
superseded once a build brief rebuilds it on the composer.
