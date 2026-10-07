#!/bin/bash
# Render container entrypoint: run DB migrations (idempotent), then start API.
set -e
cd /srv/argus/services/ml
python -m app.migrate
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
