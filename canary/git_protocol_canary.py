"""Synthetics canary that proves Gitaly is serving the git protocol.

A canary cannot shell out to git: the Synthetics runtime is a Lambda layer with
Python and a browser driver, and no git binary. Cloning is not an option.

The smart HTTP handshake is, and it is a better check anyway. The reference
advertisement is served by Gitaly, not by the web frontend, so a valid pkt-line
response proves the component that actually breaks is alive. A 200 from the
GitLab UI proves only that Puma answered.
"""

import os
import time
import urllib.error
import urllib.request

from aws_synthetics.common import synthetics_logger as logger

BASE_URL = os.environ["CANARY_BASE_URL"].rstrip("/")
PROJECT = os.environ["CANARY_PROJECT_PATH"].strip("/")
TOKEN = os.environ.get("CANARY_TOKEN", "")
TIMEOUT = int(os.environ.get("CANARY_TIMEOUT", "30"))

SERVICE = "git-upload-pack"
EXPECTED_CONTENT_TYPE = f"application/x-{SERVICE}-advertisement"


def fetch_advertisement():
    url = f"{BASE_URL}/{PROJECT}.git/info/refs?service={SERVICE}"
    request = urllib.request.Request(url, headers={"User-Agent": "git/2.40.0"})
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")

    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
        status = response.status
    return status, content_type, body, time.monotonic() - started


def parse_pkt_lines(body):
    """Yield each pkt-line payload. Malformed length prefixes raise."""
    offset = 0
    while offset < len(body):
        header = body[offset : offset + 4]
        if len(header) < 4:
            raise ValueError("truncated pkt-line header")
        length = int(header, 16)
        if length == 0:
            offset += 4
            continue
        if length < 4 or offset + length > len(body):
            raise ValueError(f"pkt-line length {length} runs past the response")
        yield body[offset + 4 : offset + length]
        offset += length


def check():
    status, content_type, body, elapsed = fetch_advertisement()

    if status != 200:
        raise RuntimeError(f"advertisement returned HTTP {status}")

    # A GitLab error page also returns 200; the content type is what separates them.
    if not content_type.startswith(EXPECTED_CONTENT_TYPE):
        raise RuntimeError(f"expected {EXPECTED_CONTENT_TYPE}, got {content_type!r}")

    payloads = list(parse_pkt_lines(body))
    if not payloads:
        raise RuntimeError("advertisement contained no pkt-lines")

    if payloads[0].strip() != f"# service={SERVICE}".encode():
        raise RuntimeError(f"unexpected first pkt-line {payloads[0][:60]!r}")

    refs = [p for p in payloads[1:] if b"refs/" in p]
    if not refs:
        raise RuntimeError("Gitaly answered but advertised no refs")

    return elapsed, len(refs)


def handler(event, context):
    try:
        elapsed, ref_count = check()
    except urllib.error.HTTPError as exc:
        logger.error(f"advertisement failed with HTTP {exc.code}")
        raise
    except urllib.error.URLError as exc:
        logger.error(f"could not reach {BASE_URL}: {exc.reason}")
        raise

    logger.info(f"advertised {ref_count} refs in {elapsed:.2f}s")
    return {"advertisement_seconds": round(elapsed, 3), "refs": ref_count}
