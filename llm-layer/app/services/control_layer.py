"""
control_layer.py
================
Service that dispatches approved actions to the real Control Layer.

Right now: stub that logs and simulates execution.
In production: makes HTTP/gRPC calls to the Control Layer
(Access + Action Planner + Change Executioner + Policy Enforcement Gateway).
"""

import logging
from typing import Optional
from app.api.models.chat_models import Intent

logger = logging.getLogger(__name__)

# Map action names → stub handlers
# Replace each handler body with a real API call to the control plane
ACTION_HANDLERS: dict[str, str] = {
    "restart":       "POST /control/device/{target}/restart",
    "configure":     "POST /control/device/{target}/configure",
    "shutdown":      "POST /control/device/{target}/shutdown",
    "enable":        "POST /control/device/{target}/enable",
    "delete":        "DELETE /control/resource/{target}",
    "update_config": "PUT /control/device/{target}/config",
}


class ControlLayerService:
    def __init__(self, base_url: str = "http://control-layer:9000"):
        self.base_url = base_url
        logger.info(f"✅ ControlLayerService initialized | base_url={base_url}")

    def dispatch(self, intent: Intent) -> dict:
        """
        Dispatch an approved action intent to the control plane.

        Returns a result dict with status and details.
        """
        if intent.type != "action":
            return {"status": "skipped", "reason": "not an action intent"}

        action   = intent.action or "unknown"
        target   = intent.target or "unspecified"
        endpoint = ACTION_HANDLERS.get(action, f"POST /control/generic/{action}")
        endpoint = endpoint.replace("{target}", target)

        logger.info(f"🚀 Dispatching action: {action} → target={target} | endpoint={endpoint}")

        # ----------------------------------------------------------------
        # TODO: replace stub below with real HTTP call, e.g.:
        #
        #   import httpx
        #   response = httpx.post(
        #       f"{self.base_url}{endpoint}",
        #       json={"intent": intent.dict(), "parameters": intent.entities},
        #       timeout=10.0
        #   )
        #   return response.json()
        # ----------------------------------------------------------------

        # Stub response
        return {
            "status":   "dispatched",
            "action":   action,
            "target":   target,
            "endpoint": f"{self.base_url}{endpoint}",
            "result":   f"[stub] Action '{action}' on '{target}' queued successfully",
        }

    def get_device_status(self, device_id: str) -> dict:
        """
        Fetch live device status from control plane.
        Stub — replace with real call.
        """
        logger.debug(f"Fetching status for {device_id} (stub)")
        return {
            "device_id":  device_id,
            "status":     "up",
            "last_seen":  "2025-01-01T00:00:00",
            "source":     "stub",
        }


_control_layer_service = None

def get_control_layer() -> ControlLayerService:
    global _control_layer_service
    if _control_layer_service is None:
        _control_layer_service = ControlLayerService()
    return _control_layer_service