# ServiceNow CMDB connector settings - redesign spec

Block: Settings. A status card and two settings cards in the page column, the same family as the GitHub connector
page (https://ui.shadcn.com/examples/forms).

## Job
A platform administrator connects a ServiceNow CMDB, is sure the change was saved or is waiting for approval, and can
see whether the connector is running.

## What moves
- The page is the column at most 42rem wide. The status card carries the state ("Connected", "Disabled" or "Not set"),
  the last sync, and the two actions: Check status and Sync now. Their result is a sentence under the state.
- Instance and OAuth2 is one card with three labelled fields; the Client secret says "Leave blank to keep the existing
  value". CMDB pull settings is the second card with the CI query filter, the field mapping and the Enable connector
  switch. Save configuration is the one primary action; Cancel returns to the connectors list.

## What merges
- Nothing is combined. The old "Test Connection" button only read the connector's status, so it is now named for
  what it does, Check status.

## What is removed
- Nothing is hidden.

## States
- Empty: "Not set"; Check status says it is not set up yet; Sync now says to save the connector first.
- Approval: an organisation that has not delegated connector changes is told the change is queued for approval, and the
  page keeps saying "Not set" until it is applied.
- Error: an address that is not an allowed https address and a field mapping that is not a JSON object are refused
  beside their field and everything typed is kept; nothing is proposed or stored.

## Journey that proves it
A platform administrator in a delegating organisation saves, reloads and reads every setting back with no secret in
the page; in a default organisation the save is queued for approval and says so; wrong values are refused in words.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
