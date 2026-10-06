"""The container HEALTHCHECK: exit 0 only when /ready answers 200.

The API key is read from the environment at run time (OCR_API_KEY), never baked
into the image.
"""

from __future__ import annotations

import os
import sys
import urllib.request


def main() -> int:
    """0 when /ready answers 200 on the loopback port, else 1. Prints nothing."""
    port = os.environ.get("OCR_PORT", "8081")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/ready", headers={"X-API-Key": os.environ.get("OCR_API_KEY", "")}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - loopback only
            return 0 if response.status == 200 else 1
    except Exception:  # noqa: BLE001 - never print: an error message could quote the key
        return 1


if __name__ == "__main__":
    sys.exit(main())
