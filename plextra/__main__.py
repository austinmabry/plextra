"""Entry point: ``python -m plextra``."""

from __future__ import annotations

import uvicorn

from . import settings
from .logbuf import configure_logging


def main() -> None:
    configure_logging(settings.LOG_LEVEL)
    uvicorn.run(
        "plextra.api:app",
        host=settings.HOST,
        port=settings.PORT,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
