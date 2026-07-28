"""Tests for the orchestration layer's decisions.

Two things are checked here that the safety and token tests cannot cover:

  * the write tool is filtered out before the tool list reaches the model, and
  * merging the model's plan with the queue always produces rows that are
    awaiting approval, correctly ordered, and honest about what the model said.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.agent import (
    READ_ONLY_MCP_TOOLS,
    _build_proposals,
    _mcp_tools_to_anthropic,
)

CREWS = [
    {"id": 1, "crew_code": "MECH-A", "name": "Mechanical & Hydraulics",
     "specialty": "Presses, hydraulics", "shift": "Day", "on_call": False},
    {"id": 2, "crew_code": "ELEC-B", "name": "Electrical & Controls",
     "specialty": "Panels, PLCs", "shift": "Day", "on_call": False},
    {"id": 4, "crew_code": "SRR-D", "name": "Safety Rapid Response",
     "specialty": "Guarding, isolation", "shift": "24/7", "on_call": True},
]


def _work_order(wo_id: int, number: str, description: str,
                reported_at: str = "2026-07-28T08:00:00", **overrides) -> dict:
    work_order = {
        "id": wo_id,
        "work_order_number": number,
        "machine_code": "HYD-PR-04",
        "machine_name": "Hydraulic Press #4",
        "machine_area": "Press Shop",
        "machine_criticality": "Critical",
        "reported_by": "R. Okafor",
        "reporter_role": "Press Operator",
        "description": description,
        "reported_at": reported_at,
        "status": "New",
        "assignment": None,
    }
    work_order.update(overrides)
    return work_order


# --------------------------------------------------------------------------- #
# The model is never handed the write tool
# --------------------------------------------------------------------------- #
def test_the_write_tool_is_not_in_the_read_only_set() -> None:
    assert "assign_crew" not in READ_ONLY_MCP_TOOLS
    assert "get_work_order_queue" in READ_ONLY_MCP_TOOLS


def test_the_write_tool_is_stripped_before_the_model_sees_the_tool_list() -> None:
    mcp_tools = [
        SimpleNamespace(name="get_work_order_queue", description="read",
                        inputSchema={"type": "object", "properties": {}}),
        SimpleNamespace(name="assign_crew", description="write",
                        inputSchema={"type": "object", "properties": {}}),
    ]
    exposed = [tool["name"] for tool in _mcp_tools_to_anthropic(mcp_tools)]
    assert exposed == ["get_work_order_queue"]


def test_an_unknown_future_tool_is_withheld_by_default() -> None:
    """The filter is an allowlist, so a new tool is not exposed by accident."""
    mcp_tools = [
        SimpleNamespace(name="delete_everything", description="",
                        inputSchema={"type": "object", "properties": {}}),
    ]
    assert _mcp_tools_to_anthropic(mcp_tools) == []


# --------------------------------------------------------------------------- #
# Merging the model's plan with the queue
# --------------------------------------------------------------------------- #
def test_a_clean_proposal_is_carried_through_untouched() -> None:
    work_orders = [_work_order(1, "WO-2483", "Conveyor drive motor hums, belt dead.")]
    plan = {"classifications": [{
        "work_order_number": "WO-2483",
        "urgency": "production_stopping",
        "crew_code": "MECH-A",
        "rationale": "Whole line is standing; mechanical drive fault.",
    }]}

    [row] = _build_proposals(work_orders, CREWS, plan, [])

    assert row["urgency"] == "production_stopping"
    assert row["safety_override"] is False
    assert row["proposed_crew_code"] == "MECH-A"
    assert row["rationale"].startswith("Whole line is standing")


def test_a_missed_hazard_is_raised_and_rerouted_to_the_safety_crew() -> None:
    """The model called it routine; the rule disagrees and says why."""
    work_orders = [_work_order(
        2, "WO-2489",
        "The pinch point guard is missing its clip. Line output is fine.",
    )]
    plan = {"classifications": [{
        "work_order_number": "WO-2489",
        "urgency": "routine",
        "crew_code": "MECH-A",
        "rationale": "Cosmetic, line still running.",
    }]}

    [row] = _build_proposals(work_orders, CREWS, plan, [])

    assert row["urgency"] == "safety_critical"
    assert row["model_urgency"] == "routine"
    assert row["safety_override"] is True
    assert row["proposed_crew_code"] == "SRR-D"
    assert "Safety rule applied" in row["rationale"]
    # The model's own note is kept rather than quietly discarded.
    assert "Cosmetic, line still running." in row["rationale"]
    assert {s["label"] for s in row["safety_signals"]} >= {"pinch point"}


def test_a_correctly_classified_hazard_keeps_the_model_s_crew_choice() -> None:
    """No override fired, so there is no reason to second-guess the routing."""
    work_orders = [_work_order(3, "WO-2486", "Sparks from the breaker, exposed wire.")]
    plan = {"classifications": [{
        "work_order_number": "WO-2486",
        "urgency": "safety_critical",
        "crew_code": "ELEC-B",
        "rationale": "Live electrical hazard on the assembly line.",
    }]}

    [row] = _build_proposals(work_orders, CREWS, plan, [])

    assert row["urgency"] == "safety_critical"
    assert row["safety_override"] is False
    assert row["proposed_crew_code"] == "ELEC-B"


def test_an_unclassified_work_order_is_surfaced_for_manual_triage() -> None:
    work_orders = [_work_order(4, "WO-2495", "Chip conveyor squeaking.")]
    notes: list[str] = []

    [row] = _build_proposals(work_orders, CREWS, {"classifications": []}, notes)

    assert row["urgency"] == "routine"
    assert row["proposed_crew_id"] is None
    assert "triage it manually" in row["rationale"]
    assert any("WO-2495" in note for note in notes)


def test_a_hazard_is_still_caught_when_the_model_returns_nothing_at_all() -> None:
    """A model failure must not be able to hide an injury-risk report."""
    work_orders = [_work_order(5, "WO-2490", "Steam leak, tech caught a burn.")]

    [row] = _build_proposals(work_orders, CREWS, None, [])

    assert row["urgency"] == "safety_critical"
    assert row["safety_override"] is True
    assert row["proposed_crew_code"] == "SRR-D"


def test_a_crew_code_that_is_not_on_the_roster_is_reported_not_invented() -> None:
    work_orders = [_work_order(6, "WO-2484", "Labels going on crooked.")]
    notes: list[str] = []
    plan = {"classifications": [{
        "work_order_number": "WO-2484",
        "urgency": "routine",
        "crew_code": "GHOST-Z",
        "rationale": "Cosmetic defect.",
    }]}

    [row] = _build_proposals(work_orders, CREWS, plan, notes)

    assert row["proposed_crew_id"] is None
    assert any("GHOST-Z" in note for note in notes)


def test_work_order_numbers_are_matched_case_insensitively() -> None:
    work_orders = [_work_order(7, "WO-2488", "Coolant low on lathe 3.")]
    plan = {"classifications": [{
        "work_order_number": "wo-2488",
        "urgency": "routine",
        "crew_code": "mech-a",
        "rationale": "Consumable top-up.",
    }]}

    [row] = _build_proposals(work_orders, CREWS, plan, [])

    assert row["proposed_crew_code"] == "MECH-A"
    assert row["rationale"] == "Consumable top-up."


# --------------------------------------------------------------------------- #
# Every row comes back awaiting approval, in triage order
# --------------------------------------------------------------------------- #
def test_every_unassigned_row_is_awaiting_approval_and_carries_no_assignment() -> None:
    work_orders = [
        _work_order(8, "WO-A", "Coolant low."),
        _work_order(9, "WO-B", "Exposed wire in the panel."),
        _work_order(10, "WO-C", "Line down, motor humming."),
    ]
    plan = {"classifications": [
        {"work_order_number": "WO-A", "urgency": "routine",
         "crew_code": "MECH-A", "rationale": "."},
        {"work_order_number": "WO-B", "urgency": "safety_critical",
         "crew_code": "SRR-D", "rationale": "."},
        {"work_order_number": "WO-C", "urgency": "production_stopping",
         "crew_code": "MECH-A", "rationale": "."},
    ]}

    rows = _build_proposals(work_orders, CREWS, plan, [])

    assert all(row["awaiting_approval"] for row in rows)
    assert all(row["assignment"] is None for row in rows)


def test_the_queue_comes_back_safety_first_even_when_safety_is_oldest() -> None:
    work_orders = [
        _work_order(11, "WO-NEW", "Coolant low.", reported_at="2026-07-28T11:00:00"),
        _work_order(12, "WO-OLD", "Exposed wire in the panel.",
                    reported_at="2026-07-28T06:00:00"),
        _work_order(13, "WO-MID", "Line down, motor humming.",
                    reported_at="2026-07-28T09:00:00"),
    ]
    plan = {"classifications": [
        {"work_order_number": "WO-NEW", "urgency": "routine",
         "crew_code": "MECH-A", "rationale": "."},
        {"work_order_number": "WO-OLD", "urgency": "safety_critical",
         "crew_code": "ELEC-B", "rationale": "."},
        {"work_order_number": "WO-MID", "urgency": "production_stopping",
         "crew_code": "MECH-A", "rationale": "."},
    ]}

    order = [row["work_order_number"] for row in
             _build_proposals(work_orders, CREWS, plan, [])]

    assert order == ["WO-OLD", "WO-MID", "WO-NEW"]


def test_an_already_assigned_work_order_is_not_awaiting_approval() -> None:
    assigned = {
        "crew_id": 1, "crew_code": "MECH-A", "crew_name": "Mechanical & Hydraulics",
        "urgency": "routine", "rationale": "Done.", "proposed_by": "agent",
        "approved_by": "j.reyes", "assigned_at": "2026-07-28T09:15:00",
    }
    work_orders = [_work_order(14, "WO-DONE", "Coolant low.",
                               status="Assigned", assignment=assigned)]
    plan = {"classifications": [{
        "work_order_number": "WO-DONE", "urgency": "routine",
        "crew_code": "MECH-A", "rationale": "Consumable.",
    }]}

    [row] = _build_proposals(work_orders, CREWS, plan, [])

    assert row["awaiting_approval"] is False
    assert row["assignment"]["approved_by"] == "j.reyes"
