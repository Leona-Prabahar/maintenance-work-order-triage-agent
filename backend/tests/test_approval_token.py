"""Tests for the approval gate.

These cover the first success criterion — no assignment is ever written without
an approval click — at the layer that enforces it against a determined caller
rather than a well-behaved one.

Single-use enforcement is *not* tested here: it lives in the PRIMARY KEY on
`approval_grants.jti`, because uniqueness is the one guarantee a stateless token
cannot make about itself. What is tested here is everything that must hold
before the database is even consulted.
"""
from __future__ import annotations

import base64
import json

import pytest

from app.approval_token import (
    ApprovalTokenError,
    mint_grant,
    verify_grant,
)

SECRET = "test-secret-not-the-real-one"
OTHER_SECRET = "a-different-secret"

BINDING = {"work_order_id": 2481, "crew_id": 4, "urgency": "safety_critical"}


def _mint(**overrides):
    kwargs = {
        **BINDING,
        "secret": SECRET,
        "approved_by": "j.reyes",
        "ttl_seconds": 120,
    }
    kwargs.update(overrides)
    return mint_grant(**kwargs)


def test_a_freshly_minted_grant_verifies_against_its_own_binding() -> None:
    token, claims = _mint()
    verified = verify_grant(token, secret=SECRET, **BINDING)
    assert verified["jti"] == claims["jti"]
    assert verified["by"] == "j.reyes"


def test_the_approver_is_carried_inside_the_signature() -> None:
    """The MCP tool reads the approver from the grant, never from an argument."""
    token, _ = _mint(approved_by="  a.okonkwo  ")
    assert verify_grant(token, secret=SECRET, **BINDING)["by"] == "a.okonkwo"


# --------------------------------------------------------------------------- #
# No grant at all — the case the success criterion is really about
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("token", ["", None, "   ", "not-a-token", "v1.only-two"],
                         ids=["empty", "none", "blank", "garbage", "truncated"])
def test_a_write_without_a_real_grant_is_refused(token) -> None:
    with pytest.raises(ApprovalTokenError):
        verify_grant(token, secret=SECRET, **BINDING)


def test_an_unsigned_but_well_formed_payload_is_refused() -> None:
    """Hand-rolling the claims gets you nowhere without the secret."""
    claims = {
        "jti": "11111111-1111-1111-1111-111111111111",
        "wo": 2481, "crew": 4, "urg": "safety_critical",
        "by": "attacker", "iat": 0, "exp": 9_999_999_999,
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")

    with pytest.raises(ApprovalTokenError, match="signature"):
        verify_grant(f"v1.{payload}.aGFuZC13YXZl", secret=SECRET, **BINDING)


def test_a_grant_signed_with_the_wrong_secret_is_refused() -> None:
    token, _ = _mint(secret=OTHER_SECRET)
    with pytest.raises(ApprovalTokenError, match="signature"):
        verify_grant(token, secret=SECRET, **BINDING)


def test_a_tampered_payload_is_refused() -> None:
    """Swap the crew in the payload and the signature stops matching."""
    token, _ = _mint()
    version, payload, signature = token.split(".")
    claims = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    claims["crew"] = 1
    tampered = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")

    with pytest.raises(ApprovalTokenError, match="signature"):
        verify_grant(
            f"{version}.{tampered}.{signature}",
            secret=SECRET,
            work_order_id=2481, crew_id=1, urgency="safety_critical",
        )


def test_an_unsupported_version_is_refused() -> None:
    token, _ = _mint()
    with pytest.raises(ApprovalTokenError, match="version"):
        verify_grant("v9" + token[2:], secret=SECRET, **BINDING)


# --------------------------------------------------------------------------- #
# Binding — a real approval must not be redirected onto a different write
# --------------------------------------------------------------------------- #
def test_a_grant_cannot_be_replayed_against_a_different_work_order() -> None:
    token, _ = _mint()
    with pytest.raises(ApprovalTokenError, match="different work order"):
        verify_grant(token, secret=SECRET, **{**BINDING, "work_order_id": 2482})


def test_a_grant_cannot_be_redirected_to_a_different_crew() -> None:
    token, _ = _mint()
    with pytest.raises(ApprovalTokenError, match="different crew"):
        verify_grant(token, secret=SECRET, **{**BINDING, "crew_id": 1})


def test_a_grant_cannot_be_reused_at_a_different_urgency() -> None:
    token, _ = _mint()
    with pytest.raises(ApprovalTokenError, match="different urgency"):
        verify_grant(token, secret=SECRET, **{**BINDING, "urgency": "routine"})


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
def test_an_expired_grant_is_refused() -> None:
    token, _ = _mint(ttl_seconds=60, now=1_000_000)
    with pytest.raises(ApprovalTokenError, match="expired"):
        verify_grant(token, secret=SECRET, now=1_000_061, **BINDING)


def test_a_grant_is_valid_right_up_to_its_expiry() -> None:
    token, _ = _mint(ttl_seconds=60, now=1_000_000)
    assert verify_grant(token, secret=SECRET, now=1_000_059, **BINDING)


def test_a_grant_from_the_far_future_is_refused() -> None:
    token, _ = _mint(now=2_000_000)
    with pytest.raises(ApprovalTokenError, match="not valid yet"):
        verify_grant(token, secret=SECRET, now=1_000_000, **BINDING)


def test_small_clock_drift_between_containers_is_tolerated() -> None:
    token, _ = _mint(ttl_seconds=120, now=1_000_010)
    assert verify_grant(token, secret=SECRET, now=1_000_000, **BINDING)


# --------------------------------------------------------------------------- #
# Configuration mistakes must fail closed
# --------------------------------------------------------------------------- #
def test_minting_without_a_secret_fails_closed() -> None:
    with pytest.raises(ApprovalTokenError, match="signing secret"):
        _mint(secret="")


def test_verifying_without_a_secret_fails_closed() -> None:
    token, _ = _mint()
    with pytest.raises(ApprovalTokenError, match="signing secret"):
        verify_grant(token, secret="", **BINDING)


def test_an_approval_must_name_a_person() -> None:
    with pytest.raises(ApprovalTokenError, match="name the person"):
        _mint(approved_by="   ")


def test_an_unknown_urgency_cannot_be_minted() -> None:
    with pytest.raises(ApprovalTokenError, match="urgency"):
        _mint(urgency="whenever")


def test_each_approval_gets_its_own_id() -> None:
    """Distinct ids are what let the database reject a replayed approval."""
    ids = {_mint()[1]["jti"] for _ in range(50)}
    assert len(ids) == 50
