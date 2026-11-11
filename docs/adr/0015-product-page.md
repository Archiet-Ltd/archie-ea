# ADR 0015 — A Product's page is the element page, extended

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

A Product is an ArchiMate 3.2 element. Until now its page was the generic element page, which lists direct
connections only. The organisation wants one page per Product that shows everything that makes it up, by
ArchiMate layer, with its diagrams, decisions, repositories, owners, cost, gaps and risks. Several Products sit
under one organisation (the Product is the element; the mapped `Product` table holds a Product's own record, and there is no second organisation level).

## Decision

**What it extends.** `archimate_crud/detail.html`, the element page, reached at
`/architecture/<layer>/Product/<id>`. A Product gets extra sections on that page; no new page template and no
second element page. The Products list is the existing list for the Product type
(`archimate_crud.list_elements`); the sidebar gains one entry in the Model group that opens it.

**The relationship walk.** The one impact walk, `app.services.impact_graph.build_impact_graph` (the data behind
`/api/element/<id>/impact-graph`). The product page reads its nodes and edges and filters them; it adds no
traversal. A member of the page is an element joined to the Product by any one relationship (direct), or joined
to a direct member by composition, aggregation, assignment, realization or serving (one step outward). Depth is
a single constant, `OUTWARD_STEPS`.

**Owners and cost.** Owners come from the one owner record (`ApplicationOwner`, read through the element page's
existing owner resolver). Annual cost of an application comes from `application_cost_accessor.get_annual_cost`;
a technology element's cost from `technology_cost.allocation`. No total is shown unless every cost it sums is
recorded: a missing cost is named, never counted as zero.

**Linked material.** Diagrams from `SavedDiagram` through `SavedDiagramElement`; decision records from
`ArchitectureDecision.archimate_element_ids` (the one decision register); repositories from the connector
provenance every element already carries (`source_kind = connector`, `source_ref`), labelled by the result
footer's own connector label; documents from `ArchitectureDocument` of the applications among the members.

**Summary.** The cited answer the element page already works out (`element_page` connection lines and the result
footer of `result_footer_service`), not a model call. The existing cited-answer service computes from the model
and needs no AI provider, so the summary is present whether or not one is configured; this differs from the brief,
which assumed a provider, and avoids a second answer path.

**Export.** One JSON file of every section at `/architecture/products/<id>/export`, built from the same
data the page draws. It sits beside the existing `export` route of the same module.

**Add from an empty layer.** One form posting to one route. The element is written with
`archimate_backbone.create_backbone_element` and joined to the Product with `ArchiMateRelationshipService`, so
the grammar check is the one the rest of the product uses. Each layer's preset relationship is a pair the
ArchiMate 3.2 matrix allows.

## New implementations, with reasons

- `product_page.py` (the grouping, wording and cost/owner roll-up). The walk, owner, cost, footer and element
  writer are reused. The grouping by layer is new because no screen groups an element's neighbourhood by layer
  with the relationship in words.
- The add route. No existing route creates an element and its relationship in one step with the relationship
  grammar enforced.

## Consequences

Every figure on the page is traceable to a query over the member list; the test compares the page's members to the
relationship data. Organisation isolation: every element is loaded by id and organisation, so an edge into another
organisation's element lists nothing.
