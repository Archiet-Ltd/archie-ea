# ADR 0016 — One drawing engine for every notation a product page needs

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

A Product's page (ADR 0015) lists its diagrams from `SavedDiagram`. The organisation also wants the product's
workflows as BPMN 2.0, its roles and journeys as a use-case view, its journeys as sequence diagrams, its data as an
entity relationship diagram (ERD) and, where a repository supplies classes, a class diagram. The code already holds
several drawing implementations for one model (inventory below). Before any diagram work is briefed, one engine and
one store are named.

## Decision

**Engine.** JointJS (`app/static/vendor/joint.min.js`) is the drawing library for every notation. The canonical
ArchiMate engine is `ComposerRenderer` (`app/static/js/archimate/composer_renderer.js`, register entry
`archimate-drawing-engine`). A notation that ArchiMate cannot draw is a sibling module that loads on the same
library and the same page chrome (`app/templates/components/diagram/`, tokens `--notation-*`); it does not add a
second library. Siblings that exist today: `bpmn/bpmn_editor.js` (BPMN) and `data/erd.js` (ERD). Siblings to add:
a sequence view and a use-case view. Class diagrams keep `codegen/uml_shapes.js`, which is also JointJS.

**Store.** The model is the store. There is no diagram table of a second kind.
- ArchiMate views: `SavedDiagram`, `SavedDiagramElement`, `SavedDiagramRelationship` keep only layout; the
  elements and relationships are the model's.
- BPMN: the flow stays on `BusinessProcess.bpmn_flow` (the process is an ArchiMate element; lanes are its
  BusinessRole elements). This is the one exception to "layout only": ArchiMate has no pools, gateways typed by
  behaviour, or message flows, so the flow document is the source for the notation. No second copy is made.
- ERD: `DataEntity`, `DataEntityAttribute`, `DataEntityRelationship` through `data_erd_service`
  (register entry `data-model-diagram`).
- Sequence, use case, class (from a repository): derived at read time from the model, never stored.

## Notation map

| Notation | Source in the model | Engine | Editable? | Shows on the product page |
|---|---|---|---|---|
| ArchiMate view | elements and relationships of the product's members | `ComposerRenderer` | Yes, in the composer | Diagrams section, from `SavedDiagram` |
| BPMN 2.0 | `BusinessProcess.bpmn_flow` of each journey; lanes are BusinessRole | `bpmn_editor.js` on JointJS | Yes (the flow is the source); XML at `/processes/<id>/bpmn.xml` | One diagram per journey, link to the process page |
| UML sequence | a journey's flow steps, plus the application components and services joined to each step | new sibling, read only | No: "edit the model" links to the process and the component | Under the journey |
| UML use case | the product's BusinessRole elements and the journeys assigned to them | new sibling, read only | No: link to the roles | Roles section |
| ERD | the product's Data objects and their `DataEntity` records | `data/erd.js` | Yes in the data domain page; read only on the product page | Data section |
| UML class | repository classes only (`UMLModel` / `UMLElement`, enrichment snapshot) | `codegen/uml_shapes.js` | No | Only when a repository supplies classes; lowest priority |

Gap found: a flow step carries no link to an application component (a node has only `processId`). The sequence view
needs that link as an ArchiMate relationship (component serves the process or step), not a field on the flow.

## Inventory of drawing code on the line

| Path | Called from | Verdict |
|---|---|---|
| `static/js/archimate/composer_renderer.js` (+ `composer*.js`, `composer_shell.js`) | composer, blueprint, solutions detail, workbench, assistant journey, applications list | **Keep** (the engine) |
| `static/js/bpmn/bpmn_editor.js`, `modules/business_architecture/bpmn_service.py` | `business_architecture/process_diagram.html`; `/processes/<id>/bpmn` routes | **Keep**; extend to load shared chrome |
| `static/js/data/erd.js`, `modules/architecture/services/data_erd_service.py` | `data_architecture/entity_diagram.html` | **Keep**; extend with a read-only mode |
| `static/js/codegen/uml_shapes.js`, `modules/codegen/services/uml_enrichment_service.py` | `codegen/workbench.html` | **Keep** for class diagrams |
| `static/js/impact_graph.js`, `services/model_map.py` | `architecture/impact_graph.html` | **Keep**: a different concept (force graph) |
| `static/js/codegen/workflow_designer.js`, `workflow_shapes.js` | `codegen/workflow_designer.html` | **Freeze**: designs code workflows, not a notation of the model |
| `static/js/codegen/workflow_builder.js` | nothing loads it | **Freeze**, then delete in its own change |
| `static/js/archimate_diagram_renderer.js` | nothing loads it; listed as a register exception | **Freeze**, then delete in its own change |
| `static/js/solution_composer_cytoscape.js` | nothing loads it; Cytoscape is not vendored | **Freeze**, then delete in its own change |
| `services/mermaid_diagram_generator.py` | one test only | **Freeze**, then delete with that test's cases moved |
| `services/composer_export_formats.py` (`to_mermaid`, Lucid, Archi) | composer export, business case and model canvas exports | **Keep** as export formats, not as an on-screen renderer |
| `static/vendor/mermaid.min.js`, `applications/dashboard.html` | applications dashboard (`mermaid_diagrams` is passed empty) | **Freeze**: no new Mermaid on screen |
| `modules/ai_chat/.../visual_generation_service.py`, `structured_deliverable_service.py`, `generate_routes.py` | chat "visual" replies (Mermaid or PlantUML text) | **Freeze**: text for chat, never a product-page diagram |
| `modules/solutions_product/services/deterministic_code_generator.py` (`_build_mermaid_diagram`), `product_spec_bundle.py` (`SequenceDiagramDef`) | generated code and spec bundles | **Freeze**: generator output, not a screen |
| `models/models.py` `UMLModel` (stub: name, `mermaid_source`, `plantuml_source`, `svg_output`) | codegen only | **Freeze**: no new rows for product diagrams |
| `static/js/solutions/traceability_sankey.js`, `archimate_views/traceability_sankey.js`, `organization/org_chart.js`, `capability_map/network.html`, `solutions/program_view.html`, `stakeholders/map.html` | their own pages | **Freeze**: charts and force graphs, not notations; moves follow register entry `force-graph` |

## Consequences

- No product-page diagram brief may add a library, a diagram table or an inline `<svg>` drawing; it adds a sibling
  module and derives its data from the model.
- Build order, each one slice: (1) the product page lists a BPMN diagram per journey (reads `bpmn_flow`, links to
  the process page); (2) the step-to-component relationship and its writer; (3) the read-only sequence sibling;
  (4) the use-case sibling; (5) the ERD on the product page, read only; (6) deletion of the four unreferenced
  files, one change each; (7) class diagram from a repository, last.
- Derived views carry the "edit the model" link to the element or process that holds the fact, so a diagram cannot
  differ from the model.
