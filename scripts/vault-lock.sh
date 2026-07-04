#!/usr/bin/env bash
# Unmount the decrypted view. After this, only ciphertext exists on disk.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .env

if ! mountpoint -q "${MEDIA_MOUNT}"; then
    echo "Vault is already locked."
    exit 0
fi

fusermount -u "${MEDIA_MOUNT}" || fusermount3 -u "${MEDIA_MOUNT}"
echo "Vault locked. ${MEDIA_MOUNT} is now empty; ciphertext remains in the vault."
