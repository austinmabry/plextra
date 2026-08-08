"""The provider registry."""

from __future__ import annotations

from typing import Any

from .arr_instance import ArrInstanceProvider
from .base import (
    MediaItem,
    Provider,
    ProviderAuthError,
    ProviderError,
    SourceField,
    SourceType,
    dedupe,
    parse_year,
    set_id,
)
from .mdblist import MdblistProvider
from .plex import PlexProvider
from .simple import CustomProvider, StevenLuProvider
from .textlist import TextListProvider
from .tmdb import TmdbProvider
from .trakt import TraktProvider

# Order matters: this is the order the list editor shows them in.
PROVIDER_CLASSES: tuple[type[Provider], ...] = (
    TraktProvider,
    TmdbProvider,
    MdblistProvider,
    PlexProvider,
    StevenLuProvider,
    ArrInstanceProvider,
    TextListProvider,
    CustomProvider,
)

PROVIDERS: dict[str, type[Provider]] = {cls.key: cls for cls in PROVIDER_CLASSES}

# Providers that existed once and no longer do. Both read web pages, which both
# services' terms forbid in plain language, so they were withdrawn rather than
# left for people to get blocked over. Lists still pointing at them are disabled
# on startup with this explanation rather than failing every few hours forever.
RETIRED: dict[str, str] = {
    "imdb": (
        "The IMDb provider was removed in 0.4.0. It read IMDb's web pages, which "
        "their Conditions of Use prohibit. Export the list from IMDb instead "
        "(open the list, then Export) and point a 'Paste or file' list at the "
        "CSV - it carries more metadata than the pages did, so filters work."
    ),
    "letterboxd": (
        "The Letterboxd provider was removed in 0.4.0. It read Letterboxd's web "
        "pages, which their Terms of Use prohibit. Export your data from "
        "Letterboxd instead (Settings -> Import & Export) and point a 'Paste or "
        "file' list at the CSV."
    ),
}


def retired_reason(key: str) -> str:
    return RETIRED.get(key, "")


def build(key: str, config: Any, **kwargs: Any) -> Provider:
    """Instantiate a provider by key."""
    cls = PROVIDERS.get(key)
    if cls is None:
        if key in RETIRED:
            raise ProviderError(RETIRED[key])
        raise ProviderError(
            f"Unknown list source {key!r}. Known sources: {', '.join(PROVIDERS)}."
        )
    if cls is TraktProvider:
        return cls(config, **kwargs)
    return cls(config)


def build_all(config: Any, **kwargs: Any) -> list[Provider]:
    return [build(key, config, **kwargs) for key in PROVIDERS]


def describe_all(config: Any) -> list[dict[str, Any]]:
    """Everything the list editor needs to render itself."""
    return [build(key, config).describe() for key in PROVIDERS]


__all__ = [
    "PROVIDERS",
    "PROVIDER_CLASSES",
    "MediaItem",
    "Provider",
    "ProviderAuthError",
    "ProviderError",
    "SourceField",
    "SourceType",
    "build",
    "build_all",
    "dedupe",
    "describe_all",
    "parse_year",
    "set_id",
]
