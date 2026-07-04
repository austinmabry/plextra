#!/usr/bin/env bash
# Unlock the vault, then start the stack.
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/vault-unlock.sh
docker compose up -d
echo
echo "Stack is up."
# shellcheck disable=SC1091
source .env
echo "  Movies/TV/Music : https://${JELLYFIN_HOST}"
echo "  Books/Audiobooks: https://${BOOKS_HOST}"
