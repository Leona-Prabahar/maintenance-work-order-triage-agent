#!/bin/bash
# Creates the least-privilege MySQL account the MCP server connects with.
#
# Defense in depth: the MCP server is the only component that touches the
# database, and even it cannot do more than the triage workflow needs.
#
#   * SELECT on everything        — reading the queue
#   * INSERT on assignments       — recording an approved assignment
#   * INSERT on approval_grants   — burning a single-use approval token
#   * INSERT on audit_log         — appending to the trail
#   * UPDATE (status) on work_orders — flipping New -> Assigned, that column only
#
# Deliberately NOT granted anywhere: DELETE, DROP, ALTER, CREATE, or UPDATE on
# assignments / approval_grants / audit_log. An assignment record, a redeemed
# approval, and an audit entry are all immutable once written — the database
# refuses to change them regardless of what the application asks for.
#
# This file must keep its execute bit. The MySQL entrypoint runs init scripts as
# root, and root's `test -x` reports true even for a plain 0644 file, so the
# entrypoint tries to exec this script either way. Without the bit that exec
# fails with "bad interpreter: Permission denied" and the account is silently
# never created — the seed data loads and the database still reports healthy.
#
# `set -e` only, no `-u`/`pipefail`: entrypoint versions that source this file
# rather than exec it would otherwise leak those options into the parent shell.
set -e

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE must be set}"
: "${DB_USER:?DB_USER must be set}"
: "${DB_PASSWORD:?DB_PASSWORD must be set}"

mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASSWORD}';

GRANT SELECT           ON \`${MYSQL_DATABASE}\`.*                 TO '${DB_USER}'@'%';
GRANT INSERT           ON \`${MYSQL_DATABASE}\`.assignments       TO '${DB_USER}'@'%';
GRANT INSERT           ON \`${MYSQL_DATABASE}\`.approval_grants   TO '${DB_USER}'@'%';
GRANT INSERT           ON \`${MYSQL_DATABASE}\`.audit_log         TO '${DB_USER}'@'%';
GRANT UPDATE (status)  ON \`${MYSQL_DATABASE}\`.work_orders       TO '${DB_USER}'@'%';

FLUSH PRIVILEGES;
SQL

echo "Application user '${DB_USER}' created with least-privilege grants."
