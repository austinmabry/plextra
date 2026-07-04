#!/usr/bin/env bash
# One-time creation of the encrypted media vault (gocryptfs).
# Only needed when ENCRYPTION=vault in .env — plain boxes skip this entirely.
#
# VAULT_DIR   holds only ciphertext — filenames and contents are encrypted
#             (AES-256-GCM, filename encryption with EME). Safe to back up raw.
# MEDIA_MOUNT is the plaintext view, which exists only while unlocked.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/lib.sh
load_env

if ! is_vault; then
    echo "ENCRYPTION=plain in .env — no vault needed. Just run ./scripts/start.sh."
    exit 0
fi

command -v gocryptfs >/dev/null || {
    echo "gocryptfs is not installed. Debian/Ubuntu: sudo apt install gocryptfs" >&2
    exit 1
}

if [ -f "${VAULT_DIR}/gocryptfs.conf" ]; then
    echo "Vault already initialized at ${VAULT_DIR} — nothing to do."
    exit 0
fi

mkdir -p "${VAULT_DIR}" "${MEDIA_MOUNT}"

echo "Initializing encrypted vault at ${VAULT_DIR}."
echo "You will be asked to choose the vault password. It is NEVER stored on disk."
echo
gocryptfs -init "${VAULT_DIR}"

echo
echo "IMPORTANT: gocryptfs printed a master key above. Write it down and store it"
echo "somewhere safe (password manager / paper). It is the ONLY way to recover"
echo "your library if you forget the password."
echo
echo "Next: ./scripts/start.sh"
