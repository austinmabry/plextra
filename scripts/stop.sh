#!/usr/bin/env bash
# Stop the stack, then re-lock the vault so nothing readable stays on disk.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose down
./scripts/vault-lock.sh
