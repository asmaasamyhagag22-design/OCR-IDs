"""The container's health check: the console must answer GET /login over HTTP(S) within five seconds.

A bare TCP connect would pass while the application is wedged (the kernel completes the
handshake on its own), so the check waits for a real HTTP answer. Over HTTPS the console's own
certificate is the trust anchor; the hostname is checked unless the probe has to use 127.0.0.1
for a console listening on every interface. Prints nothing (exit status only).
"""
from __future__ import annotations

import os
import ssl
import sys
import urllib.request


def main() -> int:
    host = os.environ.get("CONSOLE_HOST") or "127.0.0.1"
    host = "127.0.0.1" if host == "0.0.0.0" else host
    port = os.environ.get("CONSOLE_PORT") or "8090"
    cert = os.environ.get("CONSOLE_TLS_CERT") or ""
    context = None
    if cert:
        context = ssl.create_default_context(cafile=cert)
        context.check_hostname = host != "127.0.0.1"
    url = f"{'https' if cert else 'http'}://{host}:{port}/login"
    try:
        with urllib.request.urlopen(url, timeout=5, context=context) as response:
            return 0 if response.status == 200 else 1
    except Exception:  # noqa: BLE001 - any failure is "unhealthy"
        return 1


if __name__ == "__main__":
    sys.exit(main())
