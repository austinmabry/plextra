<h1 align="center">Sidecarr</h1>

<p align="center">
  Sync lists from Trakt, TMDb, MDBList, Plex and anywhere else
  into Radarr and Sonarr — from a browser, in Docker.
</p>

<p align="center">
  <a href="https://github.com/austinmabry/sidecarr/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/austinmabry/sidecarr/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/austinmabry/sidecarr/releases"><img alt="Release" src="https://img.shields.io/github/v/release/austinmabry/sidecarr?sort=semver"></a>
  <a href="https://github.com/austinmabry/sidecarr/pkgs/container/sidecarr"><img alt="Image" src="https://img.shields.io/badge/ghcr.io-sidecarr-blue?logo=docker&logoColor=white"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
</p>

![The Lists view, showing several lists with their schedules and next run times](docs/screenshots/lists.png)

Point Sidecarr at the lists you already keep — wherever you keep them — and it
adds what is missing to Radarr or Sonarr on a schedule. It never removes,
unmonitors or modifies anything you already have.

- **Eight list providers.** Trakt, TMDb, MDBList, Plex, StevenLu, another
  Radarr/Sonarr instance, a pasted list or CSV file, or any URL that returns a
  list. Every one uses a published interface — nothing here scrapes.
- **Configured entirely in the browser.** No config file to hand-edit, no CLI to
  cron. Port 9898.
- **Per-list filters and schedules.** Year, runtime, rating, votes, genres,
  countries, languages, networks, title keywords. Every list on its own interval
  or cron.
- **Dry run.** Walks the whole pipeline and reports exactly what it would do,
  without writing anything.
- **Honest history.** Every title gets a recorded outcome and a reason — added,
  already present, filtered, excluded or failed.
- **Add-only by design.** No delete, no unmonitor, no library cleaning.
- **Bulk import.** Titles go over in batches, not one request each, so a big
  first sync does not hammer Radarr's metadata service.
- **Credentials encrypted at rest**, with CSRF protection and a strict
  Content-Security-Policy on the web UI.
- **Light and dark themes**, following your system by default.
- **Multi-arch.** `linux/amd64` and `linux/arm64`, so it runs on a NAS or a Pi.

## Quick start

```yaml
services:
  sidecarr:
    image: ghcr.io/austinmabry/sidecarr:latest
    container_name: sidecarr
    restart: unless-stopped
    ports:
      - "9898:9898"
    volumes:
      - ./config:/config
    environment:
      - TZ=Etc/UTC
```

```bash
docker compose up -d
```

Open <http://localhost:9898> and work through Settings top to bottom.

<details>
<summary><strong>Unraid</strong></summary>

Unraid's appdata is owned by `nobody:users`, and the image defaults to uid 1000,
so set the user explicitly:

```bash
mkdir -p /mnt/user/appdata/sidecarr
chown -R 99:100 /mnt/user/appdata/sidecarr

docker run -d \
  --name sidecarr \
  --restart unless-stopped \
  --user 99:100 \
  -p 9898:9898 \
  -v /mnt/user/appdata/sidecarr:/config \
  -e TZ=America/Chicago \
  ghcr.io/austinmabry/sidecarr:latest
```

With the Compose Manager plugin, put the same settings in a
`docker-compose.yml` under
`/boot/config/plugins/compose.manager/projects/sidecarr/`, adding
`user: "99:100"`.

Address Radarr and Sonarr by the server's IP (`http://<UNRAID-IP>:7878`).
Unraid's default bridge network has no container-name DNS, and `localhost`
inside the container is the container itself.
</details>

<details>
<summary><strong>Alongside an existing media stack</strong></summary>

Put Sidecarr on the same network and address the other containers by name:

```yaml
services:
  sidecarr:
    image: ghcr.io/austinmabry/sidecarr:latest
    container_name: sidecarr
    restart: unless-stopped
    ports:
      - "9898:9898"
    volumes:
      - ./config:/config
    environment:
      - TZ=Etc/UTC
    networks: [media]

networks:
  media:
    external: true
```

Then use `http://radarr:7878` and `http://sonarr:8989`.
</details>

