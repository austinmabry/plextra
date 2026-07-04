#!/usr/bin/env bash
# Shared helpers, sourced by the other scripts. Not meant to be run directly.

load_env() {
    [ -f .env ] || { echo "No .env found — run scripts/setup.sh first." >&2; exit 1; }
    # shellcheck disable=SC1091
    source .env
}

# ENCRYPTION=vault  -> media lives in a gocryptfs vault, MEDIA_MOUNT is the
#                      decrypted view that exists only while unlocked
# ENCRYPTION=plain  -> MEDIA_MOUNT is just a normal directory on disk
is_vault() {
    [ "${ENCRYPTION:-vault}" = "vault" ]
}

# Create the library layout and seed Jellyfin's NVENC/branding config.
# Idempotent; never overwrites settings you've since changed in the dashboard.
ensure_layout() {
    for d in media/movies media/tv media/music media/audiobooks media/ebooks \
             appdata/jellyfin/config appdata/jellyfin/cache \
             appdata/audiobookshelf/config appdata/audiobookshelf/metadata \
             appdata/jellyseerr; do
        mkdir -p "${MEDIA_MOUNT}/${d}"
    done
    chown -R "${PUID}:${PGID}" "${MEDIA_MOUNT}/appdata" "${MEDIA_MOUNT}/media" 2>/dev/null || true

    local jf_conf="${MEDIA_MOUNT}/appdata/jellyfin/config/config"
    mkdir -p "${jf_conf}"
    [ -f "${jf_conf}/encoding.xml" ] || cp jellyfin/encoding.xml "${jf_conf}/encoding.xml"
    [ -f "${jf_conf}/branding.xml" ] || cp jellyfin/branding.xml "${jf_conf}/branding.xml"
    chown -R "${PUID}:${PGID}" "${jf_conf}" 2>/dev/null || true
}
