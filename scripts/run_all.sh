#!/bin/bash
# Run the full test suite with verbose output and timing
set -e
uv run pytest tests/ -v --tb=short --durations=10
