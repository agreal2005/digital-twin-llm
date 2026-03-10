import logging
logger = logging.getLogger(__name__)

class PromptEngine:
    def __init__(self):
        logger.info("PromptEngine initialized (stub)")
    
    def build_prompt(self, query: str, context: dict):
        """Stub for prompt building"""
        return f"User query: {query}\nContext: {context}\nAnswer:"

_prompt_engine = None

def get_prompt_engine():
    global _prompt_engine
    if _prompt_engine is None:
        _prompt_engine = PromptEngine()
    return _prompt_engine