"""MCP server for maintenance work order triage.

Transport: Streamable HTTP, so the FastAPI backend can reach it over the Docker
network. It exposes exactly two tools, matching the two things this workflow
needs to do:

    get_work_order_queue  — READ  the incoming queue and the crew roster
    assign_crew           — WRITE a crew assignment

The read tool is unrestricted. The write tool is gated: it performs no work at
all unless it is handed a signed, unexpired, unredeemed approval grant that is
bound to the exact work order, crew and urgency being written. Grants are minted
by the backend only in direct response to a maintenance lead clicking Approve.

The consequence is worth stating plainly: there is no argument you can pass to
`assign_crew`, and no prompt you can give the model, that writes an assignment
without a human approval behind it.
"""
from __future__ import annotations

import datetime
import decimal
import json
import os
from contextlib import contextmanager
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from mcp.server.fastmcp import FastMCP

from approval_token import URGENCY_LEVELS, ApprovalTokenError, verify_grant

# --------------------------------------------------------------------------- #
# Configuration (all via environment)
# --------------------------------------------------------------------------- #
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "db"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "triage_app"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "maintenance"),
    "charset": "utf8mb4",
    "cursorclass": DictCursor,
    "connect_timeout": 10,
    "read_timeout": 30,
    "autocommit": False,
}

MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))
APPROVAL_SIGNING_SECRET = os.getenv("APPROVAL_SIGNING_SECRET", "")

#: MySQL error number for a unique-constraint violation.
ER_DUP_ENTRY = 1062

mcp = FastMCP(
    "maintenance-triage",
    instructions=(
        "Maintenance work order triage. Use get_work_order_queue to read the "
        "incoming queue and the crew roster. Assignments are written only by "
        "assign_crew, which requires a human approval grant and is therefore "
        "not callable by an assistant on its own initiative."
    ),
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8001")),
)


# --------------------------------------------------------------------------- #
# Database helpers
# --------------------------------------------------------------------------- #
@contextmanager
def get_connection():
    conn = pymysql.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


