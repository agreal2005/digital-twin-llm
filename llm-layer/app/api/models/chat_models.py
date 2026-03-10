from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

# Request/Response models
class ChatRequest(BaseModel):
    query: str = Field(..., description="User's natural language query")
    user_id: Optional[str] = Field(None, description="User identifier")
    session_id: Optional[str] = Field(None, description="Session for conversation history")
    max_tokens: Optional[int] = Field(500, description="Maximum tokens in response")
    temperature: Optional[float] = Field(0.7, description="LLM temperature (0-1)")
    require_human_approval: Optional[bool] = Field(False, description="Force human review")

class ChatResponse(BaseModel):
    response: str = Field(..., description="Natural language response")
    intent: Optional[str] = Field(None, description="Detected intent type")
    confidence: Optional[float] = Field(None, description="Intent confidence score")
    tokens_used: Optional[int] = Field(None, description="Number of tokens generated")
    latency_ms: Optional[float] = Field(None, description="Processing time in milliseconds")
    human_review_required: Optional[bool] = Field(False, description="Whether human review is needed")
    approval_status: Optional[str] = Field(None, description="pending/approved/rejected")

# Intent model
class Intent(BaseModel):
    type: str = Field(..., description="informational, explanation, action, unknown")
    confidence: float = Field(..., ge=0, le=1, description="Confidence score")
    entities: Dict[str, Any] = Field(default_factory=dict, description="Extracted entities")
    action: Optional[str] = Field(None, description="Action to perform if type=action")
    target: Optional[str] = Field(None, description="Target device/service")
    raw_text: Optional[str] = Field(None, description="Original query text")

# Human verification models
class VerificationRequest(BaseModel):
    intent: Intent
    query: str
    proposed_commands: Optional[List[str]] = None
    impact_analysis: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None

class VerificationResponse(BaseModel):
    approved: bool
    reviewer_id: Optional[str] = None
    comments: Optional[str] = None
    modified_intent: Optional[Intent] = None
    timestamp: datetime = Field(default_factory=datetime.now)