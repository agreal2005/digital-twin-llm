"""
intent_parser.py
================
Parses the LLM's raw text output into a structured Intent object.

Two-stage approach:
  1. Rule-based pre-classification on the original query (fast, cheap)
  2. JSON extraction from LLM response for action intents
"""

import re
import json
import logging
from typing import Optional
from app.api.models.chat_models import Intent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword maps for rule-based classification
# ---------------------------------------------------------------------------

ACTION_KEYWORDS = {
    "restart":        ["restart", "reboot", "reset", "reload"],
    "configure":      ["configure", "config", "set", "apply", "push", "update config"],
    "shutdown":       ["shutdown", "shut down", "disable", "turn off", "stop"],
    "enable":         ["enable", "turn on", "start", "bring up"],
    "delete":         ["delete", "remove", "drop", "clear"],
    "update_config":  ["update config", "change config", "modify config", "reconfigure"],
}

DIAGNOSTIC_KEYWORDS = [
    "what's wrong", "why is", "diagnose", "troubleshoot", "debug",
    "issue", "problem", "error", "fail", "down", "degraded", "alert"
]

INFORMATIONAL_KEYWORDS = [
    "show", "list", "what is", "tell me", "explain", "describe",
    "status", "how many", "which", "where", "display", "get"
]

# Device ID pattern: word-digit combos like router-1, switch-2, server-3
DEVICE_PATTERN = re.compile(r'\b([a-zA-Z]+-\d+)\b')


def _extract_entities(query: str) -> dict:
    """Pull device names, metrics, and other entities from the query."""
    entities = {}

    # Devices mentioned
    devices = DEVICE_PATTERN.findall(query)
    if devices:
        entities["devices"] = list(set(devices))

    # Metrics mentioned
    metric_keywords = ["cpu", "memory", "latency", "bandwidth", "packet loss",
                       "throughput", "uptime", "error rate"]
    found_metrics = [m for m in metric_keywords if m in query.lower()]
    if found_metrics:
        entities["metrics"] = found_metrics

    return entities


def _classify_query(query: str) -> tuple[str, str | None, float]:
    """
    Returns (intent_type, action_name_or_None, confidence).
    """
    q = query.lower()

    # Check for action keywords
    for action_name, keywords in ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in q:
                return "action", action_name, 0.75

    # Check diagnostic
    if any(kw in q for kw in DIAGNOSTIC_KEYWORDS):
        return "diagnostic", None, 0.80

    # Check informational
    if any(kw in q for kw in INFORMATIONAL_KEYWORDS):
        return "informational", None, 0.85

    return "unknown", None, 0.50


def _extract_json_action(llm_response: str) -> Optional[dict]:
    """
    Try to extract a JSON block from the LLM response.
    Looks for ```json ... ``` fences, then falls back to bare { } blocks.
    """
    # Try fenced JSON
    fence_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try bare JSON object
    bare_match = re.search(r'\{[^{}]*"action"[^{}]*\}', llm_response, re.DOTALL)
    if bare_match:
        try:
            return json.loads(bare_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


class IntentParser:
    def __init__(self):
        logger.info("✅ IntentParser initialized")

    def parse(self, query: str, llm_response: str) -> Intent:
        """
        Parse query + LLM response into a structured Intent.
        """
        intent_type, action_name, confidence = _classify_query(query)
        entities = _extract_entities(query)

        # For action intents, try to extract JSON from LLM response
        target = None
        if intent_type == "action":
            json_action = _extract_json_action(llm_response)
            if json_action:
                action_name = json_action.get("action", action_name)
                target = json_action.get("target")
                confidence = float(json_action.get("confidence", confidence))
                entities.update(json_action.get("parameters", {}))
            elif entities.get("devices"):
                target = entities["devices"][0]

        logger.info(f"Parsed intent: type={intent_type}, action={action_name}, confidence={confidence:.2f}")

        return Intent(
            type=intent_type,
            confidence=confidence,
            entities=entities,
            action=action_name,
            target=target,
            raw_text=query,
        )


_intent_parser = None

def get_intent_parser() -> IntentParser:
    global _intent_parser
    if _intent_parser is None:
        _intent_parser = IntentParser()
    return _intent_parser