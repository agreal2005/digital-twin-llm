"""
human_verification.py
=====================
Policy gate that sits between IntentParser and Control Layer.

Rules (in priority order):
  1. If intent type is not "action"          → auto-approve
  2. If action is in ALWAYS_REQUIRE list     → require human
  3. If confidence < AUTO_APPROVE_THRESHOLD  → require human
  4. Otherwise                               → auto-approve

In a real system this would integrate with a ticketing / approval workflow.
For now: pending approvals are stored in memory and can be resolved via API.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional
from app.api.models.chat_models import Intent, VerificationRequest, VerificationResponse
from app.utils.config import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory approval store (replace with DB in production)
# ---------------------------------------------------------------------------
_pending_approvals: dict[str, dict] = {}


class HumanVerification:
    def __init__(self):
        self.auto_approve_threshold = config.AUTO_APPROVE_CONFIDENCE
        self.require_human_actions  = set(config.REQUIRE_HUMAN_ACTIONS)
        logger.info(
            f"✅ HumanVerification initialized | "
            f"threshold={self.auto_approve_threshold} | "
            f"always_require={self.require_human_actions}"
        )

    def evaluate(self, intent: Intent, query: str) -> tuple[bool, Optional[str]]:
        """
        Decide if human review is needed.

        Returns:
            (requires_human: bool, approval_id: str | None)
            approval_id is set when requires_human=True so the caller can
            poll or display it.
        """
        # Non-action intents never need approval
        if intent.type != "action":
            return False, None

        needs_review = False
        reason = ""

        if intent.action in self.require_human_actions:
            needs_review = True
            reason = f"action '{intent.action}' is in the always-require list"
        elif intent.confidence < self.auto_approve_threshold:
            needs_review = True
            reason = f"confidence {intent.confidence:.2f} below threshold {self.auto_approve_threshold}"

        if needs_review:
            approval_id = str(uuid.uuid4())[:8]
            _pending_approvals[approval_id] = {
                "approval_id":  approval_id,
                "query":        query,
                "intent":       intent.dict(),
                "reason":       reason,
                "status":       "pending",
                "created_at":   datetime.now().isoformat(),
                "resolved_at":  None,
                "reviewer_id":  None,
            }
            logger.warning(f"⚠️  Human approval required [{approval_id}]: {reason}")
            return True, approval_id

        logger.info(f"✅ Auto-approved action '{intent.action}' (confidence={intent.confidence:.2f})")
        return False, None

    # ------------------------------------------------------------------
    # Approval resolution (called by a future /approve endpoint)
    # ------------------------------------------------------------------

    def resolve(self, approval_id: str, approved: bool,
                reviewer_id: str = "system") -> Optional[VerificationResponse]:
        """Resolve a pending approval."""
        record = _pending_approvals.get(approval_id)
        if not record:
            logger.error(f"Approval ID {approval_id} not found")
            return None

        record["status"]      = "approved" if approved else "rejected"
        record["resolved_at"] = datetime.now().isoformat()
        record["reviewer_id"] = reviewer_id

        logger.info(f"Approval {approval_id} {'approved' if approved else 'rejected'} by {reviewer_id}")

        return VerificationResponse(
            approved=approved,
            reviewer_id=reviewer_id,
        )

    def get_pending(self) -> list[dict]:
        return [v for v in _pending_approvals.values() if v["status"] == "pending"]

    def get_approval(self, approval_id: str) -> Optional[dict]:
        return _pending_approvals.get(approval_id)


_human_verification = None

def get_human_verification() -> HumanVerification:
    global _human_verification
    if _human_verification is None:
        _human_verification = HumanVerification()
    return _human_verification