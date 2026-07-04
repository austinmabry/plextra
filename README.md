# Plextra

A self-hosted, **encrypted** media server with Plex-level polish — built on
the Jellyfin engine, hardened and skinned, with NVIDIA-accelerated
transcoding, a **built-in mesh VPN** (each instance runs its own private
tailnet), and a **request system** with per-user quotas. Movies, TV, music,
audiobooks, and ebooks. Invite-only accounts, no public sharing, no social
features.

```
 your devices, anywhere ┌──────────────────────── your server ─────────────────────────┐
        │               │ headscale mesh VPN (own coordination + DERP relay + STUN)    │
        │ WireGuard     │      │                                                       │
        ├───────────────┼──────┘   ┌───────┐  TLS 1.3  ┌───────────────┐ NVDEC/NVENC   │
        │ or home LAN   │          │ Caddy ├──────────►│   Jellyfin    ├── NVIDIA GPU  │
        └───────────────┼─────────►│ (TLS) ├──────────►│ Audiobookshelf│               │
                        │          └───────┘──────────►│  Jellyseerr   │──► Radarr/    │
                        │                              └──────┬────────┘    Sonarr     │
                        │                       ┌─────────────▼───────┐  (requests)    │
                        │   quota-warden ──────►│  gocryptfs vault    │ AES-256-GCM    │
                        │   (MB budgets)        │  media + metadata   │ at rest        │
                        │                       └─────────────────────┘ (optional)     │
                        └───────────────────────────────────────────────────────────────┘
```

## What you get

| Function | Served by | Where |
|---|---|---|
| Movies & TV | Jellyfin (custom Plextra skin) | `JELLYFIN_HOST` — Jellyfin apps: TV, iOS/Android, web |
| Music | Jellyfin | Jellyfin apps, Finamp |
| Audiobooks | Audiobookshelf | `BOOKS_HOST` — Audiobookshelf apps, web |
| Ebooks | Audiobookshelf | Web reader (EPUB/PDF/CBZ), send-to-Kindle |
| Search & request media | Jellyseerr (+ quota-warden) | `REQUESTS_HOST` — sign in with Jellyfin account |
| Mesh VPN control plane | headscale (+ tailscale sidecar) | `MESH_HOST` — standard Tailscale apps |

**Mesh VPN, built in** — every instance runs its *own* private mesh
(self-hosted headscale with embedded relay; zero dependence on Tailscale
Inc). Enroll a device once with `./scripts/mesh-user.sh <person>` and it
auto-connects from anywhere — same URLs on cellular as on the couch, with
only the mesh join endpoint ever exposed to the internet. Details:
[docs/MESH.md](docs/MESH.md).

**Requests with real quotas** — users search one box over everything; what's
on the server plays, what's missing gets a Request button (per-season for
TV), fulfilled through your existing Radarr/Sonarr. Admin controls per user:
movies per window and seasons per window (native Jellyseerr) **plus MB per
rolling window** via the bundled `quota-warden` sidecar, which measures the
actual on-disk bytes each user's requests consumed and auto-declines
over-budget asks. Details: [docs/REQUESTS.md](docs/REQUESTS.md).

