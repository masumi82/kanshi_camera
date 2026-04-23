"""Health check script for the kanshi_camera system.

Checks uStreamer availability, disk usage, and retry queue status.
Outputs JSON to stdout. Exits 0 if all checks pass, 1 otherwise.
"""

import json
import logging
import shutil
import sys
from pathlib import Path

import requests

from config import Config
from retry_queue import RetryQueue

logger = logging.getLogger(__name__)

# Disk space warning threshold (100 MB)
DISK_FREE_THRESHOLD_BYTES = 100 * 1024 * 1024


def check_ustreamer(config: Config) -> dict:
    """Check if uStreamer snapshot endpoint is responsive."""
    result: dict = {
        "name": "ustreamer",
        "ok": False,
        "detail": "",
    }
    try:
        kwargs: dict = {"timeout": 5}
        if config.stream_user and config.stream_password:
            kwargs["auth"] = (config.stream_user, config.stream_password)

        response = requests.get(config.snapshot_url, **kwargs)
        if response.status_code == 200:
            result["ok"] = True
            result["detail"] = "Snapshot endpoint responsive"
        else:
            result["detail"] = f"Unexpected status code: {response.status_code}"
    except requests.exceptions.RequestException as exc:
        result["detail"] = f"Connection failed: {exc}"

    return result


def check_disk_usage(path: str = "/var/lib/kanshi/") -> dict:
    """Check free disk space at the given path."""
    result: dict = {
        "name": "disk_usage",
        "ok": False,
        "detail": "",
    }
    check_path = Path(path)
    if not check_path.exists():
        result["detail"] = f"Path does not exist: {path}"
        return result

    try:
        usage = shutil.disk_usage(check_path)
        free_mb = usage.free / (1024 * 1024)
        total_mb = usage.total / (1024 * 1024)
        used_percent = (usage.used / usage.total) * 100

        result["detail"] = (
            f"Free: {free_mb:.0f} MB / Total: {total_mb:.0f} MB "
            f"({used_percent:.1f}% used)"
        )

        if usage.free >= DISK_FREE_THRESHOLD_BYTES:
            result["ok"] = True
        else:
            result["detail"] += " [WARNING: low disk space]"
    except OSError as exc:
        result["detail"] = f"Failed to check disk usage: {exc}"

    return result


def check_retry_queue(config: Config) -> dict:
    """Check the number of pending files in the retry queue."""
    result: dict = {
        "name": "retry_queue",
        "ok": True,
        "detail": "",
    }
    try:
        queue = RetryQueue(
            retry_dir=config.retry_dir,
            max_files=config.max_retry_files,
        )
        pending = queue.count()
        result["detail"] = f"{pending} file(s) pending"

        # Consider it a warning if queue is more than 50% full
        if pending > config.max_retry_files // 2:
            result["ok"] = False
            result["detail"] += " [WARNING: queue filling up]"
    except (OSError, ValueError) as exc:
        result["ok"] = False
        result["detail"] = f"Failed to check retry queue: {exc}"

    return result


def main() -> None:
    """Run all health checks and output results as JSON."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    config = Config()

    checks = [
        check_ustreamer(config),
        check_disk_usage(),
        check_retry_queue(config),
    ]

    all_ok = all(check["ok"] for check in checks)

    output = {
        "status": "healthy" if all_ok else "unhealthy",
        "checks": checks,
    }

    print(json.dumps(output, indent=2))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
