from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.api.routes import health, chat
from app.utils.logger import setup_logger

# Setup logging
setup_logger(__name__)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Digital Twin LLM Assistant",
    description="LLM-powered assistant for network digital twins",
    version="0.1.0",
)

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(chat.router, tags=["Chat"])

@app.on_event("startup")
async def startup_event():
    """Initialize components on startup"""
    logger.info("🚀 Starting Digital Twin LLM Assistant")
    try:
        from app.core.llm_core import get_llm
        get_llm()
        logger.info("✅ LLM loaded successfully")
    except Exception as e:
        logger.error(f"❌ Failed to load LLM: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("👋 Shutting down Digital Twin LLM Assistant")