def _json_safe(value: Any) -> Any:
    """Convert MySQL/Python types the JSON encoder can't handle."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _clean_row(row: dict) -> dict:
    return {k: _json_safe(v) for k, v in row.items()}


def _audit(cur, event: str, work_order_id: int | None, actor: str | None,
           detail: dict) -> None:
    """Append to the insert-only audit trail. Caller controls the transaction."""
    cur.execute(
        "INSERT INTO audit_log (event, work_order_id, actor, detail) "
        "VALUES (%s, %s, %s, %s)",
        (event, work_order_id, actor, json.dumps(detail, default=str)),
    )


def _audit_standalone(event: str, work_order_id: int | None, actor: str | None,
                      detail: dict) -> None:
    """Record an event in its own committed transaction.

    Used for refusals, which must be durable even though the surrounding
    assignment transaction never happens.
    """
    try:
        with get_connection() as conn, conn.cursor() as cur:
            _audit(cur, event, work_order_id, actor, detail)
            conn.commit()
    except pymysql.MySQLError:
        # Never let audit trouble mask the refusal we are about to return.
        pass


# --------------------------------------------------------------------------- #
# Tool 1 — READ
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_work_order_queue(include_assigned: bool = False, limit: int = 100) -> dict:
    """Read the maintenance work order queue and the technician crew roster.

    This is the only way to see what operators have reported. It is read-only and
    changes nothing.

    Args:
        include_assigned: also return work orders that have already been assigned.
            Defaults to False, i.e. only the untriaged queue.
        limit: maximum number of work orders to return (capped by the server).

    Returns:
        {generated_at, work_orders: [...], crews: [...], counts: {...}}

        Each work order carries the machine it was raised against, the machine's
        criticality to production, who reported it, the free-text description, and
        when it was reported. Assigned work orders also carry their assignment.

        Each crew carries its speciality, shift and on-call status so work can be
        routed to the crew that can actually do it.
    """
    limit = max(1, min(int(limit), MAX_ROWS))

    status_clause = "" if include_assigned else "WHERE wo.status = 'New'"

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT wo.id,
                   wo.work_order_number,
                   wo.reported_by,
                   wo.reporter_role,
                   wo.description,
                   wo.reported_at,
                   wo.status,
                   m.machine_code,
                   m.name        AS machine_name,
                   m.area        AS machine_area,
                   m.criticality AS machine_criticality,
                   a.crew_id     AS assigned_crew_id,
                   c.crew_code   AS assigned_crew_code,
                   c.name        AS assigned_crew_name,
                   a.urgency     AS assigned_urgency,
                   a.rationale   AS assigned_rationale,
                   a.proposed_by AS assigned_proposed_by,
                   a.approved_by AS assigned_approved_by,
                   a.assigned_at AS assigned_at
            FROM work_orders wo
            JOIN machines    m ON m.id = wo.machine_id
            LEFT JOIN assignments a ON a.work_order_id = wo.id
            LEFT JOIN crews       c ON c.id = a.crew_id
            {status_clause}
            ORDER BY wo.reported_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [_clean_row(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT id, crew_code, name, specialty, shift, on_call
            FROM crews
            WHERE active = 1
            ORDER BY crew_code
            """
        )
        crews = [_clean_row(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT status, COUNT(*) AS n FROM work_orders GROUP BY status"
        )
        counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}

    work_orders = []
    for r in rows:
        assignment = None
        if r["assigned_crew_id"] is not None:
            assignment = {
                "crew_id": r["assigned_crew_id"],
                "crew_code": r["assigned_crew_code"],
                "crew_name": r["assigned_crew_name"],
                "urgency": r["assigned_urgency"],
                "rationale": r["assigned_rationale"],
                "proposed_by": r["assigned_proposed_by"],
                "approved_by": r["assigned_approved_by"],
                "assigned_at": r["assigned_at"],
            }
        work_orders.append(
            {
                "id": r["id"],
                "work_order_number": r["work_order_number"],
                "machine_code": r["machine_code"],
                "machine_name": r["machine_name"],
                "machine_area": r["machine_area"],
                "machine_criticality": r["machine_criticality"],
                "reported_by": r["reported_by"],
                "reporter_role": r["reporter_role"],
                "description": r["description"],
                "reported_at": r["reported_at"],
                "status": r["status"],
                "assignment": assignment,
            }
        )

    for crew in crews:
        crew["on_call"] = bool(crew["on_call"])

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "work_orders": work_orders,
        "crews": crews,
        "counts": {
            "open": counts.get("New", 0),
            "assigned": counts.get("Assigned", 0),
            "returned": len(work_orders),
        },
    }


