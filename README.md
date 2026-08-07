# Plextra

Sync Trakt lists into Radarr and Sonarr, from a browser, in Docker.

Plextra watches the Trakt lists you care about — your watchlist, a custom list, a
friend's list, trending, whatever — filters them however you like, and adds what
is missing to Radarr or Sonarr on a schedule. Everything is configured through a
web UI on port 9898; there is no config file to hand-edit and no CLI to cron.

![The Lists view, showing three Trakt lists with their schedules and next run times](docs/screenshots/lists.png)

## Why this exists

This started as "can [traktarr](https://github.com/l3uddz/traktarr) run in Docker
with a GUI?" It can't, usefully. traktarr's last commit was June 2022 and it pins
`attrdict==2.0.0`, which crashes on Python 3.10 and newer, plus `click 6.7` and
`requests 2.20`. It is also CLI-only, keeps no state between runs, and ships a
default config with `blacklisted_max_year: 2019`, which silently discards
anything newer until you notice. Wrapping that in a container would mean
freezing Python at 3.9 and bolting a UI onto a program with nothing to show.

So Plextra reimplements the same job on a current stack, and keeps the good part:
traktarr's filtering model is carried over almost field for field, with two
deliberate changes noted under [Filters](#filters).

## Quick start

```bash
git clone https://github.com/austinmabry/plextra.git
cd plextra
docker compose up -d
```

Open <http://localhost:9898> and work through Settings top to bottom.

The compose file pulls `ghcr.io/austinmabry/plextra:latest`. Until you cut your
first release there is no published image yet — comment out the `image:` line
and uncomment `build: .` to build from the checkout in the meantime.

If Radarr and Sonarr already run in their own compose stack, put Plextra on the
same network and address them by container name:

```yaml
services:
  plextra:
    image: ghcr.io/austinmabry/plextra:latest
    container_name: plextra
    restart: unless-stopped
    ports:
      - "9898:9898"
    volumes:
      - ./config:/config
    environment:
      - TZ=America/Chicago
    networks: [media]

networks:
  media:
    external: true
```

Inside a container, `localhost` means *that container*, so use `http://radarr:7878`
or the host's LAN IP — never `http://localhost:7878`.

## Setup

### 1. Trakt

Create an app at <https://trakt.tv/oauth/applications>. Set the redirect URI to
`urn:ietf:wg:oauth:2.0:oob`. Paste the client ID and secret into Settings → Trakt
and press **Save**, then **Connect account**: Plextra shows an 8-character code
to enter at trakt.tv/activate. Tokens are refreshed automatically from then on.

You only need a connected account for your own watchlist, collection and
recommendations, and for private lists. Public lists and trending/popular work
with just the client ID.

### 2. Radarr and Sonarr

Paste the URL and API key (Radarr/Sonarr → Settings → General → API Key), press
**Test & load options**, then pick a quality profile and root folder from the
dropdowns Plextra just fetched. Save.

Sonarr v3 has language profiles and v4 does not. Plextra detects which you are
running and hides the field when it does not apply.

### 3. Add a list

Lists → **Add list**. Pick the media type, choose a Trakt source, set a schedule,
and save. **Pick from my lists** browses the lists you own and have liked so you
do not have to paste URLs.

![The list editor, showing source, selection, schedule and filter options](docs/screenshots/list-editor.png)

Use **Dry run** first. It walks the whole pipeline and records exactly what it
would add, without touching Radarr or Sonarr. History shows the per-title
outcome, including why anything was filtered.

## Sources

| Source | Needs an account | Notes |
| --- | --- | --- |
| Watchlist | yes | |
| Custom list | only if private | Paste a URL or `user/list-slug` |
| Collection | yes | |
| Personal recommendations | yes | |
| Trending / Popular / Anticipated | no | |
| Box office | no | Movies only, 10 items |
| Most watched / played | no | Daily, weekly, monthly, yearly, all time |
| By person | no | Acting credits; self and narrator roles are dropped |

## Filters

Every filter is per-list and every numeric filter is **off at 0**.

| Filter | Effect |
| --- | --- |
| Min / max year | Release year, or first-aired year for shows |
| Min / max runtime | Minutes |
| Min rating, min votes | Trakt's own numbers |
| Allowed countries / languages | Blank = anything. A list = only those. `ignore` = anything, including titles with the field missing |
| Blacklisted genres | Trakt genre slugs, e.g. `anime`, `horror` |
| Blacklisted networks | Shows only, substring match |
| Blacklisted title keywords | Substring match |
| Blacklisted IDs | TMDb for movies, TVDb for shows |

Two intentional differences from traktarr:

- **Defaults are permissive.** traktarr shipped `min_year: 2000` /
  `max_year: 2019` and quietly dropped everything else. Plextra filters nothing
  until you ask it to.
- **Countries and languages match exactly.** traktarr used a substring
  comparison, so `us` also matched `rus`.

`Limit` applies **after** filtering, so "10" means ten titles that passed, not
ten candidates that might not. `Sort` runs before the limit, so limit + sort by
votes gives you the ten most-voted eligible titles.

Plextra never adds something already in Radarr/Sonarr, and it respects each
app's exclusion list, so titles you deliberately removed stay removed.

## Scheduling

Per list: every N hours, a cron expression, or manual only. Interval schedules
resume from the last successful run rather than restarting the clock, so
restarting the container does not push the next sync out a full day. A list that
is still syncing when its next run comes due skips that tick instead of stacking.

## Configuration

Everything lives in `/config`, which you should mount as a volume:

- `config.json` — settings, lists and Trakt tokens, written `0600`
- `plextra.db` — run history, last 200 runs

`config.json` holds API keys and OAuth tokens in the clear, the same as
Radarr's and Sonarr's own config files. Back it up accordingly.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | UTC | Timezone for cron schedules and log timestamps |
| `PLEXTRA_PORT` | `9898` | Listen port |
| `PLEXTRA_LOG_LEVEL` | `INFO` | `DEBUG` logs every per-title filter decision |
| `PLEXTRA_PASSWORD` | unset | Seeds the web password on first boot only |
| `PLEXTRA_COOKIE_SECURE` | `false` | Set `true` when served over HTTPS |
| `PLEXTRA_CONFIG_DIR` | `/config` | Config and database location |
| `PLEXTRA_MAX_TRAKT_PAGES` | `20` | Page cap per sync (100 items per page) |
| `PLEXTRA_ADD_DELAY` | `0.5` | Seconds between adds |

### Security

Plextra holds credentials for three services, so set a password in
Settings → Security unless the port is genuinely private. It warns in the log on
every boot until you do. `/api/health` stays open for Docker's healthcheck;
everything else requires the session cookie.

Set `PLEXTRA_COOKIE_SECURE=true` if you put it behind an HTTPS reverse proxy.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q

PLEXTRA_CONFIG_DIR=./config .venv/bin/python -m plextra
```

The test suite covers the filter rules, config load/upgrade, Trakt's several
response shapes, the sync engine, and the Radarr/Sonarr clients against a stub
*arr server that checks the actual add payloads.

### Releasing

Pushing a tag builds and publishes a multi-arch image (`linux/amd64` and
`linux/arm64`) to GitHub Container Registry:

```bash
git tag v0.2.0
git push origin v0.2.0
```

A tag of `v1.2.3` publishes `1.2.3`, `1.2`, `1` and `latest`. A pre-release tag
like `v1.2.3-rc1` publishes only `1.2.3-rc1` and never moves `latest`. The
running app reports the tag it was built from, because the workflow bakes it in
as a build arg.

Creating a Release in the GitHub UI with a *new* tag works too, since that
pushes the tag. Creating one from a tag that already exists does not trigger
anything — re-run the Release workflow by hand from the Actions tab.

Two one-time things after the first release:

- The GHCR package starts **private**. Make it public at
  `github.com/users/austinmabry/packages/container/plextra/settings` if you want
  to pull it without logging in.
- Link it to this repo on the same page so the package page shows the README.

`CI` runs on every push and pull request: the test suite on Python 3.11 and
3.12, plus a real image build that starts the container and checks the API, the
GUI, the healthcheck, that `/config` is writable, and that it is not running as
root.

## Troubleshooting

**Nothing gets added, but nothing errors.** Check History. Every title has a
recorded outcome and reason — usually `already in library` or a filter.

**"Trakt account is not authorised".** The source needs a connected account.
Settings → Trakt → Connect account, then pick it in the list editor.

**"Radarr could not resolve TMDb ID".** Radarr's own metadata lookup failed for
that title, usually because TMDb and Trakt disagree about the ID. Skip it with a
blacklisted ID.

**Connection refused to Radarr/Sonarr.** `localhost` inside a container is the
container. Use the service name on a shared Docker network, or the host's LAN IP.

**Cron never fires.** Set `TZ`. Cron is evaluated in the container's timezone,
which is UTC unless you say otherwise. An invalid expression is logged at
startup and the list simply is not scheduled.

## License

MIT
