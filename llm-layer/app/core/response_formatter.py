"""
response_formatter.py
=====================
Cleans raw LLM output and packages it into a structured API response.

Responsibilities:
- Strip Llama special tokens / artifacts
- Remove leaked JSON action blocks from conversational response
- Truncate runaway responses
- Add metadata (sources used, suggested follow-ups)
"""

import re
import logging
from typing import Optional
from app.api.models.chat_models import Intent

logger = logging.getLogger(__name__)

MAX_RESPONSE_CHARS = 30000

# Llama 3.2 special tokens that may leak into output
LLAMA_ARTIFACTS = [
    "<|eot_id|>", "<|begin_of_text|>", "<|end_of_text|>",
    "<|start_header_id|>", "<|end_header_id|>",
    "assistant", "user", "system",  # bare role tokens
]

# Regex to strip fenced JSON blocks from conversational part
JSON_FENCE_RE = re.compile(r'```json.*?```', re.DOTALL)
JSON_BARE_RE  = re.compile(r'\{\s*"action"\s*:.*?\}', re.DOTALL)


def _clean_llm_output(raw: str) -> str:
    """Remove special tokens, JSON blocks, and normalize whitespace."""
    text = raw

    # Strip JSON action blocks (they're parsed separately by IntentParser)
    text = JSON_FENCE_RE.sub("", text)
    text = JSON_BARE_RE.sub("", text)

    # Strip Llama special tokens
    for artifact in LLAMA_ARTIFACTS:
        text = text.replace(artifact, "")

    # Normalize whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text


def _truncate(text: str, max_chars: int = MAX_RESPONSE_CHARS) -> tuple[str, bool]:
    """Truncate at sentence boundary if over limit."""
    if len(text) <= max_chars:
        return text, False

    # Try to cut at last sentence end before limit
    truncated = text[:max_chars]
    last_period = max(
        truncated.rfind(". "),
        truncated.rfind(".\n"),
    )
    if last_period > max_chars * 0.6:  # Only cut at sentence if it's not too short
        truncated = truncated[:last_period + 1]

    return truncated + " [...]", True


def _build_summary_line(intent: Intent) -> Optional[str]:
    """One-line action summary appended for action intents."""
    if intent.type != "action" or not intent.action:
        return None
    target = f" on {intent.target}" if intent.target else ""
    return f"→ Proposed action: `{intent.action}`{target} (confidence: {intent.confidence:.0%})"


class ResponseFormatter:
    def __init__(self):
        logger.info("✅ ResponseFormatter initialized")

    def format(
        self,
        raw_llm_response: str,
        intent: Intent,
        human_review_required: bool = False,
        approval_status: Optional[str] = None,
    ) -> dict:
        """
        Format raw LLM output into a clean response dict.

        Returns keys matching ChatResponse fields:
          response, intent, confidence, human_review_required, approval_status
        """
        cleaned = _clean_llm_output(raw_llm_response)
        truncated_text, was_truncated = _truncate(cleaned)

        # Append action summary for action intents
        summary = _build_summary_line(intent)
        if summary:
            truncated_text = truncated_text + "\n\n" + summary

        if was_truncated:
            logger.debug("Response was truncated to fit MAX_RESPONSE_CHARS")

        return {
            "response": truncated_text,
            "intent": intent.type,
            "confidence": intent.confidence,
            "human_review_required": human_review_required,
            "approval_status": approval_status,
        }


_response_formatter = None

def get_response_formatter() -> ResponseFormatter:
    global _response_formatter
    if _response_formatter is None:
        _response_formatter = ResponseFormatter()
    return _response_formatter