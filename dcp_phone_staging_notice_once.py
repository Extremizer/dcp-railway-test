"""One explicit, persistent mock-only Telegram notification per operator request.

A request ID is a nonsecret Railway staging variable, never an HTTP route.
The marker is created before sending. Process crashes do not resend blindly.
No supplier DCP hostname or production broker is referenced here.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re

class StagingNoticeBlocked(RuntimeError):
    pass

def send_once(*, coordinator, state_dir, request_id: str, browser_status: str) -> str:
    if not isinstance(request_id,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,64}",request_id):
        raise StagingNoticeBlocked("invalid_staging_request_id")
    if browser_status != "MOCK_ONLY":
        raise StagingNoticeBlocked("mock_browser_not_ready")
    state=Path(state_dir)
    if not state.is_dir() or not state.is_absolute() or state.is_symlink():
        raise StagingNoticeBlocked("persistent_state_missing")
    digest=hashlib.sha256(request_id.encode()).hexdigest()
    marker=state/("phone-test-notification-"+digest+".lock")
    try:
        fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        return "ALREADY_ATTEMPTED"
    try:
        with os.fdopen(fd,"w") as stream:
            stream.write("reserved\n")
            stream.flush()
            os.fsync(stream.fileno())
        # A real Telegram failure raises, leaving no "sent" claim.
        if coordinator.notify("AUTH_REQUIRED") is not True:
            raise StagingNoticeBlocked("notification_not_queued")
        return "SENT"
    except Exception:
        # A confirmed send may have happened before crash; no automatic retries.
        # Explicit new request_id is required for a second delivery attempt.
        return "UNCERTAIN"
