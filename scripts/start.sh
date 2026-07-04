#!/usr/bin/env bash
# Prepare the media root (unlocks the vault when ENCRYPTION=vault,
# no-op prep when ENCRYPTION=plain), then start the stack.
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/vault-unlock.sh
./scripts/render-headscale-config.sh
docker compose up -d
echo
echo "Stack is up."
# shellcheck disable=SC1091
source .env
echo "  Movies/TV/Music : https://${JELLYFIN_HOST}"
echo "  Books/Audiobooks: https://${BOOKS_HOST}"
echo "  Requests        : https://${REQUESTS_HOST}"
echo "  Mesh VPN        : https://${MESH_HOST} (first time? run ./scripts/mesh-init.sh)"
