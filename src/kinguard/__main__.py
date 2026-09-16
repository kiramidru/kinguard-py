"""Command-line entry point for Kinguard AI."""

from __future__ import annotations

import logging

from .config import load_config
from .monitor import FallMonitor
from .server import serve
from .webcam import WebcamService


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    config = load_config()
    monitor = FallMonitor(config)
    webcam = WebcamService(config)
    monitor.start()
    try:
        serve(monitor, config, webcam)
    finally:
        monitor.stop()


if __name__ == "__main__":
    main()
