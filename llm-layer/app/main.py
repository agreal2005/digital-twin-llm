"""
main.py
=======
FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, chat
from app.utils.logger import setup_logger

setup_logger(__name__)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("🚀 Starting Digital Twin LLM Assistant")
    try:
        from app.core.llm_core import get_llm
        get_llm()
        logger.info("✅ LLM loaded")

        from app.core.context_builder import get_context_builder
        get_context_builder()
        logger.info("✅ ContextBuilder ready")

        from app.core.prompt_engine import get_prompt_engine
        get_prompt_engine()
        logger.info("✅ PromptEngine ready")

        from app.core.intent_parser import get_intent_parser
        get_intent_parser()
        logger.info("✅ IntentParser ready")

        from app.core.response_formatter import get_response_formatter
        get_response_formatter()
        logger.info("✅ ResponseFormatter ready")

        from app.core.human_verification import get_human_verification
        get_human_verification()
        logger.info("✅ HumanVerification ready")

        from app.services.control_layer import get_control_layer
        get_control_layer()
        logger.info("✅ ControlLayer ready")

        logger.info("🟢 All components initialized — pipeline ready")

    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")

    yield  # App runs here

    logger.info("👋 Shutting down Digital Twin LLM Assistant")


app = FastAPI(
    title="Digital Twin LLM Assistant",
    description="LLM-powered assistant for network digital twins",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["Health"])
app.include_router(chat.router,   tags=["Chat"])


# ------------------------------------------------------------------
# Approval management endpoints
# ------------------------------------------------------------------
from fastapi import HTTPException
from app.core.human_verification import get_human_verification

@app.get("/approvals/pending", tags=["Approvals"])
async def get_pending_approvals():
    """List all actions awaiting human review."""
    hv = get_human_verification()
    return {"pending": hv.get_pending()}

@app.post("/approvals/{approval_id}/resolve", tags=["Approvals"])
async def resolve_approval(approval_id: str, approved: bool, reviewer_id: str = "api-user"):
    """Approve or reject a pending action."""
    hv = get_human_verification()
    result = hv.resolve(approval_id, approved, reviewer_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return result