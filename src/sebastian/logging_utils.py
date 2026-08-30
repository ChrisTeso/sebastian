from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(path: Path, *, verbose: bool = False) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    logger = logging.getLogger("sebastian")
    for existing in logger.handlers:
        existing.close()
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
