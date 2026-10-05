#!/usr/bin/env bash
# Rebuild the synthetic dataset and run the full pipeline on it.
# Output lands in sample-output/ (a fresh copy of the seed state each time).
# Run `pip install -e ".[dev]"` once first so `pnl_analyzer` is importable.
set -euo pipefail
cd "$(dirname "$0")/.."

python scripts/generate_sample_data.py
rm -rf sample-output
mkdir -p sample-output
cp -r sample-data/seed/. sample-output/
python -m pnl_analyzer.run \
  --inbox sample-data/inbox \
  --workspace sample-output \
  --month 2026-08 \
  --as-of 2026-09-15T09:00
