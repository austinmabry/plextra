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
# APPDATA_MOUNT lets appdata live apart from media (e.g. Unraid: appdata on
# SSD cache, media on the array); unset/empty keeps it inside MEDIA_MOUNT —
# which in vault mode means it stays encrypted.
ensure_layout() {
    APPDATA_MOUNT="${APPDATA_MOUNT:-${MEDIA_MOUNT}/appdata}"
    for d in movies tv music audiobooks ebooks; do
        mkdir -p "${MEDIA_MOUNT}/media/${d}"
    done
    for d in jellyfin/config jellyfin/cache \
             audiobookshelf/config audiobookshelf/metadata jellyseerr; do
        mkdir -p "${APPDATA_MOUNT}/${d}"
    done
    chown -R "${PUID}:${PGID}" "${APPDATA_MOUNT}" "${MEDIA_MOUNT}/media" 2>/dev/null || true

    local jf_conf="${APPDATA_MOUNT}/jellyfin/config/config"
    mkdir -p "${jf_conf}"
    [ -f "${jf_conf}/encoding.xml" ] || cp jellyfin/encoding.xml "${jf_conf}/encoding.xml"
    [ -f "${jf_conf}/branding.xml" ] || cp jellyfin/branding.xml "${jf_conf}/branding.xml"
    chown -R "${PUID}:${PGID}" "${jf_conf}" 2>/dev/null || true
}
