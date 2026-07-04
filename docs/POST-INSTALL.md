# Post-install guide

Ten minutes, once. Everything here assumes the stack is up (`./scripts/start.sh`).

## 1. Jellyfin wizard — `https://media.local` (or your host)

1. Pick your language.
2. **Create your admin account** — strong password (it protects playback
   access, not the vault; the vault has its own password). Household members
   get their own accounts later via Dashboard → Users → Add (needed for
   per-person request quotas). Accounts are always admin-created; there is
   no self-registration.
3. Add libraries, pointing at the container paths:
   - Movies → `/media/movies`
   - Shows → `/media/tv`
   - Music → `/media/music`
   - Enable "Prefer embedded titles" and real-time monitoring; leave
     chapter-image extraction ON (it runs on the GPU and makes the seek bar
     feel like Plex).
4. Metadata language → yours. Remote access page → leave "Allow remote
   connections" checked (Caddy is in front; nothing bypasses it).

## 2. Confirm the seeded settings took

- **Dashboard → Playback → Transcoding**: Hardware acceleration should read
  **NVIDIA NVENC**, tone mapping enabled. (Seeded from `jellyfin/encoding.xml`
  on first unlock.)
- **Dashboard → General → Branding**: Custom CSS should contain
  `@import url("theme.css");` — the UI should already look like Plextra
  (charcoal + gold). If you ever want to tweak the look, edit
  `jellyfin/theme/theme.css` in the repo and hard-refresh; no restart needed.

## 3. Verify GPU transcoding actually happens

1. Play any movie in a browser and force a transcode: player settings (gear
   icon) → Quality → pick something low like 480p/1.5 Mbps.
2. On the host, run `nvidia-smi` — you should see a `ffmpeg` process and
   nonzero encoder utilization.
3. Stop playback, set quality back to Auto. At Auto on your LAN, everything
   direct-plays; the GPU sits idle. That's the desired steady state.

Also run `./scripts/nvidia-check.sh` any time after driver updates.

## 4. Audiobookshelf — `https://books.local`

1. First visit creates the **root** user — same rule: one account, strong
   password.
2. Add libraries: `/audiobooks` (type: Books/audiobooks) and `/ebooks`
   (type: Books). Audiobookshelf serves EPUB/PDF/CBZ in its web reader and
   can send to e-readers.
3. Settings → disable "Allow public registration" style options are already
   off by default; leave them off.
4. Mobile: install the Audiobookshelf app (iOS TestFlight/App Store,
   Android Play/F-Droid) and point it at `https://books.local` (or your
   Tailscale name).

## 5. Requests — `https://requests.local`

Follow [REQUESTS.md](REQUESTS.md): sign in with the Jellyfin admin account,
connect your Radarr/Sonarr, copy the API key into `.env`
(`JELLYSEERR_API_KEY`), set per-user count quotas in the Jellyseerr UI and
MB budgets in `quota-warden/quotas.yml`.

## 6. Mesh VPN

Follow [MESH.md](MESH.md): `./scripts/mesh-init.sh` once, then
`./scripts/mesh-user.sh <person>` for each person's devices.

## 7. Client apps for the big screen

- **TV**: Jellyfin app on Android TV / Fire TV / Apple TV / webOS / Tizen.
  In app settings, enable "hardware decoding" — combined with the server's
  direct-play-first config, most content never transcodes.
- **Phone/tablet**: Jellyfin app (video/music) — under Settings → Playback,
  set maximum bitrate to Auto.
- **Music-focused**: Finamp (uses the same Jellyfin server/login).

## 8. Things to deliberately NOT do

- Don't install the DLNA plugin (unauthenticated by design).
- Don't create accounts for people outside your household — accounts exist
  for per-person quotas, not for public sharing.
- Don't expose the media apps publicly unless you've deliberately switched
  to `Caddyfile.public` and read the public-mode notes in SECURITY.md — in
  mesh mode only the VPN join endpoint faces the internet.
