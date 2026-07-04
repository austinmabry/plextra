#!/usr/bin/env bash
# Mount the decrypted view of the vault. Prompts for the vault password.
# -allow_other is required so the container users (PUID/PGID) can read it.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .env

if mountpoint -q "${MEDIA_MOUNT}"; then
    echo "Vault already unlocked at ${MEDIA_MOUNT}."
    exit 0
fi

[ -f "${VAULT_DIR}/gocryptfs.conf" ] || {
    echo "No vault at ${VAULT_DIR}. Run scripts/vault-init.sh first." >&2
    exit 1
}

mkdir -p "${MEDIA_MOUNT}"
gocryptfs -allow_other "${VAULT_DIR}" "${MEDIA_MOUNT}"

# First unlock: lay out the library structure the compose file expects.
for d in media/movies media/tv media/music media/audiobooks media/ebooks \
         appdata/jellyfin/config appdata/jellyfin/cache \
         appdata/audiobookshelf/config appdata/audiobookshelf/metadata; do
    mkdir -p "${MEDIA_MOUNT}/${d}"
done
chown -R "${PUID}:${PGID}" "${MEDIA_MOUNT}/appdata" "${MEDIA_MOUNT}/media" 2>/dev/null || true

# Seed Jellyfin's NVENC + branding config on first run only (never overwrite
# settings you've since changed in the dashboard).
JF_CONF="${MEDIA_MOUNT}/appdata/jellyfin/config/config"
mkdir -p "${JF_CONF}"
[ -f "${JF_CONF}/encoding.xml" ] || cp jellyfin/encoding.xml "${JF_CONF}/encoding.xml"
[ -f "${JF_CONF}/branding.xml" ] || cp jellyfin/branding.xml "${JF_CONF}/branding.xml"
chown -R "${PUID}:${PGID}" "${JF_CONF}" 2>/dev/null || true

echo "Vault unlocked at ${MEDIA_MOUNT}."
