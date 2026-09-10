# TicketWave - Kubernetes Horizontal Pod Autoscaling on AWS EC2

TicketWave is a stateless concert ticket booking application built to evaluate Kubernetes Horizontal Pod Autoscaling (HPA) on a self-managed cluster running on AWS EC2.

The project combines a FastAPI web application, a multi-stage Docker image, Kubernetes manifests, CPU-based autoscaling, and a reproducible Locust experiment suite. The booking endpoint deliberately performs CPU-intensive payment hashing and QR-code generation, providing a realistic workload that allows the HPA to react to changing demand.

## Project goals

- Bootstrap and operate a self-managed Kubernetes cluster on AWS EC2.
- Deploy a containerized, stateless ticket booking service.
- Scale the application automatically according to average CPU utilization.
- Measure system and client behavior under continuous, bursty, and stress workloads.
- Compare the operational cost of self-managed Kubernetes with Amazon EKS.

## Architecture

The experimental environment uses four EC2 instances:

- 1 `t3.medium` control-plane node.
- 2 `t3.medium` worker nodes.
- 1 `t3.small` load-generator node running Locust.

The Kubernetes cluster was bootstrapped with `kubeadm` and uses containerd, Flannel networking, Metrics Server, and an HPA configured with:

| Setting | Value |
| --- | ---: |
| Minimum replicas | 1 |
| Maximum replicas | 10 |
| Target CPU utilization | 50% |
| CPU request per pod | 250m |
| CPU limit per pod | 500m |
| Memory request per pod | 128Mi |
| Memory limit per pod | 256Mi |
| External NodePort | 30080 |

Scale-up is limited to two pods every 15 seconds, while scale-down removes one pod every 30 seconds after a stabilization window. This favors quick reactions to demand and a gradual return to idle capacity.

## Application features

- Responsive concert listing and booking interface.
- Booking validation with Pydantic.
- CPU-intensive payment-token hashing with bcrypt.
- QR-code ticket generation.
- Optional PDF ticket generation.
- Structured JSON application logs.
- Health endpoint for Docker and Kubernetes probes.
- Lightweight application metrics.

### API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/` | Concert list and booking interface |
| `GET` | `/health` | Liveness and readiness check |
| `GET` | `/api/concerts` | Concert and seat data in JSON format |
| `POST` | `/api/book` | Booking, payment simulation, and ticket generation |
| `GET` | `/metrics` | Request and booking counters |

## Repository structure

```text
.
├── ProjectCC/
│   ├── app/                    # FastAPI application and Docker image
│   │   ├── static/             # JavaScript and CSS
│   │   ├── templates/          # Jinja templates
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── k8s/                    # Deployment, Service, HPA, Metrics Server
│   ├── scripts/                # Cluster setup and experiment scripts
│   └── experiments/
│       ├── data/               # Raw results from repeated runs
│       ├── output/             # Aggregate plots and tables
│       └── scripts/            # Analysis and plotting utilities
└── README.md
```

## Run locally

Requirements: Python 3.11 or newer.

```bash
cd ProjectCC/app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>.

## Run with Docker

From the repository root:

```bash
docker build -t ticketwave:local ProjectCC/app
docker run --rm -p 8000:8000 ticketwave:local
```

The container runs as a non-root user and exposes a health check on `/health`.

### Runtime configuration

| Variable | Default | Description |
| --- | ---: | --- |
| `PORT` | `8000` | Application port |
| `LOG_LEVEL` | `INFO` | Application log level |
| `PAYMENT_BCRYPT_COST` | `12` | bcrypt cost factor |
| `PAYMENT_HASH_ROUNDS` | `4` | Sequential payment-hashing rounds |
| `ENABLE_PDF` | `false` | Enable PDF ticket generation |

For example:

```bash
docker run --rm -p 8000:8000 \
  -e PAYMENT_HASH_ROUNDS=6 \
  -e ENABLE_PDF=true \
  ticketwave:local
```

## Deploy to Kubernetes

The manifests expect a working Kubernetes cluster and an image available to its nodes. The supplied deployment currently references `gioviciarra/ticketing-app:1.0.0`.

```bash
kubectl apply -f ProjectCC/k8s/metrics-server.yaml
kubectl apply -f ProjectCC/k8s/deployment.yaml
kubectl apply -f ProjectCC/k8s/service.yaml
kubectl apply -f ProjectCC/k8s/hpa.yaml
```

Verify the deployment and autoscaler:

```bash
kubectl get pods -l app=ticketing
kubectl get service ticketing-service
kubectl get hpa ticketing-hpa
kubectl top pods -l app=ticketing
```

The application is exposed at:

```text
http://<NODE_PUBLIC_IP>:30080
```

> The included Metrics Server manifest uses `--kubelet-insecure-tls`, which is suitable for the project's self-managed lab cluster with self-signed kubelet certificates. Review this configuration before using it in another environment.

## Load-testing experiments

The Locust suite defines four profiles:

| Shape | Workload | Duration |
| --- | --- | ---: |
| `baseline` | 20 constant users | 5 min |
| `continuous` | 5 to 40 to 5 users | 18 min |
| `bursty` | Two sudden bursts from 5 to 40 users | 15 min |
| `stress` | 60 constant users | 10 min |

The experiment runner expects `locustfile.py` in the load generator's home directory and an activated virtual environment at `~/locust-env`:

```bash
./ProjectCC/scripts/run-experiment.sh \
  continuous continuous_run1 1080 http://<NODE_PUBLIC_IP>:30080
```

Server-side Kubernetes metrics can be collected separately:

```bash
./ProjectCC/scripts/collect-metrics.sh continuous_run1 1080
```

Before another run, restore a clean single-replica state:

```bash
./ProjectCC/scripts/reset-inventory.sh
```

## Experimental results

Each primary workload was repeated five times. The main aggregate results were:

| Workload | Pod range | Max CPU | Throughput | p50 | p95 | Availability |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Continuous | 1-10 | 1516m | 8.54 req/s | 162.9 ms | 911.3 ms | 100.00% |
| Bursty | 1-10 | 2047m | 7.15 req/s | 327.6 ms | 1291.6 ms | 99.99% |
| Stress | 1-10 | 1693m | 14.25 req/s | 1994.0 ms | 6312.9 ms | 99.65% |

The continuous workload showed the complete scale-out and scale-in cycle. During bursty traffic, residual warm capacity improved the response to the second spike. The stress workload reached the ten-pod ceiling, revealing the cluster's capacity limit through sharply increased latency while availability remained above 99.6%.

## Cost analysis

The six-month estimate in the accompanying study was approximately:

- **Self-managed Kubernetes:** USD 585.
- **Amazon EKS:** USD 832.

The self-managed setup was estimated to be about 30% cheaper, at the cost of additional operational responsibility for upgrades, patching, availability, and control-plane maintenance. Prices reflect the assumptions used in the study and should not be treated as current AWS quotations.

## Scope and limitations

This application is an experimental autoscaling workload, not a production ticketing platform. Inventory is kept in memory, so replicas do not share seat state. Authentication, persistent storage, real payment processing, and production-grade secret management are intentionally outside the project's scope.

## Authors

- Giovanni Ciarravano
- Francesco Fratello
- Giuseppe Marchio

Cloud Computing project, Department of Computer Science, Sapienza University of Rome, academic year 2025/2026.
