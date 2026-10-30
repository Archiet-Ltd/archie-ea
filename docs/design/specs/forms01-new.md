# New - form rebuild spec

Block: form. Follows shadcn examples: forms (https://ui.shadcn.com/examples/forms) and the compliant admin team and GitHub connector pages.

## Job
Register a new agent.

## What moves
- Every control is the library's field, input, select, textarea, checkbox or button inside a card in the page column, at most 2xl wide.
- Fields sit in one column, labelled, with the description under the label; the primary button is the only submit.

## What is removed
- Nothing is hidden. Field names and form actions are unchanged.

## States
- Missing required field: the browser's error state shows on the field and nothing is posted.
- Empty optional values read Not set.

## Journey that proves it
tests/smoke/test_shadcn_forms01_journeys.py
