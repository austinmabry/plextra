# Client setup, per device

Plextra keeps the server 100% Jellyfin/Audiobookshelf-protocol compatible on
purpose: every mature client on every platform works, and the pieces we
built (mesh VPN, requests, quotas, theme) plug into them rather than
requiring a fork of each app. Per device, "the Plextra client" is a specific
combination, listed here.

## iOS / iPadOS — full feature set

| Feature | App | Setup |
|---|---|---|
| Mesh VPN (auto-connect) | **Tailscale** (App Store) | One-time: get a key from `./scripts/mesh-user.sh <person>`; in the app, use the custom coordination server `https://MESH_HOST` (see MESH.md for where that setting hides). Stays signed in; VPN on-demand keeps it connected. |
| Certificate trust (mesh/LAN mode) | — | Run `./scripts/ios-trust-profile.sh`, AirDrop the `.mobileconfig`, install, enable in Certificate Trust Settings. After this, all app URLs show padlocks. |
| Movies / TV / Music | **Swiftfin** (App Store; native Jellyfin client, best direct-play/HDR path) or the official **Jellyfin** app | Server: `https://JELLYFIN_HOST` — works at home and away identically thanks to mesh DNS. Sign in with the person's Jellyfin account. |
| Music-focused | **Finamp** | Same server/login. |
| Audiobooks / Ebooks | **Audiobookshelf** app (App Store) | Server: `https://BOOKS_HOST`. Ebooks also read in Safari at the same address. |
| Requests + quotas | **Jellyseerr as a PWA** | In Safari, open `https://REQUESTS_HOST`, sign in with the Jellyfin account, Share → **Add to Home Screen**. Full-screen app: search, per-season requests, quota status. |

Order matters on first setup: Tailscale first, then the trust profile, then
the apps (so their URLs validate immediately).

## LG TV (webOS) — this repo ships a client

`clients/webos/` is a packageable Plextra app for LG TVs: branded connect
screen, remembers the server, launches Jellyfin's TV-optimized UI with the
server-side Plextra theme. Build/install: [clients/webos/README.md](../clients/webos/README.md).
Alternative with zero setup: LG Content Store → official **Jellyfin** app —
same server, same accounts, stock look.

TVs don't join the mesh (webOS can't run a VPN client); they're stationary
LAN devices and connect via `http://server-ip:8096`, the LAN-only HTTP port
the stack publishes precisely for CA-restricted TV platforms. Requests from
the couch: use the phone PWA.

## Other platforms, for completeness

- **Android / Android TV / Fire TV**: official Jellyfin app (phones also run
  Tailscale + the Jellyseerr PWA — full feature set like iOS).
- **Apple TV**: Swiftfin. Like LG TVs, no VPN on-device; LAN only.
- **Desktop**: Jellyfin Media Player, plus Tailscale for the mesh.

## What a from-scratch native app would take (honest scoping)

A single custom "Plextra app" per platform (streaming + books + requests +
embedded VPN in one binary) is possible but is a product-scale effort:

- **iOS**: fork Swiftfin (MPL-2.0, SwiftUI) as the base; embed
  `libtailscale`/the Tailscale iOS SDK in a Network Extension for in-app
  mesh join (requires an Apple Developer account, NE entitlements, App Store
  review or ad-hoc/TestFlight distribution from a Mac with Xcode); add a
  Jellyseerr-API request tab. Weeks of work plus ongoing maintenance of a
  video-player fork — the #1 source of bugs in media apps.
- **webOS**: the ceiling is lower (no VPN APIs at all on TV), which is why
  the shipped wrapper + server-side UI is already ~the practical maximum.

The combination tables above deliver every feature today with maintained,
auto-updating apps; revisit a custom build only if a specific gap emerges.
