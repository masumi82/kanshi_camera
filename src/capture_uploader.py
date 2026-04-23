from __future__ import annotations

import logging
import math
import signal
import sys
import time
from datetime import datetime, timezone

import requests

from config import Config
from gallery import Gallery
from retry_queue import RetryQueue
from settings_store import SettingsStore
from uploader import upload_image

logger = logging.getLogger(__name__)

# Flag for graceful shutdown
_shutdown_requested = False


def _handle_signal(signum: int, frame: object) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s, shutting down gracefully...", sig_name)
    _shutdown_requested = True


def capture_snapshot(config: Config) -> bytes | None:
    """Capture a JPEG snapshot from uStreamer."""
    try:
        kwargs: dict = {"timeout": 10}
        if config.stream_user and config.stream_password:
            kwargs["auth"] = (config.stream_user, config.stream_password)

        response = requests.get(config.snapshot_url, **kwargs)
        response.raise_for_status()
        if not response.content:
            logger.warning("Snapshot returned empty response (0 bytes)")
            return None
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type != "image/jpeg":
            logger.warning(
                "Snapshot returned unexpected Content-Type: %r (%d bytes); skipping",
                content_type,
                len(response.content),
            )
            return None
        if not response.content.startswith(b"\xff\xd8"):
            logger.warning(
                "Snapshot response is not a valid JPEG (bad magic bytes, %d bytes); "
                "camera may be showing 'No Signal'",
                len(response.content),
            )
            return None
        logger.info("Snapshot captured (%d bytes)", len(response.content))
        return response.content

    except requests.exceptions.HTTPError as exc:
        logger.error(
            "HTTP error capturing snapshot: %s (status %d)",
            exc,
            exc.response.status_code if exc.response is not None else 0,
        )
    except requests.exceptions.ConnectionError as exc:
        logger.error("Connection error capturing snapshot: %s", exc)
    except requests.exceptions.Timeout:
        logger.error("Timeout capturing snapshot (10s exceeded)")
    except requests.exceptions.RequestException as exc:
        logger.error("Unexpected error capturing snapshot: %s", exc)

    return None


def generate_filename() -> tuple[str, str]:
    """Generate a timestamped filename and capture timestamp for the snapshot.

    Returns:
        (filename, captured_at_iso) — both derived from the same datetime so
        the gallery filename and the uploaded captured_at always agree.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S") + ".jpg", now.isoformat()


def next_boundary(interval_min: int, now: float) -> float:
    """Return the next wall-clock boundary (in epoch seconds) for the given interval.

    For interval_min=5, boundaries are :00, :05, :10 ... past the minute mark
    of each hour. If ``now`` is exactly on a boundary, returns the next one.
    """
    period = interval_min * 60
    # ``math.floor(now / period) + 1`` avoids returning ``now`` when it lies
    # on the boundary.
    return (math.floor(now / period) + 1) * period


def sleep_until_next_capture(settings: SettingsStore) -> bool:
    """Sleep until the next clock-aligned capture boundary.

    The target boundary is fixed at the start of the cycle; if the user
    shortens the interval via the UI mid-wait, the target is pulled in so
    the change takes effect sooner. Returns True if the boundary was
    reached, False if shutdown was requested.
    """
    interval = settings.get_interval_min()
    target = next_boundary(interval, time.time())

    while not _shutdown_requested:
        now = time.time()
        remaining = target - now
        if remaining <= 0:
            return True

        # Pick up UI-driven interval changes: only pull target earlier, never
        # push it later (lengthening takes effect on the next cycle).
        new_interval = settings.get_interval_min()
        if new_interval != interval:
            interval = new_interval
            candidate = next_boundary(interval, now)
            if candidate < target:
                target = candidate
                remaining = target - now
                if remaining <= 0:
                    return True

        time.sleep(min(1.0, remaining))
    return False


def process_retry_queue(retry_queue: RetryQueue, config: Config) -> None:
    """Attempt to re-upload pending retry files."""
    pending_count = retry_queue.count()
    if pending_count == 0:
        return

    logger.info("Retry queue has %d pending file(s), processing...", pending_count)
    pending = retry_queue.get_pending(limit=5)

    for filepath, data, captured_at in pending:
        if _shutdown_requested:
            break
        retry_filename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
        if upload_image(data, config, retry_filename, captured_at):
            retry_queue.remove(filepath)
        else:
            logger.warning("Retry upload failed for %s, will try again later", filepath)


def main() -> None:
    """Main entry point for the capture-upload daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    logger.info("Starting capture_uploader")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    config = Config()
    config.validate()

    retry_queue = RetryQueue(
        retry_dir=config.retry_dir,
        max_files=config.max_retry_files,
    )

    gallery = Gallery(
        gallery_dir=config.gallery_dir,
        max_images=config.max_gallery_images,
        max_days=config.max_gallery_days,
    )

    settings = SettingsStore(
        settings_file=config.settings_file,
        default_interval_min=config.capture_interval_min,
    )

    logger.info(
        "Configuration: device_id=%s, interval=%d min, ustreamer=%s",
        config.device_id,
        settings.get_interval_min(),
        config.snapshot_url,
    )

    while not _shutdown_requested:
        # Wait until the next wall-clock boundary (e.g. every 5 min -> :00, :05).
        if not sleep_until_next_capture(settings):
            break

        # 1. Capture snapshot
        filename, captured_at = generate_filename()
        image_data = capture_snapshot(config)

        if image_data is not None:
            # 2. Save to gallery for web viewer
            gallery.save(image_data, filename)

            # 3. Upload
            success = upload_image(image_data, config, filename, captured_at)

            # 4. Save to retry queue on failure
            if not success:
                retry_queue.save(image_data, filename, captured_at)

        # 5. Process retry queue
        process_retry_queue(retry_queue, config)

    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
