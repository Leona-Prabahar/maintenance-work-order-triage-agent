-- Maintenance work order triage — schema
--
-- Design notes:
--   * `assignments` is append-only and carries a UNIQUE work_order_id, so a work
--     order can be assigned exactly once. There is no UPDATE grant on this table
--     (see 03_app_user.sh), which makes the assignment record immutable.
--   * `approval_grants` records the redeemed approval token id (jti). The PRIMARY
--     KEY on jti is what makes an approval single-use: a replayed token cannot be
--     inserted a second time, so the write is rejected by the database itself.
--   * `audit_log` is insert-only and intentionally carries no foreign keys, so the
--     trail survives regardless of what happens to the operational tables.

CREATE TABLE IF NOT EXISTS machines (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    machine_code VARCHAR(24)  NOT NULL UNIQUE,
    name         VARCHAR(120) NOT NULL,
    area         VARCHAR(80)  NOT NULL,
    -- How badly the plant hurts when this asset is down. The agent uses this as
    -- one input when deciding production-stopping vs routine.
    criticality  ENUM('Critical','High','Standard') NOT NULL DEFAULT 'Standard',

    INDEX idx_area (area)
);

CREATE TABLE IF NOT EXISTS crews (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    crew_code  VARCHAR(16)  NOT NULL UNIQUE,
    name       VARCHAR(80)  NOT NULL,
    specialty  VARCHAR(200) NOT NULL,
    shift      VARCHAR(40)  NOT NULL,
    -- On-call crews can be dispatched outside their shift window; the agent is
    -- told to prefer one for safety-critical work raised off-shift.
    on_call    TINYINT(1)   NOT NULL DEFAULT 0,
    active     TINYINT(1)   NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS work_orders (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    work_order_number VARCHAR(24) NOT NULL UNIQUE,
    machine_id        INT         NOT NULL,
    reported_by       VARCHAR(80) NOT NULL,
    reporter_role     VARCHAR(60),
    description       TEXT        NOT NULL,
    reported_at       DATETIME    NOT NULL,
    -- 'New' means it is still sitting in the triage queue. It only ever moves to
    -- 'Assigned' through the approval-gated write path.
    status            ENUM('New','Assigned') NOT NULL DEFAULT 'New',
    created_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (machine_id) REFERENCES machines(id),
    INDEX idx_status (status),
    INDEX idx_reported_at (reported_at)
);

CREATE TABLE IF NOT EXISTS assignments (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    -- UNIQUE: one assignment per work order, enforced by the database.
    work_order_id INT NOT NULL UNIQUE,
    crew_id       INT NOT NULL,
    urgency       ENUM('safety_critical','production_stopping','routine') NOT NULL,
    rationale     TEXT,
    -- 'agent' when the lead approved the proposal as-is, 'human' when the lead
    -- changed the crew or urgency before approving.
    proposed_by   ENUM('agent','human') NOT NULL DEFAULT 'agent',
    -- The human who clicked Approve. Never a service account.
    approved_by   VARCHAR(80) NOT NULL,
    -- Id of the approval token that authorised this write.
    approval_jti  CHAR(36)    NOT NULL UNIQUE,
    assigned_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (work_order_id) REFERENCES work_orders(id),
    FOREIGN KEY (crew_id)       REFERENCES crews(id),
    INDEX idx_urgency (urgency)
);

CREATE TABLE IF NOT EXISTS approval_grants (
    -- Token id. PRIMARY KEY => an approval can be redeemed exactly once.
    jti           CHAR(36)    NOT NULL PRIMARY KEY,
    work_order_id INT         NOT NULL,
    crew_id       INT         NOT NULL,
    urgency       ENUM('safety_critical','production_stopping','routine') NOT NULL,
    approved_by   VARCHAR(80) NOT NULL,
    issued_at     DATETIME    NOT NULL,
    expires_at    DATETIME    NOT NULL,
    redeemed_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_work_order (work_order_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    event         VARCHAR(48) NOT NULL,
    work_order_id INT,
    actor         VARCHAR(80),
    detail        JSON,
    created_at    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_event (event),
    INDEX idx_work_order (work_order_id)
);
