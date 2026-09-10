"""
Locust load test per ticketing-app — campagna sperimentale.
Shape attiva scelta via LOCUST_SHAPE env var (default: nessuna, per dry-run con --users).
"""
import os
from locust import HttpUser, task, between, LoadTestShape
import random

CONCERTS = ["c1", "c2", "c3", "c4", "c5"]


class TicketingUser(HttpUser):
    wait_time = between(1, 3)

    @task(10)
    def book_ticket(self):
        payload = {
            "concert_id": random.choice(CONCERTS),
            "quantity": random.randint(1, 2),
            "name": "Load Test User",
            "email": "loadtest@example.com",
            "payment_token": f"tok_{random.randint(1000, 9999)}"
        }
        with self.client.post("/api/book", json=payload, catch_response=True) as resp:
            if resp.status_code in (200, 409):
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")

    @task(2)
    def view_concerts(self):
        self.client.get("/api/concerts")

    @task(1)
    def health_check(self):
        self.client.get("/health")


# ===== LOAD SHAPES =====
# Solo una shape è "attiva" per run, controllata da env var LOCUST_SHAPE.

ACTIVE_SHAPE = os.environ.get("LOCUST_SHAPE", "").lower()


def make_shape(name, stages_def):
    """Factory che crea una classe shape solo se selezionata via env var."""
    if ACTIVE_SHAPE != name.lower():
        return None

    class _Shape(LoadTestShape):
        stages = stages_def

        def tick(self):
            elapsed = self.get_run_time()
            for stage in self.stages:
                if elapsed < stage["duration"]:
                    return (stage["users"], stage["spawn_rate"])
            return None

    _Shape.__name__ = name
    return _Shape


# E1 — Baseline: 20 utenti costanti per 5 min
BaselineShape = make_shape("baseline", [
    {"duration": 300, "users": 20, "spawn_rate": 2}
])

# E2 — Continuous trapezio (18 min)
ContinuousShape = make_shape("continuous", [
    {"duration": 180,  "users": 5,  "spawn_rate": 1},     # WU
    {"duration": 360,  "users": 40, "spawn_rate": 0.2},   # RU
    {"duration": 720,  "users": 40, "spawn_rate": 1},     # S
    {"duration": 900,  "users": 5,  "spawn_rate": 1},     # RD
    {"duration": 1080, "users": 5,  "spawn_rate": 1},     # cooldown
])

# E3 — Bursty Normal-Burst-Normal-Burst (15 min)
BurstyShape = make_shape("bursty", [
    {"duration": 120, "users": 5,  "spawn_rate": 1},
    {"duration": 300, "users": 40, "spawn_rate": 5},
    {"duration": 420, "users": 5,  "spawn_rate": 5},
    {"duration": 600, "users": 40, "spawn_rate": 5},
    {"duration": 900, "users": 5,  "spawn_rate": 5},
])

# E4 — Stress: 60 utenti per 10 min
StressShape = make_shape("stress", [
    {"duration": 600, "users": 60, "spawn_rate": 2}
])
