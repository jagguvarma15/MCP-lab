#!/bin/bash
# Run only security tests -- useful for CI security gates
set -e
python -m pytest tests/security/ -v --tb=long
