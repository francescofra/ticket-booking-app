# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the Concert Ticket Booking demo.
#
# Stage 1 (builder): install Python dependencies into a virtualenv. Build deps
#   (gcc, build-essential) live only in this stage; the final image carries
#   just the compiled wheels.
# Stage 2 (runtime): copy the virtualenv onto python:3.11-slim, run as a
#   non-root user, expose 8000, start uvicorn with two workers.
#
# Final image size target: < 250 MB.

# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# build-essential is required to compile bcrypt and Pillow wheels if no
# prebuilt manylinux wheel is available for the current Python/platform combo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    LOG_LEVEL=INFO \
    PAYMENT_BCRYPT_COST=12 \
    PAYMENT_HASH_ROUNDS=4 \
    ENABLE_PDF=false

# Non-root user.
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Copy the prebuilt venv from the builder.
COPY --from=builder /opt/venv /opt/venv

# Copy app source (owned by non-root user).
COPY --chown=app:app main.py /app/main.py
COPY --chown=app:app templates /app/templates
COPY --chown=app:app static /app/static

USER app

EXPOSE 8000

# Container-level healthcheck. Kubernetes will additionally hit /health via
# its own probes, but this is useful for `docker run` and CI smoke tests.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
import os; \
url='http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health'; \
sys.exit(0 if urllib.request.urlopen(url,timeout=2).status==200 else 1)" || exit 1

# Two uvicorn workers is a sensible default for a 2-vCPU pod request and gives
# us real parallelism on the CPU-bound booking path. Override with
# `--workers N` at run time if you want to experiment.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
