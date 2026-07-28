# Maintenance Work Order Triage

Machine operators raise maintenance requests all shift — a leaking hydraulic
press, a mis-calibrated CNC, a flickering panel. This service reads that queue,
classifies each request by urgency, and proposes the technician crew best placed
to do the work.

It never dispatches anyone. Every row is a proposal until a maintenance lead
clicks Approve.

Two properties hold by construction rather than by prompt:

1. **No assignment is written without an approval click.** Three independent
   mechanisms enforce this; removing any one of them still leaves a system that
   cannot write on its own. See [The approval gate](#the-approval-gate).
2. **Every report mentioning injury risk surfaces at the top.** The safety rule
   is a deterministic lexicon that runs *after* the model, on the operator's raw
   words, and can only raise urgency. See [The safety rule](#the-safety-rule).

## Architecture

```
  Browser — React triage dashboard
     │
     │  /api/*   (same origin; nginx proxies to the backend)
     ▼
  FastAPI backend ───────────────► Anthropic Messages API
     │                              (classification only — the model is given
     │                               the read tool and nothing else)
     │  MCP over Streamable HTTP
     ▼
  MCP server  ──────────────────►  MySQL
     get_work_order_queue  READ
     assign_crew           WRITE, refused without a human approval grant
```

The browser never reaches MySQL, the MCP server, or the Anthropic API. The MCP
server is the only component with database credentials, and those credentials
cannot DELETE, DROP or ALTER anything.

### Request flow

**Triage** — reads, proposes, writes nothing:

1. The lead clicks *Run triage*; the dashboard calls `POST /api/triage`.
2. The backend opens an MCP session and lists the server's tools. It filters
   that list down to the read-only set before showing it to the model, so
   `assign_crew` is never in the model's tool list.
3. The model calls `get_work_order_queue`, classifies every open work order, and
   returns a plan through a client-side `submit_triage_plan` tool.
4. The backend applies the safety rule to each operator's original description,
   sorts the queue, and returns proposals — all marked *awaiting approval*.

**Approval** — the only path that writes:

5. The lead clicks *Approve* (or *Change*, adjusts the crew or urgency, then
   approves). The dashboard calls `POST /api/assignments`.
6. The backend mints a signed, single-use approval grant naming that lead, that
   work order, that crew and that urgency, and calls `assign_crew` with it.
7. The MCP server verifies the grant, burns it, writes the assignment, flips the
   work order to `Assigned`, and appends to the audit log — in one transaction.

## The approval gate

| # | Layer | What it stops |
|---|-------|---------------|
| 1 | The write tool is filtered out of the tool list sent to the model (`READ_ONLY_MCP_TOOLS` in `backend/app/agent.py`) | The model cannot call `assign_crew`, however it is prompted. The filter is an allowlist, so a tool added to the MCP server later is withheld by default. |
| 2 | The backend's write path is reachable only from `POST /api/assignments` (`backend/app/assignments.py`) | Nothing in the triage flow reaches the write path. `mint_grant` is called from exactly one place, servicing exactly one route. |
| 3 | `assign_crew` refuses any call without a valid grant (`mcp-server/server.py`) | A direct call to the MCP server — bypassing the backend entirely — still writes nothing. |

A grant is an HMAC-SHA256-signed statement carrying the approver's name, the
work order, the crew, the urgency, an id, and a short expiry. Verification
checks all of it:

- **Signature** — forged or hand-rolled grants are rejected; the secret is
  shared only between the backend and the MCP server.
- **Binding** — a grant issued for "WO-2481 → crew 4, safety-critical" cannot be
  replayed against a different work order, a different crew, or the same work
  order at a different urgency.
- **Expiry** — grants live 120 seconds by default. They are minted at the click
  and redeemed immediately.
- **Single use** — the grant id is inserted into `approval_grants`, whose PRIMARY
  KEY makes a second redemption a database error rather than a second
  assignment.
- **Fail closed** — with no signing secret configured, every write is refused and
  the dashboard shows a banner saying so.

The approver's name is read *out of the signed grant*, never from a tool
argument, so a caller cannot record an approval in someone else's name.

The database enforces the last word. The MCP server's account holds SELECT,
three table-scoped INSERTs, and `UPDATE (status)` on `work_orders` — nothing
more (`database/init/03_app_user.sh`). Assignments, redeemed approvals and audit
entries are immutable once written because no component has the privilege to
change them.

## The safety rule

> Anything mentioning injury risk is always classified as safety-critical.

That is a plant rule, not a judgement call, so it is implemented as one
(`backend/app/safety.py`). The model classifies; then a lexicon of ~50 patterns
scans the operator's own words for injury, near misses, defeated guarding,
bypassed interlocks and e-stops, uncontrolled energy, chemical release and fire.
Any hit forces `safety_critical`. The override can only raise urgency.

This is why it runs after the model rather than instead of it: a model that
misses a hazard, gets confused, or returns nothing at all cannot bury a safety
report. When the override does fire, the row also re-routes to the safety rapid
response crew — the model demonstrably misread that description, so its crew
choice is suspect too — and says so in the rationale.

The lexicon is tuned for a machine shop. Words that are routine in this setting
are matched only in phrasings that indicate a hazard: "the mill is cutting
oversize" is not an injury, "cut his hand" is; "barrel temperature is falling"
is not a fall hazard, "fell from the platform" is; a "shock absorber" is not an
electric shock. False alarms teach a maintenance lead to ignore the flag, which
would defeat the rule more thoroughly than missing a keyword.

Ordering follows from ranking `safety_critical` first in the sort key, so a
safety report raised six hours ago still sorts above a routine one raised a
minute ago. Time only breaks ties within an urgency band. The dashboard renders
the bands as labelled sections in the same order.

## Getting started

### Prerequisites

- Docker and Docker Compose
- An Anthropic API key

### Run it

```bash
cp .env.example .env
# edit .env — set ANTHROPIC_API_KEY, generate APPROVAL_SIGNING_SECRET,
# and change the database passwords
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # for the secret

docker compose up --build
```

Then open <http://localhost:3000>, enter your name as the maintenance lead, and
click **Run triage**.

The seeded queue holds 16 work orders. Five carry injury risk and are
deliberately among the *oldest* entries, so a dashboard that sorted by arrival
time would bury them. One of those five — the case erector with the missing
pinch point guard — is written to read like a routine niggle ("line output is
fine") to show the safety rule catching what a quick read would not.

### Verifying the two success criteria

**Nothing is written without approval.** Run triage, then check the database
before approving anything:

```bash
docker compose exec db mysql -uroot -p"$MYSQL_ROOT_PASSWORD" maintenance \
  -e "SELECT COUNT(*) AS assignments FROM assignments;"
```

It stays at 0 no matter how many times you re-run triage. Then try to write
without going through the dashboard:

```bash
docker compose exec backend python -c "
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client('http://mcp-server:8001/mcp') as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool('assign_crew', {
                'work_order_id': 1, 'crew_id': 1,
                'urgency': 'routine', 'approval_grant': ''})
            print(res.content[0].text)
asyncio.run(main())"
```

The call is refused with `approval_required`, and the refusal is recorded in
`audit_log`. Approve one row in the dashboard and the count becomes 1, with the
approving lead's name against it.

**Safety reports surface at the top.** The dashboard groups rows into
Safety-critical, Production-stopping and Routine sections in that order. Rows
raised by the rule are marked "raised by the safety rule", show what the model
had classified them as, and highlight the exact phrases that triggered it.

## Inspecting the database

The database is published on `127.0.0.1:3307` (host-only) so you can open it in
MySQL Workbench or any client. Credentials come from your `.env`.

| Field | Value |
|---|---|
| Host | `127.0.0.1` |
| Port | `3307` (`DB_HOST_PORT`) |
| Schema | `maintenance` (`DB_NAME`) |
| Full access | `root` / `MYSQL_ROOT_PASSWORD` |
| What the MCP server sees | `DB_USER` / `DB_PASSWORD` — least privilege |

Connect as `triage_app` rather than `root` to see the service's own view of the
world: `SELECT` works everywhere, but `DELETE FROM assignments` and
`UPDATE work_orders SET description = …` are refused by the server.

Useful queries while working the dashboard:

```sql
-- The triage queue, oldest first. Urgency lives on the assignment, so
-- everything here is still unclassified as far as the database knows.
SELECT w.work_order_number, m.machine_code, w.reported_by, w.status, w.reported_at
FROM work_orders w JOIN machines m ON m.id = w.machine_id
ORDER BY w.reported_at;

-- What has actually been dispatched, and who authorised it.
SELECT w.work_order_number, c.crew_code, a.urgency, a.approved_by,
       a.proposed_by, a.assigned_at
FROM assignments a
JOIN work_orders w ON w.id = a.work_order_id
JOIN crews c       ON c.id = a.crew_id
ORDER BY a.assigned_at DESC;

-- Every write and every refused write.
SELECT created_at, event, work_order_id, actor, detail
FROM audit_log ORDER BY id DESC;

-- Approvals that have been burned. One row per assignment, never more.
SELECT jti, work_order_id, approved_by, issued_at, expires_at, redeemed_at
FROM approval_grants;
```

Run the first and second queries before approving anything: the queue is full
and `assignments` is empty, however many times you re-run triage.

## API

### `POST /api/triage`

Reads the queue and returns proposals. Writes nothing.

```json
{ "include_assigned": false, "limit": 100 }
```

```json
{
  "generated_at": "2026-07-28T09:12:44+00:00",
  "model": "claude-sonnet-5",
  "proposals": [
    {
      "work_order_id": 9,
      "work_order_number": "WO-2489",
      "machine_name": "Packaging Line #4",
      "description": "The pinch point guard on the case erector is missing …",
      "urgency": "safety_critical",
      "model_urgency": "routine",
      "safety_override": true,
      "safety_signals": [
        { "category": "injury_hazard", "label": "pinch point",
          "matched_text": "pinch point" }
      ],
      "proposed_crew_code": "SRR-D",
      "rationale": "Safety rule applied: the report mentions pinch point …",
      "awaiting_approval": true,
      "assignment": null
    }
  ],
  "crews": [ … ],
  "open_count": 16,
  "safety_count": 5,
  "notes": []
}
```

### `POST /api/assignments`

An approval click. The only route that writes.

```json
{
  "work_order_id": 9,
  "crew_id": 4,
  "urgency": "safety_critical",
  "approved_by": "j.reyes",
  "rationale": "Isolate and fit a new clip before the line restarts.",
  "proposed_by": "agent"
}
```

Returns `200` with the written assignment, or `409` when the write is refused —
already assigned, approval expired, unknown crew. `503` means the approval gate
is not configured.

### `GET /api/health`

Reports MCP connectivity, the model in use, and whether the approval gate is
armed.

## Project layout

```
.
├── docker-compose.yml
├── .env.example
├── database/init/
│   ├── 01_schema.sql          tables; append-only assignments and audit log
│   ├── 02_seed.sql            12 machines, 5 crews, 16 sample work orders
│   └── 03_app_user.sh         least-privilege MySQL account for the MCP server
├── mcp-server/
│   ├── server.py              the two tools
│   └── approval_token.py      grant verification
├── backend/
│   ├── app/
│   │   ├── agent.py           MCP + Anthropic orchestration, read-only tool filter
│   │   ├── safety.py          the safety lexicon, override and ordering
│   │   ├── approval_token.py  grant minting (identical copy)
│   │   ├── assignments.py     the write path
│   │   ├── main.py            FastAPI routes
│   │   ├── models.py          request/response schemas
│   │   └── config.py          settings
│   └── tests/                 88 tests, no database or API key needed
└── frontend/
    └── src/
        ├── App.tsx            the dashboard
        └── components/
            ├── TriageRow.tsx              one row: proposal + Approve / Change
            ├── SummaryBar.tsx             counts by urgency, MCP status
            └── HighlightedDescription.tsx marks the phrases that tripped the rule
```

`approval_token.py` is duplicated between the two services on purpose: they ship
as separate images and must not share a Python package. Keep the copies
identical — `diff backend/app/approval_token.py mcp-server/approval_token.py`
should be empty.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Model used for classification. |
| `APPROVAL_SIGNING_SECRET` | — | Required for any write to succeed. Must match between the backend and the MCP server. |
| `APPROVAL_TTL_SECONDS` | `120` | How long a grant stays valid. |
| `SAFETY_CREW_CODE` | `SRR-D` | Crew that overridden safety rows re-route to. Empty disables the re-route. |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `maintenance` / `triage_app` / — | Least-privilege database account. |
| `MYSQL_ROOT_PASSWORD` | — | Provisions the bundled MySQL container. |
| `MAX_ROWS` | `200` | Server-side cap on rows returned by the read tool. |
| `CORS_ORIGINS` | `*` | Set your real origin in production. |

## Testing

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest
```

88 tests, no database or API key required:

- `test_safety.py` — every injury-risk phrasing is flagged, ordinary machine-shop
  language is not, the override beats any classification the model returns, and
  safety rows sort above everything regardless of report time.
- `test_approval_token.py` — missing, forged, tampered, expired, wrong-secret and
  wrongly-bound grants are all refused; misconfiguration fails closed.
- `test_agent_proposals.py` — the write tool never reaches the model's tool list,
  and merging the model's plan with the queue always yields rows awaiting
  approval, in triage order, honest about what the model said.

Single-use enforcement is not unit-tested because it lives in the database
schema, not in Python. Exercise it by approving the same row twice — the second
attempt returns `approval_already_redeemed`.

## Notes for production

- **Authentication.** The maintenance lead's name is typed into the dashboard.
  Replace it with your SSO identity and sign that into the grant instead, so the
  approver on an assignment is an authenticated principal.
- **Secrets.** `APPROVAL_SIGNING_SECRET` is the whole approval gate. Keep it in a
  secret manager, rotate it, and never let it reach the browser.
- **Reassignment.** Assignments are final by design: one per work order, no
  UPDATE privilege. If crews need to hand work over, add a supersede flow that
  appends a new record rather than editing the old one.
- **The lexicon is a floor, not a ceiling.** It encodes one plant's rule. Review
  it with your safety officer, and add terms from your own incident reports —
  the tests make additions cheap to verify.
- **Auditing.** `audit_log` records both written assignments and refused writes.
  Ship it somewhere durable and alert on `assignment_refused`: a burst of them
  means something is calling the write tool without going through the dashboard.
