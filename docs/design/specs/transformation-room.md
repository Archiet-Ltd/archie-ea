# Transformation room: stage pages on library parts - rebuild spec

Block: Record detail. One programme, seen one stage at a time, following the shadcn mail reading pane and the
flask-basecoat product detail example.

## Job
A CTO opens a programme's room, reads where the change stands and moves to the next stage without losing their place.

## What moves
- The room's two rows of sections (Transform, Decisions, Execute, Outcomes; then the seven workstream stages) are the
  library's section tabs: one row, read left to right, scrolling sideways on a phone and never stacking.
- Every stage page sits in the library card. The workstream list is the library list table; adding a workstream and
  saving the objective use the library's fields and buttons.

## What merges
- The programme context card is shown once, above every stage.

## What is removed
- Nothing is hidden. Stages that are not available in this release keep their reason and now offer the next step: a
  button back to the objective (or the programme overview).

## States
- Empty: a programme with no workstreams says so and points at the form below the table.
- Error: a refused save shows the reason in an alert above the form and keeps the text entered.

## Journey that proves it
A CTO creates a programme, steps across all seven stages with the section tabs at 1440 and 390 wide, takes the next
step from every unavailable stage, then adds a workstream from the workstreams table page and opens it from the list.

Every control on these pages is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style.
