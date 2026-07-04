#!/usr/bin/env bash
# Interactive first-time setup: writes .env, checks prerequisites.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Plextra setup =="
echo

# --- Prerequisite checks -------------------------------------------------
fail=0
command -v docker >/dev/null || { echo "MISSING: docker"; fail=1; }
docker compose version >/dev/null 2>&1 || { echo "MISSING: docker compose v2"; fail=1; }
command -v gocryptfs >/dev/null || { echo "MISSING: gocryptfs (apt install gocryptfs)"; fail=1; }
if ! docker info 2>/dev/null | grep -qi nvidia && ! command -v nvidia-ctk >/dev/null; then
    echo "WARNING: nvidia-container-toolkit not detected — GPU transcoding will not work."
    echo "         Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
fi
[ "$fail" -eq 1 ] && { echo; echo "Install the missing prerequisites and re-run."; exit 1; }

# FUSE allow_other must be enabled for containers to read the vault mount
if ! grep -qE '^\s*user_allow_other' /etc/fuse.conf 2>/dev/null; then
    echo "NOTE: enabling 'user_allow_other' in /etc/fuse.conf (needs sudo)…"
    echo user_allow_other | sudo tee -a /etc/fuse.conf >/dev/null
fi

# --- .env ------------------------------------------------------------------
if [ -f .env ]; then
    echo ".env already exists — leaving it alone."
else
    cp .env.example .env
    uid=$(id -u); gid=$(id -g)
    render_gid=$(getent group render | cut -d: -f3 || true)
    sed -i "s/^PUID=.*/PUID=${uid}/" .env
    sed -i "s/^PGID=.*/PGID=${gid}/" .env
    [ -n "${render_gid}" ] && sed -i "s/^RENDER_GID=.*/RENDER_GID=${render_gid}/" .env
    tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
    sed -i "s|^TZ=.*|TZ=${tz}|" .env
    echo "Wrote .env (PUID=${uid}, PGID=${gid}, TZ=${tz})."
    echo "Edit .env now if you want different vault paths or hostnames."
fi

echo
echo "GPU check:"
./scripts/nvidia-check.sh || true

echo
echo "Next steps:"
echo "  1. Review .env"
echo "  2. ./scripts/vault-init.sh   (create the encrypted vault — one time)"
echo "  3. ./scripts/start.sh        (unlock + launch)"
echo "  4. Open https://\$JELLYFIN_HOST and run the Jellyfin wizard"
echo "     -> docs/POST-INSTALL.md walks through the polished-UI and no-sharing settings."
