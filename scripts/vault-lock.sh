#!/usr/bin/env bash
# Unmount the decrypted view (vault mode). After this, only ciphertext exists
# on disk. In plain mode there is nothing to lock.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/lib.sh
load_env

if ! is_vault; then
    echo "Encryption disabled (ENCRYPTION=plain) — nothing to lock."
    exit 0
fi

if ! mountpoint -q "${MEDIA_MOUNT}"; then
    echo "Vault is already locked."
    exit 0
fi

fusermount -u "${MEDIA_MOUNT}" || fusermount3 -u "${MEDIA_MOUNT}"
echo "Vault locked. ${MEDIA_MOUNT} is now empty; ciphertext remains in the vault."
