#!/bin/bash
set -e

# Install dependencies
pip install -e .

# Generate sample data if it doesn't exist
if [ ! -f data/sample_trades.json ]; then
    python scripts/seed.py
fi
