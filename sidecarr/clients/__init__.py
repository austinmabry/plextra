"""HTTP clients for Trakt, Radarr and Sonarr."""

from .arr import ArrError, ArrMetadataError, ArrUnknownIdError
from .radarr import RadarrClient
from .sonarr import SonarrClient
from .trakt import TraktAuthError, TraktClient, TraktError, parse_list_url

__all__ = [
    "ArrError",
    "ArrMetadataError",
    "ArrUnknownIdError",
    "RadarrClient",
    "SonarrClient",
    "TraktAuthError",
    "TraktClient",
    "TraktError",
    "parse_list_url",
]
