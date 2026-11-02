# ADR 0013 — One footer for every ranked or computed result

- **Status:** Accepted
- **Date:** 2026-11-02

## Context

A ranked risk or a prioritised action is a computed answer. Before this change none of them showed how far to
trust it: the acceptance run found no confidence on any of its 13 results, and only the overview and the assistant
named a source. Three renderers could have grown into the answer: the provenance component, the assistant's
cited-answer provenance line, and the answer cascade (which carries a confidence of its own).

## Decision

The canonical footer is the `result_footer` macro in `components/provenance.html`, fed by
`app/services/result_footer_service.py`. The component was already the data-type register's one component for
"where a result came from"; the assistant's line and the cascade stay as they are and are repointed to it one area
at a time, each in its own change. Templates draw the footer and compute nothing.

The footer has four parts:

- **Evidence** — the facts the result was computed from, each a link to its record.
- **Source** — where each fact came from (entered by the team, imported, a connector, worked out by the product,
  sample data) and who.
- **Confidence** — high, medium or low by the rule below, with the reason printed and the full rule on hover.
- **Date** — when the result was computed, and the newest input's date.

## The confidence rule

- **Low** when an input is missing, or the result has no input facts. The footer names the missing input.
- **Medium** when nothing is missing but an input is older than 90 days, has no date, or was entered by hand or is
  sample data.
- **High** when every input has a recorded origin (imported, a connector, or worked out by the product) and a date
  within 90 days.

A risk's recorded score is entered by hand, so a risk linked to elements reads medium and a risk linked to nothing
reads low ("Linked elements: none recorded"). A prioritised action with no recorded severity or priority reads low
and says so.

## Isolation

Facts read from the model are fetched by id and organisation. An element that belongs to another organisation is
absent from the answer, so it appears in a footer only as a missing input, never by name or origin.

## Consequences

Every later surface that ranks or computes something builds its facts, calls `build_footer` and draws the macro.
A second "Source:" or "Confidence:" line beside it is refused by the reuse register.
