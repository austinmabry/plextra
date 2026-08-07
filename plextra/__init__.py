"""Plextra - sync Trakt lists into Radarr and Sonarr."""

import os

# The release workflow bakes the git tag in via a Docker build arg, so a
# published image reports the version it was released as. Running from a clone
# falls back to the version in this file.
__version__ = os.environ.get("PLEXTRA_VERSION") or "0.1.0"
