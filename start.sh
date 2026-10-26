#!/bin/bash
set -e

echo "=================================================="
echo "  Spanner Omni Pickleball - Bootstrap Script"
echo "=================================================="

# Check if Spanner Omni Docker container is already active
if ! docker ps --format '{{.Names}}' | grep -w "spanner-omni" > /dev/null 2>&1; then
  echo "[+] Spanner Omni container not detected. Initializing container..."
  docker volume create spanner_omni_data > /dev/null 2>&1 || true
  
  docker run -d --name spanner-omni \
    -p 9010:9010 -p 9020:9020 \
    -v spanner_omni_data:/spanner \
    us-docker.pkg.dev/spanner-omni/images/spanner-omni:2026.r2.1-beta \
    start-single-server
  echo "[+] Spanner Omni single-server started on localhost:9010"
else
  echo "[+] Spanner Omni container is already running."
fi

# Launch Flask application
echo "[+] Starting Pickleball Web Application..."
python app.py
