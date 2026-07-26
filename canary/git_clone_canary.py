"""CloudWatch Synthetics canary that proves git actually works.

An HTTP check on the GitLab web UI returns 200 while Gitaly is down, pushes
fail, and CI is stalled. This clones a real repository and reports the duration,
so the alarm fires on the thing users care about.
"""

import os
import shutil
import subprocess
import tempfile
import time

from aws_synthetics.common import synthetics_logger as logger
from aws_synthetics.selenium import synthetics_webdriver  # noqa: F401

CLONE_URL = os.environ["CANARY_CLONE_URL"]
CLONE_TIMEOUT = int(os.environ.get("CANARY_CLONE_TIMEOUT", "120"))


def clone_once():
    workdir = tempfile.mkdtemp(prefix="canary-")
    started = time.monotonic()
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", CLONE_URL, workdir],
            check=True,
            capture_output=True,
            timeout=CLONE_TIMEOUT,
        )
        if not os.path.isdir(os.path.join(workdir, ".git")):
            raise RuntimeError("clone reported success but produced no .git directory")
        return time.monotonic() - started
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def handler(event, context):
    try:
        elapsed = clone_once()
    except subprocess.TimeoutExpired:
        logger.error(f"clone exceeded {CLONE_TIMEOUT}s")
        raise
    except subprocess.CalledProcessError as exc:
        logger.error(f"clone failed: {exc.stderr.decode('utf-8', 'replace')[:500]}")
        raise

    logger.info(f"clone completed in {elapsed:.1f}s")
    return {"clone_seconds": round(elapsed, 2)}
