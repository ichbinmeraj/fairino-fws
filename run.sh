#!/usr/bin/env bash
# FWS gateway. Binds to loopback only — reach it via SSH tunnel:
#   ssh -L 8000:localhost:8000 user@<pi-address>
# then open http://localhost:8000/docs for the API.
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn fws.app:app --host 127.0.0.1 --port 8000 "$@"
