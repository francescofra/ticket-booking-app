"""
generate_report_plots.py
=========================
Genera tutti i grafici e le tabelle per il report.

Carica i CSV client-side (Locust) e server-side (collect-metrics) di ogni run,
media i 5 run di ogni tipo di esperimento allineandoli sul tempo, e produce grafici (PNG + PDF) e tabelle riassuntive (CSV).
Esperimenti: continuous, bursty, stress.

Uso:
    python generate_report_plots.py

Struttura attesa:
    experiments/data/<tipo>_run<N>/locust_<tipo>_run<N>_stats_history.csv
    experiments/data/<tipo>_run<N>/metrics_<tipo>_run<N>.csv

Output:
    experiments/output/*.png, *.pdf, *.csv
"""
from __future__ import annotations  # compat. Python < 3.10 per i type hint "X | None"

import re
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ----------------------------------------------------------------------------
# Configurazione
# ----------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent.parent          # .../experiments
DATA = BASE / "data"
OUT = BASE / "output"
OUT.mkdir(exist_ok=True)

EXPERIMENTS = ["continuous", "bursty", "stress"]
RUNS = [1, 2, 3, 4, 5]

# Griglia temporale comune per allineare i run (passo 15s = passo del collect server-side)
SERVER_STEP = 15      # secondi
CLIENT_STEP = 5       # secondi (ricampioniamo il client-side per ridurre rumore)

# Stile "colorato ma professionale"
plt.rcParams.update({
    "figure.dpi": 120,
    "savefig.dpi": 150,
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,   # nota: per assi gemelli lo riattiviamo localmente
    "legend.frameon": False,
})

# Palette coerente per metrica
C_PODS = "#2563eb"     # blu
C_CPU = "#dc2626"      # rosso
C_HPA = "#16a34a"      # verde
C_USERS = "#7c3aed"    # viola
C_TPUT = "#ea580c"     # arancione
C_P50 = "#0891b2"      # ciano
C_P95 = "#dc2626"      # rosso
C_P99 = "#9333ea"      # viola scuro
C_ERR = "#b91c1c"      # rosso scuro
C_AVAIL = "#15803d"    # verde scuro

COLOR_BY_EXP = {
    "continuous": "#2563eb",
    "bursty": "#ea580c",
    "stress": "#dc2626",
}


# ----------------------------------------------------------------------------
# Caricamento e parsing
# ----------------------------------------------------------------------------
def parse_hpa_pct(series: pd.Series) -> pd.Series:
    """Estrae la percentuale corrente da 'NN%/50%'. Gestisce <unknown>."""
    def _extract(v):
        if not isinstance(v, str):
            return np.nan
        m = re.match(r"\s*(\d+)\s*%", v)
        return float(m.group(1)) if m else np.nan
    return series.map(_extract)


def load_server(tipo: str, run: int) -> pd.DataFrame | None:
    f = DATA / f"{tipo}_run{run}" / f"metrics_{tipo}_run{run}.csv"
    if not f.exists():
        print(f"  [WARN] manca {f.name}")
        return None
    df = pd.read_csv(f)
    df["cpu_total_m"] = pd.to_numeric(df["cpu_total_m"], errors="coerce").fillna(0)
    df["num_pods"] = pd.to_numeric(df["num_pods"], errors="coerce")
    df["hpa_pct"] = parse_hpa_pct(df["hpa_targets"])
    # cpu media per pod (resource utilization normalizzata)
    df["cpu_per_pod_m"] = df["cpu_total_m"] / df["num_pods"].replace(0, np.nan)
    df = df[["elapsed_s", "num_pods", "cpu_total_m", "cpu_per_pod_m", "hpa_pct"]].copy()
    return df