# --------------------------------------------------------------------------- #
# Tool 2 — WRITE (approval-gated)
# --------------------------------------------------------------------------- #
@mcp.tool()
def assign_crew(
    work_order_id: int,
    crew_id: int,
    urgency: str,
    approval_grant: str,
    rationale: str = "",
    proposed_by: str = "agent",
) -> dict:
    """Assign a technician crew to a work order. REQUIRES A HUMAN APPROVAL GRANT.

    This tool writes to the maintenance record. It cannot be used to explore
    options or to act on a proposal: without `approval_grant` — a signed
    authorisation produced when a maintenance lead clicks Approve in the triage
    dashboard — every call is refused and nothing is written.

    Args:
        work_order_id: the work order to assign.
        crew_id: the crew to assign it to.
        urgency: one of safety_critical, production_stopping, routine.
        approval_grant: the signed grant from the lead's approval. Must be bound
            to this exact work order, crew and urgency, and may be redeemed once.
        rationale: why this urgency and crew, recorded alongside the assignment.
        proposed_by: 'agent' if the lead approved the proposal unchanged, or
            'human' if the lead altered the crew or urgency first.

    Returns:
        {ok: true, assignment: {...}} on success, or {error, code} on refusal.
        The person recorded as approver is taken from the signed grant, never
        from an argument, so a caller cannot approve on someone else's behalf.
    """
    if urgency not in URGENCY_LEVELS:
        return {
            "error": f"Unknown urgency level: {urgency!r}. "
                     f"Expected one of {', '.join(URGENCY_LEVELS)}.",
            "code": "invalid_urgency",
        }
    if proposed_by not in ("agent", "human"):
        return {
            "error": "proposed_by must be 'agent' or 'human'.",
            "code": "invalid_proposed_by",
        }

    # ---- Gate 1: the approval must be genuine, current, and for THIS write ----
    try:
        claims = verify_grant(
            approval_grant,
            secret=APPROVAL_SIGNING_SECRET,
            work_order_id=work_order_id,
            crew_id=crew_id,
            urgency=urgency,
        )
    except ApprovalTokenError as exc:
        _audit_standalone(
            "assignment_refused",
            work_order_id,
            None,
            {"reason": str(exc), "crew_id": crew_id, "urgency": urgency},
        )
        return {"error": str(exc), "code": "approval_required"}

    approved_by = claims["by"]

    try:
        with get_connection() as conn, conn.cursor() as cur:
            conn.begin()

            # ---- Gate 2: the approval must never have been redeemed before ----
            # The PRIMARY KEY on jti turns replay into a database error.
            try:
                cur.execute(
                    """
                    INSERT INTO approval_grants
                        (jti, work_order_id, crew_id, urgency, approved_by,
                         issued_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, FROM_UNIXTIME(%s), FROM_UNIXTIME(%s))
                    """,
                    (claims["jti"], work_order_id, crew_id, urgency, approved_by,
                     claims["iat"], claims["exp"]),
                )
            except pymysql.err.IntegrityError as exc:
                conn.rollback()
                if exc.args and exc.args[0] == ER_DUP_ENTRY:
                    _audit_standalone(
                        "assignment_refused", work_order_id, approved_by,
                        {"reason": "approval grant already redeemed",
                         "jti": claims["jti"]},
                    )
                    return {
                        "error": "This approval has already been used. Each "
                                 "approval authorises exactly one assignment.",
                        "code": "approval_already_redeemed",
                    }
                raise

            # ---- Validate the target rows ----
            cur.execute(
                "SELECT id, work_order_number, status FROM work_orders "
                "WHERE id = %s FOR UPDATE",
                (work_order_id,),
            )
            work_order = cur.fetchone()
            if work_order is None:
                conn.rollback()
                return {
                    "error": f"Work order {work_order_id} does not exist.",
                    "code": "work_order_not_found",
                }
            if work_order["status"] == "Assigned":
                conn.rollback()
                return {
                    "error": f"{work_order['work_order_number']} has already been "
                             f"assigned. Assignments are final.",
                    "code": "already_assigned",
                }

            cur.execute(
                "SELECT id, crew_code, name FROM crews WHERE id = %s AND active = 1",
                (crew_id,),
            )
            crew = cur.fetchone()
            if crew is None:
                conn.rollback()
                return {
                    "error": f"Crew {crew_id} does not exist or is not active.",
                    "code": "crew_not_found",
                }

            # ---- Write ----
            cur.execute(
                """
                INSERT INTO assignments
                    (work_order_id, crew_id, urgency, rationale, proposed_by,
                     approved_by, approval_jti)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (work_order_id, crew_id, urgency, rationale or None, proposed_by,
                 approved_by, claims["jti"]),
            )
            assignment_id = cur.lastrowid

            cur.execute(
                "UPDATE work_orders SET status = 'Assigned' WHERE id = %s",
                (work_order_id,),
            )

            _audit(
                cur, "assignment_written", work_order_id, approved_by,
                {
                    "assignment_id": assignment_id,
                    "crew_code": crew["crew_code"],
                    "urgency": urgency,
                    "proposed_by": proposed_by,
                    "approval_jti": claims["jti"],
                },
            )

            conn.commit()

            cur.execute(
                "SELECT assigned_at FROM assignments WHERE id = %s", (assignment_id,)
            )
            assigned_at = _json_safe(cur.fetchone()["assigned_at"])

    except pymysql.MySQLError as exc:
        return {"error": f"Database error: {exc}", "code": "database_error"}

    return {
        "ok": True,
        "assignment": {
            "assignment_id": assignment_id,
            "work_order_id": work_order_id,
            "work_order_number": work_order["work_order_number"],
            "crew_id": crew_id,
            "crew_code": crew["crew_code"],
            "crew_name": crew["name"],
            "urgency": urgency,
            "rationale": rationale,
            "proposed_by": proposed_by,
            "approved_by": approved_by,
            "assigned_at": assigned_at,
        },
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
