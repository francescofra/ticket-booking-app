"""
Concert Ticket Booking - Cloud Computing project (Sapienza).

Stateless FastAPI app designed to demonstrate horizontal pod autoscaling on
Kubernetes (HPA on CPU). The /api/book endpoint deliberately performs realistic
CPU-bound work (bcrypt-based "payment verification" + QR generation + optional
PDF) so that traffic spikes translate into measurable CPU pressure, which is
what the HPA reacts to.

Architectural notes:
  - No disk writes, no per-pod session state: pods are fully replaceable.
  - Inventory is kept IN-MEMORY per pod. For a real production system this
    would have to live in a shared store (Redis, Postgres, etc.) to keep
    replicas consistent; here it is intentionally a soft simplification, since
    the goal is demonstrating autoscaling, not transactional correctness.
  - All configuration is environment-driven so experiments can change the CPU
    profile without rebuilding the image.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import secrets
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import bcrypt
import qrcode
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, Field, field_validator


# --------------------------------------------------------------------------- #
# Configuration (env-driven)
# --------------------------------------------------------------------------- #

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Logarithmic bcrypt cost factor. 12 ≈ 200-300ms per hash on a t3.medium.
PAYMENT_BCRYPT_COST: int = _env_int("PAYMENT_BCRYPT_COST", 12)

# How many sequential bcrypt hashes to perform per booking. Linear knob:
# total CPU per booking ≈ PAYMENT_HASH_ROUNDS × bcrypt(PAYMENT_BCRYPT_COST).
PAYMENT_HASH_ROUNDS: int = _env_int("PAYMENT_HASH_ROUNDS", 4)

# Toggle the optional PDF ticket generation. Adds ~50-100ms of CPU work.
ENABLE_PDF: bool = _env_bool("ENABLE_PDF", False)

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()


# --------------------------------------------------------------------------- #
# Logging (structured-ish, single line, stdout — Kubernetes captures it)
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=LOG_LEVEL,
    stream=sys.stdout,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)s}',
)
log = logging.getLogger("ticketing")


def _jlog(event: str, **fields: Any) -> str:
    """Build a JSON-compatible message body for the logger."""
    import json
    payload = {"event": event, **fields}
    return json.dumps(payload, default=str)


# --------------------------------------------------------------------------- #
# In-memory inventory (per-pod, soft consistency — see module docstring)
# --------------------------------------------------------------------------- #
#
# NOTE: in a production deployment this state MUST live in a shared datastore
# (e.g. Redis with WATCH/MULTI or a relational DB with row-level locking) so
# that all pod replicas see the same inventory. We keep it in-memory here on
# purpose: the project's goal is to demonstrate Kubernetes horizontal scaling
# on CPU, not transactional consistency. Each replica therefore owns its own
# view of seat availability, which is acceptable for a load-test demo.

_inventory_lock = threading.Lock()

CONCERTS: list[dict[str, Any]] = [
    {
        "id": "c1",
        "artist": "Arctic Monkeys",
        "date": "2026-07-12",
        "venue": "Stadio Olimpico, Roma",
        "price_eur": 65.0,
        "total_seats": 5000,
        "available_seats": 5000,
    },
    {
        "id": "c2",
        "artist": "Tame Impala",
        "date": "2026-08-03",
        "venue": "Ippodromo Capannelle, Roma",
        "price_eur": 55.0,
        "total_seats": 5000,
        "available_seats": 5000,
    },
    {
        "id": "c3",
        "artist": "Måneskin",
        "date": "2026-09-21",
        "venue": "Circo Massimo, Roma",
        "price_eur": 75.0,
        "total_seats": 5000,
        "available_seats": 5000,
    },
    {
        "id": "c4",
        "artist": "Billie Eilish",
        "date": "2026-10-15",
        "venue": "Palazzo dello Sport, Roma",
        "price_eur": 80.0,
        "total_seats": 5000,
        "available_seats": 5000,
    },
    {
        "id": "c5",
        "artist": "The Weeknd",
        "date": "2026-11-02",
        "venue": "Stadio Olimpico, Roma",
        "price_eur": 95.0,
        "total_seats": 5000,
        "available_seats": 5000,
    },
]

_CONCERTS_BY_ID: dict[str, dict[str, Any]] = {c["id"]: c for c in CONCERTS}


# --------------------------------------------------------------------------- #
# Metrics (also in-memory and per-pod — fine for a demo)
# --------------------------------------------------------------------------- #

_metrics_lock = threading.Lock()
_metrics: dict[str, Any] = {
    "requests_by_endpoint": {},
    "bookings_total": 0,
    "bookings_failed": 0,
    "booking_processing_ms_sum": 0.0,
    "booking_processing_ms_count": 0,
    "started_at": datetime.utcnow().isoformat() + "Z",
    "pod_id": os.getenv("HOSTNAME", "local"),
}


def _bump_endpoint(path: str) -> None:
    with _metrics_lock:
        counts = _metrics["requests_by_endpoint"]
        counts[path] = counts.get(path, 0) + 1


def _record_booking(processing_ms: float, success: bool) -> None:
    with _metrics_lock:
        if success:
            _metrics["bookings_total"] += 1
        else:
            _metrics["bookings_failed"] += 1
        _metrics["booking_processing_ms_sum"] += processing_ms
        _metrics["booking_processing_ms_count"] += 1


# --------------------------------------------------------------------------- #
# Domain logic
# --------------------------------------------------------------------------- #

class BookingRequest(BaseModel):
    concert_id: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1, le=10)
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    payment_token: str = Field(min_length=4, max_length=128)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


def _simulate_payment_processing(payment_token: str) -> str:
    """CPU-bound 'payment verification'.

    We hash the payment token multiple times with bcrypt. This intentionally
    burns CPU — that is the whole point: it gives the HPA something to react
    to. Both the bcrypt cost factor and the number of sequential rounds are
    configurable so different experiment profiles can be produced without
    rebuilding the image.
    """
    token_bytes = payment_token.encode("utf-8")
    digest = token_bytes
    for _ in range(max(1, PAYMENT_HASH_ROUNDS)):
        salt = bcrypt.gensalt(rounds=PAYMENT_BCRYPT_COST)
        digest = bcrypt.hashpw(digest[:72], salt)  # bcrypt input max 72 bytes
    # Return a short "transaction id" derived from the final hash.
    return digest.decode("utf-8", errors="ignore")[-24:]


def _generate_qr_png_base64(payload: str) -> str:
    """Generate a QR code PNG for the ticket payload and return base64."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _generate_pdf_base64(
    *,
    ticket_id: str,
    concert: dict[str, Any],
    holder_name: str,
    quantity: int,
    qr_png_bytes: bytes,
) -> str:
    """Optional PDF confirmation. Imported lazily so the dependency is only
    loaded when ENABLE_PDF is on (saves startup time when unused)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 22)
    c.drawString(20 * mm, height - 30 * mm, "Concert Ticket")

    c.setFont("Helvetica", 12)
    y = height - 50 * mm
    for label, value in [
        ("Ticket ID", ticket_id),
        ("Artist", concert["artist"]),
        ("Date", concert["date"]),
        ("Venue", concert["venue"]),
        ("Holder", holder_name),
        ("Quantity", str(quantity)),
        ("Total", f"EUR {concert['price_eur'] * quantity:.2f}"),
    ]:
        c.drawString(20 * mm, y, f"{label}: {value}")
        y -= 8 * mm

    qr_reader = ImageReader(io.BytesIO(qr_png_bytes))
    c.drawImage(qr_reader, 130 * mm, height - 90 * mm, width=55 * mm, height=55 * mm)

    c.showPage()
    c.save()
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------------- #
# App lifecycle
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(_jlog(
        "startup",
        pod_id=_metrics["pod_id"],
        payment_bcrypt_cost=PAYMENT_BCRYPT_COST,
        payment_hash_rounds=PAYMENT_HASH_ROUNDS,
        enable_pdf=ENABLE_PDF,
    ))
    yield
    log.info(_jlog("shutdown", pod_id=_metrics["pod_id"]))


app = FastAPI(title="Concert Ticket Booking", version="1.0.0", lifespan=lifespan)

# Resolve templates/static relative to this file so it works in container too.
_HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(_HERE, "static")), name="static")


@app.middleware("http")
async def _count_requests(request: Request, call_next):
    _bump_endpoint(request.url.path)
    return await call_next(request)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/health")
async def health() -> dict[str, Any]:
    """Lightweight liveness/readiness probe.

    Must stay CPU-cheap so Kubernetes probes still succeed when /api/book is
    saturating the worker threads. We deliberately do NOT touch the inventory
    lock or any heavy code path here.
    """
    return {"status": "ok", "pod_id": _metrics["pod_id"]}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    with _inventory_lock:
        concerts_snapshot = [dict(c) for c in CONCERTS]
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "concerts": concerts_snapshot, "pod_id": _metrics["pod_id"]},
    )


@app.get("/api/concerts")
async def list_concerts() -> dict[str, Any]:
    with _inventory_lock:
        return {"concerts": [dict(c) for c in CONCERTS]}


@app.post("/api/book")
def book(payload: BookingRequest):
    """Synchronous on purpose: FastAPI dispatches sync def to a threadpool,
    which keeps the async event loop free for /health and other lightweight
    requests while this one burns CPU."""
    t0 = time.perf_counter()

    concert = _CONCERTS_BY_ID.get(payload.concert_id)
    if concert is None:
        _record_booking((time.perf_counter() - t0) * 1000.0, success=False)
        raise HTTPException(status_code=404, detail="concert_not_found")

    # Reserve seats first (cheap, locked) so we don't burn CPU on a sold-out
    # concert. If the payment step later failed in a real system we would
    # release the seats back; for the demo, payment "always succeeds" once
    # the hashes complete.
    with _inventory_lock:
        if concert["available_seats"] < payload.quantity:
            _record_booking((time.perf_counter() - t0) * 1000.0, success=False)
            raise HTTPException(status_code=409, detail="sold_out_or_insufficient_seats")
        concert["available_seats"] -= payload.quantity

    try:
        tx_id = _simulate_payment_processing(payload.payment_token)
    except Exception as exc:  # rollback on unexpected failure
        with _inventory_lock:
            concert["available_seats"] += payload.quantity
        _record_booking((time.perf_counter() - t0) * 1000.0, success=False)
        log.exception(_jlog("payment_error", error=str(exc)))
        raise HTTPException(status_code=500, detail="payment_processing_failed")

    ticket_id = f"TIX-{uuid.uuid4().hex[:10].upper()}"
    qr_payload = (
        f"TICKET={ticket_id};CONCERT={concert['id']};"
        f"NAME={payload.name};QTY={payload.quantity};TX={tx_id}"
    )
    qr_b64 = _generate_qr_png_base64(qr_payload)

    response: dict[str, Any] = {
        "ticket_id": ticket_id,
        "transaction_id": tx_id,
        "concert": {
            "id": concert["id"],
            "artist": concert["artist"],
            "date": concert["date"],
            "venue": concert["venue"],
            "price_eur": concert["price_eur"],
        },
        "holder_name": payload.name,
        "holder_email": payload.email,
        "quantity": payload.quantity,
        "total_eur": round(concert["price_eur"] * payload.quantity, 2),
        "qr_png_base64": qr_b64,
        "pod_id": _metrics["pod_id"],
        "issued_at": datetime.utcnow().isoformat() + "Z",
    }

    if ENABLE_PDF:
        response["pdf_base64"] = _generate_pdf_base64(
            ticket_id=ticket_id,
            concert=concert,
            holder_name=payload.name,
            quantity=payload.quantity,
            qr_png_bytes=base64.b64decode(qr_b64),
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _record_booking(elapsed_ms, success=True)
    log.info(_jlog(
        "booking_ok",
        ticket_id=ticket_id,
        concert_id=concert["id"],
        quantity=payload.quantity,
        processing_ms=round(elapsed_ms, 2),
        pod_id=_metrics["pod_id"],
    ))
    return response


@app.get("/metrics")
async def metrics() -> JSONResponse:
    with _metrics_lock:
        avg_ms = (
            _metrics["booking_processing_ms_sum"]
            / _metrics["booking_processing_ms_count"]
            if _metrics["booking_processing_ms_count"]
            else 0.0
        )
        snapshot = {
            "pod_id": _metrics["pod_id"],
            "started_at": _metrics["started_at"],
            "requests_by_endpoint": dict(_metrics["requests_by_endpoint"]),
            "bookings_total": _metrics["bookings_total"],
            "bookings_failed": _metrics["bookings_failed"],
            "booking_processing_ms_avg": round(avg_ms, 2),
            "booking_processing_ms_count": _metrics["booking_processing_ms_count"],
            "config": {
                "payment_bcrypt_cost": PAYMENT_BCRYPT_COST,
                "payment_hash_rounds": PAYMENT_HASH_ROUNDS,
                "enable_pdf": ENABLE_PDF,
            },
        }
    return JSONResponse(snapshot)