> **Inside a container, `localhost` means that container.** Use the service name
> on a shared Docker network, or the host's LAN IP — never
> `http://localhost:7878`.

## Providers

Every provider is optional and independent. Use the one service you already keep
lists in and ignore the rest.

| Provider | Needs | Lists it can pull |
| --- | --- | --- |
| **Trakt** | Client ID + secret; an account for private lists | Watchlist, custom list, collection, personal recommendations, trending, popular, anticipated, box office, most watched/played, by person |
| **TMDb** | A free API key | Discover (incl. streaming services), custom list, collection, company, keyword, person, popular, top rated, trending, upcoming, now playing, on the air, airing today |
| **MDBList** | A free API key | Any list by URL/slug/ID, your own lists, your watchlist, the public top lists |
| **Plex** | A Plex token | Your Plex Discover watchlist |
| **StevenLu** | Nothing | The published popular-movies list (movies only) |
| **Another Radarr / Sonarr** | Its URL + API key | Mirror a second instance's library |
| **Paste or file** | Nothing | A list pasted into the box, or a CSV/JSON file in `/config` |
| **Custom list** | Nothing | Any URL returning JSON, RSS/Atom, CSV, or a list of IDs |

### Discover, and "what's on Netflix"

TMDb's Discover source answers the question no list can: *everything available on
a given streaming service, in my country*. It needs only the TMDb API key you
already have.

Type service names, not IDs — `Netflix, Disney Plus` — and Sidecarr resolves
them against TMDb's list for your region, suggesting the right spelling if you
miss. `disney+` works as well as `Disney Plus`. Genres take names too. A region
is required whenever you filter by service, because availability is per country.

Some combinations worth knowing:

| Want | Set |
| --- | --- |
| Everything on Netflix in the US | Region `US`, Streaming on `Netflix` |
| Only what's included with the subscription | …and How it is available → *Included with a subscription* |
| Highly-rated sci-fi, no obscurities | Genres `science fiction`, Order *Highest rated*, Minimum votes `200` |
| Recent arrivals, roughly | Order *Newest first* |
| Japanese animation on any service | Genres `animation`, Original language `ja` |

Two honest limits. TMDb publishes what is available *now*, not when it was added,
so there is no true "new this week on Netflix" — ordering by newest release date
is the closest approximation. And setting *Highest rated* without a minimum vote
count will hand you films with four votes and a perfect score; that is why the
field is there.

### Paste or file

For the services with no usable list API, an official export is the clean way in.
Letterboxd and IMDb both publish one, and this provider reads them as they come:

- **Letterboxd** — Settings → Import & Export → Export your data. The ZIP holds
  `watchlist.csv`, `watched.csv` and one CSV per list.
- **IMDb** — any list page → Export. Your ratings export works too.

Paste the file's contents, or drop it in the config volume and point the file
source at it. A file is re-read on every run, so anything another tool keeps
updated stays in sync.

Letterboxd's export carries no IDs at all — only a name and a year — so those
rows are matched by asking Radarr or Sonarr to search the title. That is a guess
in a way an ID is not, so the match is deliberately strict: the year has to agree
exactly, or with no year the title has to be exact. Anything less is reported as
unresolved rather than risking the wrong film. IMDb's export includes the IMDb ID
and real metadata, so it needs no guessing and its rows can be filtered on rating,
runtime and genre.

### Custom lists

The custom provider is deliberately forgiving, because "a URL that returns a
list" has no single format. It understands:

- Sonarr's custom-list JSON — `[{"title": …, "tvdbId": 1, "tmdbId": 2, "imdbId": "tt3"}]`
- Radarr's and StevenLu's — `[{"title": …, "imdb_id": "tt3"}]`
- MDBList's — `{"movies": [...], "shows": [...]}`
- Wrapped arrays under `items`, `results`, `entries` or `data`
- RSS and Atom feeds, taking IMDb IDs from the link, guid or description
- CSV or TSV with a header row, including the Letterboxd and IMDb exports
- A bare list of IDs — `[603, 604]`, `["tt0133093"]`, or newline/comma separated

