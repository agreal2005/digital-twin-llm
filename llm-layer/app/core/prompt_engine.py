"""
prompt_engine.py
================
Builds prompts for Llama 3.2 3B Instruct using the correct chat template.

Llama 3.2 Instruct format:
  <|begin_of_text|>
  <|start_header_id|>system<|end_header_id|>
  {system_prompt}<|eot_id|>
  <|start_header_id|>user<|end_header_id|>
  {user_message}<|eot_id|>
  <|start_header_id|>assistant<|end_header_id|>
"""

import logging
from typing import Optional
from app.core.context_builder import ContextBundle, get_context_builder

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a network operations assistant for a network digital twin system.
You analyze real-time network data including topology, telemetry, and traffic patterns.

OPERATING PRINCIPLES:
1. The SYSTEM-VERIFIED FACTS section contains pre-computed truths about the network. These are absolute facts, not suggestions.
2. When describing the network, use the exact numbers and types from VERIFIED FACTS. Never substitute your own.
3. If you need to describe connections, use the individual device listings below. Do not infer patterns that aren't explicitly shown.
4. If data is missing (NULL, empty, or marked unavailable), state that clearly rather than filling gaps.
5. Your role is to analyze and explain the provided data, not to generate plausible-sounding alternatives.

For topology questions: Report what the data shows. If 4 devices are all interconnected, say they form a mesh. If they form a hierarchy, describe that. Let the data guide you.
For diagnostic questions: Use the telemetry data provided. Don't assume problems exist unless the metrics show them.
For traffic questions: Work with whatever traffic data is available in the context.

Always keep responses clear and based on the provided context."""


def _build_llama_prompt(system: str, user: str) -> str:
    """Format prompt using Llama 3.2 Instruct chat template."""
    return (
        f"<|start_header_id|>system<|end_header_id|>\n{system}<|eot_id|>"
        f"<|start_header_id|>user<|end_header_id|>\n{user}<|eot_id|>"
        f"<|start_header_id|>assistant<|end_header_id|>\n"
    )


class PromptEngine:
    def __init__(self):
        self.context_builder = get_context_builder()
        logger.info("✅ PromptEngine initialized")

    def build_prompt(self, query: str, context_bundle: Optional[ContextBundle] = None) -> str:
        """
        Build a full Llama 3.2 Instruct prompt from query + context bundle.
        If no bundle provided, builds one from the query.
        """
        if context_bundle is None:
            context_bundle = self.context_builder.build_context(query)

        context_text = self.context_builder.summarize_context(context_bundle)

        user_message = f"""Network Context:
{context_text}

Question: {query}"""

        prompt = _build_llama_prompt(SYSTEM_PROMPT, user_message)
        logger.debug(f"Built prompt ({len(prompt)} chars)")
        return prompt

    def build_action_prompt(self, query: str, context_bundle: Optional[ContextBundle] = None) -> str:
        """
        Prompt variant for action intents — asks model to output structured JSON action.
        """
        if context_bundle is None:
            context_bundle = self.context_builder.build_context(query)

        context_text = self.context_builder.summarize_context(context_bundle)

        print(f"=== CONTEXT SENT TO LLM ===\n{context_text}\n=== END CONTEXT ===", file=sys.stderr)

        action_system = SYSTEM_PROMPT + """

For action requests, end your response with a JSON block in this exact format:
```json
{
  "action": "<action_name>",
  "target": "<device_id>",
  "parameters": {},
  "confidence": 0.0,
  "requires_human_approval": true
}
```"""

        user_message = f"""Network Context:
{context_text}

Action Request: {query}"""

        # end of action_system string, make it more explicit:
        "You MUST end your response with the JSON block. Do not skip it."

        return _build_llama_prompt(action_system, user_message)


_prompt_engine = None

def get_prompt_engine() -> PromptEngine:
    global _prompt_engine
    if _prompt_engine is None:
        _prompt_engine = PromptEngine()
    return _prompt_engine
