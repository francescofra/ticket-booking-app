#!/bin/bash
echo ">>> Reset inventario: restart deployment..."
kubectl rollout restart deployment/ticketing-app
kubectl rollout status deployment/ticketing-app

echo ">>> Scale forzato a 1 replica per stato pulito..."
kubectl scale deployment/ticketing-app --replicas=1

echo ">>> Attendo stabilizzazione (max 5 min)..."
for i in $(seq 1 30); do
    PODS=$(kubectl get pods -l app=ticketing --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)
    HPA=$(kubectl get hpa ticketing-hpa --no-headers 2>/dev/null | awk '{print $3}')
    echo "[${i}/30] pods=${PODS} hpa=${HPA}"
    if [ "$PODS" = "1" ]; then
        CPU_PCT=$(echo $HPA | cut -d'%' -f1)
        if [ "$CPU_PCT" -lt 30 ] 2>/dev/null; then
            echo ">>> ✓ Sistema stabilizzato: 1 pod, CPU <30%."
            break
        fi
    fi
    sleep 10
done

echo ">>> Stato finale:"
kubectl get hpa
kubectl get pods -l app=ticketing
echo ">>> ✓ Pronto per il prossimo run."
