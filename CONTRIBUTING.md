# Contributing to Plextra

Thanks for taking a look. Bug reports, provider requests and pull requests are
all welcome.

## Getting set up

```bash
git clone https://github.com/austinmabry/plextra.git
cd plextra

python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest

PLEXTRA_CONFIG_DIR=./config .venv/bin/python -m plextra
```

The app is then on <http://localhost:9898>, storing its config in `./config`.

## How the code fits together

| Module | Responsibility |
| --- | --- |
| `providers/` | One module per list source. Fetch, and normalise to `MediaItem`. |
| `providers/base.py` | `MediaItem`, the `Provider` interface, and the self-description the web UI renders from. |
| `providers/payload.py` | The forgiving parser behind custom lists, RSS and StevenLu. |
| `clients/` | HTTP clients for Trakt, Radarr and Sonarr. |
| `sync.py` | The engine: fetch, filter, sort, limit, resolve IDs, add. |
| `filters.py` | Filter rules, running on the normalised item. |
| `scheduler.py` | Turns each list's schedule into an APScheduler job. |
| `api.py` | REST API and the static web UI. |
| `web/` | Dependency-free vanilla JS front end. No build step. |

## Adding a provider

This is the most likely contribution, and it needs no front-end work.

1. Create `plextra/providers/yoursite.py` with a class subclassing `Provider`.
2. Declare `source_types` — a `SourceType` per kind of list, each with the
   `SourceField`s it needs. The list editor renders itself from this.
3. Implement `fetch()`, returning `MediaItem` objects.
4. If the site can convert its own IDs into TMDb/TVDb ones, override
   `resolve_ids()`. If not, skip it: Radarr and Sonarr's own lookup already
   knows the cross-mappings and runs as a fallback.
5. Register the class in `providers/__init__.py`.
6. Add tests. `tests/test_providers.py` covers payload conversion,
   `tests/test_provider_http.py` covers request shape against a stub server.

If the site needs credentials, add a small config model in `config.py`, an
endpoint in `api.py`, and a field in the Settings screen.

## Tests

```bash
.venv/bin/pytest              # everything
.venv/bin/pytest tests/test_providers.py -q
```

Please keep new work covered. The suite deliberately avoids mocking at the HTTP
boundary where it can instead run against a stub server, so request shape and
payloads are checked rather than assumed.

CI runs the suite on Python 3.11 and 3.12, then builds the Docker image, starts
the container and checks the API, the web UI, the healthcheck, that `/config` is
writable and that it does not run as root.

## Releasing

Maintainers only. Pushing a `v*.*.*` tag builds and publishes a multi-arch image
(`linux/amd64` and `linux/arm64`) to GitHub Container Registry:

```bash
git tag v0.3.0
git push origin v0.3.0
```

`v1.2.3` publishes `1.2.3`, `1.2`, `1` and `latest`. A pre-release tag such as
`v1.2.3-rc1` publishes only `1.2.3-rc1` and never moves `latest`. The running
app reports the tag it was built from, because the workflow passes it in as a
build argument.

Creating a Release in the GitHub UI with a *new* tag works too, since that
pushes the tag. Creating one from a tag that already exists does not trigger
anything — re-run the Release workflow by hand from the Actions tab.

### First release only

A newly created GHCR package is **private**, which makes it invisible
everywhere: absent from Packages on the repository page, and `docker pull` fails
with `denied` as though nothing was built. Package visibility is an account
setting the workflow cannot change, so it is a one-time manual step:

1. Open the package settings under your GitHub account's Packages tab
2. **Danger Zone → Change visibility → Public**
3. **Connect repository**, so the package page links back here

To check whether an image really is public, ask the registry anonymously — this
prints a manifest for a public package and `DENIED` for a private one:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:austinmabry/plextra:pull" | jq -r .token)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://ghcr.io/v2/austinmabry/plextra/manifests/latest | head
```
