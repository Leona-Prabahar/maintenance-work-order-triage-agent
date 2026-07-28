"""Request/response schemas for the API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Urgency = Literal["safety_critical", "production_stopping", "routine"]


class SafetySignalOut(BaseModel):
    category: str
    category_label: str
    label: str
    matched_text: str = Field(
        ..., description="The exact phrase that triggered the safety rule."
    )


class CrewOut(BaseModel):
    id: int
    crew_code: str
    name: str
    specialty: str
    shift: str
    on_call: bool


class AssignmentOut(BaseModel):
    assignment_id: int | None = None
    crew_id: int
    crew_code: str
    crew_name: str
    urgency: Urgency
    rationale: str | None = None
    proposed_by: Literal["agent", "human"]
    approved_by: str
    assigned_at: str | None = None


class TriageProposal(BaseModel):
    """One row of the triage dashboard: a proposal, never a decision."""

    work_order_id: int
    work_order_number: str
    machine_code: str
    machine_name: str
    machine_area: str
    machine_criticality: str
    reported_by: str
    reporter_role: str | None = None
    description: str
    reported_at: str
    status: str

    urgency: Urgency = Field(..., description="Urgency after the safety rule is applied.")
    model_urgency: Urgency = Field(..., description="What the model classified it as.")
    safety_override: bool = Field(
        False,
        description="True when the safety rule raised the urgency above the model's.",
    )
    safety_signals: list[SafetySignalOut] = Field(default_factory=list)

    proposed_crew_id: int | None = None
    proposed_crew_code: str | None = None
    proposed_crew_name: str | None = None
    rationale: str = ""

    awaiting_approval: bool = Field(
        True,
        description="True until a maintenance lead approves; nothing is written before then.",
    )
    assignment: AssignmentOut | None = None


class TriageRequest(BaseModel):
    include_assigned: bool = Field(
        False, description="Also return work orders that are already assigned."
    )
    limit: int = Field(100, ge=1, le=200)


class TriageResponse(BaseModel):
    generated_at: str
    model: str
    proposals: list[TriageProposal]
    crews: list[CrewOut]
    open_count: int = 0
    safety_count: int = 0
    notes: list[str] = Field(
        default_factory=list,
        description="Anything the operator of the dashboard should know about this run.",
    )


class AssignmentRequest(BaseModel):
    """The payload of an explicit human approval click."""

    work_order_id: int
    crew_id: int
    urgency: Urgency
    approved_by: str = Field(
        ..., min_length=2, max_length=80,
        description="The maintenance lead approving this assignment.",
    )
    rationale: str = Field("", max_length=2000)
    proposed_by: Literal["agent", "human"] = Field(
        "agent",
        description="'agent' if approved as proposed, 'human' if the lead changed it.",
    )


class AssignmentResponse(BaseModel):
    ok: bool
    assignment: AssignmentOut | None = None
    message: str = ""


class HealthResponse(BaseModel):
    status: str
    mcp_connected: bool
    model: str
    approval_gate_configured: bool = Field(
        ..., description="False means no approval signing secret is set and no write can succeed."
    )
