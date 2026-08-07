# Plextra

Sync lists from Trakt, TMDb, MDBList, IMDb, Plex and anywhere else into Radarr
and Sonarr, from a browser, in Docker.

Plextra watches the lists you care about — wherever you keep them — filters them
however you like, and adds what is missing to Radarr or Sonarr on a schedule.
Everything is configured through a web UI on port 9898; there is no config file
to hand-edit and no CLI to cron.

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

### 1. A list provider (at least one)

Set up whichever you actually use. IMDb, StevenLu, another Radarr/Sonarr and
custom URLs need no credentials at all, so you can skip this entirely.

**Trakt** — create an app at <https://trakt.tv/oauth/applications> with the
redirect URI `urn:ietf:wg:oauth:2.0:oob`. Paste the client ID and secret into
Settings → Trakt, press **Save**, then **Connect account**: Plextra shows an
8-character code to enter at trakt.tv/activate, and refreshes tokens from then
on. An account is only needed for your own watchlist, collection and
recommendations, and for private lists.

**TMDb** — a free v3 API key from
<https://www.themoviedb.org/settings/api>, into Settings → Other list providers.

**MDBList** — a free API key from <https://mdblist.com/preferences/>.

**Plex** — in Plex Web, open any item, choose Get Info, then View XML, and copy
the `X-Plex-Token` value out of the address bar.

Each has a **Test** button that saves what is in the box and then checks it.

### 2. Radarr and Sonarr

Paste the URL and API key (Radarr/Sonarr → Settings → General → API Key), press
**Test & load options**, then pick a quality profile and root folder from the
dropdowns Plextra just fetched. Save.

Sonarr v3 has language profiles and v4 does not. Plextra detects which you are
running and hides the field when it does not apply.

### 3. Add a list

Lists → **Add list**. Pick the media type, choose a provider and one of its
lists, set a schedule, and save. The form rebuilds itself for whichever provider
you pick, and only offers combinations that can actually work — Trakt's box
office disappears when the list targets shows, StevenLu disappears entirely.

For Trakt and MDBList, **Pick from my lists** browses the lists you own and have
liked so you do not have to paste URLs.

![The list editor, showing source, selection, schedule and filter options](docs/screenshots/list-editor.png)

Use **Dry run** first. It walks the whole pipeline and records exactly what it
would add, without writing anything to Radarr or Sonarr. It also resolves each
candidate, so a title Radarr has no metadata for is reported as a failure rather
than being promised and then failing on the real run. History shows the
per-title outcome, including why anything was filtered or skipped.

## Providers

Every provider is optional and independent — use the one service you already
keep lists in and ignore the rest. Pick the provider in the list editor and the
form rebuilds itself around it.

| Provider | Needs | Lists it can pull |
| --- | --- | --- |
| **Trakt** | Client ID + secret, and an account for private lists | Watchlist, custom list, collection, personal recommendations, trending, popular, anticipated, box office, most watched/played, by person |
| **TMDb** | A free API key | Custom list, collection, company, keyword, person, popular, top rated, trending, upcoming, now playing, on the air, airing today |
| **MDBList** | A free API key | Any list by URL/slug/ID, your own lists, your watchlist, the public top lists |
| **IMDb** | Nothing | Any public `ls…` list, plus the Top 250, Most Popular, Top English and box office charts |
| **Plex** | A Plex token | Your Plex Discover watchlist |
| **StevenLu** | Nothing | The published popular-movies list (movies only) |
| **Another Radarr / Sonarr** | Its URL + API key | Mirror a second instance's library |
| **Custom list** | Nothing | Any URL returning JSON, RSS/Atom, or a list of IDs |

### Custom lists

The custom provider is deliberately forgiving, because "a URL that returns a
list" has no single format. It understands:

- Sonarr's custom-list JSON — `[{"title": …, "tvdbId": 1, "tmdbId": 2, "imdbId": "tt3"}]`
- Radarr's / StevenLu's — `[{"title": …, "imdb_id": "tt3"}]`
- MDBList's — `{"movies": [...], "shows": [...]}`
- Wrapped arrays under `items`, `results`, `entries` or `data`
- RSS and Atom feeds, taking IMDb IDs out of the link, guid or description
- A bare list of IDs — `[603, 604]`, `["tt0133093"]`, or newline/comma separated

Each entry only needs one of a TMDb, TVDb or IMDb ID; Plextra resolves the rest.

### What is not here

**Simkl, AniList and MyAnimeList** are missing on purpose. Radarr and Sonarr
reach all three through `auth.servarr.com`, the Servarr project's own OAuth proxy
using their registered application credentials. That is not mine to use, and
doing it properly means registering separate OAuth applications with each
service. If you want one of these, open an issue — the provider interface is the
easy part, the OAuth registration is the blocker.

CouchPotato is also absent; the project has been dead for years.

## Mixing IDs across providers

Radarr identifies movies by TMDb ID and Sonarr identifies series by TVDb ID, but
most providers hand out something else — MDBList and IMDb lead with IMDb IDs,
TMDb has no TVDb ID for shows. Plextra resolves the gap in three steps:

