# EA Automated Workflows - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
An enterprise architect sees what is running, waiting for them, finished or failed, starts a scenario workflow and opens any recent run.

## What moves
- Four figures become the Stat card row: running, waiting approval, completed and failed, each with a sentence and no icons or coloured borders. Each count is the total the instances list reports for that status; it was a count of the newest twenty runs.
- The phase coverage matrix and Recent Runs are library tables with badges for phases and status; the scenarios, the ADM phase views and the journey iterations are library cards. The coloured icon tiles are gone.
- The start dialog is the library dialog on the library field and textarea; its fields and step preview are still filled in by the page script.

## What is fixed
- An organisation with no runs says why and offers Choose a workflow to start, which scrolls to the scenarios.
- The waiting card links to the approvals queue only when something is waiting, and says so when nothing is.

## Journey that proves it
tests/smoke/test_ea_workflows_dashboard_journey.py: each count equals the instances list's total for that status with more runs than the recent list holds; a recent run opens its page; the waiting card leads to the queue; four cards with no icons or coloured borders; a scenario Start opens and Cancel closes the dialog; an empty organisation says why; nothing overflows at 390.
