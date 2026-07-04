# Plextra for LG TVs (webOS)

A native webOS app package: Plextra-branded connect screen that remembers
your server and launches Jellyfin's TV-optimized web UI (the same UI stack
LG's official Jellyfin app wraps), skinned by the server-side Plextra theme.
Playback, direct play, transcode fallback, and the theme all come from the
server, so the TV app never needs updating when the server does.

## Build & install (one time, ~15 minutes)

1. **Enable Developer Mode on the TV**: install the "Developer Mode" app
   from the LG Content Store, create/sign into an LG developer account
   (free, developer.lge.com), turn Dev Mode ON, and note the TV's IP and
   passphrase. (Dev mode sessions last 50 days — the included dev-mode app
   can extend with one click, or re-enable when it lapses.)
2. **On your computer** (any OS with Node.js):
   ```bash
   npm install -g @webos-tools/cli
   ares-setup-device        # add the TV: name, IP, passphrase from step 1
   cd clients/webos
   ares-package .           # -> com.plextra.tv_1.0.0_all.ipk
   ares-install --device tv com.plextra.tv_1.0.0_all.ipk
   ares-launch  --device tv com.plextra.tv
   ```
3. **First run on the TV**: enter your server address and press Connect.
   - Home network: `http://<server-ip>:8096` (the stack publishes this
     LAN-only HTTP port specifically for TVs, which can't trust private CAs)
   - Public-HTTPS mode servers: `https://media.yourdomain.com`
   The app validates the address, saves it, and auto-connects on every
   launch after that. Press BACK on the connect screen to clear the saved
   server.

## Notes

- TVs stay put, so they don't join the mesh VPN — they talk to the server
  over the LAN like any appliance. (webOS cannot run Tailscale; if a TV
  ever truly needs remote access, put a travel router with Tailscale in
  front of it.)
- Sign in with the household member's own Jellyfin account so watch state
  and request quotas stay per-person.
- To restyle the icons: edit `make-icons.py`, `pip install pillow`,
  `python3 make-icons.py`, re-package.
