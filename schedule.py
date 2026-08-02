"""
schedule.py — Camera Snapshot Pipeline Runner
=============================================
Runs the full pipeline in order:
    1. discover.py   — Identify NVR brands and build capture queue
    2. capture.py    — Take RTSP snapshots
    3. compress.py   — Compress snapshots to meet size limits
    4. pdf.py        — Generate summary PDFs
    5. mail.py       — Email PDFs via Microsoft Graph

Designed to be invoked by Windows Task Scheduler. All output is written to
pipeline.log in the script directory so runs are fully auditable.

Task Scheduler setup:
    Program:   C:\\Path\\To\\venv\\Scripts\\python.exe
    Arguments: C:\\Path\\To\\schedule.py
    Start in:  C:\\Path\\To\\  (the folder containing all scripts)
"""

import subprocess
import sys
import os
import logging
from datetime import datetime

# Force UTF-8 on the console so any unicode in child script output doesn't
# crash the logger on Windows' default cp1252 encoding.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Logging — everything goes to pipeline.log AND stdout so Task Scheduler's
# "last run result" and the log file both capture failures.
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.log")

logger = logging.getLogger("pipeline")
logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# ---------------------------------------------------------------------------
# Pipeline definition — each step is (label, script_filename).
# Steps run in order; if any step exits non-zero the pipeline aborts.
# ---------------------------------------------------------------------------
PIPELINE = [
    ("Discover",  "discover.py"),
    ("Capture",   "capture.py"),
    ("Compress",  "compress.py"),
    ("PDF",       "pdf.py"),
    ("Mail",      "mail.py"),
]

# Path to the Python interpreter running this script — ensures the same venv
# or installation is used for all child scripts.
PYTHON = sys.executable

# Directory containing all pipeline scripts (same folder as this file).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run_step(label: str, script: str) -> bool:
    """
    Run a single pipeline script as a subprocess.
    Returns True on success, False on failure.
    Streams each line of output to the logger in real time.
    """
    script_path = os.path.join(SCRIPT_DIR, script)

    if not os.path.exists(script_path):
        logger.error(f"[{label}] Script not found: {script_path}")
        return False

    logger.info(f"[{label}] Starting — {script}")
    start = datetime.now()

    try:
        proc = subprocess.Popen(
            [PYTHON, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr into stdout
            text=True,
            encoding='utf-8',           # decode child output as UTF-8, not system cp1252
            errors='replace',           # replace undecodable bytes with ? rather than crashing
            cwd=SCRIPT_DIR,             # all scripts expect to run from their own directory
            bufsize=1,
        )

        # Stream output line by line so the log is live, not buffered
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                logger.info(f"    {line}")

        proc.wait()
        elapsed = (datetime.now() - start).total_seconds()

        if proc.returncode == 0:
            logger.info(f"[{label}] Completed in {elapsed:.1f}s ✓")
            return True
        else:
            logger.error(f"[{label}] FAILED (exit code {proc.returncode}) after {elapsed:.1f}s")
            return False

    except Exception as e:
        logger.error(f"[{label}] Exception: {e}")
        return False


def main():
    separator = "=" * 70
    run_start = datetime.now()

    logger.info(separator)
    logger.info(f"PIPELINE START  {run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(separator)

    for label, script in PIPELINE:
        success = run_step(label, script)
        if not success:
            logger.error(separator)
            logger.error(f"PIPELINE ABORTED at step [{label}]")
            logger.error(f"Subsequent steps were skipped.")
            logger.error(separator)
            sys.exit(1)   # Non-zero exit tells Task Scheduler the run failed

    elapsed = (datetime.now() - run_start).total_seconds()
    logger.info(separator)
    logger.info(f"PIPELINE COMPLETE  Total time: {elapsed:.1f}s")
    logger.info(separator)
    sys.exit(0)


if __name__ == "__main__":
    main()