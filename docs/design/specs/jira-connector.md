# Jira ALM connector - redesign spec

Block: Settings. A single-column settings card, following the shadcn sidebar-08 inset section
pattern (https://ui.shadcn.com/blocks#sidebar-08), the same family as the GitHub connector page.

## Job

An admin saves Jira credentials and the integration they govern.

## What moves

- The instance URL, email, API token and project key fields, and the enable toggle, are the
  shared input, hidden-input and switch components, matching admin/connectors/github.html.

## What merges

- Nothing.

## What is removed

- Nothing is hidden.

## States

- Populated: existing stored config values are shown.
- Error: a missing instance URL or email is refused above the form, with everything else kept.
- Queued: a fresh organisation sees the change queued for approval, not applied, and the page says so.

## Journey that proves it

An admin fills in Jira credentials and saves; a fresh organisation sees the honest "queued for
approval" outcome, and the enable control is a real, keyboard-reachable switch.

Every control on the page is a component from the shared library: no hand-written button, input,
select, table or dialog, no raw colour, no shadow and no inline style. The page is judged at 1440
and 390 wide, in light and dark.
