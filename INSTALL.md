# Aurora — complete install guide

Two independent installs: the **theme** (5 minutes, no server access needed
beyond the dashboard) and **home rows** (10 minutes, needs Docker somewhere
that can reach Jellyfin). Nothing here modifies Jellyfin's files; both
connect through official interfaces (the Branding setting and the REST API),
so Jellyfin updates never break the install itself.

---

## Part 1 — The theme

### How the repo connects to Jellyfin

Jellyfin has a server-side **Custom CSS** setting. Whatever is in it is sent
to every web-based client at load time: browsers, Jellyfin Media Player /
desktop, the **LG webOS app**, and the **Samsung Tizen app**. You either
point that setting at this repo via a CDN URL (updates flow automatically)
or paste the file contents (fully offline, frozen until you re-paste).

### Option A — live from the repo (recommended)

1. Sign into Jellyfin **as an admin** in a browser.
2. Click the ☰ menu → **Dashboard** → **General**.
3. Scroll to **Branding** → **Custom CSS** and paste:

   ```css
   @import url("https://cdn.jsdelivr.net/gh/austinmabry/plextra@claude/aurora-theme/theme/aurora.css");
   ```

4. Optional accent variant — add a SECOND line under the first
   (order matters; the variant must come after):

   ```css
   @import url("https://cdn.jsdelivr.net/gh/austinmabry/plextra@claude/aurora-theme/theme/variants/plex-gold.css");
   ```

   Variants: `plex-gold.css` (classic Plex warmth), `glacier.css` (ice
   blue), `emerald.css` (green). No variant = Aurora crimson.
5. Click **Save** at the bottom.

Notes on this route:
- The repo must be **public** for jsDelivr to serve it. Private repo → use
  Option B.
- jsDelivr caches branch URLs for ~12 h, so a repo tweak can take up to half
  a day to appear. To force-refresh, open
  `https://purge.jsdelivr.net/gh/austinmabry/plextra@claude/aurora-theme/theme/aurora.css`
  once in a browser.

### Option B — paste the file (works offline / private repo)

1. Open `theme/aurora.css` in this repo, copy the entire contents.
2. Dashboard → **General** → **Branding** → **Custom CSS** → paste.
3. Variant: open its file (e.g. `theme/variants/plex-gold.css`) and paste
   its contents BELOW the theme.
4. **Save**.

### Seeing it

- **Browser**: hard-refresh (Ctrl+Shift+R / Cmd+Shift+R).
- **Desktop app**: restart it.
- **LG / Samsung TV apps**: fully quit and relaunch the app (webOS: long
  press BACK; Tizen: hold Return/Exit). The CSS arrives from the server on
  launch — nothing to install on the TV.
- It applies to **all users** automatically; individual users can opt out
  with *Settings → Display → Disable server-provided custom CSS*.

### Customizing

Every color/shape/motion decision is a CSS variable in the `:root` block at
the top of `aurora.css`. To tweak, add overrides BELOW your import in the
same Custom CSS box — e.g. a different accent and softer corners:

```css
:root {
    --au-accent: #7c5cff;
    --au-accent-2: #9d85ff;
    --au-radius: 12px;
}
```

### Native apps (read once)

iOS, Android, Roku, Android TV, Apple TV draw native UIs — no CSS ever
reaches them, from any theme. Their Aurora experience comes from Part 2.

---

## Part 2 — Home rows

### How the repo connects to Jellyfin

`homerows/homerows.py` talks to Jellyfin's REST API using an **admin API
key** you generate in the dashboard. On a schedule it evaluates your rules
in `rows.yml` and creates/updates **collections** — which every client
(including iOS and Roku) displays. No plugin, no file edits on the server.

### Step 1 — get the code

On any machine with Docker that can reach your Jellyfin over the network
(the Jellyfin host itself is fine; on Unraid use the Compose Manager
plugin, same as the other branch):

```bash
git clone https://github.com/austinmabry/plextra.git aurora
cd aurora
git checkout claude/aurora-theme
cd homerows
```

### Step 2 — create the API key in Jellyfin

1. Dashboard → **Advanced** → **API Keys** (older versions:
   Dashboard → API Keys).
2. Click **+**, name it `homerows`, save.
3. Copy the long hex key it shows.

### Step 3 — configure

```bash
cp .env.example .env
nano .env
```

```bash
# URL of Jellyfin AS REACHABLE FROM INSIDE A CONTAINER.
# Use the LAN IP — NOT localhost/127.0.0.1 (that would point at the
# container itself). Example:
JELLYFIN_URL=http://192.168.1.50:8096
JELLYFIN_API_KEY=<the key from step 2>
INTERVAL_SECONDS=21600        # re-sync every 6 h
ROW_PREFIX=                   # optional: "· " groups rows together in A-Z lists
```

If Jellyfin is only reachable via HTTPS with a private-CA cert, prefer the
plain LAN URL instead; the sync is API traffic on your own network.

### Step 4 — define your rows

Edit `rows.yml` — it ships with 10 rows ready to go (Recently Added, New
Releases, Top Rated Action/Sci-Fi, Comedy Night, Hidden Gems, 90s Rewind,
Short & Sweet, Binge-Worthy Series, Family Movie Night). The full rule
vocabulary is documented at the top of the file: `types`, `genres`, `sort`
(including `Random`), `min_rating`, `released_within_days`,
`added_within_days`, `decade`/`years`, `max_runtime_minutes`, `tags`,
`parental_ratings`, `unplayed`, `limit`.

Renaming a row later creates a new collection — delete the old one in
Jellyfin (or from the config first, then in Jellyfin).

### Step 5 — first run + verify

```bash
docker compose build
docker compose run --rm -e RUN_ONCE=1 homerows   # one immediate sync, watch it work
```

Expected log lines: `created 'Recently Added' with 30 items` (or
`synced ... (+n/-n)` on later runs). A row that matches nothing logs a
warning and is skipped — usually a genre name that doesn't match your
library's metadata (check a movie's genre spelling in Jellyfin).

Then start the scheduler:

```bash
docker compose up -d
docker compose logs -f     # Ctrl+C to stop watching; it keeps running
```

### Step 6 — where the rows appear (per client)

- **Web / desktop / LG / Samsung**: Movies (or Shows) library → **Collections**
  tab — with the theme active these render as cinematic rows. Mixed-type
  rows (`types: Movie,Series`) appear in both libraries' Collections tabs.
- **iOS / Android**: library → Collections.
- **Roku / Android TV**: library view → Collections row/section.
- **Home screen**: stock Jellyfin home sections are fixed types (Resume,
  Next Up, Latest per library) — it cannot pin an arbitrary collection as
  its own home row; no theme or plugin changes that honestly. Your curated
  rows live one click away under Collections, and the theme's home screen
  still gets the Netflix treatment on its stock rows.
- Tip: mark your favorite rows' collections as **favorites** so they also
  surface under Favorites on most clients.

### Updating

- Rules: edit `rows.yml`, then `docker compose restart homerows` (or just
  wait for the next scheduled sync).
- Engine: `git pull && docker compose up -d --build`.

### Uninstall

`docker compose down`, delete the API key in the dashboard, and delete the
collections it created (Jellyfin → each collection → ⋮ → Delete). The
theme: clear the Custom CSS box.
