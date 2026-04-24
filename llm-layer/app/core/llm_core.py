from llama_cpp import Llama
import time
import logging
from typing import Optional, Dict, Any, List
from app.utils.config import config

logger = logging.getLogger(__name__)

class LLMCore:
    def __init__(self, model_path: str = None, n_ctx: int = None, n_threads: int = None):
        """
        Initialize the Llama model.
        """
        self.model_path = model_path or config.MODEL_PATH
        self.n_ctx = n_ctx or config.MODEL_N_CTX
        self.n_threads = n_threads or config.MODEL_N_THREADS
        
        logger.info(f"Loading model from {self.model_path}")
        logger.info(f"Context window: {self.n_ctx}, Threads: {self.n_threads}")
        
        try:
            self.llm = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
                verbose=False,
            )
            logger.info("✅ Model loaded successfully")
        except Exception as e:
            logger.error(f"❌ Failed to load model: {e}")
            raise
        
        # Performance tracking
        self.total_tokens = 0
        self.total_latency = 0.0
        self.call_count = 0
        
    def generate(
        self, 
        prompt: str, 
        max_tokens: int = 500,
        temperature: float = 0.0,  # Changed from 0.7 to 0.0
        stop: Optional[List[str]] = None,
        echo: bool = False
    ) -> Dict[str, Any]:
        """
        Generate text from the model.
        """
        start_time = time.time()
        self.call_count += 1
        
        # Prepare generation kwargs
        kwargs = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "echo": echo,
        }
        
        if stop:
            kwargs["stop"] = stop
        
        try:
            # Generate
            response = self.llm(**kwargs)
            latency = time.time() - start_time
            
            # Extract the generated text
            generated_text = response['choices'][0]['text'].strip()
            
            # Count tokens (approximate)
            completion_tokens = len(generated_text.split())
            
            # Update stats
            self.total_tokens += completion_tokens
            self.total_latency += latency
            
            logger.info(f"Generated {completion_tokens} tokens in {latency:.2f}s")
            
            return {
                "response": generated_text,
                "completion_tokens": completion_tokens,
                "latency": latency,
                "tokens_per_second": completion_tokens / latency if latency > 0 else 0,
                "model_status": "success"
            }
            
        except Exception as e:
            logger.error(f"❌ Generation failed: {e}")
            return {
                "response": f"Error: {str(e)}",
                "error": str(e),
                "model_status": "error",
                "latency": time.time() - start_time
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        avg_latency = self.total_latency / self.call_count if self.call_count > 0 else 0
        return {
            "total_calls": self.call_count,
            "total_tokens": self.total_tokens,
            "total_latency": round(self.total_latency, 2),
            "avg_latency": round(avg_latency, 2),
            "avg_tokens_per_call": round(self.total_tokens / self.call_count, 2) if self.call_count > 0 else 0
        }

# Singleton instance
_llm_instance = None

def get_llm() -> LLMCore:
    """Get or create the LLM instance (singleton)."""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMCore()
    return _llm_instance