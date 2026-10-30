# Team: members, invitations and roles - redesign spec

Block: Data table. Two tables (invitations, members) following the shadcn tasks
example (https://ui.shadcn.com/examples/tasks).

## Job

An admin invites a teammate, sees who has joined, and changes a member's role.

## What moves

- The invite and per-row role-change forms' hidden CSRF, user id and role fields are the shared
  hidden-input macro in place of hand-written hidden inputs; the invite and member tables were
  already on the list table before this round.

## What merges

- Nothing.

## What is removed

- Nothing is hidden.

## States

- Empty, no invitations: the Invitations section does not render at all.
- Empty, no mail configured: a plain notice replaces the invite description.
- Error: an invite that fails shows the reason above the form, with what was typed kept.

## Journey that proves it

An admin invites a teammate with a chosen role; the new row appears in Invitations not yet
accepted with that role shown, not a default.

Every control on the page is a component from the shared library: no hand-written button, input,
select, table or dialog, no raw colour, no shadow and no inline style. The page is judged at 1440
and 390 wide, in light and dark.
