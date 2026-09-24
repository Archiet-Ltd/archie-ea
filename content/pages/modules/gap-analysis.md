---
page_family: module
module_label: "Gap Analysis"
endpoint: enterprise.gap_analysis
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-13, segment: S3}
capture_status: awaiting_capture
---

# Gap Analysis

*The register of what's missing between today's estate and the target state — the record the
Roadmaps page's gap timeline is built from.*

## What this module does

Where Roadmaps shows the stage-by-stage picture, Gap Analysis is the underlying register: each gap
named, tracked, and linked to the projects closing it. One source both the standard roadmap view and
the tech-lead view read from, so they never disagree about what's actually missing.

## Where you'll meet it

- [Show the path from today's estate to the target state](/enterprise-architecture/target-state-roadmap)

## Related modules

- [Roadmaps](/modules/roadmaps)
- [Projects](/modules/projects)
