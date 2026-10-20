#!/bin/bash
# Render container entrypoint.
#
# BOOT-SAFETY: a database outage must NEVER stop the API from becoming
# healthy. Migrations run first, but they are deliberately unable to abort
# this script: migrate.py already reports a database outage as a loud
# non-fatal warning by default, and the `|| echo` below additionally keeps a
# non-zero exit (e.g. MIGRATIONS_FATAL=1, or a crash inside migrate) from
# blocking uvicorn. uvicorn therefore always execs, binds PORT and answers
# /health -- which probes the database and reports reachability honestly.
#
# If a failed migration SHOULD block a release, run it as a Render
# preDeployCommand (see render.yaml) with MIGRATIONS_FATAL=1 instead of
# relying on the entrypoint.
set -u
cd /srv/argus/services/ml || exit 1

if ! python -m app.migrate; then
    echo "start.sh: migrate.py returned non-zero; starting the API anyway" \
         "(a database outage must not keep /health from answering)" >&2
fi

# --proxy-headers so uvicorn knows it sits behind a reverse proxy (it uses
# FORWARDED_ALLOW_IPS, default 127.0.0.1, as its uvicorn-level trust list; the
# real client-IP resolution is done by app/middleware.py from TRUSTED_PROXIES,
# which must be set or every client shares the proxy's rate-limit bucket).
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
