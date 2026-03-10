from fastapi import APIRouter, HTTPException
import time
import logging
from app.api.models.chat_models import ChatRequest, ChatResponse
from app.core.llm_core import get_llm

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Basic chat endpoint - direct LLM call without context yet.
    """
    start_time = time.time()
    
    try:
        logger.info(f"📝 Received query: {request.query[:50]}...")
        
        # Get LLM instance
        llm = get_llm()
        
        # Simple prompt
        prompt = f"""You are a helpful network assistant. Answer the following question about networks:

Question: {request.query}

Answer:"""
        
        # Generate response
        result = llm.generate(
            prompt=prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            stop=["Question:", "\n\n"]
        )
        
        # Calculate total latency
        latency_ms = (time.time() - start_time) * 1000
        
        # Simple intent detection
        intent = "informational"
        confidence = 0.7
        
        # Check for action keywords
        action_keywords = ["restart", "reboot", "configure", "change", "stop", "start", "deploy"]
        if any(kw in request.query.lower() for kw in action_keywords):
            intent = "action"
            confidence = 0.6
        
        return ChatResponse(
            response=result["response"],
            intent=intent,
            confidence=confidence,
            tokens_used=result.get("completion_tokens", 0),
            latency_ms=latency_ms,
            human_review_required=False
        )
        
    except Exception as e:
        logger.error(f"❌ Error in chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))