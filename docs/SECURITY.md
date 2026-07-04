# Security model

> **Per-box switch:** at-rest encryption is controlled by `ENCRYPTION=vault|plain`
> in `.env`. This document describes `vault` mode. In `plain` mode the
> at-rest row of the table below simply doesn't apply — the library is a
> normal directory (appropriate when the box's disk is already
> full-disk-encrypted, or physical access just isn't in your threat model) —
> while the TLS/in-transit and no-sharing posture remain in full effect.

## What "fully encrypted" means here

There are three places media can be attacked: **on disk**, **on the wire**,
and **on the server while running**. Plextra encrypts the first two:

| Layer | Mechanism | Protects against |
|---|---|---|
| At rest | gocryptfs vault: AES-256-GCM content encryption, EME wide-block filename encryption, scrypt-hardened password | Disk theft, seized/decommissioned drives, other users on the host reading files, cloud backups of the raw vault |
| In transit | TLS 1.3 via Caddy (private CA on LAN, Let's Encrypt in public mode) + HSTS | Network snooping, MITM on hotel/coffee-shop Wi-Fi, ISP inspection |
| Metadata too | Jellyfin's and Audiobookshelf's databases, artwork, and watch history live **inside** the vault | Leaking your library contents/habits even if media itself were elsewhere |

## Why not "true" end-to-end encryption?

True E2EE means the server never holds decryption keys — only clients do.
That is fundamentally incompatible with two things you asked for:

1. **Transcoding.** To convert HEVC→H.264 or tone-map HDR, the GPU must
   decode raw frames. A server that can't read the media can't transcode it.
2. **Server-side libraries.** Metadata scraping, thumbnails, chapter images,
   and search all require the server to read files.

The honest version of "E2EE media server" is a dumb encrypted file store with
direct-play-only clients that each hold keys and support every codec natively —
no GPU use, no metadata, no web player. If you ever want that trade-off, say
so, but it isn't what a Plex-like experience can be built on.

So the design goal here is: **an attacker who gets your disks, your backups,
or your network traffic gets nothing.** An attacker with root on the *running*
host can read media while the vault is unlocked — that's true of every
transcoding media server, including Plex.

## Vault mechanics

- `vault-init.sh` prints a **master key** at creation. Store it offline; it is
  the only recovery path if you forget the password.
- The password is prompted at unlock and never written to disk. Consequence:
  the stack does **not** auto-start on boot — after a reboot, run
  `./scripts/start.sh` once and type the password. That is the price of the
  key never existing at rest.
- `stop.sh` unmounts the plaintext view; only ciphertext remains.
- Back up the **vault directory** (ciphertext) — it's safe for untrusted
  storage. Never back up `$MEDIA_MOUNT`.
- Transcode segments live in a Docker volume outside the vault and are
  short-lived (auto-deleted by Jellyfin's segment deletion, wiped on
  restart). If your threat model requires it, point
  `TranscodingTempPath` at a tmpfs instead.

## Trusting the LAN CA (default mode)

In LAN/VPN mode Caddy generates a private root CA. To make browsers/apps show
the padlock, install the root cert once per device:

```bash
docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt > plextra-root.crt
```

Import `plextra-root.crt` into your OS/browser trust store (iOS: AirDrop then
Settings → General → VPN & Device Management; Android: Settings → Security →
Install certificate). Alternatively, Tailscale users can use `tailscale cert`
hostnames and skip the private CA entirely.

## Mesh VPN exposure (mesh mode)

The only internet-facing surface is the headscale endpoint on `MESH_HOST`:
443/tcp (coordination API + DERP relay), 3478/udp (STUN), 41641/udp
(WireGuard). Joining requires a single-use pre-auth key you generate;
there is no self-service signup. DERP relays only see WireGuard ciphertext.
Mesh keys/state live in `headscale/data/` outside the media vault — they
identify devices but contain no media or library data — so the VPN
recovers on boot while the vault is still locked. Revoke a device any time:
`docker compose exec headscale headscale nodes delete -i <id>`.

## No-sharing posture

- Invite-only accounts: every Jellyfin user is admin-created (household
  members need accounts for per-person request quotas); no self-registration
  exists.
- Quick Connect: off (default) and its login button hidden by the theme.
- Login page: user tiles hidden by the theme; no "forgot password" exposure
  beyond localhost.
- DLNA: leave the plugin uninstalled — DLNA is unauthenticated by design.
- No Jellyfin plugins from untrusted repos; every plugin runs with full
  server privileges.
- Audiobookshelf: single admin user; registration is admin-created only.
- Public mode extra: consider fail2ban on Caddy's JSON access logs and
  geo/IP allowlists in the Caddyfile if your usage pattern allows.

## Update policy

Images are pinned to major tags (`jellyfin:latest`, `caddy:2-alpine`,
`audiobookshelf:latest`). Update deliberately:

```bash
docker compose pull && docker compose up -d
```

Watch Jellyfin release notes for breaking changes to `encoding.xml` fields;
your live settings are in the vault and survive image updates regardless.
