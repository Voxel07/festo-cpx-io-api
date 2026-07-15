"""API-wide emergency-stop latch and optional external interlock adapter."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request


class SafetyController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._emergency_stop = False
        self._reason = ""
        self._interlock_checked_at = 0.0
        self._interlock_error = ""

    def emergency_stop(self, reason: str = "Emergency stop requested") -> None:
        with self._lock:
            self._emergency_stop = True
            self._reason = reason

    def reset(self) -> None:
        with self._lock:
            self._emergency_stop = False
            self._reason = ""

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "safe": not self._emergency_stop,
                "emergency_stop": self._emergency_stop,
                "reason": self._reason,
                "external_interlock_configured": bool(os.environ.get("FESTO_INTERLOCK_URL")),
            }

    def assert_safe(self) -> None:
        state = self.status()
        if state["emergency_stop"]:
            raise RuntimeError(str(state["reason"] or "Emergency stop is latched"))
        url = os.environ.get("FESTO_INTERLOCK_URL", "").strip()
        if not url:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._interlock_checked_at < 0.5:
                if self._interlock_error:
                    raise RuntimeError(self._interlock_error)
                return
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read())
            if payload.get("safe") is not True:
                raise RuntimeError(payload.get("reason") or "External interlock is not safe")
        except Exception as exc:
            message = f"External safety interlock check failed: {exc}"
            with self._lock:
                self._interlock_checked_at = now
                self._interlock_error = message
            raise RuntimeError(message) from exc
        with self._lock:
            self._interlock_checked_at = now
            self._interlock_error = ""


safety_controller = SafetyController()
