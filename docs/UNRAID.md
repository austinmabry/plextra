# Unraid setup, step by step

Tested workflow for Unraid 6.12+. Differences from a generic Linux host:
Unraid's web GUI owns ports 80/443, appdata belongs on the SSD cache while
media belongs on the array, container files use `nobody:users` (99:100),
the NVIDIA GPU arrives via a plugin with the legacy docker runtime, and
there's no `apt` — so at-rest encryption comes from Unraid itself, not
gocryptfs (details in step 2).

## 1. Plugins (Apps tab → search → Install)

1. **Compose Manager** (dcflachs) — gives Unraid `docker compose` plus a
   Compose tab in the GUI.
2. **Nvidia Driver** (ich777) — installs the NVIDIA kernel driver. After
   install, open Settings → Nvidia Driver and confirm your GPU and a driver
   version are listed. Reboot if the plugin asks.

## 2. Encryption decision (do this before anything else)

`ENCRYPTION=plain` is the right choice on Unraid: gocryptfs isn't packaged
for it. If you want at-rest encryption on this box, use Unraid's **native
LUKS disk encryption** instead — when creating/formatting array or pool
disks choose an `encrypted` filesystem (e.g. "xfs - encrypted"), and Unraid
prompts for the passphrase at array start. That gives the same guarantee
(cold disks are ciphertext) one layer down, and the stack runs in plain
mode on top. Converting existing disks is a reformat — plan it as a
separate project if your data disks aren't encrypted today.

## 3. Free ports 80/443 for Caddy

Settings → Management Access:
- HTTP port: `80` → `8088`
- HTTPS port: `443` → `4438`

Apply; the Unraid GUI moves to `http://<unraid-ip>:8088`. (Alternative if
you refuse to move the GUI: edit the caddy `ports:` in
`docker-compose.yml` to `8080:80` / `8443:443` — but then mesh clients
must use `https://MESH_HOST:8443` everywhere a URL appears.)

## 4. Shares

Shares tab → Add Share:

1. **`plextra`** — the media library. Primary storage: Array (or
   cache:yes if you want mover-buffered writes). This becomes `MEDIA_MOUNT`.
2. Use the existing **`appdata`** share (cache-only, SSD) for app
   databases — Jellyfin's DB on the array would be painfully slow.

Already have media in other shares? Two options: move/hardlink it under
`/mnt/user/plextra/media/...`, or edit the five `MEDIA_MOUNT/media/...`
volume lines in `docker-compose.yml` to point at your existing shares.

## 5. Get the stack

Open the web terminal (`>_` icon, top right):

```bash
cd /mnt/user/appdata
git clone https://github.com/austinmabry/plextra.git plextra-stack
cd plextra-stack
cp unraid/docker-compose.override.yml .    # legacy nvidia runtime + no /dev/dri
cp .env.example .env
nano .env
```

Set in `.env` (skip `scripts/setup.sh` — it assumes a Debian-ish host):

```bash
ENCRYPTION=plain
PUID=99                                  # nobody — Unraid convention
PGID=100                                 # users
TZ=America/New_York
MEDIA_MOUNT=/mnt/user/plextra
APPDATA_MOUNT=/mnt/user/appdata/plextra  # app DBs on the SSD cache
JELLYFIN_HOST=media.yourdomain.com       # real names; private DNS via the mesh
BOOKS_HOST=books.yourdomain.com
REQUESTS_HOST=requests.yourdomain.com
MESH_HOST=mesh.yourdomain.com            # the ONE public endpoint
ACME_EMAIL=you@yourdomain.com
CADDYFILE=./caddy/Caddyfile.mesh
```

(`VAULT_DIR` is ignored in plain mode; leave the rest for later.)

## 6. GPU sanity check

```bash
bash scripts/nvidia-check.sh
```

Expect: GPU listed, "legacy --runtime=nvidia" container path OK, NVENC
encode OK. If the container step fails, re-check the Nvidia Driver plugin
page shows the GPU, then `docker info | grep -i nvidia` should mention the
runtime.

## 7. First launch

```bash
bash scripts/start.sh
```

Plain mode: no password prompt; it creates the library folders, seeds the
NVENC + theme config, renders the headscale config, and starts everything.
Check: `docker compose ps` — all services Up. Then run through
[POST-INSTALL.md](POST-INSTALL.md) (Jellyfin wizard at
`http://<unraid-ip>:8096` until DNS/mesh is up, libraries at
`/media/movies` etc., verify NVENC in Dashboard → Playback).

## 8. Mesh VPN

1. Router: forward to the Unraid box — **443/tcp**, **3478/udp**,
   **41641/udp**.
2. DNS: A record `mesh.yourdomain.com` → your public IP (a DDNS name works).
3. ```bash
   bash scripts/mesh-init.sh          # joins the server to its own mesh
   bash scripts/mesh-user.sh alice    # one per person; prints device key
   ```
Devices install the normal Tailscale app pointed at
`https://mesh.yourdomain.com` ([MESH.md](MESH.md)), and then reach
`https://media.yourdomain.com` from anywhere. For padlocks on Apple
devices run `bash scripts/ios-trust-profile.sh` and AirDrop the profile
([CLIENTS.md](CLIENTS.md)).

## 9. Requests

Radarr/Sonarr already on this Unraid box? Their URLs from inside the stack
are `http://<unraid-ip>:7878` and `http://<unraid-ip>:8989`. Follow
[REQUESTS.md](REQUESTS.md): Jellyseerr first-run at
`https://requests.yourdomain.com` (or `http://<unraid-ip>` via the
container's 5055 while DNS settles), connect Radarr/Sonarr, put the API key
in `.env` (`JELLYSEERR_API_KEY=`), set count quotas per user in Jellyseerr,
MB budgets in `quota-warden/quotas.yml`, then:

```bash
docker compose up -d quota-warden && docker compose logs -f quota-warden
```

## 10. Autostart + updates

- **Autostart**: Compose tab → Add New Stack → name `plextra`, and under
  the stack's settings point it at
  `/mnt/user/appdata/plextra-stack` (Compose Manager's "stack directory").
  Enable autostart. Plain mode needs no password, so the whole stack — mesh
  included — survives reboots unattended.
- **Updates**: from the stack directory,
  `docker compose pull && docker compose up -d --build` (the `--build`
  picks up quota-warden changes). Media, databases, and mesh state all
  persist in the shares.
- **Backups**: appdata via your usual appdata-backup plugin;
  `headscale/data/` (mesh identity) lives inside the stack directory —
  include `/mnt/user/appdata/plextra-stack` in backups too.
