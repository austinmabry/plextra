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
from .imdb import ImdbProvider
from .letterboxd import LetterboxdProvider
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
    ImdbProvider,
    LetterboxdProvider,
    PlexProvider,
    StevenLuProvider,
    ArrInstanceProvider,
    TextListProvider,
    CustomProvider,
)

PROVIDERS: dict[str, type[Provider]] = {cls.key: cls for cls in PROVIDER_CLASSES}


def build(key: str, config: Any, **kwargs: Any) -> Provider:
    """Instantiate a provider by key."""
    cls = PROVIDERS.get(key)
    if cls is None:
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
