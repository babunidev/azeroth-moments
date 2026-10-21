"""Filesystem watcher for the Azeroth Moments folder."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from azeroth_moments.config import Config
from azeroth_moments.db import Database
from azeroth_moments.ingest import is_candidate_file, register_screenshot

log = logging.getLogger(__name__)


class ScreenshotHandler(FileSystemEventHandler):
    def __init__(self, db: Database, config: Config) -> None:
        super().__init__()
        self.db = db
        self.config = config
        self._lock = threading.Lock()
        self._seen: set[str] = set()

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(Path(str(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(Path(str(event.dest_path)))

    def on_closed(self, event: FileSystemEvent) -> None:
        if getattr(event, "is_directory", False):
            return
        self._handle(Path(str(event.src_path)))

    def _handle(self, path: Path) -> None:
        if not is_candidate_file(path, self.config):
            return
        key = str(path.resolve())
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
        try:
            register_screenshot(self.db, self.config, path, ingest_reason="new")
        except Exception:  # noqa: BLE001
            log.exception("Failed to ingest %s", path)
            with self._lock:
                self._seen.discard(key)


def baseline_scan(db: Database, config: Config) -> int:
    """Scan watch directory once. Baseline-skip unless publish_existing_on_startup."""
    watch = config.watch_directory
    watch.mkdir(parents=True, exist_ok=True)
    reason = "new" if config.publish_existing_on_startup else "baseline_skipped"
    count = 0
    for path in sorted(watch.iterdir()):
        if not is_candidate_file(path, config):
            continue
        sid = register_screenshot(db, config, path, ingest_reason=reason)
        if sid is not None:
            count += 1
    if not db.baseline_done():
        db.mark_baseline_done()
    log.info("Startup scan registered %s files (reason=%s)", count, reason)
    return count


def start_observer(db: Database, config: Config) -> Observer:
    handler = ScreenshotHandler(db, config)
    observer = Observer()
    config.watch_directory.mkdir(parents=True, exist_ok=True)
    observer.schedule(handler, str(config.watch_directory), recursive=False)
    observer.start()
    log.info("Watching %s", config.watch_directory)
    return observer
