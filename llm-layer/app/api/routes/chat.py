"""
chat.py
=======
/chat endpoint — wires the full LLM pipeline:

  ChatRequest
      │
      ▼
  ContextBuilder      ← topology + telemetry + semantic docs
      │
      ▼
  PromptEngine        ← Llama 3.2 Instruct chat template
      │
      ▼
  LLMCore.generate()  ← llama-cpp-python inference
      │
      ▼
  IntentParser        ← classify + extract structured intent
      │
      ▼
  HumanVerification   ← gate: auto-approve or require review
      │
      ▼
  ControlLayer        ← dispatch if action + approved
      │
      ▼
  ResponseFormatter   ← clean output + package ChatResponse
"""

import time
import logging
from fastapi import APIRouter, HTTPException

from app.api.models.chat_models import ChatRequest, ChatResponse
from app.core.llm_core          import get_llm
from app.core.context_builder   import get_context_builder
from app.core.prompt_engine     import get_prompt_engine
from app.core.intent_parser     import get_intent_parser
from app.core.response_formatter import get_response_formatter
from app.core.human_verification import get_human_verification
from app.services.control_layer  import get_control_layer

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    start_time = time.time()
    logger.info(f"📝 Query: {request.query[:80]}...")

    try:
        # ----------------------------------------------------------
        # 1. Build context
        # ----------------------------------------------------------
        context_builder = get_context_builder()
        context_bundle  = context_builder.build_context(request.query)

        # ----------------------------------------------------------
        # 2. Build prompt
        # ----------------------------------------------------------
        prompt_engine = get_prompt_engine()

        # Use action prompt variant if query looks like an action
        action_keywords = ["restart", "reboot", "configure", "shutdown",
                           "enable", "disable", "delete", "update config"]
        is_likely_action = any(kw in request.query.lower() for kw in action_keywords)

        if is_likely_action or request.require_human_approval:
            prompt = prompt_engine.build_action_prompt(request.query, context_bundle)
        else:
            prompt = prompt_engine.build_prompt(request.query, context_bundle)

        # ----------------------------------------------------------
        # 3. LLM inference
        # ----------------------------------------------------------
        llm    = get_llm()
        result = llm.generate(
            prompt=prompt,
            max_tokens=request.max_tokens or 0,
            temperature=request.temperature or 0.0,
            stop=["<|eot_id|>", "<|start_header_id|>"],
        )

        raw_response = result["response"]
        logger.info(f"🤖 LLM generated {result.get('completion_tokens', '?')} tokens "
                    f"in {result.get('latency', 0):.2f}s")

        # ----------------------------------------------------------
        # 4. Parse intent
        # ----------------------------------------------------------
        intent_parser = get_intent_parser()
        intent        = intent_parser.parse(request.query, raw_response)

        # ----------------------------------------------------------
        # 5. Human verification gate
        # ----------------------------------------------------------
        human_verifier = get_human_verification()

        if request.require_human_approval:
            # Caller explicitly forced review
            requires_review = True
            approval_id     = "manual-" + str(int(time.time()))
        else:
            requires_review, approval_id = human_verifier.evaluate(intent, request.query)

        approval_status = None
        if requires_review:
            approval_status = "pending"
            logger.warning(f"⚠️  Action held for human review [id={approval_id}]")
        elif intent.type == "action" and intent.action:
            # ----------------------------------------------------------
            # 6. Dispatch to control layer (auto-approved actions only)
            # ----------------------------------------------------------
            control = get_control_layer()
            dispatch_result = control.dispatch(intent)
            logger.info(f"✅ Dispatched: {dispatch_result}")
            approval_status = "auto_approved"

        # ----------------------------------------------------------
        # 7. Format response
        # ----------------------------------------------------------
        formatter = get_response_formatter()
        formatted = formatter.format(
            raw_llm_response=raw_response,
            intent=intent,
            human_review_required=requires_review,
            approval_status=approval_status,
        )

        total_latency_ms = (time.time() - start_time) * 1000

        return ChatResponse(
            response             = formatted["response"],
            intent               = formatted["intent"],
            confidence           = formatted["confidence"],
            tokens_used          = result.get("completion_tokens", 0),
            latency_ms           = round(total_latency_ms, 2),
            human_review_required= formatted["human_review_required"],
            approval_status      = formatted["approval_status"],
        )

    except Exception as e:
        logger.error(f"❌ Pipeline error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))