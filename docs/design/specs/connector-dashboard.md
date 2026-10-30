# Connector health dashboard - redesign spec

Block: Dashboard. A stat row above a grid of connector cards, following the shadcn
dashboard-01 block (https://ui.shadcn.com/blocks#dashboard-01).

## Job

An admin sees at a glance how many connectors are configured and how many need attention, then
opens one to see its sync history.

## What moves

- A three-card stat row (Connectors, Active, Needs attention) sits above the grid, counted from
  the connectors the page already fetches -- no new endpoint, no invented figure.
- The connector detail panel is the shared dialog component; its two actions are the shared
  button component, in the card and in the dialog.

## What merges

- Nothing.

## What is removed

- Nothing is hidden. Sync stays a disabled, labelled control everywhere it was before.

## States

- Loading: the existing spinner and caption.
- Empty: no connectors configured, with the existing explanation.
- Error: the existing banner.
- Populated: the stat row and the cards agree with each other, since both read the one fetched list.

## Journey that proves it

An admin with one configured connector sees the stat row read 1 connector, 1 active, 0 needing
attention, opens it, and sees its real sync history in the dialog.

Every control on the page is a component from the shared library: no hand-written button, input,
select, table or dialog, no raw colour, no shadow and no inline style. The page is judged at 1440
and 390 wide, in light and dark.
