---
wave: 3
page_family: module
module_label: "Diagrams & Composer"
endpoint: archimate.diagrams_library
grouped_sub_pages: [archimate.composer_page]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-07, segment: S2}
capture_status: live
cta: plans
icon: network
---

# Diagrams & Composer

*A library of the diagrams you have saved, and a composer for building ArchiMate views from your
model's elements and exporting them.*

## What this module does

The library lists your saved diagrams across your solutions, most recently updated first and capped at the latest 200, searchable by name and filterable by
solution, and opens each one in the composer. The composer builds views from the elements already
in your model, either on a blank canvas or from a viewpoint, and saves them back to the library. A
link can open the composer on a specific saved diagram, a viewpoint or a solution.

Views export as ArchiMate Exchange XML, and the composer can also export its live canvas as
Mermaid, Lucidchart or Archi files. The Twin map is its own page; it is not saved to this library
and has no link into the composer.

## Where you'll meet it

- [Give the acquirer a current architecture picture we didn't draw by hand](/use-cases/architecture-map-for-due-diligence)

## Related modules

- [Architecture Model](/modules/architecture-model)
