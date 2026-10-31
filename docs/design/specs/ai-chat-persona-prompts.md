# AI chat persona prompts - redesign spec

Block: Settings. Layout follows the shadcn playground example: the prompt editor is the main column, the persona's
settings sit in a side column at desktop width and stack beneath it on a phone.

## Job
A platform administrator edits the system prompt a chat persona uses, and the chat uses it.

## What moves
- The table plus modal become one form: System prompt card (main column, Save changes and Reset to defaults in its
  footer), Persona card (Persona select, status badge, Description) and Sample prompts card (side column).
- Every field has its label: Persona, System prompt, Description, and a labelled input per sample prompt.
- Reset to defaults asks first, in words.

## What merges
- Nothing.

## What is removed
- Nothing is hidden. The icon and domain columns of the old table are not settings and were not editable.

## States
- Loading: the select fills when the personas arrive. Empty sample prompts say so and offer Add prompt.
- Error: a refused save is explained in a banner and keeps what was typed. Access denied is stated.
- Saved: a status banner says the persona was updated and that new chats use it; the badge reads Overridden.

## Journey that proves it
An administrator picks a persona, writes a system prompt, saves, reloads and reads it back, and the chat's prompt
builder returns that prompt for the persona; an empty save is refused in words; Reset returns the built-in prompt.

Every control is a component from the shared library: no hand-written button, input, select, textarea or dialog, no raw
colour, no shadow and no inline style. Judged at 1440 and 390 wide, in light and dark.
