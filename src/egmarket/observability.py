"""Logging that is useful inside GitHub Actions: phase groups, progress lines and
`::warning::` / `::error::` annotations. Plain console logging elsewhere."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
log = logging.getLogger("egmarket")


class AnnotationHandler(logging.Handler):
    """Mirror egmarket warnings/errors as GitHub workflow annotations (stdout commands)."""

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("egmarket") or record.levelno < logging.WARNING:
            return
        kind = "error" if record.levelno >= logging.ERROR else "warning"
        msg = record.getMessage().replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{kind} title={record.name}::{msg[:900]}", flush=True)


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
        if not IN_ACTIONS
        else "%(levelname)-7s %(name)s: %(message)s",  # Actions adds its own timestamps
        datefmt="%H:%M:%S",
        # In Actions everything goes through ONE stream so ::group:: commands and log lines
        # keep their order (the runner reads stdout and stderr as separate pipes).
        stream=sys.stdout if IN_ACTIONS else sys.stderr,
        force=True,
    )
    for noisy in (
        "httpx",
        "httpx2",
        "httpcore",
        "openai",
        "huggingface_hub",
        "onnxruntime",
        "urllib3",
        "filelock",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if IN_ACTIONS:
        logging.getLogger().addHandler(AnnotationHandler())


@contextmanager
def phase(title: str) -> Iterator[None]:
    """Sequential pipeline phase: collapsible group in Actions, banner + duration everywhere."""
    t0 = time.monotonic()
    if IN_ACTIONS:
        print(f"::group::{title}", flush=True)
    log.info("▶ %s", title)
    try:
        yield
    finally:
        log.info("✔ %s (%.1fs)", title, time.monotonic() - t0)
        if IN_ACTIONS:
            print("::endgroup::", flush=True)


def notice(message: str) -> None:
    if IN_ACTIONS:
        print(f"::notice::{message}", flush=True)
    log.info(message)


class Progress:
    """Rate-limited progress logger for long loops (every N steps or every T seconds)."""

    def __init__(self, label: str, *, every: int = 10, seconds: float = 45.0) -> None:
        self.label, self.every, self.seconds = label, every, seconds
        self.t0 = self.last = time.monotonic()
        self.n = 0

    def tick(self, detail: str = "") -> None:
        self.n += 1
        now = time.monotonic()
        if self.n % self.every == 0 or now - self.last >= self.seconds:
            self.last = now
            log.info("%s: %s (%.0fs)", self.label, detail or f"step {self.n}", now - self.t0)
