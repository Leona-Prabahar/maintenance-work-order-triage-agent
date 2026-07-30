"""The orchestration layer.

Connects to the maintenance MCP server, exposes its *read* tool to Claude, and
runs a manual tool-use loop that produces one triage proposal per open work
order. The model classifies urgency and proposes a crew; it does not assign
anyone.

The write tool is filtered out before the tool list is ever sent to the model
(`READ_ONLY_MCP_TOOLS` below). This is the first of the three independent
reasons no assignment can be written without a human approval:

  1. the model is never given the write tool, so it cannot call it;
  2. the backend's write path is reachable only from POST /api/assignments,
     which exists to service an approval click; and
  3. the MCP write tool refuses any call without a signed, single-use approval
     grant bound to that exact work order, crew and urgency.

Removing any one of the three still leaves a system that cannot write without a
human. That is the point.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import settings
from .safety import (
    URGENCY_LEVELS,
    apply_safety_override,
    sort_proposals,
)

logger = logging.getLogger("triage.agent")

#: MCP tools the model is allowed to see. `assign_crew` is deliberately absent.
READ_ONLY_MCP_TOOLS = frozenset({"get_work_order_queue"})

#: Client-side tool the model uses to hand back a structured plan. It writes
#: nothing — the backend just collects the arguments.
SUBMIT_TRIAGE_PLAN_TOOL: dict[str, Any] = {
    "name": "submit_triage_plan",
    "description": (
        "Submit your triage classification for every open work order. This "
        "records a proposal for the maintenance lead to review. It does NOT "
        "assign anyone — nothing is dispatched until a human approves."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "description": "One entry per open work order. Do not omit any.",
                "items": {
                    "type": "object",
                    "properties": {
                        "work_order_number": {
                            "type": "string",
                            "description": "e.g. WO-2481",
                        },
                        "urgency": {
                            "type": "string",
                            "enum": list(URGENCY_LEVELS),
                        },
                        "crew_code": {
                            "type": "string",
                            "description": "Crew code from the roster, e.g. MECH-A.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": (
                                "One or two sentences the maintenance lead can "
                                "check at a glance: why this urgency, why this crew."
                            ),
                        },
                    },
                    "required": [
                        "work_order_number",
                        "urgency",
                        "crew_code",
                        "rationale",
                    ],
                },
            }
        },
        "required": ["classifications"],
    },
}

SYSTEM_PROMPT = """\
You are the triage assistant for a manufacturing plant's maintenance desk. \
Machine operators raise work orders all shift. Your job is to read the incoming \
queue, classify each work order's urgency, and propose the crew best placed to \
do the work.

You are proposing, not deciding. A maintenance lead reviews every row and clicks \
Approve before anything is dispatched. You have no tool that assigns a crew, and \
you must never claim that work has been assigned, dispatched or scheduled.

How to work:
1. Call `get_work_order_queue` to read the open queue and the crew roster.
2. Classify every open work order.
3. Call `submit_triage_plan` exactly once with one entry per open work order.

Urgency levels, most severe first:

- `safety_critical` — the report mentions any risk to a person. Injuries or near \
misses, defeated or missing guarding, bypassed interlocks or light curtains, \
faulty emergency stops, exposed or live conductors, arc flash, sparking, fire or \
smoke, hot surfaces or steam on a walkway, chemical or gas release, failed \
ventilation. When in doubt between safety_critical and anything else, choose \
safety_critical.
- `production_stopping` — nobody is at risk, but output has stopped or is being \
scrapped: a line down, a machine offline, parts failing quality, a plant-wide \
utility degraded enough to halt work.
- `routine` — everything else. Drift and calibration, cosmetic defects, \
consumables, lubrication, planned maintenance items, minor faults where the \
machine keeps running.

The plant's standing rule is that anything mentioning injury risk is \
safety-critical, no matter how minor the machine fault sounds or how calm the \
operator's wording is. A report can read like a routine niggle and still be \
safety-critical: "the guard clip is missing so operators reach in to clear jams, \
output is fine" is safety_critical, not routine.

