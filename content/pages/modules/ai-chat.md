---
wave: 2
page_family: module
module_label: "AI Chat"
endpoint: unified_ai_chat.index
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: briefed
answers_use_cases:
  - {id: UC-S3-10, segment: S3}
cta: plans
capture_status: live
icon: message-square
headline: "Ask about your organisation and read the records behind the answer"
summary: "Ask what breaks if a system fails, who owns it, which risks reach it or what it costs. Every answer cites the records it came from."
who_for:
  - "An architect, CTO or portfolio manager who needs an answer in a meeting, not after a week of collecting."
  - "A team lead who wants to check an answer against the record it came from."
  - "An administrator who needs every suggested change to wait for a person's approval."
questions:
  - {q: "What breaks if the Payments service fails?", who: "a CTO during an incident review"}
  - {q: "Who owns this application and what does it cost?", who: "a portfolio manager"}
  - {q: "Which risks reach this system?", who: "a risk owner"}
  - {q: "Which of our suppliers does this depend on?", who: "a procurement lead"}
steps:
  - {name: "Ask in plain words", text: "Type the question the way you would put it to a colleague."}
  - {name: "Read the cited answer", text: "The answer names the records it was built from, so you can open and check each one."}
  - {name: "Review any suggested change", text: "A change the assistant suggests waits in your approval queue."}
  - {name: "Approve or decline", text: "Nothing in your model changes until a person approves it."}
---

# AI Chat

*Ask about your organisation in plain words and read an answer that names the records behind it.*

## What it does

The AI chat is the one place to ask. Ask what breaks if a system fails, who owns it, which risks reach it or what it costs. The answer cites the records it came from, and any change it suggests waits in your approval queue until a person approves it.

Available now on every plan, including the free Community plan. [See plans](/pricing).

## Related

- [Let our AI assistant query the model instead of guessing](/use-cases/ai-assistant-for-your-architecture)
