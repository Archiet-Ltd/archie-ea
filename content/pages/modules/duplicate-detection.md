---
page_family: module
module_label: "Duplicate Detection"
endpoint: unified_duplicate.simple_dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-03, segment: S2}
capture_status: awaiting_capture
---

# Duplicate Detection

*The engine behind Rationalization — finds two applications doing the same job before you go looking
for them.*

## What this module does

Scans your application list for functional overlap and surfaces candidates automatically, feeding
straight into the Rationalization dashboard and a consolidation list. This is the detection step;
Rationalization is where you act on what it finds.

## Where you'll meet it

- [What are we paying for twice?](/scale-up/duplicate-spend)

## Related modules

- [Rationalization](/modules/rationalization)
- [Applications](/modules/applications)