1. Use the ID the provider gave, if it is already the right one.
2. Ask the provider — TMDb, for instance, can turn its own show ID into a TVDb one.
3. Ask Radarr or Sonarr, whose own search already knows the cross-mappings.

Step 3 means an IMDb-only list works with no extra API key at all. Resolution
happens lazily, only for titles that survive filtering and are about to be added,
so a 5000-item list does not cost 5000 lookups. Anything that cannot be resolved
is recorded in History as `no TMDb ID found` rather than silently vanishing.

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

A filter can only judge metadata the provider actually sent, and providers differ
enormously. Trakt and TMDb are rich; an IMDb list or a bare custom URL may give
little more than an ID. Filtering an IMDb list by year or genre therefore rejects
everything — the reason recorded in History names the provider
(`no release year from IMDb`) so this is visible rather than mysterious. Use the
limit instead, or pull the same list through MDBList, which does return metadata.

TMDb's list endpoints carry no runtime, so runtime filters have nothing to judge
there either. Fetching it would mean an extra request per title.

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
.venv/bin/pytest

PLEXTRA_CONFIG_DIR=./config .venv/bin/python -m plextra
```

The test suite covers the filter rules, config load/upgrade, Trakt's several
response shapes, every provider's payload conversion, the custom-list parser
against each format it claims to support, the sync engine including ID
resolution, and both the provider and Radarr/Sonarr HTTP clients against stub
servers that assert the actual requests and add payloads.

### Adding a provider

`plextra/providers/` holds one module per source. A provider subclasses
`Provider`, declares its `source_types` (which is what the web UI renders itself
from — there is no front-end change to make), and implements `fetch()` returning
`MediaItem` objects. If it can turn its own IDs into TMDb/TVDb ones, override
`resolve_ids()`; otherwise Radarr and Sonarr's lookup handles it. Register the
class in `providers/__init__.py`.

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

#### After the very first release: make the package pullable

A published image is **private by default**, and a private package is invisible
in every place you would look for it. It does not appear under Packages on the
repository page, and `docker pull` fails as though it was never built:

```
Error response from daemon: denied
```

The workflow cannot change this — package visibility is an account setting, so
it is a one-time manual step:

1. Go to <https://github.com/users/austinmabry/packages/container/plextra/settings>
2. **Danger Zone → Change visibility → Public**
3. While you are there, **Connect repository** so the package page shows this README

To keep it private instead, log in on each machine that pulls it, using a
personal access token with the `read:packages` scope:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u austinmabry --password-stdin
```

To confirm an image really is public, ask the registry anonymously — this prints
the manifest for a public package and `DENIED` for a private one:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:austinmabry/plextra:pull" | jq -r .token)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://ghcr.io/v2/austinmabry/plextra/manifests/latest | head
```

`CI` runs on every push and pull request: the test suite on Python 3.11 and
3.12, plus a real image build that starts the container and checks the API, the
GUI, the healthcheck, that `/config` is writable, and that it is not running as
root.

## Troubleshooting

**Nothing gets added, but nothing errors.** Check History. Every title has a
recorded outcome and reason — usually `already in library` or a filter.

**"Trakt account is not authorised".** The source needs a connected account.
Settings → Trakt → Connect account, then pick it in the list editor.

**"Radarr returned HTTP 500 … metadata service".** Radarr proxies its metadata
through `api.radarr.video`. That service answers a title it does not have with a
clean **404**, so a **500 is not "this title does not exist"** — it means the
metadata service, or your Radarr's network path to it, failed. That is
transient. Do nothing: the next scheduled sync retries it, and it usually
succeeds. In particular, do *not* blacklist the ID — the title is almost
certainly fine.

If it happens to several titles in one burst and then stops, the most likely
cause is rate limiting: each add makes Radarr fetch metadata, and a big first
sync fires a lot of those at once. Set a **limit** on the list to spread the
work over several runs.

**"Radarr does not recognise TMDb …".** *This* is the genuine miss — a clean 404
from the metadata service. Radarr's own Add Movie search will not find it
either, so there is nothing Plextra can do. Usually a TMDb entry that was
deleted or merged after the list was built. Plextra tries an IMDb lookup as a
second route first. Add the ID to the list's blacklisted IDs to stop it being
retried.

**"Path '…' is already configured for an existing movie".** You already have
that film, under a *different* TMDb ID — merged or duplicated TMDb entries cause
this. Plextra now matches the library on IMDb ID as well as TMDb ID and reports
these as `already in library, under a different ID` before attempting an add, so
this should not reach Radarr. If it still does, the existing Radarr entry has no
IMDb ID recorded.

**Connection refused to Radarr/Sonarr.** `localhost` inside a container is the
container. Use the service name on a shared Docker network, or the host's LAN IP.

**`docker compose pull` says denied, or the release "did not build an image".**
Check the Release workflow run first: if the `Build and publish` job is green,
the image exists and the package is simply still private. See
[Releasing](#releasing) for how to make it public. If that job was *skipped*
rather than failed, the `Tests` job before it failed and blocked the publish.

**Cron never fires.** Set `TZ`. Cron is evaluated in the container's timezone,
which is UTC unless you say otherwise. An invalid expression is logged at
startup and the list simply is not scheduled.

## License

MIT
