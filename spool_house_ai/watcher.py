from __future__ import annotations

import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from spool_house_ai.config import AppConfig
from spool_house_ai.pipeline import ImagePipeline


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg"}


class ImageCreatedHandler(FileSystemEventHandler):
    def __init__(self, config: AppConfig, pipeline: ImagePipeline, logger: logging.Logger) -> None:
        self.config = config
        self.pipeline = pipeline
        self.logger = logger

    def on_created(self, event: FileSystemEvent) -> None:
        self._process_event(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        destination_path = getattr(event, "dest_path", "")
        if destination_path:
            self._process_path(Path(destination_path))

    def _process_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._process_path(Path(event.src_path))

    def _process_path(self, image_path: Path) -> None:
        if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        if not wait_until_file_is_stable(
            image_path,
            self.config.watcher.stable_check_seconds,
            self.config.watcher.stable_check_attempts,
        ):
            self.logger.warning("Skipped unstable file: %s", image_path)
            return
        self.pipeline.process(image_path)


def wait_until_file_is_stable(path: Path, seconds: float, attempts: int) -> bool:
    previous_size = -1
    stable_count = 0

    for _ in range(attempts):
        if not path.exists():
            time.sleep(seconds)
            continue

        current_size = path.stat().st_size
        if current_size > 0 and current_size == previous_size:
            stable_count += 1
            if stable_count >= 2:
                return True
        else:
            stable_count = 0

        previous_size = current_size
        time.sleep(seconds)

    return False


def watch_input_folder(config: AppConfig, pipeline: ImagePipeline, logger: logging.Logger) -> None:
    event_handler = ImageCreatedHandler(config=config, pipeline=pipeline, logger=logger)
    observer = Observer()
    observer.schedule(event_handler, str(config.input_dir), recursive=False)
    observer.start()

    logger.info("Watching for new PNG/JPG images. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping watcher")
        observer.stop()
    observer.join()