Choosing a crew:
- Match the crew's speciality to the actual work. Read the roster returned by \
the tool rather than assuming which crews exist.
- Safety-critical work goes to the safety rapid response crew when one is on the \
roster, because it needs isolating and making safe before the underlying repair.
- Prefer an on-call crew for urgent work raised outside a day-shift crew's hours.
- Machine criticality is a tie-breaker for urgency between production_stopping \
and routine, not a reason to downgrade a safety report.

Write each rationale for a busy maintenance lead: one or two plain sentences \
naming the deciding detail. No preamble, no restating the description.
"""


class AgentError(RuntimeError):
    """Raised when the agent cannot complete a request."""


def _tool_result_to_data(result: Any) -> dict:
    """Normalise an MCP call_tool result into a plain dict."""
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)
    return {"error": "Unreadable tool result."}


def _mcp_tools_to_anthropic(mcp_tools) -> list[dict]:
    """Convert MCP tool definitions, dropping anything that is not read-only."""
    tools = []
    for tool in mcp_tools:
        if tool.name not in READ_ONLY_MCP_TOOLS:
            logger.debug("Withholding non-read tool from the model: %s", tool.name)
            continue
        tools.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
            }
        )
    return tools


async def triage_queue(include_assigned: bool = False, limit: int = 100) -> dict:
    """Read the queue, classify it, and return proposals awaiting approval."""
    if not settings.anthropic_api_key:
        raise AgentError("Server is not configured with an ANTHROPIC_API_KEY.")

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    notes: list[str] = []

    async with streamable_http_client(settings.mcp_server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            anthropic_tools = _mcp_tools_to_anthropic(tools_result.tools)
            if not anthropic_tools:
                raise AgentError(
                    "The MCP server exposed no readable tools; cannot triage."
                )
            anthropic_tools.append(SUBMIT_TRIAGE_PLAN_TOOL)

            # The backend reads the queue itself as well. The safety rule must run
            # against the operators' raw words, not against whatever the model
            # chose to repeat back.
            queue = _tool_result_to_data(
                await session.call_tool(
                    "get_work_order_queue",
                    {"include_assigned": include_assigned, "limit": limit},
                )
            )
            if "error" in queue:
                raise AgentError(f"Could not read the work order queue: {queue['error']}")

            work_orders = queue.get("work_orders", [])
            crews = queue.get("crews", [])
            if not work_orders:
                return {
                    "generated_at": queue.get("generated_at", ""),
                    "model": settings.anthropic_model,
                    "proposals": [],
                    "crews": crews,
                    "open_count": 0,
                    "safety_count": 0,
                    "notes": ["The work order queue is empty."],
                }

            messages: list[dict] = [
                {
                    "role": "user",
                    "content": (
                        "Triage the current maintenance queue. Read it with "
                        "`get_work_order_queue`, then submit one classification "
                        "per open work order with `submit_triage_plan`."
                    ),
                }
            ]

            plan: dict | None = None

            for _ in range(settings.max_tool_iterations):
                response = await client.messages.create(
                    model=settings.anthropic_model,
                    max_tokens=settings.max_tokens,
                    system=SYSTEM_PROMPT,
                    tools=anthropic_tools,
                    messages=messages,
                )

                if response.stop_reason != "tool_use":
                    break

                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    if block.name == "submit_triage_plan":
                        plan = block.input
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(
                                    {
                                        "recorded": True,
                                        "note": "Proposals are queued for the "
                                                "maintenance lead's approval. "
                                                "Nothing has been assigned.",
                                    }
                                ),
                            }
                        )
                        continue

                    # Any other tool must be one we allowed through the filter.
                    if block.name not in READ_ONLY_MCP_TOOLS:
                        logger.warning(
                            "Model attempted a tool outside the read-only set: %s",
                            block.name,
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(
                                    {"error": "That tool is not available to you."}
                                ),
                                "is_error": True,
                            }
                        )
                        continue

                    data = _tool_result_to_data(
                        await session.call_tool(block.name, block.input)
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(data),
                            "is_error": "error" in data,
                        }
                    )

                messages.append({"role": "user", "content": tool_results})

                if plan is not None:
                    break
            else:
                logger.warning("Tool-use loop hit the iteration cap.")

    if plan is None:
        notes.append(
            "The model did not return a classification. Every work order is shown "
            "unclassified for manual triage; the safety rule was still applied."
        )

    proposals = _build_proposals(work_orders, crews, plan, notes)

    return {
        "generated_at": queue.get("generated_at", ""),
        "model": settings.anthropic_model,
        "proposals": proposals,
        "crews": crews,
        "open_count": sum(1 for p in proposals if p["assignment"] is None),
        "safety_count": sum(1 for p in proposals if p["urgency"] == "safety_critical"),
        "notes": notes,
    }


def _build_proposals(
    work_orders: list[dict],
    crews: list[dict],
    plan: dict | None,
    notes: list[str],
) -> list[dict]:
    """Merge the model's classifications with the queue, then apply the rules."""
    by_number: dict[str, dict] = {}
    for entry in (plan or {}).get("classifications", []) or []:
        number = str(entry.get("work_order_number", "")).strip().upper()
        if number:
            by_number[number] = entry

    crews_by_code = {str(c["crew_code"]).upper(): c for c in crews}
    safety_crew = crews_by_code.get(settings.safety_crew_code.upper())

    unclassified: list[str] = []
    unknown_crews: set[str] = set()

    proposals: list[dict] = []
    for work_order in work_orders:
        number = str(work_order["work_order_number"])
        entry = by_number.get(number.upper(), {})

        if not entry:
            unclassified.append(number)

        model_urgency = str(entry.get("urgency", "routine"))
        rationale = str(entry.get("rationale", "")).strip()

        crew_code = str(entry.get("crew_code", "")).strip().upper()
        crew = crews_by_code.get(crew_code)
        if crew_code and crew is None:
            unknown_crews.add(crew_code)

        # ---- The safety rule, applied to the operator's own words ----
        decision = apply_safety_override(model_urgency, work_order["description"])

        if decision.overridden:
            # The model missed a hazard in this description, so its crew choice is
            # suspect too. Fall back to the safety crew and say so plainly.
            flagged = ", ".join(sorted({s.label for s in decision.signals}))
            if safety_crew is not None:
                crew = safety_crew
            rationale = (
                f"Safety rule applied: the report mentions {flagged}. Raised to "
                f"safety-critical automatically."
                + (f" Model's note: {rationale}" if rationale else "")
            )
        elif not rationale:
            rationale = (
                "No rationale was returned for this work order — triage it manually."
            )

        assignment = work_order.get("assignment")

        proposals.append(
            {
                "work_order_id": work_order["id"],
                "work_order_number": number,
                "machine_code": work_order["machine_code"],
                "machine_name": work_order["machine_name"],
                "machine_area": work_order["machine_area"],
                "machine_criticality": work_order["machine_criticality"],
                "reported_by": work_order["reported_by"],
                "reporter_role": work_order.get("reporter_role"),
                "description": work_order["description"],
                "reported_at": work_order["reported_at"],
                "status": work_order["status"],
                "urgency": decision.urgency,
                "model_urgency": decision.model_urgency,
                "safety_override": decision.overridden,
                "safety_signals": [s.as_dict() for s in decision.signals],
                "proposed_crew_id": crew["id"] if crew else None,
                "proposed_crew_code": crew["crew_code"] if crew else None,
                "proposed_crew_name": crew["name"] if crew else None,
                "rationale": rationale,
                # Always true for an unassigned row. The dashboard has no path
                # that writes without an explicit approval action.
                "awaiting_approval": assignment is None,
                "assignment": assignment,
            }
        )

    if unclassified and plan is not None:
        notes.append(
            f"{len(unclassified)} work order(s) came back unclassified and need "
            f"manual triage: {', '.join(unclassified)}."
        )
    if unknown_crews:
        notes.append(
            "The model referred to crew code(s) not on the roster: "
            f"{', '.join(sorted(unknown_crews))}. Pick a crew manually on those rows."
        )

    return sort_proposals(proposals)
