# Aurora

The Jellyfin experience, rebuilt to out-Plex Plex and feel like Netflix:
a cinematic theme plus a declarative home-row engine. Two pieces that work
together but install independently. (Requests? Pair with JellyBridge —
anything it surfaces inside jellyfin-web inherits the theme automatically.)

| Piece | What it does | Reaches |
|---|---|---|
| [`theme/`](theme/) | Netflix-caliber skin for the Jellyfin UI: near-black canvas, glass header, hover-grow rows, hero detail pages, cinematic player, skeleton shimmer, TV-remote focus rings, accent variants | Web, desktop app, **LG webOS app**, **Samsung Tizen app** (everything that runs jellyfin-web) |
| [`homerows/`](homerows/) | Your home screen, declared in YAML: Recently Added, New Releases, Top Rated per genre, Hidden Gems, decade rows, Family Night… self-updating smart collections | **Every client** — including iOS, Roku, Android TV, Swiftfin |

## The one honest paragraph you should read first

CSS/JS can restyle every client that renders Jellyfin's web UI — which
includes the LG and Samsung TV apps, since they embed jellyfin-web and pull
the server's Custom CSS. **No theme can restyle the native apps** (iOS,
Roku, Android, Android TV, Apple TV): they draw platform-native interfaces
and expose no styling hooks; any project claiming otherwise is misleading
you. That's why Aurora has three layers: where pixels can be owned they
are, and where they can't, the *content* is curated server-side (home rows)
so every client still feels deliberately designed.

**Full step-by-step install (both pieces, including how they connect to
Jellyfin): [INSTALL.md](INSTALL.md).**

## Quick start

### Theme (2 minutes)
Dashboard → General → Branding → Custom CSS:
```css
@import url("https://cdn.jsdelivr.net/gh/austinmabry/plextra@claude/aurora-theme/theme/aurora.css");
```
Accent variants — add a second import AFTER the first:
`variants/plex-gold.css` (classic Plex), `variants/glacier.css`,
`variants/emerald.css`. Or paste the file contents directly (works offline).
Every setting keys off the CSS variables at the top of `aurora.css` —
change `--au-accent` and the whole UI follows, TVs included, on next app
launch.

### Home rows (5 minutes)
```bash
cd homerows
cp .env.example .env    # JELLYFIN_URL + admin API key
# edit rows.yml — it ships with 10 opinionated rows
docker compose up -d --build
```
Rules support: type, genre, sort (incl. Random), rating floor,
released/added windows, decades, runtime caps, tags, parental ratings.
Rows appear in **all** clients under each library's Collections view —
rendered as cinematic rows wherever the theme applies. (Stock Jellyfin
home sections are fixed types and can't pin arbitrary collections; the
rows live one click away, and that's true of every honest solution.)
Full reference in [`homerows/rows.yml`](homerows/rows.yml); complete
walkthrough in [INSTALL.md](INSTALL.md).

## Design language

- **Canvas** `#0c0d10` near-black with a subtle radial ambient wash — not
  flat gray like stock, not pure black like OLED-crush.
- **Rows** grow on hover/focus (`scale 1.06`, lifted shadow) with the
  siblings staying put; skeleton shimmer while posters stream in.
- **Detail pages** get the hero treatment: backdrop bleeding through a
  bottom-weighted gradient, 800-weight title, pill CTAs with accent glow.
- **Player OSD** fades from true black, accent timeline, oversized touch
  targets.
- **TV-first focus**: a hard accent ring + glow on every focusable, tuned
  for d-pad navigation on webOS/Tizen.
- **Motion discipline**: one easing curve, two durations, and full
  `prefers-reduced-motion` compliance.

## Compatibility

Selectors track jellyfin-web 10.9/10.10. Point releases occasionally rename
classes; the tokens block at the top of `aurora.css` makes repairs local
and quick. The home-rows engine and request overlay use stable public APIs
(Jellyfin `/Items`+`/Collections`, Jellyseerr v1) and are version-tolerant.
