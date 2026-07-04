# Plextra

A self-hosted, single-owner, **encrypted** media server with Plex-level polish —
built on the Jellyfin engine, hardened and skinned, with NVIDIA-accelerated
transcoding. Movies, TV, music, audiobooks, and ebooks. No sharing, no social
features, no accounts but yours.

```
                        ┌──────────────────────── your server ────────────────────────┐
  Your devices          │  ┌───────┐   TLS 1.3    ┌───────────────┐  NVDEC/NVENC       │
  (TV, phone,  ────────►│  │ Caddy ├─────────────►│   Jellyfin    ├──── NVIDIA GPU     │
  browser, tablet)      │  │ (TLS) ├─────────────►│ Audiobookshelf│                    │
                        │  └───────┘              └──────┬────────┘                    │
                        │                                │ reads                       │
                        │                     ┌──────────▼──────────┐                  │
                        │                     │  gocryptfs vault    │  AES-256-GCM     │
                        │                     │  (media + metadata  │  encrypted       │
                        │                     │   encrypted at rest)│  at rest         │
                        │                     └─────────────────────┘                  │
                        └──────────────────────────────────────────────────────────────┘
```

## What you get

| Media | Served by | Client apps |
|---|---|---|
| Movies & TV | Jellyfin (custom Plextra skin) | Jellyfin apps: TV, iOS/Android, web |
| Music | Jellyfin | Jellyfin apps, Finamp |
| Audiobooks | Audiobookshelf | Audiobookshelf iOS/Android apps, web |
| Ebooks | Audiobookshelf | Web reader (EPUB/PDF/CBZ), send-to-Kindle |

**Encryption model** — media files, all metadata, watch history, and both app
databases live inside a [gocryptfs](https://nuetzlich.net/gocryptfs/) vault
(AES-256-GCM content encryption, EME filename encryption). The decrypted view
exists only while the stack is running; `./scripts/stop.sh` re-locks it. All
client traffic is TLS 1.3 via Caddy. See [docs/SECURITY.md](docs/SECURITY.md)
for the full threat model, including why "true E2EE" and GPU transcoding are
mutually exclusive and what this design protects instead.

**Playback model** — direct play first, always. Jellyfin only transcodes when
the client genuinely can't play the file, and when it does, decoding (NVDEC)
and encoding (NVENC) both run on your NVIDIA GPU, including HDR→SDR tone
mapping in CUDA. The seeded config is in
[jellyfin/encoding.xml](jellyfin/encoding.xml).

**No sharing, by construction** — Jellyfin has no public registration; the
setup guide creates exactly one account (yours), Quick Connect stays off and
its button is hidden by the theme, and the login page never lists users.
DLNA and all remote-share plugins stay uninstalled.

## Requirements

- Linux host with Docker + Compose v2
- NVIDIA GPU with NVENC (GTX 10-series or newer recommended) + driver +
  [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- `gocryptfs` (`sudo apt install gocryptfs`)

## Quick start

```bash
./scripts/setup.sh        # checks prerequisites, writes .env
./scripts/vault-init.sh   # create the encrypted vault (one time — SAVE THE MASTER KEY)
./scripts/start.sh        # unlock vault + launch stack
```

Then open `https://media.local` (or your hostname), run the Jellyfin wizard,
and follow **[docs/POST-INSTALL.md](docs/POST-INSTALL.md)** — it's short and
covers the one-account setup, library paths, and verifying GPU transcoding.

Copy media into the **unlocked** vault (it's encrypted transparently as it's written):

```
$MEDIA_MOUNT/media/movies/      Movie Name (2024)/Movie Name (2024).mkv
$MEDIA_MOUNT/media/tv/          Show Name/Season 01/Show Name S01E01.mkv
$MEDIA_MOUNT/media/music/       Artist/Album/01 - Track.flac
$MEDIA_MOUNT/media/audiobooks/  Author/Title/Title.m4b
$MEDIA_MOUNT/media/ebooks/      Author/Title.epub
```

Daily driving:

```bash
./scripts/start.sh   # unlock + up (asks for the vault password)
./scripts/stop.sh    # down + lock (disk holds only ciphertext)
```

## Remote access

Two supported modes, chosen in `.env`:

1. **LAN + VPN (default, recommended)** — server is never internet-exposed.
   Install [Tailscale](https://tailscale.com) or WireGuard on the server and
   your devices; streaming works anywhere as if you were home. Caddy uses its
   own private CA (`caddy/Caddyfile.lan`) — trust it once per device
   (see docs/SECURITY.md).
2. **Public HTTPS** — set real domains + `ACME_EMAIL` in `.env`, switch
   `CADDYFILE=./caddy/Caddyfile.public`, forward ports 80/443. Let's Encrypt
   certificates are automatic.

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

## Repo layout

```
docker-compose.yml        The stack: Jellyfin, Audiobookshelf, Caddy
caddy/                    TLS configs (LAN internal-CA / public Let's Encrypt)
jellyfin/encoding.xml     NVENC + tone-mapping transcode config (seeded once)
jellyfin/branding.xml     Loads the theme (seeded once)
jellyfin/theme/theme.css  The Plextra skin
scripts/                  setup, vault init/unlock/lock, start/stop, GPU check
docs/                     SECURITY.md (threat model), POST-INSTALL.md (wizard guide)
```