**Encryption model** — at-rest encryption is a per-box switch
(`ENCRYPTION=vault|plain` in `.env`); everything else is identical in both
modes. With `vault`, media files, all metadata, watch history, and both app
databases live inside a [gocryptfs](https://nuetzlich.net/gocryptfs/) vault
(AES-256-GCM content encryption, EME filename encryption); the decrypted view
exists only while the stack is running, and `./scripts/stop.sh` re-locks it.
With `plain`, the library is a normal directory — right for boxes where disk
encryption isn't wanted or the disk is already FDE. Either way, all client
traffic is TLS 1.3 via Caddy. See [docs/SECURITY.md](docs/SECURITY.md) for the
full threat model, including why "true E2EE" and GPU transcoding are mutually
exclusive and what this design protects instead.

**Playback model** — direct play first, always. Jellyfin only transcodes when
the client genuinely can't play the file, and when it does, decoding (NVDEC)
and encoding (NVENC) both run on your NVIDIA GPU, including HDR→SDR tone
mapping in CUDA. The seeded config is in
[jellyfin/encoding.xml](jellyfin/encoding.xml).

**Invite-only, no public sharing** — Jellyfin has no public registration;
every account is created by you in the dashboard (household members get
accounts so request quotas apply per person). Quick Connect stays off and its
button is hidden by the theme, the login page never lists users, and DLNA and
all remote-share plugins stay uninstalled. Media access from outside the LAN
happens only over your own mesh VPN.

**Clients** — any Jellyfin/Audiobookshelf client works; the per-device
recipes (iOS with mesh + trust profile + requests PWA, the bundled LG webOS
app in `clients/webos/`, Android TV, Apple TV) are in
[docs/CLIENTS.md](docs/CLIENTS.md). Running Unraid? The full walkthrough is
[docs/UNRAID.md](docs/UNRAID.md).

## Requirements

- Linux host with Docker + Compose v2 (Unraid: see [docs/UNRAID.md](docs/UNRAID.md))
- NVIDIA GPU with NVENC (GTX 10-series or newer recommended) + driver +
  [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- `gocryptfs` (`sudo apt install gocryptfs`) — **only** if using at-rest encryption

## Quick start

```bash
./scripts/setup.sh        # checks prerequisites, asks encrypted vs plain, writes .env
./scripts/vault-init.sh   # vault mode only, one time — SAVE THE MASTER KEY
./scripts/start.sh        # launch (unlocks the vault first when in vault mode)
```

On an unencrypted box, `setup.sh` → answer `n` to encryption → `start.sh`.
That's the whole difference; every other command and doc applies unchanged.

Then open `https://media.local` (or your hostname), run the Jellyfin wizard,
and follow **[docs/POST-INSTALL.md](docs/POST-INSTALL.md)** — it covers
accounts, library paths, verifying GPU transcoding, and wiring up requests.
For the built-in VPN: `./scripts/mesh-init.sh` once, then
`./scripts/mesh-user.sh <person>` per person ([docs/MESH.md](docs/MESH.md)).

Copy media into the **unlocked** vault (it's encrypted transparently as it's written):

```
$MEDIA_MOUNT/media/movies/      Movie Name (2024)/Movie Name (2024).mkv
$MEDIA_MOUNT/media/tv/          Show Name/Season 01/Show Name S01E01.mkv
$MEDIA_MOUNT/media/music/       Artist/Album/01 - Track.flac
$MEDIA_MOUNT/media/audiobooks/  Author/Title/Title.m4b
$MEDIA_MOUNT/media/ebooks/      Author/Title.epub
```

Daily driving (identical in both modes — the scripts detect the mode from `.env`):

```bash
./scripts/start.sh   # up (vault mode: asks the vault password first)
./scripts/stop.sh    # down (vault mode: re-locks, disk holds only ciphertext)
```

One behavioral difference: in plain mode the containers auto-start on boot
(`restart: unless-stopped`, no password needed). In vault mode you run
`start.sh` once per reboot — the price of the vault password never touching
disk.

## Remote access

Three supported modes, chosen via `CADDYFILE` in `.env`:

1. **Mesh (recommended)** — `caddy/Caddyfile.mesh`. The built-in headscale
   mesh is the only public exposure (443/tcp, 3478/udp, 41641/udp on
   `MESH_HOST`); the media apps are reachable solely from the LAN and from
   enrolled devices, at identical URLs everywhere. Setup:
   [docs/MESH.md](docs/MESH.md).
2. **LAN only** — `caddy/Caddyfile.lan`. Nothing public at all; private CA
   certs (trust once per device, see docs/SECURITY.md).
3. **Public HTTPS** — `caddy/Caddyfile.public`. Everything on real domains
   with Let's Encrypt, ports 80/443 forwarded. Largest attack surface; the
   mesh endpoint is included so the VPN still works for devices that prefer
   it.

## Design decisions (and how to change them)

- **Stock Jellyfin engine + custom layer, not a source fork.** All polish
  (theme), performance (NVENC), and security (vault, TLS, hardening) live in
  this repo, so `docker compose pull` upgrades Jellyfin without merge pain.
  If you ever want deeper UI changes than CSS allows, the next step is
  building [jellyfin-web](https://github.com/jellyfin/jellyfin-web) from
  source with the theme baked in — the compose file already mounts into the
  web root, so it would slot in cleanly.
- **Audiobookshelf for books.** Jellyfin's book support is its weakest area;
  Audiobookshelf is best-in-class for audiobooks *and* ebooks with progress
  sync and proper mobile apps. It shares the vault, the proxy, and the
  single-user posture. If you'd rather have one app, drop the service from
  `docker-compose.yml` and install Jellyfin's Bookshelf plugin instead.
- **Encrypted at rest + TLS, not "true" E2EE.** Transcoding requires the
  server to decode frames, so a server that never sees plaintext cannot use
  your GPU. Full reasoning in [docs/SECURITY.md](docs/SECURITY.md).
- **headscale + standard Tailscale clients for the mesh.** The control
  plane, relay, and STUN are all self-hosted per instance; clients are the
  battle-tested official apps on every platform. Mesh state lives outside
  the vault so the VPN survives reboots while the vault is locked.
- **Jellyseerr for requests + a custom quota sidecar.** Jellyseerr covers
  Jellyfin-account login, availability-aware search, per-season TV requests,
  Radarr/Sonarr handoff, and count quotas natively; `quota-warden` (in this
  repo) adds the data-volume dimension it lacks.

## Repo layout

```
docker-compose.yml        The stack: Jellyfin, Audiobookshelf, Jellyseerr,
                          quota-warden, headscale, tailscale, Caddy
caddy/                    TLS configs (mesh / LAN internal-CA / public ACME)
clients/webos/            Buildable Plextra app for LG TVs
headscale/                Mesh VPN control-plane config template
quota-warden/             MB-per-window request quota sidecar (+ quotas.yml)
unraid/                   GPU compose override for Unraid
jellyfin/encoding.xml     NVENC + tone-mapping transcode config (seeded once)
jellyfin/branding.xml     Loads the theme (seeded once)
jellyfin/theme/theme.css  The Plextra skin
scripts/                  setup, vault, start/stop, GPU check, mesh + iOS trust
docs/                     SECURITY, POST-INSTALL, MESH, REQUESTS, CLIENTS, UNRAID
```
