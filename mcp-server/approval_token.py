"""Signed, single-use approval grants — the human-in-the-loop gate.

A *grant* is a short-lived HMAC-signed statement that reads, in effect:

    "At 14:32:07, maintenance lead `j.reyes` approved assigning work order 2481
     to crew 4 at urgency safety_critical."

The backend mints one only in direct response to a lead clicking Approve. The
MCP write tool refuses to assign a crew unless it is handed a grant that

  1. carries a valid signature made with the shared secret,
  2. has not expired,
  3. is bound to exactly the work order, crew and urgency being written, and
  4. has never been redeemed before (enforced by the PRIMARY KEY on
     `approval_grants.jti`, not by this module).

Points 1-3 live here. Point 4 lives in the database, because uniqueness is the
one thing a stateless token cannot check for itself.

This file is deliberately duplicated between `backend/app/` and `mcp-server/`:
the two services ship as separate images and must not share a Python package.
Keep the copies identical.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

GRANT_VERSION = "v1"

#: Urgency levels, most severe first. The ordering is meaningful — see
#: `backend/app/safety.py`, which uses the same tuple to rank the queue.
URGENCY_LEVELS = ("safety_critical", "production_stopping", "routine")

#: Tolerance, in seconds, for clock drift between the backend and MCP server
#: containers when checking the issued-at timestamp.
CLOCK_SKEW_SECONDS = 30


class ApprovalTokenError(Exception):
    """Raised when a grant is missing, malformed, expired, or does not match."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: str, payload_b64: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64url_encode(digest)


def mint_grant(
    *,
    secret: str,
    work_order_id: int,
    crew_id: int,
    urgency: str,
    approved_by: str,
    ttl_seconds: int,
    now: int | None = None,
) -> tuple[str, dict]:
    """Create a grant authorising one specific assignment.

    Returns ``(token, claims)``. Never call this anywhere except the code path
    that handles an explicit human approval action.
    """
    if not secret:
        raise ApprovalTokenError("No approval signing secret is configured.")
    if urgency not in URGENCY_LEVELS:
        raise ApprovalTokenError(f"Unknown urgency level: {urgency!r}")
    if not approved_by or not approved_by.strip():
        raise ApprovalTokenError("An approval must name the person who gave it.")

    issued_at = int(time.time()) if now is None else now
    claims = {
        "jti": str(uuid.uuid4()),
        "wo": int(work_order_id),
        "crew": int(crew_id),
        "urg": urgency,
        "by": approved_by.strip(),
        "iat": issued_at,
        "exp": issued_at + int(ttl_seconds),
    }

    payload_b64 = _b64url_encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    token = f"{GRANT_VERSION}.{payload_b64}.{_sign(secret, payload_b64)}"
    return token, claims


def verify_grant(
    token: str,
    *,
    secret: str,
    work_order_id: int,
    crew_id: int,
    urgency: str,
    now: int | None = None,
) -> dict:
    """Validate a grant and confirm it authorises *this exact* assignment.

    The binding check is the important one. A valid signature alone is not
    enough: a grant for "assign WO-2495 to the calibration crew" must not be
    replayed to assign a different work order, a different crew, or the same
    work order at a different urgency.
    """
    if not secret:
        raise ApprovalTokenError("No approval signing secret is configured.")
    if not token or not isinstance(token, str):
        raise ApprovalTokenError("No approval grant was presented.")

    parts = token.split(".")
    if len(parts) != 3:
        raise ApprovalTokenError("Malformed approval grant.")

    version, payload_b64, signature = parts
    if version != GRANT_VERSION:
        raise ApprovalTokenError(f"Unsupported approval grant version: {version!r}")

    # Constant-time comparison: never let signature checking leak timing.
    if not hmac.compare_digest(signature, _sign(secret, payload_b64)):
        raise ApprovalTokenError("Approval grant signature is not valid.")

    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApprovalTokenError("Approval grant payload is unreadable.") from exc

    if not isinstance(claims, dict):
        raise ApprovalTokenError("Approval grant payload is unreadable.")

    required = {"jti", "wo", "crew", "urg", "by", "iat", "exp"}
    if not required.issubset(claims):
        raise ApprovalTokenError("Approval grant is missing required claims.")

    current = int(time.time()) if now is None else now
    if current >= int(claims["exp"]):
        raise ApprovalTokenError(
            "Approval grant has expired. Ask the maintenance lead to approve again."
        )
    if int(claims["iat"]) > current + CLOCK_SKEW_SECONDS:
        raise ApprovalTokenError("Approval grant is not valid yet.")

    # Binding: the grant must describe precisely the write being attempted.
    if int(claims["wo"]) != int(work_order_id):
        raise ApprovalTokenError(
            "Approval grant was issued for a different work order."
        )
    if int(claims["crew"]) != int(crew_id):
        raise ApprovalTokenError("Approval grant was issued for a different crew.")
    if claims["urg"] != urgency:
        raise ApprovalTokenError(
            "Approval grant was issued for a different urgency level."
        )

    return claims
