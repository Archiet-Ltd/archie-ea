---
wave: 3
page_family: module
module_label: "Architecture Model"
endpoint: archimate_crud.dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-01, segment: S3}
capture_status: live
cta: plans
icon: boxes
headline: Every element of your architecture in one browser
summary: Business, application, technology and motivation layers in one page, with a filter that takes you straight to any layer. It is where an imported model lands.
who_for:
- An enterprise architect who has just imported a model and wants to see what came in.
- An architect who needs to find any element at any layer without opening a separate tool.
questions:
- q: What elements does our model hold at each layer?
  who: an enterprise architect after an import
- q: Which applications sit under this business process?
  who: a business architect
- q: Which goals and drivers is this change meant to serve?
  who: a solution architect
- q: Did the import bring in everything in the file?
  who: an architect checking a migration
steps:
- name: Bring in your model
  text: Import an existing ArchiMate model, or build it from GitHub or Jira.
- name: Browse the layers
  text: Business, application, technology and the goals and drivers behind them, in one list.
- name: Filter to a layer
  text: Go straight to the layer you need.
- name: Open an element
  text: Read its record and what it connects to.
---

# Architecture Model

*One browser for every element of your architecture, at every layer — the page an imported model
lands in.*

## What this module does

Every layer of your architecture — business, application, technology, and the goals and drivers
behind them — in one browsable page, with a filter that takes you straight to any layer without it
needing to be a separate destination.

## Where you'll meet it

- [Import our existing model](/use-cases/import-archimate-model)

## Related modules

- [Diagrams & Composer](/modules/diagrams)
