"""The one sentence a person sees when the assistant cannot answer.

Every customer-visible failure in the chat says this, so the wording cannot drift
between the streamed path, the plain path and the upload and matching routes.
Where to fix a provider is shown to an administrator as a link, never in this text.
"""

ASSISTANT_UNAVAILABLE = (
    "The assistant could not answer just now. "
    "Your question is saved; try again in a moment."
)
