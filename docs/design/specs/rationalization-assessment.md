# Rationalization assessment - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
An application manager answers the questions that feed the TIME score for one application.

## What moves
- Six steps in one page; each question is a labelled radio set or select; progress is a library progress bar.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- Not scored and Not set stand where a figure is missing.

## Journey that proves it
An application manager chooses an application, answers, submits and lands on the rationalization dashboard.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
