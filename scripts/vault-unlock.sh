#!/usr/bin/env bash
# Prepare the media root for the stack.
#   ENCRYPTION=vault -> mount the decrypted gocryptfs view (prompts for password)
#   ENCRYPTION=plain -> nothing to unlock; just ensure the directory layout
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/lib.sh
load_env

if ! is_vault; then
    mkdir -p "${MEDIA_MOUNT}"
    ensure_layout
    echo "Encryption disabled (ENCRYPTION=plain) — media root ready at ${MEDIA_MOUNT}."
    exit 0
fi

if mountpoint -q "${MEDIA_MOUNT}"; then
    echo "Vault already unlocked at ${MEDIA_MOUNT}."
    exit 0
fi

[ -f "${VAULT_DIR}/gocryptfs.conf" ] || {
    echo "No vault at ${VAULT_DIR}. Run scripts/vault-init.sh first," >&2
    echo "or set ENCRYPTION=plain in .env to run without at-rest encryption." >&2
    exit 1
}

mkdir -p "${MEDIA_MOUNT}"
# -allow_other is required so the container users (PUID/PGID) can read it.
gocryptfs -allow_other "${VAULT_DIR}" "${MEDIA_MOUNT}"
ensure_layout
echo "Vault unlocked at ${MEDIA_MOUNT}."
