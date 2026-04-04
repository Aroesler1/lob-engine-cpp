#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/build"
DATA_DIR="$ROOT_DIR/data"
RESULT_DIR="$ROOT_DIR/benchmark/results"
AAPL_ZIP_URL="https://data.lobsterdata.com/info/sample/LOBSTER_SampleFile_AAPL_2012-06-21_1.zip"
BENCHMARK_RESULTS="$RESULT_DIR/benchmark_results.csv"
CORRECTNESS_RESULTS="$RESULT_DIR/correctness_results.csv"
REPORT_PATH="$ROOT_DIR/benchmark/report.md"

mkdir -p "$BUILD_DIR" "$RESULT_DIR"

echo "[bench] configuring and building"
(
    cd "$BUILD_DIR"
    cmake ..
    make -j"$(nproc)"
)

AAPL_FILE="$(find "$DATA_DIR" -maxdepth 1 -type f -name 'AAPL*_message*.csv' | head -n 1 || true)"
if [[ -z "$AAPL_FILE" ]]; then
    echo "[bench] downloading official AAPL LOBSTER sample"
    python3 - "$DATA_DIR" "$AAPL_ZIP_URL" <<'PY'
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

data_dir = Path(sys.argv[1])
url = sys.argv[2]
payload = urllib.request.urlopen(url, timeout=60).read()
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    for name in archive.namelist():
        if name.endswith("_message_1.csv"):
            archive.extract(name, data_dir)
            print(data_dir / name)
            break
    else:
        raise SystemExit("AAPL sample zip did not contain a message file")
PY
    AAPL_FILE="$(find "$DATA_DIR" -maxdepth 2 -type f -name 'AAPL*_message*.csv' | head -n 1 || true)"
fi

if [[ ! -f "$DATA_DIR/MSFT_synthetic_message.csv" || ! -f "$DATA_DIR/TSLA_synthetic_message.csv" || ! -f "$DATA_DIR/SPY_synthetic_message.csv" ]]; then
    echo "[bench] generating synthetic benchmark data"
    python3 "$ROOT_DIR/scripts/generate_synthetic_data.py"
fi

rm -f "$BENCHMARK_RESULTS" "$CORRECTNESS_RESULTS"

declare -a DATASETS=(
    "AAPL:$AAPL_FILE"
    "MSFT_synthetic:$DATA_DIR/MSFT_synthetic_message.csv"
    "TSLA_synthetic:$DATA_DIR/TSLA_synthetic_message.csv"
    "SPY_synthetic:$DATA_DIR/SPY_synthetic_message.csv"
)

declare -a CONTAINERS=("map" "flat_vec")

for dataset in "${DATASETS[@]}"; do
    ticker="${dataset%%:*}"
    file="${dataset#*:}"
    if [[ ! -f "$file" ]]; then
        echo "[bench] missing input file for $ticker: $file" >&2
        exit 1
    fi

    echo "[bench] verifying correctness for $ticker"
    "$BUILD_DIR/verify_correctness" \
        --data "$file" \
        --ticker "$ticker" \
        --summary-output "$CORRECTNESS_RESULTS"

    for container in "${CONTAINERS[@]}"; do
        echo "[bench] running $ticker with $container"
        "$BUILD_DIR/run_benchmarks" \
            --data "$file" \
            --ticker "$ticker" \
            --container "$container" \
            --iterations 5 \
            --warmup 1 \
            --output "$BENCHMARK_RESULTS"
    done
done

echo "[bench] generating markdown report"
python3 "$ROOT_DIR/benchmark/generate_report.py" \
    --results "$BENCHMARK_RESULTS" \
    --correctness "$CORRECTNESS_RESULTS" \
    --output "$REPORT_PATH"

echo "[bench] results written to $BENCHMARK_RESULTS"
echo "[bench] correctness written to $CORRECTNESS_RESULTS"
echo "[bench] report written to $REPORT_PATH"
