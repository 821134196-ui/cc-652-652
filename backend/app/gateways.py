"""Local mocks for the external e-signature gateway and the notification push.

Both are intentionally file/log based stand-ins: they append a line to a local
log file so a test (or a human) can prove the side effect happened. The real
system would swap these for HTTP clients; the service layer depends only on the
two functions below.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .database import NOTIFY_GATEWAY_LOG, SIGN_GATEWAY_LOG


def issue_signature_receipt(*, surgery_id: int, version_no: int, signer_name: str) -> str:
    """Pretend to call an e-signature service; return a unique receipt id.

    Receipts are unique tokens. A redelivery of the SAME receipt must not create
    a second signature — the service layer enforces that via UNIQUE(receipt).
    """
    receipt_id = f"RCP-{surgery_id}-V{version_no}-{uuid.uuid4().hex[:12].upper()}"
    event = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": "RECEIPT_ISSUED",
        "receipt_id": receipt_id,
        "surgery_id": surgery_id,
        "version_no": version_no,
        "signer": signer_name,
    }
    with SIGN_GATEWAY_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return receipt_id


def notify_surgical_team(*, surgery_id: int, type_: str, message: str) -> None:
    """Pretend to push a message to the surgical team's channel."""
    event = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": "TEAM_NOTIFY",
        "surgery_id": surgery_id,
        "type": type_,
        "message": message,
    }
    with NOTIFY_GATEWAY_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
