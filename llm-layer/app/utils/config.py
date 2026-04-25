import os
from dotenv import load_dotenv
from typing import List

# Load .env file
load_dotenv()

class Config:
    # Model settings
    MODEL_PATH: str = os.getenv("MODEL_PATH", "/home/agreal2016/btp/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf")
    MODEL_N_CTX: int = int(os.getenv("MODEL_N_CTX", "16384"))
    MODEL_N_THREADS: int = int(os.getenv("MODEL_N_THREADS", "4"))
    
    # API settings
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"
    
    # Human verification settings
    AUTO_APPROVE_CONFIDENCE: float = float(os.getenv("AUTO_APPROVE_CONFIDENCE", "0.9"))
    REQUIRE_HUMAN_ACTIONS: List[str] = os.getenv("REQUIRE_HUMAN_ACTIONS", 
                                                 "restart_device,update_config,delete_interface").split(",")

config = Config()