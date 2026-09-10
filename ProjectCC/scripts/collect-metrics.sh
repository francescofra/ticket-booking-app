#!/bin/bash
# Uso: ./collect-metrics.sh <nome_esperimento> <durata_secondi>
EXP_NAME=$1
DURATION=${2:-900}
INTERVAL=15
OUTPUT="metrics_${EXP_NAME}.csv"

echo "timestamp,elapsed_s,num_pods,hpa_targets,cpu_total_m" > $OUTPUT

echo ">>> Raccolta metriche per '$EXP_NAME' per ${DURATION}s ogni ${INTERVAL}s"
echo ">>> Output: $OUTPUT"

START=$SECONDS
while [ $((SECONDS - START)) -lt $DURATION ]; do
    TS=$(date +%s)
    ELAPSED=$((SECONDS - START))
    NUM_PODS=$(kubectl get pods -l app=ticketing --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)
    HPA_T=$(kubectl get hpa ticketing-hpa --no-headers 2>/dev/null | awk '{print $3}')
    CPU_TOTAL=$(kubectl top pods -l app=ticketing --no-headers 2>/dev/null | awk '{gsub(/m/,"",$2); sum+=$2} END {print sum}')
    echo "${TS},${ELAPSED},${NUM_PODS},${HPA_T},${CPU_TOTAL}" >> $OUTPUT
    echo "[t=${ELAPSED}s] pods=${NUM_PODS} hpa=${HPA_T} cpu=${CPU_TOTAL}m"
    sleep $INTERVAL
done

echo ">>> Raccolta completata: $OUTPUT"
