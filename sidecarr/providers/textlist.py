"""A list pasted into the box, or read from a file in /config.

The point of this one is that it needs no API at all. Letterboxd and IMDb both
publish an official CSV export, and neither has a usable list API, so an export
is often the only clean route in. Trakt, MDBList and Plex can all export too.

Anything :func:`parse_any` understands works here - CSV, JSON, an RSS body, or
just a column of IMDb IDs. A CSV export usually carries no ID for its rows, only
a name and a year; those resolve through Radarr's or Sonarr's own search, which
is stricter than it sounds - see ``resolve_by_title``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .. import settings
from .base import MediaItem, Provider, ProviderError, SourceField, SourceType
from .payload import parse_any

log = logging.getLogger(__name__)

# A pasted list is stored in the config file, so it has to stay a sane size.
MAX_PASTE_CHARS = 512_000

_CONTENT = SourceField(
    "content",
    "The list",
    kind="textarea",
    placeholder="Name,Year\nThe Matrix,1999\nHeat,1995",
    help="Paste a CSV export, a JSON list, or one ID per line.",
)
_PATH = SourceField(
    "path",
    "File path",
    placeholder="/config/imports/letterboxd.csv",
    help="Must be inside the config volume, so it survives a container restart.",
)
_ID_HINT = SourceField(
    "id_hint",
    "Bare numbers are",
    kind="select",
    required=False,
    default="",
    choices=[
        {"value": "", "label": "Match the media type (TMDb / TVDb)"},
        {"value": "tmdb", "label": "TMDb IDs"},
        {"value": "tvdb", "label": "TVDb IDs"},
        {"value": "imdb", "label": "IMDb IDs"},
    ],
)


class TextListProvider(Provider):
    key = "text"
    name = "Paste or file"
    blurb = "A list you paste in, or a CSV/JSON file in the config volume. No API needed."

    source_types = (
        SourceType(
            "paste",
            "Pasted list",
            fields=(_CONTENT, _ID_HINT),
            help=(
                "Understands the Letterboxd and IMDb CSV exports, Sonarr's custom-list "
                "JSON, an RSS body, or a plain column of IDs. Re-runs are harmless, so "
                "an import can sit here as a permanent list."
            ),
        ),
        SourceType(
            "file",
            "File in /config",
            fields=(_PATH, _ID_HINT),
            help=(
                "Read fresh on every run, so a file another tool keeps updated stays "
                "in sync. Same formats as a pasted list."
            ),
        ),
    )

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        if source.type == "paste":
            raw = source.get("content")
            if not raw.strip():
                raise ProviderError("Nothing was pasted into the list box.")
            if len(raw) > MAX_PASTE_CHARS:
                raise ProviderError(
                    f"That list is {len(raw):,} characters, over the "
                    f"{MAX_PASTE_CHARS:,} limit for a pasted list. Save it as a file "
                    "in the config volume and use the file source instead."
                )
        elif source.type == "file":
            raw = self._read(source.get("path"))
        else:
            raise ProviderError(f"Unknown source type {source.type!r}.")

        items = parse_any(raw, media_type, id_hint=source.get("id_hint"))
        if not items:
            raise ProviderError(
                "Nothing usable in that list. Expected CSV with a header row, JSON, "
                "or one ID per line."
            )
        return items

    @staticmethod
    def _read(raw_path: str) -> str:
        if not raw_path.strip():
            raise ProviderError("No file path given.")

        config_dir = settings.CONFIG_DIR.resolve()
        try:
            path = Path(raw_path).expanduser().resolve()
        except OSError as exc:
            raise ProviderError(f"Could not read {raw_path}: {exc}") from exc

        # Reading an arbitrary path would turn a list into a way to print any
        # file on the host into the run log.
        if path != config_dir and config_dir not in path.parents:
            raise ProviderError(
                f"{path} is outside the config volume ({config_dir}). Put the file "
                "there so it is readable and survives a restart."
            )
        if not path.is_file():
            raise ProviderError(f"No file at {path}.")

        try:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            raise ProviderError(f"Could not read {path}: {exc}") from exc
