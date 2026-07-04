# Requests: search, request, quota

Plextra bakes in [Jellyseerr](https://github.com/Fallenbagel/jellyseerr) at
`https://REQUESTS_HOST`. Users sign in **with their Jellyfin account** — no
separate credentials — and get one search box over everything: results show
what's already on the server (playable immediately) and what isn't, with a
**Request** button. TV shows are requested **per season** — pick exactly the
seasons you want, not all-or-nothing. Approved requests are handed to your
Radarr (movies) / Sonarr (TV) instances, and once media lands in the library
Jellyseerr marks the request Available automatically.

## One-time setup

1. Open `https://REQUESTS_HOST` → choose **Jellyfin** as the server type →
   sign in with your Jellyfin **admin** account → select which libraries to
   sync.
2. Settings → Services → add **Radarr** and **Sonarr** (their URL + API key,
   default quality profile and root folder). These are your existing
   instances — join them to the `plextra` docker network or use a
   host-reachable URL.
3. Settings → General → copy the **API key** into `.env` as
   `JELLYSEERR_API_KEY`, then `docker compose up -d quota-warden`.
4. Settings → Users: set *Enable New Jellyfin Sign-In* on, so household
   members you created in Jellyfin can log in; leave *Enable Local Sign-In*
   off. No public registration exists.

## Admin quota controls (per user)

Two layers, both enforced per rolling window:

**Counts — native Jellyseerr** (Users → edit user → Quotas, or a global
default under Settings → Users):

- **Movies**: N movies per X days (e.g. 5 per 7 days)
- **Series**: N *seasons* per X days (e.g. 2 seasons per 7 days) — a request
  for 3 seasons consumes 3.

**Data volume — quota-warden** (this repo's sidecar, config in
[`quota-warden/quotas.yml`](../quota-warden/quotas.yml)):

- `default_quota_mb` — MB per user per `window_days` (default 50 GB / 7 days)
- `users:` — per-user overrides by Jellyfin username (`0` = unlimited)

quota-warden checks every 5 minutes: it computes each user's real usage in
the window (actual on-disk sizes from Radarr/Sonarr for what their requests
pulled in) and auto-declines pending requests that would bust the budget,
using conservative size estimates for not-yet-downloaded content. Everything
under budget flows through Jellyseerr's normal approval rules (you can set
auto-approve per user there). Edit `quotas.yml` → `docker compose restart
quota-warden`. Watch it work: `docker compose logs -f quota-warden`.

## How the flow looks to a user

1. Search "Dune" in the requests app → *Dune (2021)* shows **Play** (already
   on the server); *Dune: Part Two* shows **Request**.
2. They request it; for a show, a season picker appears.
3. Within quota → request goes to pending (or auto-approves if you allowed
   that for this user) → Radarr/Sonarr grabs it → user gets a notification
   and it appears in Jellyfin. Over quota → automatically declined; the
   request page shows it, and they can re-request once their window frees up.
