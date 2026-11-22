#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "Starting Clip Desk server on http://127.0.0.1:5000..."

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:5000" &
elif command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:5000" &
elif command -v start >/dev/null 2>&1; then
  start "http://127.0.0.1:5000" &
fi

if command -v python3 >/dev/null 2>&1; then
  python3 server.py
else
  python server.py
fi
