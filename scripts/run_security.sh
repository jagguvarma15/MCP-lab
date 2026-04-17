#!/bin/bash
# Run only security tests -- useful for CI security gates
set -e
uv run pytest tests/security/ -v --tb=long
