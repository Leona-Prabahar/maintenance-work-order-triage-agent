"""FastAPI application — the single entry point the frontend talks to.

Three routes, mapping one-to-one onto the workflow:

    POST /api/triage       read the queue and propose (writes nothing)
    POST /api/assignments  an approval click, and the only path that writes
    GET  /api/health       readiness, including whether the approval gate is armed

The frontend never touches the database, the MCP server, or the Anthropic API
directly.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .agent import AgentError, triage_queue
from .assignments import ApprovalError, assign_with_approval
from .config import settings
from .models import (
    AssignmentRequest,
    AssignmentResponse,
    HealthResponse,
    TriageRequest,
    TriageResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("triage.api")

app = FastAPI(
    title="Maintenance Work Order Triage API",
    description=(
        "Classifies incoming maintenance work orders by urgency and proposes a "
        "technician crew. Assignments are written only after a maintenance lead "
        "approves them."
    ),
    version="1.0.0",
)

_origins = ["*"] if settings.cors_origins.strip() == "*" else [
    o.strip() for o in settings.cors_origins.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    mcp_connected = False
    try:
        async with streamable_http_client(settings.mcp_server_url) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                await session.list_tools()
                mcp_connected = True
    except Exception as exc:  # noqa: BLE001 — health probe should never raise
        logger.warning("MCP health check failed: %s", exc)

    return HealthResponse(
        status="ok",
        mcp_connected=mcp_connected,
        model=settings.anthropic_model,
        approval_gate_configured=bool(settings.approval_signing_secret),
    )


@app.post("/api/triage", response_model=TriageResponse)
async def triage(request: TriageRequest | None = None) -> TriageResponse:
    """Classify the open queue and propose crews. Writes nothing."""
    request = request or TriageRequest()
    try:
        result = await triage_queue(
            include_assigned=request.include_assigned, limit=request.limit
        )
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Triage failed")
        raise HTTPException(
            status_code=502, detail=f"Failed to triage the queue: {exc}"
        ) from exc

    return TriageResponse(**result)


@app.post("/api/assignments", response_model=AssignmentResponse)
async def create_assignment(request: AssignmentRequest) -> AssignmentResponse:
    """Approve and write one assignment.

    Called only in response to a maintenance lead clicking Approve. The lead's
    name is signed into the approval grant and recorded against the assignment.
    """
    try:
        result = await assign_with_approval(request)
    except ApprovalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Assignment failed")
        raise HTTPException(
            status_code=502, detail=f"Failed to write the assignment: {exc}"
        ) from exc

    if not result.get("ok"):
        # A refused write is a normal outcome, not a server fault: the work order
        # may already be assigned, or the approval may have expired.
        raise HTTPException(
            status_code=409,
            detail=result.get("error", "The assignment was refused."),
        )

    return AssignmentResponse(
        ok=True,
        assignment=result["assignment"],
        message=(
            f"{result['assignment']['work_order_number']} assigned to "
            f"{result['assignment']['crew_name']}, approved by "
            f"{result['assignment']['approved_by']}."
        ),
    )