def load_client(tipo: str, run: int) -> pd.DataFrame | None:
    f = DATA / f"{tipo}_run{run}" / f"locust_{tipo}_run{run}_stats_history.csv"
    if not f.exists():
        print(f"  [WARN] manca {f.name}")
        return None
    df = pd.read_csv(f)
    # Teniamo solo le righe aggregate (una per timestamp)
    df = df[df["Name"] == "Aggregated"].copy()
    df["elapsed_s"] = df["Timestamp"] - df["Timestamp"].min()
    # Percentili: in alcune righe iniziali sono "N/A"
    for col in ["50%", "95%", "99%"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Requests/s"] = pd.to_numeric(df["Requests/s"], errors="coerce").fillna(0)
    df["Failures/s"] = pd.to_numeric(df["Failures/s"], errors="coerce").fillna(0)
    df["User Count"] = pd.to_numeric(df["User Count"], errors="coerce").fillna(0)
    df["err_rate"] = np.where(
        (df["Requests/s"] + df["Failures/s"]) > 0,
        df["Failures/s"] / (df["Requests/s"] + df["Failures/s"]),
        0.0,
    )
    df["availability"] = 1.0 - df["err_rate"]
    keep = ["elapsed_s", "User Count", "Requests/s", "Failures/s",
            "50%", "95%", "99%", "err_rate", "availability"]
    return df[keep].copy()


def resample_to_grid(df: pd.DataFrame, value_cols, step: int, t_max: float) -> pd.DataFrame:
    """Ricampiona un run su una griglia temporale regolare via interpolazione."""
    grid = np.arange(0, t_max + step, step)
    out = {"elapsed_s": grid}
    for c in value_cols:
        # interp ignora i NaN se li rimuoviamo prima
        sub = df[["elapsed_s", c]].dropna()
        if len(sub) >= 2:
            out[c] = np.interp(grid, sub["elapsed_s"], sub[c])
        else:
            out[c] = np.full_like(grid, np.nan, dtype=float)
    return pd.DataFrame(out)


def aggregate_runs(tipo: str, loader, value_cols, step: int):
    """Carica i 3 run, li ricampiona su griglia comune, calcola media e std."""
    runs = []
    for r in RUNS:
        df = loader(tipo, r)
        if df is not None and len(df) > 1:
            runs.append(df)
    if not runs:
        return None
    t_max = min(df["elapsed_s"].max() for df in runs)  # tronchiamo al più corto
    grids = [resample_to_grid(df, value_cols, step, t_max) for df in runs]
    grid_t = grids[0]["elapsed_s"].values
    agg = {"elapsed_s": grid_t}
    for c in value_cols:
        stacked = np.vstack([g[c].values for g in grids])
        agg[f"{c}_mean"] = np.nanmean(stacked, axis=0)
        agg[f"{c}_std"] = np.nanstd(stacked, axis=0)
    return pd.DataFrame(agg)


def band(ax, x, mean, std, color, label, alpha_line=1.0):
    ax.plot(x, mean, color=color, linewidth=2, label=label, alpha=alpha_line)
    ax.fill_between(x, mean - std, mean + std, color=color, alpha=0.18, linewidth=0)


# ----------------------------------------------------------------------------
# Caricamento aggregato di tutti gli esperimenti
# ----------------------------------------------------------------------------
print(">>> Caricamento e aggregazione dei run...")
SERVER_COLS = ["num_pods", "cpu_total_m", "cpu_per_pod_m", "hpa_pct"]
CLIENT_COLS = ["User Count", "Requests/s", "Failures/s", "50%", "95%", "99%",
               "err_rate", "availability"]

server_agg = {}
client_agg = {}
for tipo in EXPERIMENTS:
    print(f"  - {tipo}")
    server_agg[tipo] = aggregate_runs(tipo, load_server, SERVER_COLS, SERVER_STEP)
    client_agg[tipo] = aggregate_runs(tipo, load_client, CLIENT_COLS, CLIENT_STEP)


def save(fig, name):
    png = OUT / f"{name}.png"
    pdf = OUT / f"{name}.pdf"
    fig.savefig(png, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"    salvato: {name}.png / .pdf")


# ----------------------------------------------------------------------------
# GRAFICO 1 — Pod vs Utenti sovrapposti nel tempo (per ogni tipo) [slide 27]
#   "Le risorse fornite seguono le risorse necessarie"
# ----------------------------------------------------------------------------
print(">>> Grafici 1: risorse vs workload nel tempo (per esperimento)")
for tipo in EXPERIMENTS:
    s = server_agg[tipo]
    c = client_agg[tipo]
    if s is None or c is None:
        continue
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.spines.right.set_visible(True)
    # Pod (asse sinistro)
    band(ax, s["elapsed_s"], s["num_pods_mean"], s["num_pods_std"], C_PODS, "Pod attivi (media ±σ)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("# Pod attivi", color=C_PODS)
    ax.tick_params(axis="y", labelcolor=C_PODS)
    ax.set_ylim(bottom=0)
    # Utenti (asse destro)
    ax2 = ax.twinx()
    ax2.spines.right.set_visible(True)
    ax2.plot(c["elapsed_s"], c["User Count_mean"], color=C_USERS,
             linewidth=2, linestyle="--", label="Concurrent users")
    ax2.set_ylabel("# Concurrent users (workload)", color=C_USERS)
    ax2.tick_params(axis="y", labelcolor=C_USERS)
    ax2.set_ylim(bottom=0)
    ax.set_title(f"Scaling delle risorse vs workload — {tipo.capitalize()}")
    # Legenda combinata
    l1, lab1 = ax.get_legend_handles_labels()
    l2, lab2 = ax2.get_legend_handles_labels()
    ax.legend(l1 + l2, lab1 + lab2, loc="upper right")
    save(fig, f"01_resources_vs_workload_{tipo}")


# ----------------------------------------------------------------------------
# GRAFICO 2 — Resource utilization: CPU totale + HPA% (per ogni tipo) [R3, slide 4]
# ----------------------------------------------------------------------------
print(">>> Grafici 2: CPU e HPA target nel tempo (per esperimento)")
for tipo in EXPERIMENTS:
    s = server_agg[tipo]
    if s is None:
        continue
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.spines.right.set_visible(True)
    band(ax, s["elapsed_s"], s["cpu_total_m_mean"], s["cpu_total_m_std"], C_CPU, "Total CPU (m)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Total CPU (millicores)", color=C_CPU)
    ax.tick_params(axis="y", labelcolor=C_CPU)
    ax.set_ylim(bottom=0)
    ax2 = ax.twinx()
    ax2.spines.right.set_visible(True)
    ax2.plot(s["elapsed_s"], s["hpa_pct_mean"], color=C_HPA, linewidth=2,
             linestyle="--", label="HPA utilizzo (%)")
    ax2.axhline(50, color=C_HPA, linewidth=1, linestyle=":", alpha=0.6)
    ax2.set_ylabel("HPA utilizzo CPU (%)", color=C_HPA)
    ax2.tick_params(axis="y", labelcolor=C_HPA)
    ax2.set_ylim(bottom=0)
    ax.set_title(f"Resource utilization e target HPA — {tipo.capitalize()}")
    l1, lab1 = ax.get_legend_handles_labels()
    l2, lab2 = ax2.get_legend_handles_labels()
    ax.legend(l1 + l2, lab1 + lab2, loc="upper right")
    save(fig, f"02_cpu_hpa_{tipo}")


# ----------------------------------------------------------------------------
# GRAFICO 3 — Latenza percentili nel tempo (per ogni tipo) [slide 4, 19]
# ----------------------------------------------------------------------------
print(">>> Grafici 3: latenza percentili nel tempo (per esperimento)")
for tipo in EXPERIMENTS:
    c = client_agg[tipo]
    if c is None:
        continue
    fig, ax = plt.subplots(figsize=(10, 5.5))
    band(ax, c["elapsed_s"], c["50%_mean"], c["50%_std"], C_P50, "p50 (mediana)")
    band(ax, c["elapsed_s"], c["95%_mean"], c["95%_std"], C_P95, "p95")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Response time (ms)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Response time (percentiles) — {tipo.capitalize()}")
    ax.legend(loc="upper right")
    save(fig, f"03_latency_{tipo}")


# ----------------------------------------------------------------------------
# GRAFICO 4 — Throughput nel tempo (per ogni tipo) [slide 4, 18]
# ----------------------------------------------------------------------------
print(">>> Grafici 4: throughput nel tempo (per esperimento)")
for tipo in EXPERIMENTS:
    c = client_agg[tipo]
    if c is None:
        continue
    fig, ax = plt.subplots(figsize=(10, 5.5))
    band(ax, c["elapsed_s"], c["Requests/s_mean"], c["Requests/s_std"], C_TPUT, "Throughput (req/s)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Throughput (req/s)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Served throughput — {tipo.capitalize()}")
    ax.legend(loc="upper right")
    save(fig, f"04_throughput_{tipo}")


# ----------------------------------------------------------------------------
# GRAFICO 5 — Scalability: # Pod vs Throughput (scatter, tutti i tipi) [slide 18]
# ----------------------------------------------------------------------------
print(">>> Grafico 5: scalability (pod vs throughput)")
fig, ax = plt.subplots(figsize=(9, 6))
for tipo in EXPERIMENTS:
    s = server_agg[tipo]
    c = client_agg[tipo]
    if s is None or c is None:
        continue
    # allineiamo throughput (client, step 5) sulla griglia server (step 15)
    tput_on_server = np.interp(s["elapsed_s"], c["elapsed_s"], c["Requests/s_mean"])
    ax.scatter(tput_on_server, s["num_pods_mean"], s=28, alpha=0.55,
               color=COLOR_BY_EXP[tipo], label=tipo.capitalize(), edgecolors="none")
ax.set_xlabel("Served throughput (req/s)")
ax.set_ylabel("# Pod attivi")
ax.set_title("Scalability: pods needed as a function of throughput")
ax.legend(title="Esperimento")
ax.set_ylim(bottom=0)
ax.set_xlim(left=0)
save(fig, "05_scalability_pods_vs_throughput")


# ----------------------------------------------------------------------------
# GRAFICO 6 — Response time vs workload intensity (scatter) [R3]
# ----------------------------------------------------------------------------
print(">>> Grafico 6: latenza vs intensità workload")
fig, ax = plt.subplots(figsize=(9, 6))
for tipo in EXPERIMENTS:
    c = client_agg[tipo]
    if c is None:
        continue
    ax.scatter(c["User Count_mean"], c["95%_mean"], s=28, alpha=0.55,
               color=COLOR_BY_EXP[tipo], label=tipo.capitalize(), edgecolors="none")
ax.set_xlabel("# Concurrent users (workload intensity)")
ax.set_ylabel("Response time p95 (ms)")
ax.set_title("Response time (p95) as a function of workload intensity")
ax.legend(title="Esperimento")
ax.set_ylim(bottom=0)
ax.set_xlim(left=0)
save(fig, "06_latency_vs_workload")


# ----------------------------------------------------------------------------
# GRAFICO 7 — CPU media per pod (resource utilization normalizzata) [R3]
# ----------------------------------------------------------------------------
print(">>> Grafico 7: CPU media per pod")
fig, ax = plt.subplots(figsize=(10, 5.5))
for tipo in EXPERIMENTS:
    s = server_agg[tipo]
    if s is None:
        continue
    ax.plot(s["elapsed_s"], s["cpu_per_pod_m_mean"], linewidth=2,
            color=COLOR_BY_EXP[tipo], label=tipo.capitalize())
ax.axhline(250, color="gray", linestyle=":", linewidth=1.2,
           label="requests.cpu (250m)")
ax.set_xlabel("Time (s)")
ax.set_ylabel("CPU media per pod (millicores)")
ax.set_title("Normalised resource utilization (CPU per pod)")
ax.legend(loc="upper right")
ax.set_ylim(bottom=0)
save(fig, "07_cpu_per_pod")


# ----------------------------------------------------------------------------
# GRAFICO 8 — Availability nel tempo (tutti i tipi) [slide 4, 18]
# ----------------------------------------------------------------------------
print(">>> Grafico 8: availability")
fig, ax = plt.subplots(figsize=(10, 5.5))
for tipo in EXPERIMENTS:
    c = client_agg[tipo]
    if c is None:
        continue
    ax.plot(c["elapsed_s"], c["availability_mean"] * 100, linewidth=2,
            color=COLOR_BY_EXP[tipo], label=tipo.capitalize())
ax.set_xlabel("Time (s)")
ax.set_ylabel("Availability (%)")
ax.set_title("Availability over time (1 − error rate)")
ax.legend(loc="lower right")
ax.set_ylim(90, 100.5)   # zoom sulla parte alta; se hai errori veri abbassa
save(fig, "08_availability")


# ----------------------------------------------------------------------------
# GRAFICO 9 — Pannello 4-in-1 per OGNI esperimento (continuous/bursty/stress)
# ----------------------------------------------------------------------------
print(">>> Grafici 9: pannelli 4-in-1 (uno per esperimento)")

# titoli descrittivi per ciascun esperimento (in inglese, coerenti col report)
PANEL_SUBTITLE = {
    "continuous": "Continuous workload — complete picture (avg. of 5 runs)",
    "bursty":     "Bursty workload — complete picture (avg. of 5 runs)",
    "stress":     "Stress workload — complete picture (avg. of 5 runs)",
}

def make_panel(tipo):
    """Genera il pannello 2x2 (pod+workload, CPU+HPA, throughput, latenza) per un esperimento."""
    s = server_agg[tipo]
    c = client_agg[tipo]
    if s is None or c is None:
        print(f"    [skip] dati mancanti per {tipo}")
        return
    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    fig.suptitle(PANEL_SUBTITLE.get(tipo, f"{tipo.capitalize()} — complete picture (avg. of 5 runs)"),
                 fontsize=15, fontweight="bold")

    # (a) pod vs utenti
    ax = axes[0, 0]; ax.spines.right.set_visible(True)
    band(ax, s["elapsed_s"], s["num_pods_mean"], s["num_pods_std"], C_PODS, "Pod")
    ax.set_ylabel("# Pod", color=C_PODS); ax.tick_params(axis="y", labelcolor=C_PODS)
    ax.set_xlabel("Time (s)"); ax.set_ylim(bottom=0)
    axb = ax.twinx(); axb.spines.right.set_visible(True)
    axb.plot(c["elapsed_s"], c["User Count_mean"], color=C_USERS, linestyle="--", linewidth=2)
    axb.set_ylabel("# Users", color=C_USERS); axb.tick_params(axis="y", labelcolor=C_USERS)
    axb.set_ylim(bottom=0); ax.set_title("(a) Pod vs workload")

    # (b) cpu + hpa
    ax = axes[0, 1]; ax.spines.right.set_visible(True)
    band(ax, s["elapsed_s"], s["cpu_total_m_mean"], s["cpu_total_m_std"], C_CPU, "CPU (m)")
    ax.set_ylabel("Total CPU (m)", color=C_CPU); ax.tick_params(axis="y", labelcolor=C_CPU)
    ax.set_xlabel("Time (s)"); ax.set_ylim(bottom=0)
    axb = ax.twinx(); axb.spines.right.set_visible(True)
    axb.plot(s["elapsed_s"], s["hpa_pct_mean"], color=C_HPA, linestyle="--", linewidth=2)
    axb.axhline(50, color=C_HPA, linestyle=":", alpha=0.6)
    axb.set_ylabel("HPA (%)", color=C_HPA); axb.tick_params(axis="y", labelcolor=C_HPA)
    axb.set_ylim(bottom=0); ax.set_title("(b) Resource utilization + HPA")

    # (c) throughput
    ax = axes[1, 0]
    band(ax, c["elapsed_s"], c["Requests/s_mean"], c["Requests/s_std"], C_TPUT, "Throughput")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("req/s"); ax.set_ylim(bottom=0)
    ax.set_title("(c) Throughput")

    # (d) latenza
    ax = axes[1, 1]
    band(ax, c["elapsed_s"], c["50%_mean"], c["50%_std"], C_P50, "p50")
    band(ax, c["elapsed_s"], c["95%_mean"], c["95%_std"], C_P95, "p95")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("ms"); ax.set_ylim(bottom=0)
    ax.set_title("(d) Response time"); ax.legend(loc="upper right")

    fig.tight_layout()
    save(fig, f"09_panel_{tipo}")

for tipo in EXPERIMENTS:
    make_panel(tipo)


# ----------------------------------------------------------------------------
# TABELLA riassuntiva per esperimento [slide 33]
# ----------------------------------------------------------------------------
print(">>> Tabella riassuntiva metriche")
rows = []
for tipo in EXPERIMENTS:
    s = server_agg[tipo]
    c = client_agg[tipo]
    if s is None or c is None:
        continue
    rows.append({
        "Experiment": tipo.capitalize(),
        "Pod min": int(np.nanmin(s["num_pods_mean"])),
        "Pod max": int(round(np.nanmax(s["num_pods_mean"]))),
        "Max CPU tot (m)": int(np.nanmax(s["cpu_total_m_mean"])),
        "Avg throughput (req/s)": round(np.nanmean(c["Requests/s_mean"]), 2),
        "Max throughput (req/s)": round(np.nanmax(c["Requests/s_mean"]), 2),
        "Avg p50 (ms)": round(np.nanmean(c["50%_mean"]), 1),
        "Avg p95 (ms)": round(np.nanmean(c["95%_mean"]), 1),
        "Max p95 (ms)": round(np.nanmax(c["95%_mean"]), 1),
        "Avg error rate (%)": round(np.nanmean(c["err_rate_mean"]) * 100, 3),
        "Avg availability (%)": round(np.nanmean(c["availability_mean"]) * 100, 3),
    })
table = pd.DataFrame(rows)
table.to_csv(OUT / "summary_table.csv", index=False)
print(table.to_string(index=False))
print(f"\n    salvato: summary_table.csv")

print("\n>>> FATTO. Tutti i file in:", OUT)
