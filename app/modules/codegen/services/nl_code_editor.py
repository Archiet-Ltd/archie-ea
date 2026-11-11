"""Natural language code editing service — converts user instructions to unified diffs.

Orchestrates:
1. Context assembly (genome + current file + related files + conversation history)
2. LLM call with patch-only system prompt
3. Diff parsing and validation
4. Destructive action detection (schema drops, security downgrades)
"""

from __future__ import annotations

import re
import logging
from typing import Generator

logger = logging.getLogger(__name__)

# ── Destructive pattern guard ─────────────────────────────────────────────────

_DESTRUCTIVE_PATTERNS = [
    (re.compile(r"DROP\s+TABLE", re.I), "DROP TABLE detected — potential data loss"),
    (re.compile(r"allow_origins\s*=\s*\[[\'\"]?\*[\'\"]?\]", re.I), "CORS wildcard re-introduced"),
    (re.compile(r"verify\s*=\s*False", re.I), "SSL verification disabled"),
    (re.compile(r"DEBUG\s*=\s*True", re.I), "DEBUG=True in production code"),
    (re.compile(r"os\.system\s*\(", re.I), "os.system() call — command injection risk"),
    (re.compile(r"eval\s*\(", re.I), "eval() — code injection risk"),
    (re.compile(r"exec\s*\(", re.I), "exec() — code injection risk"),
]

MAX_CONTEXT_CHARS = 80_000  # ~20k tokens headroom


def _check_destructive(patch_content: str) -> list[str]:
    """Return list of warnings for dangerous patterns in a diff's added lines."""
    warnings = []
    for line in patch_content.splitlines():
        if not line.startswith("+"):
            continue
        for pattern, message in _DESTRUCTIVE_PATTERNS:
            if pattern.search(line):
                warnings.append(message)
    return list(set(warnings))


# ── Diff parsing ──────────────────────────────────────────────────────────────

_DIFF_HEADER_RE = re.compile(
    r"^(?:---\s+(.+?)(?:\t.*)?$\n\+\+\+\s+(.+?)(?:\t.*)?$)",
    re.MULTILINE,
)


def parse_patches(llm_response: str) -> list[dict]:
    """Extract per-file patches from LLM response.

    Returns list of {file: str, diff: str, warnings: list[str]}.
    LLM may return multiple ```diff ... ``` blocks or raw unified diff.
    """
    patches = []

    # Extract fenced diff blocks
    fenced = re.findall(r"```(?:diff|patch)?\n(.*?)```", llm_response, re.DOTALL)
    if fenced:
        for block in fenced:
            file_match = re.search(r"^---\s+(.+?)(?:\t.*)?$", block, re.MULTILINE)
            file_path = file_match.group(1).strip() if file_match else "unknown"
            file_path = re.sub(r"^[ab]/", "", file_path)
            warnings = _check_destructive(block)
            patches.append({"file": file_path, "diff": block.strip(), "warnings": warnings})
        return patches

    # Fallback: raw unified diff blocks delimited by --- header
    raw_blocks = re.split(r"(?=^---\s+)", llm_response, flags=re.MULTILINE)
    for block in raw_blocks:
        if not block.strip() or not block.startswith("---"):
            continue
        m = re.match(r"^---\s+(.+?)(?:\t.*)?$", block, re.MULTILINE)
        if m:
            file_path = re.sub(r"^[ab]/", "", m.group(1).strip())
            warnings = _check_destructive(block)
            patches.append({"file": file_path, "diff": block.strip(), "warnings": warnings})

    return patches


# ── Context builder ───────────────────────────────────────────────────────────

