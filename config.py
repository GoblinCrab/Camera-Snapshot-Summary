"""
config.py — Single source of truth for all pipeline settings.

Every other script does:
    from config import cfg
and then accesses values via cfg.NVR_USER, cfg.SEND_DELAY, etc.

Settings are read from secrets.env (dotenv format) in the same directory.
Missing required keys raise a clear error at startup rather than failing
silently mid-run.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load secrets.env from the same directory as this file
_env_path = Path(__file__).parent / "secrets.env"
if not _env_path.exists():
    raise FileNotFoundError(
        f"[config] secrets.env not found at {_env_path}. "
        "Copy secrets.env.example and fill in your values."
    )
load_dotenv(_env_path, override=True)


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"[config] Required key '{key}' is missing from secrets.env")
    return val


def _get(key: str, default) -> str:
    return os.getenv(key, str(default))


class _Config:
    # NVR credentials
    NVR_USER:              str   = _require("NVR_USER")
    NVR_PASS:              str   = _require("NVR_PASS")
    NVR_TPLINK_PASS:       str   = _require("NVR_TPLINK_PASS")

    # Microsoft Graph / mail
    TENANT_ID:             str   = _require("TENANT_ID")
    CLIENT_ID:             str   = _require("CLIENT_ID")
    CLIENT_SECRET:         str   = _require("CLIENT_SECRET")
    SENDER_EMAIL:          str   = _require("SENDER_EMAIL")
    RECIPIENT:             str   = _require("RECIPIENT")
    SEND_DELAY:            int   = int(_get("SEND_DELAY", 5))
    GRAPH_MAX_RETRIES:     int   = int(_get("GRAPH_MAX_RETRIES", 6))

    # Capture
    CAPTURE_MAX_WORKERS:   int   = int(_get("CAPTURE_MAX_WORKERS", 20))
    CAPTURE_TIMEOUT_S:     int   = int(_get("CAPTURE_TIMEOUT_S", 15))
    CAPTURE_RETRIES:       int   = int(_get("CAPTURE_RETRIES", 2))

    # Compression
    COMPRESS_MAX_TOTAL_MB: float = float(_get("COMPRESS_MAX_TOTAL_MB", 34.0))
    COMPRESS_DEFAULT_MAX_KB: int = int(_get("COMPRESS_DEFAULT_MAX_KB", 400))

    # PDF
    PDF_MAX_IMAGES_PER_PDF: int  = int(_get("PDF_MAX_IMAGES_PER_PDF", 64))

    # Paths
    SNAPSHOTS_DIR:         str   = _get("SNAPSHOTS_DIR", "snapshots")
    CAPTURE_QUEUE_FILE:    str   = _get("CAPTURE_QUEUE_FILE", "capture_queue.json")
    MANIFEST_FILE:         str   = _get("MANIFEST_FILE", "manifest.json")


cfg = _Config()
