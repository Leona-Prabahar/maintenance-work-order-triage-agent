"""The write path — reachable only from an explicit human approval.

This module is the *only* place in the backend that calls the MCP write tool,
and `mint_approval_grant` is the only place a grant is ever created. Both are
called from exactly one route, POST /api/assignments, which exists to service
an Approve click in the triage dashboard.

The model does not reach this code. It has no tool that leads here, and the
route it would need to hit is not exposed to it.
"""
from __future__ import annotations

import logging

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .agent import _tool_result_to_data
from .approval_token import ApprovalTokenError, mint_grant
from .config import settings
from .models import AssignmentRequest

logger = logging.getLogger("triage.assignments")


class ApprovalError(RuntimeError):
    """Raised when an approval cannot be minted or is rejected downstream."""


async def assign_with_approval(request: AssignmentRequest) -> dict:
    """Mint an approval grant for this click and redeem it against the MCP server.

    Returns the MCP tool's response dict: ``{ok, assignment}`` on success, or
    ``{error, code}`` if the write was refused.
    """
    if not settings.approval_signing_secret:
        raise ApprovalError(
            "No approval signing secret is configured, so no assignment can be "
            "approved. Set APPROVAL_SIGNING_SECRET on the backend and the MCP "
            "server."
        )

    try:
        grant, claims = mint_grant(
            secret=settings.approval_signing_secret,
            work_order_id=request.work_order_id,
            crew_id=request.crew_id,
            urgency=request.urgency,
            approved_by=request.approved_by,
            ttl_seconds=settings.approval_ttl_seconds,
        )
    except ApprovalTokenError as exc:
        raise ApprovalError(str(exc)) from exc

    logger.info(
        "Approval %s minted by %s for work order %s -> crew %s (%s)",
        claims["jti"], claims["by"], request.work_order_id, request.crew_id,
        request.urgency,
    )

    async with streamablehttp_client(settings.mcp_server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = _tool_result_to_data(
                await session.call_tool(
                    "assign_crew",
                    {
                        "work_order_id": request.work_order_id,
                        "crew_id": request.crew_id,
                        "urgency": request.urgency,
                        "approval_grant": grant,
                        "rationale": request.rationale,
                        "proposed_by": request.proposed_by,
                    },
                )
            )

    if not result.get("ok"):
        logger.warning(
            "Assignment refused for work order %s: %s",
            request.work_order_id, result.get("error"),
        )

    return result
