"""
Anteprima grafici di un singolo run: combina CSV server-side e client-side.
Uso: python preview_run.py <nome_run>
"""
import sys
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

if len(sys.argv) < 2:
    print("Uso: python preview_run.py <nome_run>")
    sys.exit(1)

run_name = sys.argv[1]
data_dir = Path(__file__).parent.parent / "data" / run_name

# Carica server-side (master)
server_csv = data_dir / f"metrics_{run_name}.csv"
server = pd.read_csv(server_csv)
server["cpu_total_m"] = pd.to_numeric(server["cpu_total_m"], errors="coerce").fillna(0)
server["hpa_pct"] = server["hpa_targets"].str.extract(r"(\d+)%").astype(float)

# Carica client-side (Locust history)
client_csv = data_dir / f"locust_{run_name}_stats_history.csv"
client = pd.read_csv(client_csv)
client = client[client["Name"] == "Aggregated"].copy()
client["elapsed_s"] = client["Timestamp"] - client["Timestamp"].min()

# Crea 4 grafici in una sola figura
fig, axes = plt.subplots(2, 2, figsize=(14, 9))
fig.suptitle(f"Anteprima run: {run_name}", fontsize=14, fontweight="bold")

# 1. Pod nel tempo
ax = axes[0, 0]
ax.step(server["elapsed_s"], server["num_pods"], where="post", color="tab:blue", linewidth=2)
ax.set_xlabel("Tempo (s)"); ax.set_ylabel("# Pod attivi")
ax.set_title("Numero pod nel tempo")
ax.grid(alpha=0.3)
ax.set_ylim(bottom=0)

# 2. CPU totale e target HPA
ax = axes[0, 1]
ax.plot(server["elapsed_s"], server["cpu_total_m"], color="tab:red", linewidth=2, label="CPU totale (m)")
ax2 = ax.twinx()
ax2.plot(server["elapsed_s"], server["hpa_pct"], color="tab:green", linewidth=2, linestyle="--", label="HPA target (%)")
ax.set_xlabel("Tempo (s)"); ax.set_ylabel("CPU (millicores)", color="tab:red")
ax2.set_ylabel("HPA % current", color="tab:green")
ax.set_title("CPU consumata e target HPA")
ax.grid(alpha=0.3)

# 3. Utenti e throughput client
ax = axes[1, 0]
ax.plot(client["elapsed_s"], client["User Count"], color="tab:purple", linewidth=2, label="Utenti")
ax2 = ax.twinx()
ax2.plot(client["elapsed_s"], client["Requests/s"], color="tab:orange", linewidth=2, alpha=0.7, label="req/s")
ax.set_xlabel("Tempo (s)"); ax.set_ylabel("# Utenti Locust", color="tab:purple")
ax2.set_ylabel("Throughput (req/s)", color="tab:orange")
ax.set_title("Carico generato (client-side)")
ax.grid(alpha=0.3)

# 4. Latenza p50/p95
ax = axes[1, 1]
ax.plot(client["elapsed_s"], client["50%"], color="tab:cyan", linewidth=2, label="p50")
ax.plot(client["elapsed_s"], client["95%"], color="tab:red", linewidth=2, label="p95")
ax.set_xlabel("Tempo (s)"); ax.set_ylabel("Latenza (ms)")
ax.set_title("Response time percentili (client-side)")
ax.legend(); ax.grid(alpha=0.3)

plt.tight_layout()
output = data_dir / f"preview_{run_name}.png"
plt.savefig(output, dpi=120, bbox_inches="tight")
print(f"Grafico salvato: {output}")
plt.show()