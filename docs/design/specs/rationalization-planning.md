# Application planning - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
A portfolio manager reads one application's score, reviews it, and records its replacement plan, retirement blockers
or a disposition override.

## What moves
- Four figures become the Stat card row: the portfolio's applications (the Applications list's own count), health score,
  decision readiness and review state.
- The score breakdown is the primary chart (radar with its figures table) beside the recommendation and data readiness.
- The review workflow, replacement plan and override are library cards and fields; the tabs are library buttons.
- Retirement blockers and dependency impact are two cards.

## What is fixed
- Missing values read "Not set" or "Not measured", never a dash.

## States
- No score: the figure cards say Not scored and what to do (Score this application).
- Empty retirement tab: says no blockers were found and where to import dependencies.

## Journey that proves it
tests/smoke/test_rationalization_family_journey.py: a row opened from the workbench lands on its planning page with
the health, review and application count figures and the dimension table.
