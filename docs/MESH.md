# Built-in mesh VPN

Every Plextra instance runs its **own private mesh network** — a self-hosted
[headscale](https://github.com/juanfont/headscale) coordination server with an
embedded DERP relay and STUN, so nothing depends on Tailscale Inc's
infrastructure. Devices sign in once with the standard Tailscale app and are
then permanently, automatically connected to *your* server's mesh: open the
Jellyfin app anywhere and it just works, at the same addresses as at home.

```
 phone (cell network)  laptop (hotel wifi)         home LAN
        │ WireGuard           │ WireGuard              │ plain LAN
        └──────────┬──────────┘                        │
                   ▼                                   ▼
   MESH_HOST:443 (coordination+DERP) ─────►  Caddy ──► Jellyfin / Books / Requests
   MESH_HOST:3478/udp (STUN)                  ▲
   MESH_HOST:41641/udp (direct WireGuard) ────┘  (tailscale sidecar shares
                                                  Caddy's network namespace)
```

## How it fits together

- **headscale** is the control plane: it authenticates devices, distributes
  WireGuard keys, and pushes DNS. Its state lives in `headscale/data/`
  (gitignored) — deliberately *outside* the media vault, so the VPN comes up
  after a reboot even while the vault is still locked.
- **tailscale sidecar** joins the server itself to its own mesh. It shares
  Caddy's network namespace, so the server's mesh IP serves the exact same
  TLS vhosts as the LAN.
- **Mesh DNS**: `mesh-init.sh` publishes A records inside the mesh mapping
  `JELLYFIN_HOST` / `BOOKS_HOST` / `REQUESTS_HOST` to the server's mesh IP.
  Clients use the same URLs everywhere; no split configs.
- **Traffic path**: clients first try a direct WireGuard connection
  (port 41641/udp, discovered via STUN). If NAT blocks it, traffic relays
  through the embedded DERP server on your own 443 — still encrypted
  end-to-end by WireGuard; the relay only sees ciphertext.

## Setup (once)

1. Point `MESH_HOST` (e.g. `mesh.yourdomain.com`) at your public IP and open
   443/tcp, 3478/udp, 41641/udp. This is the only public exposure in mesh
   mode — the media apps themselves are never internet-facing.
2. In `.env`: set `MESH_HOST`, `ACME_EMAIL`, `CADDYFILE=./caddy/Caddyfile.mesh`,
   and real-domain names for the three app hosts (e.g. `media.yourdomain.com`
   — they don't need public DNS; the mesh serves their records).
3. `./scripts/start.sh` then `./scripts/mesh-init.sh`.

## Enrolling devices

```bash
./scripts/mesh-user.sh alice
```

prints a single-use key (24 h validity) and per-platform instructions —
install the normal Tailscale app, point it at `https://MESH_HOST`, paste the
key. From then on the device auto-connects: sign in once, connected forever.
Repeat per person; each person's devices live under their own mesh user, and
`docker compose exec headscale headscale nodes list` shows every enrolled
device (…`nodes delete -i <id>` revokes one instantly).

## Notes

- WireGuard encrypts device-to-server traffic end-to-end; on top of that the
  apps still speak HTTPS. Double-wrapped is fine and keeps LAN and mesh
  behavior identical.
- The one-per-instance property you asked for is structural: each stack has
  its own headscale with its own keys and user database. A device enrolled in
  server A knows nothing about server B (Tailscale apps can hold multiple
  accounts, so one device can join several Plextra servers side by side).
- If you can't open ports at all (CGNAT), everything still works except
  direct paths — traffic uses DERP over 443. If even 443 can't be forwarded,
  fall back to plain Tailscale/another tunnel; the stack doesn't care how
  packets arrive at Caddy.
