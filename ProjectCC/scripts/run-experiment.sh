#!/bin/bash
# Uso: ./run-experiment.sh <shape> <NOME_RUN> <DURATA_SEC> <HOST>
# shape: baseline | continuous | bursty | stress

SHAPE=$1
NAME=$2
DURATION=$3
HOST=$4

if [ -z "$SHAPE" ] || [ -z "$NAME" ] || [ -z "$DURATION" ] || [ -z "$HOST" ]; then
    echo "Uso: $0 <shape> <NOME_RUN> <DURATA_SEC> <HOST>"
    echo "shape: baseline | continuous | bursty | stress"
    exit 1
fi

mkdir -p ~/results/${NAME}
cd ~/results/${NAME}

source ~/locust-env/bin/activate

echo ">>> Esperimento: $NAME (shape=$SHAPE) — $DURATION s"
echo ">>> Host: $HOST"
echo ">>> Inizio: $(date +%Y-%m-%dT%H:%M:%S)"
echo "RUN_START_UNIX=$(date +%s)" > metadata.txt
echo "SHAPE=$SHAPE" >> metadata.txt
echo "DURATION=$DURATION" >> metadata.txt

LOCUST_SHAPE=$SHAPE locust -f ~/locustfile.py \
    --host=$HOST \
    --headless \
    --run-time ${DURATION}s \
    --csv=locust_${NAME} \
    --csv-full-history \
    --html=report_${NAME}.html \
    -L INFO 2>&1 | tee locust_${NAME}.log

echo "RUN_END_UNIX=$(date +%s)" >> metadata.txt
echo ">>> Fine: $(date +%Y-%m-%dT%H:%M:%S)"
ls -la
