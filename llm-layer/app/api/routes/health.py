from fastapi import APIRouter
import logging
from app.core.llm_core import get_llm

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/health")
async def health_check():
    """Basic health check endpoint"""
    try:
        llm = get_llm()
        stats = llm.get_stats()
        
        return {
            "status": "healthy",
            "model_loaded": True,
            "model_stats": stats,
            "service": "digital-twin-llm",
            "version": "0.1.0"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Digital Twin LLM Assistant API",
        "version": "0.1.0",
        "endpoints": [
            "/",
            "/health",
            "/chat",
            "/docs"
        ]
    }