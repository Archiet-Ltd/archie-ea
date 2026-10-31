# Administration: forms and row actions - redesign spec

Covers single sign-on group mappings, the GitHub import review, the connectors list, the application report, an
organisation's detail page and the consolidation status. Settings pages follow the shadcn forms example
(https://ui.shadcn.com/examples/forms); row actions follow the tasks example (https://ui.shadcn.com/examples/tasks).

## Job
An administrator changes a setting or acts on one row and sees at once that it took effect.

## What moves
- Every field is a library field with its label; every button is a library button; hidden values use the library
  hidden input. A form submits from the keyboard.
- A row's actions sit in one menu at the end of the row. Destructive actions are the last item, behind a confirmation.
- The progress bar on the consolidation status is the library progress component.

## What merges
- Nothing. Each page keeps its one table.

## What is removed
- Nothing is hidden. Every action that was available stays available.

## States
- Empty: each list says why it is empty and what to do next.
- Error: a refused action returns to the page with a plain message.

## Journeys that prove it
An administrator saves an SSO group mapping and disables it again; reviews a GitHub import, skips one row and accepts
the rest; builds and runs a report and sees its rows; opens an organisation and uses every action in a row menu.

Every control on these pages is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. Judged at 1440 and 390 wide, in light and dark.
