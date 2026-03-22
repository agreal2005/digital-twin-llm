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
You have access to real-time network topology, telemetry metrics, and configuration documentation.

Your job is to:
1. Answer questions about the network clearly and concisely
2. Diagnose issues based on provided telemetry and topology data
3. Suggest actionable remediation steps
4. For any action requests (restart, reconfigure, etc.), respond with a structured action plan

Always base your answers on the provided network context. If context is missing, say so.
Keep responses focused and under 200 words unless a detailed explanation is needed."""


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