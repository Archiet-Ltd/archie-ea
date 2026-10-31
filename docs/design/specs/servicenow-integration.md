# ServiceNow CMDB integration (second page) - redesign spec

Block: Settings. One column of two cards, each at most 42rem wide.

## Job
A platform administrator sees whether the ServiceNow integration is on, tests it, syncs now, and edits the instance,
account and query.

## What moves
- Sync status (state badge, instance, last sync, applications synced) with Test Connection and Sync Now in its footer.
- Configuration: instance URL, username, password or token, batch size, query filter and the Enable switch; one Save
  configuration button in the card footer.

## What merges
- The "what gets synced" list becomes one sentence in the status card.

## What is removed
- Nothing is hidden. The check-mark list and icons are dropped.

## States
- Empty: an unconfigured instance says what to do next and shows no controls that cannot work.
- The stored password is never written back; the field says it keeps the stored value when left blank.
- A count that cannot be read shows a dash, never a zero.

## Duplicate
ServiceNow is also configured at the connector page, which is the page of record. No route reaches this page.
Retiring it is a product-manager decision.

## Proof
No route reaches the page, so there is no browser journey. A render check covers labels, the password and the shared
parts.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
