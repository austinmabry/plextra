# Post-install guide

Ten minutes, once. Everything here assumes the stack is up (`./scripts/start.sh`).

## 1. Jellyfin wizard — `https://media.local` (or your host)

1. Pick your language.
2. **Create your one account.** This is the only account the server will ever
   have — pick a strong password (it protects playback access, not the vault;
   the vault has its own password).
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

## 5. Client apps for the big screen

- **TV**: Jellyfin app on Android TV / Fire TV / Apple TV / webOS / Tizen.
  In app settings, enable "hardware decoding" — combined with the server's
  direct-play-first config, most content never transcodes.
- **Phone/tablet**: Jellyfin app (video/music) — under Settings → Playback,
  set maximum bitrate to Auto.
- **Music-focused**: Finamp (uses the same Jellyfin server/login).

## 6. Things to deliberately NOT do

- Don't install the DLNA plugin (unauthenticated by design).
- Don't create additional users "just to try it" — the no-sharing posture is
  one account everywhere.
- Don't open ports 80/443 unless you've switched to `Caddyfile.public` and
  read the public-mode notes in SECURITY.md.
