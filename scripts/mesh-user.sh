#!/usr/bin/env bash
# Enroll a person's devices into this server's mesh.
# Usage: ./scripts/mesh-user.sh <person>   (e.g. ./scripts/mesh-user.sh alice)
# Creates the headscale user if needed and prints a fresh single-use key +
# per-platform join instructions.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/lib.sh
load_env

person="${1:?usage: mesh-user.sh <person>}"

hs() { docker compose exec headscale headscale "$@"; }

hs users create "${person}" 2>/dev/null || true
key=$(hs preauthkeys create --user "${person}" --reusable=false --expiration 24h --output json | tr -d '\r' | sed -n 's/.*"key":"\([^"]*\)".*/\1/p')
[ -n "${key}" ] || { echo "Failed to create pre-auth key." >&2; exit 1; }

cat <<EOF

Device key for '${person}' (single use, valid 24h):

    ${key}

Join instructions — install the standard Tailscale app, then:

  Linux:
    tailscale up --login-server https://${MESH_HOST} --auth-key ${key}

  Windows / macOS:
    Install Tailscale, then in the app's settings choose
    "Use alternate coordination server" / run:
      tailscale login --login-server https://${MESH_HOST} --auth-key ${key}

  iOS / Android:
    Tailscale app -> Settings -> Accounts -> triple-tap the version row to
    reveal "Custom coordination server" (iOS) or Settings -> "Use an
    alternate server" (Android) -> enter https://${MESH_HOST}
    -> sign in with the key above.

Once connected, the device reaches the server at the SAME addresses as at
home: https://${JELLYFIN_HOST}, https://${BOOKS_HOST}, https://${REQUESTS_HOST}.
The Tailscale app stays signed in and reconnects automatically — sign in
once, connected forever after.
EOF
