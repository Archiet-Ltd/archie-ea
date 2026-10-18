#!/usr/bin/env bash
# Create (or re-key) the synthetic monitoring account and its credentials file.
# Idempotent: safe to re-run, rotates the password each time.
#
# The password is generated here and written only to /etc/archie-synthetic.env
# (root-only). It is never printed, never passed on a command line, and never
# leaves this host.
set -euo pipefail

EMAIL="synthetic-monitor@archiet.dev"
ORG=5                      # "QA Smoke Tenant" - tenant isolation keeps this
                           # account away from real customer data while still
                           # exercising the full stack.
ROLE_ID=2
ENT_ROLE="procurement"     # reaches the monitored pages; not platform_admin.
ENVFILE=/etc/archie-synthetic.env
BASE_URL="https://165-22-125-156.sslip.io"

cd /root/archie-ea

PW="$(openssl rand -base64 24 | tr -d '/+=' | head -c 28)"

# Hash inside the ALREADY-RUNNING container, importing werkzeug only. Never
# start a fresh process that imports the Flask app - that is what OOM-killed
# this box. The password goes in as an env var, not an argv, so it does not
# appear in the process list.
# method is the literal value of PASSWORD_HASH_METHOD in app/models/user.py:
# this script cannot import the app, so the one pinned format is duplicated
# here as a string.
HASH="$(docker compose exec -T -e MONPW="$PW" server python -c \
    'import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ["MONPW"], method="scrypt:32768:8:1"))' \
    | tr -d '\r\n')"

if [ -z "$HASH" ]; then
    echo "FAILED: could not hash the password inside the server container" >&2
    exit 1
fi

# Two statements rather than ON CONFLICT: this does not assume a unique index
# on email exists, and it works whether or not the row is already there.
SQL=$(mktemp)
trap 'rm -f "$SQL"' EXIT
printf "%s\n" \
"INSERT INTO users (email, password_hash, first_name, last_name, confirmed, role_id, organization_id, enterprise_role)" \
"SELECT '$EMAIL', '$HASH', 'Synthetic', 'Monitor', true, $ROLE_ID, $ORG, '$ENT_ROLE'" \
"WHERE NOT EXISTS (SELECT 1 FROM users WHERE email = '$EMAIL');" \
"UPDATE users SET password_hash = '$HASH', confirmed = true, enterprise_role = '$ENT_ROLE'," \
"    organization_id = $ORG, role_id = $ROLE_ID WHERE email = '$EMAIL';" > "$SQL"

docker compose exec -T postgres psql -U postgres -q archie < "$SQL"

# Confirm the row is really there before writing credentials that claim it is.
FOUND=$(docker compose exec -T postgres psql -U postgres -tAc \
    "SELECT count(*) FROM users WHERE email = '$EMAIL'" archie | tr -d '\r\n ')
if [ "$FOUND" != "1" ]; then
    echo "FAILED: expected exactly 1 monitor account, found '$FOUND'" >&2
    exit 1
fi

umask 077
cat > "$ENVFILE" <<ENVEOF
# Credentials for the Archie synthetic monitor (deploy/synthetic_monitor.py).
# Root-only. Rotate by re-running deploy/provision_monitor.sh.
SYNTHETIC_BASE_URL=$BASE_URL
SYNTHETIC_EMAIL=$EMAIL
SYNTHETIC_PASSWORD=$PW

# Set this to a Slack/Teams incoming-webhook URL to get notified on failure.
# Without it, failures land in the journal and nobody is told.
SYNTHETIC_ALERT_WEBHOOK=
ENVEOF
chmod 600 "$ENVFILE"

echo "monitor account ready: $EMAIL (org $ORG, role $ENT_ROLE)"
echo "credentials written to $ENVFILE (mode $(stat -c %a "$ENVFILE"), root-only)"
