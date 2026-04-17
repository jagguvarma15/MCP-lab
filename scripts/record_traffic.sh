#!/bin/bash
# Record all MCP traffic between a client and server for later analysis
# Usage: ./record_traffic.sh "python my_server.py" traffic.jsonl
set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <server-command> [output-file]"
    echo "Example: $0 \"python my_server.py\" traffic.jsonl"
    exit 1
fi

uv run python -m harness.interceptor --cmd "$1" --log "${2:-traffic.jsonl}"