def build_llm_context(
    instruction: str,
    current_file: str,
    current_file_content: str,
    related_files: list[dict],  # [{path, content}]
    conversation_history: list[dict],  # [{role, content}]
    genome_summary: str = "",
) -> list[dict]:
    """Build the messages list for the LLM API call."""
    system = (
        "You are a precise code editor. When given an instruction, you output ONLY unified diff "
        "patches in ```diff blocks — one block per file. Never include explanations, summaries, "
        "or prose outside the diff blocks. If no change is needed, output exactly: NO_CHANGE\n\n"
        "Diff format rules:\n"
        "  --- a/<file_path>\n"
        "  +++ b/<file_path>\n"
        "  @@ -L,N +L,N @@ context\n"
        "  -removed lines (prefixed -)\n"
        "  +added lines (prefixed +)\n"
        "   context lines (prefixed space)\n\n"
        "Security constraints (NEVER violate):\n"
        "- Never output allow_origins=['*']\n"
        "- Never disable SSL verification (verify=False)\n"
        "- Never use eval(), exec(), os.system()\n"
        "- Never output DEBUG=True\n"
    )

    # Build user context block
    context_parts = []
    if genome_summary:
        context_parts.append(f"## Architecture Summary\n{genome_summary[:3000]}")

    context_parts.append(f"## Current File: {current_file}\n```\n{current_file_content[:30000]}\n```")

    total_chars = len(current_file_content)
    for rf in related_files:
        remaining = MAX_CONTEXT_CHARS - total_chars
        if remaining < 500:
            break
        snippet = rf["content"][: min(remaining - 200, 8000)]
        context_parts.append(f"## Related File: {rf['path']}\n```\n{snippet}\n```")
        total_chars += len(snippet)

    context_block = "\n\n".join(context_parts)
    messages: list[dict] = [{"role": "system", "content": system}]

    # Inject conversation history (last 8 exchanges)
    for msg in conversation_history[-16:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": str(msg["content"])[:4000]})

    messages.append({
        "role": "user",
        "content": f"{context_block}\n\n## Instruction\n{instruction}",
    })
    return messages


# ── LLM call (streaming SSE) ──────────────────────────────────────────────────

def stream_chat_edit(
    instruction: str,
    current_file: str,
    current_file_content: str,
    related_files: list[dict],
    conversation_history: list[dict],
    genome_summary: str = "",
    model: str | None = None,
) -> Generator[str, None, None]:
    """Stream SSE events for a chat-edit request.

    Yields raw SSE lines: 'event: TYPE\ndata: JSON\n\n'
    """
    import json

    yield f"event: thinking\ndata: {json.dumps({'status': 'Building context...'})}\n\n"

    messages = build_llm_context(
        instruction=instruction,
        current_file=current_file,
        current_file_content=current_file_content,
        related_files=related_files,
        conversation_history=conversation_history,
        genome_summary=genome_summary,
    )

    try:
        llm_response = _call_llm(messages, model=model)
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        return

    if not llm_response:
        yield f"event: error\ndata: {json.dumps({'message': 'LLM returned an empty response. Check that an API key with available credits is configured in Admin → AI settings.'})}\n\n"
        return

    if llm_response.strip() == "NO_CHANGE":
        yield f"event: complete\ndata: {json.dumps({'patches_count': 0, 'summary': 'No changes needed.'})}\n\n"
        return

    patches = parse_patches(llm_response)
    if not patches:
        yield f"event: error\ndata: {json.dumps({'message': 'LLM did not return a valid diff. Try rephrasing.'})}\n\n"
        return

    for patch in patches:
        yield f"event: patch\ndata: {json.dumps(patch)}\n\n"

    summary = f"Generated {len(patches)} file change{'s' if len(patches) != 1 else ''}."
    all_warnings = [w for p in patches for w in p.get("warnings", [])]
    yield f"event: complete\ndata: {json.dumps({'patches_count': len(patches), 'summary': summary, 'warnings': all_warnings})}\n\n"


def stream_selection_action(
    action: str,
    file: str,
    selection_content: str,
    full_file_content: str,
    conversation_history: list[dict],
    model: str | None = None,
) -> Generator[str, None, None]:
    """Stream SSE for explain/fix/refactor on a selection."""

    action_prompts = {
        "explain": (
            "Explain the selected code in plain English. No diffs — just a clear explanation "
            "that a non-technical stakeholder can understand."
        ),
        "fix": (
            "Fix any bugs or issues in the selected code. Return a unified diff patch."
        ),
        "refactor": (
            "Refactor the selected code for clarity, performance, and best practices. "
            "Return a unified diff patch."
        ),
        "add-test": (
            "Write a pytest test for the selected code. Return a unified diff adding the test."
        ),
    }

    instruction = action_prompts.get(action, f"Perform action '{action}' on the selected code.")
    full_instruction = (
        f"{instruction}\n\n"
        f"Selected code (lines to act on):\n```\n{selection_content}\n```"
    )

    yield from stream_chat_edit(
        instruction=full_instruction,
        current_file=file,
        current_file_content=full_file_content,
        related_files=[],
        conversation_history=conversation_history,
        model=model,
    )


def get_completion(
    file: str,
    prefix_lines: str,
    suffix_lines: str,
    model: str | None = None,
) -> str:
    """FIM (fill-in-middle) completion — returns the completion string."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a code completion engine. Output ONLY the code that should be inserted "
                "at the cursor. No explanations, no markdown fences. Match the indentation of the "
                "surrounding code exactly. Complete as many lines as make sense for the context."
            ),
        },
        {
            "role": "user",
            "content": (
                f"File: {file}\n\n"
                f"Code before cursor:\n{prefix_lines}\n\n"
                f"Code after cursor:\n{suffix_lines}\n\n"
                "Complete the code at the cursor position:"
            ),
        },
    ]
    try:
        return _call_llm(messages, model=model, max_tokens=256)
    except Exception as exc:
        logger.warning("Completion failed: %s", exc)
        return ""


# ── LLM client (platform LLMService — multi-provider with failover) ───────────

def _call_llm(messages: list[dict], model: str | None = None, max_tokens: int = 4096) -> str:
    """Call the platform LLMService (Anthropic / OpenAI / Azure / HuggingFace).

    Uses whatever provider is configured and available in APISettings —
    same routing, failover, budget enforcement, and cost tracking as every
    other LLM call in the platform.

    messages: list of {role: system|user|assistant, content: str}
    """
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    # Flatten the messages into a single prompt string.
    # LLMService._call_llm takes a unified prompt; we preserve the system
    # instructions as a header block so the model follows the diff-only rule.
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            parts.append(f"[SYSTEM]\n{content}")
        elif role == "assistant":
            parts.append(f"[ASSISTANT]\n{content}")
        else:
            parts.append(f"[USER]\n{content}")

    prompt = "\n\n".join(parts)

    provider, resolved_model = LLMService._get_configured_provider()
    chosen_model = model or resolved_model

    response_text, _ = LLMService._call_llm(
        prompt=prompt,
        model=chosen_model,
        provider=provider,
        max_tokens=max_tokens,
    )
    return response_text or ""
