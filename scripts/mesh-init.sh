#!/usr/bin/env bash
# One-time mesh bootstrap. Run AFTER the stack is up (./scripts/start.sh):
#   1. creates the server's mesh user in headscale
#   2. issues a pre-auth key and joins the server itself to the mesh
#   3. publishes DNS records so mesh clients resolve JELLYFIN_HOST /
#      BOOKS_HOST / REQUESTS_HOST to the server's mesh IP
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/lib.sh
load_env

hs() { docker compose exec headscale headscale "$@"; }

echo "== Plextra mesh bootstrap =="

# 1. Server user (idempotent)
hs users create plextra-server 2>/dev/null || true

# 2. Join the server node
if docker compose exec tailscale tailscale status >/dev/null 2>&1 &&
   docker compose exec tailscale tailscale ip -4 >/dev/null 2>&1; then
    echo "Server is already joined to the mesh."
else
    echo "Issuing pre-auth key and joining the server node…"
    key=$(hs preauthkeys create --user plextra-server --reusable=false --expiration 1h --output json | tr -d '\r' | sed -n 's/.*"key":"\([^"]*\)".*/\1/p')
    [ -n "${key}" ] || { echo "Failed to create pre-auth key." >&2; exit 1; }
    sed -i "s|^TS_AUTHKEY=.*|TS_AUTHKEY=${key}|" .env
    docker compose up -d --force-recreate tailscale
    echo -n "Waiting for the node to register"
    for _ in $(seq 1 30); do
        sleep 2; echo -n "."
        docker compose exec tailscale tailscale ip -4 >/dev/null 2>&1 && break
    done
    echo
fi

mesh_ip=$(docker compose exec tailscale tailscale ip -4 | tr -d '\r\n ')
[ -n "${mesh_ip}" ] || { echo "Could not determine the server's mesh IP." >&2; exit 1; }
echo "Server mesh IP: ${mesh_ip}"

# 3. DNS: same hostnames on the VPN as on the LAN (headscale hot-reloads this)
cat > headscale/data/extra_records.json <<EOF
[
  {"name": "${JELLYFIN_HOST}", "type": "A", "value": "${mesh_ip}"},
  {"name": "${BOOKS_HOST}", "type": "A", "value": "${mesh_ip}"},
  {"name": "${REQUESTS_HOST}", "type": "A", "value": "${mesh_ip}"}
]
EOF
echo "Published mesh DNS records for ${JELLYFIN_HOST}, ${BOOKS_HOST}, ${REQUESTS_HOST}."

echo
echo "Mesh is live. Add a device with: ./scripts/mesh-user.sh <person>"
