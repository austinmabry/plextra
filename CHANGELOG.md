# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Renamed to Sidecarr.** The project, the Python package, the container image
  (`ghcr.io/austinmabry/sidecarr`) and every environment variable now use the
  new name. Upgrades are handled automatically:
  - `PLEXTRA_*` environment variables are still read, with a deprecation warning
    naming each one that was used. `PLEXTRA_SECRET_KEY` in particular has to keep
    working, or encrypted credentials would stop decrypting on upgrade.
  - `plextra.db` is renamed to `sidecarr.db` on first start, so run history
    survives.
  - Session and CSRF cookies are now `sidecarr_session` and `sidecarr_csrf`,
    which signs everyone out once.

  The `PLEXTRA_*` fallback will be removed in a later release.
- `SIDECARR_BULK_BATCH_SIZE` (default 50) controls the batch size, and the delay
  between adds now applies between batches rather than between titles.

### Added

- **Bulk import.** Titles are now added in batches of 50 through Radarr's
  `movie/import` and Sonarr's `series/import` endpoints instead of one request
  each. The target resolves a batch against its metadata service in one go,
  which is far kinder than one lookup per title on a large first sync. A title
  the batch declines is retried on its own so it still gets a real reason,
  and if the endpoint is unavailable the whole batch falls back to individual
  adds.
- **Credentials encrypted at rest.** API keys and OAuth tokens in `config.json`
  are encrypted with Fernet. Set `SIDECARR_SECRET_KEY` to keep the key off the
  config volume entirely; otherwise a random key is generated into
  `/config/secret.key` with mode 0600. Existing plaintext configs are read and
  re-written encrypted on first load.
- **CSRF protection** on every mutating request, using a double-submit token.
  Everything here changes a real library, and cookie authentication alone would
  let another site drive it just by knowing the port.
- **Security headers**: Content-Security-Policy, X-Frame-Options,
  X-Content-Type-Options, Referrer-Policy and Cross-Origin-Opener-Policy. The
  CSP allows no inline or remote script.
- **Light theme**, switchable from the sidebar and following the operating
  system when no preference has been set.
- **Global scheduler pause**, for maintenance windows. Manual runs still work
  while paused.
- **Pre-flight health check** before each scheduled run. A target that is simply
  down is skipped with a log line instead of recording a failed run every few
  hours.

### Fixed

- A 5xx from Radarr's lookup endpoint is no longer reported as the title being
  missing. Radarr's metadata service answers a genuine miss with a clean 404, so
  a 500 means that service or the network to it failed — transient, and retried
  on the next sync rather than written off.

## [0.2.1]

### Fixed

- Titles already held under a *different* TMDb ID are matched on IMDb ID and
  reported as already present, instead of failing late with "path is already
  configured for an existing movie".
- Dry run resolves each candidate, so it can no longer promise an add that the
  real run will fail on.

## [0.2.0]

### Added

- **Seven more list providers** behind a common provider interface: TMDb,
  MDBList, IMDb, Plex, StevenLu, another Radarr/Sonarr instance, and any custom
  URL returning JSON, RSS/Atom or bare IDs. Providers describe their own source
  types and fields, and the list editor renders itself from that description.
- **ID resolution across providers.** Radarr keys movies by TMDb and Sonarr keys
  series by TVDb, but most providers hand out something else. Resolution tries
  the provider's ID, then the provider's own conversion, then Radarr/Sonarr's
  search — so an IMDb-only list works with no extra API key. It runs lazily,
  only for titles about to be added.

### Changed

- Filters run on a normalised item, so they behave identically across providers.
  A filter that cannot judge an item now names the provider that left the field
  empty, e.g. `no release year from IMDb`.

## [0.1.1]

### Fixed

- Test collection failed under a bare `pytest` invocation because the repository
  root was not on `sys.path`.

## [0.1.0]

Initial release. Trakt lists into Radarr and Sonarr, configured from a web UI,
with per-list filters, scheduling, dry run and per-title run history.
