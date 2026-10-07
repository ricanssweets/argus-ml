# ARGUS ML API — Render image (build context = repo root).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Keep the monorepo layout (services/ml/app): predict.py resolves its
# calibration registry as <4 levels up>/services/ml/app/registry.
WORKDIR /srv/argus/services/ml
COPY services/ml/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
# Shared quant engine library (argus_quant): the live 8-engine pipeline
# imports it; without it the API degrades to the synthetic stub.
COPY packages/quant /tmp/argus-quant
RUN pip install /tmp/argus-quant && rm -rf /tmp/argus-quant
COPY services/ml/app ./app
COPY services/ml/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

# SQL migrations + seeds, applied by start.sh on every boot (idempotent).
COPY db/migrations /srv/argus/db/migrations
COPY db/seeds /srv/argus/db/seeds

EXPOSE 8000
CMD ["/usr/local/bin/start.sh"]
