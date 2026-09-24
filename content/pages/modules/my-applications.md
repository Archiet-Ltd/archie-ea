---
page_family: module
module_label: "My Applications"
endpoint: my_applications.dashboard
grouped_sub_pages: [my_applications.app_list]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-10, segment: S2}
capture_status: awaiting_capture
---

# My Applications

*Not the whole portfolio — just what you personally own, and what's on the hook because of it.*

## What this module does

A dashboard and list scoped to you: the applications you're the recorded owner of, with health
status for each. The full Applications module is everyone's estate; this page is yours.

## Where you'll meet it

- [Show me what I own and what depends on it](/scale-up/what-i-own)

## Related modules

- [Applications](/modules/applications)
