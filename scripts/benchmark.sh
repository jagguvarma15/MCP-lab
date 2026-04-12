#!/bin/bash
# Run transport latency benchmarks and output a JSON report
set -e
python -m pytest tests/transport/test_latency.py -v -s --tb=short
python -c "
from harness import TestReporter
import json
r = TestReporter('benchmark')
print(r.to_json())
" > benchmark_results.json
echo "Results written to benchmark_results.json"
