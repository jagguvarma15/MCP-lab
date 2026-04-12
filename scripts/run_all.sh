#!/bin/bash
# Run the full test suite with verbose output and timing
set -e
pip install -r requirements.txt --quiet
python -m pytest tests/ -v --tb=short --durations=10