Each entry needs only one of a TMDb, TVDb or IMDb ID; Sidecarr resolves the rest.
A CSV export is the exception: it may carry only a title and year, which is
resolved by search as described above.

### Not included

**Simkl, AniList and MyAnimeList.** Radarr and Sonarr reach all three through
`auth.servarr.com`, an OAuth proxy using the Servarr project's own registered
application credentials. Those are not Sidecarr's to use, and supporting these
properly means registering separate OAuth applications with each service.
Contributions welcome — the provider interface is the easy part.

**CouchPotato.** The project has been dead for years.

## Setup

### 1. A list provider

Set up whichever you actually use. StevenLu, another Radarr/Sonarr, pasted lists
and custom URLs need no credentials at all, so this step is often skippable.

| Provider | Where to get it |
| --- | --- |
| Trakt | Create an app at [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications) with the redirect URI `urn:ietf:wg:oauth:2.0:oob`. Save the client ID and secret, then **Connect account** and enter the 8-character code at trakt.tv/activate. |
| TMDb | A free v3 API key from [themoviedb.org](https://www.themoviedb.org/settings/api). |
| MDBList | A free API key from [mdblist.com/preferences](https://mdblist.com/preferences/). |
| Plex | In Plex Web, open any item → Get Info → View XML, and copy `X-Plex-Token` from the address bar. |

A Trakt account is only needed for your own watchlist, collection and
recommendations, and for private lists. Each provider has a **Test** button that
saves what is in the box and then checks it.

![The Settings screen](docs/screenshots/settings.png)

### 2. Radarr and Sonarr

Paste the URL and API key (Radarr/Sonarr → Settings → General → API Key), press
**Test & load options**, then pick a quality profile and root folder from the
dropdowns Sidecarr just fetched.

Sonarr v3 has language profiles and v4 does not. Sidecarr detects which you are
running and hides the field when it does not apply.

### 3. Add a list

Lists → **Add list**. Pick the media type, choose a provider and one of its
lists, set a schedule, and save. The form rebuilds itself for whichever provider
you pick, and only offers combinations that can work — Trakt's box office
disappears when the list targets shows, StevenLu disappears entirely.

For Trakt and MDBList, **Pick from my lists** browses the lists you own and have
liked, so there are no URLs to paste.

![The list editor, showing source, selection, schedule and filter options](docs/screenshots/list-editor.png)

The whole UI has a light theme too, switchable from the sidebar and following
your system by default.

![The Lists view in the light theme](docs/screenshots/lists-light.png)

**Use Dry run first.** It walks the whole pipeline and records exactly what it
would add, without writing anything. It also resolves every candidate, so a
title Radarr has no metadata for is reported up front rather than promised and
then failed on the real run.

## What Sidecarr will and will not do

It makes exactly two kinds of write request, ever: `POST /api/v3/movie` and
`POST /api/v3/series`. There is no `DELETE`, `PUT` or `PATCH` anywhere in the
codebase, and no code path that can remove, unmonitor, rename or modify anything
already in your library.

This is deliberately narrower than Radarr's own import lists, whose
`CleanLibraryLevel` setting includes removing a movie and deleting its files
when it falls off a list. Sidecarr has no equivalent.

Before adding anything it reads your whole library and skips what you already
have, matching on both TMDb/TVDb and IMDb IDs. A list of 100 where you own 90
adds 10. It also respects Radarr's and Sonarr's exclusion lists.

**Adding a movie triggers a search by default.** Radarr's `search_on_add`
defaults to on, so it will start grabbing immediately; Sonarr's defaults to off.
Both are toggles in Settings. Turn Radarr's off to add titles as monitored but
missing, and untick **Add as monitored** as well if you want entries that do
nothing until you say so.

## Matching IDs across providers

Radarr identifies movies by TMDb ID and Sonarr identifies series by TVDb ID, but
most providers hand out something else — MDBList leads with IMDb IDs,
and TMDb has no TVDb ID for shows. Sidecarr closes the gap in three steps:

1. Use the ID the provider gave, if it is already the right one.
2. Ask the provider — TMDb, for instance, can turn its own show ID into a TVDb one.
3. Ask Radarr or Sonarr, whose own search already knows the cross-mappings.

Step 3 means a list carrying only IMDb IDs works with no extra API key at all. Resolution is
lazy, running only for titles that survive filtering and are about to be added,
so a long list does not cost a lookup per entry. Anything unresolvable is
recorded in History rather than silently vanishing.

## Filters

Every filter is per-list, and every numeric filter is **off at 0**.

| Filter | Effect |
| --- | --- |
| Min / max year | Release year, or first-aired year for shows |
| Min / max runtime | Minutes |
| Min rating, min votes | The provider's own numbers |
| Allowed countries / languages | Blank = anything. A list = only those. `ignore` = anything, including titles missing the field |
| Blacklisted genres | Genre slugs, e.g. `anime`, `horror` |
| Blacklisted networks | Shows only, substring match |
| Blacklisted title keywords | Substring match |
| Blacklisted IDs | TMDb for movies, TVDb for shows |

`Limit` applies **after** filtering, so 10 means ten titles that passed, not ten
candidates that might not. `Sort` runs before the limit, so limit + sort by votes
gives the ten most-voted eligible titles.

A filter can only judge metadata the provider actually sent, and providers differ
enormously. Trakt and TMDb are rich; a bare custom URL or a Letterboxd export may
give little more than a title. Filtering such a list by genre therefore rejects
everything — the reason recorded in History names the provider (`no release year
from Paste or file`) so this is visible rather than mysterious. Use the limit
instead, or pull the same list through MDBList, which does return metadata. An
IMDb CSV export is the exception: it carries rating, runtime and genres, so
filters work on it. TMDb's list endpoints carry no runtime, so runtime filters
have nothing to judge there either.

### Coming from traktarr

Sidecarr carries [traktarr](https://github.com/l3uddz/traktarr)'s filtering model
over almost field for field, with two deliberate changes:

- **Defaults are permissive.** traktarr shipped `min_year: 2000` /
  `max_year: 2019` and quietly dropped everything else. Sidecarr filters nothing
  until asked.
- **Countries and languages match exactly.** traktarr used a substring
  comparison, so `us` also matched `rus`.

traktarr itself has been unmaintained since June 2022 and pins
`attrdict==2.0.0`, which cannot run on Python 3.10 or newer.

## Scheduling

Per list: every N hours, a cron expression, or manual only. Interval schedules
resume from the last successful run rather than restarting the clock, so
restarting the container does not push the next sync out a full day. A list still
syncing when its next run comes due skips that tick instead of stacking.

## Limiting the add rate

The first sync of a big list is the dangerous one. A 1,000-title list you own ten
of means 990 additions in one go, and each one makes Radarr or Sonarr search every
indexer and hand the results to a download client. That is what strains a setup —
indexer rate limits, a saturated download queue, a disk filling faster than
expected — not the additions themselves.

Settings → **Add rate** turns on a limit: *N titles per M minutes*, 10 per 10
minutes by default, which works out around 60 an hour. A sync adds what the
current window allows and parks the rest in a queue; a background job releases
more every minute as capacity frees up. The limit is global, because your
download client is — five lists do not each get their own budget.

- The queue survives restarts, and each list shows how many of its titles are
  waiting.
- Before releasing anything, Sidecarr re-checks the library, so a title you added
  by hand while it waited is dropped rather than added twice.
- A title that fails to add is not retried from the queue. It is still on its
  list, so the next sync picks it up — one bad title cannot block the queue.
- **Turning the limit off releases the whole backlog** on the next tick, rather
  than stranding it. Clearing the queue by hand is also safe: those titles are
  still on their lists and come back on the next sync.
- A dry run reports what *would* be held back and queues nothing.

Off by default, since it changes when titles appear.

## Configuration

Everything lives in `/config`, which should be a mounted volume:

- `config.json` — settings, lists and Trakt tokens, written `0600`
- `sidecarr.db` — run history, last 200 runs

API keys and OAuth tokens inside `config.json` are encrypted. By default the key
is generated into `/config/secret.key` (mode 0600), which protects a stray copy
of `config.json` but not a copy of the whole volume — the key is sitting next to
it. Set `SIDECARR_SECRET_KEY` to a passphrase to keep the key off the volume
entirely, which is the stronger arrangement. Back up `secret.key` alongside the
config, or the stored credentials cannot be read back.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | UTC | Timezone for cron schedules and log timestamps |
| `SIDECARR_PORT` | `9898` | Listen port |
| `SIDECARR_LOG_LEVEL` | `INFO` | `DEBUG` logs every per-title filter decision |
| `SIDECARR_PASSWORD` | unset | Seeds the web password on first boot only |
| `SIDECARR_SECRET_KEY` | unset | Passphrase for encrypting stored credentials. Keeps the key off the config volume |
| `SIDECARR_COOKIE_SECURE` | `false` | Set `true` when served over HTTPS |
| `SIDECARR_CONFIG_DIR` | `/config` | Config and database location |
| `SIDECARR_MAX_TRAKT_PAGES` | `20` | Page cap per sync (100 items per page) |
| `SIDECARR_ADD_DELAY` | `0.5` | Seconds between batches of adds |
| `SIDECARR_BULK_BATCH_SIZE` | `50` | Titles per bulk import request |
| `SIDECARR_QUEUE_TTL_DAYS` | `30` | How long a rate-limited title waits before being given up on |

### Upgrading from Plextra

This project was called Plextra through 0.2.0. Nothing needs to be done by hand:

- `PLEXTRA_*` variables are still read, so an unchanged compose file keeps
  working. Sidecarr logs a deprecation warning naming each one it fell back to.
  `PLEXTRA_SECRET_KEY` matters most — if it stopped being read, stored
  credentials would no longer decrypt.
- `plextra.db` is renamed to `sidecarr.db` on first start, so run history
  survives.
- The image moved to `ghcr.io/austinmabry/sidecarr`. Point the container at the
  new image and keep the same `/config` volume.

Rename the variables to `SIDECARR_*` when convenient; the fallback will be
removed in a later release.

### Security

Sidecarr holds credentials for several services, so set a password in
Settings → Security unless the port is genuinely private. It warns in the log on
every boot until you do. `/api/health` stays open for Docker's healthcheck;
everything else requires the session cookie.

Beyond that: stored credentials are encrypted at rest, every mutating request
needs a CSRF token, and responses carry a Content-Security-Policy that permits
no inline or remote script, plus `X-Frame-Options`, `X-Content-Type-Options`,
`Referrer-Policy` and `Cross-Origin-Opener-Policy`.

Set `SIDECARR_COOKIE_SECURE=true` behind an HTTPS reverse proxy.

Scripted clients must read the `sidecarr_csrf` cookie from any GET and send it
back in an `X-CSRF-Token` header:

```bash
TOKEN=$(curl -s -c /tmp/jar http://localhost:9898/api/health >/dev/null && awk '/sidecarr_csrf/{print $7}' /tmp/jar)
curl -b /tmp/jar -H "X-CSRF-Token: $TOKEN" -X POST http://localhost:9898/api/lists/<id>/run
```

## Troubleshooting

**Nothing gets added, but nothing errors.** Check History. Every title has a
recorded outcome and reason — usually `already in library` or a filter.

**"Trakt account is not authorised".** The source needs a connected account.
Settings → Trakt → Connect account, then pick it in the list editor.

**Connection refused to Radarr/Sonarr.** `localhost` inside a container is the
container. Use the service name on a shared Docker network, or the host's LAN IP.

**"Radarr returned HTTP 500 … metadata service".** Radarr proxies metadata
through `api.radarr.video`, which answers a title it does not have with a clean
**404**. A **500 therefore does not mean the title is missing** — it means that
service, or the network path to it, failed. It is transient. Do nothing: the
next sync retries and usually succeeds, and the title should *not* be
blacklisted. If it hits several titles in one burst then stops, the likely cause
is rate limiting, since every add makes Radarr fetch metadata — set a **limit**
on the list to spread the work across runs.

**"Radarr does not recognise TMDb …".** This is the genuine miss, a clean 404.
Radarr's own Add Movie search will not find it either, so there is nothing to be
done — usually a TMDb entry deleted or merged after the list was built. Add the
ID to the list's blacklisted IDs to stop it being retried.

**"Path '…' is already configured for an existing movie".** The film is already
in the library under a different TMDb ID. Sidecarr matches on IMDb ID as well and
reports these as `already in library, under a different ID` before attempting an
add, so this should not reach Radarr. If it still does, the existing entry has no
IMDb ID recorded.

**`docker compose pull` says denied.** Check the Release workflow run: if
**Build and publish** is green the image exists and the package is simply
private. If that job was *skipped*, the tests before it failed and blocked the
publish.

**Cron never fires.** Set `TZ`. Cron is evaluated in the container's timezone,
which is UTC unless you say otherwise. An invalid expression is logged at startup
and the list is not scheduled.

## Data sources

Sidecarr reads from other people's services. Which one you use determines whether
you are using a published interface or reading pages that were not meant for a
program, so it is worth knowing which is which.

| Provider | How it is reached |
| --- | --- |
| **Trakt** | Official API, with an application you register yourself |
| **TMDb** | Official API, with a key you obtain yourself |
| **MDBList** | Official API, with a key you obtain yourself |
| **Plex** | Plex's own endpoint, using your token, for your own account's data |
| **StevenLu** | A JSON file published for exactly this purpose |
| **Another Radarr / Sonarr** | Your own instance's API, with your own key |
| **Paste or file** | Nothing is fetched — you supply the data |
| **Custom list** | Whatever URL you point it at, which makes its terms yours to check |

### IMDb and Letterboxd

Sidecarr had providers for both. They read the services' web pages, because
neither offers a usable public list API — and both services' terms forbid exactly
that, in plain language.

IMDb's Conditions of Use:

> **Robots and Screen Scraping:** You may not use data mining, robots, screen
> scraping, or similar data gathering and extraction tools on this site, except
> with our express written consent.

Letterboxd's Terms of Use:

> Except as explicitly authorized in these Terms, you must not employ any robot,
> spider, scraper, deep-link, or other automated data gathering or extraction
> tool, program, or algorithm to access, acquire, copy, or monitor any portion of
> the Service.

**Both providers were removed in 0.4.0.** Shipping them meant handing users a way
to get themselves blocked over terms they had not read, which is not a reasonable
thing to do to someone who just wanted their watchlist synced.

Use the official export instead. Both services publish one, through the interface
they provide, and it is a better source anyway:

- **Letterboxd** — Settings → Import & Export → Export your data. The ZIP holds
  `watchlist.csv`, `watched.csv` and one CSV per list.
- **IMDb** — open any list → Export. Your ratings export works too.

Point a **Paste or file** list at the CSV. IMDb's export even carries ratings,
runtime and genres, so filters work on it — which they never could on the
scraped pages.

If you already had a list using either provider, it is disabled on upgrade with
that explanation attached, and kept intact. Re-point it at an export and it picks
up its old filters, schedule and history.

## Legal

Sidecarr adds titles to Radarr and Sonarr. It does not download anything, does
not connect to any indexer or tracker, and has no knowledge of where a file
eventually comes from — that is entirely between you, your Radarr/Sonarr
configuration, and whatever sources you have set up there.

**This project does not condone or encourage copyright infringement.** Adding a
title to a library is not the same as obtaining it, and you are responsible for
ensuring that whatever you do download is lawful where you live — content you own,
public domain works, or material you are licensed to access. Do not use this
software to acquire copyrighted material you have no right to.

Sidecarr is not affiliated with, endorsed by, or connected to Trakt, TMDb,
MDBList, IMDb, Letterboxd, Plex, Radarr, Sonarr, or the Servarr project. All
trademarks belong to their respective owners.

This product uses the TMDB API but is not endorsed or certified by TMDB.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Contributing

Bug reports, provider requests and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, how the code fits
together, and a walkthrough of adding a provider — which needs no front-end work,
because the web UI renders the list editor from each provider's own description.

## License

[MIT](LICENSE)
