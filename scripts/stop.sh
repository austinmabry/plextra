#!/usr/bin/env bash
# Stop the stack, then re-lock the vault (no-op when ENCRYPTION=plain).
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose down
./scripts/vault-lock.sh
